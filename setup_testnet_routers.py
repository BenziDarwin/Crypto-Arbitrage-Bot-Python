"""
Quick Fix: Configure BiSwap Router Only
Use this if PancakeSwap is already configured
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3
import json

load_dotenv(".env.live")

class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    END = "\033[0m"

def log(message: str, color: str = ""):
    print(f"{color}{message}{Colors.END}")

def load_abi(filename: str) -> list:
    abi_dir = Path(__file__).parent / "abi"
    abi_path = abi_dir / filename
    
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

# Configuration
RPC_URL = "https://data-seed-prebsc-1-s1.binance.org:8545"
BISWAP_ROUTER = "0xe73341a56cffdcbf47cee93d35f36aedaf2f993a"

# Simulated values
BORROW_AMOUNT = 1000  # BUSD
WBNB_BOUGHT = 1.66375  # From PancakeSwap
BISWAP_WBNB_PRICE = 602.50  # $602.50/WBNB
BISWAP_FEE = 0.001  # 0.1%

# Calculate output: 1.66375 WBNB * $602.50 * (1 - 0.001)
busd_output = WBNB_BOUGHT * BISWAP_WBNB_PRICE * (1 - BISWAP_FEE)
biswap_mock_output = int(busd_output * 10**18)

def main():
    print(f"\n{Colors.CYAN}{'=' * 60}{Colors.END}")
    log("QUICK FIX: Configure BiSwap Router", Colors.CYAN)
    print(f"{Colors.CYAN}{'=' * 60}{Colors.END}\n")
    
    # Get private key
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        log("❌ Error: PRIVATE_KEY not set", Colors.RED)
        return
    
    # Connect
    log("Connecting to BSC Testnet...", Colors.BLUE)
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    
    try:
        from web3.middleware import geth_poa_middleware
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    except:
        pass
    
    if not w3.is_connected():
        log("❌ Failed to connect", Colors.RED)
        return
    
    log("✓ Connected", Colors.GREEN)
    
    # Setup account
    account = w3.eth.account.from_key(private_key)
    address = account.address
    log(f"✓ Wallet: {address}", Colors.GREEN)
    
    # Load ABI
    log("Loading ABI...", Colors.BLUE)
    router_abi = load_abi("RouterV2Mock.json")
    log("✓ ABI loaded", Colors.GREEN)
    
    # Get BiSwap contract
    biswap = w3.eth.contract(
        address=Web3.to_checksum_address(BISWAP_ROUTER),
        abi=router_abi
    )
    
    # Check current mock output
    try:
        current_output = biswap.functions.mockOutput().call()
        log(f"\nCurrent BiSwap mockOutput: {w3.from_wei(current_output, 'ether')} tokens", Colors.CYAN)
    except Exception as e:
        log(f"⚠️  Could not read current output: {e}", Colors.YELLOW)
    
    # Configure
    log(f"\nConfiguring BiSwap with output: {busd_output:.6f} BUSD", Colors.BLUE)
    log(f"  (This simulates selling {WBNB_BOUGHT:.6f} WBNB at ${BISWAP_WBNB_PRICE} with {BISWAP_FEE*100}% fee)", Colors.CYAN)
    
    try:
        # Get fresh nonce (including pending transactions)
        nonce = w3.eth.get_transaction_count(address, 'pending')
        log(f"  Using nonce: {nonce}", Colors.CYAN)
        
        # Build transaction
        tx = biswap.functions.setMockOutput(
            biswap_mock_output
        ).build_transaction({
            "from": address,
            "gas": 100000,
            "gasPrice": w3.eth.gas_price,
            "nonce": nonce,
        })
        
        # Sign and send
        log("  Signing transaction...", Colors.BLUE)
        signed = w3.eth.account.sign_transaction(tx, private_key)
        
        log("  Sending transaction...", Colors.BLUE)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        log(f"  TX Hash: {tx_hash.hex()}", Colors.CYAN)
        
        # Wait for confirmation
        log("  Waiting for confirmation...", Colors.BLUE)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        
        if receipt["status"] == 1:
            log(f"\n✅ SUCCESS! BiSwap configured: {busd_output:.6f} BUSD", Colors.GREEN)
            
            # Verify
            new_output = biswap.functions.mockOutput().call()
            log(f"✓ Verified mockOutput: {w3.from_wei(new_output, 'ether')} BUSD", Colors.GREEN)
            
            # Show expected profit
            expected_profit = busd_output - BORROW_AMOUNT
            log(f"\n💰 Expected Profit:", Colors.CYAN)
            log(f"   Borrow: {BORROW_AMOUNT} BUSD", Colors.CYAN)
            log(f"   Return: {busd_output:.6f} BUSD", Colors.CYAN)
            log(f"   Gross:  ${expected_profit:.6f}", Colors.GREEN if expected_profit > 0 else Colors.RED)
            
            print(f"\n{Colors.GREEN}{'=' * 60}{Colors.END}")
            log("All routers are now configured!", Colors.GREEN)
            log("You can run your arbitrage bot now.", Colors.GREEN)
            print(f"{Colors.GREEN}{'=' * 60}{Colors.END}\n")
            
        else:
            log("❌ Transaction reverted", Colors.RED)
            
    except Exception as e:
        log(f"❌ Error: {e}", Colors.RED)

if __name__ == "__main__":
    main()