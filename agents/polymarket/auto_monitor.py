"""
Automatic monitoring service that detects new markets matching criteria
and starts logging their orderbooks.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Set, Dict, List
from agents.polymarket.market_finder import (
    find_15min_markets,
    find_1hour_markets,
    get_token_ids_from_market,
    get_market_info_for_logging,
)
from agents.polymarket.orderbook_db import OrderbookDatabase
from agents.polymarket.orderbook_stream import OrderbookLogger
from typing import Optional

logger = logging.getLogger(__name__)


class AutoMarketMonitor:
    """
    Automatically monitors new markets matching duration criteria
    and starts logging their orderbooks.
    """
    
    def __init__(
        self,
        db: OrderbookDatabase,
        check_interval: float = 60.0,
        monitor_15min: bool = True,
        monitor_1hour: bool = True,
        mode: str = "websocket",
    ):
        """
        Initialize auto monitor.
        
        Args:
            db: OrderbookDatabase instance
            check_interval: Seconds between checks for new markets (default: 60)
            monitor_15min: Monitor 15-minute markets
            monitor_1hour: Monitor 1-hour markets
            mode: "websocket" or "poll" (default: websocket)
        """
        self.db = db
        self.check_interval = check_interval
        self.monitor_15min = monitor_15min
        self.monitor_1hour = monitor_1hour
        self.mode = mode
        
        # Track which markets we're already monitoring
        self.monitored_market_ids: Set[str] = set()
        self.monitored_token_ids: Set[str] = set()
        
        # Current logger instance
        self.logger_service: Optional[OrderbookLogger] = None
        self.logger_task: Optional[asyncio.Task] = None
        self.running = False
    
    async def _check_for_new_markets(self):
        """Check for new markets and start monitoring them."""
        new_token_ids = []
        new_market_info = {}
        
        # Check 15-minute markets
        if self.monitor_15min:
            logger.info("Checking for new 15-minute markets...")
            markets_15min = find_15min_markets(active_only=True, limit=100)
            for market in markets_15min:
                market_id = str(market.get("id", ""))
                if market_id not in self.monitored_market_ids:
                    token_ids = get_token_ids_from_market(market)
                    market_info = get_market_info_for_logging(market)
                    
                    for token_id in token_ids:
                        if token_id not in self.monitored_token_ids:
                            new_token_ids.append(token_id)
                            new_market_info.update(market_info)
                            self.monitored_token_ids.add(token_id)
                    
                    self.monitored_market_ids.add(market_id)
                    logger.info(f"Found new 15-minute market: {market.get('question', market_id)}")
        
        # Check 1-hour markets
        if self.monitor_1hour:
            logger.info("Checking for new 1-hour markets...")
            markets_1hour = find_1hour_markets(active_only=True, limit=100)
            for market in markets_1hour:
                market_id = str(market.get("id", ""))
                if market_id not in self.monitored_market_ids:
                    token_ids = get_token_ids_from_market(market)
                    market_info = get_market_info_for_logging(market)
                    
                    for token_id in token_ids:
                        if token_id not in self.monitored_token_ids:
                            new_token_ids.append(token_id)
                            new_market_info.update(market_info)
                            self.monitored_token_ids.add(token_id)
                    
                    self.monitored_market_ids.add(market_id)
                    logger.info(f"Found new 1-hour market: {market.get('question', market_id)}")
        
        # Start monitoring new tokens
        if new_token_ids:
            logger.info(f"Starting to monitor {len(new_token_ids)} new tokens")
            await self._start_monitoring(new_token_ids, new_market_info)
        else:
            logger.debug("No new markets found")
    
    async def _start_monitoring(self, token_ids: List[str], market_info: Dict):
        """Start monitoring new tokens."""
        if self.mode == "websocket":
            # For WebSocket, we need to add subscriptions to existing stream
            # or create a new one if none exists
            if self.logger_service is None:
                # First time - create logger with initial tokens
                self.logger_service = OrderbookLogger(
                    self.db,
                    token_ids,
                    market_info=market_info,
                )
                # Start in background
                self.logger_task = asyncio.create_task(self.logger_service.start())
            else:
                # Add new subscriptions to existing stream
                if self.logger_service.stream and self.logger_service.stream.websocket:
                    for token_id in token_ids:
                        try:
                            await self.logger_service.stream.subscribe_to_orderbook(token_id)
                            # Update market info
                            self.logger_service.market_info.update(market_info)
                            # Add to token_ids list
                            if token_id not in self.logger_service.token_ids:
                                self.logger_service.token_ids.append(token_id)
                            logger.info(f"Added subscription for token {token_id}")
                        except Exception as e:
                            logger.error(f"Error subscribing to {token_id}: {e}")
                else:
                    logger.warning("WebSocket not connected, cannot add subscriptions")
        else:
            # For polling mode, we'd need to restart with all tokens
            # This is simpler but less efficient
            logger.warning("Polling mode with dynamic tokens not fully implemented")
            logger.info(f"Would monitor {len(token_ids)} new tokens in polling mode")
    
    async def run(self):
        """Main monitoring loop."""
        self.running = True
        logger.info(
            f"Starting auto monitor (check interval: {self.check_interval}s, "
            f"15min: {self.monitor_15min}, 1hour: {self.monitor_1hour})"
        )
        
        # Initial check
        await self._check_for_new_markets()
        
        # Periodic checks
        while self.running:
            try:
                await asyncio.sleep(self.check_interval)
                if self.running:
                    await self._check_for_new_markets()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """Stop monitoring."""
        self.running = False
        if self.logger_service:
            # Create task to stop (in case we're not in async context)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.logger_service.stop())
                else:
                    loop.run_until_complete(self.logger_service.stop())
            except:
                pass


async def run_auto_monitor(
    check_interval: float = 60.0,
    monitor_15min: bool = True,
    monitor_1hour: bool = True,
    mode: str = "websocket",
    db_path: Optional[str] = None,
    database_url: Optional[str] = None,
):
    """
    Convenience function to run auto monitor.
    
    Args:
        check_interval: Seconds between checks for new markets
        monitor_15min: Monitor 15-minute markets
        monitor_1hour: Monitor 1-hour markets
        mode: "websocket" or "poll"
        db_path: Optional path to SQLite database (deprecated, use database_url)
        database_url: Optional database URL (overrides db_path and env vars)
    """
    from agents.polymarket.orderbook_db import OrderbookDatabase
    
    # Support both db_path (legacy) and database_url
    if database_url is None and db_path is not None:
        database_url = f"sqlite:///{db_path}"
    
    db = OrderbookDatabase(database_url=database_url)
    monitor = AutoMarketMonitor(
        db,
        check_interval=check_interval,
        monitor_15min=monitor_15min,
        monitor_1hour=monitor_1hour,
        mode=mode,
    )
    
    try:
        await monitor.run()
    except KeyboardInterrupt:
        logger.info("Stopping auto monitor...")
        monitor.stop()
    except Exception as e:
        logger.error(f"Error in auto monitor: {e}")
        monitor.stop()
        raise

