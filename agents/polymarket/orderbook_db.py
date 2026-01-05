"""
Database models and utilities for storing orderbook snapshots.
"""
import os
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, JSON, Index, text, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

Base = declarative_base()

# Lock for table creation to prevent race conditions
_table_creation_locks = {}
_table_creation_lock = threading.Lock()


class OrderbookSnapshot(Base):
    """Database model for storing orderbook snapshots."""
    __tablename__ = "orderbook_snapshots"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    token_id = Column(String, nullable=False, index=True)
    market_id = Column(String, nullable=True, index=True)  # Polymarket market ID
    timestamp = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    
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


class BTCEthOrderbookSnapshot(Base):
    """Single table for all BTC and ETH 15-minute market orderbook snapshots."""
    __tablename__ = "btc_eth_table"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    token_id = Column(String, nullable=False, index=True)
    market_id = Column(String, nullable=False, index=True)  # Polymarket market ID (required)
    timestamp = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    
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
    asset_type = Column(String, nullable=True)  # 'BTC' or 'ETH'
    
    # Market timing (for time-decay strategies and backtesting)
    market_start_date = Column(DateTime, nullable=True)  # When market starts (UTC timezone-aware)
    market_end_date = Column(DateTime, nullable=True, index=True)  # When market resolves (UTC timezone-aware)
    time_remaining_seconds = Column(Float, nullable=True, index=True)  # Calculated: end_date - timestamp
    
    # Additional metadata
    extra_metadata = Column(JSON, nullable=True)
    
    __table_args__ = (
        Index('idx_btc_eth_token_timestamp', 'token_id', 'timestamp'),
        Index('idx_btc_eth_market_timestamp', 'market_id', 'timestamp'),
        Index('idx_btc_eth_asset_timestamp', 'asset_type', 'timestamp'),
        Index('idx_btc_eth_time_remaining', 'time_remaining_seconds'),
    )


