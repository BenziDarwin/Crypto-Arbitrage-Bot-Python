from web3 import Web3

# -----------------------------
# CONFIG
# -----------------------------
BSC_MAINNET_RPC = "https://bsc-dataseed1.binance.org"
w3 = Web3(Web3.HTTPProvider(BSC_MAINNET_RPC))

# Factories
FACTORIES = {
    "DVMFactory": "0x790B4A80Fb1094589A3c0eFC8740aA9b0C1733fB",
    "DPPFactory": "0xd9CAc3D964327e47399aebd8e1e6dCC4c251DaAE",
    "DSPFactory": "0x0fb9815938Ad069Bf90E14FE6C596c514BEDe767"
}

# Target tokens
TOKENS = {
    "USDT": "0x55d398326f99059ff775485246999027b3197955",
    "USDC": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
    "WETH": "0x2170ed0880ac9a755fd29b2688956bd959f933f8",
    "WBNB": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
}

MIN_BALANCE = 1200  # minimum token amount

# Factory ABI - using getDODOPool instead of getDODOPoolBidirection
FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "baseToken", "type": "address"},
            {"internalType": "address", "name": "quoteToken", "type": "address"}
        ],
        "name": "getDODOPool",
        "outputs": [
            {"internalType": "address[]", "name": "pools", "type": "address[]"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

# Pool ABI - standard DODO V2 interface
POOL_ABI = [
    {"inputs": [], "name": "_BASE_TOKEN_", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "_QUOTE_TOKEN_", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
]

ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
]

# -----------------------------
# Helper functions
# -----------------------------
def from_wei(amount, decimals=18):
    return int(amount) / (10 ** decimals)

def to_checksum(addr):
    return w3.to_checksum_address(addr)

def get_token_info(token_addr):
    """Get token symbol and decimals"""
    try:
        token = w3.eth.contract(address=to_checksum(token_addr), abi=ERC20_ABI)
        symbol = token.functions.symbol().call()
        decimals = token.functions.decimals().call()
        return symbol, decimals
    except Exception as e:
        return "UNKNOWN", 18

def get_pool_info(pool_addr):
    """Get pool token addresses and balances"""
    try:
        pool = w3.eth.contract(address=to_checksum(pool_addr), abi=POOL_ABI)
        
        # Get base and quote token addresses
        base_token = pool.functions._BASE_TOKEN_().call()
        quote_token = pool.functions._QUOTE_TOKEN_().call()
        
        # Get token info
        base_symbol, base_decimals = get_token_info(base_token)
        quote_symbol, quote_decimals = get_token_info(quote_token)
        
        # Get balances
        base_contract = w3.eth.contract(address=to_checksum(base_token), abi=ERC20_ABI)
        quote_contract = w3.eth.contract(address=to_checksum(quote_token), abi=ERC20_ABI)
        
        base_balance = from_wei(base_contract.functions.balanceOf(pool_addr).call(), base_decimals)
        quote_balance = from_wei(quote_contract.functions.balanceOf(pool_addr).call(), quote_decimals)
        
        return {
            'base_token': base_token,
            'quote_token': quote_token,
            'base_symbol': base_symbol,
            'quote_symbol': quote_symbol,
            'base_balance': base_balance,
            'quote_balance': quote_balance
        }
    except Exception as e:
        return None

# -----------------------------
# Scan pools
# -----------------------------
def scan_factory(factory_name, factory_addr):
    print(f"\n{'='*70}")
    print(f"Scanning {factory_name}: {factory_addr}")
    print('='*70)
    
    factory = w3.eth.contract(address=to_checksum(factory_addr), abi=FACTORY_ABI)
    
    # Generate all token pairs (check both directions)
    token_list = list(TOKENS.items())
    
    for token0_name, token0_addr in token_list:
        for token1_name, token1_addr in token_list:
            if token0_addr.lower() == token1_addr.lower():
                continue
            
            try:
                # Query factory for pools with token0 as base and token1 as quote
                pools = factory.functions.getDODOPool(
                    to_checksum(token0_addr),
                    to_checksum(token1_addr)
                ).call()
                
                if not pools:
                    continue
                
                print(f"\n{token0_name} (base) -> {token1_name} (quote): {len(pools)} pool(s)")
                
                for pool_addr in pools[:10]:  # Limit to first 10 pools
                    pool_info = get_pool_info(pool_addr)
                    
                    if pool_info and (pool_info['base_balance'] >= MIN_BALANCE or pool_info['quote_balance'] >= MIN_BALANCE):
                        print("-" * 70)
                        print(f"Pool:  {pool_addr}")
                        print(f"Base:  {pool_info['base_token']} ({pool_info['base_symbol']})")
                        print(f"       Balance: {pool_info['base_balance']:,.2f}")
                        print(f"Quote: {pool_info['quote_token']} ({pool_info['quote_symbol']})")
                        print(f"       Balance: {pool_info['quote_balance']:,.2f}")
                        
            except Exception as e:
                # Silent fail for pairs with no pools
                pass

# -----------------------------
# Main
# -----------------------------
def main():
    if not w3.is_connected():
        print("❌ Could not connect to BSC mainnet")
        return
    
    print("\n" + "="*70)
    print("DODO Pool Scanner - BSC Mainnet")
    print(f"Minimum Balance: {MIN_BALANCE}")
    print("="*70)
    
    for name, addr in FACTORIES.items():
        scan_factory(name, addr)
    
    print("\n" + "="*70)
    print("✅ Scan completed")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()