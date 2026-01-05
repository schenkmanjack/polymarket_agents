"""
Polling-based orderbook logger as an alternative to WebSocket streaming.
Useful when WebSocket connections are unreliable or you need more control over polling frequency.
"""
import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
import httpx
from agents.polymarket.orderbook_db import OrderbookDatabase

# Try to import Polymarket, but make it optional
try:
    from agents.polymarket.polymarket import Polymarket
    POLYMARKET_AVAILABLE = True
except Exception:
    POLYMARKET_AVAILABLE = False
    Polymarket = None

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
        track_top_n: int = 20,  # Track top N competitive levels for HFT
    ):
        """
        Initialize the orderbook poller.
        
        Args:
            db: OrderbookDatabase instance
            token_ids: List of token IDs to monitor
            poll_interval: Seconds between polls (default: 1.0)
            market_info: Optional dict mapping token_id to market metadata
            track_top_n: Number of top bid/ask levels to track for change detection (default: 20)
        """
        self.db = db
        self.token_ids = token_ids
        self.poll_interval = poll_interval
        self.market_info = market_info or {}
        self.track_top_n = track_top_n  # Track top N levels (0 = save all, no change detection)
        # Only initialize Polymarket if available (requires wallet key)
        self.polymarket = Polymarket() if POLYMARKET_AVAILABLE else None
        self.running = False
        # Track last orderbooks for change detection (only used if track_top_n > 0)
        self._last_orderbooks = {} if track_top_n > 0 else {}
    
    def _fetch_orderbook_direct(self, token_id: str):
        """Fetch orderbook directly from CLOB API (no auth needed)."""
        try:
            url = "https://clob.polymarket.com/book"
            response = httpx.get(url, params={"token_id": token_id}, timeout=10.0)
            
            if response.status_code == 200:
                data = response.json()
                bids = [[float(b["price"]), float(b["size"])] for b in data.get("bids", [])]
                asks = [[float(a["price"]), float(a["size"])] for a in data.get("asks", [])]
                return bids, asks
            else:
                logger.warning(f"Failed to fetch orderbook for {token_id}: HTTP {response.status_code}")
                return [], []
        except Exception as e:
            logger.error(f"Error fetching orderbook directly: {e}")
            return [], []
    
    def _orderbook_changed(self, bids: list, asks: list, last_bids: list, last_asks: list) -> bool:
        """
        Check if orderbook has changed in top N competitive levels.
        For HFT backtesting, we capture ANY change, no matter how small.
        
        Saves if:
        - First time (no last orderbook)
        - ANY price change in top N levels
        - ANY size change in top N levels (even 0.01% - captures all liquidity shifts)
        - Levels added/removed in top N
        """
        # Always save first time
        if not last_bids and not last_asks:
            return True
        
        # Check top N bid levels for ANY changes
        top_n_bids = min(self.track_top_n, len(bids), len(last_bids))
        
        # Check if number of levels changed
        if len(bids) != len(last_bids):
            return True
        
        # Check each level in top N for ANY price or size change
        for i in range(top_n_bids):
            if i >= len(bids) or i >= len(last_bids):
                return True  # Level added/removed
            
            # ANY price change (even 0.0001)
            if bids[i][0] != last_bids[i][0]:
                return True
            
            # ANY size change (even tiny amounts matter for HFT)
            if bids[i][1] != last_bids[i][1]:
                return True
        
        # Check top N ask levels for ANY changes
        top_n_asks = min(self.track_top_n, len(asks), len(last_asks))
        
        # Check if number of levels changed
        if len(asks) != len(last_asks):
            return True
        
        # Check each level in top N for ANY price or size change
        for i in range(top_n_asks):
            if i >= len(asks) or i >= len(last_asks):
                return True  # Level added/removed
            
            # ANY price change
            if asks[i][0] != last_asks[i][0]:
                return True
            
            # ANY size change
            if asks[i][1] != last_asks[i][1]:
                return True
        
        return False  # No changes in top N levels
    
    async def _fetch_and_save_orderbook(self, token_id: str):
        """Fetch orderbook for a token and save to database."""
        try:
            bids, asks = [], []
            
            # Prefer Polymarket client if wallet key is available (more reliable)
            if self.polymarket.private_key:
                try:
                    logger.debug(f"Fetching orderbook via Polymarket client for {token_id[:20]}...")
                    orderbook = self.polymarket.get_orderbook(token_id)
                    bids = [[float(bid.price), float(bid.size)] for bid in orderbook.bids]
                    asks = [[float(ask.price), float(ask.size)] for ask in orderbook.asks]
                    logger.debug(f"Got {len(bids)} bids, {len(asks)} asks via Polymarket client")
                except Exception as e:
                    logger.warning(f"Polymarket client failed for {token_id[:20]}...: {e}, trying direct HTTP")
                    # Fallback to direct HTTP
                    bids, asks = self._fetch_orderbook_direct(token_id)
            else:
                # No wallet key - use direct HTTP
                logger.debug(f"Fetching orderbook via direct HTTP for {token_id[:20]}...")
                bids, asks = self._fetch_orderbook_direct(token_id)
            
            if not bids and not asks:
                logger.warning(f"No orderbook data retrieved for token {token_id[:20]}...")
                return
            
            logger.debug(f"Retrieved orderbook: {len(bids)} bids, {len(asks)} asks")
            
            # Check if orderbook has changed (if change detection enabled)
            if self.track_top_n > 0:
                last_bids, last_asks = self._last_orderbooks.get(token_id, ([], []))
                has_changed = self._orderbook_changed(bids, asks, last_bids, last_asks)
                
                if not has_changed:
                    logger.debug(f"No change in top {self.track_top_n} levels for token {token_id[:20]}..., skipping save")
                    return
                
                # Update last orderbook
                self._last_orderbooks[token_id] = (bids.copy(), asks.copy())
            
            # Get market info if available
            market_meta = self.market_info.get(token_id, {})
            
            # Save to database asynchronously (non-blocking)
            # This allows polling to continue without waiting for DB write
            snapshot = await self.db.save_snapshot_async(
                token_id=token_id,
                bids=bids,
                asks=asks,
                market_id=market_meta.get("market_id"),
                market_question=market_meta.get("market_question"),
                outcome=market_meta.get("outcome"),
                metadata={"source": "polling", "poll_interval": self.poll_interval},
            )
            
            # Log periodically to avoid spam
            if not hasattr(self, '_save_count'):
                self._save_count = {}
            self._save_count[token_id] = self._save_count.get(token_id, 0) + 1
            
            best_bid = bids[0][0] if bids else None
            best_ask = asks[0][0] if asks else None
            logger.info(f"✓ Saved orderbook snapshot #{self._save_count[token_id]} (DB ID: {snapshot.id}) for token {token_id[:20]}... | Bid: {best_bid}, Ask: {best_ask} | Changed")
            
        except Exception as e:
            logger.error(f"❌ Error fetching/saving orderbook for {token_id[:20]}...: {e}", exc_info=True)
    
    async def poll_loop(self):
        """Main polling loop."""
        self.running = True
        has_wallet = bool(self.polymarket.private_key)
        logger.info(f"Starting orderbook poller for {len(self.token_ids)} tokens (interval: {self.poll_interval}s)")
        logger.info(f"  Using {'Polymarket client (wallet key found)' if has_wallet else 'direct HTTP (no wallet key)'} for fetching")
        
        poll_count = 0
        while self.running:
            try:
                poll_count += 1
                logger.debug(f"Poll cycle #{poll_count} - fetching orderbooks for {len(self.token_ids)} tokens")
                
                # Fetch orderbooks for all tokens concurrently
                tasks = [
                    self._fetch_and_save_orderbook(token_id)
                    for token_id in self.token_ids
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Check for exceptions
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Error in task for token {self.token_ids[i][:20]}...: {result}")
                
                logger.debug(f"Completed poll cycle #{poll_count}, sleeping {self.poll_interval}s")
                await asyncio.sleep(self.poll_interval)
                
            except asyncio.CancelledError:
                logger.info("Polling cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in polling loop: {e}", exc_info=True)
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

