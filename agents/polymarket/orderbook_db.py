"""
Database models and utilities for storing orderbook snapshots.
"""
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, JSON, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

Base = declarative_base()


class OrderbookSnapshot(Base):
    """Database model for storing orderbook snapshots."""
    __tablename__ = "orderbook_snapshots"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    token_id = Column(String, nullable=False, index=True)
    market_id = Column(String, nullable=True, index=True)  # Polymarket market ID
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    # Best bid/ask
    best_bid_price = Column(Float, nullable=True)
    best_bid_size = Column(Float, nullable=True)
    best_ask_price = Column(Float, nullable=True)
    best_ask_size = Column(Float, nullable=True)
    
    # Spread metrics
    spread = Column(Float, nullable=True)
    spread_bps = Column(Float, nullable=True)  # Spread in basis points
    
    # Full orderbook data (stored as JSON)
    bids = Column(JSON, nullable=True)  # List of [price, size] tuples
    asks = Column(JSON, nullable=True)  # List of [price, size] tuples
    
    # Market metadata
    market_question = Column(String, nullable=True)
    outcome = Column(String, nullable=True)  # Which outcome this token represents
    
    # Additional metadata
    extra_metadata = Column(JSON, nullable=True)  # Store any additional data (renamed from 'metadata' - SQLAlchemy reserved)
    
    __table_args__ = (
        Index('idx_token_timestamp', 'token_id', 'timestamp'),
        Index('idx_market_timestamp', 'market_id', 'timestamp'),
    )


