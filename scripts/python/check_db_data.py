"""
Quick script to check if there's any data in the database.
"""
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

# Load .env file
load_dotenv()

from agents.polymarket.orderbook_db import OrderbookDatabase

def check_database():
    """Check if database has any data."""
    print("Checking database for orderbook snapshots...")
    print()
    
    try:
        db = OrderbookDatabase()
        
        # Get total count
        from sqlalchemy import func
        from agents.polymarket.orderbook_db import OrderbookSnapshot
        
        session = db.get_session()
        try:
            total_count = session.query(func.count(OrderbookSnapshot.id)).scalar()
            print(f"Total snapshots in database: {total_count}")
            
            if total_count == 0:
                print()
                print("⚠ No data found in database!")
                print("This could mean:")
                print("  1. WebSocket isn't receiving orderbook updates")
                print("  2. Updates are being received but not saved (check Railway logs for errors)")
                print("  3. Database connection issue")
                return
            
            # Get most recent snapshots
            print()
            print("Most recent snapshots:")
            recent = session.query(OrderbookSnapshot).order_by(OrderbookSnapshot.timestamp.desc()).limit(5).all()
            
            for snapshot in recent:
                print(f"  - ID: {snapshot.id}")
                print(f"    Token: {snapshot.token_id[:30]}...")
                print(f"    Timestamp: {snapshot.timestamp}")
                print(f"    Bid: {snapshot.best_bid_price}, Ask: {snapshot.best_ask_price}")
                print(f"    Market: {snapshot.market_question[:50] if snapshot.market_question else 'N/A'}...")
                print()
            
            # Get unique token IDs
            unique_tokens = session.query(func.count(func.distinct(OrderbookSnapshot.token_id))).scalar()
            print(f"Unique tokens being monitored: {unique_tokens}")
            
            # Get date range
            oldest = session.query(func.min(OrderbookSnapshot.timestamp)).scalar()
            newest = session.query(func.max(OrderbookSnapshot.timestamp)).scalar()
            print(f"Date range: {oldest} to {newest}")
            
        finally:
            session.close()
        
    except Exception as e:
        print(f"❌ Error checking database: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    check_database()

