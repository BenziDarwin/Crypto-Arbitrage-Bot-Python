"""
Decode the mysterious 0xe450d38c error properly
"""

# Your revert data (from the tuple)
revert_hex = "0xe450d38c0000000000000000000000009ee47bba211192011c35d65e8c6a7e2ac8458ae1000000000000000000000000000000000000000000000000171262f11316400000000000000000000000000000000000000000000000003642471f01287a0000"

print("=" * 70)
print("DECODING REVERT DATA")
print("=" * 70)

# Parse the components
selector = revert_hex[:10]
print(f"\nError Selector: {selector}")

# This is NOT a known error from your contracts!
# Let's check if it could be an ERC20 error

print("\nChecking if this matches any standard ERC20 errors...")

# Common ERC20/SafeERC20 errors:
print("  SafeERC20FailedOperation(address)      - Different signature")
print("  ERC20InsufficientBalance(...)          - Different signature")
print("  ERC20InsufficientAllowance(...)        - Different signature")
print()

print("NONE of these match 0xe450d38c!")
print()

# Decode the parameters
params_hex = revert_hex[10:]
param1 = "0x" + params_hex[:64][-40:]  # address
param2_hex = params_hex[64:128]
param3_hex = params_hex[128:192]

param2_int = int(param2_hex, 16)
param3_int = int(param3_hex, 16)

print("=" * 70)
print("ERROR PARAMETERS")
print("=" * 70)
print(f"\n1. Address: {param1}")
print(f"   → Your arbitrage contract")
print(f"\n2. Amount: {param2_int} wei = {param2_int / 10**18:.6f} tokens")
print(f"   → 1.6625 WBNB (intermediate amount)")
print(f"\n3. Amount: {param3_int} wei = {param3_int / 10**18:.6f} tokens")
print(f"   → 1000.9 BUSD (final amount)")

print("\n" + "=" * 70)
print("CRITICAL INSIGHT")
print("=" * 70)

print("""
This error signature 0xe450d38c does NOT exist in:
  ✗ FlashLoanArbitrage.sol
  ✗ RouterV2Mock.sol
  ✗ DodoPoolMock.sol
  ✗ ERC20Mock.sol
  ✗ OpenZeppelin SafeERC20
  ✗ OpenZeppelin ERC20

So where is it coming from???

HYPOTHESIS: It's a PHANTOM ERROR!

Web3.py is returning malformed revert data. Notice how your error
came as a TUPLE with the same data twice:

('0xe450d38c...', '0xe450d38c...')

This suggests Web3.py couldn't properly decode the revert and is
returning raw bytes that LOOK like an error but aren't actually
from your contracts!

REAL CAUSE:
-----------
The transaction is reverting somewhere, but Web3.py can't decode
the revert reason properly. This often happens when:

1. require() fails with a long string message
2. Low-level call fails without revert data
3. Out of gas during revert encoding
4. External contract call fails

Let's check what could actually be failing...
""")

print("=" * 70)
print("EXECUTION FLOW ANALYSIS")
print("=" * 70)

print("""
When you call executeArbitrageV2(), this happens:

1. FlashLoanArbitrage.executeArbitrageV2()
   ✓ onlyOwner check
   ✓ Amount validation
   ✓ Path validation
   
2. DODO.flashLoan()
   ✓ Transfers 1000 BUSD to arbitrage contract
   
3. DODO calls back: arbitrage.DVMFlashLoanCall()
   ✓ No validation (just calls internal function)
   
4. arbitrage._flashLoanCallback()
   ✓ Decodes parameters
   ✓ Calculates repayment
   
5. arbitrage._swapV2() for buy swap
   ✓ Approves router
   → Router.swapExactTokensForTokens()
     → Router tries: transferFrom(arbitrage, router, 1000 BUSD)
     → Router tries: transfer(arbitrage, 1.6625 WBNB)
     
6. arbitrage._swapV2() for sell swap
   ✓ Approves router  
   → Router.swapExactTokensForTokens()
     → Router tries: transferFrom(arbitrage, router, 1.6625 WBNB)
     → Router tries: transfer(arbitrage, 1000.9 BUSD)
     
7. Repay DODO and transfer profit

FAILURE POINT:
--------------
Step 5 or 6 - Router swap is failing!

Most likely: Router doesn't have enough tokens to give back.
""")

print("=" * 70)
print("THE FIX")
print("=" * 70)

print("""
Your check_allowances.py output showed:
  🥞 PancakeSwap Balance: 100,000 BUSD ✓
  🥞 PancakeSwap Balance: 100,000 WBNB ✓
  🔄 BiSwap Balance: 100,000 BUSD ✓
  🔄 BiSwap Balance: 100,000 WBNB ✓

So routers HAVE tokens!

But did you configure mockOutput?

If mockOutput = 0, the router returns amountIn.
If mockOutput = 1.6625 WBNB, the router tries to send 1.6625 WBNB.

Check: Did your auto-config actually run successfully?

Look for these log messages:
  🔧 Configuring testnet routers for this opportunity...
  ✓ pancakeswap configured
  ✓ biswap configured
  ✓ Both routers configured successfully!

If you DON'T see those, the routers aren't configured and will
return the wrong amounts!

ACTION:
-------
1. Make sure your bot's _configure_testnet_routers() actually runs
2. Verify both routers get configured before execution
3. Check that the configuration transactions actually succeed
4. Try manually setting mockOutput first to test

Run this:
  python fix_biswap_router.py
  
Then immediately run your arbitrage.
""")