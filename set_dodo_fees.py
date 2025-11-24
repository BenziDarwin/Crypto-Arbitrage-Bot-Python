from web3 import Web3
import os
from dotenv import load_dotenv

load_dotenv(".env.live")

# Connect
RPC = "https://data-seed-prebsc-1-s1.binance.org:8545"
w3 = Web3(Web3.HTTPProvider(RPC))

try:
    from web3.middleware import geth_poa_middleware
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
except:
    pass

print("✓ Connected to BSC testnet\n")

ARBITRAGE = "0x42239b27c3ef6584c7299c1f77373629e41d0bf6"

# Minimal ABI for setDodoFeeRate
ABI = [
    {
        "inputs": [{"internalType": "uint256", "name": "_feeRate", "type": "uint256"}],
        "name": "setDodoFeeRate",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "dodoFeeRate",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

private_key = os.getenv("PRIVATE_KEY")
if not private_key:
    print("❌ PRIVATE_KEY not set")
    exit(1)

account = w3.eth.account.from_key(private_key)
address = account.address

print(f"Wallet: {address}\n")

# Get contract
arbitrage = w3.eth.contract(
    address=Web3.to_checksum_address(ARBITRAGE),
    abi=ABI
)

# Check current fee rate
print("Checking current DODO fee rate...")
current_rate = arbitrage.functions.dodoFeeRate().call()
print(f"Current rate: {current_rate} basis points ({current_rate/100}%)\n")

if current_rate == 0:
    print("✓ Fee rate is already 0%!")
    print("The problem must be something else.")
    exit(0)

print(f"Setting fee rate to 0...")

# Build transaction
tx = arbitrage.functions.setDodoFeeRate(0).build_transaction({
    "from": address,
    "gas": 100000,
    "gasPrice": w3.eth.gas_price,
    "nonce": w3.eth.get_transaction_count(address, 'pending'),
})

# Sign and send
print("Signing transaction...")
signed = w3.eth.account.sign_transaction(tx, private_key)

print("Sending transaction...")
tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
print(f"TX Hash: {tx_hash.hex()}\n")

# Wait for confirmation
print("Waiting for confirmation...")
receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

if receipt["status"] == 1:
    print("✅ SUCCESS!\n")
    
    # Verify
    new_rate = arbitrage.functions.dodoFeeRate().call()
    print(f"New fee rate: {new_rate} basis points ({new_rate/100}%)")
    
    print("\n" + "=" * 70)
    print("DODO fee rate set to 0%!")
    print("Now your bot's calculations will match the contract!")
    print("=" * 70)
    print("\nRun your bot again - it should work now! 🎉")
else:
    print("❌ Transaction failed")