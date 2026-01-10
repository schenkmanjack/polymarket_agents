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
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Boolean, Index, text, inspect
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
    
    # Sell order information (for claiming proceeds)
    sell_order_id = Column(String, nullable=True, index=True)
    sell_order_price = Column(Float, nullable=True)  # Usually 0.99
    sell_order_size = Column(Float, nullable=True)  # Number of shares to sell
    sell_order_status = Column(String, nullable=True)  # 'open', 'filled', 'cancelled', 'partial'
    sell_order_placed_at = Column(DateTime, nullable=True)
    sell_order_filled_at = Column(DateTime, nullable=True)
    sell_dollars_received = Column(Float, nullable=True)  # Amount received from selling
    sell_fee = Column(Float, nullable=True)  # Fee paid on sell
    
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
        
        # Migrate existing table to add new columns if they don't exist
        self._migrate_table()
        
        SessionLocal = sessionmaker(bind=self.engine)
        self.SessionLocal = SessionLocal
    
    def _migrate_table(self):
        """Add missing columns to real_trades_threshold table if they don't exist (migration)."""
        try:
            inspector = inspect(self.engine)
            table_name = "real_trades_threshold"
            
            if table_name not in inspector.get_table_names():
                logger.debug(f"Table {table_name} doesn't exist yet, will be created with all columns")
                return
            
            # Get existing columns
            existing_columns = [col['name'] for col in inspector.get_columns(table_name)]
            columns_to_add = []
            
            # Check which sell order columns are missing
            sell_order_columns = {
                'sell_order_id': 'VARCHAR',
                'sell_order_price': 'FLOAT',
                'sell_order_size': 'FLOAT',
                'sell_order_status': 'VARCHAR',
                'sell_order_placed_at': 'TIMESTAMP',
                'sell_order_filled_at': 'TIMESTAMP',
                'sell_dollars_received': 'FLOAT',
                'sell_fee': 'FLOAT',
            }
            
            for col_name, col_type in sell_order_columns.items():
                if col_name not in existing_columns:
                    columns_to_add.append((col_name, col_type))
            
            # Add missing columns
            if columns_to_add:
                logger.info(f"Migrating {table_name}: adding {len(columns_to_add)} missing columns")
                # Use a single transaction for all column additions
                conn = self.engine.connect()
                trans = conn.begin()
                try:
                    for col_name, col_type in columns_to_add:
                        try:
                            if 'postgresql' in str(self.engine.url).lower():
                                # PostgreSQL
                                if col_type == 'VARCHAR':
                                    sql_type = 'VARCHAR'
                                elif col_type == 'FLOAT':
                                    sql_type = 'DOUBLE PRECISION'
                                elif col_type == 'TIMESTAMP':
                                    sql_type = 'TIMESTAMP'
                                else:
                                    sql_type = 'VARCHAR'
                                
                                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {sql_type}"))
                            else:
                                # SQLite
                                if col_type == 'VARCHAR':
                                    sql_type = 'TEXT'
                                elif col_type == 'FLOAT':
                                    sql_type = 'REAL'
                                elif col_type == 'TIMESTAMP':
                                    sql_type = 'TIMESTAMP'
                                else:
                                    sql_type = 'TEXT'
                                
                                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {sql_type}"))
                            
                            logger.info(f"  ✓ Added column {col_name}")
                        except Exception as e:
                            error_str = str(e).lower()
                            # Column might already exist (race condition) - that's okay
                            if 'already exists' not in error_str and 'duplicate' not in error_str:
                                logger.warning(f"  ⚠ Could not add column {col_name}: {e}")
                                # Don't rollback for individual column errors - continue with others
                    
                    # Commit all changes
                    trans.commit()
                    logger.info(f"✓ Migration complete for {table_name} - added {len(columns_to_add)} columns")
                except Exception as e:
                    trans.rollback()
                    logger.error(f"Migration failed, rolling back: {e}", exc_info=True)
                    raise  # Re-raise to ensure we know migration failed
                finally:
                    conn.close()
            else:
                logger.debug(f"Table {table_name} already has all required columns")
        except Exception as e:
            logger.error(f"Error during table migration: {e}", exc_info=True)
            # Re-raise the exception - we need migration to succeed before proceeding
            raise
    
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
    
    def update_sell_order(
        self,
        trade_id: int,
        sell_order_id: str,
        sell_order_price: float,
        sell_order_size: float,
        sell_order_status: str = "open",
    ):
        """Update trade with sell order information."""
        session = self.SessionLocal()
        try:
            trade = session.query(RealTradeThreshold).filter_by(id=trade_id).first()
            if trade:
                trade.sell_order_id = sell_order_id
                trade.sell_order_price = sell_order_price
                trade.sell_order_size = sell_order_size
                trade.sell_order_status = sell_order_status
                trade.sell_order_placed_at = datetime.now(timezone.utc)
                session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating sell order: {e}")
            raise
        finally:
            session.close()
    
    def update_sell_order_fill(
        self,
        trade_id: int,
        sell_order_status: str,
        sell_dollars_received: Optional[float] = None,
        sell_fee: Optional[float] = None,
    ):
        """Update sell order with fill information."""
        session = self.SessionLocal()
        try:
            trade = session.query(RealTradeThreshold).filter_by(id=trade_id).first()
            if trade:
                trade.sell_order_status = sell_order_status
                if sell_dollars_received is not None:
                    trade.sell_dollars_received = sell_dollars_received
                if sell_fee is not None:
                    trade.sell_fee = sell_fee
                if sell_order_status == "filled":
                    trade.sell_order_filled_at = datetime.now(timezone.utc)
                session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating sell order fill: {e}")
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
    
    def get_open_trades(self, deployment_id: Optional[str] = None) -> List[RealTradeThreshold]:
        """
        Get all trades with open buy orders.
        
        Args:
            deployment_id: Optional deployment ID to filter by. If provided, only returns
                          trades from that deployment. If None, returns all open trades.
        """
        session = self.SessionLocal()
        try:
            query = session.query(RealTradeThreshold).filter(
                RealTradeThreshold.order_status.in_(["open", "partial"])
            )
            
            # Filter by deployment_id if provided
            if deployment_id is not None:
                query = query.filter(RealTradeThreshold.deployment_id == deployment_id)
            
            return query.all()
        finally:
            session.close()
    
    def get_open_sell_orders(self, deployment_id: Optional[str] = None) -> List[RealTradeThreshold]:
        """
        Get all trades with open sell orders.
        
        Args:
            deployment_id: Optional deployment ID to filter by. If provided, only returns
                          trades from that deployment. If None, returns all open sell orders.
        """
        session = self.SessionLocal()
        try:
            query = session.query(RealTradeThreshold).filter(
                RealTradeThreshold.sell_order_status.in_(["open", "partial"]),
                RealTradeThreshold.sell_order_id.isnot(None),
            )
            
            # Filter by deployment_id if provided
            if deployment_id is not None:
                query = query.filter(RealTradeThreshold.deployment_id == deployment_id)
            
            return query.all()
        finally:
            session.close()
    
    def get_unresolved_trades(self, deployment_id: Optional[str] = None) -> List[RealTradeThreshold]:
        """
        Get all trades where market hasn't resolved yet.
        
        Args:
            deployment_id: Optional deployment ID to filter by. If provided, only returns
                          trades from that deployment. If None, returns all unresolved trades.
        
        Only returns trades that:
        - Have order_id (order was placed)
        - Are not cancelled or failed
        - Have been filled (filled_shares > 0) or are still open/partial
        - Match deployment_id if provided
        """
        session = self.SessionLocal()
        try:
            query = session.query(RealTradeThreshold).filter(
                RealTradeThreshold.market_resolved_at.is_(None),
                RealTradeThreshold.order_id.isnot(None),  # Must have order_id
                RealTradeThreshold.order_status.notin_(["cancelled", "failed"]),  # Exclude cancelled/failed
            )
            
            # Filter by deployment_id if provided
            if deployment_id is not None:
                query = query.filter(RealTradeThreshold.deployment_id == deployment_id)
            
            return query.all()
        finally:
            session.close()
    
    def get_latest_principal(self, deployment_id: Optional[str] = None) -> Optional[float]:
        """
        Get the latest principal from the most recent resolved trade.
        
        Args:
            deployment_id: Optional deployment ID to filter by. If None, returns principal from any deployment.
                          If provided, only returns principal from trades with matching deployment_id.
        
        Only considers trades that:
        - Have principal_after set (trade resolved)
        - Have order_id set (order was successfully placed)
        - Have market_resolved_at set (market actually resolved)
        - Have a valid order_status (not 'failed')
        - Have principal_after > 0 (positive principal only)
        - Match deployment_id if provided
        
        This filters out test entries, failed orders, and invalid negative principals.
        """
        session = self.SessionLocal()
        try:
            query = session.query(RealTradeThreshold).filter(
                RealTradeThreshold.principal_after.isnot(None),
                RealTradeThreshold.principal_after > 0,  # Only positive principals
                RealTradeThreshold.order_id.isnot(None),  # Must have order_id (order was placed)
                RealTradeThreshold.market_resolved_at.isnot(None),  # Market must be resolved
                RealTradeThreshold.order_status != "failed",  # Exclude failed orders
            )
            
            # Filter by deployment_id if provided
            if deployment_id is not None:
                query = query.filter(RealTradeThreshold.deployment_id == deployment_id)
            
            trade = query.order_by(RealTradeThreshold.market_resolved_at.desc()).first()
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
    
    def get_resolved_trades(self, deployment_id: Optional[str] = None) -> List[RealTradeThreshold]:
        """
        Get all resolved trades.
        
        Args:
            deployment_id: Optional deployment ID to filter by. If provided, only returns
                          trades from that deployment. If None, returns all resolved trades.
        """
        session = self.SessionLocal()
        try:
            query = session.query(RealTradeThreshold).filter(
                RealTradeThreshold.market_resolved_at.isnot(None),
                RealTradeThreshold.is_win == True,  # Only winning trades
                RealTradeThreshold.outcome_price.isnot(None),
            )
            
            # Filter by deployment_id if provided
            if deployment_id is not None:
                query = query.filter(RealTradeThreshold.deployment_id == deployment_id)
            
            return query.all()
        finally:
            session.close()
    
    def get_most_recent_filled_trade_without_sell(self, deployment_id: Optional[str] = None) -> Optional[RealTradeThreshold]:
        """
        Get the most recent filled trade that doesn't have a sell order yet (for early sell checking).
        
        Args:
            deployment_id: Optional deployment ID to filter by. If provided, only returns
                          trade from that deployment. If None, returns most recent from any deployment.
        
        Returns:
            Most recent filled trade without sell order, or None if not found.
        """
        session = self.SessionLocal()
        try:
            query = session.query(RealTradeThreshold).filter(
                RealTradeThreshold.order_status == "filled",  # Buy order must be filled
                RealTradeThreshold.filled_shares.isnot(None),
                RealTradeThreshold.filled_shares > 0,  # Must have filled shares
                RealTradeThreshold.sell_order_id.is_(None),  # No sell order yet
            )
            
            # Filter by deployment_id if provided
            if deployment_id is not None:
                query = query.filter(RealTradeThreshold.deployment_id == deployment_id)
            
            # Order by order_placed_at descending and get the first (most recent)
            trade = query.order_by(RealTradeThreshold.order_placed_at.desc()).first()
            return trade
        finally:
            session.close()
    
    def get_trades_by_deployment(self, deployment_id: str) -> List[RealTradeThreshold]:
        """Get all trades from a specific deployment."""
        session = self.SessionLocal()
        try:
            return session.query(RealTradeThreshold).filter_by(deployment_id=deployment_id).all()
        finally:
            session.close()
