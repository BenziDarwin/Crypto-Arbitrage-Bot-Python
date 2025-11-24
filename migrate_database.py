"""
Database Migration Script
Adds best_gross_profit column to existing price_scans table
Run this once before using the updated bot
"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def migrate_database():
    """Add best_gross_profit column to price_scans table"""
    
    # Database connection parameters
    conn_params = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'database': os.getenv('DB_NAME', 'arbitrage_db'),
        'user': os.getenv('DB_USER', 'postgres'),
        'password': os.getenv('DB_PASSWORD', 'password=1')
    }
    
    print("=" * 70)
    print("DATABASE MIGRATION - Add best_gross_profit Column")
    print("=" * 70)
    print(f"\nConnecting to database: {conn_params['database']}@{conn_params['host']}")
    
    try:
        # Connect to database
        conn = psycopg2.connect(**conn_params)
        cursor = conn.cursor()
        
        print("✓ Connected to database\n")
        
        # Check if column already exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='price_scans' AND column_name='best_gross_profit'
        """)
        
        if cursor.fetchone() is not None:
            print("✓ Column 'best_gross_profit' already exists")
            print("  No migration needed\n")
        else:
            print("Adding 'best_gross_profit' column to price_scans table...")
            
            # Add the column
            cursor.execute("""
                ALTER TABLE price_scans 
                ADD COLUMN best_gross_profit DECIMAL(20, 8) DEFAULT 0
            """)
            
            # Create index for better performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_scans_gross_profit 
                ON price_scans(best_gross_profit DESC)
            """)
            
            conn.commit()
            print("✓ Column added successfully")
            print("✓ Index created\n")
        
        # Verify the column exists
        cursor.execute("""
            SELECT column_name, data_type, column_default
            FROM information_schema.columns 
            WHERE table_name='price_scans' AND column_name='best_gross_profit'
        """)
        
        result = cursor.fetchone()
        if result:
            print("Column Details:")
            print(f"  Name:    {result[0]}")
            print(f"  Type:    {result[1]}")
            print(f"  Default: {result[2]}\n")
        
        # Show table structure
        cursor.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name='price_scans'
            ORDER BY ordinal_position
        """)
        
        print("Current price_scans table structure:")
        print("-" * 50)
        for row in cursor.fetchall():
            print(f"  {row[0]:<30} {row[1]}")
        print("-" * 50)
        
        print("\n✓ Migration completed successfully!")
        print("  You can now run the bot with gross profit logging\n")
        
        cursor.close()
        conn.close()
        
        return True
        
    except psycopg2.Error as e:
        print(f"\n✗ Migration failed: {e}\n")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False
    
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}\n")
        if 'conn' in locals():
            conn.close()
        return False


if __name__ == "__main__":
    print()
    success = migrate_database()
    
    if success:
        print("=" * 70)
        print("NEXT STEPS:")
        print("=" * 70)
        print("1. The migration is complete")
        print("2. You can now run your arbitrage bot")
        print("3. Gross profit will be logged for every scan")
        print("=" * 70)
        print()
    else:
        print("=" * 70)
        print("TROUBLESHOOTING:")
        print("=" * 70)
        print("1. Check your database credentials in .env file")
        print("2. Ensure PostgreSQL is running")
        print("3. Verify you have ALTER TABLE permissions")
        print("=" * 70)
        print()