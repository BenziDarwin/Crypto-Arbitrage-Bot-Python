"""
Flash Loan Arbitrage Bot - TRUE Live Execution with Dynamic Router Config
Automatically configures testnet mock routers before each execution
"""
import time
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Tuple
from dotenv import load_dotenv

load_dotenv(".env.live")

try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    print("⚠️  Web3.py not installed. Install with: pip install web3")

try:
    from database_live import ArbitrageDatabase
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    print("⚠️  Database module not found. Running without database logging.")

# === TRADING CONFIGURATION ===
TRADING_CONFIG = {
    "borrow_token": "BUSD",      # Token to flash loan
    "trade_token": "WBNB",       # Intermediate token
    "borrow_amount": 1000,       # Amount to borrow (whole tokens)
    "min_profit": 0.01,          # Minimum profit in BUSD to execute (after all fees)
    "min_spread_pct": 0.37,       # Minimum spread % to even attempt (pre-filter)
    "flash_loan_fee": 0.0,       # DODO flash loan fee (0% = free, 0.0009 = 0.09%)
    "gas_cost_usd": 0.08,        # Estimated gas cost in USD
}

# === NETWORK CONFIGURATION ===
NETWORK = os.getenv("NETWORK", "bsc_testnet")

NETWORKS = {
    "bsc_mainnet": {
        "rpc": "https://bsc-dataseed1.binance.org",
        "chain_id": 56,
        "explorer": "https://bscscan.com",
    },
    "bsc_testnet": {
        "rpc": "https://data-seed-prebsc-1-s1.binance.org:8545",
        "chain_id": 97,
        "explorer": "https://testnet.bscscan.com",
    },
}

# === CONTRACT ADDRESSES ===
CONTRACT_CONFIG = {
    "bsc_mainnet": {
        "arbitrage": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        "dodo_pool": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        "v2_routers": {
            "pancakeswap": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
            "biswap": "0x3a6d8cA21D1CF76F653A67577FA0D27453350dD8",
        },
        "tokens": {
            "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
            "BUSD": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
            "USDT": "0x55d398326f99059fF775485246999027B3197955",
        },
    },
    "bsc_testnet": {
        "arbitrage": "0x9ee47bba211192011c35d65e8c6a7e2ac8458ae1",
        "dodo_pool": "0x110b1289bb16be557b34644bf798d2d80ae5bccd",
        "v2_routers": {
            "pancakeswap": "0x12971e3662c1513df5551f4b814212b2bbc5fdcd",
            "biswap": "0xe73341a56cffdcbf47cee93d35f36aedaf2f993a",
        },
        "tokens": {
            "WBNB": "0x9611465326218a535235bee029ac67b48e58c39b",
            "BUSD": "0x0fa8f92990a4f9272bbc4a32aa4fa58ede59acb5",
        },
    },
}

ABI_DIR = Path(__file__).parent / "abi"

class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    BOLD = "\033[1m"
    END = "\033[0m"

def log(message: str, color: str = ""):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"{color}[{timestamp}] {message}{Colors.END}")

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

