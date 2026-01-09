"""
WebSocket client for real-time Polymarket orderbook streaming.
Uses Polymarket's Real-Time Data Socket (RTDS) for live orderbook updates.
"""
import json
import asyncio
import logging
from typing import List, Optional, Callable, Dict, Any
import websockets
from datetime import datetime

logger = logging.getLogger(__name__)


class OrderbookStream:
    """
    WebSocket client for streaming real-time orderbook data from Polymarket RTDS.
    
    RTDS Documentation: https://docs.polymarket.com/developers/RTDS/RTDS-overview
    """
    
    # Try both RTDS and CLOB WebSocket endpoints
    RTDS_URL = "wss://ws-live-data.polymarket.com"
    CLOB_WS_URL = "wss://clob.polymarket.com/ws"  # Alternative endpoint
    
    def __init__(self, on_orderbook_update: Optional[Callable] = None, api_credentials: Optional[Dict[str, str]] = None):
        """
        Initialize the orderbook stream.
        
        Args:
            on_orderbook_update: Callback function called when orderbook updates are received.
                                 Should accept (token_id, orderbook_data) as arguments.
            api_credentials: Optional dict with 'api_key', 'api_secret', 'api_passphrase' for authentication.
        """
        self.on_orderbook_update = on_orderbook_update
        self.api_credentials = api_credentials
        self.websocket = None
        self.running = False
        self.subscribed_tokens: set = set()
    
    async def connect(self):
        """Connect to the RTDS WebSocket."""
        try:
            self.websocket = await websockets.connect(self.RTDS_URL)
            logger.info(f"Connected to RTDS: {self.RTDS_URL}")
        except Exception as e:
            logger.error(f"Failed to connect to RTDS: {e}")
            raise
    
    async def subscribe_to_orderbook(self, token_id: str):
        """
        Subscribe to orderbook updates for a specific token.
        
        Args:
            token_id: The CLOB token ID to subscribe to
        """
        if not self.websocket:
            await self.connect()
        
        # Build subscription message with authentication if available
        subscribe_message = {
            "type": "subscribe",
            "channel": "orderbook",
            "id": token_id,
        }
        
        # Add API credentials if available (RTDS might require auth)
        if self.api_credentials:
            subscribe_message["auth"] = {
                "key": self.api_credentials.get("api_key"),
                "secret": self.api_credentials.get("api_secret"),
                "passphrase": self.api_credentials.get("api_passphrase"),
            }
            logger.debug(f"Adding API credentials to subscription")
        
        try:
            logger.info(f"Sending subscription for token: {token_id[:20]}...")
            logger.debug(f"Subscription message (without secrets): {json.dumps({k: v for k, v in subscribe_message.items() if k != 'auth'})}")
            
            await self.websocket.send(json.dumps(subscribe_message))
            self.subscribed_tokens.add(token_id)
            logger.info(f"✓ Subscription sent for token: {token_id[:20]}...")
            
            # Wait briefly to see if we get a response
            import asyncio
            await asyncio.sleep(0.1)
            
        except Exception as e:
            logger.error(f"Error subscribing to {token_id}: {e}", exc_info=True)
            raise
    
    async def unsubscribe_from_orderbook(self, token_id: str):
        """Unsubscribe from orderbook updates for a token."""
        if not self.websocket:
            return
        
        unsubscribe_message = {
            "type": "unsubscribe",
            "channel": "orderbook",
            "id": token_id,
        }
        
        await self.websocket.send(json.dumps(unsubscribe_message))
        self.subscribed_tokens.discard(token_id)
        logger.info(f"Unsubscribed from orderbook for token: {token_id}")
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "unknown")
            
            # Log first few messages to understand format
            if not hasattr(self, '_message_count'):
                self._message_count = 0
            self._message_count += 1
            if self._message_count <= 10:
                logger.info(f"📨 WebSocket message #{self._message_count}: type={msg_type}, keys={list(data.keys())}")
                if self._message_count <= 3:
                    logger.info(f"   Full message: {str(data)[:500]}")
            
            # Handle different message types
            if msg_type == "orderbook":
                token_id = data.get("id") or data.get("asset_id")
                orderbook_data = data.get("data", {})
                
                if token_id and self.on_orderbook_update:
                    await self.on_orderbook_update(token_id, orderbook_data)
                else:
                    logger.warning(f"Orderbook message missing token_id or callback: token_id={token_id}, has_callback={bool(self.on_orderbook_update)}")
            
            elif msg_type == "error":
                error_msg = data.get('message', 'Unknown error')
                error_code = data.get('code', '')
                logger.error(f"RTDS error: {error_msg} (code: {error_code})")
                logger.error(f"Full error data: {data}")
            
            elif msg_type == "subscribed":
                subscribed_id = data.get('id') or data.get('asset_id') or (data.get('assets_ids', [None])[0] if data.get('assets_ids') else None)
                logger.info(f"✓ Successfully subscribed: {subscribed_id}")
                logger.debug(f"Subscription confirmation: {data}")
            
            elif msg_type == "unsubscribed":
                logger.info(f"Successfully unsubscribed: {data}")
            
            elif msg_type == "ping":
                # Respond to ping with pong
                pong_message = {"type": "pong"}
                await self.websocket.send(json.dumps(pong_message))
                logger.debug("Responded to ping with pong")
            
            else:
                # Log any other message types we receive (for debugging)
                if self._message_count <= 20:  # Log first 20 unknown messages
                    logger.info(f"Received message type '{msg_type}': {str(data)[:200]}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}, message: {message[:200]}")
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
    
    async def listen(self):
        """Listen for incoming messages."""
        if not self.websocket:
            await self.connect()
        
        self.running = True
        logger.info("Starting to listen for orderbook updates...")
        
        try:
            async for message in self.websocket:
                if not self.running:
                    break
                await self._handle_message(message)
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"WebSocket connection closed: {e}")
            logger.warning("Will attempt to reconnect on next check")
            self.running = False
        except Exception as e:
            logger.error(f"Error in listen loop: {e}", exc_info=True)
            self.running = False
    
    async def disconnect(self):
        """Disconnect from the WebSocket."""
        self.running = False
        if self.websocket:
            await self.websocket.close()
            logger.info("Disconnected from RTDS")
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()


