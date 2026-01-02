"""
Query historical orderbook data from the database.

Usage:
    # Get snapshots for a token:
    python scripts/python/query_orderbook.py --token TOKEN_ID --limit 100
    
    # Get spread history:
    python scripts/python/query_orderbook.py --token TOKEN_ID --spread-history
    
    # Export to CSV:
    python scripts/python/query_orderbook.py --token TOKEN_ID --export output.csv
    
    # Get statistics:
    python scripts/python/query_orderbook.py --token TOKEN_ID --stats
"""
import argparse
import sys
import os
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.polymarket.orderbook_query import OrderbookQuery

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("Warning: pandas not available. Some features will be limited.")


def main():
    parser = argparse.ArgumentParser(
        description="Query historical orderbook data"
    )
    parser.add_argument(
        "--token",
        help="Token ID to query",
    )
    parser.add_argument(
        "--market",
        help="Market ID to query",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of results",
    )
    parser.add_argument(
        "--start-time",
        help="Start time (ISO format: YYYY-MM-DDTHH:MM:SS)",
    )
    parser.add_argument(
        "--end-time",
        help="End time (ISO format: YYYY-MM-DDTHH:MM:SS)",
    )
    parser.add_argument(
        "--spread-history",
        action="store_true",
        help="Show spread history",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show statistics",
    )
    parser.add_argument(
        "--export",
        help="Export to CSV file",
    )
    parser.add_argument(
        "--db-path",
        help="Path to SQLite database (default: ./orderbook.db)",
    )
    
    args = parser.parse_args()
    
    if not args.token and not args.market:
        parser.error("Must provide either --token or --market")
    
    # Parse time arguments
    start_time = None
    end_time = None
    if args.start_time:
        start_time = datetime.fromisoformat(args.start_time.replace("Z", "+00:00"))
    if args.end_time:
        end_time = datetime.fromisoformat(args.end_time.replace("Z", "+00:00"))
    
    query = OrderbookQuery(db_path=args.db_path)
    
    # Get snapshots
    snapshots = query.get_snapshots(
        token_id=args.token,
        market_id=args.market,
        start_time=start_time,
        end_time=end_time,
        limit=args.limit,
    )
    
    if not snapshots:
        print("No snapshots found")
        return
    
    print(f"Found {len(snapshots)} snapshots")
    
    # Handle different output modes
    if args.stats:
        if not args.token:
            print("Statistics require --token")
            return
        
        stats = query.get_statistics(
            token_id=args.token,
            start_time=start_time,
            end_time=end_time,
        )
        print("\nStatistics:")
        for key, value in stats.items():
            print(f"  {key}: {value}")
    
    elif args.spread_history:
        if not args.token:
            print("Spread history requires --token")
            return
        
        if not PANDAS_AVAILABLE:
            print("pandas required for spread history")
            return
        
        df = query.get_spread_history(
            token_id=args.token,
            start_time=start_time,
            end_time=end_time,
        )
        print("\nSpread History:")
        print(df.to_string())
    
    elif args.export:
        if not PANDAS_AVAILABLE:
            print("pandas required for CSV export")
            return
        
        query.export_to_csv(
            output_path=args.export,
            token_id=args.token,
            market_id=args.market,
            start_time=start_time,
            end_time=end_time,
            limit=args.limit * 10,  # Export more for CSV
        )
    
    else:
        # Default: show recent snapshots
        print("\nRecent Snapshots:")
        print(f"{'Timestamp':<20} {'Token ID':<20} {'Bid':<10} {'Ask':<10} {'Spread':<10} {'Spread BPS':<10}")
        print("-" * 100)
        for snapshot in snapshots[-10:]:  # Show last 10
            bid_str = f"{snapshot.best_bid_price:.4f}" if snapshot.best_bid_price else "N/A"
            ask_str = f"{snapshot.best_ask_price:.4f}" if snapshot.best_ask_price else "N/A"
            spread_str = f"{snapshot.spread:.4f}" if snapshot.spread else "N/A"
            spread_bps_str = f"{snapshot.spread_bps:.2f}" if snapshot.spread_bps else "N/A"
            print(
                f"{snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S'):<20} "
                f"{snapshot.token_id[:18]:<20} "
                f"{bid_str:<10} {ask_str:<10} {spread_str:<10} {spread_bps_str:<10}"
            )


if __name__ == "__main__":
    main()

