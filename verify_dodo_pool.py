"""
DODO Pool Verification Script
Checks if your DODO pool address is correct and has liquidity
"""
from web3 import Web3
from pathlib import Path
import json

# Your configuration
BSC_TESTNET_RPC = "https://data-seed-prebsc-1-s1.binance.org:8545"
DODO_POOL = "0x110b1289bb16be557b34644bf798d2d80ae5bccd".lower()
BUSD_TESTNET = "0x0fa8f92990a4f9272bbc4a32aa4fa58ede59acb5".lower()

# Minimal DODO pool ABI
ABI_DIR = Path(__file__).parent / "abi"

def load_abi(filename: str) -> list:
    """Load ABI from JSON file"""
    abi_path = ABI_DIR / filename
    if not abi_path.exists():
        raise FileNotFoundError(f"ABI file not found: {abi_path}")
    
    with open(abi_path, "r") as f:
        data = json.load(f)
    
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and "abi" in data:
        return data["abi"]
    else:
        raise ValueError(f"Invalid ABI format in {filename}")
    
DODO_ABI = load_abi("DodoPool.json")
ERC20_ABI = load_abi("ERC20.json")


def main():
    print("\n" + "="*60)
    print("DODO POOL VERIFICATION")
    print("="*60 + "\n")
    
    # Connect to BSC testnet
    print("Connecting to BSC testnet...")
    w3 = Web3(Web3.HTTPProvider(BSC_TESTNET_RPC))
    
    if not w3.is_connected():
        print("❌ Failed to connect to BSC testnet")
        return
    
    print("✓ Connected to BSC testnet\n")
    
    # Check if address is a contract
    print(f"Checking DODO pool: {DODO_POOL}")
    code = w3.eth.get_code(Web3.to_checksum_address(DODO_POOL))
    
    if code == b'' or code == b'0x':
        print("❌ DODO pool address has NO CODE - not a contract!")
        print("   This address is either:")
        print("   1. Not deployed")
        print("   2. Wrong address")
        print("   3. An EOA (wallet) not a contract")
        return
    
    print("✓ DODO pool is a contract\n")
    
    # Try to read DODO pool data
    try:
        dodo = w3.eth.contract(
            address=Web3.to_checksum_address(DODO_POOL),
            abi=DODO_ABI
        )
        
        print("Attempting to read pool tokens...")
        
        try:
            base_token = dodo.functions._BASE_CAPITAL_TOKEN_().call()
            print(f"✓ Base Token: {base_token}")
            
            # Get token symbol
            base_contract = w3.eth.contract(
                address=Web3.to_checksum_address(base_token),
                abi=ERC20_ABI
            )
            base_symbol = base_contract.functions.symbol().call()
            base_balance = base_contract.functions.balanceOf(DODO_POOL).call()
            print(f"  Symbol: {base_symbol}")
            print(f"  Pool Balance: {w3.from_wei(base_balance, 'ether')} {base_symbol}")
            
        except Exception as e:
            print(f"❌ Could not read _BASE_CAPITAL_TOKEN_: {e}")
        
        try:
            quote_token = dodo.functions._QUOTE_CAPITAL_TOKEN_().call()
            print(f"✓ Quote Token: {quote_token}")
            
            # Get token symbol
            quote_contract = w3.eth.contract(
                address=Web3.to_checksum_address(quote_token),
                abi=ERC20_ABI
            )
            quote_symbol = quote_contract.functions.symbol().call()
            quote_balance = quote_contract.functions.balanceOf(DODO_POOL).call()
            print(f"  Symbol: {quote_symbol}")
            print(f"  Pool Balance: {w3.from_wei(quote_balance, 'ether')} {quote_symbol}")
            
        except Exception as e:
            print(f"❌ Could not read _QUOTE_CAPITAL_TOKEN_: {e}")
        
        print("\n" + "="*60)
        print("EXPECTED CONFIGURATION:")
        print("="*60)
        print(f"Your BUSD testnet: {BUSD_TESTNET}")
        print(f"Pool base or quote should match your BUSD address")
        print()
        
        if base_token.lower() == BUSD_TESTNET.lower():
            print("✓ Base token matches BUSD!")
        elif quote_token.lower() == BUSD_TESTNET.lower():
            print("✓ Quote token matches BUSD!")
        else:
            print("❌ Neither base nor quote token matches BUSD!")
            print("   This pool doesn't have BUSD - wrong pool!")
        
    except Exception as e:
        print(f"❌ Error reading DODO pool: {e}")
        print("\nThis might not be a DODO V2 pool, or the ABI doesn't match.")
    
    print("\n" + "="*60)
    print("RECOMMENDATIONS:")
    print("="*60)
    print("1. Verify DODO pool address for BSC testnet")
    print("2. Check DODO docs for correct testnet pools")
    print("3. Ensure pool has BUSD liquidity")
    print("4. Consider using BSC mainnet for real testing")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()