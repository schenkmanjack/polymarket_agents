"""
Test script to verify database connection (Neon PostgreSQL or SQLite).
Run this to make sure your DATABASE_URL is working.

Usage:
    # Test with environment variable
    export DATABASE_URL="postgresql://user:pass@host.neon.tech/dbname?sslmode=require"
    python scripts/python/test_db_connection.py
    
    # Or pass as argument
    python scripts/python/test_db_connection.py --database-url "postgresql://..."
"""
import sys
import os
import argparse
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

# Load .env file
load_dotenv()

from agents.polymarket.orderbook_db import OrderbookDatabase, OrderbookSnapshot


def test_connection(database_url=None):
    """Test database connection and create a test snapshot."""
    print("Testing database connection...")
    print(f"Database URL: {database_url[:50]}..." if database_url and len(database_url) > 50 else f"Database URL: {database_url}")
    print()
    
    try:
        # Initialize database
        db = OrderbookDatabase(database_url=database_url)
        print("✓ Database connection successful!")
        print()
        
        # Create a test snapshot
        print("Creating test snapshot...")
        test_snapshot = db.save_snapshot(
            token_id="test_token_123",
            bids=[[0.45, 1000.0], [0.44, 2000.0]],
            asks=[[0.46, 500.0], [0.47, 800.0]],
            market_id="test_market_123",
            market_question="Test market - can be deleted",
            outcome="Yes",
            metadata={"test": True, "created_at": datetime.utcnow().isoformat()},
        )
        print(f"✓ Test snapshot created with ID: {test_snapshot.id}")
        print()
        
        # Query it back
        print("Querying test snapshot...")
        snapshots = db.get_snapshots(token_id="test_token_123", limit=1)
        if snapshots:
            snapshot = snapshots[0]
            print(f"✓ Retrieved snapshot:")
            print(f"  - ID: {snapshot.id}")
            print(f"  - Token ID: {snapshot.token_id}")
            print(f"  - Timestamp: {snapshot.timestamp}")
            print(f"  - Best Bid: ${snapshot.best_bid_price}")
            print(f"  - Best Ask: ${snapshot.best_ask_price}")
            print(f"  - Spread: ${snapshot.spread}")
            print()
            print("✓ Database is working correctly!")
            print()
            print("Note: You can delete the test snapshot if you want:")
            print(f"  DELETE FROM orderbook_snapshots WHERE id = {snapshot.id};")
        else:
            print("✗ Could not retrieve test snapshot")
            return False
        
        return True
        
    except Exception as e:
        print(f"✗ Database connection failed: {e}")
        print()
        print("Troubleshooting:")
        print("1. Check your DATABASE_URL is correct")
        print("2. For Neon: Ensure SSL is enabled (add ?sslmode=require)")
        print("3. For Neon: Check that your IP is allowed (Neon allows all by default)")
        print("4. Verify the database exists and credentials are correct")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test database connection")
    parser.add_argument(
        "--database-url",
        help="Database URL (overrides DATABASE_URL env var)",
    )
    
    args = parser.parse_args()
    
    # Get database URL from args or environment
    database_url = args.database_url or os.getenv("DATABASE_URL")
    
    if not database_url:
        print("Error: No DATABASE_URL provided")
        print()
        print("Usage:")
        print("  export DATABASE_URL='postgresql://...'")
        print("  python scripts/python/test_db_connection.py")
        print()
        print("Or:")
        print("  python scripts/python/test_db_connection.py --database-url 'postgresql://...'")
        sys.exit(1)
    
    success = test_connection(database_url)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

