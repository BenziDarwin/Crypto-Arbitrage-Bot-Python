"""
BSC Arbitrage Bot - Web3 Real-time Version
Fetches prices directly from DEX smart contracts for instant updates
WITH PostgreSQL DATABASE LOGGING
"""
import time
from datetime import datetime
from typing import Optional, Tuple, Dict
from decimal import Decimal
import os

try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    print("⚠️  Web3.py not installed. Install with: pip install web3 --break-system-packages")

try:
    from database import ArbitrageDatabase
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    print("⚠️  Database module not found. Running without database logging.")

# === BSC RPC ENDPOINTS (Free) ===
BSC_RPC_ENDPOINTS = [
    "https://bsc-dataseed1.binance.org",
    "https://bsc-dataseed2.binance.org",
    "https://bsc-dataseed3.binance.org",
    "https://bsc-dataseed4.binance.org",
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed2.defibit.io"
]

# === TOKEN & PAIR ADDRESSES ===
WBNB_ADDRESS = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
BUSD_ADDRESS = "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56"

# PancakeSwap V2 Router
PANCAKE_ROUTER = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
PANCAKE_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"

# BiSwap Router  
BISWAP_ROUTER = "0x3a6d8cA21D1CF76F653A67577FA0D27453350dD8"
BISWAP_FACTORY = "0x858E3312ed3A876947EA49d572A7C42DE08af7EE"

# === ROUTER ABI (minimal for getAmountsOut) ===
ROUTER_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"}
        ],
        "name": "getAmountsOut",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "type": "function"
    }
]

# === SIMULATION VALUES ===
FLASH_LOAN_AMOUNT_USD = 1000
GAS_COST_USD = 0.08
FLASH_LOAN_FEE = 0.0009
MIN_PROFIT_THRESHOLD = 0.01  # Minimum $0.01 net profit (changed from spread %)

PANCAKESWAP_FEE = 0.0025
BISWAP_FEE = 0.001

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'

def log(message: str, color: str = ""):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"{color}[{timestamp}] {message}{Colors.END}")

class Web3PriceFetcher:
    def __init__(self):
        self.w3 = None
        self.pancake_router = None
        self.biswap_router = None
        self.connected = False
        
        if not WEB3_AVAILABLE:
            return
        
        # Try connecting to BSC RPC
        for rpc in BSC_RPC_ENDPOINTS:
            try:
                self.w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 5}))
                if self.w3.is_connected():
                    log(f"Connected to BSC via {rpc}", Colors.GREEN)
                    self.pancake_router = self.w3.eth.contract(
                        address=Web3.to_checksum_address(PANCAKE_ROUTER),
                        abi=ROUTER_ABI
                    )
                    self.biswap_router = self.w3.eth.contract(
                        address=Web3.to_checksum_address(BISWAP_ROUTER),
                        abi=ROUTER_ABI
                    )
                    self.connected = True
                    break
            except Exception as e:
                continue
        
        if not self.connected:
            log("Failed to connect to any BSC RPC endpoint", Colors.RED)
    
    def get_price_from_router(self, router_contract, amount_in: int, path: list) -> Optional[float]:
        """Get price from DEX router using getAmountsOut"""
        try:
            amounts = router_contract.functions.getAmountsOut(
                amount_in,
                [Web3.to_checksum_address(addr) for addr in path]
            ).call()
            
            # Calculate price: output amount / input amount
            # amounts[0] is input, amounts[1] is output
            price = amounts[1] / amounts[0]
            return price
            
        except Exception as e:
            return None
    
    def get_wbnb_price_busd(self) -> Optional[Dict[str, float]]:
        """Get WBNB price in BUSD from both DEXes"""
        if not self.connected:
            return None
        
        # Use 1 WBNB as input amount (18 decimals)
        amount_in = 10**18
        path = [WBNB_ADDRESS, BUSD_ADDRESS]
        
        prices = {}
        
        # Get PancakeSwap price
        pancake_price = self.get_price_from_router(self.pancake_router, amount_in, path)
        if pancake_price:
            prices["pancakeswap"] = pancake_price
        
        # Get BiSwap price
        biswap_price = self.get_price_from_router(self.biswap_router, amount_in, path)
        if biswap_price:
            prices["biswap"] = biswap_price
        
        return prices if len(prices) == 2 else None

