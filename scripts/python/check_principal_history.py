#!/usr/bin/env python3
"""
Check principal history from database to debug why principal went negative.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.trading.trade_db import TradeDatabase
from datetime import datetime

def main():
    db = TradeDatabase()
    session = db.SessionLocal()
    
    try:
        from agents.trading.trade_db import RealTradeThreshold
        
        # Get all trades ordered by placement time
        all_trades = session.query(RealTradeThreshold).order_by(RealTradeThreshold.order_placed_at.desc()).limit(10).all()
        
        print("=" * 100)
        print("RECENT TRADES - PRINCIPAL HISTORY")
        print("=" * 100)
        print()
        
        if not all_trades:
            print("No trades found in database.")
            return
        
        trades = [t for t in all_trades if t.order_id]  # Filter to only trades with order_id
        if not trades:
            print(f"Found {len(all_trades)} trades but none have order_id set.")
            print("Showing all trades anyway:")
            trades = all_trades
        
        for trade in reversed(trades):  # Show oldest first
            print(f"Trade ID: {trade.id}")
            print(f"  Market: {trade.market_slug}")
            print(f"  Side: {trade.order_side}")
            print(f"  Order Price: ${trade.order_price:.4f}")
            print(f"  Order Size: {trade.order_size:.2f} shares")
            print(f"  Dollars Spent: ${trade.dollars_spent:.2f}")
            print(f"  Buy Fee: ${trade.fee:.4f}")
            print(f"  Sell Order Filled: {trade.sell_order_status == 'filled'}")
            print(f"  Sell Dollars Received: ${trade.sell_dollars_received:.2f if trade.sell_dollars_received else 0:.2f}")
            print(f"  Sell Fee: ${trade.sell_fee:.4f if trade.sell_fee else 0:.4f}")
            print(f"  Outcome Price: {trade.outcome_price:.4f if trade.outcome_price is not None else 'N/A'}")
            print(f"  Winning Side: {trade.winning_side}")
            print(f"  Payout: ${trade.payout:.2f if trade.payout else 0:.2f}")
            print(f"  Net Payout: ${trade.net_payout:.2f if trade.net_payout else 0:.2f}")
            print(f"  ROI: {trade.roi*100:.2f}% if trade.roi else 'N/A'")
            print(f"  Is Win: {trade.is_win}")
            print(f"  Principal Before: ${trade.principal_before:.2f}")
            print(f"  Principal After: ${trade.principal_after:.2f if trade.principal_after else 'N/A'}")
            print(f"  Resolved At: {trade.market_resolved_at}")
            print()
        
        # Get latest principal
        latest_principal = db.get_latest_principal()
        print(f"Latest Principal from DB: ${latest_principal:.2f if latest_principal else 'N/A'}")
        
    finally:
        session.close()

if __name__ == "__main__":
    main()
