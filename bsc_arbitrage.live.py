"""
Flash Loan Arbitrage Bot - Live Execution Version
Uses deployed FlashLoanArbitrage contract for real trades
Supports BSC Mainnet/Testnet and other EVM chains
WITH DATABASE LOGGING AND SPREAD CALCULATIONS
V2 ROUTERS ONLY
ALWAYS FETCHES PRICES FROM MAINNET
WITH PROPER NET PROFIT CALCULATIONS
"""
import time
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Tuple
from decimal import Decimal
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

# === FEE CONFIGURATION ===
FLASH_LOAN_FEE_PCT = 0.0009  # 0.09% flash loan fee
GAS_COST_USD = 0.08  # Estimated gas cost in USD

# === TRADING CONFIGURATION ===
TRADING_CONFIG = {
    "borrow_token": "BUSD",      # Token to borrow via flash loan
    "trade_token": "WBNB",       # Token to trade/intermediate token
    "borrow_amount": 100,         # Amount to borrow (in whole tokens)
    "min_profit": 0.01,          # Minimum NET profit required (in borrowed token)
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
    "localhost": {
        "rpc": "http://127.0.0.1:7545",
        "chain_id": 31337,
        "explorer": None,
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
        "arbitrage": "0xd78f4b1452e9314096e711ce3efe7c00d5612cdf",
        "dodo_pool": "0x0df90e293e2cf231f93736a0d46b1df59086834a",
        "v2_routers": {
            "pancakeswap": "0x5ffb2e1aa043bfee7cca97f0178d24fbe38e9575",
            "biswap": "0xcecb74ac39d184fb1ee5915783f3f8c88366e70c",
        },
        "tokens": {
            "WBNB": "0xa77e9383370472e84ab89196a83c0c33f295c95b",
            "BUSD": "0x142f562e6c384777195bcf76fd7df360808d0a89",
            "USDT": "0x8a9424745056eb399fd19a0ec26a14316684e274",
        },
    },
    "localhost": {
        "arbitrage": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        "dodo_pool": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        "v2_routers": {
            "router_v2": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        },
        "tokens": {
            "TokenA": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
            "TokenB": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        },
    },
}

