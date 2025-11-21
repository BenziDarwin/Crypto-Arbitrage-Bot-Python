"""
CEX-DEX Arbitrage Scanner with proper liquidity routing.
Routes through native tokens (WMATIC, WETH) where direct pairs lack liquidity.
"""

import json
import asyncio
import aiohttp
from web3 import Web3
from decimal import Decimal, getcontext
from datetime import datetime
import time

getcontext().prec = 18

ROUTER_ABI = json.loads('''[{
    "inputs": [
        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
        {"internalType": "address[]", "name": "path", "type": "address[]"}
    ],
    "name": "getAmountsOut",
    "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
    "stateMutability": "view",
    "type": "function"
}]''')

# =============================================================================
# CHAIN CONFIG
# =============================================================================

CHAINS = {
    "BSC": {
        "rpc": "https://bsc-dataseed1.binance.org",
        "router": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
        "dex": "PancakeSwap",
        "native": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
        "usdt": "0x55d398326f99059fF775485246999027B3197955",   # 18 decimals
        "usdt_dec": 18,
    },
    "Polygon": {
        "rpc": "https://polygon-rpc.com",
        "router": "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
        "dex": "QuickSwap",
        "native": "0x0d500B1d8e8ef31e21c99d1db9a6444d3adf1270",  # WMATIC
        "usdc": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",   # 6 decimals - more liquid than USDT
        "usdc_dec": 6,
    },
    "Arbitrum": {
        "rpc": "https://arb1.arbitrum.io/rpc",
        "router": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
        "dex": "SushiSwap",
        "native": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH (is native on Arb)
        "usdc": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",   # Native USDC (6 dec)
        "usdc_dec": 6,
    },
}

# Tokens with ROUTING PATHS for liquidity
# path: the swap path to get USD price for 1 token
TOKENS = {
    "ETH_BSC": {
        "symbol": "ETH",
        "binance": "ETHUSDT", 
        "chain": "BSC",
        "token": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
        "token_dec": 18,
        # Direct ETH->USDT has good liquidity on BSC
        "path_type": "direct",
    },
    "BNB_BSC": {
        "symbol": "BNB",
        "binance": "BNBUSDT",
        "chain": "BSC",
        "token": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "token_dec": 18,
        "path_type": "direct",
    },
    "ETH_Polygon": {
        "symbol": "ETH",
        "binance": "ETHUSDT",
        "chain": "Polygon",
        "token": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        "token_dec": 18,
        # Route: WETH -> WMATIC -> USDC (better liquidity)
        "path_type": "via_native",
    },
    "MATIC_Polygon": {
        "symbol": "MATIC",
        "binance": "MATICUSDT",
        "chain": "Polygon",
        "token": "0x0d500B1d8e8ef31e21c99d1db9a6444d3adf1270",
        "token_dec": 18,
        # WMATIC -> USDC direct (native token, high liquidity)
        "path_type": "native_direct",
    },
    "ETH_Arbitrum": {
        "symbol": "ETH",
        "binance": "ETHUSDT",
        "chain": "Arbitrum",
        "token": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "token_dec": 18,
        # On Arbitrum, WETH IS the native token, direct to USDC
        "path_type": "native_direct",
    },
    "ARB_Arbitrum": {
        "symbol": "ARB",
        "binance": "ARBUSDT",
        "chain": "Arbitrum",
        "token": "0x912CE59144191C1204E64559FE8253a0e49E6548",
        "token_dec": 18,
        # ARB -> WETH -> USDC
        "path_type": "via_native",
    },
}

SCAN_INTERVAL = 5
MIN_SPREAD = Decimal("0.3")
MAX_VALID_SPREAD = Decimal("5.0")  # Ignore spreads > 5% as data errors

# Cache connections
W3_CACHE = {}
ROUTER_CACHE = {}

def get_router(chain_name):
    if chain_name not in ROUTER_CACHE:
        chain = CHAINS[chain_name]
        w3 = Web3(Web3.HTTPProvider(chain["rpc"]))
        W3_CACHE[chain_name] = w3
        ROUTER_CACHE[chain_name] = w3.eth.contract(
            address=w3.to_checksum_address(chain["router"]),
            abi=ROUTER_ABI
        )
    return W3_CACHE[chain_name], ROUTER_CACHE[chain_name]

# =============================================================================
# PRICE FETCHING WITH PROPER ROUTING
# =============================================================================

