"""
Database models and utilities for storing live trading data.

This module provides:
- Trade database model (real_trades_threshold table)
- Database helper functions for CRUD operations
- Principal tracking
"""
import os
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Boolean, Index, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

Base = declarative_base()

logger = logging.getLogger(__name__)


class RealTradeThreshold(Base):
    """Database model for storing threshold strategy trades."""
    __tablename__ = "real_trades_threshold"
    
    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Deployment tracking
    deployment_id = Column(String, nullable=False, index=True)  # UUID generated at script startup
    
    # Config parameters
    threshold = Column(Float, nullable=False)
    margin = Column(Float, nullable=False)
    kelly_fraction = Column(Float, nullable=False)
    kelly_scale_factor = Column(Float, nullable=False)
    market_type = Column(String, nullable=False)  # '15m' or '1h'
    
    # Market information
    market_id = Column(String, nullable=False, index=True)
    market_slug = Column(String, nullable=False, index=True)
    token_id = Column(String, nullable=False)  # YES or NO token ID
    winning_side = Column(String, nullable=True)  # 'YES' or 'NO' after resolution
    
    # Order information
    order_id = Column(String, nullable=True, index=True)
    order_price = Column(Float, nullable=False)
    order_size = Column(Float, nullable=False)  # Number of shares
    order_side = Column(String, nullable=False)  # 'YES' or 'NO'
    order_status = Column(String, nullable=True)  # 'open', 'filled', 'cancelled', 'partial'
    
    # Fill information
    filled_shares = Column(Float, nullable=True)  # May be less than order_size if partial fill
    fill_price = Column(Float, nullable=True)  # Weighted average fill price
    dollars_spent = Column(Float, nullable=True)
    fee = Column(Float, nullable=True)
    
    # Outcome information
    outcome_price = Column(Float, nullable=True)  # Final outcome price (0.0 or 1.0)
    payout = Column(Float, nullable=True)  # Total payout received
    net_payout = Column(Float, nullable=True)  # payout - dollars_spent - fee
    roi = Column(Float, nullable=True)  # Return on investment
    is_win = Column(Boolean, nullable=True)  # True if roi > 0
    
    # Principal tracking
    principal_before = Column(Float, nullable=False)
    principal_after = Column(Float, nullable=True)  # Updated after market resolution
    
    # Timestamps
    order_placed_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    order_filled_at = Column(DateTime, nullable=True)
    market_resolved_at = Column(DateTime, nullable=True)
    
    # Error tracking
    error_message = Column(String, nullable=True)  # If order placement or resolution failed
    
    __table_args__ = (
        Index('idx_market_slug', 'market_slug'),
        Index('idx_order_id', 'order_id'),
        Index('idx_deployment_id', 'deployment_id'),
        Index('idx_order_status', 'order_status'),
    )


