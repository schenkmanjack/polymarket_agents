#!/usr/bin/env python3
"""
Test script to verify market maker database connection (Neon PostgreSQL or SQLite).

This script:
1. Tests database connection
2. Verifies the real_market_maker_positions table exists
3. Shows table structure
4. Confirms Neon/PostgreSQL is being used if DATABASE_URL is set

Usage:
    # Use Neon (from environment variable)
    export DATABASE_URL="postgresql://user:pass@host.neon.tech/dbname?sslmode=require"
    python scripts/python/test_market_maker_db.py

    # Or specify directly
    python scripts/python/test_market_maker_db.py --database-url "postgresql://..."
"""
import os
import sys
import argparse
from sqlalchemy import inspect, text
from agents.trading.trade_db import TradeDatabase, RealMarketMakerPosition


def test_market_maker_db(database_url=None):
    """Test market maker database connection and table."""
    print("=" * 80)
    print("TESTING MARKET MAKER DATABASE CONNECTION")
    print("=" * 80)
    print()
    
    if database_url:
        print(f"Database URL: {database_url[:50]}..." if len(database_url) > 50 else f"Database URL: {database_url}")
    else:
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            print(f"Database URL: {database_url[:50]}..." if len(database_url) > 50 else f"Database URL: {database_url}")
        else:
            print("⚠️  No DATABASE_URL found - will use SQLite")
    
    print()
    
    try:
        # Initialize database
        db = TradeDatabase(database_url=database_url)
        
        # Check database type
        db_url_str = str(db.engine.url)
        if 'postgresql' in db_url_str.lower():
            db_type = "PostgreSQL/Neon"
            print(f"✓ Connected to {db_type} database")
        elif 'sqlite' in db_url_str.lower():
            db_type = "SQLite"
            print(f"⚠️  Using {db_type} database (set DATABASE_URL to use Neon)")
        else:
            db_type = "Unknown"
            print(f"⚠️  Unknown database type: {db_url_str[:50]}")
        
        print()
        
        # Check if table exists
        inspector = inspect(db.engine)
        table_name = "real_market_maker_positions"
        
        if table_name in inspector.get_table_names():
            print(f"✓ Table '{table_name}' exists")
            
            # Get table columns
            columns = inspector.get_columns(table_name)
            print(f"\nTable structure ({len(columns)} columns):")
            print("-" * 80)
            for col in columns:
                col_name = col['name']
                col_type = str(col['type'])
                nullable = "NULL" if col['nullable'] else "NOT NULL"
                print(f"  {col_name:40} {col_type:30} {nullable}")
            
            # Check indexes
            indexes = inspector.get_indexes(table_name)
            if indexes:
                print(f"\nIndexes ({len(indexes)}):")
                print("-" * 80)
                for idx in indexes:
                    idx_cols = ', '.join(idx['column_names'])
                    unique = "UNIQUE" if idx.get('unique', False) else ""
                    print(f"  {idx['name']:40} on ({idx_cols}) {unique}")
            
            # Count existing records
            session = db.SessionLocal()
            try:
                count = session.query(RealMarketMakerPosition).count()
                print(f"\n✓ Existing records: {count}")
            finally:
                session.close()
            
        else:
            print(f"❌ Table '{table_name}' does NOT exist")
            print("   This should be created automatically when TradeDatabase is initialized.")
            print("   Try running the market maker to create it.")
            return False
        
        print()
        print("=" * 80)
        print("✅ DATABASE TEST PASSED")
        print("=" * 80)
        return True
        
    except Exception as e:
        print()
        print("=" * 80)
        print("❌ DATABASE TEST FAILED")
        print("=" * 80)
        print(f"Error: {e}")
        print()
        print("Troubleshooting:")
        print("1. Check your DATABASE_URL is correct")
        print("2. For Neon: Ensure SSL is enabled (add ?sslmode=require)")
        print("3. For Neon: Check that your IP is allowed")
        print("4. Verify database credentials are correct")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Test market maker database connection (Neon PostgreSQL or SQLite)"
    )
    parser.add_argument(
        "--database-url",
        help="Database URL (overrides DATABASE_URL env var)",
        default=None,
    )
    
    args = parser.parse_args()
    
    database_url = args.database_url or os.getenv("DATABASE_URL")
    
    if not database_url:
        print("⚠️  No DATABASE_URL provided")
        print()
        print("Usage:")
        print("  export DATABASE_URL='postgresql://...'")
        print("  python scripts/python/test_market_maker_db.py")
        print()
        print("  OR")
        print()
        print("  python scripts/python/test_market_maker_db.py --database-url 'postgresql://...'")
        print()
        print("Note: If DATABASE_URL is not set, SQLite will be used.")
        print()
    
    success = test_market_maker_db(database_url)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
