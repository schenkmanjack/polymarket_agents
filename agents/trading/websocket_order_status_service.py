"""
WebSocket-based order status service for real-time order updates.

Provides authenticated WebSocket connection to CLOB User channel for instant
order status updates (fills, cancellations, placements).
"""
import asyncio
import json
import logging
import os
import hmac
import hashlib
import base64
import time
from typing import Dict, List, Optional, Set, Any, Callable, Awaitable
from datetime import datetime, timezone
import threading

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
except ImportError:
    websockets = None
    WebSocketClientProtocol = None

logger = logging.getLogger(__name__)


class WebSocketOrderStatusService:
    """
    WebSocket service for real-time Polymarket order status updates.
    
    Connects to authenticated User channel to receive instant notifications
    about order placements, fills, cancellations, and trades.
    """
    
    # CLOB User WebSocket endpoint (authenticated)
    USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        proxy_url: Optional[str] = None,
        health_check_timeout: float = 14.0,
        reconnect_delay: float = 5.0,
        on_order_update: Optional[Callable[[Dict], Awaitable[None]]] = None,
        on_trade_update: Optional[Callable[[Dict], Awaitable[None]]] = None,
    ):
        """
        Initialize WebSocket order status service.
        
        Args:
            api_key: Polymarket API key
            api_secret: Polymarket API secret
            api_passphrase: Polymarket API passphrase
            proxy_url: Optional proxy URL for VPN/proxy support
            health_check_timeout: Seconds of silence before considering connection dead (default: 14.0)
            reconnect_delay: Initial delay before reconnecting (default: 5.0)
            on_order_update: Optional async callback(order_data) for order status updates
            on_trade_update: Optional async callback(trade_data) for trade/fill updates
        """
        if websockets is None:
            raise ImportError("websockets library not installed. Install with: pip install websockets")
        
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.proxy_url = proxy_url
        self.health_check_timeout = health_check_timeout
        self.reconnect_delay = reconnect_delay
        self.on_order_update = on_order_update
        self.on_trade_update = on_trade_update
        
        # WebSocket connection
        self.websocket: Optional[WebSocketClientProtocol] = None
        self.running = False
        self.connected = False
        
        # Connection health tracking
        self.last_message_time: Optional[datetime] = None
        self._last_message_lock = threading.Lock()
        
        # Reconnection tracking
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        
        # Background tasks
        self._listen_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._health_check_task: Optional[asyncio.Task] = None
        
        # Track known orders for verbose logging
        self._known_orders: Set[str] = set()
    
    def _generate_auth_signature(self, timestamp: str) -> str:
        """
        Generate HMAC signature for WebSocket authentication.
        
        Args:
            timestamp: Unix timestamp as string
            
        Returns:
            Base64-encoded HMAC signature
        """
        message = timestamp + "GET" + "/ws/user"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        return base64.b64encode(signature).decode('utf-8')
    
    async def start(self):
        """Start the WebSocket service (connect and begin listening)."""
        if self.running:
            logger.warning("WebSocket order status service already running")
            return
        
        self.running = True
        logger.info("🚀 Starting WebSocket order status service...")
        await self._connect_and_authenticate()
        
        # Start background tasks
        self._listen_task = asyncio.create_task(self._listen_loop())
        self._ping_task = asyncio.create_task(self._ping_loop())
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        
        logger.info("✓ WebSocket order status service started")
    
    async def stop(self):
        """Stop the WebSocket service."""
        logger.info("Stopping WebSocket order status service...")
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
        logger.info("✓ WebSocket order status service stopped")
    
    async def _connect_and_authenticate(self):
        """Connect to WebSocket and authenticate."""
        try:
            # Configure proxy if provided
            original_proxy_env = {}
            if self.proxy_url:
                original_proxy_env["HTTP_PROXY"] = os.environ.get("HTTP_PROXY")
                original_proxy_env["HTTPS_PROXY"] = os.environ.get("HTTPS_PROXY")
                os.environ["HTTP_PROXY"] = self.proxy_url
                os.environ["HTTPS_PROXY"] = self.proxy_url
                logger.info(f"Connecting to User WebSocket via proxy: {self.proxy_url.split('@')[1] if '@' in self.proxy_url else 'configured'}")
            
            try:
                self.websocket = await websockets.connect(self.USER_WS_URL)
                logger.info(f"✓ Connected to User WebSocket: {self.USER_WS_URL}")
                
                # Authenticate with API credentials
                # CLOB User WebSocket authentication: send subscription message with auth embedded
                # Format based on Polymarket docs: subscription message includes auth object
                timestamp = str(int(time.time()))
                signature = self._generate_auth_signature(timestamp)
                
                # User WebSocket subscription format: type="user" with auth embedded
                subscribe_message = {
                    "type": "user",
                    "markets": [],  # Empty list = subscribe to all user orders/trades
                    "auth": {
                        "apiKey": self.api_key,
                        "secret": self.api_secret,
                        "passphrase": self.api_passphrase,
                        "timestamp": timestamp,
                        "signature": signature,
                    }
                }
                
                logger.info(f"📡 Sending User WebSocket subscription with authentication (timestamp: {timestamp})")
                logger.debug(f"  Auth message (without secrets): type=user, apiKey={self.api_key[:10]}..., timestamp={timestamp}")
                await self.websocket.send(json.dumps(subscribe_message))
                
                # Wait for authentication/subscription response (should come quickly)
                try:
                    response = await asyncio.wait_for(self.websocket.recv(), timeout=2.0)
                    await self._handle_message(response)
                    logger.info("✓ Received authentication/subscription response from User WebSocket")
                except asyncio.TimeoutError:
                    logger.warning("⚠️ No authentication response received within 2 seconds, continuing anyway...")
                except Exception as e:
                    logger.debug(f"Error waiting for auth response: {e}")
                
                self.connected = True
                self._reconnect_attempts = 0
                logger.info("✓ Authenticated with User WebSocket")
                
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
                
        except Exception as e:
            self.connected = False
            logger.error(f"Failed to connect/authenticate to User WebSocket: {e}", exc_info=True)
            raise
    
    def is_connected(self) -> bool:
        """Check if WebSocket is connected and authenticated."""
        return self.connected and self.running
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            if not message or not message.strip():
                return
            
            # Update last message time for health checking
            with self._last_message_lock:
                self.last_message_time = datetime.now(timezone.utc)
            
            data = json.loads(message)
            
            # Log raw message for debugging (first 10 messages)
            if not hasattr(self, '_raw_message_count'):
                self._raw_message_count = 0
            self._raw_message_count += 1
            if self._raw_message_count <= 10:
                logger.info(f"📨 User WebSocket message #{self._raw_message_count}: {json.dumps(data)[:200]}...")
            
            event_type = data.get("event_type", "unknown")
            msg_type = data.get("type", "unknown")
            
            # Handle authentication/subscription response
            if event_type == "auth" or msg_type == "auth" or event_type == "subscribed" or msg_type == "subscribed":
                auth_status = data.get("status", data.get("event_type", "unknown"))
                if auth_status in ["success", "ok", "subscribed"]:
                    logger.info("✅ User WebSocket authentication/subscription successful")
                    logger.info(f"  Response: {json.dumps(data)[:200]}...")
                elif auth_status == "error" or "error" in str(data).lower():
                    logger.error(f"❌ User WebSocket authentication/subscription failed: {data}")
                else:
                    logger.info(f"📨 User WebSocket auth/subscription response: {data}")
            
            # Handle order updates (placements, cancellations, fills)
            elif event_type == "order" or msg_type == "order":
                order_id = data.get("id") or data.get("order_id") or data.get("orderID")
                order_status = data.get("status", "unknown")
                
                logger.info(
                    f"📋 Order update | ID: {order_id[:20] if order_id and len(order_id) > 20 else order_id}... | "
                    f"Status: {order_status} | "
                    f"Size: {data.get('size', 'N/A')} | "
                    f"Price: {data.get('price', 'N/A')}"
                )
                
                # Track known orders for verbose logging
                if order_id:
                    is_new = order_id not in self._known_orders
                    self._known_orders.add(order_id)
                    if is_new:
                        logger.info(f"  🆕 New order detected: {order_id[:20]}...")
                
                # Call callback if provided
                if self.on_order_update:
                    try:
                        await self.on_order_update(data)
                    except Exception as e:
                        logger.error(f"Error in on_order_update callback: {e}", exc_info=True)
            
            # Handle trade/fill updates
            elif event_type == "trade" or msg_type == "trade":
                trade_id = data.get("id") or data.get("trade_id")
                order_id = data.get("order_id") or data.get("orderID")
                size = data.get("size", 0)
                price = data.get("price", 0)
                
                logger.info(
                    f"💰 TRADE/FILL | Order: {order_id[:20] if order_id and len(order_id) > 20 else order_id}... | "
                    f"Size: {size} | Price: {price:.4f} | "
                    f"Trade ID: {trade_id[:20] if trade_id and len(trade_id) > 20 else trade_id}..."
                )
                
                # Call callback if provided
                if self.on_trade_update:
                    try:
                        await self.on_trade_update(data)
                    except Exception as e:
                        logger.error(f"Error in on_trade_update callback: {e}", exc_info=True)
            
            # Handle ping/pong
            elif event_type == "ping" or msg_type == "ping":
                if self.websocket:
                    pong_message = {"type": "pong"}
                    await self.websocket.send(json.dumps(pong_message))
                    logger.debug("Responded to ping")
            
            elif event_type == "pong" or msg_type == "pong":
                logger.debug("Received pong")
            
            # Handle errors
            elif event_type == "error" or msg_type == "error":
                error_msg = data.get("message", "Unknown error")
                error_code = data.get("code", "")
                logger.error(f"❌ User WebSocket error: {error_msg} (code: {error_code})")
                logger.error(f"  Full error data: {data}")
            
            else:
                # Log unknown messages (first 20 only)
                if self._raw_message_count <= 20:
                    logger.info(f"Received unknown event_type '{event_type}', type '{msg_type}': {str(data)[:300]}")
        
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse User WebSocket message: {e}, message: {message[:200]}")
        except Exception as e:
            logger.error(f"Error handling User WebSocket message: {e}", exc_info=True)
    
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
                    logger.warning("⚠️ User WebSocket connection closed")
                    self.connected = False
                    await self._reconnect()
                except Exception as e:
                    logger.error(f"Error receiving User WebSocket message: {e}", exc_info=True)
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
                    logger.debug("Sent ping to keep User WebSocket connection alive")
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
                            f"⚠️ User WebSocket health check failed: No messages for {silence_duration:.1f}s "
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
        logger.info(f"🔄 Reconnecting User WebSocket in {delay:.1f}s (attempt {self._reconnect_attempts}/{self._max_reconnect_attempts})...")
        
        await asyncio.sleep(delay)
        
        try:
            await self._connect_and_authenticate()
            logger.info("✓ Reconnected successfully")
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")
            # Will retry on next health check
