#!/usr/bin/env python3
"""
Query historical orderbook data for BTC 15-minute markets.

This script allows you to:
1. Query orderbook snapshots at different timesteps for a historical market
2. View full orderbook depth (all bid/ask levels) at specific times
3. Export orderbook data for backtesting analysis

Usage:
    # Query orderbooks for a specific market by market ID:
    python scripts/python/query_btc_market_orderbooks.py --market MARKET_ID --start-time "2024-01-01T12:00:00" --end-time "2024-01-01T12:15:00"
    
    # Query by token ID:
    python scripts/python/query_btc_market_orderbooks.py --token TOKEN_ID --limit 100
    
    # View full orderbook depth at a specific time:
    python scripts/python/query_btc_market_orderbooks.py --market MARKET_ID --at-time "2024-01-01T12:05:00" --show-depth
    
    # Export to CSV for analysis:
    python scripts/python/query_btc_market_orderbooks.py --market MARKET_ID --export orderbooks.csv
"""
import argparse
import sys
import os
from datetime import datetime, timedelta
import json

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import httpx
from agents.polymarket.orderbook_query import OrderbookQuery, get_market_token_ids
from agents.backtesting.market_fetcher import HistoricalMarketFetcher

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("Warning: pandas not available. Some features will be limited.")


def print_orderbook_depth(snapshot, show_all_levels: bool = False, max_levels: int = 10):
    """Print full orderbook depth for a snapshot."""
    print(f"\n{'='*80}")
    print(f"Orderbook at {snapshot.timestamp} (UTC)")
    print(f"Token ID: {snapshot.token_id}")
    if snapshot.market_question:
        print(f"Market: {snapshot.market_question}")
    if snapshot.outcome:
        print(f"Outcome: {snapshot.outcome}")
    print(f"{'='*80}")
    
    # Best bid/ask summary
    print(f"\nBest Bid: {snapshot.best_bid_price:.6f} @ {snapshot.best_bid_size:.2f}")
    print(f"Best Ask: {snapshot.best_ask_price:.6f} @ {snapshot.best_ask_size:.2f}")
    print(f"Spread: {snapshot.spread:.6f} ({snapshot.spread_bps:.2f} bps)")
    
    # Full orderbook depth
    if snapshot.bids and snapshot.asks:
        print(f"\n{'BIDS':<40} {'ASKS':<40}")
        print("-" * 80)
        
        bids = snapshot.bids if isinstance(snapshot.bids, list) else json.loads(snapshot.bids) if isinstance(snapshot.bids, str) else []
        asks = snapshot.asks if isinstance(snapshot.asks, list) else json.loads(snapshot.asks) if isinstance(snapshot.asks, str) else []
        
        # Show top N levels from each side
        num_levels = min(max_levels, len(bids), len(asks)) if not show_all_levels else max(len(bids), len(asks))
        
        for i in range(num_levels):
            bid_str = ""
            ask_str = ""
            
            if i < len(bids):
                bid_price, bid_size = bids[i]
                bid_str = f"{bid_price:.6f} @ {bid_size:.2f}"
            
            if i < len(asks):
                ask_price, ask_size = asks[i]
                ask_str = f"{ask_price:.6f} @ {ask_size:.2f}"
            
            print(f"{bid_str:<40} {ask_str:<40}")
        
        if not show_all_levels and (len(bids) > max_levels or len(asks) > max_levels):
            print(f"\n... ({len(bids)} total bid levels, {len(asks)} total ask levels)")
            print("Use --show-all-levels to see full depth")
    else:
        print("\n⚠️  Full orderbook depth not available (only best bid/ask stored)")


def get_market_info(market_id: str) -> dict:
    """Get market information including token IDs."""
    fetcher = HistoricalMarketFetcher()
    
    # Try to get market from API
    try:
        url = f"{fetcher.gamma_markets_endpoint}/{market_id}"
        response = httpx.get(url, proxies=fetcher.proxy, timeout=10.0)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Warning: Could not fetch market info: {e}")
    
    return {}