class OrderbookDatabase:
    """Database manager for orderbook snapshots."""
    
    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize database connection.
        
        Args:
            database_url: SQLAlchemy database URL. If None, checks DATABASE_URL env var,
                        then ORDERBOOK_DB_PATH, then defaults to SQLite at ./orderbook.db
        """
        if database_url is None:
            # Check for standard DATABASE_URL (Railway, Neon, etc.)
            database_url = os.getenv("DATABASE_URL")
            
            if database_url:
                # Handle Neon/PostgreSQL connection strings
                # Neon uses postgres:// but SQLAlchemy needs postgresql://
                if database_url.startswith("postgres://"):
                    database_url = database_url.replace("postgres://", "postgresql://", 1)
                # Log database type (but hide credentials)
                db_type = "PostgreSQL/Neon" if database_url.startswith("postgresql://") else "Unknown"
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"✓ Connecting to {db_type} database (from DATABASE_URL)")
            else:
                # Fall back to SQLite
                db_path = os.getenv("ORDERBOOK_DB_PATH", "./orderbook.db")
                database_url = f"sqlite:///{db_path}"
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"⚠ No DATABASE_URL found - using SQLite at {db_path}")
        
        # Configure engine based on database type
        if database_url.startswith("sqlite"):
            # SQLite-specific configuration
            self.engine = create_engine(
                database_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        else:
            # PostgreSQL/Neon - use connection pooling
            self.engine = create_engine(
                database_url,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,  # Verify connections before using
            )
        
        Base.metadata.create_all(self.engine)
        SessionLocal = sessionmaker(bind=self.engine)
        self.SessionLocal = SessionLocal
    
    def get_session(self) -> Session:
        """Get a database session."""
        return self.SessionLocal()
    
    def save_snapshot(
        self,
        token_id: str,
        bids: List[List[float]],
        asks: List[List[float]],
        market_id: Optional[str] = None,
        market_question: Optional[str] = None,
        outcome: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OrderbookSnapshot:
        """
        Save an orderbook snapshot to the database (synchronous).
        For async version, use save_snapshot_async().
        """
        """
        Save an orderbook snapshot to the database.
        
        Args:
            token_id: The CLOB token ID
            bids: List of [price, size] tuples for bids
            asks: List of [price, size] tuples for asks
            market_id: Optional Polymarket market ID
            market_question: Optional market question text
            outcome: Optional outcome name
            metadata: Optional additional metadata
            
        Returns:
            The created OrderbookSnapshot object
        """
        session = self.get_session()
        try:
            # Calculate best bid/ask
            best_bid_price = bids[0][0] if bids else None
            best_bid_size = bids[0][1] if bids else None
            best_ask_price = asks[0][0] if asks else None
            best_ask_size = asks[0][1] if asks else None
            
            # Calculate spread
            spread = None
            spread_bps = None
            if best_bid_price and best_ask_price:
                spread = best_ask_price - best_bid_price
                mid_price = (best_bid_price + best_ask_price) / 2
                if mid_price > 0:
                    spread_bps = (spread / mid_price) * 10000
            
            snapshot = OrderbookSnapshot(
                token_id=token_id,
                market_id=market_id,
                timestamp=datetime.utcnow(),
                best_bid_price=best_bid_price,
                best_bid_size=best_bid_size,
                best_ask_price=best_ask_price,
                best_ask_size=best_ask_size,
                spread=spread,
                spread_bps=spread_bps,
                bids=bids,
                asks=asks,
                market_question=market_question,
                outcome=outcome,
                extra_metadata=metadata,
            )
            
            session.add(snapshot)
            session.commit()
            session.refresh(snapshot)
            return snapshot
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    
    async def save_snapshot_async(
        self,
        token_id: str,
        bids: List[List[float]],
        asks: List[List[float]],
        market_id: Optional[str] = None,
        market_question: Optional[str] = None,
        outcome: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OrderbookSnapshot:
        """
        Save an orderbook snapshot asynchronously (non-blocking).
        Runs database write in thread pool to avoid blocking event loop.
        """
        import asyncio
        from functools import partial
        
        # Run synchronous save in thread pool
        loop = asyncio.get_event_loop()
        save_func = partial(
            self.save_snapshot,
            token_id=token_id,
            bids=bids,
            asks=asks,
            market_id=market_id,
            market_question=market_question,
            outcome=outcome,
            metadata=metadata,
        )
        return await loop.run_in_executor(None, save_func)
    
    def get_snapshots(
        self,
        token_id: Optional[str] = None,
        market_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[OrderbookSnapshot]:
        """
        Query historical orderbook snapshots.
        
        Args:
            token_id: Filter by token ID
            market_id: Filter by market ID
            start_time: Start of time range
            end_time: End of time range
            limit: Maximum number of results
            
        Returns:
            List of OrderbookSnapshot objects
        """
        session = self.get_session()
        try:
            query = session.query(OrderbookSnapshot)
            
            if token_id:
                query = query.filter(OrderbookSnapshot.token_id == token_id)
            if market_id:
                query = query.filter(OrderbookSnapshot.market_id == market_id)
            if start_time:
                query = query.filter(OrderbookSnapshot.timestamp >= start_time)
            if end_time:
                query = query.filter(OrderbookSnapshot.timestamp <= end_time)
            
            query = query.order_by(OrderbookSnapshot.timestamp.desc())
            query = query.limit(limit)
            
            return query.all()
        finally:
            session.close()
    
    def get_latest_snapshot(
        self,
        token_id: Optional[str] = None,
        market_id: Optional[str] = None,
    ) -> Optional[OrderbookSnapshot]:
        """Get the most recent snapshot for a token or market."""
        snapshots = self.get_snapshots(token_id=token_id, market_id=market_id, limit=1)
        return snapshots[0] if snapshots else None
    
    def get_market_statistics(
        self,
        token_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Calculate statistics for a market over a time period.
        
        Returns:
            Dictionary with statistics like min/max spread, avg spread, etc.
        """
        snapshots = self.get_snapshots(
            token_id=token_id,
            start_time=start_time,
            end_time=end_time,
            limit=100000,  # Large limit for statistics
        )
        
        if not snapshots:
            return {}
        
        spreads = [s.spread for s in snapshots if s.spread is not None]
        spread_bps = [s.spread_bps for s in snapshots if s.spread_bps is not None]
        
        return {
            "count": len(snapshots),
            "min_spread": min(spreads) if spreads else None,
            "max_spread": max(spreads) if spreads else None,
            "avg_spread": sum(spreads) / len(spreads) if spreads else None,
            "min_spread_bps": min(spread_bps) if spread_bps else None,
            "max_spread_bps": max(spread_bps) if spread_bps else None,
            "avg_spread_bps": sum(spread_bps) / len(spread_bps) if spread_bps else None,
            "first_timestamp": snapshots[-1].timestamp,
            "last_timestamp": snapshots[0].timestamp,
        }

