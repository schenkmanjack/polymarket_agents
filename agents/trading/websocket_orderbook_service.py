"""
WebSocket-based orderbook service for real-time orderbook updates.

Provides a single WebSocket connection to CLOB endpoint with dynamic token subscription
and in-memory caching for fast orderbook lookups.
"""
import asyncio
import json
import logging
import os
from typing import Dict, List, Optional, Set, Any
from datetime import datetime, timezone
from collections import defaultdict
import threading

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
except ImportError:
    websockets = None
    WebSocketClientProtocol = None

logger = logging.getLogger(__name__)


class WebSocketOrderbookService:
    """
    WebSocket service for real-time Polymarket orderbook streaming.
    
    Maintains a single WebSocket connection and subscribes to multiple tokens.
    Provides thread-safe access to cached orderbook data.
    """
    
    # CLOB WebSocket endpoint for orderbook data
    CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    def __init__(
        self,
        proxy_url: Optional[str] = None,
        health_check_timeout: float = 14.0,
        reconnect_delay: float = 5.0,
    ):
        """
        Initialize WebSocket orderbook service.
        
        Args:
            proxy_url: Optional proxy URL for VPN/proxy support
            health_check_timeout: Seconds of silence before considering connection dead (default: 14.0)
            reconnect_delay: Initial delay before reconnecting (default: 5.0)
        """
        if websockets is None:
            raise ImportError("websockets library not installed. Install with: pip install websockets")
        
        self.proxy_url = proxy_url
        self.health_check_timeout = health_check_timeout
        self.reconnect_delay = reconnect_delay
        
        # WebSocket connection
        self.websocket: Optional[WebSocketClientProtocol] = None
        self.running = False
        self.connected = False
        
        # Token subscription management
        self.subscribed_tokens: Set[str] = set()  # Set of token IDs we're subscribed to
        self.token_to_market_slug: Dict[str, str] = {}  # Map token_id -> market_slug for logging
        
        # Orderbook cache: {token_id: {"bids": [...], "asks": [...], "last_update": datetime}}
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.RLock()  # Thread-safe access
        
        # Connection health tracking
        self.last_message_time: Optional[datetime] = None
        self._last_message_lock = threading.Lock()
        
        # Reconnection tracking
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        
        # Fallback tracking (log once when falling back, log when switching back)
        self._fallback_logged = False
        self._websocket_back_logged = False
        
        # Background tasks
        self._listen_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._health_check_task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start the WebSocket service (connect and begin listening)."""
        if self.running:
            logger.warning("WebSocket service already running")
            return
        
        self.running = True
        logger.info("🚀 Starting WebSocket orderbook service...")
        await self._connect_and_subscribe()
        
        # Start background tasks
        self._listen_task = asyncio.create_task(self._listen_loop())
        self._ping_task = asyncio.create_task(self._ping_loop())
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        
        logger.info("✓ WebSocket orderbook service started")
    
    async def stop(self):
        """Stop the WebSocket service."""
        logger.info("Stopping WebSocket orderbook service...")
        self.running = False
        
        # Cancel background tasks
        for task in [self._listen_task, self._ping_task, self._health_check_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Close WebSocket connection
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")
        
        self.connected = False
        logger.info("✓ WebSocket orderbook service stopped")
    
    async def _connect_and_subscribe(self):
        """Connect to WebSocket and subscribe to current tokens."""
        try:
            # Configure proxy if provided
            original_proxy_env = {}
            if self.proxy_url:
                original_proxy_env["HTTP_PROXY"] = os.environ.get("HTTP_PROXY")
                original_proxy_env["HTTPS_PROXY"] = os.environ.get("HTTPS_PROXY")
                os.environ["HTTP_PROXY"] = self.proxy_url
                os.environ["HTTPS_PROXY"] = self.proxy_url
                logger.info(f"Connecting to WebSocket via proxy: {self.proxy_url.split('@')[1] if '@' in self.proxy_url else 'configured'}")
            
            try:
                self.websocket = await websockets.connect(self.CLOB_WS_URL)
                self.connected = True
                self._reconnect_attempts = 0
                logger.info(f"✓ Connected to CLOB WebSocket: {self.CLOB_WS_URL}")
                
                # Reset fallback logging flags
                self._fallback_logged = False
                self._websocket_back_logged = False
                
            finally:
                # Restore original environment variables
                if self.proxy_url:
                    if original_proxy_env.get("HTTP_PROXY"):
                        os.environ["HTTP_PROXY"] = original_proxy_env["HTTP_PROXY"]
                    else:
                        os.environ.pop("HTTP_PROXY", None)
                    if original_proxy_env.get("HTTPS_PROXY"):
                        os.environ["HTTPS_PROXY"] = original_proxy_env["HTTPS_PROXY"]
                    else:
                        os.environ.pop("HTTPS_PROXY", None)
            
            # Subscribe to tokens if we have any
            if self.subscribed_tokens:
                await self._resubscribe()
            else:
                logger.info("No tokens to subscribe to yet")
                
        except Exception as e:
            self.connected = False
            logger.error(f"Failed to connect to WebSocket: {e}", exc_info=True)
            raise
    
    async def _resubscribe(self):
        """Re-subscribe to all current tokens (sends full subscription list)."""
        if not self.websocket or not self.connected:
            logger.warning("Cannot subscribe: WebSocket not connected")
            return
        
        if not self.subscribed_tokens:
            logger.debug("No tokens to subscribe to")
            return
        
        subscribe_message = {
            "type": "market",
            "assets_ids": list(self.subscribed_tokens)
        }
        
        try:
            await self.websocket.send(json.dumps(subscribe_message))
            logger.info(f"📡 Re-subscribed to {len(self.subscribed_tokens)} token(s)")
            logger.debug(f"  Subscription: {json.dumps(subscribe_message)}")
            # Wait a moment for initial data
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error re-subscribing: {e}", exc_info=True)
            raise
    
    def subscribe_tokens(self, token_ids: List[str], market_slug: Optional[str] = None):
        """
        Subscribe to additional tokens (non-blocking, will re-subscribe on next iteration).
        
        Args:
            token_ids: List of token IDs to subscribe to
            market_slug: Optional market slug for logging
        """
        new_tokens = []
        for token_id in token_ids:
            if token_id not in self.subscribed_tokens:
                self.subscribed_tokens.add(token_id)
                new_tokens.append(token_id)
                if market_slug:
                    self.token_to_market_slug[token_id] = market_slug
        
        if new_tokens:
            logger.info(f"➕ Added {len(new_tokens)} token(s) to subscription list (will re-subscribe)")
            # Trigger re-subscription in background
            if self.connected and self.running:
                asyncio.create_task(self._resubscribe())
    
    def unsubscribe_tokens(self, token_ids: List[str]):
        """
        Unsubscribe from tokens (non-blocking, will re-subscribe on next iteration).
        
        Args:
            token_ids: List of token IDs to unsubscribe from
        """
        removed_tokens = []
        for token_id in token_ids:
            if token_id in self.subscribed_tokens:
                self.subscribed_tokens.discard(token_id)
                removed_tokens.append(token_id)
                self.token_to_market_slug.pop(token_id, None)
        
        if removed_tokens:
            logger.info(f"➖ Removed {len(removed_tokens)} token(s) from subscription list (will re-subscribe)")
            # Clear cache for removed tokens
            with self._cache_lock:
                for token_id in removed_tokens:
                    self._cache.pop(token_id, None)
            # Trigger re-subscription in background
            if self.connected and self.running:
                asyncio.create_task(self._resubscribe())
    
    def get_orderbook(self, token_id: str) -> Optional[Dict]:
        """
        Get orderbook from cache (thread-safe).
        
        Args:
            token_id: Token ID to get orderbook for
            
        Returns:
            Dict with 'bids' and 'asks' keys, or None if not in cache or stale
        """
        with self._cache_lock:
            if token_id not in self._cache:
                return None
            
            cache_entry = self._cache[token_id]
            last_update = cache_entry.get("last_update")
            
            # Check if cache is stale (older than 30 seconds)
            if last_update:
                age = (datetime.now(timezone.utc) - last_update).total_seconds()
                if age > 30.0:
                    logger.debug(f"Cache entry for {token_id[:20]}... is stale ({age:.1f}s old)")
                    return None
            
            # Return copy of orderbook data
            return {
                "bids": cache_entry["bids"].copy(),
                "asks": cache_entry["asks"].copy(),
            }
    
    def is_connected(self) -> bool:
        """Check if WebSocket is connected and healthy."""
        return self.connected and self.running
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            if not message or not message.strip():
                return
            
            # Update last message time for health checking
            with self._last_message_lock:
                self.last_message_time = datetime.now(timezone.utc)
            
            # Handle plain text control messages (not JSON)
            message_stripped = message.strip()
            if message_stripped in ["INVALID OPERATION", "PING", "PONG"]:
                logger.debug(f"Received WebSocket control message: {message_stripped}")
                # Respond to PING if needed
                if message_stripped == "PING" and self.websocket:
                    await self.websocket.send("PONG")
                return
            
            # Try to parse as JSON
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                # If it's not JSON and not a known control message, log at debug level
                logger.debug(f"Received non-JSON WebSocket message: {message[:100]}")
                return
            
            # Handle array of orderbook snapshots (initial message)
            if isinstance(data, list):
                for orderbook_snapshot in data:
                    asset_id = orderbook_snapshot.get("asset_id")
                    bids = orderbook_snapshot.get("bids", [])
                    asks = orderbook_snapshot.get("asks", [])
                    
                    if asset_id and (bids or asks):
                        await self._update_cache(asset_id, bids, asks)
                return
            
            # Handle single message objects
            event_type = data.get("event_type", "unknown")
            msg_type = data.get("type", "unknown")
            
            # CLOB WebSocket sends orderbook updates with event_type == "book"
            if event_type == "book":
                asset_id = data.get("asset_id")
                bids = data.get("bids", [])
                asks = data.get("sells", [])  # CLOB uses "sells" for asks
                
                if asset_id:
                    await self._update_cache(asset_id, bids, asks)
            
            # Handle price_change events (incremental updates)
            elif event_type == "price_change":
                price_changes = data.get("price_changes", [])
                for change in price_changes:
                    asset_id = change.get("asset_id")
                    best_bid = change.get("best_bid")
                    best_ask = change.get("best_ask")
                    
                    if asset_id and best_bid and best_ask:
                        # Create minimal orderbook from best bid/ask
                        await self._update_cache(asset_id, [{"price": best_bid, "size": 0.0}], [{"price": best_ask, "size": 0.0}])
            
            elif event_type == "subscribed" or msg_type == "subscribed":
                subscribed_ids = data.get("assets_ids") or data.get("asset_id")
                logger.info(f"✓ Confirmed subscription: {subscribed_ids}")
            
            elif event_type == "error" or msg_type == "error":
                error_msg = data.get("message", "Unknown error")
                error_code = data.get("code", "")
                logger.error(f"❌ CLOB WebSocket error: {error_msg} (code: {error_code})")
            
            elif event_type == "ping" or msg_type == "ping":
                # Respond to ping with pong
                if self.websocket:
                    pong_message = {"type": "pong"}
                    await self.websocket.send(json.dumps(pong_message))
                    logger.debug("Responded to ping")
        
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse WebSocket message: {e}, message: {message[:200]}")
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}", exc_info=True)
    
    async def _update_cache(self, token_id: str, bids: List, asks: List):
        """Update orderbook cache with new data (verbose logging)."""
        # Convert to standard format
        bids_formatted = []
        if bids:
            for bid in bids:
                if isinstance(bid, dict):
                    bids_formatted.append([float(bid.get("price", 0)), float(bid.get("size", 0))])
                elif isinstance(bid, list) and len(bid) >= 2:
                    bids_formatted.append([float(bid[0]), float(bid[1])])
        
        asks_formatted = []
        if asks:
            for ask in asks:
                if isinstance(ask, dict):
                    asks_formatted.append([float(ask.get("price", 0)), float(ask.get("size", 0))])
                elif isinstance(ask, list) and len(ask) >= 2:
                    asks_formatted.append([float(ask[0]), float(ask[1])])
        
        # Calculate best bid/ask before updating cache
        best_bid = bids_formatted[0][0] if bids_formatted else None
        best_ask = asks_formatted[0][0] if asks_formatted else None
        spread = best_ask - best_bid if (best_bid is not None and best_ask is not None) else None
        
        # Update cache (thread-safe)
        with self._cache_lock:
            cache_entry = self._cache.get(token_id, {})
            update_count = cache_entry.get("update_count", 0) + 1
            last_bid = cache_entry.get("last_best_bid")
            last_ask = cache_entry.get("last_best_ask")
            
            self._cache[token_id] = {
                "bids": bids_formatted,
                "asks": asks_formatted,
                "last_update": datetime.now(timezone.utc),
                "update_count": update_count,
                "last_best_bid": best_bid,
                "last_best_ask": best_ask,
            }
        
        token_short = token_id[:20] if token_id and len(token_id) > 20 else (token_id or "unknown")
        market_slug = self.token_to_market_slug.get(token_id) or "unknown"
        bid_str = f"{best_bid:.4f}" if best_bid is not None else "N/A"
        ask_str = f"{best_ask:.4f}" if best_ask is not None else "N/A"
        spread_str = f"{spread:.4f}" if spread is not None else "N/A"
        
        # Log updates at DEBUG level to reduce noise (only log significant changes or periodically)
        # Log every 100th update or if price changed significantly (>1%)
        should_log = False
        if update_count % 100 == 0:
            should_log = True
        elif best_bid and best_ask and last_bid and last_ask:
            # Check if price changed significantly (more than 1%)
            if abs(best_bid - last_bid) / last_bid > 0.01:
                should_log = True
            elif abs(best_ask - last_ask) / last_ask > 0.01:
                should_log = True
        
        if should_log:
            logger.debug(
                f"📥 WebSocket update | Market: {market_slug} | Token: {token_short}... | "
                f"Bid: {bid_str} | Ask: {ask_str} | Spread: {spread_str} (update #{update_count})"
            )
        
        # Log full orderbook every 1000th update (reduced frequency)
        if update_count % 1000 == 0:
            logger.info(f"  📊 Full orderbook for {token_short}... (update #{update_count}):")
            logger.info(f"    Top 5 bids: {bids_formatted[:5]}")
            logger.info(f"    Top 5 asks: {asks_formatted[:5]}")
    
    async def _listen_loop(self):
        """Main loop for receiving WebSocket messages."""
        while self.running:
            try:
                if not self.websocket or not self.connected:
                    await asyncio.sleep(1.0)
                    continue
                
                try:
                    message = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=self.health_check_timeout
                    )
                    if message:
                        await self._handle_message(message)
                except asyncio.TimeoutError:
                    # No message received - health check will handle this
                    continue
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("⚠️ WebSocket connection closed")
                    self.connected = False
                    await self._reconnect()
                except Exception as e:
                    logger.error(f"Error receiving WebSocket message: {e}", exc_info=True)
                    self.connected = False
                    await self._reconnect()
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in listen loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)
    
    async def _ping_loop(self):
        """Send periodic pings to keep connection alive."""
        while self.running:
            try:
                await asyncio.sleep(5.0)  # Send ping every 5 seconds
                if self.running and self.websocket and self.connected:
                    ping_message = {"type": "ping"}
                    await self.websocket.send(json.dumps(ping_message))
                    logger.debug("Sent ping to keep connection alive")
            except Exception as e:
                logger.debug(f"Ping task error: {e}")
                await asyncio.sleep(1.0)
    
    async def _health_check_loop(self):
        """Monitor connection health and reconnect if needed."""
        while self.running:
            try:
                await asyncio.sleep(self.health_check_timeout)
                
                if not self.running:
                    break
                
                # Check if we've received messages recently
                with self._last_message_lock:
                    last_message = self.last_message_time
                
                if self.connected and last_message:
                    silence_duration = (datetime.now(timezone.utc) - last_message).total_seconds()
                    if silence_duration > self.health_check_timeout:
                        logger.warning(
                            f"⚠️ WebSocket health check failed: No messages for {silence_duration:.1f}s "
                            f"(timeout: {self.health_check_timeout}s) - reconnecting..."
                        )
                        self.connected = False
                        await self._reconnect()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health check loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)
    
    async def _reconnect(self):
        """Reconnect to WebSocket with exponential backoff."""
        if not self.running:
            return
        
        self._reconnect_attempts += 1
        
        if self._reconnect_attempts > self._max_reconnect_attempts:
            logger.error(f"❌ Max reconnection attempts ({self._max_reconnect_attempts}) reached. Stopping.")
            self.running = False
            return
        
        # Exponential backoff: delay = reconnect_delay * 2^(attempts-1), max 60s
        delay = min(self.reconnect_delay * (2 ** (self._reconnect_attempts - 1)), 60.0)
        logger.info(f"🔄 Reconnecting in {delay:.1f}s (attempt {self._reconnect_attempts}/{self._max_reconnect_attempts})...")
        
        await asyncio.sleep(delay)
        
        try:
            await self._connect_and_subscribe()
            logger.info("✓ Reconnected successfully")
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")
            # Will retry on next health check
