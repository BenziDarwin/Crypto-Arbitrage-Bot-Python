"""
Flash Loan Arbitrage Bot - MAINNET Live Execution
Executes real arbitrage trades on BSC mainnet
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
    "borrow_token": "USDT",      # Token to flash loan from DODO
    "trade_token": "WBNB",       # Intermediate token
    "borrow_amount": 1000,       # Amount of USDT to borrow (whole tokens)
    "min_profit": 0.01,          # Minimum profit in USDT to execute (after all fees)
    "min_spread_pct": 0.37,      # Minimum spread % to even attempt (pre-filter)
    "flash_loan_fee": 0.0,       # DODO flash loan fee (0% = free)
    "gas_cost_usd": 0.08,        # Estimated gas cost in USD
}

# === NETWORK CONFIGURATION ===
NETWORKS = {
    "bsc_mainnet": {
        "rpc": "https://bsc-dataseed1.binance.org",
        "chain_id": 56,
        "explorer": "https://bscscan.com",
    },
}

# === CONTRACT ADDRESSES ===
CONTRACT_CONFIG = {
    "bsc_mainnet": {
        "arbitrage": "0xfb1e102682fb0493c3135e1803f194c925ae8f60",
        "dodo_pool": "0x6098A5638d8D7e9Ed2f952d35B2b67c34EC6B476",  # DODO USDT-BUSD pool
        "v2_routers": {
            "pancakeswap": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
            "biswap": "0x3a6d8cA21D1CF76F653A67577FA0D27453350dD8",
        },
        "tokens": {
            "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
            "USDT": "0x55d398326f99059fF775485246999027B3197955",
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
    def __init__(self, private_key: str, dry_run: bool = True):
        self.network = "bsc_mainnet"
        self.dry_run = dry_run
        self.private_key = private_key
        self.db = None
        self.session_id = None
        
        if not WEB3_AVAILABLE:
            raise ImportError("Web3.py is required")
        
        # Connect to BSC mainnet
        net_config = NETWORKS["bsc_mainnet"]
        self.w3 = Web3(Web3.HTTPProvider(net_config["rpc"]))

        # Add PoA middleware for BSC
        try:
            from web3.middleware import geth_poa_middleware
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        except ImportError:
            log("PoA middleware not available", Colors.YELLOW)
                
        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to BSC mainnet")
        
        log(f"✓ Connected to BSC Mainnet", Colors.GREEN)
        
        # Setup account
        self.account = self.w3.eth.account.from_key(private_key)
        self.address = self.account.address
        log(f"Wallet: {self.address}", Colors.CYAN)
        
        # Load config
        self.config = CONTRACT_CONFIG["bsc_mainnet"]
        
        # Load ABIs
        log("Loading ABIs...", Colors.BLUE)
        self.arbitrage_abi = load_abi("FlashLoanArbitrage.json")
        self.router_abi = load_abi("RouterV2.json")
        self.erc20_abi = load_abi("ERC20.json")
        log("ABIs loaded", Colors.GREEN)
        
        # Initialize contracts
        self.arbitrage_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.config["arbitrage"]),
            abi=self.arbitrage_abi,
        )
        
        # DEX routers
        self.routers = {}
        for name, addr in self.config["v2_routers"].items():
            self.routers[name] = self.w3.eth.contract(
                address=Web3.to_checksum_address(addr),
                abi=self.router_abi,
            )
            log(f"  Router: {name}", Colors.CYAN)
        
        # Token addresses
        self.tokens = self.config["tokens"]
        
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
    
    def get_balance(self) -> float:
        """Get native BNB balance"""
        balance = self.w3.eth.get_balance(self.address)
        return self.w3.from_wei(balance, "ether")
    
    def get_token_balance(self, token_symbol: str) -> float:
        """Get ERC20 token balance"""
        try:
            token_addr = self.tokens.get(token_symbol)
            if not token_addr:
                return 0.0
            
            token_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_addr),
                abi=self.erc20_abi,
            )
            balance_wei = token_contract.functions.balanceOf(self.address).call()
            return self.w3.from_wei(balance_wei, 'ether')
        except Exception as e:
            log(f"Error getting {token_symbol} balance: {str(e)}", Colors.RED)
            return 0.0
    
    def get_price(self, router_contract, amount_in: int, path: list) -> Optional[int]:
        """Get price from DEX"""
        try:
            path_checksum = [Web3.to_checksum_address(addr) for addr in path]
            amounts = router_contract.functions.getAmountsOut(amount_in, path_checksum).call()
            return amounts[-1]
        except Exception as e:
            log(f"Price fetch error: {str(e)[:50]}", Colors.RED)
            return None
    
    def find_arbitrage_opportunity(self) -> Dict:
        """
        Find arbitrage opportunity by comparing WBNB prices on different DEXes
        Flash loan USDT → Buy WBNB on cheaper DEX → Sell WBNB on expensive DEX → Repay USDT
        """
        token_usdt = self.tokens.get("USDT")
        token_wbnb = self.tokens.get("WBNB")
        
        if not token_usdt or not token_wbnb:
            log("Missing token configuration", Colors.RED)
            return {"prices": {}, "spreads": {}, "profits": {}, "opportunity": None}
        
        # Get WBNB price on each DEX (in USDT per WBNB)
        wbnb_amount = 10**18  # 1 WBNB
        path_wbnb_to_usdt = [token_wbnb, token_usdt]  # WBNB → USDT
        
        wbnb_prices = {}
        router_names = list(self.routers.keys())
        
        for router_name in router_names:
            router = self.routers[router_name]
            usdt_for_wbnb = self.get_price(router, wbnb_amount, path_wbnb_to_usdt)
            if usdt_for_wbnb:
                price = self.w3.from_wei(usdt_for_wbnb, 'ether')
                wbnb_prices[router_name] = price
        
        if len(wbnb_prices) < 2:
            return {"prices": {}, "spreads": {}, "profits": {}, "opportunity": None}
        
        # Simulate arbitrage using these prices
        FLASH_LOAN = TRADING_CONFIG["borrow_amount"]  # USDT to borrow
        GAS_COST = TRADING_CONFIG.get("gas_cost_usd", 0.08)
        PANCAKE_FEE = 0.0025  # 0.25%
        BISWAP_FEE = 0.001    # 0.1%
        
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
                
                buy_price = float(wbnb_prices[buy_router])  # USDT per WBNB
                sell_price = float(wbnb_prices[sell_router])  # USDT per WBNB
                
                # Get DEX fees
                buy_fee = PANCAKE_FEE if buy_router == "pancakeswap" else BISWAP_FEE
                sell_fee = PANCAKE_FEE if sell_router == "pancakeswap" else BISWAP_FEE
                
                # Flash loan USDT, trade for WBNB, sell WBNB, repay USDT
                borrowed_usdt = float(FLASH_LOAN)
                
                # Step 1: Buy WBNB with borrowed USDT
                wbnb_bought = (borrowed_usdt / buy_price) * (1 - buy_fee)
                
                # Step 2: Sell WBNB for USDT
                wbnb_after_sell_fee = wbnb_bought * (1 - sell_fee)
                usdt_received = wbnb_after_sell_fee * sell_price
                
                # Step 3: Calculate DODO repayment (with fee if any)
                flash_loan_fee_pct = TRADING_CONFIG.get("flash_loan_fee", 0.0)
                dodo_repay = borrowed_usdt * (1 + flash_loan_fee_pct)
                
                # Step 4: Calculate profits
                gross_profit = float(usdt_received - dodo_repay)
                net_profit = float(gross_profit - GAS_COST)
                
                # Calculate spread
                spread = ((sell_price - buy_price) / buy_price) * 100
                
                # Store all paths
                path_key = f"{buy_router}_to_{sell_router}"
                all_spreads[path_key] = spread
                all_profits[path_key] = self.w3.to_wei(net_profit, 'ether') if net_profit >= 0 else -self.w3.to_wei(abs(net_profit), 'ether')
                
                # Track best opportunity
                if abs(spread) > TRADING_CONFIG["min_spread_pct"]:
                    if best_opportunity is None or abs(spread) > abs(best_opportunity.get("spread", 0)):
                        borrow_wei = self.w3.to_wei(FLASH_LOAN, 'ether')
                        wbnb_wei = self.w3.to_wei(wbnb_bought, 'ether')
                        usdt_return_wei = self.w3.to_wei(usdt_received, 'ether')
                        
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
                            "intermediate_amount": wbnb_wei,
                            "final_amount": usdt_return_wei,
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
        Flash loan USDT → Swap on DEXes → Repay loan → Keep profit
        """
        if self.dry_run:
            log("🔶 DRY RUN - Would execute arbitrage:", Colors.YELLOW)
            log(f"  Borrow: {self.w3.from_wei(opportunity['borrow_amount'], 'ether')} USDT", Colors.YELLOW)
            log(f"  Buy on: {opportunity['buy_router']} @ ${opportunity['buy_price']:.6f}", Colors.YELLOW)
            log(f"  Sell on: {opportunity['sell_router']} @ ${opportunity['sell_price']:.6f}", Colors.YELLOW)
            log(f"  Spread: {opportunity['spread']:.4f}%", Colors.YELLOW)
            
            net_profit_value = opportunity['estimated_net_profit']
            if net_profit_value >= 0:
                net_profit_display = self.w3.from_wei(net_profit_value, 'ether')
            else:
                net_profit_display = -self.w3.from_wei(abs(net_profit_value), 'ether')
            log(f"  Expected Net Profit: ${net_profit_display:.4f} USDT", Colors.YELLOW)
            
            return "DRY_RUN"
        
        try:
            # Get token addresses
            token_usdt = self.tokens["USDT"]  # Borrow token
            token_wbnb = self.tokens["WBNB"]  # Trade token
            
            # Build paths
            path_buy = [token_usdt, token_wbnb]   # USDT → WBNB
            path_sell = [token_wbnb, token_usdt]  # WBNB → USDT
            
            # Min profit in wei
            min_profit = self.w3.to_wei(TRADING_CONFIG["min_profit"], "ether")
            
            log("📝 Building transaction...", Colors.BLUE)
            log(f"   Flash loan: {self.w3.from_wei(opportunity['borrow_amount'], 'ether')} USDT", Colors.CYAN)
            log(f"   Buy on {opportunity['buy_router']}, Sell on {opportunity['sell_router']}", Colors.CYAN)
            
            # Build transaction for executeArbitrageV2
            tx = self.arbitrage_contract.functions.executeArbitrageV2(
                Web3.to_checksum_address(token_usdt),                      # borrowedToken (USDT)
                opportunity["borrow_amount"],                               # borrowAmount (USDT wei)
                False,                                                       # isBase (USDT is base token in DODO pool)
                Web3.to_checksum_address(opportunity["buy_router_addr"]),  # buyRouter
                Web3.to_checksum_address(opportunity["sell_router_addr"]), # sellRouter
                [Web3.to_checksum_address(t) for t in path_buy],           # pathBuy (USDT → WBNB)
                [Web3.to_checksum_address(t) for t in path_sell],          # pathSell (WBNB → USDT)
                min_profit,                                                 # minProfit
            ).build_transaction({
                "from": self.address,
                "gas": 400000,
                "gasPrice": self.w3.eth.gas_price,
                "nonce": self.w3.eth.get_transaction_count(self.address, 'pending'),
            })
            
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
                
                if receipt["logs"]:
                    log(f"   Event logs: {len(receipt['logs'])} events emitted", Colors.CYAN)
                
                return tx_hash.hex()
            else:
                log(f"❌ Transaction REVERTED", Colors.RED)
                
                # Try to get revert reason
                try:
                    self.w3.eth.call(tx, receipt["blockNumber"])
                except Exception as e:
                    error_msg = str(e)
                    
                    if "InsufficientProfit" in error_msg:
                        log(f"   Revert Reason: InsufficientProfit", Colors.YELLOW)
                        log(f"   → Actual profit was below minProfit threshold", Colors.YELLOW)
                    elif "execution reverted" in error_msg:
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
            import traceback
            traceback.print_exc()
            return None
    
    def run(self, interval: float = 10.0):
        """Main bot loop - scan for opportunities and execute"""
        print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 80}")
        print("FLASH LOAN ARBITRAGE BOT - BSC MAINNET")
        print("Real money, real trades!")
        print(f"{'=' * 80}{Colors.END}\n")
        
        print(f"{Colors.BLUE}Configuration:{Colors.END}")
        print(f"  Network:           BSC Mainnet")
        print(f"  Wallet:            {self.address}")
        print(f"  Contract:          {self.config['arbitrage']}")
        print(f"  DODO Pool:         {self.config['dodo_pool']}")
        print(f"  Borrow Token:      {TRADING_CONFIG['borrow_token']} (flash loan)")
        print(f"  Trade Token:       {TRADING_CONFIG['trade_token']}")
        print(f"  Borrow Amount:     {TRADING_CONFIG['borrow_amount']} USDT")
        print(f"  Flash Loan Fee:    {TRADING_CONFIG.get('flash_loan_fee', 0.0)*100:.2f}%")
        print(f"  Gas Cost:          ${TRADING_CONFIG.get('gas_cost_usd', 0.08)}")
        print(f"  Min Spread:        {TRADING_CONFIG['min_spread_pct']}%")
        print(f"  Min Profit:        {TRADING_CONFIG['min_profit']} USDT")
        print(f"  Dry Run:           {'Yes ✓' if self.dry_run else 'NO - LIVE! ⚠️'}")
        print(f"  Database:          {'Enabled ✓' if self.db else 'Disabled'}")
        print(f"  DEX Routers:       {list(self.routers.keys())}")
        
        bnb_balance = self.get_balance()
        usdt_balance = self.get_token_balance("USDT")
        wbnb_balance = self.get_token_balance("WBNB")
        
        print(f"\n{Colors.BOLD}Balances:{Colors.END}")
        print(f"  BNB:   {bnb_balance:.4f}")
        print(f"  USDT:  {usdt_balance:.2f}")
        print(f"  WBNB:  {wbnb_balance:.6f}\n")
        
        if not self.dry_run:
            print(f"{Colors.RED}{Colors.BOLD}⚠️  LIVE MODE - REAL TRANSACTIONS WITH REAL MONEY!{Colors.END}")
            print(f"{Colors.YELLOW}Flash loan arbitrage will execute on BSC mainnet!{Colors.END}\n")
        
        log("🚀 Starting arbitrage bot...", Colors.GREEN)
        
        iteration = 0
        opportunities_found = 0
        executions_attempted = 0
        executions_successful = 0
        
        try:
            while True:
                iteration += 1
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                
                # Find opportunity
                result = self.find_arbitrage_opportunity()
                
                prices = result.get("prices", {})
                spreads = result.get("spreads", {})
                profits = result.get("profits", {})
                opp = result.get("opportunity")
                
                # Log to database
                scan_id = None
                if self.db and len(prices) >= 2:
                    price_list = list(prices.values())
                    overall_spread = abs(price_list[0] - price_list[1]) / min(price_list) * 100
                    
                    best_net_profit = 0
                    if opp:
                        net_profit_value = opp.get('estimated_net_profit', 0)
                        best_net_profit = float(net_profit_value) / 1e18 if net_profit_value >= 0 else -float(abs(net_profit_value)) / 1e18
                    
                    scan_id = self.db.log_price_scan(
                        pancake_price=float(price_list[0]),
                        biswap_price=float(price_list[1]) if len(price_list) > 1 else 0,
                        spread=overall_spread,
                        price_changed=True,
                        best_gross_profit=best_net_profit,
                    )
                
                # Display
                if len(prices) >= 2:
                    print(f"\n{Colors.BOLD}[{timestamp}] Scan #{iteration}{Colors.END}")
                    
                    for router_name, price in sorted(prices.items()):
                        print(f"  {router_name.capitalize()}: ${price:.6f} USDT/WBNB")
                    
                    price_list = list(prices.values())
                    if len(price_list) >= 2:
                        overall_spread = abs(price_list[0] - price_list[1]) / min(price_list) * 100
                        print(f"  Spread:      {overall_spread:.4f}%")
                    
                    if scan_id:
                        print(f"  DB Scan ID: {scan_id}")
                    
                    if spreads:
                        print(f"\n  {Colors.CYAN}Spreads:{Colors.END}")
                        for path, spread_val in spreads.items():
                            color = Colors.GREEN if abs(spread_val) > 0.5 else Colors.YELLOW
                            print(f"    {path}: {color}{spread_val:.4f}%{Colors.END}")
                    
                    if profits:
                        print(f"\n  {Colors.CYAN}Estimated Net Profits:{Colors.END}")
                        for path, profit_wei in profits.items():
                            profit_val = self.w3.from_wei(abs(profit_wei), 'ether') if profit_wei >= 0 else -self.w3.from_wei(abs(profit_wei), 'ether')
                            color = Colors.GREEN if profit_val > 0 else Colors.RED
                            print(f"    {path}: {color}${profit_val:.4f} USDT{Colors.END}")
                    
                    if opp:
                        opportunities_found += 1
                        net_profit_value = opp['estimated_net_profit']
                        if net_profit_value >= 0:
                            net_profit_display = self.w3.from_wei(net_profit_value, 'ether')
                        else:
                            net_profit_display = -self.w3.from_wei(abs(net_profit_value), 'ether')
                        
                        print(f"\n{Colors.GREEN}{Colors.BOLD}🔥 OPPORTUNITY #{opportunities_found}!{Colors.END}")
                        print(f"  Strategy: Buy {opp['buy_router'].capitalize()} → Sell {opp['sell_router'].capitalize()}")
                        print(f"  Net Profit: {Colors.GREEN}${net_profit_display:.4f} USDT{Colors.END}")
                        
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
                        
                        # Execute
                        log("⚡ Executing arbitrage...", Colors.BOLD)
                        executions_attempted += 1
                        
                        tx_hash = self.execute_arbitrage_v2(opp)
                        
                        if tx_hash and tx_hash != "DRY_RUN":
                            executions_successful += 1
                            explorer_url = f"{NETWORKS['bsc_mainnet']['explorer']}/tx/{tx_hash}"
                            print(f"{Colors.GREEN}🔗 {explorer_url}{Colors.END}\n")
                    else:
                        print(f"  {Colors.YELLOW}No opportunity{Colors.END}")
                else:
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
            
            log("Goodbye! 👋", Colors.YELLOW)

def main():
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 80}")
    print("FLASH LOAN ARBITRAGE BOT - BSC MAINNET")
    print("Live Smart Contract Execution")
    print(f"{'=' * 80}{Colors.END}\n")
    
    if not WEB3_AVAILABLE:
        print(f"{Colors.RED}Error: Web3.py is required{Colors.END}")
        return
    
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        print(f"{Colors.RED}Error: PRIVATE_KEY environment variable not set{Colors.END}")
        return
    
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    
    log(f"Initializing bot for BSC Mainnet...", Colors.BLUE)
    
    try:
        bot = ArbitrageBot(private_key, dry_run)
        bot.run(interval=10)
    except Exception as e:
        log(f"Fatal error: {e}", Colors.RED)
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()