"""
Test script for WebSocket orderbook streaming with VPN/proxy support.

This script tests real-time orderbook updates via WebSocket instead of HTTP polling.
Much faster than HTTP polling - updates arrive in real-time as they happen.

IMPORTANT: WebSocket proxy support may be limited. The websockets library supports
proxy via environment variables (HTTP_PROXY/HTTPS_PROXY), but HTTP proxies with
authentication may require additional setup. For best results with authenticated proxies,
consider using SOCKS5 proxies or a proxy tunnel.

Usage:
    # Test without proxy (direct connection)
    python scripts/python/test_websocket_orderbook.py --token TOKEN_ID
    
    # Test with proxy from environment variables
    python scripts/python/test_websocket_orderbook.py --token TOKEN_ID
    
    # Test with specific proxy (may not work with HTTP auth proxies)
    python scripts/python/test_websocket_orderbook.py --token TOKEN_ID --proxy "http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001"
    
    # Test multiple tokens
    python scripts/python/test_websocket_orderbook.py --token TOKEN_ID1 --token TOKEN_ID2
    
    # Disable proxy explicitly
    python scripts/python/test_websocket_orderbook.py --token TOKEN_ID --no-proxy
"""
import asyncio
import json
import logging
import sys
import os
import argparse
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True
)
logging.getLogger().handlers[0].stream = sys.stdout
logger = logging.getLogger(__name__)

# Import proxy configuration
from agents.utils.proxy_config import configure_proxy, get_proxy_from_env, get_proxy_dict

# Import websockets library
try:
    import websockets
    from websockets.client import WebSocketClientProtocol
except ImportError:
    logger.error("websockets library not installed. Install with: pip install websockets")
    sys.exit(1)


