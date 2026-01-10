"""
Quick script to count 15-minute and 1-hour markets in the database.
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.polymarket.orderbook_db import OrderbookDatabase
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv()


def count_markets():
    """Count 15-minute and 1-hour markets in the database."""
    
    # Count 15-minute markets
    db_15m = OrderbookDatabase(use_btc_15_min_table=True)
    with db_15m.get_session() as session:
        query_15m = text("""
            SELECT COUNT(DISTINCT market_id) as market_count
            FROM btc_15_min_table
        """)
        result_15m = session.execute(query_15m)
        count_15m = result_15m.scalar() or 0
        
        # Also get snapshot count
        query_snapshots_15m = text("""
            SELECT COUNT(*) as snapshot_count
            FROM btc_15_min_table
        """)
        result_snapshots_15m = session.execute(query_snapshots_15m)
        snapshot_count_15m = result_snapshots_15m.scalar() or 0
    
    # Count 1-hour markets
    db_1h = OrderbookDatabase(use_btc_1_hour_table=True)
    with db_1h.get_session() as session:
        query_1h = text("""
            SELECT COUNT(DISTINCT market_id) as market_count
            FROM btc_1_hour_table
        """)
        result_1h = session.execute(query_1h)
        count_1h = result_1h.scalar() or 0
        
        # Also get snapshot count
        query_snapshots_1h = text("""
            SELECT COUNT(*) as snapshot_count
            FROM btc_1_hour_table
        """)
        result_snapshots_1h = session.execute(query_snapshots_1h)
        snapshot_count_1h = result_snapshots_1h.scalar() or 0
    
    print("=" * 60)
    print("MARKET COUNT SUMMARY")
    print("=" * 60)
    print(f"\n15-minute markets: {count_15m}")
    print(f"  Total snapshots: {snapshot_count_15m:,}")
    if count_15m > 0:
        print(f"  Avg snapshots per market: {snapshot_count_15m // count_15m if count_15m > 0 else 0}")
    
    print(f"\n1-hour markets: {count_1h}")
    print(f"  Total snapshots: {snapshot_count_1h:,}")
    if count_1h > 0:
        print(f"  Avg snapshots per market: {snapshot_count_1h // count_1h if count_1h > 0 else 0}")
    
    print(f"\nTotal markets: {count_15m + count_1h}")
    print(f"Total snapshots: {snapshot_count_15m + snapshot_count_1h:,}")
    print("=" * 60)


if __name__ == "__main__":
    count_markets()