def simulate_flash_arbitrage(price_buy: float, price_sell: float, buy_fee: float, sell_fee: float) -> Tuple[float, float, float, float, float]:
    """Simulate flash loan arbitrage trade"""
    flash_loan_fee = FLASH_LOAN_AMOUNT_USD * FLASH_LOAN_FEE
    effective_capital = FLASH_LOAN_AMOUNT_USD - flash_loan_fee
    
    tokens_bought = (effective_capital / price_buy) * (1 - buy_fee)
    tokens_after_sell_fee = tokens_bought * (1 - sell_fee)
    usd_return = tokens_after_sell_fee * price_sell
    
    gross_profit = usd_return - FLASH_LOAN_AMOUNT_USD
    net_profit = gross_profit - GAS_COST_USD
    roi = (net_profit / FLASH_LOAN_AMOUNT_USD) * 100
    
    return tokens_bought, usd_return, gross_profit, net_profit, roi

def check_arbitrage(prices: Dict[str, float]) -> Optional[dict]:
    """Check for arbitrage opportunity"""
    pancake = prices["pancakeswap"]
    biswap = prices["biswap"]
    
    spread_buy_pancake = ((biswap - pancake) / pancake) * 100
    spread_buy_biswap = ((pancake - biswap) / biswap) * 100
    
    best_opp = None
    
    # Check: Buy on PancakeSwap, Sell on BiSwap
    tokens, usd_out, gross, net, roi = simulate_flash_arbitrage(
        pancake, biswap, PANCAKESWAP_FEE, BISWAP_FEE
    )
    if net > MIN_PROFIT_THRESHOLD:  # Check net profit, not spread
        best_opp = {
            "buy_dex": "PancakeSwap",
            "sell_dex": "BiSwap",
            "buy_price": pancake,
            "sell_price": biswap,
            "spread": spread_buy_pancake,
            "tokens": tokens,
            "usd_out": usd_out,
            "gross": gross,
            "net": net,
            "roi": roi
        }
    
    # Check: Buy on BiSwap, Sell on PancakeSwap
    tokens, usd_out, gross, net, roi = simulate_flash_arbitrage(
        biswap, pancake, BISWAP_FEE, PANCAKESWAP_FEE
    )
    if net > MIN_PROFIT_THRESHOLD:  # Check net profit, not spread
        if not best_opp or net > best_opp["net"]:
            best_opp = {
                "buy_dex": "BiSwap",
                "sell_dex": "PancakeSwap",
                "buy_price": biswap,
                "sell_price": pancake,
                "spread": spread_buy_biswap,
                "tokens": tokens,
                "usd_out": usd_out,
                "gross": gross,
                "net": net,
                "roi": roi
            }
    
    return best_opp

