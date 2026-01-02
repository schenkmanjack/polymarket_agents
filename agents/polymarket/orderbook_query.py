"""
Utilities for querying historical orderbook data from the database.
"""
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    pd = None

from agents.polymarket.orderbook_db import OrderbookDatabase, OrderbookSnapshot


class OrderbookQuery:
    """Query utilities for historical orderbook data."""
    
    def __init__(self, db: Optional[OrderbookDatabase] = None, db_path: Optional[str] = None):
        """
        Initialize query utilities.
        
        Args:
            db: Optional OrderbookDatabase instance
            db_path: Optional path to SQLite database (if db not provided)
        """
        if db is None:
            self.db = OrderbookDatabase(
                database_url=None if db_path is None else f"sqlite:///{db_path}"
            )
        else:
            self.db = db
    
    def get_snapshots(
        self,
        token_id: Optional[str] = None,
        market_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[OrderbookSnapshot]:
        """Get orderbook snapshots with filters."""
        return self.db.get_snapshots(
            token_id=token_id,
            market_id=market_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
    
    def get_snapshots_dataframe(
        self,
        token_id: Optional[str] = None,
        market_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 10000,
    ):
        """
        Get orderbook snapshots as a pandas DataFrame.
        
        Returns:
            DataFrame with columns: timestamp, token_id, market_id, best_bid_price,
            best_bid_size, best_ask_price, best_ask_size, spread, spread_bps
        """
        if not PANDAS_AVAILABLE:
            raise ImportError("pandas is required for get_snapshots_dataframe")
        
        snapshots = self.get_snapshots(
            token_id=token_id,
            market_id=market_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        
        if not snapshots:
            return pd.DataFrame()
        
        data = []
        for snapshot in snapshots:
            data.append({
                "timestamp": snapshot.timestamp,
                "token_id": snapshot.token_id,
                "market_id": snapshot.market_id,
                "best_bid_price": snapshot.best_bid_price,
                "best_bid_size": snapshot.best_bid_size,
                "best_ask_price": snapshot.best_ask_price,
                "best_ask_size": snapshot.best_ask_size,
                "spread": snapshot.spread,
                "spread_bps": snapshot.spread_bps,
                "market_question": snapshot.market_question,
                "outcome": snapshot.outcome,
            })
        
        df = pd.DataFrame(data)
        if not df.empty:
            df = df.sort_values("timestamp")
        return df
    
    def get_spread_history(
        self,
        token_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ):
        """
        Get spread history for a token.
        
        Returns:
            DataFrame with timestamp and spread columns
        """
        if not PANDAS_AVAILABLE:
            raise ImportError("pandas is required for get_spread_history")
        
        df = self.get_snapshots_dataframe(
            token_id=token_id,
            start_time=start_time,
            end_time=end_time,
        )
        
        if df.empty:
            return pd.DataFrame()
        
        return df[["timestamp", "spread", "spread_bps", "best_bid_price", "best_ask_price"]]
    
    def get_orderbook_at_time(
        self,
        token_id: str,
        target_time: datetime,
        tolerance_seconds: int = 60,
    ) -> Optional[OrderbookSnapshot]:
        """
        Get the orderbook snapshot closest to a specific time.
        
        Args:
            token_id: Token ID
            target_time: Target timestamp
            tolerance_seconds: Maximum seconds difference to accept
            
        Returns:
            Closest OrderbookSnapshot or None
        """
        start_time = target_time - timedelta(seconds=tolerance_seconds)
        end_time = target_time + timedelta(seconds=tolerance_seconds)
        
        snapshots = self.get_snapshots(
            token_id=token_id,
            start_time=start_time,
            end_time=end_time,
            limit=1000,
        )
        
        if not snapshots:
            return None
        
        # Find closest snapshot
        closest = min(
            snapshots,
            key=lambda s: abs((s.timestamp - target_time).total_seconds())
        )
        
        if abs((closest.timestamp - target_time).total_seconds()) > tolerance_seconds:
            return None
        
        return closest
    
    def get_statistics(
        self,
        token_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Get statistics for a token over a time period."""
        return self.db.get_market_statistics(
            token_id=token_id,
            start_time=start_time,
            end_time=end_time,
        )
    
    def export_to_csv(
        self,
        output_path: str,
        token_id: Optional[str] = None,
        market_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100000,
    ):
        """Export orderbook snapshots to CSV."""
        if not PANDAS_AVAILABLE:
            raise ImportError("pandas is required for export_to_csv")
        
        df = self.get_snapshots_dataframe(
            token_id=token_id,
            market_id=market_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        
        if df.empty:
            print("No data to export")
            return
        
        df.to_csv(output_path, index=False)
        print(f"Exported {len(df)} snapshots to {output_path}")


def get_market_token_ids(market_id: str) -> List[str]:
    """
    Helper function to get token IDs for a market.
    
    Args:
        market_id: Polymarket market ID
        
    Returns:
        List of token IDs for the market
    """
    from agents.polymarket.polymarket import Polymarket
    
    polymarket = Polymarket()
    market = polymarket.get_market(market_id)
    
    if not market:
        return []
    
    # Parse clob_token_ids
    import ast
    try:
        token_ids = ast.literal_eval(market.clob_token_ids)
        return token_ids if isinstance(token_ids, list) else [token_ids]
    except:
        return []