class OrderbookLogger:
    """
    Service that combines WebSocket streaming with database logging.
    """
    
    def __init__(self, db, token_ids: List[str], market_info: Optional[Dict[str, Dict]] = None):
        """
        Initialize the orderbook logger.
        
        Args:
            db: OrderbookDatabase instance
            token_ids: List of token IDs to monitor
            market_info: Optional dict mapping token_id to market metadata
                        {token_id: {"market_id": "...", "market_question": "...", "outcome": "..."}}
        """
        self.db = db
        self.token_ids = token_ids
        self.market_info = market_info or {}
        self._update_count = {}  # Track update counts per token
        self.stream = None
    
    async def _on_orderbook_update(self, token_id: str, orderbook_data: Dict[str, Any]):
        """
        Callback for orderbook updates - saves to database.
        
        Args:
            token_id: The token ID
            orderbook_data: Orderbook data from RTDS
        """
        try:
            # Log first update to confirm we're receiving data
            if token_id not in self._update_count:
                logger.info(f"📥 Received first orderbook update for token {token_id[:20]}...")
                logger.debug(f"Orderbook data keys: {list(orderbook_data.keys()) if isinstance(orderbook_data, dict) else 'N/A'}")
            
            # Parse orderbook data
            # RTDS format may vary, but typically includes bids/asks
            bids = orderbook_data.get("bids", [])
            asks = orderbook_data.get("asks", [])
            
            # Handle different RTDS message formats
            # Sometimes data is nested under 'data' key
            if not bids and not asks:
                if isinstance(orderbook_data, dict) and "data" in orderbook_data:
                    nested_data = orderbook_data["data"]
                    bids = nested_data.get("bids", [])
                    asks = nested_data.get("asks", [])
            
            # Convert to list of [price, size] tuples if needed
            if bids and isinstance(bids[0], dict):
                bids = [[float(b["price"]), float(b["size"])] for b in bids]
            elif bids and isinstance(bids[0], list):
                # Already in [price, size] format
                bids = [[float(b[0]), float(b[1])] for b in bids if len(b) >= 2]
            
            if asks and isinstance(asks[0], dict):
                asks = [[float(a["price"]), float(a["size"])] for a in asks]
            elif asks and isinstance(asks[0], list):
                # Already in [price, size] format
                asks = [[float(a[0]), float(a[1])] for a in asks if len(a) >= 2]
            
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
            
            # Save to database
            snapshot = self.db.save_snapshot(
                token_id=token_id,
                bids=bids,
                asks=asks,
                market_id=market_meta.get("market_id"),
                market_question=market_meta.get("market_question"),
                outcome=market_meta.get("outcome"),
                metadata={"source": "rtds", "raw_data": orderbook_data},
                market_start_date=market_meta.get("market_start_date"),
                market_end_date=market_meta.get("market_end_date"),
                asset_type=asset_type,
            )
            
            # Get market info for outcome_price
            market_meta = self.market_info.get(token_id, {})
            outcome_price = market_meta.get("outcome_price")
            
            # Extract last_trade_price from orderbook_data if available
            last_trade_price = orderbook_data.get("last_trade_price")
            if last_trade_price:
                try:
                    last_trade_price = float(last_trade_price)
                except:
                    last_trade_price = None
            
            # Calculate market price
            market_price = None
            if outcome_price is not None:
                market_price = outcome_price
            elif last_trade_price is not None:
                market_price = last_trade_price
            elif bids and asks:
                best_bid = bids[0][0] if bids else None
                best_ask = asks[0][0] if asks else None
                if best_bid and best_ask:
                    market_price = (best_bid + best_ask) / 2
            
            # Log periodically (every 10th update) to avoid log spam
            self._update_count[token_id] = self._update_count.get(token_id, 0) + 1
            if self._update_count[token_id] % 10 == 1:
                # Get best bid/ask from top of orderbook (may be stale)
                best_bid_raw = bids[0][0] if bids else None
                best_ask_raw = asks[0][0] if asks else None
                
                # Find best bid/ask near actual market price (like UI does)
                from agents.polymarket.orderbook_utils import get_best_bid_ask_near_price
                
                reference_price = outcome_price or last_trade_price or market_price
                best_bid_near, best_ask_near = None, None
                if reference_price and bids and asks:
                    # Convert to dict format if needed
                    bids_dict = bids if isinstance(bids[0], dict) else [{"price": str(b[0]), "size": str(b[1])} for b in bids]
                    asks_dict = asks if isinstance(asks[0], dict) else [{"price": str(a[0]), "size": str(a[1])} for a in asks]
                    best_bid_near, best_ask_near = get_best_bid_ask_near_price(
                        bids_dict, asks_dict, reference_price, max_spread_pct=0.15
                    )
                
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
                
                logger.info(f"✓ Saved orderbook snapshot #{self._update_count[token_id]} (DB ID: {snapshot.id}) for token {token_id[:20]}... | {price_info}")
            
        except Exception as e:
            logger.error(f"❌ Error saving orderbook update for {token_id}: {e}", exc_info=True)
            # Log the orderbook data structure for debugging
            logger.error(f"Orderbook data structure: {type(orderbook_data)}, keys: {list(orderbook_data.keys()) if isinstance(orderbook_data, dict) else 'N/A'}")
            logger.error(f"Full orderbook data: {orderbook_data}")
    
    async def start(self):
        """Start logging orderbook updates."""
        # Get API credentials from Polymarket if monitoring script wallet key is available
        api_credentials = None
        import os
        # Use separate wallet key for monitoring script (not trading script)
        monitoring_wallet_key = os.getenv("POLYGON_WALLET_MONITORING_SCRIPT_PRIVATE_KEY")
        if monitoring_wallet_key:
            try:
                # Temporarily set POLYGON_WALLET_PRIVATE_KEY to get credentials
                # (Polymarket class reads from env var)
                original_key = os.environ.get("POLYGON_WALLET_PRIVATE_KEY")
                os.environ["POLYGON_WALLET_PRIVATE_KEY"] = monitoring_wallet_key
                try:
                    from agents.polymarket.polymarket import Polymarket
                    pm = Polymarket()
                    if hasattr(pm, 'credentials') and pm.credentials:
                        api_credentials = {
                            "api_key": pm.credentials.api_key,
                            "api_secret": pm.credentials.api_secret,
                            "api_passphrase": pm.credentials.api_passphrase,
                        }
                        logger.info("✓ Using API credentials for WebSocket authentication (monitoring script wallet)")
                finally:
                    # Restore original key (or remove if it wasn't set)
                    if original_key:
                        os.environ["POLYGON_WALLET_PRIVATE_KEY"] = original_key
                    else:
                        os.environ.pop("POLYGON_WALLET_PRIVATE_KEY", None)
            except Exception as e:
                logger.warning(f"Could not get API credentials: {e}, trying without auth")
        
        self.stream = OrderbookStream(
            on_orderbook_update=self._on_orderbook_update,
            api_credentials=api_credentials
        )
        
        await self.stream.connect()
        
        # Subscribe to all tokens
        for token_id in self.token_ids:
            await self.stream.subscribe_to_orderbook(token_id)
        
        # Start listening
        await self.stream.listen()
    
    async def stop(self):
        """Stop logging."""
        if self.stream:
            await self.stream.disconnect()


async def run_orderbook_logger(token_ids: List[str], db_path: Optional[str] = None):
    """
    Convenience function to run the orderbook logger.
    
    Args:
        token_ids: List of token IDs to monitor
        db_path: Optional path to SQLite database (defaults to ./orderbook.db)
    """
    from agents.polymarket.orderbook_db import OrderbookDatabase
    
    db = OrderbookDatabase(database_url=None if db_path is None else f"sqlite:///{db_path}")
    logger_service = OrderbookLogger(db, token_ids)
    
    try:
        await logger_service.start()
    except KeyboardInterrupt:
        logger.info("Stopping orderbook logger...")
        await logger_service.stop()
    except Exception as e:
        logger.error(f"Error in orderbook logger: {e}")
        await logger_service.stop()
        raise