def print_arbitrage_opportunity(opp: dict):
    """Print formatted arbitrage opportunity"""
    print(f"\n{Colors.GREEN}{Colors.BOLD}🔥 ARBITRAGE OPPORTUNITY DETECTED!{Colors.END}")
    print(f"{Colors.CYAN}{'=' * 70}{Colors.END}")
    
    print(f"{Colors.BLUE}Strategy:{Colors.END} Buy on {Colors.BOLD}{opp['buy_dex']}{Colors.END}, Sell on {Colors.BOLD}{opp['sell_dex']}{Colors.END}")
    print(f"{Colors.BLUE}Buy Price:{Colors.END}  ${opp['buy_price']:.6f}")
    print(f"{Colors.BLUE}Sell Price:{Colors.END} ${opp['sell_price']:.6f}")
    print(f"{Colors.YELLOW}Spread:{Colors.END}     {opp['spread']:.4f}%\n")
    
    print(f"{Colors.CYAN}📊 SIMULATION RESULTS:{Colors.END}")
    print(f"  Flash Loan:        ${FLASH_LOAN_AMOUNT_USD:.2f}")
    print(f"  Net Profit:        {Colors.GREEN}${opp['net']:.4f}{Colors.END}")
    print(f"  ROI:               {Colors.GREEN}{opp['roi']:.4f}%{Colors.END}")
    print(f"{Colors.CYAN}{'=' * 70}{Colors.END}\n")