def get_dex_price(token_key):
    """Get DEX price with proper routing for liquidity."""
    token_cfg = TOKENS[token_key]
    chain_cfg = CHAINS[token_cfg["chain"]]
    
    w3, router = get_router(token_cfg["chain"])
    if not w3.is_connected():
        return None
    
    token_addr = w3.to_checksum_address(token_cfg["token"])
    native_addr = w3.to_checksum_address(chain_cfg["native"])
    
    # Get quote token (USDT for BSC, USDC for others)
    if "usdt" in chain_cfg:
        quote_addr = w3.to_checksum_address(chain_cfg["usdt"])
        quote_dec = chain_cfg["usdt_dec"]
    else:
        quote_addr = w3.to_checksum_address(chain_cfg["usdc"])
        quote_dec = chain_cfg["usdc_dec"]
    
    one_token = 10 ** token_cfg["token_dec"]
    
    # Build path based on liquidity routing
    path_type = token_cfg["path_type"]
    
    if path_type == "direct":
        path = [token_addr, quote_addr]
    elif path_type == "native_direct":
        # Token IS the native token, go direct to quote
        path = [token_addr, quote_addr]
    elif path_type == "via_native":
        # Route through native token for better liquidity
        path = [token_addr, native_addr, quote_addr]
    else:
        path = [token_addr, quote_addr]
    
    try:
        amounts = router.functions.getAmountsOut(one_token, path).call()
        price = Decimal(amounts[-1]) / Decimal(10 ** quote_dec)
        return price
    except Exception as e:
        print(f"❌ {token_key}: {e}")
        return None

# =============================================================================
# BINANCE
# =============================================================================

async def get_binance_prices(session):
    url = "https://api.binance.com/api/v3/ticker/price"
    try:
        async with session.get(url, timeout=5) as resp:
            data = await resp.json()
            symbols = set(t["binance"] for t in TOKENS.values())
            return {item['symbol']: Decimal(item['price']) 
                    for item in data if item['symbol'] in symbols}
    except Exception as e:
        print(f"❌ Binance: {e}")
        return {}

# =============================================================================
# SCANNER
# =============================================================================

async def scan_dex_prices():
    loop = asyncio.get_event_loop()
    results = {}
    
    tasks = [(key, loop.run_in_executor(None, get_dex_price, key)) 
             for key in TOKENS]
    
    for key, task in tasks:
        try:
            price = await task
            if price and price > 0:
                results[key] = price
        except:
            pass
    
    return results

def calculate_spreads(binance_prices, dex_prices):
    opportunities = []
    
    for key, cfg in TOKENS.items():
        cex_price = binance_prices.get(cfg["binance"])
        dex_price = dex_prices.get(key)
        
        if not cex_price or not dex_price:
            continue
        
        spread = ((dex_price - cex_price) / cex_price) * 100
        
        # Skip invalid data
        if abs(spread) > MAX_VALID_SPREAD:
            continue
        
        opportunities.append({
            "key": key,
            "symbol": cfg["symbol"],
            "chain": cfg["chain"],
            "dex": CHAINS[cfg["chain"]]["dex"],
            "cex": float(cex_price),
            "dex_price": float(dex_price),
            "spread": float(spread),
            "direction": "CEX→DEX" if spread > 0 else "DEX→CEX",
        })
    
    return sorted(opportunities, key=lambda x: abs(x["spread"]), reverse=True)

# =============================================================================
# MAIN
# =============================================================================

async def main():
    print("🔍 CEX-DEX Arbitrage Scanner (Routed Liquidity)")
    print(f"📊 Tokens: {len(TOKENS)}")
    print("=" * 70)
    
    # Test connections
    for chain in CHAINS:
        w3, _ = get_router(chain)
        status = "✅" if w3.is_connected() else "❌"
        print(f"   {status} {chain} ({CHAINS[chain]['dex']})")
    
    scan_num = 0
    
    async with aiohttp.ClientSession() as session:
        while True:
            scan_num += 1
            start = time.time()
            
            binance_prices, dex_prices = await asyncio.gather(
                get_binance_prices(session),
                scan_dex_prices()
            )
            
            elapsed = time.time() - start
            opps = calculate_spreads(binance_prices, dex_prices)
            
            print(f"\n{'='*70}")
            print(f"⏱️  Scan #{scan_num} @ {datetime.now().strftime('%H:%M:%S')} ({elapsed:.2f}s)")
            print(f"{'='*70}")
            
            if not opps:
                print("❌ No valid price data")
                await asyncio.sleep(SCAN_INTERVAL)
                continue
            
            print(f"\n{'Symbol':<8} {'Chain':<10} {'DEX':<12} {'CEX $':<12} {'DEX $':<12} {'Spread':<10}")
            print("-" * 70)
            
            for o in opps:
                spread = o["spread"]
                if abs(spread) >= 0.5:
                    icon = "🚀"
                elif abs(spread) >= 0.3:
                    icon = "📊"
                else:
                    icon = "  "
                
                print(f"{icon} {o['symbol']:<6} {o['chain']:<10} {o['dex']:<12} "
                      f"${o['cex']:<11.4f} ${o['dex_price']:<11.4f} {spread:+.2f}%")
            
            # Best opportunity
            valid = [o for o in opps if abs(o["spread"]) >= MIN_SPREAD]
            if valid:
                best = valid[0]
                profit_est = abs(best["spread"]) - 0.3  # minus ~0.3% fees
                print(f"\n🚀 Best: {best['symbol']} on {best['chain']}")
                print(f"   {best['direction']} | Spread: {best['spread']:+.2f}% | Net: ~{profit_est:.2f}%")
            else:
                print(f"\n💤 No spreads >= {MIN_SPREAD}%")
            
            await asyncio.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())