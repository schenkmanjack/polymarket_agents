"""
Script to clear BTC market orderbook tables.
Useful for starting fresh or cleaning up old data.

Usage:
    python scripts/python/clear_btc_tables.py [--15m] [--1h] [--all]
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from dotenv import load_dotenv
load_dotenv()

from agents.polymarket.orderbook_db import OrderbookDatabase
from sqlalchemy import text


def clear_table(db: OrderbookDatabase, table_name: str) -> int:
    """Clear a specific table and return the number of rows deleted."""
    with db.engine.connect() as conn:
        result = conn.execute(text(f'SELECT COUNT(*) FROM {table_name}'))
        count = result.scalar()
    
    if count > 0:
        with db.engine.begin() as conn:
            conn.execute(text(f'DELETE FROM {table_name}'))
        
        # Verify deletion
        with db.engine.connect() as conn:
            result = conn.execute(text(f'SELECT COUNT(*) FROM {table_name}'))
            new_count = result.scalar()
        
        return count
    return 0


def main():
    parser = argparse.ArgumentParser(description='Clear BTC market orderbook tables')
    parser.add_argument('--15m', action='store_true', help='Clear btc_15_min_table')
    parser.add_argument('--1h', action='store_true', help='Clear btc_1_hour_table')
    parser.add_argument('--all', action='store_true', help='Clear all BTC tables')
    
    args = parser.parse_args()
    
    # If no flags, default to --all
    if not args.__dict__['15m'] and not args.__dict__['1h']:
        args.all = True
    
    cleared = {}
    
    if args.all or args.__dict__['15m']:
        print('Clearing btc_15_min_table...')
        db_15m = OrderbookDatabase(use_btc_15_min_table=True)
        count = clear_table(db_15m, 'btc_15_min_table')
        cleared['btc_15_min_table'] = count
        print(f'  ✓ Deleted {count} rows from btc_15_min_table')
    
    if args.all or args.__dict__['1h']:
        print('Clearing btc_1_hour_table...')
        db_1h = OrderbookDatabase(use_btc_1_hour_table=True)
        count = clear_table(db_1h, 'btc_1_hour_table')
        cleared['btc_1_hour_table'] = count
        print(f'  ✓ Deleted {count} rows from btc_1_hour_table')
    
    print(f'\n✓ Complete! Cleared {sum(cleared.values())} total rows')


if __name__ == '__main__':
    main()