def main():
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 70}")
    print("BSC ARBITRAGE BOT - WEB3 REAL-TIME VERSION")
    print("Direct Blockchain Price Feeds + PostgreSQL Logging")
    print(f"{'=' * 70}{Colors.END}\n")
    
    if not WEB3_AVAILABLE:
        print(f"{Colors.RED}Error: Web3.py is required for this version{Colors.END}")
        print(f"Install with: {Colors.YELLOW}pip install web3 --break-system-packages{Colors.END}\n")
        return
    
    # Initialize database
    db = None
    session_id = None
    
    if DATABASE_AVAILABLE:
        db = ArbitrageDatabase(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 5432)),
            database=os.getenv('DB_NAME', 'bsc_arbitrage_db'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', 'password=1')
        )
        
        if db.connect():
            db.create_tables()
            session_id = db.start_session()
            if session_id:
                log(f"Database session started (ID: {session_id})", Colors.GREEN)
        else:
            log("Running without database logging", Colors.YELLOW)
            db = None
    else:
        log("Database module not available - running without logging", Colors.YELLOW)
    
    fetcher = Web3PriceFetcher()
    
    if not fetcher.connected:
        log("Could not connect to BSC network. Exiting...", Colors.RED)
        return
    
    print(f"{Colors.BLUE}Configuration:{Colors.END}")
    print(f"  Network:            BSC Mainnet")
    print(f"  Token Pair:         WBNB/BUSD")
    print(f"  Flash Loan:         ${FLASH_LOAN_AMOUNT_USD}")
    print(f"  Min Net Profit:     ${MIN_PROFIT_THRESHOLD} (after all fees)")
    print(f"  Update Interval:    Real-time (every block)")
    print(f"  Database Logging:   {'✓ Enabled' if db else '✗ Disabled'}")
    
    log("\nStarting real-time monitoring... (Press Ctrl+C to stop)", Colors.GREEN)
    log("Prices fetched directly from smart contracts\n", Colors.CYAN)
    
    iteration = 0
    opportunities_found = 0
    last_prices = {"pancakeswap": None, "biswap": None}
    
    try:
        while True:
            iteration += 1
            
            prices = fetcher.get_wbnb_price_busd()
            
            if not prices:
                log("Failed to fetch prices, retrying...", Colors.YELLOW)
                time.sleep(3)
                continue
            
            pancake = prices["pancakeswap"]
            biswap = prices["biswap"]
            
            # Detect changes
            prices_changed = (
                last_prices["pancakeswap"] != pancake or 
                last_prices["biswap"] != biswap
            )
            
            # Calculate spread
            spread = abs(biswap - pancake) / min(pancake, biswap) * 100
            
            # Log to database (every scan, including stale ones)
            scan_id = None
            if db:
                scan_id = db.log_price_scan(
                    pancake_price=pancake,
                    biswap_price=biswap,
                    spread=spread,
                    price_changed=prices_changed
                )
            
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            
            if prices_changed or iteration == 1:
                print(f"\n{Colors.BOLD}[{timestamp}] Update #{iteration}{Colors.END}")
                
                # Show change indicators
                if last_prices["pancakeswap"] is not None:
                    p_change = pancake - last_prices["pancakeswap"]
                    b_change = biswap - last_prices["biswap"]
                    
                    p_ind = f" {Colors.GREEN}↑{Colors.END}" if p_change > 0 else (f" {Colors.RED}↓{Colors.END}" if p_change < 0 else "")
                    b_ind = f" {Colors.GREEN}↑{Colors.END}" if b_change > 0 else (f" {Colors.RED}↓{Colors.END}" if b_change < 0 else "")
                    
                    print(f"  BiSwap:      ${biswap:.6f}{b_ind}")
                    print(f"  PancakeSwap: ${pancake:.6f}{p_ind}")
                else:
                    print(f"  BiSwap:      ${biswap:.6f}")
                    print(f"  PancakeSwap: ${pancake:.6f}")
                
                print(f"  Spread:      {spread:.4f}%")
                if db and scan_id:
                    print(f"  {Colors.CYAN}DB Scan ID:  {scan_id}{Colors.END}")
                
                opportunity = check_arbitrage(prices)
                if opportunity:
                    opportunities_found += 1
                    print_arbitrage_opportunity(opportunity)
                    
                    # Log opportunity to database
                    if db and scan_id:
                        opportunity['flash_loan_amount'] = FLASH_LOAN_AMOUNT_USD
                        db.log_arbitrage_opportunity(scan_id, opportunity)
                        log(f"Opportunity #{opportunities_found} logged to database", Colors.GREEN)
                else:
                    print(f"  {Colors.YELLOW}No opportunity{Colors.END}")
                
                last_prices = prices.copy()
            else:
                # Compact display (but still logged to DB)
                db_indicator = f" [DB:{scan_id}]" if (db and scan_id) else ""
                print(f"[{timestamp}] Monitoring... (no change){db_indicator}", end='\r')
            
            time.sleep(2)  # Check every 2 seconds
            
    except KeyboardInterrupt:
        print()
        log("\nBot stopped by user", Colors.YELLOW)
        
        # End database session
        if db and session_id:
            db.end_session(session_id, iteration, opportunities_found)
            log(f"Session ended - {iteration} scans, {opportunities_found} opportunities", Colors.CYAN)
            
            # Show session statistics
            stats = db.get_statistics(hours=24)
            if stats:
                print(f"\n{Colors.CYAN}{'=' * 70}{Colors.END}")
                print(f"{Colors.BOLD}📊 SESSION STATISTICS:{Colors.END}")
                print(f"{Colors.CYAN}{'=' * 70}{Colors.END}")
                print(f"  Total Scans:          {stats.get('total_scans', 0)}")
                print(f"  Price Changes:        {stats.get('price_changes', 0)}")
                print(f"  Average Spread:       {float(stats.get('avg_spread', 0)):.4f}%")
                print(f"  Max Spread:           {float(stats.get('max_spread', 0)):.4f}%")
                print(f"  Min Spread:           {float(stats.get('min_spread', 0)):.4f}%")
                print(f"  Opportunities Found:  {stats.get('total_opportunities', 0)}")
                if stats.get('total_potential_profit'):
                    print(f"  Total Potential:      ${float(stats.get('total_potential_profit', 0)):.4f}")
                    print(f"  Avg Profit/Opp:       ${float(stats.get('avg_profit', 0)):.4f}")
                    print(f"  Max Profit:           ${float(stats.get('max_profit', 0)):.4f}")
                print(f"{Colors.CYAN}{'=' * 70}{Colors.END}\n")
            
            db.close()
        
        log("Goodbye! 👋", Colors.YELLOW)
        
    except Exception as e:
        log(f"\nError: {str(e)}", Colors.RED)
        import traceback
        traceback.print_exc()
        
        # End session with error
        if db and session_id:
            db.end_session(session_id, iteration, opportunities_found)
            db.close()

if __name__ == "__main__":
    main()