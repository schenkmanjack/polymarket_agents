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
        self.token_ids = token_ids.copy() if isinstance(token_ids, list) else list(token_ids)  # Make mutable copy
        self.poll_interval = poll_interval
        self.market_info = market_info or {}
        self.track_top_n = track_top_n  # Track top N levels (0 = save all, no change detection)
        # Only initialize Polymarket if available (requires wallet key)
        self.polymarket = Polymarket() if POLYMARKET_AVAILABLE else None
        self.running = False
        # Track last orderbooks for change detection (only used if track_top_n > 0)
        self._last_orderbooks = {} if track_top_n > 0 else {}
        # Track consecutive failures for ended markets
        self._failed_tokens = {}  # {token_id: failure_count}
        self._max_failures = 3  # Remove token after 3 consecutive 404 failures
    
    def _fetch_orderbook_direct(self, token_id: str):
        """Fetch orderbook directly from CLOB API (no auth needed)."""
        try:
            url = "https://clob.polymarket.com/book"
            response = httpx.get(url, params={"token_id": token_id}, timeout=10.0)
            
            if response.status_code == 200:
                data = response.json()
                bids = [[float(b["price"]), float(b["size"])] for b in data.get("bids", [])]
                asks = [[float(a["price"]), float(a["size"])] for a in data.get("asks", [])]
                # Extract last_trade_price if available (this is the actual market price)
                last_trade_price = data.get("last_trade_price")
                if last_trade_price is not None:
                    last_trade_price = float(last_trade_price)
                return bids, asks, None, last_trade_price  # Return last_trade_price as 4th element
            else:
                # Return status code so caller can detect 404
                return [], [], response.status_code, None
        except Exception as e:
            logger.error(f"Error fetching orderbook directly: {e}")
            return [], [], None, None
    
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
            http_status = None  # Initialize http_status
            last_trade_price = None  # Initialize last_trade_price
            
            # Prefer Polymarket client if wallet key is available (more reliable)
            if self.polymarket and self.polymarket.private_key:
                try:
                    logger.debug(f"Fetching orderbook via Polymarket client for {token_id[:20]}...")
                    orderbook = self.polymarket.get_orderbook(token_id)
                    bids = [[float(bid.price), float(bid.size)] for bid in orderbook.bids]
                    asks = [[float(ask.price), float(ask.size)] for ask in orderbook.asks]
                    # Try to get last_trade_price from orderbook object if available
                    if hasattr(orderbook, 'last_trade_price') and orderbook.last_trade_price:
                        try:
                            last_trade_price = float(orderbook.last_trade_price)
                        except:
                            pass
                    logger.debug(f"Got {len(bids)} bids, {len(asks)} asks via Polymarket client")
                    # Success - http_status remains None (not a 404)
                except Exception as e:
                    error_str = str(e).lower()
                    # Check if it's a 404 error (market ended)
                    if "404" in error_str or "no orderbook exists" in error_str:
                        http_status = 404  # Mark as 404
                        self._failed_tokens[token_id] = self._failed_tokens.get(token_id, 0) + 1
                        failure_count = self._failed_tokens[token_id]
                        
                        if failure_count >= self._max_failures:
                            logger.info(f"⚠ Market ended: Token {token_id[:20]}... returned 404 {failure_count} times - removing from monitoring")
                            # Remove from token_ids list
                            if token_id in self.token_ids:
                                self.token_ids.remove(token_id)
                                # Clean up tracking data
                                self._failed_tokens.pop(token_id, None)
                                self._last_orderbooks.pop(token_id, None)
                                if hasattr(self, '_save_count'):
                                    self._save_count.pop(token_id, None)
                                if hasattr(self, '_first_fetch'):
                                    self._first_fetch.discard(token_id)
                            return
                        else:
                            logger.debug(f"Token {token_id[:20]}... returned 404 ({failure_count}/{self._max_failures}) - market may be ending")
                            return  # Don't try direct HTTP if we already got 404
                    
                    logger.warning(f"Polymarket client failed for {token_id[:20]}...: {e}, trying direct HTTP")
                    # Fallback to direct HTTP
                    bids, asks, http_status, last_trade_price = self._fetch_orderbook_direct(token_id)
            else:
                # No wallet key - use direct HTTP
                logger.debug(f"Fetching orderbook via direct HTTP for {token_id[:20]}...")
                bids, asks, http_status, last_trade_price = self._fetch_orderbook_direct(token_id)
            
            # Check for 404 errors (market ended) from direct HTTP
            if http_status == 404:
                self._failed_tokens[token_id] = self._failed_tokens.get(token_id, 0) + 1
                failure_count = self._failed_tokens[token_id]
                
                if failure_count >= self._max_failures:
                    logger.info(f"⚠ Market ended: Token {token_id[:20]}... returned 404 {failure_count} times - removing from monitoring")
                    # Remove from token_ids list
                    if token_id in self.token_ids:
                        self.token_ids.remove(token_id)
                        # Clean up tracking data
                        self._failed_tokens.pop(token_id, None)
                        self._last_orderbooks.pop(token_id, None)
                        if hasattr(self, '_save_count'):
                            self._save_count.pop(token_id, None)
                        if hasattr(self, '_first_fetch'):
                            self._first_fetch.discard(token_id)
                    return
                else:
                    logger.debug(f"Token {token_id[:20]}... returned 404 ({failure_count}/{self._max_failures}) - market may be ending")
                    return
            
            if not bids and not asks:
                logger.warning(f"No orderbook data retrieved for token {token_id[:20]}...")
                return
            
            # Successfully fetched orderbook - reset failure count
            if token_id in self._failed_tokens:
                self._failed_tokens.pop(token_id)
            
            # Log first retrieval to confirm we're getting data
            if not hasattr(self, '_first_fetch'):
                self._first_fetch = set()
            if token_id not in self._first_fetch:
                logger.info(f"✓ Retrieved orderbook for {token_id[:20]}...: {len(bids)} bids, {len(asks)} asks")
                self._first_fetch.add(token_id)
            
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
            
            # Determine asset type from market question
            asset_type = None
            market_question = market_meta.get("market_question", "")
            if market_question:
                question_lower = market_question.lower()
                if "bitcoin" in question_lower or "btc" in question_lower:
                    asset_type = "BTC"
                elif "ethereum" in question_lower or "eth" in question_lower:
                    asset_type = "ETH"
            
            # Calculate market price (actual trading price)
            # Priority: 1) outcome_price from Gamma API (what website shows), 2) last_trade_price, 3) mid price
            market_price = None
            outcome_price = market_meta.get("outcome_price")  # From Gamma API (what website shows)
            
            if outcome_price is not None:
                market_price = outcome_price
            elif last_trade_price is not None:
                market_price = last_trade_price
            elif bids and asks:
                best_bid = bids[0][0] if bids else None
                best_ask = asks[0][0] if asks else None
                if best_bid and best_ask:
                    market_price = (best_bid + best_ask) / 2
            
            # Save to database asynchronously (non-blocking)
            # This allows polling to continue without waiting for DB write
            metadata = {
                "source": "polling",
                "poll_interval": self.poll_interval,
                "last_trade_price": last_trade_price,
                "outcome_price": outcome_price,  # From Gamma API (what website shows)
                "market_price": market_price,  # Actual trading price (outcome_price > last_trade_price > mid price)
            }
            
            snapshot = await self.db.save_snapshot_async(
                token_id=token_id,
                bids=bids,
                asks=asks,
                market_id=market_meta.get("market_id"),
                market_question=market_meta.get("market_question"),
                outcome=market_meta.get("outcome"),
                metadata=metadata,
                market_start_date=market_meta.get("market_start_date"),
                market_end_date=market_meta.get("market_end_date"),
                asset_type=asset_type,
            )
            
            # Log every save (with track_top_n=0, we save everything)
            if not hasattr(self, '_save_count'):
                self._save_count = {}
            self._save_count[token_id] = self._save_count.get(token_id, 0) + 1
            
            # Get best bid/ask from top of orderbook (may be stale)
            best_bid_raw = bids[0][0] if bids else None
            best_ask_raw = asks[0][0] if asks else None
            
            # Find best bid/ask near actual market price (like UI does)
            from agents.polymarket.orderbook_utils import get_best_bid_ask_near_price
            
            reference_price = outcome_price or last_trade_price or market_price
            best_bid_near, best_ask_near = None, None
            if reference_price and bids and asks:
                # Convert bids/asks to dict format if needed
                bids_dict = [{"price": str(b[0]), "size": str(b[1])} for b in bids] if isinstance(bids[0], list) else bids
                asks_dict = [{"price": str(a[0]), "size": str(a[1])} for a in asks] if isinstance(asks[0], list) else asks
                best_bid_near, best_ask_near = get_best_bid_ask_near_price(
                    bids_dict, asks_dict, reference_price, max_spread_pct=0.15
                )
            
            # Log every save (since we're saving full orderbook every 0.5s for HFT)
            # Log every 10th save to avoid spam, but always log first few
            # Show meaningful prices: outcome_price > market_price > last_trade_price > bid/ask
            price_parts = []
            
            if outcome_price is not None:
                price_parts.append(f"Outcome: {outcome_price:.4f} (website)")
            if market_price is not None:
                price_parts.append(f"Market: {market_price:.4f}")
            if last_trade_price is not None:
                price_parts.append(f"LastTrade: {last_trade_price:.4f}")
            
            # Show best bid/ask near market price (like UI shows)
            if best_bid_near and best_ask_near:
                spread = best_ask_near - best_bid_near
                price_parts.append(f"Bid/Ask: {best_bid_near:.4f}/{best_ask_near:.4f} (near market)")
                if best_bid_raw and best_ask_raw and (abs(best_bid_raw - best_bid_near) > 0.1 or abs(best_ask_raw - best_ask_near) > 0.1):
                    price_parts.append(f"[raw: {best_bid_raw:.2f}/{best_ask_raw:.2f}]")
            elif best_bid_raw and best_ask_raw:
                spread = best_ask_raw - best_bid_raw
                if spread > 0.1:
                    price_parts.append(f"Bid/Ask: {best_bid_raw:.2f}/{best_ask_raw:.2f} (wide spread!)")
                else:
                    price_parts.append(f"Bid/Ask: {best_bid_raw:.4f}/{best_ask_raw:.4f}")
            
            price_info = " | ".join(price_parts) if price_parts else "No price data"
            
            if self._save_count[token_id] <= 5 or self._save_count[token_id] % 10 == 0:
                logger.info(f"✓ Saved orderbook snapshot #{self._save_count[token_id]} (DB ID: {snapshot.id}) for token {token_id[:20]}... | {price_info} | Levels: {len(bids)} bids, {len(asks)} asks")
            
        except Exception as e:
            logger.error(f"❌ Error fetching/saving orderbook for {token_id[:20]}...: {e}", exc_info=True)
    
    async def poll_loop(self):
        """Main polling loop."""
        self.running = True
        has_wallet = bool(self.polymarket and self.polymarket.private_key)
        logger.info(f"Starting orderbook poller for {len(self.token_ids)} tokens (interval: {self.poll_interval}s)")
        logger.info(f"  Using {'Polymarket client (wallet key found)' if has_wallet else 'direct HTTP (no wallet key)'} for fetching")
        
        poll_count = 0
        while self.running:
            try:
                poll_count += 1
                # Log every 10th poll cycle to show activity
                if poll_count == 1 or poll_count % 10 == 0:
                    logger.info(f"Poll cycle #{poll_count} - fetching orderbooks for {len(self.token_ids)} tokens")
                
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
                
                if poll_count == 1 or poll_count % 10 == 0:
                    logger.info(f"Completed poll cycle #{poll_count}, sleeping {self.poll_interval}s")
                await asyncio.sleep(self.poll_interval)
                
            except asyncio.CancelledError:
                logger.info("Polling cancelled")
                raise  # Re-raise to properly propagate cancellation
            except SystemExit:
                logger.info("Polling received SystemExit")
                break
            except KeyboardInterrupt:
                logger.info("Polling received KeyboardInterrupt")
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