# === ABI PATHS ===
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
    
    # Handle both raw ABI arrays and Hardhat artifact format
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
        
        # Connect to execution network (where we submit transactions)
        net_config = NETWORKS.get(network)
        if not net_config:
            raise ValueError(f"Unknown network: {network}")
        
        self.w3 = Web3(Web3.HTTPProvider(net_config["rpc"]))

        # Add PoA middleware for BSC
        if "bsc" in network:
            try:
                from web3.middleware import ExtraDataToPOAMiddleware
                self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            except ImportError:
                try:
                    from web3.middleware import geth_poa_middleware
                    self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
                except ImportError:
                    log("PoA middleware not available, continuing anyway...", Colors.YELLOW)
                
        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to {network}")
        
        log(f"✓ Connected to {network} (execution)", Colors.GREEN)
        
        # ALWAYS connect to mainnet for price fetching
        mainnet_config = NETWORKS.get("bsc_mainnet")
        self.w3_mainnet = Web3(Web3.HTTPProvider(mainnet_config["rpc"]))
        
        try:
            from web3.middleware import ExtraDataToPOAMiddleware
            self.w3_mainnet.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except ImportError:
            try:
                from web3.middleware import geth_poa_middleware
                self.w3_mainnet.middleware_onion.inject(geth_poa_middleware, layer=0)
            except ImportError:
                pass
        
        if not self.w3_mainnet.is_connected():
            raise ConnectionError("Failed to connect to BSC mainnet for price fetching")
        
        log(f"✓ Connected to BSC mainnet (price oracle)", Colors.GREEN)
        
        # Setup account
        self.account = self.w3.eth.account.from_key(private_key)
        self.address = self.account.address
        log(f"Wallet: {self.address}", Colors.CYAN)
        
        # Load contract configs
        self.config = CONTRACT_CONFIG.get(network, {})
        self.mainnet_config = CONTRACT_CONFIG.get("bsc_mainnet", {})
        
        # Load ABIs
        log("Loading ABIs...", Colors.BLUE)
        self.arbitrage_abi = load_abi("FlashLoanArbitrage.json")
        self.router_abi = load_abi("RouterV2.json")
        self.erc20_abi = load_abi("ERC20.json")
        log("ABIs loaded successfully", Colors.GREEN)
        
        # Initialize execution contract (on target network)
        self.arbitrage_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.config["arbitrage"]),
            abi=self.arbitrage_abi,
        )
        
        # Initialize EXECUTION routers (on target network - for transactions)
        self.execution_routers = {}
        for name, addr in self.config.get("v2_routers", {}).items():
            self.execution_routers[name] = {
                "contract": self.w3.eth.contract(
                    address=Web3.to_checksum_address(addr),
                    abi=self.router_abi,
                ),
                "version": 0,
                "address": addr,
            }
            log(f"  Execution Router loaded: {name} ({network})", Colors.CYAN)
        
        # Initialize MAINNET routers (for price fetching ONLY)
        self.mainnet_routers = {}
        for name, addr in self.mainnet_config.get("v2_routers", {}).items():
            self.mainnet_routers[name] = {
                "contract": self.w3_mainnet.eth.contract(
                    address=Web3.to_checksum_address(addr),
                    abi=self.router_abi,
                ),
                "version": 0,
                "address": addr,
            }
            log(f"  Price Oracle Router: {name} (mainnet)", Colors.BLUE)
        
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
            log("Running without database logging", Colors.YELLOW)
            self.db = None
    
    def get_balance(self) -> float:
        """Get native token balance"""
        balance = self.w3.eth.get_balance(self.address)
        return self.w3.from_wei(balance, "ether")
    
    def get_token_balance(self, token_address: str) -> Tuple[int, int]:
        """Get ERC20 token balance and decimals"""
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=self.erc20_abi,
        )
        balance = token.functions.balanceOf(self.address).call()
        decimals = token.functions.decimals().call()
        return balance, decimals
    
    def get_mainnet_price(self, router_name: str, amount_in: int, path: list) -> Optional[int]:
        """Get output amount from MAINNET router (for price oracle)"""
        try:
            router = self.mainnet_routers.get(router_name)
            if not router:
                log(f"Router {router_name} not found", Colors.RED)
                return None
            
            path_checksum = [Web3.to_checksum_address(addr) for addr in path]
            amounts = router["contract"].functions.getAmountsOut(
                amount_in, path_checksum
            ).call()
            
            return amounts[-1]
        except Exception as e:
            log(f"Mainnet price fetch error ({router_name}): {str(e)[:100]}", Colors.RED)
            return None
    
    def find_arbitrage(
        self,
        token_borrow: str,
        token_intermediate: str,
        borrow_amount: int,
    ) -> Optional[Dict]:
        """Find best arbitrage opportunity using MAINNET prices"""
        # Use MAINNET token addresses for price checking
        mainnet_token_borrow = self.mainnet_tokens.get(TRADING_CONFIG["borrow_token"])
        mainnet_token_intermediate = self.mainnet_tokens.get(TRADING_CONFIG["trade_token"])
        
        if not mainnet_token_borrow or not mainnet_token_intermediate:
            log("Missing mainnet token configuration", Colors.RED)
            return None
        
        path_buy = [mainnet_token_borrow, mainnet_token_intermediate]
        path_sell = [mainnet_token_intermediate, mainnet_token_borrow]
        
        best_opportunity = None
        best_net_profit = 0
        
        router_names = list(self.mainnet_routers.keys())
        prices = {}
        
        # Get all prices from MAINNET (for display purposes - WBNB price in BUSD)
        # Use 1 WBNB (1e18) to get the price per WBNB
        wbnb_amount = 10**18  # 1 WBNB
        path_price_check = [mainnet_token_intermediate, mainnet_token_borrow]  # WBNB -> BUSD
        
        for router_name in router_names:
            price_output = self.get_mainnet_price(router_name, wbnb_amount, path_price_check)
            if price_output:
                # This gives us BUSD per WBNB (the actual BNB price)
                prices[router_name] = price_output
        
        # Calculate spreads between all router pairs
        spreads = {}
        for router1 in router_names:
            for router2 in router_names:
                if router1 != router2 and router1 in prices and router2 in prices:
                    spread = ((prices[router2] - prices[router1]) / prices[router1]) * 100
                    spreads[f"{router1}_to_{router2}"] = spread
        
        # Calculate flash loan fee
        flash_loan_fee = int(borrow_amount * FLASH_LOAN_FEE_PCT)
        
        # Calculate gas cost in borrow token (assuming BUSD ~= $1)
        gas_cost_tokens = self.w3.to_wei(GAS_COST_USD, 'ether')
        
        # Find best arbitrage opportunity using actual trade amounts
        for buy_router in router_names:
            for sell_router in router_names:
                if buy_router == sell_router:
                    continue  # Skip same router
                
                # Get buy price (MAINNET) - BUSD -> WBNB
                intermediate_amount = self.get_mainnet_price(buy_router, borrow_amount, path_buy)
                if not intermediate_amount:
                    continue
                
                # Get sell price (MAINNET) - WBNB -> BUSD
                final_amount = self.get_mainnet_price(sell_router, intermediate_amount, path_sell)
                if not final_amount:
                    continue
                
                # Calculate gross profit (before fees)
                gross_profit = final_amount - borrow_amount
                
                # Calculate net profit (after all fees)
                net_profit = gross_profit - flash_loan_fee - gas_cost_tokens
                
                if net_profit > best_net_profit:
                    best_net_profit = net_profit
                    # Map to EXECUTION router addresses
                    best_opportunity = {
                        "buy_router": buy_router,
                        "sell_router": sell_router,
                        "buy_router_address": self.execution_routers[buy_router]["address"],
                        "sell_router_address": self.execution_routers[sell_router]["address"],
                        "buy_router_version": 0,
                        "sell_router_version": 0,
                        "borrow_amount": borrow_amount,
                        "intermediate_amount": intermediate_amount,
                        "final_amount": final_amount,
                        "gross_profit": gross_profit,
                        "flash_loan_fee": flash_loan_fee,
                        "gas_cost": gas_cost_tokens,
                        "net_profit": net_profit,
                        "path_buy": [token_borrow, token_intermediate],  # Execution network tokens
                        "path_sell": [token_intermediate, token_borrow],  # Execution network tokens
                        "prices": prices,
                        "spreads": spreads,
                    }
        
        # ALWAYS return prices and spreads, even if no profitable opportunity
        if not best_opportunity and len(prices) > 0:
            return {
                "buy_router": None,
                "sell_router": None,
                "buy_router_address": None,
                "sell_router_address": None,
                "buy_router_version": 0,
                "sell_router_version": 0,
                "borrow_amount": borrow_amount,
                "intermediate_amount": 0,
                "final_amount": 0,
                "gross_profit": 0,
                "flash_loan_fee": flash_loan_fee,
                "gas_cost": gas_cost_tokens,
                "net_profit": 0,
                "path_buy": [token_borrow, token_intermediate],
                "path_sell": [token_intermediate, token_borrow],
                "prices": prices,
                "spreads": spreads,
            }
        
        return best_opportunity
    
    def execute_arbitrage_v2(
        self,
        token_borrow: str,
        borrow_amount: int,
        is_base: bool,
        buy_router: str,
        sell_router: str,
        path_buy: list,
        path_sell: list,
        min_profit: int,
    ) -> Optional[str]:
        """Execute V2 arbitrage on target network"""
        if self.dry_run:
            log("DRY RUN - Would execute V2 arbitrage:", Colors.YELLOW)
            log(f"  Borrow: {self.w3.from_wei(borrow_amount, 'ether')} tokens", Colors.YELLOW)
            log(f"  Buy Router: {buy_router}", Colors.YELLOW)
            log(f"  Sell Router: {sell_router}", Colors.YELLOW)
            log(f"  Min Profit: {self.w3.from_wei(min_profit, 'ether')} tokens", Colors.YELLOW)
            return "DRY_RUN"
        
        try:
            log("Building transaction...", Colors.BLUE)
            
            # Build transaction
            tx = self.arbitrage_contract.functions.executeArbitrageV2(
                Web3.to_checksum_address(token_borrow),
                borrow_amount,
                is_base,
                Web3.to_checksum_address(buy_router),
                Web3.to_checksum_address(sell_router),
                [Web3.to_checksum_address(t) for t in path_buy],
                [Web3.to_checksum_address(t) for t in path_sell],
                min_profit,
            ).build_transaction({
                "from": self.address,
                "gas": 500000,
                "gasPrice": self.w3.eth.gas_price,
                "nonce": self.w3.eth.get_transaction_count(self.address),
            })
            
            log("Signing transaction...", Colors.BLUE)
            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            
            log("Sending transaction...", Colors.BLUE)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            
            log(f"TX sent: {tx_hash.hex()}", Colors.GREEN)
            
            # Wait for receipt
            log("Waiting for confirmation...", Colors.BLUE)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            if receipt["status"] == 1:
                log(f"✅ Arbitrage successful! Gas used: {receipt['gasUsed']}", Colors.GREEN)
                return tx_hash.hex()
            else:
                log(f"❌ Arbitrage failed (reverted)", Colors.RED)
                return None
                
        except Exception as e:
            log(f"Execution error: {e}", Colors.RED)
            return None
    
    def run(
        self,
        token_borrow: str,
        token_intermediate: str,
        borrow_amount: int,
        min_profit: int,
        interval: float = 2.0,
    ):
        """Main bot loop"""
        print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 70}")
        print("FLASH LOAN ARBITRAGE BOT - LIVE EXECUTION (V2 ONLY)")
        print("WITH NET PROFIT CALCULATIONS")
        print(f"{'=' * 70}{Colors.END}\n")
        
        print(f"{Colors.BLUE}Configuration:{Colors.END}")
        print(f"  Execution Network: {self.network}")
        print(f"  Price Oracle:      BSC Mainnet (live prices)")
        print(f"  Wallet:            {self.address}")
        print(f"  Contract:          {self.config['arbitrage']}")
        print(f"  Borrow Token:      {TRADING_CONFIG['borrow_token']}")
        print(f"  Trade Token:       {TRADING_CONFIG['trade_token']}")
        print(f"  Borrow Amount:     {self.w3.from_wei(borrow_amount, 'ether')} tokens")
        print(f"  Flash Loan Fee:    {FLASH_LOAN_FEE_PCT * 100}%")
        print(f"  Gas Cost:          ${GAS_COST_USD}")
        print(f"  Min NET Profit:    {self.w3.from_wei(min_profit, 'ether')} tokens (after all fees)")
        print(f"  Dry Run:           {'Yes' if self.dry_run else 'NO - LIVE EXECUTION'}")
        print(f"  Database Logging:  {'✓ Enabled' if self.db else '✗ Disabled'}")
        print(f"  DEX Routers:       {list(self.mainnet_routers.keys())}")
        
        balance = self.get_balance()
        print(f"  Balance:           {balance:.4f} BNB/ETH\n")
        
        if not self.dry_run:
            print(f"{Colors.RED}{Colors.BOLD}⚠️  LIVE MODE - Real transactions will be sent!{Colors.END}")
            # confirm = input("Type 'CONFIRM' to proceed: ")
            # if confirm != "CONFIRM":
            #     log("Aborted by user", Colors.YELLOW)
            #     return
        
        log("Starting arbitrage bot...", Colors.GREEN)
        
        iteration = 0
        opportunities_found = 0
        last_prices = {}
        
        try:
            while True:
                iteration += 1
                
                opp = self.find_arbitrage(token_borrow, token_intermediate, borrow_amount)
                
                # Extract prices and spreads regardless of opportunity
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                prices = opp.get("prices", {}) if opp else {}
                spreads = opp.get("spreads", {}) if opp else {}
                prices_changed = prices != last_prices and len(prices) > 0
                
                # Get net profit for this scan
                current_net_profit = opp.get("net_profit", 0) if opp else 0
                current_gross_profit = opp.get("gross_profit", 0) if opp else 0
                
                # Debug: Show if prices were fetched
                if iteration == 1 and len(prices) == 0:
                    log("Warning: No prices fetched from DEXes", Colors.YELLOW)
                
                # Log to database
                scan_id = None
                if self.db and len(prices) >= 2:
                    price_values = list(prices.values())
                    spread = abs(price_values[0] - price_values[1]) / min(price_values) * 100
                    
                    # Use net profit for database logging
                    best_net_profit = float(current_net_profit) / 1e18 if current_net_profit else 0
                    
                    scan_id = self.db.log_price_scan(
                        pancake_price=float(price_values[0]) / 1e18,
                        biswap_price=float(price_values[1]) / 1e18 if len(price_values) > 1 else 0,
                        spread=spread,
                        price_changed=prices_changed,
                        best_gross_profit=best_net_profit,  # Actually storing net profit
                    )
                
                # Display current prices and spreads (on every scan with prices)
                if len(prices) > 0 and (prices_changed or iteration == 1):
                    print(f"\n{Colors.BOLD}[{timestamp}] Update #{iteration}{Colors.END}")
                    
                    # Show individual DEX prices (WBNB price in BUSD)
                    for dex_name, price in prices.items():
                        price_display = self.w3.from_wei(price, 'ether')
                        
                        # Show change indicator
                        if last_prices.get(dex_name) is not None:
                            change = price - last_prices[dex_name]
                            change_pct = (change / last_prices[dex_name]) * 100
                            change_ind = f" {Colors.GREEN}↑ (+{change_pct:.4f}%){Colors.END}" if change > 0 else (
                                f" {Colors.RED}↓ ({change_pct:.4f}%){Colors.END}" if change < 0 else ""
                            )
                        else:
                            change_ind = ""
                        
                        print(f"  {dex_name.capitalize()}: ${price_display:.2f} per {TRADING_CONFIG['trade_token']}{change_ind}")
                    
                    # Show spreads between DEXes
                    if spreads:
                        print(f"\n  {Colors.CYAN}Spreads:{Colors.END}")
                        for spread_key, spread_val in spreads.items():
                            spread_color = Colors.GREEN if abs(spread_val) > 0.5 else Colors.YELLOW
                            print(f"    {spread_key}: {spread_color}{spread_val:.4f}%{Colors.END}")
                    
                    # Show profit analysis for this scan (ALWAYS, even if below threshold)
                    print(f"\n  {Colors.CYAN}Profit Analysis:{Colors.END}")
                    if current_gross_profit > 0:
                        gross_display = self.w3.from_wei(current_gross_profit, 'ether')
                        flash_fee_display = self.w3.from_wei(opp.get("flash_loan_fee", 0), 'ether')
                        gas_cost_display = self.w3.from_wei(opp.get("gas_cost", 0), 'ether')
                        net_display = self.w3.from_wei(current_net_profit, 'ether')
                        
                        print(f"    Gross Profit:     {Colors.YELLOW}{gross_display:.6f} {TRADING_CONFIG['borrow_token']}{Colors.END}")
                        print(f"    Flash Loan Fee:   {Colors.RED}-{flash_fee_display:.6f} {TRADING_CONFIG['borrow_token']}{Colors.END}")
                        print(f"    Gas Cost:         {Colors.RED}-{gas_cost_display:.6f} {TRADING_CONFIG['borrow_token']}{Colors.END}")
                        
                        # Color-code net profit based on whether it meets threshold
                        if current_net_profit >= min_profit:
                            profit_color = Colors.GREEN
                            profit_status = "✓ PROFITABLE"
                        elif current_net_profit > 0:
                            profit_color = Colors.YELLOW
                            profit_status = "⚠ Below threshold"
                        else:
                            profit_color = Colors.RED
                            profit_status = "✗ Unprofitable"
                        
                        print(f"    Net Profit:       {profit_color}{net_display:.6f} {TRADING_CONFIG['borrow_token']} {profit_status}{Colors.END}")
                    else:
                        print(f"    {Colors.RED}No profitable path found{Colors.END}")
                    
                    if scan_id:
                        print(f"\n  {Colors.CYAN}DB Scan ID: {scan_id}{Colors.END}")
                    
                    # Update last prices
                    last_prices = prices.copy()
                
                # Check for PROFITABLE opportunity (net profit >= threshold)
                if opp and current_net_profit >= min_profit:
                    opportunities_found += 1
                    
                    print(f"\n{Colors.GREEN}{Colors.BOLD}🔥 ARBITRAGE OPPORTUNITY #{opportunities_found}{Colors.END}")
                    print(f"{Colors.CYAN}{'=' * 70}{Colors.END}")
                    print(f"  Strategy:     Buy on {Colors.BOLD}{opp['buy_router'].capitalize()}{Colors.END}, "
                          f"Sell on {Colors.BOLD}{opp['sell_router'].capitalize()}{Colors.END}")
                    print(f"  Borrow:       {self.w3.from_wei(opp['borrow_amount'], 'ether'):.6f} {TRADING_CONFIG['borrow_token']}")
                    print(f"  Intermediate: {self.w3.from_wei(opp['intermediate_amount'], 'ether'):.6f} {TRADING_CONFIG['trade_token']}")
                    print(f"  Final:        {self.w3.from_wei(opp['final_amount'], 'ether'):.6f} {TRADING_CONFIG['borrow_token']}")
                    print(f"\n  {Colors.CYAN}Profit Breakdown:{Colors.END}")
                    print(f"    Gross Profit:  {Colors.YELLOW}{self.w3.from_wei(opp['gross_profit'], 'ether'):.6f} {TRADING_CONFIG['borrow_token']}{Colors.END}")
                    print(f"    Flash Fee:     {Colors.RED}-{self.w3.from_wei(opp['flash_loan_fee'], 'ether'):.6f} {TRADING_CONFIG['borrow_token']}{Colors.END}")
                    print(f"    Gas Cost:      {Colors.RED}-{self.w3.from_wei(opp['gas_cost'], 'ether'):.6f} {TRADING_CONFIG['borrow_token']}{Colors.END}")
                    print(f"    Net Profit:    {Colors.GREEN}{self.w3.from_wei(opp['net_profit'], 'ether'):.6f} {TRADING_CONFIG['borrow_token']}{Colors.END}")
                    
                    # Show opportunity spread
                    buy_price = opp['prices'].get(opp['buy_router'], 0)
                    sell_price = opp['prices'].get(opp['sell_router'], 0)
                    if buy_price and sell_price:
                        opp_spread = ((sell_price - buy_price) / buy_price) * 100
                        print(f"    Spread:        {Colors.YELLOW}{opp_spread:.4f}%{Colors.END}")
                    
                    print(f"{Colors.CYAN}{'=' * 70}{Colors.END}")
                    
                    # Log opportunity to database
                    if self.db and scan_id:
                        db_opp = {
                            "buy_dex": opp["buy_router"],
                            "sell_dex": opp["sell_router"],
                            "buy_price": float(opp["borrow_amount"]) / 1e18,
                            "sell_price": float(opp["final_amount"]) / 1e18,
                            "net": float(opp["net_profit"]) / 1e18,  # Store net profit
                            "flash_loan_amount": float(borrow_amount) / 1e18,
                        }
                        self.db.log_arbitrage_opportunity(scan_id, db_opp)
                        log(f"Opportunity logged to database", Colors.CYAN)
                    
                    # Execute V2 arbitrage
                    tx_hash = self.execute_arbitrage_v2(
                        token_borrow,
                        borrow_amount,
                        True,
                        opp["buy_router_address"],
                        opp["sell_router_address"],
                        opp["path_buy"],
                        opp["path_sell"],
                        min_profit,
                    )
                    
                    if tx_hash and tx_hash != "DRY_RUN":
                        log(f"Transaction: {tx_hash}", Colors.GREEN)
                elif not prices_changed and iteration > 1:
                    # Compact display when no changes
                    db_indicator = f" [DB:{scan_id}]" if (self.db and scan_id) else ""
                    net_profit_display = self.w3.from_wei(current_net_profit, 'ether') if current_net_profit else 0
                    print(f"[{timestamp}] Monitoring... Net: {net_profit_display:.6f}{db_indicator}", end="\r")
                else:
                    # Price changed but no opportunity above threshold
                    if current_net_profit > 0:
                        print(f"  {Colors.YELLOW}Below threshold - Net profit too low{Colors.END}")
                    else:
                        print(f"  {Colors.YELLOW}No profitable path{Colors.END}")
                
                time.sleep(interval)
                
        except KeyboardInterrupt:
            print(f"\n\n{Colors.YELLOW}Bot stopped by user{Colors.END}")
            log(f"Total scans: {iteration}", Colors.CYAN)
            log(f"Opportunities found: {opportunities_found}", Colors.CYAN)
            
            # End database session
            if self.db and self.session_id:
                self.db.end_session(self.session_id, iteration, opportunities_found)
                log(f"Database session ended", Colors.CYAN)
                
                # Show statistics
                stats = self.db.get_statistics(hours=24)
                if stats:
                    print(f"\n{Colors.CYAN}{'=' * 70}{Colors.END}")
                    print(f"{Colors.BOLD}📊 SESSION STATISTICS:{Colors.END}")
                    print(f"{Colors.CYAN}{'=' * 70}{Colors.END}")
                    print(f"  Total Scans:          {stats.get('total_scans', 0)}")
                    print(f"  Price Changes:        {stats.get('price_changes', 0)}")
                    print(f"  Scans with Profit:    {stats.get('scans_with_profit', 0)}")
                    print(f"\n  {Colors.CYAN}Spread Analysis:{Colors.END}")
                    print(f"    Average:            {float(stats.get('avg_spread', 0)):.4f}%")
                    print(f"    Maximum:            {float(stats.get('max_spread', 0)):.4f}%")
                    print(f"    Minimum:            {float(stats.get('min_spread', 0)):.4f}%")
                    print(f"\n  {Colors.CYAN}Net Profit Analysis (all scans):{Colors.END}")
                    if stats.get('avg_gross_profit'):
                        print(f"    Average:            {float(stats.get('avg_gross_profit', 0)):.6f} {TRADING_CONFIG['borrow_token']}")
                        print(f"    Maximum:            {float(stats.get('max_gross_profit', 0)):.6f} {TRADING_CONFIG['borrow_token']}")
                    print(f"\n  {Colors.CYAN}Profitable Opportunities:{Colors.END}")
                    print(f"    Found:              {stats.get('total_opportunities', 0)}")
                    if stats.get('total_potential_profit'):
                        print(f"    Total Potential:    {float(stats.get('total_potential_profit', 0)):.4f} {TRADING_CONFIG['borrow_token']}")
                        print(f"    Avg Net Profit:     {float(stats.get('avg_profit', 0)):.4f} {TRADING_CONFIG['borrow_token']}")
                        print(f"    Max Net Profit:     {float(stats.get('max_profit', 0)):.4f} {TRADING_CONFIG['borrow_token']}")
                    print(f"{Colors.CYAN}{'=' * 70}{Colors.END}\n")
                
                self.db.close()
            
            log("Goodbye! 👋", Colors.YELLOW)


