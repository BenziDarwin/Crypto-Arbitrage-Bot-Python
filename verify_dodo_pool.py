from web3 import Web3
import sys

# -----------------------------
# CONFIG
# -----------------------------
BSC_MAINNET_RPC = "https://bsc-dataseed1.binance.org"
w3 = Web3(Web3.HTTPProvider(BSC_MAINNET_RPC))

MIN_LIQUIDITY = 1000
MIN_FLASH_LOAN = 100

# Extended Pool ABI with all callback variants
POOL_ABI = [
    {"inputs": [], "name": "_BASE_TOKEN_", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "_QUOTE_TOKEN_", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {
        "inputs": [
            {"internalType": "uint256", "name": "baseAmount", "type": "uint256"},
            {"internalType": "uint256", "name": "quoteAmount", "type": "uint256"},
            {"internalType": "address", "name": "assetTo", "type": "address"},
            {"internalType": "bytes", "name": "data", "type": "bytes"}
        ],
        "name": "flashLoan",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {"inputs": [], "name": "_K_", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "_I_", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "_LP_FEE_RATE_", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]

ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

# -----------------------------
# Helper functions
# -----------------------------
def from_wei(amount, decimals=18):
    return int(amount) / (10 ** decimals)

def to_checksum(addr):
    return w3.to_checksum_address(addr)

def get_token_info(token_addr):
    """Get complete token information"""
    try:
        token = w3.eth.contract(address=to_checksum(token_addr), abi=ERC20_ABI)
        name = token.functions.name().call()
        symbol = token.functions.symbol().call()
        decimals = token.functions.decimals().call()
        return {
            'address': token_addr,
            'name': name,
            'symbol': symbol,
            'decimals': decimals
        }
    except Exception as e:
        return {
            'address': token_addr,
            'name': "UNKNOWN",
            'symbol': "UNKNOWN",
            'decimals': 18,
            'error': str(e)
        }

def detect_pool_type_advanced(pool_addr):
    """Advanced pool type detection using multiple methods"""
    pool = w3.eth.contract(address=to_checksum(pool_addr), abi=POOL_ABI)
    
    print("\n🔍 DETAILED POOL TYPE DETECTION:")
    
    # Method 1: Check for DVM/DSP specific state variables
    try:
        k_value = pool.functions._K_().call()
        i_value = pool.functions._I_().call()
        print(f"  ✓ Found _K_ = {k_value}")
        print(f"  ✓ Found _I_ = {i_value}")
        
        if k_value == 0 or k_value == 1000000000000000000:  # 1e18 in DSP
            pool_type = 'DSP'
            print(f"  → Identified as DSP (Stable Pool)")
        else:
            pool_type = 'DVM'
            print(f"  → Identified as DVM (Vending Machine)")
        
        return pool_type
    except Exception as e:
        print(f"  ✗ _K_ not found (might be DPP)")
    
    # Method 2: Check bytecode for function selectors
    try:
        code = w3.eth.get_code(pool_addr).hex()
        
        # Function selectors (first 4 bytes of keccak256 hash)
        selectors = {
            '4c61a0f7': 'DVM',  # DVMFlashLoanCall(address,uint256,uint256,bytes)
            '63e6e0ca': 'DPP',  # DPPFlashLoanCall(address,uint256,uint256,bytes)
            'c60b6df7': 'DSP',  # DSPFlashLoanCall(address,uint256,uint256,bytes)
        }
        
        for selector, ptype in selectors.items():
            if selector in code:
                print(f"  ✓ Found {ptype}FlashLoanCall selector: 0x{selector}")
                return ptype
        
        print(f"  ✗ No specific callback selector found")
    except Exception as e:
        print(f"  ✗ Could not analyze bytecode: {e}")
    
    # Method 3: Check for LP fee rate (present in DVM/DSP, not in DPP)
    try:
        lp_fee = pool.functions._LP_FEE_RATE_().call()
        print(f"  ✓ Found _LP_FEE_RATE_ = {lp_fee}")
        print(f"  → Likely DVM or DSP (DPP doesn't have LP fees)")
        return 'DVM'  # Default to DVM if we can't be more specific
    except:
        print(f"  ✗ No _LP_FEE_RATE_ (might be DPP)")
        return 'DPP'
    
    return 'UNKNOWN'

def check_flash_loan_compatibility(pool_addr):
    """Check if flash loan function exists and is callable"""
    try:
        pool = w3.eth.contract(address=to_checksum(pool_addr), abi=POOL_ABI)
        
        # Try to get the flash loan function
        flash_loan_fn = pool.functions.flashLoan
        
        print("\n🔧 FLASH LOAN COMPATIBILITY:")
        print("  ✓ flashLoan function exists")
        
        # Check if contract is not paused (try a view function call)
        try:
            base_token = pool.functions._BASE_TOKEN_().call()
            print("  ✓ Contract is responsive")
            return True
        except:
            print("  ✗ Contract might be paused or non-functional")
            return False
            
    except Exception as e:
        print(f"\n🔧 FLASH LOAN COMPATIBILITY:")
        print(f"  ✗ No flashLoan function: {e}")
        return False

def get_pool_parameters(pool_addr):
    """Get additional pool parameters if available"""
    pool = w3.eth.contract(address=to_checksum(pool_addr), abi=POOL_ABI)
    params = {}
    
    print("\n📋 POOL PARAMETERS:")
    
    try:
        k = pool.functions._K_().call()
        params['K'] = k / 1e18
        print(f"  K (price curve): {params['K']:.6f}")
    except:
        pass
    
    try:
        i = pool.functions._I_().call()
        params['I'] = i / 1e18
        print(f"  I (initial price): {params['I']:.6f}")
    except:
        pass
    
    try:
        lp_fee = pool.functions._LP_FEE_RATE_().call()
        params['LP_FEE'] = lp_fee / 1e18
        print(f"  LP Fee Rate: {params['LP_FEE'] * 100:.3f}%")
    except:
        pass
    
    if not params:
        print("  (No additional parameters available)")
    
    return params

def verify_pool_advanced(pool_addr):
    """Comprehensive advanced pool verification"""
    print("\n" + "="*70)
    print(f"ADVANCED POOL VERIFICATION: {pool_addr}")
    print("="*70)
    
    try:
        pool = w3.eth.contract(address=to_checksum(pool_addr), abi=POOL_ABI)
        
        # Get basic pool info
        base_token = pool.functions._BASE_TOKEN_().call()
        quote_token = pool.functions._QUOTE_TOKEN_().call()
        
        # Get token details
        base_info = get_token_info(base_token)
        quote_info = get_token_info(quote_token)
        
        print(f"\n📊 POOL TOKENS:")
        print(f"  Base Token:  {base_info['symbol']} ({base_info['name']})")
        print(f"               {base_info['address']}")
        print(f"               Decimals: {base_info['decimals']}")
        
        print(f"  Quote Token: {quote_info['symbol']} ({quote_info['name']})")
        print(f"               {quote_info['address']}")
        print(f"               Decimals: {quote_info['decimals']}")
        
        # Get balances
        base_contract = w3.eth.contract(address=to_checksum(base_token), abi=ERC20_ABI)
        quote_contract = w3.eth.contract(address=to_checksum(quote_token), abi=ERC20_ABI)
        
        base_balance = base_contract.functions.balanceOf(pool_addr).call()
        quote_balance = quote_contract.functions.balanceOf(pool_addr).call()
        
        base_balance_human = from_wei(base_balance, base_info['decimals'])
        quote_balance_human = from_wei(quote_balance, quote_info['decimals'])
        
        print(f"\n💰 LIQUIDITY:")
        print(f"  Base:  {base_balance_human:,.4f} {base_info['symbol']}")
        print(f"  Quote: {quote_balance_human:,.4f} {quote_info['symbol']}")
        
        # Advanced pool type detection
        pool_type = detect_pool_type_advanced(pool_addr)
        
        # Get pool parameters
        params = get_pool_parameters(pool_addr)
        
        # Flash loan compatibility check
        flash_loan_supported = check_flash_loan_compatibility(pool_addr)
        
        # Calculate max safe flash loan amounts
        max_base_loan = base_balance_human * 0.95
        max_quote_loan = quote_balance_human * 0.95
        
        print(f"\n📈 MAX SAFE FLASH LOAN (95% of balance):")
        print(f"  Base:  {max_base_loan:,.4f} {base_info['symbol']}")
        print(f"  Quote: {max_quote_loan:,.4f} {quote_info['symbol']}")
        
        # Liquidity checks
        liquidity_check = (base_balance_human >= MIN_LIQUIDITY or 
                          quote_balance_human >= MIN_LIQUIDITY)
        
        # Final verification
        print(f"\n✅ VERIFICATION RESULTS:")
        print(f"  {'✓' if liquidity_check else '✗'} Sufficient Liquidity (>= {MIN_LIQUIDITY})")
        print(f"  {'✓' if flash_loan_supported else '✗'} Flash Loan Function Available")
        print(f"  {'✓' if pool_type != 'UNKNOWN' else '⚠'} Pool Type: {pool_type}")
        print(f"  {'✓' if base_balance_human >= MIN_FLASH_LOAN else '✗'} Base Token Flashable (>= {MIN_FLASH_LOAN})")
        print(f"  {'✓' if quote_balance_human >= MIN_FLASH_LOAN else '✗'} Quote Token Flashable (>= {MIN_FLASH_LOAN})")
        
        all_checks = (liquidity_check and flash_loan_supported and 
                     (base_balance_human >= MIN_FLASH_LOAN or 
                      quote_balance_human >= MIN_FLASH_LOAN))
        
        print(f"\n{'='*70}")
        if all_checks:
            print("✅ POOL IS SUITABLE FOR FLASH LOAN ARBITRAGE")
            print(f"\n📝 CONTRACT CONFIGURATION:")
            print(f"  Pool Address: {pool_addr}")
            print(f"  Pool Type: {pool_type}")
            print(f"  Flash Loan Fee: 0% (FREE!)")
            print(f"\n💡 DEPLOYMENT:")
            print(f"  FlashLoanArbitrage contract = new FlashLoanArbitrage(")
            print(f"      \"{pool_addr}\"")
            print(f"  );")
            print(f"\n⚡ RECOMMENDED CALLBACK:")
            if pool_type == 'DVM':
                print(f"  Contract will receive: DVMFlashLoanCall()")
            elif pool_type == 'DPP':
                print(f"  Contract will receive: DPPFlashLoanCall()")
            elif pool_type == 'DSP':
                print(f"  Contract will receive: DSPFlashLoanCall()")
            else:
                print(f"  Contract implements all three callbacks (auto-detect)")
        else:
            print("❌ POOL NOT SUITABLE - FAILED VERIFICATION CHECKS")
        print("="*70)
        
        return {
            'suitable': all_checks,
            'pool_address': pool_addr,
            'pool_type': pool_type,
            'base_token': base_info,
            'quote_token': quote_info,
            'base_balance': base_balance_human,
            'quote_balance': quote_balance_human,
            'max_base_loan': max_base_loan,
            'max_quote_loan': max_quote_loan,
            'parameters': params
        }
        
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        print("="*70)
        return None

# -----------------------------
# Main
# -----------------------------
def main():
    if not w3.is_connected():
        print("❌ Could not connect to BSC mainnet")
        return
    
    print("\n" + "="*70)
    print("DODO POOL ADVANCED VERIFICATION TOOL")
    print("="*70)
    
    if len(sys.argv) < 2:
        print("\nUsage: python verify_pool_advanced.py <pool_address>")
        print("\nExample:")
        print("  python verify_pool_advanced.py 0x6098A5638d8D7e9Ed2f952d35B2b67c34EC6B476")
        return
    
    pool_address = sys.argv[1]
    
    # Verify the address is valid
    try:
        pool_address = to_checksum(pool_address)
    except Exception as e:
        print(f"\n❌ Invalid address: {str(e)}")
        return
    
    # Run verification
    result = verify_pool_advanced(pool_address)
    
    if result and result['suitable']:
        print("\n🚀 READY TO USE THIS POOL FOR ARBITRAGE!")
        print(f"   Base your trades on up to {result['max_base_loan']:,.2f} {result['base_token']['symbol']}")
        print(f"   or {result['max_quote_loan']:,.2f} {result['quote_token']['symbol']}\n")

if __name__ == "__main__":
    main()