class OrderbookDatabase:
    """Database manager for orderbook snapshots."""
    
    def __init__(self, database_url: Optional[str] = None, per_market_tables: bool = False, use_btc_eth_table: bool = False):
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
        
        # Create base table
        Base.metadata.create_all(self.engine)
        SessionLocal = sessionmaker(bind=self.engine)
        self.SessionLocal = SessionLocal
        
        # Track created per-market tables (cache table classes to avoid recreating)
        self._created_tables = set()  # Track table names
        self._table_class_cache = {}  # Cache table_name -> table_class mapping
        self.per_market_tables = per_market_tables
        self.use_btc_eth_table = use_btc_eth_table  # Use single btc_eth_table instead
        
        # Create btc_eth_table if requested
        if self.use_btc_eth_table:
            BTCEthOrderbookSnapshot.__table__.create(self.engine, checkfirst=True)
            # Migrate existing table to add new columns if they don't exist
            self._migrate_btc_eth_table()
        # Per-table locks for creation (prevents race conditions)
        self._table_locks = {}
    
    def _migrate_btc_eth_table(self):
        """Add missing columns to btc_eth_table if they don't exist (migration)."""
        from sqlalchemy import inspect
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            inspector = inspect(self.engine)
            table_name = "btc_eth_table"
            
            if table_name not in inspector.get_table_names():
                logger.debug(f"Table {table_name} doesn't exist yet, will be created with all columns")
                return
            
            # Get existing columns
            existing_columns = [col['name'] for col in inspector.get_columns(table_name)]
            columns_to_add = []
            
            # Check which columns are missing
            if 'market_start_date' not in existing_columns:
                columns_to_add.append('market_start_date')
            if 'market_end_date' not in existing_columns:
                columns_to_add.append('market_end_date')
            if 'time_remaining_seconds' not in existing_columns:
                columns_to_add.append('time_remaining_seconds')
            
            # Add missing columns
            if columns_to_add:
                logger.info(f"Migrating {table_name}: Adding {len(columns_to_add)} missing column(s)...")
                with self.engine.begin() as conn:
                    for col_name in columns_to_add:
                        try:
                            # Determine column type
                            if col_name in ['market_start_date', 'market_end_date']:
                                # PostgreSQL uses TIMESTAMP, SQLite uses DATETIME
                                if 'postgresql' in str(self.engine.url).lower():
                                    conn.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {col_name} TIMESTAMP'))
                                else:
                                    conn.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {col_name} DATETIME'))
                            elif col_name == 'time_remaining_seconds':
                                # PostgreSQL uses DOUBLE PRECISION, SQLite uses REAL
                                if 'postgresql' in str(self.engine.url).lower():
                                    conn.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {col_name} DOUBLE PRECISION'))
                                else:
                                    conn.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {col_name} REAL'))
                            
                            logger.info(f"  ✓ Added column: {col_name}")
                        except Exception as e:
                            error_str = str(e).lower()
                            if 'duplicate' in error_str or 'already exists' in error_str or 'duplicate column' in error_str:
                                logger.debug(f"  Column {col_name} already exists (skipping)")
                            else:
                                logger.warning(f"  Failed to add column {col_name}: {e}")
                
                logger.info(f"✓ Migration complete for {table_name}")
            else:
                logger.debug(f"Table {table_name} already has all required columns")
        except Exception as e:
            logger.warning(f"Migration check failed (non-critical): {e}")
            # Don't raise - allow script to continue
    
    def get_session(self) -> Session:
        """Get a database session."""
        return self.SessionLocal()
    
    def _get_table_for_market(self, market_id: Optional[str]):
        """
        Get or create table for a specific market.
        If per_market_tables is False, returns base OrderbookSnapshot.
        If per_market_tables is True, creates/returns market-specific table.
        """
        if not self.per_market_tables or not market_id:
            return OrderbookSnapshot
        
        # Create market-specific table name (sanitize for SQL)
        table_name = f"orderbook_snapshots_market_{market_id}"
        
        # Check cache first (fastest)
        if table_name in self._table_class_cache:
            return self._table_class_cache[table_name]
        
        # Check if table class already exists in Base registry (from previous run or concurrent access)
        for mapper in Base.registry.mappers:
            if hasattr(mapper.class_, '__tablename__') and mapper.class_.__tablename__ == table_name:
                # Cache it for next time
                self._table_class_cache[table_name] = mapper.class_
                self._created_tables.add(table_name)
                return mapper.class_
        
        # Create new table class for this market
        # Use a unique class name to avoid conflicts
        class_name = f"OrderbookSnapshot_{market_id}_{id(self)}"  # Add instance ID for uniqueness
        
        # Check one more time if table was created by another thread/process
        for mapper in Base.registry.mappers:
            if hasattr(mapper.class_, '__tablename__') and mapper.class_.__tablename__ == table_name:
                self._table_class_cache[table_name] = mapper.class_
                self._created_tables.add(table_name)
                return mapper.class_
        
        try:
            market_table = type(
                class_name,
                (Base,),
                {
                    "__tablename__": table_name,
                    "__table_args__": (
                        Index('idx_token_timestamp', 'token_id', 'timestamp'),
                        Index('idx_market_timestamp', 'market_id', 'timestamp'),
                    ),
                    "id": Column(Integer, primary_key=True, autoincrement=True),
                    "token_id": Column(String, nullable=False, index=True),
                    "market_id": Column(String, nullable=True, index=True),
                    "timestamp": Column(DateTime, nullable=False, default=datetime.utcnow, index=True),
                    "best_bid_price": Column(Float, nullable=True),
                    "best_bid_size": Column(Float, nullable=True),
                    "best_ask_price": Column(Float, nullable=True),
                    "best_ask_size": Column(Float, nullable=True),
                    "spread": Column(Float, nullable=True),
                    "spread_bps": Column(Float, nullable=True),
                    "bids": Column(JSON, nullable=True),
                    "asks": Column(JSON, nullable=True),
                    "market_question": Column(String, nullable=True),
                    "outcome": Column(String, nullable=True),
                    "extra_metadata": Column(JSON, nullable=True),
                }
            )
            
            # Create table in database (checkfirst=True means it won't error if exists)
            # Use bind=self.engine to ensure it's created synchronously
            try:
                # First check if table already exists
                from sqlalchemy import inspect
                inspector = inspect(self.engine)
                if table_name in inspector.get_table_names():
                    # Table exists, skip creation
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.debug(f"Table {table_name} already exists in database")
                else:
                    # Create table - SQLAlchemy will handle indexes
                    market_table.__table__.create(bind=self.engine, checkfirst=True)
            except Exception as e:
                # Handle different types of errors
                import logging
                logger = logging.getLogger(__name__)
                error_str = str(e).lower()
                
                # If it's a duplicate index/table error, that's okay - table exists
                if "duplicate" in error_str or "already exists" in error_str:
                    # Table/index exists, that's fine
                    logger.debug(f"Table {table_name} or indexes already exist: {error_str[:100]}")
                else:
                    # Check if table exists in DB before re-raising
                    from sqlalchemy import inspect
                    inspector = inspect(self.engine)
                    if table_name in inspector.get_table_names():
                        # Table exists in DB, just cache the class
                        logger.debug(f"Table {table_name} exists in database despite error")
                    else:
                        # Re-raise if it's a different error
                        logger.error(f"Error creating table {table_name}: {e}")
                        raise
            
            # Cache the table class
            self._table_class_cache[table_name] = market_table
            self._created_tables.add(table_name)
            
            return market_table
        except Exception as e:
            # If table already exists in metadata, try to find it
            if "already defined" in str(e) or "already exists" in str(e).lower():
                # Look for existing table in registry
                for mapper in Base.registry.mappers:
                    if hasattr(mapper.class_, '__tablename__') and mapper.class_.__tablename__ == table_name:
                        self._table_class_cache[table_name] = mapper.class_
                        self._created_tables.add(table_name)
                        return mapper.class_
            raise
    
    def save_snapshot(
        self,
        token_id: str,
        bids: List[List[float]],
        asks: List[List[float]],
        market_id: Optional[str] = None,
        market_question: Optional[str] = None,
        outcome: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        market_start_date: Optional[datetime] = None,
        market_end_date: Optional[datetime] = None,
        asset_type: Optional[str] = None,
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
            # Determine asset type (for btc_eth_table)
            asset_type = None
            if market_question:
                question_lower = market_question.lower()
                if "bitcoin" in question_lower or "btc" in question_lower:
                    asset_type = "BTC"
                elif "ethereum" in question_lower or "eth" in question_lower:
                    asset_type = "ETH"
            
            # Use btc_eth_table if enabled (simpler, no dynamic table creation)
            if self.use_btc_eth_table:
                SnapshotTable = BTCEthOrderbookSnapshot
            else:
                # Get appropriate table (base or market-specific)
                # This ensures table exists before we try to insert
                SnapshotTable = self._get_table_for_market(market_id)
            
            # CRITICAL: Ensure table exists in database before inserting
            # This is especially important for per-market tables that might be created concurrently
            if self.per_market_tables and market_id and not self.use_btc_eth_table:
                table_name = f"orderbook_snapshots_market_{market_id}"
                from sqlalchemy import inspect
                import logging
                logger = logging.getLogger(__name__)
                
                # Get or create a lock for this specific table (prevents concurrent creation)
                with _table_creation_lock:
                    if table_name not in _table_creation_locks:
                        _table_creation_locks[table_name] = threading.Lock()
                    table_lock = _table_creation_locks[table_name]
                
                # Use table-specific lock to prevent concurrent creation attempts
                with table_lock:
                    inspector = inspect(self.engine)
                    
                    # Always verify table exists before inserting (handles race conditions)
                    if table_name not in inspector.get_table_names():
                        # Table doesn't exist - create it now synchronously
                        logger.warning(f"Table {table_name} does not exist, creating it now...")
                        try:
                            # Create table using engine.begin() for atomic transaction
                            with self.engine.begin() as conn:
                                SnapshotTable.__table__.create(bind=conn, checkfirst=True)
                            
                            # Verify it was created (refresh inspector)
                            inspector = inspect(self.engine)
                            if table_name not in inspector.get_table_names():
                                # Double-check - maybe it was created by another process
                                import time
                                time.sleep(0.1)  # Brief wait for DB to sync
                                inspector = inspect(self.engine)
                                if table_name not in inspector.get_table_names():
                                    raise Exception(f"Failed to create table {table_name} - table still does not exist after creation")
                            logger.info(f"✓ Created table {table_name}")
                        except Exception as e:
                            error_str = str(e).lower()
                            
                            # Check if table exists now (might have been created by another process)
                            inspector = inspect(self.engine)
                            if table_name in inspector.get_table_names():
                                # Table exists now, that's fine
                                logger.debug(f"Table {table_name} exists (created by another process)")
                            elif "duplicate" in error_str or "already exists" in error_str:
                                # Table/index exists, that's fine
                                logger.debug(f"Table {table_name} or indexes already exist")
                            else:
                                # Real error - re-raise
                                logger.error(f"Failed to create table {table_name}: {e}")
                                raise
                    else:
                        # Table exists, proceed
                        logger.debug(f"Table {table_name} exists, proceeding with insert")
            
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
            
            # Get current timestamp (timezone-aware UTC)
            current_timestamp = datetime.now(timezone.utc)
            
            # Calculate time remaining if end_date is provided
            time_remaining_seconds = None
            if market_end_date:
                # Ensure both are timezone-aware for subtraction
                if market_end_date.tzinfo is None:
                    # If naive, assume UTC
                    market_end_date = market_end_date.replace(tzinfo=timezone.utc)
                if current_timestamp.tzinfo is None:
                    current_timestamp = current_timestamp.replace(tzinfo=timezone.utc)
                
                time_delta = market_end_date - current_timestamp
                time_remaining_seconds = time_delta.total_seconds()
            
            # Create snapshot with appropriate fields
            snapshot_data = {
                "token_id": token_id,
                "market_id": market_id,
                "timestamp": current_timestamp,
                "best_bid_price": best_bid_price,
                "best_bid_size": best_bid_size,
                "best_ask_price": best_ask_price,
                "best_ask_size": best_ask_size,
                "spread": spread,
                "spread_bps": spread_bps,
                "bids": bids,
                "asks": asks,
                "market_question": market_question,
                "outcome": outcome,
                "extra_metadata": metadata,
            }
            
            # Add fields for btc_eth_table
            if self.use_btc_eth_table:
                snapshot_data["asset_type"] = asset_type
                snapshot_data["market_start_date"] = market_start_date
                snapshot_data["market_end_date"] = market_end_date
                snapshot_data["time_remaining_seconds"] = time_remaining_seconds
            
            snapshot = SnapshotTable(**snapshot_data)
            
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
        market_start_date: Optional[datetime] = None,
        market_end_date: Optional[datetime] = None,
        asset_type: Optional[str] = None,
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
            market_start_date=market_start_date,
            market_end_date=market_end_date,
            asset_type=asset_type,
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
            market_id: Filter by market ID (required if per_market_tables=True)
            start_time: Start of time range
            end_time: End of time range
            limit: Maximum number of results
            
        Returns:
            List of OrderbookSnapshot objects
        """
        session = self.get_session()
        try:
            # If per-market tables enabled, use market-specific table
            if self.per_market_tables and market_id:
                model_class = self._get_table_for_market(market_id)
            else:
                model_class = OrderbookSnapshot
            
            query = session.query(model_class)
            
            if token_id:
                query = query.filter(model_class.token_id == token_id)
            if market_id and not self.per_market_tables:
                query = query.filter(model_class.market_id == market_id)
            if start_time:
                query = query.filter(model_class.timestamp >= start_time)
            if end_time:
                query = query.filter(model_class.timestamp <= end_time)
            
            query = query.order_by(model_class.timestamp.desc())
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