def main():
    parser = argparse.ArgumentParser(
        description="Query historical orderbook data for BTC 15-minute markets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Query orderbooks for a market during its active period:
  python scripts/python/query_btc_market_orderbooks.py --market 12345 --start-time "2024-01-01T12:00:00" --end-time "2024-01-01T12:15:00"
  
  # View orderbook at a specific time:
  python scripts/python/query_btc_market_orderbooks.py --market 12345 --at-time "2024-01-01T12:05:00" --show-depth
  
  # Export all orderbook data:
  python scripts/python/query_btc_market_orderbooks.py --market 12345 --export orderbooks.csv
        """
    )
    
    parser.add_argument("--market", help="Polymarket market ID")
    parser.add_argument("--token", help="CLOB token ID (alternative to --market)")
    parser.add_argument("--start-time", help="Start time (ISO format: YYYY-MM-DDTHH:MM:SS)")
    parser.add_argument("--end-time", help="End time (ISO format: YYYY-MM-DDTHH:MM:SS)")
    parser.add_argument("--at-time", help="Specific time to query (ISO format: YYYY-MM-DDTHH:MM:SS)")
    parser.add_argument("--limit", type=int, default=100, help="Maximum number of snapshots")
    parser.add_argument("--show-depth", action="store_true", help="Show full orderbook depth")
    parser.add_argument("--show-all-levels", action="store_true", help="Show all orderbook levels (not just top 10)")
    parser.add_argument("--export", help="Export to CSV file")
    parser.add_argument("--db-path", help="Path to SQLite database (default: uses DATABASE_URL env var)")
    
    args = parser.parse_args()
    
    if not args.market and not args.token:
        parser.error("Must provide either --market or --token")
    
    # Parse time arguments
    start_time = None
    end_time = None
    at_time = None
    
    if args.start_time:
        start_time = datetime.fromisoformat(args.start_time.replace("Z", "+00:00"))
    if args.end_time:
        end_time = datetime.fromisoformat(args.end_time.replace("Z", "+00:00"))
    if args.at_time:
        at_time = datetime.fromisoformat(args.at_time.replace("Z", "+00:00"))
    
    # Initialize query - use btc_eth_table like monitor_btc_15m.py does
    from agents.polymarket.orderbook_db import OrderbookDatabase
    db = OrderbookDatabase(use_btc_eth_table=True)
    query = OrderbookQuery(db=db)
    
    # Get token IDs if market ID provided
    token_ids = []
    if args.market:
        # First try to get token IDs from database (works for closed markets)
        print(f"Looking for token IDs in database for market {args.market}...")
        try:
            snapshots_with_market = query.get_snapshots(market_id=args.market, limit=100)
            print(f"  Query returned {len(snapshots_with_market)} snapshots")
            if snapshots_with_market:
                token_ids = list(set(s.token_id for s in snapshots_with_market))
                print(f"✓ Found {len(token_ids)} token ID(s) in database: {token_ids}")
                print(f"  Found {len(snapshots_with_market)} snapshots for this market")
            else:
                print(f"  No snapshots found in database for market {args.market}")
        except Exception as e:
            print(f"  Error querying database: {e}")
            snapshots_with_market = []
        
        if not snapshots_with_market:
            # Fallback: try API (may fail for closed markets)
            print(f"Not found in database, trying API...")
            try:
                token_ids = get_market_token_ids(args.market)
                if token_ids:
                    print(f"✓ Found {len(token_ids)} token ID(s) from API: {token_ids}")
            except Exception as e:
                print(f"⚠️  Could not get token IDs for market {args.market}")
                print(f"   Error: {e}")
                print("   This might mean:")
                print("   1. Market is closed and token IDs are no longer available")
                print("   2. Market doesn't exist")
                print("   3. No orderbook data was logged for this market")
                print("\n   Try querying by --token ID directly if you know it.")
                return
    elif args.token:
        token_ids = [args.token]
    
    if not token_ids:
        print("No token IDs found. Cannot query orderbooks.")
        return
    
    # Query orderbooks
    all_snapshots = []
    for token_id in token_ids:
        if at_time:
            # Get snapshot at specific time
            snapshot = query.get_orderbook_at_time(
                token_id=token_id,
                target_time=at_time,
                tolerance_seconds=60,
            )
            if snapshot:
                all_snapshots.append(snapshot)
        else:
            # Get snapshots in time range
            snapshots = query.get_snapshots(
                token_id=token_id,
                market_id=args.market,
                start_time=start_time,
                end_time=end_time,
                limit=args.limit,
            )
            all_snapshots.extend(snapshots)
    
    if not all_snapshots:
        print("\n⚠️  No orderbook snapshots found!")
        print("\nPossible reasons:")
        print("  1. Orderbooks were not logged during the market's active period")
        print("  2. Time range doesn't match when the market was active")
        print("  3. Token ID is incorrect")
        print("\nTo log orderbooks for future markets, use:")
        print("  python scripts/python/orderbook_logger.py --mode websocket --market MARKET_ID")
        return
    
    print(f"\n✓ Found {len(all_snapshots)} orderbook snapshot(s)")
    
    # Sort by timestamp
    all_snapshots.sort(key=lambda s: s.timestamp)
    
    # Handle different output modes
    if args.export:
        if not PANDAS_AVAILABLE:
            print("pandas required for CSV export")
            return
        
        # Export to CSV
        df = query.get_snapshots_dataframe(
            token_id=token_ids[0] if len(token_ids) == 1 else None,
            market_id=args.market,
            start_time=start_time,
            end_time=end_time,
            limit=args.limit * 10,
        )
        
        if df.empty:
            print("No data to export")
            return
        
        df.to_csv(args.export, index=False)
        print(f"✓ Exported {len(df)} snapshots to {args.export}")
    
    elif args.show_depth or at_time:
        # Show detailed orderbook depth
        max_levels = 1000 if args.show_all_levels else 10
        
        if at_time:
            # Show single snapshot at specific time
            if all_snapshots:
                print_orderbook_depth(all_snapshots[0], show_all_levels=args.show_all_levels, max_levels=max_levels)
        else:
            # Show depth for multiple snapshots
            for snapshot in all_snapshots[:5]:  # Show first 5
                print_orderbook_depth(snapshot, show_all_levels=args.show_all_levels, max_levels=max_levels)
            
            if len(all_snapshots) > 5:
                print(f"\n... ({len(all_snapshots) - 5} more snapshots available)")
    
    else:
        # Default: show summary table
        print(f"\n{'Timestamp':<20} {'Token ID':<20} {'Bid':<12} {'Ask':<12} {'Spread':<12} {'Spread BPS':<10}")
        print("-" * 100)
        
        for snapshot in all_snapshots[:20]:  # Show first 20
            bid_str = f"{snapshot.best_bid_price:.6f}" if snapshot.best_bid_price else "N/A"
            ask_str = f"{snapshot.best_ask_price:.6f}" if snapshot.best_ask_price else "N/A"
            spread_str = f"{snapshot.spread:.6f}" if snapshot.spread else "N/A"
            spread_bps_str = f"{snapshot.spread_bps:.2f}" if snapshot.spread_bps else "N/A"
            
            print(
                f"{snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S'):<20} "
                f"{snapshot.token_id[:18]:<20} "
                f"{bid_str:<12} {ask_str:<12} {spread_str:<12} {spread_bps_str:<10}"
            )
        
        if len(all_snapshots) > 20:
            print(f"\n... ({len(all_snapshots) - 20} more snapshots)")
            print("\nUse --show-depth to see full orderbook depth")
            print("Use --export FILE.csv to export all data")


if __name__ == "__main__":
    main()

