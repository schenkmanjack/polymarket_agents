"""
Polling-based orderbook logger as an alternative to WebSocket streaming.
Useful when WebSocket connections are unreliable or you need more control over polling frequency.
"""
import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from agents.polymarket.polymarket import Polymarket
from agents.polymarket.orderbook_db import OrderbookDatabase

logger = logging.getLogger(__name__)


class OrderbookPoller:
    """
    Polls Polymarket API for orderbook updates and logs them to database.
    This is a fallback/alternative to WebSocket streaming.
    """
    
    def __init__(
        self,
        db: OrderbookDatabase,
        token_ids: List[str],
        poll_interval: float = 1.0,
        market_info: Optional[Dict[str, Dict]] = None,
    ):
        """
        Initialize the orderbook poller.
        
        Args:
            db: OrderbookDatabase instance
            token_ids: List of token IDs to monitor
            poll_interval: Seconds between polls (default: 1.0)
            market_info: Optional dict mapping token_id to market metadata
        """
        self.db = db
        self.token_ids = token_ids
        self.poll_interval = poll_interval
        self.market_info = market_info or {}
        self.polymarket = Polymarket()
        self.running = False
    
    async def _fetch_and_save_orderbook(self, token_id: str):
        """Fetch orderbook for a token and save to database."""
        try:
            orderbook = self.polymarket.get_orderbook(token_id)
            
            # Convert OrderBookSummary to lists
            bids = [[float(bid.price), float(bid.size)] for bid in orderbook.bids]
            asks = [[float(ask.price), float(ask.size)] for ask in orderbook.asks]
            
            # Get market info if available
            market_meta = self.market_info.get(token_id, {})
            
            # Save to database
            self.db.save_snapshot(
                token_id=token_id,
                bids=bids,
                asks=asks,
                market_id=market_meta.get("market_id"),
                market_question=market_meta.get("market_question"),
                outcome=market_meta.get("outcome"),
                metadata={"source": "polling", "poll_interval": self.poll_interval},
            )
            
            logger.debug(f"Saved orderbook snapshot for token {token_id}")
            
        except Exception as e:
            logger.error(f"Error fetching/saving orderbook for {token_id}: {e}")
    
    async def poll_loop(self):
        """Main polling loop."""
        self.running = True
        logger.info(f"Starting orderbook poller for {len(self.token_ids)} tokens (interval: {self.poll_interval}s)")
        
        while self.running:
            try:
                # Fetch orderbooks for all tokens concurrently
                tasks = [
                    self._fetch_and_save_orderbook(token_id)
                    for token_id in self.token_ids
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
                
                # Wait before next poll
                await asyncio.sleep(self.poll_interval)
                
            except asyncio.CancelledError:
                logger.info("Polling cancelled")
                break
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")
                await asyncio.sleep(self.poll_interval)
    
    def stop(self):
        """Stop polling."""
        self.running = False
        logger.info("Stopping orderbook poller")


async def run_orderbook_poller(
    token_ids: List[str],
    poll_interval: float = 1.0,
    db_path: Optional[str] = None,
):
    """
    Convenience function to run the orderbook poller.
    
    Args:
        token_ids: List of token IDs to monitor
        poll_interval: Seconds between polls
        db_path: Optional path to SQLite database
    """
    db = OrderbookDatabase(database_url=None if db_path is None else f"sqlite:///{db_path}")
    poller = OrderbookPoller(db, token_ids, poll_interval=poll_interval)
    
    try:
        await poller.poll_loop()
    except KeyboardInterrupt:
        logger.info("Stopping orderbook poller...")
        poller.stop()
    except Exception as e:
        logger.error(f"Error in orderbook poller: {e}")
        poller.stop()
        raise

