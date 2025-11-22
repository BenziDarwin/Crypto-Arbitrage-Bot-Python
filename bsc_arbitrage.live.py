"""
Flash Loan Arbitrage Bot - Live Execution Version
Uses deployed FlashLoanArbitrage contract for real trades
Supports BSC Mainnet/Testnet and other EVM chains
WITH DATABASE LOGGING
"""
import time
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Tuple
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    print("⚠️  Web3.py not installed. Install with: pip install web3")

try:
    from database import ArbitrageDatabase
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    print("⚠️  Database module not found. Running without database logging.")

# === CONFIGURATION ===
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

# === CONTRACT ADDRESSES (UPDATE THESE) ===
CONTRACT_CONFIG = {
    "bsc_mainnet": {
        "arbitrage": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        "dodo_pool": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        "v2_routers": {
            "pancakeswap": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
            "biswap": "0x3a6d8cA21D1CF76F653A67577FA0D27453350dD8",
        },
        "v3_routers": {
            "pancakeswap_v3": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",
        },
        "tokens": {
            "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
            "BUSD": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
            "USDT": "0x55d398326f99059fF775485246999027B3197955",
        },
    },
    "bsc_testnet": {
        "arbitrage": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        "dodo_pool": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        "v2_routers": {
            "pancakeswap": "0xD99D1c33F9fC3444f8101754aBC46c52416550D1",
        },
        "v3_routers": {},
        "tokens": {
            "WBNB": "0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd",
            "BUSD": "0x78867BbEeF44f2326bF8DDd1941a4439382EF2A7",
        },
    },
    "localhost": {
        "arbitrage": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        "dodo_pool": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        "v2_routers": {
            "router_v2": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
        },
        "v3_routers": {
            "router_v3": "0x0fe261aeE0d1C4DFdDee4102E82Dd425999065F4",
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
        
        # Connect to network
        net_config = NETWORKS.get(network)
        if not net_config:
            raise ValueError(f"Unknown network: {network}")
        
        self.w3 = Web3(Web3.HTTPProvider(net_config["rpc"]))

        # Add PoA middleware for BSC (handles extraData field > 32 bytes)
        if "bsc" in network:
            try:
                # Web3.py v6+
                from web3.middleware import ExtraDataToPOAMiddleware
                self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            except ImportError:
                try:
                    # Web3.py v5
                    from web3.middleware import geth_poa_middleware
                    self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
                except ImportError:
                    # Skip if neither available
                    log("PoA middleware not available, continuing anyway...", Colors.YELLOW)
                
                if not self.w3.is_connected():
                    raise ConnectionError(f"Failed to connect to {network}")
        
        log(f"Connected to {network}", Colors.GREEN)
        
        # Setup account
        self.account = self.w3.eth.account.from_key(private_key)
        self.address = self.account.address
        log(f"Wallet: {self.address}", Colors.CYAN)
        
        # Load contract config
        self.config = CONTRACT_CONFIG.get(network, {})
        
        # Load ABIs
        log("Loading ABIs...", Colors.BLUE)
        self.arbitrage_abi = load_abi("FlashLoanArbitrage.json")
        self.router_abi = load_abi("RouterV2.json")
        self.erc20_abi = load_abi("ERC20.json")
        log("ABIs loaded successfully", Colors.GREEN)
        
        # Initialize contracts
        self.arbitrage_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.config["arbitrage"]),
            abi=self.arbitrage_abi,
        )
        
        # Initialize routers
        self.routers = {}
        for name, addr in self.config.get("v2_routers", {}).items():
            self.routers[name] = {
                "contract": self.w3.eth.contract(
                    address=Web3.to_checksum_address(addr),
                    abi=self.router_abi,
                ),
                "version": 0,  # V2
                "address": addr,
            }
            log(f"  V2 Router loaded: {name}", Colors.CYAN)
        
        for name, addr in self.config.get("v3_routers", {}).items():
            self.routers[name] = {
                "contract": self.w3.eth.contract(
                    address=Web3.to_checksum_address(addr),
                    abi=self.router_abi,
                ),
                "version": 1,  # V3
                "address": addr,
            }
            log(f"  V3 Router loaded: {name}", Colors.CYAN)
        
        self.tokens = self.config.get("tokens", {})
        
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
    
    def get_price(self, router_name: str, amount_in: int, path: list) -> Optional[int]:
        """Get output amount from router"""
        try:
            router = self.routers.get(router_name)
            if not router:
                return None
            
            path_checksum = [Web3.to_checksum_address(addr) for addr in path]
            amounts = router["contract"].functions.getAmountsOut(
                amount_in, path_checksum
            ).call()
            
            return amounts[-1]
        except Exception as e:
            log(f"Price fetch error ({router_name}): {e}", Colors.RED)
            return None
    
    def find_arbitrage(
        self,
        token_borrow: str,
        token_intermediate: str,
        borrow_amount: int,
    ) -> Optional[Dict]:
        """Find best arbitrage opportunity between routers"""
        path_buy = [token_borrow, token_intermediate]
        path_sell = [token_intermediate, token_borrow]
        
        best_opportunity = None
        best_profit = 0
        
        router_names = list(self.routers.keys())
        prices = {}
        
        # Get all prices first (for logging)
        for router_name in router_names:
            price = self.get_price(router_name, borrow_amount, path_buy)
            if price:
                prices[router_name] = price
        
        for buy_router in router_names:
            for sell_router in router_names:
                # Get buy price
                intermediate_amount = self.get_price(buy_router, borrow_amount, path_buy)
                if not intermediate_amount:
                    continue
                
                # Get sell price
                final_amount = self.get_price(sell_router, intermediate_amount, path_sell)
                if not final_amount:
                    continue
                
                # Calculate profit (before fees)
                profit = final_amount - borrow_amount
                
                if profit > best_profit:
                    best_profit = profit
                    best_opportunity = {
                        "buy_router": buy_router,
                        "sell_router": sell_router,
                        "buy_router_address": self.routers[buy_router]["address"],
                        "sell_router_address": self.routers[sell_router]["address"],
                        "buy_router_version": self.routers[buy_router]["version"],
                        "sell_router_version": self.routers[sell_router]["version"],
                        "borrow_amount": borrow_amount,
                        "intermediate_amount": intermediate_amount,
                        "final_amount": final_amount,
                        "gross_profit": profit,
                        "path_buy": path_buy,
                        "path_sell": path_sell,
                        "prices": prices,
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
        """Execute V2 arbitrage"""
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
    
    def execute_arbitrage_mixed(
        self,
        token_borrow: str,
        borrow_amount: int,
        is_base: bool,
        buy_router: str,
        buy_version: int,
        sell_router: str,
        sell_version: int,
        path_buy: list,
        path_sell: list,
        buy_fee: int,
        sell_fee: int,
        min_profit: int,
    ) -> Optional[str]:
        """Execute mixed V2/V3 arbitrage"""
        if self.dry_run:
            log("DRY RUN - Would execute mixed arbitrage:", Colors.YELLOW)
            log(f"  Buy: {'V3' if buy_version else 'V2'} @ {buy_router[:10]}...", Colors.YELLOW)
            log(f"  Sell: {'V3' if sell_version else 'V2'} @ {sell_router[:10]}...", Colors.YELLOW)
            log(f"  Borrow: {self.w3.from_wei(borrow_amount, 'ether')} tokens", Colors.YELLOW)
            return "DRY_RUN"
        
        try:
            log("Building mixed arbitrage transaction...", Colors.BLUE)
            
            tx = self.arbitrage_contract.functions.executeArbitrageMixed(
                Web3.to_checksum_address(token_borrow),
                borrow_amount,
                is_base,
                Web3.to_checksum_address(buy_router),
                buy_version,
                Web3.to_checksum_address(sell_router),
                sell_version,
                [Web3.to_checksum_address(t) for t in path_buy],
                [Web3.to_checksum_address(t) for t in path_sell],
                buy_fee,
                sell_fee,
                min_profit,
            ).build_transaction({
                "from": self.address,
                "gas": 600000,
                "gasPrice": self.w3.eth.gas_price,
                "nonce": self.w3.eth.get_transaction_count(self.address),
            })
            
            log("Signing transaction...", Colors.BLUE)
            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            
            log("Sending transaction...", Colors.BLUE)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            
            log(f"TX sent: {tx_hash.hex()}", Colors.GREEN)
            
            log("Waiting for confirmation...", Colors.BLUE)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            if receipt["status"] == 1:
                log(f"✅ Mixed arbitrage successful! Gas used: {receipt['gasUsed']}", Colors.GREEN)
                return tx_hash.hex()
            else:
                log(f"❌ Mixed arbitrage failed (reverted)", Colors.RED)
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
        print("FLASH LOAN ARBITRAGE BOT - LIVE EXECUTION")
        print(f"{'=' * 70}{Colors.END}\n")
        
        print(f"{Colors.BLUE}Configuration:{Colors.END}")
        print(f"  Network:          {self.network}")
        print(f"  Wallet:           {self.address}")
        print(f"  Contract:         {self.config['arbitrage']}")
        print(f"  Borrow Amount:    {self.w3.from_wei(borrow_amount, 'ether')} tokens")
        print(f"  Min Profit:       {self.w3.from_wei(min_profit, 'ether')} tokens")
        print(f"  Dry Run:          {'Yes' if self.dry_run else 'NO - LIVE EXECUTION'}")
        print(f"  Database Logging: {'✓ Enabled' if self.db else '✗ Disabled'}")
        print(f"  Routers:          {list(self.routers.keys())}")
        
        balance = self.get_balance()
        print(f"  Balance:          {balance:.4f} BNB/ETH\n")
        
        if not self.dry_run:
            print(f"{Colors.RED}{Colors.BOLD}⚠️  LIVE MODE - Real transactions will be sent!{Colors.END}")
            confirm = input("Type 'CONFIRM' to proceed: ")
            if confirm != "CONFIRM":
                log("Aborted by user", Colors.YELLOW)
                return
        
        log("Starting arbitrage bot...", Colors.GREEN)
        
        iteration = 0
        opportunities_found = 0
        last_prices = {}
        
        try:
            while True:
                iteration += 1
                
                opp = self.find_arbitrage(token_borrow, token_intermediate, borrow_amount)
                
                # Log to database
                scan_id = None
                if self.db and opp:
                    prices = opp.get("prices", {})
                    if len(prices) >= 2:
                        price_values = list(prices.values())
                        spread = abs(price_values[0] - price_values[1]) / min(price_values) * 100
                        prices_changed = prices != last_prices
                        
                        scan_id = self.db.log_price_scan(
                            pancake_price=float(price_values[0]) / 1e18,
                            biswap_price=float(price_values[1]) / 1e18 if len(price_values) > 1 else 0,
                            spread=spread,
                            price_changed=prices_changed,
                        )
                        last_prices = prices.copy()
                
                if opp and opp["gross_profit"] > min_profit:
                    opportunities_found += 1
                    
                    print(f"\n{Colors.GREEN}{Colors.BOLD}🔥 ARBITRAGE OPPORTUNITY #{opportunities_found}{Colors.END}")
                    print(f"{Colors.CYAN}{'=' * 70}{Colors.END}")
                    print(f"  Buy Router:   {opp['buy_router']} (V{'3' if opp['buy_router_version'] else '2'})")
                    print(f"  Sell Router:  {opp['sell_router']} (V{'3' if opp['sell_router_version'] else '2'})")
                    print(f"  Intermediate: {self.w3.from_wei(opp['intermediate_amount'], 'ether'):.6f} tokens")
                    print(f"  Final:        {self.w3.from_wei(opp['final_amount'], 'ether'):.6f} tokens")
                    print(f"  Gross Profit: {Colors.GREEN}{self.w3.from_wei(opp['gross_profit'], 'ether'):.6f} tokens{Colors.END}")
                    if self.db and scan_id:
                        print(f"  {Colors.CYAN}DB Scan ID:   {scan_id}{Colors.END}")
                    print(f"{Colors.CYAN}{'=' * 70}{Colors.END}")
                    
                    # Log opportunity to database
                    if self.db and scan_id:
                        db_opp = {
                            "buy_dex": opp["buy_router"],
                            "sell_dex": opp["sell_router"],
                            "buy_price": float(opp["borrow_amount"]) / 1e18,
                            "sell_price": float(opp["final_amount"]) / 1e18,
                            "net": float(opp["gross_profit"]) / 1e18,
                            "flash_loan_amount": float(borrow_amount) / 1e18,
                        }
                        self.db.log_arbitrage_opportunity(scan_id, db_opp)
                        log(f"Opportunity logged to database", Colors.CYAN)
                    
                    # Execute
                    tx_hash = None
                    if opp["buy_router_version"] == opp["sell_router_version"] == 0:
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
                    else:
                        tx_hash = self.execute_arbitrage_mixed(
                            token_borrow,
                            borrow_amount,
                            True,
                            opp["buy_router_address"],
                            opp["buy_router_version"],
                            opp["sell_router_address"],
                            opp["sell_router_version"],
                            opp["path_buy"],
                            opp["path_sell"],
                            3000 if opp["buy_router_version"] == 1 else 0,
                            3000 if opp["sell_router_version"] == 1 else 0,
                            min_profit,
                        )
                    
                    if tx_hash and tx_hash != "DRY_RUN":
                        log(f"Transaction: {tx_hash}", Colors.GREEN)
                else:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    db_indicator = f" [DB:{scan_id}]" if (self.db and scan_id) else ""
                    print(f"[{timestamp}] Scan #{iteration} - No opportunity{db_indicator}", end="\r")
                
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
                    print(f"  Opportunities Found:  {stats.get('total_opportunities', 0)}")
                    if stats.get('total_potential_profit'):
                        print(f"  Total Potential:      {float(stats.get('total_potential_profit', 0)):.4f} tokens")
                    print(f"{Colors.CYAN}{'=' * 70}{Colors.END}\n")
                
                self.db.close()
            
            log("Goodbye! 👋", Colors.YELLOW)


def main():
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 70}")
    print("FLASH LOAN ARBITRAGE BOT")
    print("Live Execution Version with Database Logging")
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
    
    # Configure tokens
    tokens = bot.tokens
    token_borrow = tokens.get("WBNB") or list(tokens.values())[0]
    token_intermediate = tokens.get("BUSD") or list(tokens.values())[1]
    
    log(f"Token Borrow: {token_borrow}", Colors.CYAN)
    log(f"Token Intermediate: {token_intermediate}", Colors.CYAN)
    
    # Borrow 10 tokens, min profit 0.01 tokens
    borrow_amount = bot.w3.to_wei(10, "ether")
    min_profit = bot.w3.to_wei(0.01, "ether")
    
    bot.run(token_borrow, token_intermediate, borrow_amount, min_profit)


if __name__ == "__main__":
    main()