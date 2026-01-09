"""
Combined monitoring script for BTC 15-minute and 1-hour markets.
Monitors both market types and logs to separate tables.

Usage:
    python scripts/python/monitor_btc_markets.py
"""
import asyncio
import logging
import sys
import os
from typing import Set
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, skip (Railway sets env vars directly)

from agents.polymarket.orderbook_db import OrderbookDatabase
from agents.polymarket.orderbook_stream import OrderbookLogger
from agents.polymarket.orderbook_poller import OrderbookPoller
from agents.polymarket.btc_market_detector import (
    get_latest_btc_15m_market_proactive,
    get_latest_btc_15m_market,
    get_all_active_btc_15m_markets,
    get_latest_btc_1h_market_proactive,
    get_latest_btc_1h_market,
    get_all_active_btc_1h_markets,
    is_market_active,
)
from agents.polymarket.market_finder import get_token_ids_from_market, get_market_info_for_logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True  # Force reconfiguration (useful for Railway)
)
# Ensure logs are flushed immediately (important for Railway)
import sys
logging.getLogger().handlers[0].stream = sys.stdout
logger = logging.getLogger(__name__)


class BTCMarketsMonitor:
    """Monitor for BTC 15-minute and 1-hour markets."""
    
    def __init__(self, db_15m: OrderbookDatabase, db_1h: OrderbookDatabase, check_interval: float = 10.0, proactive: bool = True):
        """
        Initialize BTC markets monitor.
        
        Args:
            db_15m: OrderbookDatabase instance for 15-minute markets (use_btc_15_min_table=True)
            db_1h: OrderbookDatabase instance for 1-hour markets (use_btc_1_hour_table=True)
            check_interval: How often to check for new markets (seconds). Default 10s for proactive mode.
            proactive: If True, use proactive detection (check future windows). Default True.
        """
        self.db_15m = db_15m
        self.db_1h = db_1h
        self.check_interval = check_interval
        self.proactive = proactive
        
        # Track monitored markets separately for 15m and 1h
        self.monitored_15m_event_slugs: Set[str] = set()
        self.monitored_1h_event_slugs: Set[str] = set()
        self.monitored_token_ids: Set[str] = set()
        
        # WebSocket services (one per market type)
        self.logger_service_15m = None
        self.logger_service_1h = None
        self.logger_task_15m = None
        self.logger_task_1h = None
        
        # Pollers (fallback)
        self.poller_15m = None
        self.poller_1h = None
        self.poller_task_15m = None
        self.poller_task_1h = None
        
        self.running = False
    
    async def _check_for_new_markets(self):
        """Check for new BTC 15-minute and 1-hour markets."""
        logger.info("=" * 80)
        logger.info("Checking for new BTC markets (15-minute and 1-hour)...")
        logger.info("=" * 80)
        
        # Check 15-minute markets
        await self._check_15m_markets()
        
        # Check 1-hour markets
        await self._check_1h_markets()
    
    async def _check_15m_markets(self):
        """Check for new BTC 15-minute markets."""
        logger.info("Checking for new BTC updown 15-minute markets...")
        
        if self.proactive:
            latest_market = get_latest_btc_15m_market_proactive()
        else:
            latest_market = get_latest_btc_15m_market()
        
        markets = []
        if latest_market:
            markets = [latest_market]
            logger.info(f"Found latest BTC 15-minute market via {'proactive' if self.proactive else 'standard'} detection")
        else:
            markets = get_all_active_btc_15m_markets()
            logger.info(f"Found {len(markets)} active BTC 15-minute markets via search")
        
        new_token_ids = []
        new_market_info = {}
        
        for market in markets:
            event_slug = market.get("_event_slug", "")
            market_id = market.get("id", "unknown")
            question = market.get("question", "N/A")[:60]
            
            logger.info(f"Processing 15m market: ID={market_id}, slug={event_slug}, question={question}...")
            
            # Extract token IDs first
            token_ids = get_token_ids_from_market(market)
            logger.info(f"  Extracted {len(token_ids)} token IDs: {token_ids}")
            
            if not token_ids:
                logger.warning(f"  No token IDs found for market {event_slug}")
                continue
            
            # Only monitor markets that are currently running (within their actual time window)
            from agents.polymarket.btc_market_detector import is_market_currently_running
            if not is_market_currently_running(market):
                logger.info(f"  Market {event_slug} is not currently running (may be future or past), skipping")
                # If market ended and was being monitored, clean it up
                if event_slug in self.monitored_15m_event_slugs:
                    logger.info(f"  Market {event_slug} has ended - removing from monitoring")
                    self.monitored_15m_event_slugs.discard(event_slug)
                    for token_id in token_ids:
                        self.monitored_token_ids.discard(token_id)
                continue
            
            # Check if already monitoring - verify both event slug AND tokens are actually monitored
            if event_slug in self.monitored_15m_event_slugs:
                # Check if tokens are actually in the monitored set
                tokens_monitored = any(tid in self.monitored_token_ids for tid in token_ids)
                if tokens_monitored:
                    logger.info(f"  Already monitoring event {event_slug} (tokens active), skipping")
                    continue
                else:
                    # Event slug marked but tokens not found - stale entry (e.g., from failed start)
                    logger.warning(f"  Event {event_slug} marked as monitoring but tokens not found - clearing stale entry")
                    self.monitored_15m_event_slugs.discard(event_slug)
                    # Fall through to start monitoring
            
            # Check if we're already monitoring any of these tokens (by token ID)
            if any(tid in self.monitored_token_ids for tid in token_ids):
                logger.info(f"  Already monitoring some tokens for this market, skipping")
                continue
            
            # Prepare for monitoring (store event slug and token IDs for tracking)
            market_info = get_market_info_for_logging(market)
            market_info["_event_slug"] = event_slug
            market_info["_token_ids"] = token_ids
            
            for token_id in token_ids:
                if token_id not in self.monitored_token_ids:
                    new_token_ids.append(token_id)
                    new_market_info.update(market_info)
            
            logger.info(f"✓ Found new BTC 15-minute market: {market.get('question', event_slug)[:60]}...")
            logger.info(f"  Event slug: {event_slug}")
            logger.info(f"  Token IDs: {token_ids}")
        
        # Start monitoring new tokens
        if new_token_ids:
            logger.info(f"Starting to monitor {len(new_token_ids)} new 15-minute market tokens")
            try:
                success = await self._start_monitoring(new_token_ids, new_market_info, market_type="15m")
                if success:
                    # Only mark as monitored AFTER successful start
                    event_slug = new_market_info.get("_event_slug")
                    token_ids = new_market_info.get("_token_ids", [])
                    if event_slug:
                        self.monitored_15m_event_slugs.add(event_slug)
                        for token_id in token_ids:
                            self.monitored_token_ids.add(token_id)
                        logger.info(f"✓ Successfully started monitoring 15-minute market {event_slug}")
                else:
                    logger.warning(f"⚠ Failed to start monitoring 15-minute market (will retry on next check)")
            except Exception as e:
                logger.error(f"Error starting monitoring for 15-minute market: {e}", exc_info=True)
    
    async def _check_1h_markets(self):
        """Check for new BTC 1-hour markets."""
        logger.info("Checking for new BTC updown 1-hour markets...")
        
        if self.proactive:
            latest_market = get_latest_btc_1h_market_proactive()
        else:
            latest_market = get_latest_btc_1h_market()
        
        markets = []
        if latest_market:
            markets = [latest_market]
            logger.info(f"Found latest BTC 1-hour market via {'proactive' if self.proactive else 'standard'} detection")
        else:
            markets = get_all_active_btc_1h_markets()
            logger.info(f"Found {len(markets)} active BTC 1-hour markets via search")
        
        new_token_ids = []
        new_market_info = {}
        
        for market in markets:
            event_slug = market.get("_event_slug", "")
            market_id = market.get("id", "unknown")
            question = market.get("question", "N/A")[:60]
            
            logger.info(f"Processing 1h market: ID={market_id}, slug={event_slug}, question={question}...")
            
            # Extract token IDs first
            token_ids = get_token_ids_from_market(market)
            logger.info(f"  Extracted {len(token_ids)} token IDs: {token_ids}")
            
            if not token_ids:
                logger.warning(f"  No token IDs found for market {event_slug}")
                continue
            
            # Only monitor markets that are currently running (within their actual time window)
            from agents.polymarket.btc_market_detector import is_market_currently_running
            if not is_market_currently_running(market):
                logger.info(f"  Market {event_slug} is not currently running (may be future or past), skipping")
                # If market ended and was being monitored, clean it up
                if event_slug in self.monitored_1h_event_slugs:
                    logger.info(f"  Market {event_slug} has ended - removing from monitoring")
                    self.monitored_1h_event_slugs.discard(event_slug)
                    for token_id in token_ids:
                        self.monitored_token_ids.discard(token_id)
                continue
            
            # Check if already monitoring - verify both event slug AND tokens are actually monitored
            if event_slug in self.monitored_1h_event_slugs:
                # Check if tokens are actually in the monitored set
                tokens_monitored = any(tid in self.monitored_token_ids for tid in token_ids)
                if tokens_monitored:
                    logger.info(f"  Already monitoring event {event_slug} (tokens active), skipping")
                    continue
                else:
                    # Event slug marked but tokens not found - stale entry (e.g., from failed start)
                    logger.warning(f"  Event {event_slug} marked as monitoring but tokens not found - clearing stale entry")
                    self.monitored_1h_event_slugs.discard(event_slug)
                    # Fall through to start monitoring
            
            # Check if we're already monitoring any of these tokens (by token ID)
            if any(tid in self.monitored_token_ids for tid in token_ids):
                logger.info(f"  Already monitoring some tokens for this market, skipping")
                continue
            
            # Prepare for monitoring (store event slug and token IDs for tracking)
            market_info = get_market_info_for_logging(market)
            market_info["_event_slug"] = event_slug
            market_info["_token_ids"] = token_ids
            
            for token_id in token_ids:
                if token_id not in self.monitored_token_ids:
                    new_token_ids.append(token_id)
                    new_market_info.update(market_info)
            
            logger.info(f"✓ Found new BTC 1-hour market: {market.get('question', event_slug)[:60]}...")
            logger.info(f"  Event slug: {event_slug}")
            logger.info(f"  Token IDs: {token_ids}")
        
        # Start monitoring new tokens
        if new_token_ids:
            logger.info(f"Starting to monitor {len(new_token_ids)} new 1-hour market tokens")
            try:
                success = await self._start_monitoring(new_token_ids, new_market_info, market_type="1h")
                if success:
                    # Only mark as monitored AFTER successful start
                    event_slug = new_market_info.get("_event_slug")
                    token_ids = new_market_info.get("_token_ids", [])
                    if event_slug:
                        self.monitored_1h_event_slugs.add(event_slug)
                        for token_id in token_ids:
                            self.monitored_token_ids.add(token_id)
                        logger.info(f"✓ Successfully started monitoring 1-hour market {event_slug}")
                else:
                    logger.warning(f"⚠ Failed to start monitoring 1-hour market (will retry on next check)")
            except Exception as e:
                logger.error(f"Error starting monitoring for 1-hour market: {e}", exc_info=True)
    
    async def _start_monitoring(self, token_ids: list, market_info: dict, market_type: str) -> bool:
        """
        Start monitoring new tokens.
        
        Args:
            token_ids: List of token IDs to monitor
            market_info: Market metadata dict
            market_type: "15m" or "1h"
        
        Returns:
            True if monitoring started successfully, False otherwise
        """
        import os
        # Use separate wallet key for monitoring script (not trading script)
        has_wallet_key = bool(os.getenv("POLYGON_WALLET_MONITORING_SCRIPT_PRIVATE_KEY"))
        
        # Select appropriate database and services based on market type
        if market_type == "15m":
            db = self.db_15m
            logger_service = self.logger_service_15m
            logger_task = self.logger_task_15m
            poller = self.poller_15m
            poller_task = self.poller_task_15m
        else:  # "1h"
            db = self.db_1h
            logger_service = self.logger_service_1h
            logger_task = self.logger_task_1h
            poller = self.poller_1h
            poller_task = self.poller_task_1h
        
        # Try WebSocket first (lower latency)
        if has_wallet_key:
            from agents.polymarket.orderbook_stream import OrderbookLogger
            logger.info(f"✓ Wallet key found - Trying WebSocket mode for {market_type} markets")
            
            try:
                if logger_service is None:
                    # First time - create logger
                    new_logger_service = OrderbookLogger(
                        db,
                        token_ids,
                        market_info=market_info,
                    )
                    # Start WebSocket
                    new_logger_task = asyncio.create_task(new_logger_service.start())
                    
                    # Wait for stream to initialize
                    for _ in range(10):
                        await asyncio.sleep(0.5)
                        if (new_logger_service.stream and 
                            new_logger_service.stream.websocket):
                            break
                    
                    # Check if WebSocket is connected
                    if (new_logger_service.stream and 
                        new_logger_service.stream.websocket):
                        await asyncio.sleep(3)
                        
                        if hasattr(new_logger_service.stream, '_message_count'):
                            msg_count = new_logger_service.stream._message_count
                            if msg_count > 0:
                                logger.info(f"✓ WebSocket is working for {market_type}! Received {msg_count} messages")
                                # Update instance variables
                                if market_type == "15m":
                                    self.logger_service_15m = new_logger_service
                                    self.logger_task_15m = new_logger_task
                                else:
                                    self.logger_service_1h = new_logger_service
                                    self.logger_task_1h = new_logger_task
                                return True  # Success
                            else:
                                logger.warning(f"⚠ WebSocket connected but no messages for {market_type} - falling back to polling")
                                new_logger_task.cancel()
                                try:
                                    await new_logger_service.stop()
                                except:
                                    pass
                                raise Exception("No WebSocket messages")
                        else:
                            logger.warning(f"⚠ WebSocket message counter not initialized for {market_type} - falling back to polling")
                            new_logger_task.cancel()
                            try:
                                await new_logger_service.stop()
                            except:
                                pass
                            raise Exception("WebSocket message counter not initialized")
                    else:
                        logger.warning(f"⚠ WebSocket failed to connect for {market_type} - falling back to polling")
                        if new_logger_task:
                            new_logger_task.cancel()
                        raise Exception("WebSocket connection failed")
                else:
                    # Add new subscriptions to existing stream
                    if logger_service.stream and logger_service.stream.websocket:
                        for token_id in token_ids:
                            await logger_service.stream.subscribe_to_orderbook(token_id)
                            logger_service.market_info.update(market_info)
                            if token_id not in logger_service.token_ids:
                                logger_service.token_ids.append(token_id)
                        logger.info(f"Added {len(token_ids)} new subscriptions to {market_type} WebSocket")
                        return True  # Success
            except Exception as e:
                logger.warning(f"WebSocket failed for {market_type}: {e} - Falling back to polling mode")
                has_wallet_key = False  # Force polling fallback
        
        # Use polling if WebSocket failed or no wallet key
        if not has_wallet_key:
            if os.getenv("POLYGON_WALLET_MONITORING_SCRIPT_PRIVATE_KEY"):
                logger.warning(f"⚠ WebSocket failed for {market_type} - Using polling mode")
            else:
                logger.warning(f"⚠ No wallet key for {market_type} - Using polling mode")
            
            from agents.polymarket.orderbook_poller import OrderbookPoller
            
            if poller is None:
                # Poll every 0.5 seconds
                new_poller = OrderbookPoller(
                    db,
                    token_ids,
                    poll_interval=0.5,
                    market_info=market_info,
                    track_top_n=0,  # Save all snapshots
                )
                new_poller_task = asyncio.create_task(new_poller.poll_loop())
                
                # Update instance variables
                if market_type == "15m":
                    self.poller_15m = new_poller
                    self.poller_task_15m = new_poller_task
                else:
                    self.poller_1h = new_poller
                    self.poller_task_1h = new_poller_task
                
                logger.info(f"Started polling for {market_type} markets (0.5s interval)")
                return True  # Success
            else:
                # Add tokens to existing poller
                for token_id in token_ids:
                    if token_id not in poller.token_ids:
                        poller.token_ids.append(token_id)
                poller.market_info.update(market_info)
                logger.info(f"Added {len(token_ids)} tokens to existing {market_type} poller")
                return True  # Success
        
        # If we get here, monitoring didn't start
        logger.error(f"Failed to start monitoring for {market_type} markets")
        return False
    
    async def run(self):
        """Main monitoring loop."""
        self.running = True
        logger.info("=" * 80)
        logger.info("BTC Markets Monitor Started")
        logger.info(f"  Check interval: {self.check_interval}s")
        logger.info(f"  Proactive mode: {self.proactive}")
        logger.info("=" * 80)
        
        while self.running:
            try:
                await self._check_for_new_markets()
            except Exception as e:
                logger.error(f"Error checking for markets: {e}", exc_info=True)
            
            await asyncio.sleep(self.check_interval)
    
    async def stop(self):
        """Stop monitoring."""
        logger.info("Stopping BTC markets monitor...")
        self.running = False
        
        # Stop WebSocket services
        if self.logger_service_15m:
            try:
                await self.logger_service_15m.stop()
            except:
                pass
        if self.logger_service_1h:
            try:
                await self.logger_service_1h.stop()
            except:
                pass
        
        # Stop pollers
        if self.poller_15m:
            try:
                await self.poller_15m.stop()
            except:
                pass
        if self.poller_1h:
            try:
                await self.poller_1h.stop()
            except:
                pass


async def main():
    """Main entry point."""
    # Initialize databases
    # 15-minute markets -> btc_15_min_table
    db_15m = OrderbookDatabase(use_btc_15_min_table=True)
    logger.info("✓ Initialized database for BTC 15-minute markets (btc_15_min_table)")
    
    # 1-hour markets -> btc_1_hour_table
    db_1h = OrderbookDatabase(use_btc_1_hour_table=True)
    logger.info("✓ Initialized database for BTC 1-hour markets (btc_1_hour_table)")
    
    # Create monitor
    monitor = BTCMarketsMonitor(
        db_15m=db_15m,
        db_1h=db_1h,
        check_interval=10.0,  # Check every 10 seconds
        proactive=True,  # Use proactive detection
    )
    
    try:
        await monitor.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await monitor.stop()


if __name__ == "__main__":
    asyncio.run(main())

