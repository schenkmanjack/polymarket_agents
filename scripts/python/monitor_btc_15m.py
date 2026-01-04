"""
Automatically monitor new BTC updown 15-minute markets.
Detects new markets by checking event slugs and starts monitoring them.

Usage:
    python scripts/python/monitor_btc_15m.py
"""
import asyncio
import logging
import sys
import os
from typing import Set
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.polymarket.orderbook_db import OrderbookDatabase
from agents.polymarket.orderbook_stream import OrderbookLogger
from agents.polymarket.orderbook_poller import OrderbookPoller
from agents.polymarket.btc_market_detector import (
    get_latest_btc_15m_market,
    get_all_active_btc_15m_markets,
    extract_timestamp_from_slug,
    is_market_active,
)
from agents.polymarket.market_finder import get_token_ids_from_market, get_market_info_for_logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class BTC15mMonitor:
    """Monitor for new BTC 15-minute markets."""
    
    def __init__(self, db: OrderbookDatabase, check_interval: float = 60.0):
        self.db = db
        self.check_interval = check_interval
        self.monitored_event_slugs: Set[str] = set()
        self.monitored_token_ids: Set[str] = set()
        self.logger_service = None
        self.logger_task = None
        self.poller = None
        self.poller_task = None
        self.running = False
    
    async def _check_for_new_markets(self):
        """Check for new BTC 15-minute markets."""
        logger.info("Checking for new BTC updown 15-minute markets...")
        
        # Try to get latest market (uses multiple detection approaches)
        from agents.polymarket.btc_market_detector import get_latest_btc_15m_market
        latest_market = get_latest_btc_15m_market()
        
        markets = []
        if latest_market:
            markets = [latest_market]
            logger.info(f"Found latest BTC 15-minute market via detection")
        else:
            # Fallback: try getting all active markets
            markets = get_all_active_btc_15m_markets()
            logger.info(f"Found {len(markets)} active BTC 15-minute markets via search")
        
        new_token_ids = []
        new_market_info = {}
        
        for market in markets:
            event_slug = market.get("_event_slug", "")
            market_id = market.get("id", "unknown")
            question = market.get("question", "N/A")[:60]
            
            logger.info(f"Processing market: ID={market_id}, slug={event_slug}, question={question}...")
            
            # Skip if already monitoring this event
            if event_slug in self.monitored_event_slugs:
                logger.info(f"  Already monitoring event {event_slug}, skipping")
                continue
            
            # Check if market is still active
            if not is_market_active(market):
                logger.info(f"  Market {event_slug} is not active, skipping")
                continue
            
            # Extract token IDs
            logger.debug(f"  Extracting token IDs from market...")
            logger.debug(f"  Market keys: {list(market.keys())}")
            logger.debug(f"  clobTokenIds: {market.get('clobTokenIds')}")
            
            token_ids = get_token_ids_from_market(market)
            logger.info(f"  Extracted {len(token_ids)} token IDs: {token_ids}")
            
            if not token_ids:
                logger.warning(f"  No token IDs found for market {event_slug}")
                logger.warning(f"  Market data: {market}")
                continue
            
            # Check if we're already monitoring any of these tokens
            if any(tid in self.monitored_token_ids for tid in token_ids):
                logger.info(f"  Already monitoring tokens {token_ids}, skipping")
                continue
            
            # Add to monitoring
            market_info = get_market_info_for_logging(market)
            
            for token_id in token_ids:
                if token_id not in self.monitored_token_ids:
                    new_token_ids.append(token_id)
                    new_market_info.update(market_info)
                    self.monitored_token_ids.add(token_id)
            
            self.monitored_event_slugs.add(event_slug)
            logger.info(f"Found new BTC 15-minute market: {market.get('question', event_slug)[:60]}...")
            logger.info(f"  Event slug: {event_slug}")
            logger.info(f"  Token IDs: {token_ids}")
        
        # Start monitoring new tokens
        if new_token_ids:
            logger.info(f"Starting to monitor {len(new_token_ids)} new tokens")
            await self._start_monitoring(new_token_ids, new_market_info)
        else:
            logger.debug("No new markets found")
    
    async def _start_monitoring(self, token_ids: list, market_info: dict):
        """Start monitoring new tokens."""
        import os
        has_wallet_key = bool(os.getenv("POLYGON_WALLET_PRIVATE_KEY"))
        
        # Try WebSocket first (lower latency) - now with API credentials
        # Fallback to polling if WebSocket fails
        if has_wallet_key:
            # Try WebSocket first (lower latency, real-time updates)
            from agents.polymarket.orderbook_stream import OrderbookLogger
            logger.info("✓ Wallet key found - Trying WebSocket mode with API credentials")
            logger.info("  WebSocket provides sub-second updates vs polling every 0.5s")
            
            try:
                if self.logger_service is None:
                    # First time - create logger
                    self.logger_service = OrderbookLogger(
                        self.db,
                        token_ids,
                        market_info=market_info,
                    )
                    # Start WebSocket - it will connect, subscribe, and listen
                    # We'll let it run in background and check periodically if it's working
                    self.logger_task = asyncio.create_task(self.logger_service.start())
                    
                    # Wait for stream to initialize (connect happens in start())
                    for _ in range(10):  # Wait up to 5 seconds
                        await asyncio.sleep(0.5)
                        if (self.logger_service.stream and 
                            self.logger_service.stream.websocket):
                            break
                    
                    # Check if WebSocket is connected and receiving messages
                    if (self.logger_service.stream and 
                        self.logger_service.stream.websocket):
                        # Give it a bit more time to receive subscription confirmations
                        await asyncio.sleep(3)
                        
                        if hasattr(self.logger_service.stream, '_message_count'):
                            msg_count = self.logger_service.stream._message_count
                            if msg_count > 0:
                                logger.info(f"✓ WebSocket is working! Received {msg_count} messages")
                            else:
                                logger.warning("⚠ WebSocket connected but no messages received - falling back to polling")
                                # Cancel WebSocket task
                                self.logger_task.cancel()
                                try:
                                    await self.logger_service.stop()
                                except:
                                    pass
                                self.logger_service = None
                                raise Exception("No WebSocket messages")
                        else:
                            logger.warning("⚠ WebSocket connected but message counter not initialized - falling back to polling")
                            self.logger_task.cancel()
                            try:
                                await self.logger_service.stop()
                            except:
                                pass
                            self.logger_service = None
                            raise Exception("WebSocket message counter not initialized")
                    else:
                        logger.warning("⚠ WebSocket failed to connect - falling back to polling")
                        if self.logger_task:
                            self.logger_task.cancel()
                        self.logger_service = None
                        raise Exception("WebSocket connection failed")
                else:
                    # Add new subscriptions to existing stream
                    if self.logger_service.stream and self.logger_service.stream.websocket:
                        for token_id in token_ids:
                            await self.logger_service.stream.subscribe_to_orderbook(token_id)
                            self.logger_service.market_info.update(market_info)
                            if token_id not in self.logger_service.token_ids:
                                self.logger_service.token_ids.append(token_id)
                        logger.info(f"Added {len(token_ids)} new subscriptions to WebSocket")
            except Exception as e:
                logger.warning(f"WebSocket failed: {e} - Falling back to polling mode")
                # Fall through to polling mode
                has_wallet_key = False  # Force polling fallback
        else:
            # No wallet key - use polling
            logger.warning("⚠ No wallet key - Using polling mode")
            logger.info("  Add POLYGON_WALLET_PRIVATE_KEY to use WebSocket for better performance")
            
            from agents.polymarket.orderbook_poller import OrderbookPoller
            
            if not hasattr(self, 'poller') or self.poller is None:
                # Poll every 0.5 seconds for HFT backtesting
                # Track top 20 competitive levels - captures all micro-movements
                self.poller = OrderbookPoller(
                    self.db,
                    token_ids,
                    poll_interval=0.5,  # 500ms - fast polling for HFT
                    market_info=market_info,
                    track_top_n=20,  # Track top 20 bid/ask levels for competitive edge
                )
                self.poller_task = asyncio.create_task(self.poller.poll_loop())
            else:
                # Add new tokens to existing poller
                for token_id in token_ids:
                    if token_id not in self.poller.token_ids:
                        self.poller.token_ids.append(token_id)
                self.poller.market_info.update(market_info)
                logger.info(f"Added {len(token_ids)} tokens to polling")
        
        # OLD WebSocket code (commented out until fixed)
        # if has_wallet_key:
        #     # Use WebSocket (lower latency)
        #     from agents.polymarket.orderbook_stream import OrderbookLogger
        #     
        #     if self.logger_service is None:
        #         # First time - create logger
        #         self.logger_service = OrderbookLogger(
        #             self.db,
        #             token_ids,
        #             market_info=market_info,
        #         )
        #         self.logger_task = asyncio.create_task(self.logger_service.start())
        #     else:
        #         # Add new subscriptions to existing stream
        #         if self.logger_service.stream and self.logger_service.stream.websocket:
        #             for token_id in token_ids:
        #                 await self.logger_service.stream.subscribe_to_orderbook(token_id)
        #                 self.logger_service.market_info.update(market_info)
        #                 if token_id not in self.logger_service.token_ids:
        #                     self.logger_service.token_ids.append(token_id)
        #             logger.info(f"Added {len(token_ids)} new subscriptions to WebSocket")
    
    async def run(self):
        """Main monitoring loop."""
        self.running = True
        logger.info(f"Starting BTC 15-minute market monitor (check interval: {self.check_interval}s)")
        
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
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.logger_service.stop())
            except:
                pass


async def main():
    from agents.polymarket.orderbook_db import OrderbookDatabase
    
    # Initialize database (will use DATABASE_URL from env if set, otherwise SQLite)
    # This will log which database it's connecting to
    db = OrderbookDatabase()
    monitor = BTC15mMonitor(db, check_interval=60.0)
    
    try:
        await monitor.run()
    except KeyboardInterrupt:
        logger.info("Stopping monitor...")
        monitor.stop()
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        monitor.stop()
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)