class TradeDatabase:
    """Database manager for live trading data."""
    
    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize database connection.
        
        Args:
            database_url: SQLAlchemy database URL. If None, uses DATABASE_URL from env.
        """
        if database_url is None:
            database_url = os.getenv("DATABASE_URL")
            
            if database_url:
                # Handle Neon/PostgreSQL connection strings
                if database_url.startswith("postgres://"):
                    database_url = database_url.replace("postgres://", "postgresql://", 1)
                db_type = "PostgreSQL" if database_url.startswith("postgresql://") else "Unknown"
                logger.info(f"✓ Connecting to {db_type} database for trading data")
            else:
                # Fall back to SQLite
                db_path = os.getenv("TRADE_DB_PATH", "./trades.db")
                database_url = f"sqlite:///{db_path}"
                logger.warning(f"⚠ No DATABASE_URL found - using SQLite at {db_path}")
        
        # Configure engine based on database type
        if database_url.startswith("sqlite"):
            self.engine = create_engine(
                database_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        else:
            # PostgreSQL - use connection pooling
            self.engine = create_engine(
                database_url,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
            )
        
        # Create tables
        Base.metadata.create_all(self.engine)
        SessionLocal = sessionmaker(bind=self.engine)
        self.SessionLocal = SessionLocal
    
    def create_trade(
        self,
        deployment_id: str,
        threshold: float,
        margin: float,
        kelly_fraction: float,
        kelly_scale_factor: float,
        market_type: str,
        market_id: str,
        market_slug: str,
        token_id: str,
        order_id: Optional[str],
        order_price: float,
        order_size: float,
        order_side: str,
        principal_before: float,
        order_status: str = "open",
        error_message: Optional[str] = None,
    ) -> int:
        """
        Create a new trade record.
        
        Returns:
            Trade ID
        """
        session = self.SessionLocal()
        try:
            trade = RealTradeThreshold(
                deployment_id=deployment_id,
                threshold=threshold,
                margin=margin,
                kelly_fraction=kelly_fraction,
                kelly_scale_factor=kelly_scale_factor,
                market_type=market_type,
                market_id=market_id,
                market_slug=market_slug,
                token_id=token_id,
                order_id=order_id,
                order_price=order_price,
                order_size=order_size,
                order_side=order_side,
                order_status=order_status,
                principal_before=principal_before,
                error_message=error_message,
            )
            session.add(trade)
            session.commit()
            trade_id = trade.id
            session.refresh(trade)
            return trade_id
        except Exception as e:
            session.rollback()
            logger.error(f"Error creating trade: {e}")
            raise
        finally:
            session.close()
    
    def update_trade_fill(
        self,
        trade_id: int,
        filled_shares: float,
        fill_price: float,
        dollars_spent: float,
        fee: float,
        order_status: str = "filled",
    ):
        """Update trade with fill information."""
        session = self.SessionLocal()
        try:
            trade = session.query(RealTradeThreshold).filter_by(id=trade_id).first()
            if trade:
                trade.filled_shares = filled_shares
                trade.fill_price = fill_price
                trade.dollars_spent = dollars_spent
                trade.fee = fee
                trade.order_status = order_status
                trade.order_filled_at = datetime.now(timezone.utc)
                session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating trade fill: {e}")
            raise
        finally:
            session.close()
    
    def update_trade_outcome(
        self,
        trade_id: int,
        outcome_price: float,
        payout: float,
        net_payout: float,
        roi: float,
        is_win: bool,
        principal_after: float,
        winning_side: Optional[str] = None,
    ):
        """Update trade with outcome information."""
        session = self.SessionLocal()
        try:
            trade = session.query(RealTradeThreshold).filter_by(id=trade_id).first()
            if trade:
                trade.outcome_price = outcome_price
                trade.payout = payout
                trade.net_payout = net_payout
                trade.roi = roi
                trade.is_win = is_win
                trade.principal_after = principal_after
                trade.market_resolved_at = datetime.now(timezone.utc)
                if winning_side:
                    trade.winning_side = winning_side
                session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating trade outcome: {e}")
            raise
        finally:
            session.close()
    
    def update_order_status(
        self,
        trade_id: int,
        order_status: str,
        order_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        """Update order status."""
        session = self.SessionLocal()
        try:
            trade = session.query(RealTradeThreshold).filter_by(id=trade_id).first()
            if trade:
                trade.order_status = order_status
                if order_id:
                    trade.order_id = order_id
                if error_message:
                    trade.error_message = error_message
                session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating order status: {e}")
            raise
        finally:
            session.close()
    
    def get_trade_by_id(self, trade_id: int) -> Optional[RealTradeThreshold]:
        """Get trade by ID."""
        session = self.SessionLocal()
        try:
            return session.query(RealTradeThreshold).filter_by(id=trade_id).first()
        finally:
            session.close()
    
    def get_trades_by_market_slug(self, market_slug: str) -> List[RealTradeThreshold]:
        """Get all trades for a market slug."""
        session = self.SessionLocal()
        try:
            return session.query(RealTradeThreshold).filter_by(market_slug=market_slug).all()
        finally:
            session.close()
    
    def get_open_trades(self) -> List[RealTradeThreshold]:
        """Get all trades with open orders."""
        session = self.SessionLocal()
        try:
            return session.query(RealTradeThreshold).filter(
                RealTradeThreshold.order_status.in_(["open", "partial"])
            ).all()
        finally:
            session.close()
    
    def get_unresolved_trades(self) -> List[RealTradeThreshold]:
        """
        Get all trades where market hasn't resolved yet.
        
        Only returns trades that:
        - Have order_id (order was placed)
        - Are not cancelled or failed
        - Have been filled (filled_shares > 0) or are still open/partial
        """
        session = self.SessionLocal()
        try:
            return session.query(RealTradeThreshold).filter(
                RealTradeThreshold.market_resolved_at.is_(None),
                RealTradeThreshold.order_id.isnot(None),  # Must have order_id
                RealTradeThreshold.order_status.notin_(["cancelled", "failed"]),  # Exclude cancelled/failed
            ).all()
        finally:
            session.close()
    
    def get_latest_principal(self) -> Optional[float]:
        """
        Get the latest principal from the most recent resolved trade.
        
        Only considers trades that:
        - Have principal_after set (trade resolved)
        - Have order_id set (order was successfully placed)
        - Have market_resolved_at set (market actually resolved)
        - Have a valid order_status (not 'failed')
        - Have principal_after > 0 (positive principal only)
        
        This filters out test entries, failed orders, and invalid negative principals.
        """
        session = self.SessionLocal()
        try:
            trade = session.query(RealTradeThreshold).filter(
                RealTradeThreshold.principal_after.isnot(None),
                RealTradeThreshold.principal_after > 0,  # Only positive principals
                RealTradeThreshold.order_id.isnot(None),  # Must have order_id (order was placed)
                RealTradeThreshold.market_resolved_at.isnot(None),  # Market must be resolved
                RealTradeThreshold.order_status != "failed",  # Exclude failed orders
            ).order_by(RealTradeThreshold.market_resolved_at.desc()).first()
            if trade:
                return trade.principal_after
            return None
        finally:
            session.close()
    
    def has_bet_on_market(self, market_slug: str) -> bool:
        """Check if we've already bet on this market."""
        session = self.SessionLocal()
        try:
            count = session.query(RealTradeThreshold).filter_by(market_slug=market_slug).count()
            return count > 0
        finally:
            session.close()
    
    def get_trades_by_deployment(self, deployment_id: str) -> List[RealTradeThreshold]:
        """Get all trades from a specific deployment."""
        session = self.SessionLocal()
        try:
            return session.query(RealTradeThreshold).filter_by(deployment_id=deployment_id).all()
        finally:
            session.close()