class WebSocketOrderbookStream:
    """
    WebSocket client for real-time Polymarket orderbook streaming with proxy support.
    
    Uses CLOB WebSocket endpoint for orderbook updates (not RTDS which is for global events).
    """
    
    # CLOB WebSocket endpoint for orderbook data (high-frequency)
    CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    # RTDS endpoint (for global events, not orderbooks)
    RTDS_URL = "wss://ws-live-data.polymarket.com"
    
    def __init__(self, token_ids: List[str], proxy_url: Optional[str] = None, market_slugs: Optional[List[str]] = None, api_credentials: Optional[Dict[str, str]] = None):
        """
        Initialize WebSocket orderbook stream.
        
        Args:
            token_ids: List of CLOB token IDs to subscribe to
            proxy_url: Optional proxy URL for VPN/proxy support
            market_slugs: Optional list of market slugs (for RTDS subscription format)
            api_credentials: Optional dict with 'api_key', 'api_secret', 'api_passphrase' for authentication
        """
        self.token_ids = token_ids
        self.market_slugs = market_slugs or []
        self.proxy_url = proxy_url
        self.api_credentials = api_credentials
        self.websocket: Optional[WebSocketClientProtocol] = None
        self.running = False
        self.update_count = 0
        self.last_update_time = {}
    
    async def connect(self):
        """Connect to RTDS WebSocket with optional proxy."""
        try:
            # Configure proxy if provided
            # websockets library supports proxy via environment variables or proxy parameter
            # For HTTP proxies with auth, we set environment variables temporarily
            original_proxy_env = {}
            if self.proxy_url:
                # Set environment variables for websockets to pick up
                # websockets library reads HTTP_PROXY and HTTPS_PROXY
                original_proxy_env["HTTP_PROXY"] = os.environ.get("HTTP_PROXY")
                original_proxy_env["HTTPS_PROXY"] = os.environ.get("HTTPS_PROXY")
                os.environ["HTTP_PROXY"] = self.proxy_url
                os.environ["HTTPS_PROXY"] = self.proxy_url
                logger.info(f"Connecting to WebSocket via proxy: {self.proxy_url.split('@')[1] if '@' in self.proxy_url else 'configured'}")
            else:
                logger.info("Connecting to WebSocket without proxy")
            
            try:
                # Use CLOB WebSocket endpoint for orderbook data
                # websockets.connect() will automatically use HTTP_PROXY/HTTPS_PROXY if set
                self.websocket = await websockets.connect(
                    self.CLOB_WS_URL,
                    # Note: websockets library may not directly support proxy parameter
                    # but it reads from HTTP_PROXY/HTTPS_PROXY environment variables
                )
                logger.info(f"✓ Connected to CLOB WebSocket: {self.CLOB_WS_URL}")
            finally:
                # Restore original environment variables
                if self.proxy_url:
                    if original_proxy_env["HTTP_PROXY"]:
                        os.environ["HTTP_PROXY"] = original_proxy_env["HTTP_PROXY"]
                    else:
                        os.environ.pop("HTTP_PROXY", None)
                    if original_proxy_env["HTTPS_PROXY"]:
                        os.environ["HTTPS_PROXY"] = original_proxy_env["HTTPS_PROXY"]
                    else:
                        os.environ.pop("HTTPS_PROXY", None)
        except Exception as e:
            logger.error(f"Failed to connect to RTDS: {e}", exc_info=True)
            raise
    
    async def subscribe(self, token_id: str, market_slug: Optional[str] = None):
        """Subscribe to orderbook updates for a token using CLOB WebSocket format.
        
        Args:
            token_id: CLOB token ID (asset_id)
            market_slug: Optional market slug (for logging, not used in CLOB subscription)
        """
        if not self.websocket:
            await self.connect()
        
        # CLOB WebSocket uses simple format: {"type": "market", "assets_ids": [token_ids]}
        # We'll subscribe to all tokens at once after collecting them
        # This method is called per token, so we just track them
        pass  # Actual subscription happens in subscribe_all()
    
    async def subscribe_all(self):
        """Subscribe to all tokens at once using CLOB format."""
        if not self.websocket:
            await self.connect()
        
        # CLOB WebSocket subscription format
        subscribe_message = {
            "type": "market",
            "assets_ids": self.token_ids
        }
        
        # Note: CLOB WebSocket typically doesn't require auth for public orderbook data
        # But we can add it if needed
        if self.api_credentials:
            # Some CLOB endpoints may accept auth in headers or initial message
            logger.info("  Note: CLOB WebSocket may not require auth for public orderbook data")
        
        try:
            await self.websocket.send(json.dumps(subscribe_message))
            logger.info(f"✓ CLOB subscription sent for {len(self.token_ids)} token(s)")
            logger.info(f"  Subscription message: {json.dumps(subscribe_message)}")
            # Wait for confirmation and initial data
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.error(f"Error subscribing to CLOB WebSocket: {e}", exc_info=True)
            raise
    
    async def handle_message(self, message: str):
        """Handle incoming WebSocket message from CLOB WebSocket."""
        try:
            # Skip empty messages
            if not message or not message.strip():
                return
            
            # Log raw message for debugging (first 5 messages)
            if not hasattr(self, '_raw_message_count'):
                self._raw_message_count = 0
            self._raw_message_count += 1
            if self._raw_message_count <= 5:
                logger.info(f"📨 Raw message #{self._raw_message_count}: {message[:300]}...")
            
            data = json.loads(message)
            
            # Handle array of orderbook snapshots (first message)
            if isinstance(data, list):
                for orderbook_snapshot in data:
                    asset_id = orderbook_snapshot.get("asset_id")
                    bids = orderbook_snapshot.get("bids", [])
                    asks = orderbook_snapshot.get("asks", [])
                    
                    if asset_id and (bids or asks):
                        # Convert CLOB format to our standard format
                        orderbook_data = {
                            "bids": [[float(b.get("price", 0)), float(b.get("size", 0))] for b in bids] if bids else [],
                            "asks": [[float(a.get("price", 0)), float(a.get("size", 0))] for a in asks] if asks else [],
                        }
                        await self.on_orderbook_update(asset_id, orderbook_data)
                return
            
            # Handle single message objects
            event_type = data.get("event_type", "unknown")
            msg_type = data.get("type", "unknown")
            
            # Log message types for debugging (first 10)
            if self._raw_message_count <= 10:
                logger.info(f"Message event_type: '{event_type}', type: '{msg_type}', keys: {list(data.keys())}")
            
            # CLOB WebSocket sends orderbook updates with event_type == "book" (full snapshot)
            if event_type == "book":
                asset_id = data.get("asset_id")
                bids = data.get("bids", [])
                asks = data.get("sells", [])  # CLOB uses "sells" for asks
                
                if asset_id:
                    # Convert CLOB format to our standard format
                    orderbook_data = {
                        "bids": [[float(b.get("price", 0)), float(b.get("size", 0))] for b in bids] if bids else [],
                        "asks": [[float(s.get("price", 0)), float(s.get("size", 0))] for s in asks] if asks else [],
                    }
                    await self.on_orderbook_update(asset_id, orderbook_data)
            
            # Handle price_change events (incremental updates with best_bid/best_ask)
            elif event_type == "price_change":
                price_changes = data.get("price_changes", [])
                for change in price_changes:
                    asset_id = change.get("asset_id")
                    best_bid = change.get("best_bid")
                    best_ask = change.get("best_ask")
                    
                    if asset_id and best_bid and best_ask:
                        # Create minimal orderbook from best bid/ask
                        orderbook_data = {
                            "bids": [[float(best_bid), 0.0]],  # Size not provided in price_change
                            "asks": [[float(best_ask), 0.0]],
                        }
                        await self.on_orderbook_update(asset_id, orderbook_data)
            
            elif event_type == "subscribed" or msg_type == "subscribed":
                subscribed_ids = data.get("assets_ids") or data.get("asset_id")
                logger.info(f"✓ Confirmed subscription: {subscribed_ids}")
            
            elif event_type == "error" or msg_type == "error":
                error_msg = data.get("message", "Unknown error")
                error_code = data.get("code", "")
                logger.error(f"CLOB WebSocket error: {error_msg} (code: {error_code})")
                logger.error(f"  Full error data: {data}")
            
            elif event_type == "ping" or msg_type == "ping":
                # Respond to ping with pong
                pong_message = {"type": "pong"}
                await self.websocket.send(json.dumps(pong_message))
                logger.debug("Responded to ping")
            
            else:
                # Log unknown messages (first 10 only)
                if self._raw_message_count <= 10:
                    logger.info(f"Received event_type '{event_type}', type '{msg_type}': {str(data)[:300]}")
        
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}, message: {message[:200]}")
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
    
    async def on_orderbook_update(self, token_id: str, orderbook_data: Dict[str, Any]):
        """Process orderbook update from CLOB WebSocket."""
        self.update_count += 1
        
        # Calculate time since last update
        now = datetime.now(timezone.utc)
        time_since_last = None
        if token_id in self.last_update_time:
            time_since_last = (now - self.last_update_time[token_id]).total_seconds()
        self.last_update_time[token_id] = now
        
        # Extract bids and asks (CLOB format: already in [price, size] format)
        bids = orderbook_data.get("bids", [])
        asks = orderbook_data.get("asks", [])
        
        # Ensure they're in [price, size] format
        if bids and isinstance(bids[0], dict):
            bids = [[float(b.get("price", 0)), float(b.get("size", 0))] for b in bids]
        elif bids and isinstance(bids[0], list):
            bids = [[float(b[0]), float(b[1])] for b in bids if len(b) >= 2]
        
        if asks and isinstance(asks[0], dict):
            asks = [[float(a.get("price", 0)), float(a.get("size", 0))] for a in asks]
        elif asks and isinstance(asks[0], list):
            asks = [[float(a[0]), float(a[1])] for a in asks if len(a) >= 2]
        
        # Get best bid/ask (first element is best)
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        spread = best_ask - best_bid if (best_bid and best_ask) else None
        
        # Log update
        time_info = f" (Δt={time_since_last:.3f}s)" if time_since_last else ""
        token_short = token_id[:20] if len(token_id) > 20 else token_id
        spread_str = f"{spread:.4f}" if spread else "N/A"
        logger.info(
            f"📥 Update #{self.update_count} for {token_short} | "
            f"Bid: {best_bid:.4f} | Ask: {best_ask:.4f} | Spread: {spread_str}{time_info}"
        )
        
        # Log full orderbook every 10th update
        if self.update_count % 10 == 0:
            logger.info(f"  Top 3 bids: {bids[:3] if len(bids) >= 3 else bids}")
            logger.info(f"  Top 3 asks: {asks[:3] if len(asks) >= 3 else asks}")
    
    async def listen(self):
        """Listen for incoming messages."""
        if not self.websocket:
            await self.connect()
        
        self.running = True
        logger.info("Starting to listen for orderbook updates...")
        
        # Start ping task to keep connection alive (RTDS requires periodic PING)
        async def ping_task():
            while self.running:
                try:
                    await asyncio.sleep(5.0)  # Send ping every 5 seconds
                    if self.running and self.websocket:
                        ping_message = {"type": "ping"}
                        await self.websocket.send(json.dumps(ping_message))
                        logger.debug("Sent ping to keep connection alive")
                except Exception as e:
                    logger.debug(f"Ping task error: {e}")
                    break
        
        ping_task_handle = asyncio.create_task(ping_task())
        
        try:
            # Try to receive messages with timeout to detect if connection is alive
            while self.running:
                try:
                    # Use asyncio.wait_for to detect if we're receiving messages
                    message = await asyncio.wait_for(self.websocket.recv(), timeout=10.0)
                    if message:  # Only process non-empty messages
                        await self.handle_message(message)
                except asyncio.TimeoutError:
                    # No message received in 10 seconds - log but continue
                    logger.debug("⏳ No messages received in last 10 seconds (connection still alive)")
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"WebSocket connection closed: {e}")
            self.running = False
        except Exception as e:
            logger.error(f"Error in listen loop: {e}", exc_info=True)
            self.running = False
        finally:
            ping_task_handle.cancel()
            try:
                await ping_task_handle
            except asyncio.CancelledError:
                pass
    
    async def disconnect(self):
        """Disconnect from WebSocket."""
        self.running = False
        if self.websocket:
            await self.websocket.close()
            logger.info("Disconnected from RTDS")


