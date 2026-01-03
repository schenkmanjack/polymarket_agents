"""
Example script showing what orderbook data looks like when logged to the database.
Run this after logging some data to see the structure.
"""
import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.polymarket.orderbook_query import OrderbookQuery


def show_orderbook_structure():
    """Show what a single orderbook snapshot looks like."""
    query = OrderbookQuery()
    
    # Get a recent snapshot
    snapshots = query.get_snapshots(limit=1)
    
    if not snapshots:
        print("No orderbook data found in database.")
        print("\nTo generate data, run:")
        print("  python scripts/python/orderbook_logger.py --mode websocket --tokens YOUR_TOKEN_ID")
        return
    
    snapshot = snapshots[0]
    
    print("=" * 80)
    print("EXAMPLE ORDERBOOK SNAPSHOT STRUCTURE")
    print("=" * 80)
    print()
    
    print("BASIC FIELDS:")
    print(f"  ID: {snapshot.id}")
    print(f"  Token ID: {snapshot.token_id}")
    print(f"  Market ID: {snapshot.market_id}")
    print(f"  Timestamp: {snapshot.timestamp}")
    print(f"  Market Question: {snapshot.market_question}")
    print(f"  Outcome: {snapshot.outcome}")
    print()
    
    print("BEST BID/ASK (Quick Access):")
    print(f"  Best Bid Price: ${snapshot.best_bid_price:.4f}")
    print(f"  Best Bid Size: {snapshot.best_bid_size:,.0f} shares")
    print(f"  Best Ask Price: ${snapshot.best_ask_price:.4f}")
    print(f"  Best Ask Size: {snapshot.best_ask_size:,.0f} shares")
    print()
    
    print("SPREAD METRICS:")
    print(f"  Spread: ${snapshot.spread:.4f}")
    print(f"  Spread (BPS): {snapshot.spread_bps:.2f} basis points")
    if snapshot.best_bid_price and snapshot.best_ask_price:
        mid = (snapshot.best_bid_price + snapshot.best_ask_price) / 2
        print(f"  Mid Price: ${mid:.4f}")
    print()
    
    print("FULL ORDERBOOK LADDERS:")
    print()
    print("BIDS (Buy Orders - sorted descending by price):")
    print("  Price      Size")
    print("  " + "-" * 30)
    if snapshot.bids:
        for i, (price, size) in enumerate(snapshot.bids[:10]):  # Show top 10
            marker = " <-- Best Bid" if i == 0 else ""
            print(f"  ${price:.4f}   {size:,.0f}{marker}")
        if len(snapshot.bids) > 10:
            print(f"  ... ({len(snapshot.bids) - 10} more levels)")
    else:
        print("  (No bids)")
    print()
    
    print("ASKS (Sell Orders - sorted ascending by price):")
    print("  Price      Size")
    print("  " + "-" * 30)
    if snapshot.asks:
        for i, (price, size) in enumerate(snapshot.asks[:10]):  # Show top 10
            marker = " <-- Best Ask" if i == 0 else ""
            print(f"  ${price:.4f}   {size:,.0f}{marker}")
        if len(snapshot.asks) > 10:
            print(f"  ... ({len(snapshot.asks) - 10} more levels)")
    else:
        print("  (No asks)")
    print()
    
    print("METADATA:")
    if snapshot.extra_metadata:
        print(f"  {json.dumps(snapshot.extra_metadata, indent=2)}")
    print()
    
    print("=" * 80)
    print("FOR BACKTESTING:")
    print("=" * 80)
    print()
    print("Each snapshot contains:")
    print("  ✓ Full orderbook depth (all bid/ask levels)")
    print("  ✓ Precise timestamp (when snapshot was taken)")
    print("  ✓ Best bid/ask for quick access")
    print("  ✓ Spread metrics (pre-calculated)")
    print()
    print("You can:")
    print("  1. Reconstruct orderbook state at any point in time")
    print("  2. Simulate order execution against historical orderbook")
    print("  3. Calculate depth, liquidity, imbalance metrics")
    print("  4. Track spread evolution over time")
    print()
    print("Example usage:")
    print("  from agents.polymarket.orderbook_query import OrderbookQuery")
    print("  query = OrderbookQuery()")
    print("  snapshots = query.get_snapshots(token_id='YOUR_TOKEN_ID')")
    print("  for snapshot in snapshots:")
    print("      bids = snapshot.bids  # [[price, size], ...]")
    print("      asks = snapshot.asks  # [[price, size], ...]")
    print()


def show_multiple_snapshots():
    """Show how data evolves over time."""
    query = OrderbookQuery()
    
    snapshots = query.get_snapshots(limit=5)
    
    if not snapshots:
        return
    
    print("=" * 80)
    print("MULTIPLE SNAPSHOTS OVER TIME")
    print("=" * 80)
    print()
    print(f"{'Timestamp':<20} {'Best Bid':<12} {'Best Ask':<12} {'Spread':<12} {'Spread BPS':<12}")
    print("-" * 80)
    
    for snapshot in reversed(snapshots):  # Show oldest first
        bid_str = f"${snapshot.best_bid_price:.4f}" if snapshot.best_bid_price else "N/A"
        ask_str = f"${snapshot.best_ask_price:.4f}" if snapshot.best_ask_price else "N/A"
        spread_str = f"${snapshot.spread:.4f}" if snapshot.spread else "N/A"
        spread_bps_str = f"{snapshot.spread_bps:.2f}" if snapshot.spread_bps else "N/A"
        
        print(
            f"{snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S'):<20} "
            f"{bid_str:<12} {ask_str:<12} {spread_str:<12} {spread_bps_str:<12}"
        )
    print()


if __name__ == "__main__":
    show_orderbook_structure()
    show_multiple_snapshots()

