"""
Script to check recent trades and their order/fill prices.
"""
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from agents.trading.trade_db import TradeDatabase

def main():
    db = TradeDatabase()
    
    # Get most recent trades
    session = db.SessionLocal()
    try:
        from agents.trading.trade_db import RealTradeThreshold
        from sqlalchemy import desc
        
        trades = session.query(RealTradeThreshold).order_by(desc(RealTradeThreshold.order_placed_at)).limit(10).all()
        
        if not trades:
            print("No trades found in database.")
            return
        
        print(f"Found {len(trades)} recent trades:\n")
        
        for trade in trades:
            print(f"Trade ID: {trade.id}")
            print(f"  Market: {trade.market_slug}")
            print(f"  Order ID: {trade.order_id}")
            print(f"  Order Price (limit): ${trade.order_price:.4f}")
            print(f"  Order Size: {trade.order_size} shares")
            print(f"  Order Value: ${trade.order_price * trade.order_size:.2f}")
            print(f"  Order Status: {trade.order_status}")
            
            if trade.fill_price:
                print(f"  Fill Price: ${trade.fill_price:.4f}")
                print(f"  Filled Shares: {trade.filled_shares}")
                print(f"  Dollars Spent: ${trade.dollars_spent:.2f}")
                print(f"  Fee: ${trade.fee:.4f}")
                
                # Calculate price difference
                price_diff = abs(trade.fill_price - trade.order_price)
                if price_diff > 0.01:
                    print(f"  ⚠️  WARNING: Fill price differs from limit price by ${price_diff:.4f}")
                    print(f"     Expected: ${trade.order_price:.4f}, Got: ${trade.fill_price:.4f}")
                
                # Check if fill price seems wrong
                if trade.fill_price > 0.90:
                    print(f"  ⚠️  WARNING: Fill price is very high (${trade.fill_price:.4f})")
                    print(f"     This might indicate an issue with how fill price is being read from API")
            
            print(f"  Placed At: {trade.order_placed_at}")
            if trade.order_filled_at:
                print(f"  Filled At: {trade.order_filled_at}")
            print()
    finally:
        session.close()

if __name__ == "__main__":
    main()