async def test_websocket_orderbook(token_ids: List[str], proxy_url: Optional[str] = None, market_slugs: Optional[List[str]] = None, api_credentials: Optional[Dict[str, str]] = None):
    """
    Test WebSocket orderbook streaming.
    
    Args:
        token_ids: List of token IDs to subscribe to
        proxy_url: Optional proxy URL
        market_slugs: Optional list of market slugs
        api_credentials: Optional API credentials for authentication
    """
    logger.info("=" * 80)
    logger.info("WEBSOCKET ORDERBOOK STREAM TEST")
    logger.info("=" * 80)
    logger.info(f"Tokens to monitor: {len(token_ids)}")
    for i, token_id in enumerate(token_ids, 1):
        logger.info(f"  {i}. {token_id[:20]}...")
    if market_slugs:
        logger.info(f"Market slugs: {market_slugs}")
    if api_credentials:
        logger.info(f"Authentication: Enabled (API key: {api_credentials.get('api_key', '')[:10]}...)")
    else:
        logger.info("Authentication: Disabled (no credentials provided)")
    logger.info("=" * 80)
    
    stream = WebSocketOrderbookStream(token_ids, proxy_url=proxy_url, market_slugs=market_slugs, api_credentials=api_credentials)
    
    try:
        # Connect
        await stream.connect()
        
        # Subscribe to all tokens at once (CLOB format)
        await stream.subscribe_all()
        
        logger.info(f"✓ Subscribed to {len(token_ids)} token(s) via CLOB WebSocket")
        logger.info("Listening for updates... (Press Ctrl+C to stop)")
        logger.info("")
        
        # Start listening
        await stream.listen()
    
    except KeyboardInterrupt:
        logger.info("\nReceived interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Error in test: {e}", exc_info=True)
    finally:
        await stream.disconnect()
        logger.info("Test completed")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test WebSocket orderbook streaming with VPN/proxy support"
    )
    parser.add_argument(
        "--token",
        action="append",
        required=True,
        help="CLOB token ID to subscribe to (can specify multiple times)"
    )
    parser.add_argument(
        "--market-slug",
        action="append",
        default=None,
        help="Market slug for RTDS subscription (can specify multiple times, should match token order)"
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="Proxy URL (e.g., 'http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001'). "
             "If not provided, will auto-detect from environment variables."
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Disable proxy (use direct connection)"
    )
    parser.add_argument(
        "--use-auth",
        action="store_true",
        help="Use API credentials from POLYGON_WALLET_PRIVATE_KEY for authentication"
    )
    
    args = parser.parse_args()
    
    # Determine proxy URL
    proxy_url = None
    if args.no_proxy:
        logger.info("Proxy disabled (--no-proxy flag)")
    elif args.proxy:
        proxy_url = args.proxy
        logger.info(f"Using provided proxy URL")
    else:
        # Auto-detect from environment
        proxy_url = get_proxy_from_env()
        if proxy_url:
            logger.info(f"Auto-detected proxy from environment variables")
        else:
            logger.info("No proxy configured (using direct connection)")
    
    # Verify proxy if configured
    if proxy_url:
        from agents.utils.proxy_config import verify_proxy_ip
        logger.info("Verifying proxy connection...")
        ip_info = verify_proxy_ip(proxy_url)
        if ip_info:
            logger.info("✓ Proxy verified and working")
        else:
            logger.warning("⚠ Proxy verification failed, but continuing anyway...")
    
    # Get API credentials if requested
    api_credentials = None
    if args.use_auth:
        logger.info("Attempting to get API credentials...")
        try:
            from agents.polymarket.polymarket import Polymarket
            pm = Polymarket()
            if hasattr(pm, 'credentials') and pm.credentials:
                api_credentials = {
                    "api_key": pm.credentials.api_key,
                    "api_secret": pm.credentials.api_secret,
                    "api_passphrase": pm.credentials.api_passphrase,
                }
                logger.info("✓ API credentials obtained from wallet key")
            else:
                logger.warning("⚠ No credentials found in Polymarket instance")
        except Exception as e:
            logger.warning(f"⚠ Could not get API credentials: {e}")
            logger.info("  Continuing without authentication...")
    
    # Run test
    try:
        asyncio.run(test_websocket_orderbook(args.token, proxy_url=proxy_url, market_slugs=args.market_slug, api_credentials=api_credentials))
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
