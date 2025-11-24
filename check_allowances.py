"""
Check Token Allowances and Balances
This will show if the router can actually transfer tokens
"""
from web3 import Web3
import json

# Connect
RPC = "https://data-seed-prebsc-1-s1.binance.org:8545"
w3 = Web3(Web3.HTTPProvider(RPC))

try:
    from web3.middleware import geth_poa_middleware
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
except:
    pass

print("✓ Connected to BSC testnet\n")

# Addresses
ARBITRAGE = "0x9ee47bba211192011c35d65e8c6a7e2ac8458ae1"
PANCAKE = "0x12971e3662c1513df5551f4b814212b2bbc5fdcd"
BISWAP = "0xe73341a56cffdcbf47cee93d35f36aedaf2f993a"
BUSD = "0x0fa8f92990a4f9272bbc4a32aa4fa58ede59acb5"
WBNB = "0x9611465326218a535235bee029ac67b48e58c39b"

# ERC20 ABI
ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}
]

def check_token(token_addr, token_name):
    token = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
    
    print(f"{'=' * 70}")
    print(f"{token_name} ({token_addr})")
    print(f"{'=' * 70}")
    
    # Check arbitrage contract balance
    arb_bal = token.functions.balanceOf(Web3.to_checksum_address(ARBITRAGE)).call()
    arb_bal_tokens = w3.from_wei(arb_bal, 'ether')
    print(f"\n📦 Arbitrage Contract Balance: {arb_bal_tokens:.6f} {token_name}")
    
    # Check router balances
    pancake_bal = token.functions.balanceOf(Web3.to_checksum_address(PANCAKE)).call()
    pancake_bal_tokens = w3.from_wei(pancake_bal, 'ether')
    print(f"🥞 PancakeSwap Balance: {pancake_bal_tokens:.6f} {token_name}")
    
    biswap_bal = token.functions.balanceOf(Web3.to_checksum_address(BISWAP)).call()
    biswap_bal_tokens = w3.from_wei(biswap_bal, 'ether')
    print(f"🔄 BiSwap Balance: {biswap_bal_tokens:.6f} {token_name}")
    
    # Check allowances (arbitrage → routers)
    print(f"\n🔐 Allowances from Arbitrage Contract:")
    
    pancake_allow = token.functions.allowance(
        Web3.to_checksum_address(ARBITRAGE),
        Web3.to_checksum_address(PANCAKE)
    ).call()
    pancake_allow_tokens = w3.from_wei(pancake_allow, 'ether')
    print(f"   → PancakeSwap: {pancake_allow_tokens:.6f} {token_name}")
    if pancake_allow == 0:
        print(f"      ❌ NO ALLOWANCE! Router can't pull tokens!")
    
    biswap_allow = token.functions.allowance(
        Web3.to_checksum_address(ARBITRAGE),
        Web3.to_checksum_address(BISWAP)
    ).call()
    biswap_allow_tokens = w3.from_wei(biswap_allow, 'ether')
    print(f"   → BiSwap: {biswap_allow_tokens:.6f} {token_name}")
    if biswap_allow == 0:
        print(f"      ❌ NO ALLOWANCE! Router can't pull tokens!")
    
    print()

print("=" * 70)
print("TOKEN ALLOWANCE & BALANCE CHECK")
print("=" * 70)
print()

check_token(BUSD, "BUSD")
check_token(WBNB, "WBNB")

print("=" * 70)
print("DIAGNOSIS")
print("=" * 70)
print("""
The error is likely happening because:

❌ PROBLEM 1: Arbitrage contract hasn't approved routers
   → When router calls transferFrom(), it fails
   → FlashLoanArbitrage MUST approve routers before swap

❌ PROBLEM 2: Router has no tokens to give back
   → When router tries transfer(), it fails
   → Router needs token balance to fulfill swaps

SOLUTION:
=========
Your FlashLoanArbitrage.sol already does this correctly:

function _swapV2(...) internal {
    IERC20(path[0]).safeIncreaseAllowance(router, amountIn);  ✅
    // Then swap...
}

So if allowances show 0 above, it means the contract IS approving,
but it's being spent and not checked before the swap.

The real issue is: Router needs tokens to give back!
Check if routers have sufficient balances above.
""")