def main():
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 70}")
    print("FLASH LOAN ARBITRAGE BOT")
    print("Live Execution with Net Profit Calculation (V2 Only)")
    print(f"{'=' * 70}{Colors.END}\n")
    
    if not WEB3_AVAILABLE:
        print(f"{Colors.RED}Error: Web3.py is required{Colors.END}")
        print(f"Install with: {Colors.YELLOW}pip install web3{Colors.END}\n")
        return
    
    # Get private key from environment
    private_key = os.getenv("PRIVATE_KEY")
    
    if not private_key:
        print(f"{Colors.RED}Error: PRIVATE_KEY environment variable not set{Colors.END}")
        print("Set it with: export PRIVATE_KEY=your_private_key_here")
        return
    
    network = os.getenv("NETWORK", "bsc_testnet")
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    
    log(f"Initializing bot for {network}...", Colors.BLUE)
    
    bot = ArbitrageBot(private_key, network, dry_run)
    
    # Get token addresses from TRADING_CONFIG
    borrow_token_name = TRADING_CONFIG["borrow_token"]
    trade_token_name = TRADING_CONFIG["trade_token"]
    
    tokens = bot.tokens
    token_borrow = tokens.get(borrow_token_name)
    token_intermediate = tokens.get(trade_token_name)
    
    if not token_borrow or not token_intermediate:
        print(f"{Colors.RED}Error: Token configuration missing{Colors.END}")
        print(f"Borrow Token ({borrow_token_name}): {token_borrow}")
        print(f"Trade Token ({trade_token_name}): {token_intermediate}")
        return
    
    log(f"Token Borrow ({borrow_token_name}): {token_borrow}", Colors.CYAN)
    log(f"Token Trade ({trade_token_name}): {token_intermediate}", Colors.CYAN)
    
    # Use amounts from TRADING_CONFIG
    borrow_amount = bot.w3.to_wei(TRADING_CONFIG["borrow_amount"], "ether")
    min_profit = bot.w3.to_wei(TRADING_CONFIG["min_profit"], "ether")
    
    bot.run(token_borrow, token_intermediate, borrow_amount, min_profit, interval=10)


if __name__ == "__main__":
    main()