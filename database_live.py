"""
Database module for BSC Arbitrage Bot
Handles PostgreSQL connection and logging of all price scans
WITH GROSS PROFIT TRACKING
"""
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from datetime import datetime
from typing import Optional, Dict
import os

class ArbitrageDatabase:
    def __init__(self, 
                 host: str = "localhost",
                 port: int = 5432,
                 database: str = "bsc_arbitrage_db",
                 user: str = "postgres",
                 password: str = "password=1"):
        """
        Initialize database connection pool
        """
        self.connection_params = {
            'host': host,
            'port': port,
            'database': database,
            'user': user,
            'password': password
        }
        
        self.connection_pool = None
        self.connected = False
        
    def connect(self) -> bool:
        """Create connection pool"""
        try:
            self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                1, 20,  # min and max connections
                **self.connection_params
            )
            
            if self.connection_pool:
                self.connected = True
                print("✓ Connected to PostgreSQL database")
                return True
            
        except psycopg2.Error as e:
            print(f"✗ Database connection failed: {e}")
            self.connected = False
            return False
    
    def create_tables(self):
        """Create necessary tables if they don't exist"""
        if not self.connected:
            print("✗ Not connected to database")
            return False
        
        conn = self.connection_pool.getconn()
        
        try:
            cursor = conn.cursor()
            
            # Create price_scans table with best_gross_profit column
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS price_scans (
                    id SERIAL PRIMARY KEY,
                    scan_timestamp TIMESTAMP NOT NULL,
                    pancakeswap_price DECIMAL(20, 8) NOT NULL,
                    biswap_price DECIMAL(20, 8) NOT NULL,
                    spread_percentage DECIMAL(10, 4) NOT NULL,
                    price_changed BOOLEAN NOT NULL,
                    best_gross_profit DECIMAL(20, 8) DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Create arbitrage_opportunities table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS arbitrage_opportunities (
                    id SERIAL PRIMARY KEY,
                    scan_id INTEGER REFERENCES price_scans(id),
                    opportunity_timestamp TIMESTAMP NOT NULL,
                    buy_dex VARCHAR(50) NOT NULL,
                    sell_dex VARCHAR(50) NOT NULL,
                    buy_price DECIMAL(20, 8) NOT NULL,
                    sell_price DECIMAL(20, 8) NOT NULL,
                    spread_percentage DECIMAL(10, 4) NOT NULL,
                    tokens_bought DECIMAL(20, 8) NOT NULL,
                    usd_return DECIMAL(20, 4) NOT NULL,
                    gross_profit DECIMAL(20, 4) NOT NULL,
                    net_profit DECIMAL(20, 4) NOT NULL,
                    roi_percentage DECIMAL(10, 4) NOT NULL,
                    flash_loan_amount DECIMAL(20, 4) NOT NULL,
                    executed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Create bot_sessions table to track when bot runs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_sessions (
                    id SERIAL PRIMARY KEY,
                    session_start TIMESTAMP NOT NULL,
                    session_end TIMESTAMP,
                    total_scans INTEGER DEFAULT 0,
                    opportunities_found INTEGER DEFAULT 0,
                    status VARCHAR(20) DEFAULT 'running',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Create indexes for better query performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_scans_timestamp 
                ON price_scans(scan_timestamp);
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_scans_gross_profit 
                ON price_scans(best_gross_profit DESC);
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_opportunities_timestamp 
                ON arbitrage_opportunities(opportunity_timestamp);
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_opportunities_net_profit 
                ON arbitrage_opportunities(net_profit DESC);
            """)
            
            conn.commit()
            print("✓ Database tables created successfully")
            
            # Run migration to add column if it doesn't exist
            self._migrate_add_gross_profit_column(cursor, conn)
            
            return True
            
        except psycopg2.Error as e:
            print(f"✗ Error creating tables: {e}")
            conn.rollback()
            return False
            
        finally:
            cursor.close()
            self.connection_pool.putconn(conn)
    
    def _migrate_add_gross_profit_column(self, cursor, conn):
        """Add best_gross_profit column to existing price_scans table if needed"""
        try:
            # Check if column exists
            cursor.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='price_scans' AND column_name='best_gross_profit'
            """)
            
            if cursor.fetchone() is None:
                # Column doesn't exist, add it
                cursor.execute("""
                    ALTER TABLE price_scans 
                    ADD COLUMN best_gross_profit DECIMAL(20, 8) DEFAULT 0
                """)
                conn.commit()
                print("✓ Added best_gross_profit column to price_scans table")
            
        except psycopg2.Error as e:
            print(f"✗ Migration error: {e}")
            conn.rollback()
    
    def start_session(self) -> Optional[int]:
        """Start a new bot session and return session ID"""
        if not self.connected:
            return None
        
        conn = self.connection_pool.getconn()
        
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bot_sessions (session_start, status)
                VALUES (%s, 'running')
                RETURNING id;
            """, (datetime.now(),))
            
            session_id = cursor.fetchone()[0]
            conn.commit()
            return session_id
            
        except psycopg2.Error as e:
            print(f"✗ Error starting session: {e}")
            conn.rollback()
            return None
            
        finally:
            cursor.close()
            self.connection_pool.putconn(conn)
    
    def end_session(self, session_id: int, total_scans: int, opportunities_found: int):
        """End the current bot session"""
        if not self.connected:
            return
        
        conn = self.connection_pool.getconn()
        
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE bot_sessions
                SET session_end = %s,
                    total_scans = %s,
                    opportunities_found = %s,
                    status = 'completed'
                WHERE id = %s;
            """, (datetime.now(), total_scans, opportunities_found, session_id))
            
            conn.commit()
            
        except psycopg2.Error as e:
            print(f"✗ Error ending session: {e}")
            conn.rollback()
            
        finally:
            cursor.close()
            self.connection_pool.putconn(conn)
    
    def log_price_scan(self, pancake_price: float, biswap_price: float, 
                       spread: float, price_changed: bool,
                       best_gross_profit: float = 0) -> Optional[int]:
        """
        Log a price scan to the database with best gross profit
        Returns the scan_id if successful
        """
        if not self.connected:
            return None
        
        conn = self.connection_pool.getconn()
        
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO price_scans 
                (scan_timestamp, pancakeswap_price, biswap_price, spread_percentage, 
                 price_changed, best_gross_profit)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (datetime.now(), pancake_price, biswap_price, spread, price_changed, best_gross_profit))
            
            scan_id = cursor.fetchone()[0]
            conn.commit()
            return scan_id
            
        except psycopg2.Error as e:
            print(f"✗ Error logging price scan: {e}")
            conn.rollback()
            return None
            
        finally:
            cursor.close()
            self.connection_pool.putconn(conn)
    
    def log_arbitrage_opportunity(self, scan_id: int, opportunity: Dict) -> bool:
        """Log an arbitrage opportunity to the database"""
        if not self.connected:
            return False
        
        conn = self.connection_pool.getconn()
        
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO arbitrage_opportunities 
                (scan_id, opportunity_timestamp, buy_dex, sell_dex, buy_price, sell_price,
                 spread_percentage, tokens_bought, usd_return, gross_profit, net_profit,
                 roi_percentage, flash_loan_amount)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                scan_id,
                datetime.now(),
                opportunity['buy_dex'],
                opportunity['sell_dex'],
                opportunity['buy_price'],
                opportunity['sell_price'],
                opportunity.get('spread', 0),
                opportunity.get('tokens', 0),
                opportunity.get('usd_out', 0),
                opportunity.get('gross', 0),
                opportunity['net'],
                opportunity.get('roi', 0),
                opportunity.get('flash_loan_amount', 100)
            ))
            
            conn.commit()
            return True
            
        except psycopg2.Error as e:
            print(f"✗ Error logging opportunity: {e}")
            conn.rollback()
            return False
            
        finally:
            cursor.close()
            self.connection_pool.putconn(conn)
    
    def get_recent_scans(self, limit: int = 100):
        """Get recent price scans"""
        if not self.connected:
            return []
        
        conn = self.connection_pool.getconn()
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT * FROM price_scans
                ORDER BY scan_timestamp DESC
                LIMIT %s;
            """, (limit,))
            
            results = cursor.fetchall()
            return results
            
        except psycopg2.Error as e:
            print(f"✗ Error fetching scans: {e}")
            return []
            
        finally:
            cursor.close()
            self.connection_pool.putconn(conn)
    
    def get_opportunities(self, min_profit: float = 0.01, limit: int = 100):
        """Get arbitrage opportunities above minimum profit"""
        if not self.connected:
            return []
        
        conn = self.connection_pool.getconn()
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT * FROM arbitrage_opportunities
                WHERE net_profit >= %s
                ORDER BY opportunity_timestamp DESC
                LIMIT %s;
            """, (min_profit, limit))
            
            results = cursor.fetchall()
            return results
            
        except psycopg2.Error as e:
            print(f"✗ Error fetching opportunities: {e}")
            return []
            
        finally:
            cursor.close()
            self.connection_pool.putconn(conn)
    
    def get_statistics(self, hours: int = 24):
        """Get comprehensive statistics for the last N hours"""
        if not self.connected:
            return None
        
        conn = self.connection_pool.getconn()
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get price scan statistics including gross profit
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_scans,
                    COUNT(CASE WHEN price_changed THEN 1 END) as price_changes,
                    AVG(spread_percentage) as avg_spread,
                    MAX(spread_percentage) as max_spread,
                    MIN(spread_percentage) as min_spread,
                    AVG(best_gross_profit) as avg_gross_profit,
                    MAX(best_gross_profit) as max_gross_profit,
                    COUNT(CASE WHEN best_gross_profit > 0 THEN 1 END) as scans_with_profit
                FROM price_scans
                WHERE scan_timestamp >= NOW() - INTERVAL '%s hours';
            """, (hours,))
            
            stats = cursor.fetchone()
            
            # Get opportunity stats
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_opportunities,
                    SUM(net_profit) as total_potential_profit,
                    AVG(net_profit) as avg_profit,
                    MAX(net_profit) as max_profit
                FROM arbitrage_opportunities
                WHERE opportunity_timestamp >= NOW() - INTERVAL '%s hours';
            """, (hours,))
            
            opp_stats = cursor.fetchone()
            
            # Combine stats
            if stats and opp_stats:
                stats.update(opp_stats)
            
            return stats
            
        except psycopg2.Error as e:
            print(f"✗ Error fetching statistics: {e}")
            return None
            
        finally:
            cursor.close()
            self.connection_pool.putconn(conn)
    
    def get_profit_analysis(self, hours: int = 24):
        """Get detailed profit analysis"""
        if not self.connected:
            return None
        
        conn = self.connection_pool.getconn()
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_scans,
                    COUNT(CASE WHEN best_gross_profit > 0 THEN 1 END) as profitable_scans,
                    COUNT(CASE WHEN best_gross_profit >= 0.01 THEN 1 END) as scans_above_threshold,
                    AVG(CASE WHEN best_gross_profit > 0 THEN best_gross_profit END) as avg_positive_profit,
                    MAX(best_gross_profit) as max_profit,
                    MIN(CASE WHEN best_gross_profit > 0 THEN best_gross_profit END) as min_positive_profit,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY best_gross_profit) as median_profit,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY best_gross_profit) as p95_profit
                FROM price_scans
                WHERE scan_timestamp >= NOW() - INTERVAL '%s hours';
            """, (hours,))
            
            return cursor.fetchone()
            
        except psycopg2.Error as e:
            print(f"✗ Error fetching profit analysis: {e}")
            return None
            
        finally:
            cursor.close()
            self.connection_pool.putconn(conn)
    
    def close(self):
        """Close all database connections"""
        if self.connection_pool:
            self.connection_pool.closeall()
            print("✓ Database connections closed")
            self.connected = False


# Example usage and testing
if __name__ == "__main__":
    # Test database connection
    db = ArbitrageDatabase(
        host="localhost",
        database="bsc_arbitrage_db",
        user="postgres",
        password="password=1"
    )
    
    if db.connect():
        print("\n✓ Database connected successfully")
        
        # Create tables
        db.create_tables()
        
        # Test logging a scan with gross profit
        scan_id = db.log_price_scan(
            pancake_price=842.31,
            biswap_price=842.08,
            spread=0.0273,
            price_changed=True,
            best_gross_profit=0.002347
        )
        
        if scan_id:
            print(f"✓ Logged price scan with ID: {scan_id}")
        
        # Get statistics
        stats = db.get_statistics(hours=24)
        if stats:
            print(f"\n📊 Statistics (last 24h):")
            print(f"  Total scans: {stats.get('total_scans', 0)}")
            print(f"  Price changes: {stats.get('price_changes', 0)}")
            print(f"  Scans with profit: {stats.get('scans_with_profit', 0)}")
            print(f"  Avg spread: {float(stats.get('avg_spread', 0)):.4f}%")
            if stats.get('avg_gross_profit'):
                print(f"  Avg gross profit: {float(stats.get('avg_gross_profit', 0)):.6f}")
                print(f"  Max gross profit: {float(stats.get('max_gross_profit', 0)):.6f}")
        
        # Get profit analysis
        profit_analysis = db.get_profit_analysis(hours=24)
        if profit_analysis:
            print(f"\n💰 Profit Analysis:")
            print(f"  Profitable scans: {profit_analysis.get('profitable_scans', 0)}")
            print(f"  Above threshold: {profit_analysis.get('scans_above_threshold', 0)}")
            if profit_analysis.get('avg_positive_profit'):
                print(f"  Avg positive profit: {float(profit_analysis.get('avg_positive_profit', 0)):.6f}")
        
        db.close()