class ArbitrageBot:
    def __init__(self, private_key: str, network: str = "bsc_testnet", dry_run: bool = True):
        self.network = network
        self.dry_run = dry_run
        self.private_key = private_key
        self.db = None
        self.session_id = None
        
        if not WEB3_AVAILABLE:
            raise ImportError("Web3.py is required")
        
        # Connect to network
        net_config = NETWORKS.get(network)
        if not net_config:
            raise ValueError(f"Unknown network: {network}")
        
        self.w3 = Web3(Web3.HTTPProvider(net_config["rpc"]))

        # Add PoA middleware for BSC
        if "bsc" in network:
            try:
                from web3.middleware import geth_poa_middleware
                self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            except ImportError:
                log("PoA middleware not available", Colors.YELLOW)
                
        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to {network}")
        
        log(f"✓ Connected to {network}", Colors.GREEN)
        
        # Setup account
        self.account = self.w3.eth.account.from_key(private_key)
        self.address = self.account.address
        log(f"Wallet: {self.address}", Colors.CYAN)
        
        # Load config
        self.config = CONTRACT_CONFIG.get(network, {})
        
        # For price fetching, ALWAYS use mainnet
        mainnet_config = NETWORKS.get("bsc_mainnet")
        self.w3_mainnet = Web3(Web3.HTTPProvider(mainnet_config["rpc"]))
        try:
            from web3.middleware import geth_poa_middleware
            self.w3_mainnet.middleware_onion.inject(geth_poa_middleware, layer=0)
        except:
            pass
        
        if self.w3_mainnet.is_connected():
            log(f"✓ Connected to BSC mainnet (price oracle)", Colors.GREEN)
        
        self.mainnet_config = CONTRACT_CONFIG.get("bsc_mainnet", {})
        
        # Load ABIs
        log("Loading ABIs...", Colors.BLUE)
        self.arbitrage_abi = load_abi("FlashLoanArbitrage.json")
        self.router_abi = load_abi("RouterV2.json")
        self.erc20_abi = load_abi("ERC20.json")
        
        # For testnet, also load mock router ABI
        if "testnet" in network:
            try:
                self.router_mock_abi = load_abi("RouterV2Mock.json")
                log("ABIs loaded (including mock router)", Colors.GREEN)
            except:
                log("⚠️  RouterV2Mock.json not found - dynamic config disabled", Colors.YELLOW)
                self.router_mock_abi = None
        else:
            log("ABIs loaded", Colors.GREEN)
            self.router_mock_abi = None
        
        # Initialize contracts
        self.arbitrage_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.config["arbitrage"]),
            abi=self.arbitrage_abi,
        )
        
        # Mainnet routers for price fetching
        self.mainnet_routers = {}
        for name, addr in self.mainnet_config.get("v2_routers", {}).items():
            self.mainnet_routers[name] = self.w3_mainnet.eth.contract(
                address=Web3.to_checksum_address(addr),
                abi=self.router_abi,
            )
            log(f"  Router (mainnet): {name}", Colors.CYAN)
        
        # Testnet mock routers for configuration
        self.testnet_mock_routers = {}
        if "testnet" in network and self.router_mock_abi:
            for name, addr in self.config.get("v2_routers", {}).items():
                self.testnet_mock_routers[name] = self.w3.eth.contract(
                    address=Web3.to_checksum_address(addr),
                    abi=self.router_mock_abi,
                )
        
        # Token addresses
        self.tokens = self.config.get("tokens", {})
        self.mainnet_tokens = self.mainnet_config.get("tokens", {})
        
        # Initialize database
        if DATABASE_AVAILABLE:
            self._init_database()
    
    def _init_database(self):
        """Initialize database connection"""
        self.db = ArbitrageDatabase(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "arbitrage_db"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "password"),
        )
        
        if self.db.connect():
            self.db.create_tables()
            self.session_id = self.db.start_session()
            if self.session_id:
                log(f"Database session started (ID: {self.session_id})", Colors.GREEN)
        else:
            log("Running without database", Colors.YELLOW)
            self.db = None
    
    def _configure_testnet_routers(self, opportunity: Dict) -> bool:
        """
        Configure testnet mock routers with the opportunity's prices
        This makes the routers return the expected amounts for this specific arbitrage
        """
        if "mainnet" in self.network or not self.router_mock_abi:
            return True  # Skip for mainnet or if mock ABI not available
        
        log("  🔧 Configuring testnet routers for this opportunity...", Colors.CYAN)
        
        try:
            BORROW_AMOUNT = TRADING_CONFIG["borrow_amount"]
            
            # Get DEX fees
            PANCAKE_FEE = 0.0025  # 0.25%
            BISWAP_FEE = 0.001    # 0.1%
            
            buy_router_name = opportunity["buy_router"]
            sell_router_name = opportunity["sell_router"]
            buy_price = opportunity["buy_price"]
            sell_price = opportunity["sell_price"]
            
            buy_fee = PANCAKE_FEE if buy_router_name == "pancakeswap" else BISWAP_FEE
            sell_fee = PANCAKE_FEE if sell_router_name == "pancakeswap" else BISWAP_FEE
            
            # Calculate expected outputs
            # Buy router: BUSD -> WBNB
            wbnb_bought = (BORROW_AMOUNT / buy_price) * (1 - buy_fee)
            buy_output_wei = int(wbnb_bought * 10**18)
            
            # Sell router: WBNB -> BUSD
            busd_received = wbnb_bought * sell_price * (1 - sell_fee)
            sell_output_wei = int(busd_received * 10**18)
            
            log(f"     Buy on {buy_router_name}: {BORROW_AMOUNT} BUSD → {wbnb_bought:.6f} WBNB", Colors.CYAN)
            log(f"     Sell on {sell_router_name}: {wbnb_bought:.6f} WBNB → {busd_received:.6f} BUSD", Colors.CYAN)
            
            # Configure both routers
            success_count = 0
            
            for router_name, output_wei in [(buy_router_name, buy_output_wei), (sell_router_name, sell_output_wei)]:
                try:
                    router_contract = self.testnet_mock_routers.get(router_name)
                    if not router_contract:
                        log(f"     ⚠️  {router_name} contract not found", Colors.YELLOW)
                        continue
                    
                    # Get fresh nonce
                    nonce = self.w3.eth.get_transaction_count(self.address, 'pending')
                    
                    # Build transaction
                    tx = router_contract.functions.setMockOutput(
                        output_wei
                    ).build_transaction({
                        "from": self.address,
                        "gas": 100000,
                        "gasPrice": self.w3.eth.gas_price,
                        "nonce": nonce,
                    })
                    
                    # Sign and send
                    signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
                    tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                    
                    # Wait for confirmation
                    receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
                    
                    if receipt["status"] == 1:
                        log(f"     ✓ {router_name} configured", Colors.GREEN)
                        success_count += 1
                    else:
                        log(f"     ❌ {router_name} config failed", Colors.RED)
                        
                except Exception as e:
                    log(f"     ❌ Error configuring {router_name}: {str(e)[:50]}", Colors.RED)
            
            if success_count == 2:
                log(f"  ✓ Both routers configured successfully!", Colors.GREEN)
                return True
            else:
                log(f"  ⚠️  Only {success_count}/2 routers configured", Colors.YELLOW)
                return False
                
        except Exception as e:
            log(f"  ❌ Router configuration failed: {str(e)}", Colors.RED)
            return False
    
    def get_balance(self) -> float:
        """Get native token balance"""
        balance = self.w3.eth.get_balance(self.address)
        return self.w3.from_wei(balance, "ether")
    
    def get_mainnet_price(self, router_contract, amount_in: int, path: list) -> Optional[int]:
        """Get price from mainnet DEX"""
        try:
            path_checksum = [Web3.to_checksum_address(addr) for addr in path]
            amounts = router_contract.functions.getAmountsOut(amount_in, path_checksum).call()
            return amounts[-1]
        except Exception as e:
            log(f"Price fetch error: {str(e)[:50]}", Colors.RED)
            return None
    
    def find_arbitrage_opportunity(self) -> Dict:
        """
        Find arbitrage opportunity - MATCHES DEMO LOGIC EXACTLY
        Gets WBNB price, then simulates with DEX fees like demo does
        """
        # Use mainnet tokens for price checking
        token_borrow = self.mainnet_tokens.get(TRADING_CONFIG["borrow_token"])
        token_intermediate = self.mainnet_tokens.get(TRADING_CONFIG["trade_token"])
        
        if not token_borrow or not token_intermediate:
            log("Missing token configuration", Colors.RED)
            return {"prices": {}, "spreads": {}, "profits": {}, "opportunity": None}
        
        # Get WBNB price on each DEX (like demo does)
        # Use 1 WBNB to get price per WBNB in BUSD
        wbnb_amount = 10**18  # 1 WBNB
        path_wbnb_to_busd = [token_intermediate, token_borrow]  # WBNB -> BUSD
        
        wbnb_prices = {}
        router_names = list(self.mainnet_routers.keys())
        
        for router_name in router_names:
            router = self.mainnet_routers[router_name]
            # Get how much BUSD for 1 WBNB (this is the price)
            busd_for_wbnb = self.get_mainnet_price(router, wbnb_amount, path_wbnb_to_busd)
            if busd_for_wbnb:
                # Convert to float price (BUSD per WBNB)
                price = self.w3.from_wei(busd_for_wbnb, 'ether')
                wbnb_prices[router_name] = price
        
        if len(wbnb_prices) < 2:
            return {"prices": {}, "spreads": {}, "profits": {}, "opportunity": None}
        
        # Now simulate arbitrage using these prices
        FLASH_LOAN = TRADING_CONFIG["borrow_amount"]
        GAS_COST = TRADING_CONFIG.get("gas_cost_usd", 0.08)
        PANCAKE_FEE = 0.0025  # 0.25%
        BISWAP_FEE = 0.001    # 0.1%
        MIN_PROFIT = TRADING_CONFIG["min_profit"]
        
        all_spreads = {}
        all_profits = {}
        best_opportunity = None
        
        # Check both directions
        for buy_router in router_names:
            for sell_router in router_names:
                if buy_router == sell_router:
                    continue
                
                if buy_router not in wbnb_prices or sell_router not in wbnb_prices:
                    continue
                
                buy_price = float(wbnb_prices[buy_router])
                sell_price = float(wbnb_prices[sell_router])
                
                # Get DEX fees
                buy_fee = PANCAKE_FEE if buy_router == "pancakeswap" else BISWAP_FEE
                sell_fee = PANCAKE_FEE if sell_router == "pancakeswap" else BISWAP_FEE
                
                # DODO FLASH LOAN CALCULATION (fee is configurable, typically 0%)
                # DODO fee is paid on REPAYMENT, not deducted upfront
                # So we trade with the FULL borrowed amount
                borrowed = float(FLASH_LOAN)
                
                # Step 1: Buy tokens with FULL borrowed amount
                tokens_bought = (borrowed / buy_price) * (1 - buy_fee)
                
                # Step 2: Sell tokens
                tokens_after_sell_fee = tokens_bought * (1 - sell_fee)
                usd_return = tokens_after_sell_fee * sell_price
                
                # Step 3: Calculate DODO repayment (with fee if any)
                flash_loan_fee_pct = TRADING_CONFIG.get("flash_loan_fee", 0.0)
                dodo_repay = borrowed * (1 + flash_loan_fee_pct)
                
                # Step 4: Calculate profits
                gross_profit = float(usd_return - dodo_repay)
                net_profit = float(gross_profit - GAS_COST)
                roi = (net_profit / borrowed) * 100
                
                # Calculate spread for display
                spread = ((sell_price - buy_price) / buy_price) * 100
                
                # Store all paths
                path_key = f"{buy_router}_to_{sell_router}"
                all_spreads[path_key] = spread
                all_profits[path_key] = self.w3.to_wei(net_profit, 'ether') if net_profit >= 0 else -self.w3.to_wei(abs(net_profit), 'ether')
                
                # Track best opportunity based on spread (pre-filter)
                # Contract will enforce min_profit, we just check if spread is promising
                if abs(spread) > TRADING_CONFIG["min_spread_pct"]:
                    if best_opportunity is None or abs(spread) > abs(best_opportunity.get("spread", 0)):
                        # Handle negative values for wei conversion
                        borrow_wei = self.w3.to_wei(FLASH_LOAN, 'ether')
                        tokens_wei = self.w3.to_wei(tokens_bought, 'ether')
                        usd_return_wei = self.w3.to_wei(usd_return, 'ether')
                        
                        # For negative profits, store as negative integer (not wei)
                        if gross_profit >= 0:
                            gross_profit_wei = self.w3.to_wei(gross_profit, 'ether')
                        else:
                            gross_profit_wei = -self.w3.to_wei(abs(gross_profit), 'ether')
                        
                        if net_profit >= 0:
                            net_profit_wei = self.w3.to_wei(net_profit, 'ether')
                        else:
                            net_profit_wei = -self.w3.to_wei(abs(net_profit), 'ether')
                        
                        best_opportunity = {
                        "buy_router": buy_router,
                        "sell_router": sell_router,
                        "buy_router_addr": self.config["v2_routers"][buy_router],
                        "sell_router_addr": self.config["v2_routers"][sell_router],
                        "borrow_amount": borrow_wei,
                        "intermediate_amount": tokens_wei,
                        "final_amount": usd_return_wei,
                        "spread": spread,
                        "estimated_gross_profit": gross_profit_wei,
                        "estimated_net_profit": net_profit_wei,
                        "buy_price": buy_price,
                        "sell_price": sell_price,
                    }
        
        return {
            "prices": wbnb_prices,
            "spreads": all_spreads,
            "profits": all_profits,
            "opportunity": best_opportunity
        }
    
    def execute_arbitrage_v2(self, opportunity: Dict) -> Optional[str]:
        """
        Execute arbitrage via smart contract executeArbitrageV2
        The contract handles: flash loan, swaps, profit calculation, repayment
        """
        if self.dry_run:
            log("🔶 DRY RUN - Would execute arbitrage:", Colors.YELLOW)
            log(f"  Buy Router: {opportunity['buy_router']}", Colors.YELLOW)
            log(f"  Sell Router: {opportunity['sell_router']}", Colors.YELLOW)
            log(f"  Borrow: {self.w3.from_wei(opportunity['borrow_amount'], 'ether')} {TRADING_CONFIG['borrow_token']}", Colors.YELLOW)
            log(f"  Spread: {opportunity['spread']:.4f}%", Colors.YELLOW)
            
            # Configure routers even in dry run for testing
            if "testnet" in self.network:
                self._configure_testnet_routers(opportunity)
            
            return "DRY_RUN"
        
        try:
            # ⭐ CONFIGURE TESTNET ROUTERS FIRST (if testnet)
            if "testnet" in self.network:
                router_config_success = self._configure_testnet_routers(opportunity)
                if not router_config_success:
                    log("⚠️  Router configuration incomplete - continuing anyway", Colors.YELLOW)
            
            # Get token addresses (execution network)
            token_borrow = self.tokens.get(TRADING_CONFIG["borrow_token"])
            token_intermediate = self.tokens.get(TRADING_CONFIG["trade_token"])
            
            # Build paths (execution network tokens)
            path_buy = [token_borrow, token_intermediate]
            path_sell = [token_intermediate, token_borrow]
            
            # Min profit in wei (contract will revert if not met)
            min_profit = self.w3.to_wei(TRADING_CONFIG["min_profit"], "ether")
            
            log("📝 Building transaction...", Colors.BLUE)
            
            # Build transaction for executeArbitrageV2
            tx = self.arbitrage_contract.functions.executeArbitrageV2(
                Web3.to_checksum_address(token_borrow),              # borrowedToken
                opportunity["borrow_amount"],                         # borrowAmount
                True,                                                 # isBase (BUSD is base token)
                Web3.to_checksum_address(opportunity["buy_router_addr"]),   # buyRouter
                Web3.to_checksum_address(opportunity["sell_router_addr"]),  # sellRouter
                [Web3.to_checksum_address(t) for t in path_buy],     # pathBuy
                [Web3.to_checksum_address(t) for t in path_sell],    # pathSell
                min_profit,                                           # minProfit
            ).build_transaction({
                "from": self.address,
                "gas": 400000,  # Estimate - adjust if needed
                "gasPrice": self.w3.eth.gas_price,
                "nonce": self.w3.eth.get_transaction_count(self.address, 'pending'),
            })
            
            # Simulate transaction first to catch reverts early
            log("🔍 Simulating transaction...", Colors.BLUE)
            try:
                self.w3.eth.call(tx)
                log("   ✓ Simulation passed", Colors.GREEN)
            except Exception as sim_error:
                error_msg = str(sim_error)
                log(f"❌ Simulation FAILED - Would revert on-chain!", Colors.RED)
                
                # Decode custom errors from your smart contract
                # Error signatures (first 4 bytes of keccak256 hash)
                ERROR_SIGNATURES = {
                    "0xe450d38c": "InvalidCallback",
                    "0x82b42900": "Unauthorized", 
                    "0x6bb6d469": "InsufficientProfit",
                    "0x386691c6": "InvalidAmount",
                    "0x8baa579f": "InvalidToken",
                }
                
                # Check if it's a custom error
                decoded_error = None
                for sig, error_name in ERROR_SIGNATURES.items():
                    if sig in error_msg:
                        decoded_error = error_name
                        break
                
                if decoded_error == "InsufficientProfit":
                    log(f"   Reason: InsufficientProfit", Colors.YELLOW)
                    log(f"   → Actual profit < minProfit ({self.w3.from_wei(min_profit, 'ether')} {TRADING_CONFIG['borrow_token']})", Colors.YELLOW)
                    log(f"   → Router outputs may not match expected prices", Colors.YELLOW)
                elif decoded_error == "InvalidCallback":
                    log(f"   Reason: InvalidCallback", Colors.YELLOW)
                    log(f"   → Flash loan callback validation failed", Colors.YELLOW)
                elif decoded_error == "Unauthorized":
                    log(f"   Reason: Unauthorized", Colors.YELLOW)
                    log(f"   → Only contract owner can execute", Colors.YELLOW)
                elif decoded_error == "InvalidAmount":
                    log(f"   Reason: InvalidAmount", Colors.YELLOW)
                    log(f"   → Borrow amount is 0 or invalid", Colors.YELLOW)
                elif "execution reverted" in error_msg.lower():
                    # Generic revert
                    if ":" in error_msg:
                        reason = error_msg.split(":")[-1].strip()[:200]
                        log(f"   Reason: {reason}", Colors.YELLOW)
                    else:
                        log(f"   Reason: Generic revert", Colors.YELLOW)
                else:
                    # Unknown error
                    log(f"   Reason: {error_msg[:200]}...", Colors.YELLOW)
                
                log(f"   💡 Transaction not sent - no gas wasted!", Colors.CYAN)
                return None
            
            log("✍️  Signing transaction...", Colors.BLUE)
            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            
            log("📤 Sending transaction...", Colors.BLUE)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            
            log(f"📨 TX Hash: {tx_hash.hex()}", Colors.GREEN)
            
            # Wait for confirmation
            log("⏳ Waiting for confirmation...", Colors.BLUE)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            if receipt["status"] == 1:
                gas_used = receipt["gasUsed"]
                gas_price = tx["gasPrice"]
                gas_cost = self.w3.from_wei(gas_used * gas_price, "ether")
                
                log(f"✅ ARBITRAGE SUCCESSFUL!", Colors.GREEN)
                log(f"   Gas used: {gas_used} ({gas_cost:.6f} BNB)", Colors.CYAN)
                
                # Try to get profit from event logs
                if receipt["logs"]:
                    log(f"   Event logs: {len(receipt['logs'])} events emitted", Colors.CYAN)
                
                return tx_hash.hex()
            else:
                log(f"❌ Transaction REVERTED", Colors.RED)
                
                # Try to get revert reason
                try:
                    # Replay the transaction to get the revert reason
                    self.w3.eth.call(tx, receipt["blockNumber"])
                except Exception as e:
                    error_msg = str(e)
                    
                    # Parse common revert reasons
                    if "InsufficientProfit" in error_msg:
                        log(f"   Revert Reason: InsufficientProfit", Colors.YELLOW)
                        log(f"   → Actual profit was below minProfit threshold", Colors.YELLOW)
                    elif "execution reverted" in error_msg:
                        # Try to extract the revert message
                        if ":" in error_msg:
                            reason = error_msg.split(":")[-1].strip()
                            log(f"   Revert Reason: {reason}", Colors.YELLOW)
                        else:
                            log(f"   Revert Reason: {error_msg}", Colors.YELLOW)
                    else:
                        log(f"   Revert Reason: {error_msg}", Colors.YELLOW)
                
                return None
                
        except Exception as e:
            log(f"❌ Execution error: {str(e)}", Colors.RED)
            return None
    
    def run(self, interval: float = 5.0):
        """Main bot loop - scan for opportunities and execute"""
        print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 80}")
        print("FLASH LOAN ARBITRAGE BOT - LIVE EXECUTION")
        print("Auto-configures testnet routers before each execution")
        print(f"{'=' * 80}{Colors.END}\n")
        
        print(f"{Colors.BLUE}Configuration:{Colors.END}")
        print(f"  Network:           {self.network}")
        print(f"  Price Oracle:      BSC Mainnet")
        print(f"  Wallet:            {self.address}")
        print(f"  Contract:          {self.config['arbitrage']}")
        print(f"  Borrow Token:      {TRADING_CONFIG['borrow_token']}")
        print(f"  Trade Token:       {TRADING_CONFIG['trade_token']}")
        print(f"  Borrow Amount:     {TRADING_CONFIG['borrow_amount']} tokens")
        print(f"  Flash Loan Fee:    {TRADING_CONFIG.get('flash_loan_fee', 0.0)*100:.2f}%")
        print(f"  Gas Cost:          ${TRADING_CONFIG.get('gas_cost_usd', 0.08)}")
        print(f"  Min Spread:        {TRADING_CONFIG['min_spread_pct']}%")
        print(f"  Min Profit:        {TRADING_CONFIG['min_profit']} {TRADING_CONFIG['borrow_token']}")
        print(f"  Dry Run:           {'Yes ✓' if self.dry_run else 'NO - LIVE! ⚠️'}")
        print(f"  Database:          {'Enabled ✓' if self.db else 'Disabled'}")
        print(f"  DEX Routers:       {list(self.mainnet_routers.keys())}")
        print(f"  Dynamic Config:    {'Enabled ✓' if self.router_mock_abi and 'testnet' in self.network else 'Disabled'}")
        
        balance = self.get_balance()
        print(f"  Balance:           {balance:.4f} BNB\n")
        
        if not self.dry_run:
            print(f"{Colors.RED}{Colors.BOLD}⚠️  LIVE MODE - REAL TRANSACTIONS!{Colors.END}")
            print(f"{Colors.YELLOW}The smart contract will execute real trades with real money!{Colors.END}\n")
        
        if "testnet" in self.network and self.router_mock_abi:
            print(f"{Colors.CYAN}💡 Testnet Mode: Routers will be auto-configured before each execution{Colors.END}\n")
        
        log("🚀 Starting arbitrage bot...", Colors.GREEN)
        
        iteration = 0
        opportunities_found = 0
        executions_attempted = 0
        executions_successful = 0
        
        try:
            while True:
                iteration += 1
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                
                # Find opportunity (always returns price data)
                result = self.find_arbitrage_opportunity()
                
                prices = result.get("prices", {})
                spreads = result.get("spreads", {})
                profits = result.get("profits", {})
                opp = result.get("opportunity")
                
                # Log to database
                scan_id = None
                prices_changed = prices != {}
                if self.db and len(prices) >= 2:
                    price_list = list(prices.values())
                    overall_spread = abs(price_list[0] - price_list[1]) / min(price_list) * 100
                    
                    # Calculate net profit for this scan
                    best_net_profit = 0
                    if opp:
                        net_profit_value = opp.get('estimated_net_profit', 0)
                        best_net_profit = float(net_profit_value) / 1e18 if net_profit_value >= 0 else -float(abs(net_profit_value)) / 1e18
                    
                    scan_id = self.db.log_price_scan(
                        pancake_price=float(price_list[0]),
                        biswap_price=float(price_list[1]) if len(price_list) > 1 else 0,
                        spread=overall_spread,
                        price_changed=prices_changed,
                        best_gross_profit=best_net_profit,
                    )
                
                # Display (matching demo style)
                if len(prices) >= 2:
                    print(f"\n{Colors.BOLD}[{timestamp}] Update #{iteration}{Colors.END}")
                    
                    # Show WBNB prices (like demo)
                    for router_name, price in sorted(prices.items()):
                        print(f"  {router_name.capitalize()}: ${price:.6f}")
                    
                    # Calculate and show spread
                    price_list = list(prices.values())
                    if len(price_list) >= 2:
                        overall_spread = abs(price_list[0] - price_list[1]) / min(price_list) * 100
                        print(f"  Spread:      {overall_spread:.4f}%")
                    
                    if scan_id:
                        print(f"  DB Scan ID: {scan_id}")
                    
                    # Show if opportunity exists
                    if opp:
                        opportunities_found += 1
                        # Handle negative profit display
                        net_profit_value = opp['estimated_net_profit']
                        if net_profit_value >= 0:
                            net_profit_display = self.w3.from_wei(net_profit_value, 'ether')
                        else:
                            net_profit_display = -self.w3.from_wei(abs(net_profit_value), 'ether')
                        
                        print(f"\n{Colors.GREEN}{Colors.BOLD}🔥 OPPORTUNITY #{opportunities_found}!{Colors.END}")
                        print(f"  Strategy: Buy {opp['buy_router'].capitalize()} → Sell {opp['sell_router'].capitalize()}")
                        print(f"  Net Profit: {Colors.GREEN}${net_profit_display:.4f}{Colors.END}")
                        
                        # Log opportunity to database
                        if self.db and scan_id:
                            db_opp = {
                                "buy_dex": opp["buy_router"],
                                "sell_dex": opp["sell_router"],
                                "buy_price": float(opp["buy_price"]),
                                "sell_price": float(opp["sell_price"]),
                                "net": net_profit_display,
                                "flash_loan_amount": float(TRADING_CONFIG["borrow_amount"]),
                            }
                            self.db.log_arbitrage_opportunity(scan_id, db_opp)
                            log(f"Opportunity logged to database", Colors.CYAN)
                        
                        # Execute via smart contract
                        log("⚡ Executing arbitrage...", Colors.BOLD)
                        executions_attempted += 1
                        
                        tx_hash = self.execute_arbitrage_v2(opp)
                        
                        if tx_hash and tx_hash != "DRY_RUN":
                            executions_successful += 1
                            explorer_url = f"{NETWORKS[self.network]['explorer']}/tx/{tx_hash}" if NETWORKS[self.network]['explorer'] else None
                            if explorer_url:
                                print(f"{Colors.GREEN}🔗 {explorer_url}{Colors.END}\n")
                    else:
                        print(f"  {Colors.YELLOW}No opportunity{Colors.END}")
                else:
                    # No price data
                    print(f"[{timestamp}] Scan #{iteration} - Failed to fetch prices", end='\r')
                
                time.sleep(interval)
                
        except KeyboardInterrupt:
            print(f"\n\n{Colors.YELLOW}Bot stopped by user{Colors.END}")
            print(f"\n{Colors.CYAN}{'=' * 80}{Colors.END}")
            print(f"{Colors.BOLD}📊 SESSION SUMMARY:{Colors.END}")
            print(f"{Colors.CYAN}{'=' * 80}{Colors.END}")
            print(f"  Total Scans:           {iteration}")
            print(f"  Opportunities Found:   {opportunities_found}")
            print(f"  Executions Attempted:  {executions_attempted}")
            print(f"  Executions Successful: {executions_successful}")
            if executions_attempted > 0:
                success_rate = (executions_successful / executions_attempted) * 100
                print(f"  Success Rate:          {success_rate:.1f}%")
            print(f"{Colors.CYAN}{'=' * 80}{Colors.END}\n")
            
            # End database session
            if self.db and self.session_id:
                self.db.end_session(self.session_id, iteration, opportunities_found)
                self.db.close()
                log(f"Database session ended", Colors.CYAN)
                
                # Show statistics
                stats = self.db.get_statistics(hours=24)
                if stats:
                    print(f"\n{Colors.CYAN}{'=' * 80}{Colors.END}")
                    print(f"{Colors.BOLD}📊 SESSION STATISTICS:{Colors.END}")
                    print(f"{Colors.CYAN}{'=' * 80}{Colors.END}")
                    print(f"  Total Scans:          {stats.get('total_scans', 0)}")
                    print(f"  Price Changes:        {stats.get('price_changes', 0)}")
                    print(f"  Scans with Profit:    {stats.get('scans_with_profit', 0)}")
                    print(f"\n  {Colors.CYAN}Spread Analysis:{Colors.END}")
                    print(f"    Average:            {float(stats.get('avg_spread', 0)):.4f}%")
                    print(f"    Maximum:            {float(stats.get('max_spread', 0)):.4f}%")
                    print(f"    Minimum:            {float(stats.get('min_spread', 0)):.4f}%")
                    print(f"\n  {Colors.CYAN}Net Profit Analysis:{Colors.END}")
                    if stats.get('avg_gross_profit'):
                        print(f"    Average:            {float(stats.get('avg_gross_profit', 0)):.6f} {TRADING_CONFIG['borrow_token']}")
                        print(f"    Maximum:            {float(stats.get('max_gross_profit', 0)):.6f} {TRADING_CONFIG['borrow_token']}")
                    print(f"\n  {Colors.CYAN}Profitable Opportunities:{Colors.END}")
                    print(f"    Found:              {stats.get('total_opportunities', 0)}")
                    if stats.get('total_potential_profit'):
                        print(f"    Total Potential:    {float(stats.get('total_potential_profit', 0)):.4f} {TRADING_CONFIG['borrow_token']}")
                        print(f"    Avg Net Profit:     {float(stats.get('avg_profit', 0)):.4f} {TRADING_CONFIG['borrow_token']}")
                        print(f"    Max Net Profit:     {float(stats.get('max_profit', 0)):.4f} {TRADING_CONFIG['borrow_token']}")
                    print(f"{Colors.CYAN}{'=' * 80}{Colors.END}\n")
            
            log("Goodbye! 👋", Colors.YELLOW)

def main():
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 80}")
    print("FLASH LOAN ARBITRAGE BOT")
    print("Live Smart Contract Execution with Dynamic Router Config")
    print(f"{'=' * 80}{Colors.END}\n")
    
    if not WEB3_AVAILABLE:
        print(f"{Colors.RED}Error: Web3.py is required{Colors.END}")
        return
    
    # Get private key
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        print(f"{Colors.RED}Error: PRIVATE_KEY environment variable not set{Colors.END}")
        return
    
    network = os.getenv("NETWORK", "bsc_testnet")
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    
    log(f"Initializing bot for {network}...", Colors.BLUE)
    
    try:
        bot = ArbitrageBot(private_key, network, dry_run)
        bot.run(interval=10)
    except Exception as e:
        log(f"Fatal error: {e}", Colors.RED)
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()