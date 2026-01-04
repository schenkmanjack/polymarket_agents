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
    
    RTDS_URL = "wss://ws-live-data.polymarket.com"
    
    def __init__(self, on_orderbook_update: Optional[Callable] = None):
        """
        Initialize the orderbook stream.
        
        Args:
            on_orderbook_update: Callback function called when orderbook updates are received.
                                 Should accept (token_id, orderbook_data) as arguments.
        """
        self.on_orderbook_update = on_orderbook_update
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
        
        subscribe_message = {
            "type": "subscribe",
            "channel": "orderbook",
            "id": token_id,
        }
        
        try:
            await self.websocket.send(json.dumps(subscribe_message))
            self.subscribed_tokens.add(token_id)
            logger.info(f"Subscribed to orderbook for token: {token_id}")
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
            
            # Handle different message types
            if data.get("type") == "orderbook":
                token_id = data.get("id")
                orderbook_data = data.get("data", {})
                
                if token_id and self.on_orderbook_update:
                    await self.on_orderbook_update(token_id, orderbook_data)
            
            elif data.get("type") == "error":
                error_msg = data.get('message', 'Unknown error')
                error_code = data.get('code', '')
                logger.error(f"RTDS error: {error_msg} (code: {error_code})")
                logger.error(f"Full error data: {data}")
            
            elif data.get("type") == "subscribed":
                logger.info(f"Successfully subscribed: {data}")
            
            elif data.get("type") == "unsubscribed":
                logger.info(f"Successfully unsubscribed: {data}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
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
        self.stream = None
    
    async def _on_orderbook_update(self, token_id: str, orderbook_data: Dict[str, Any]):
        """
        Callback for orderbook updates - saves to database.
        
        Args:
            token_id: The token ID
            orderbook_data: Orderbook data from RTDS
        """
        try:
            # Parse orderbook data
            # RTDS format may vary, but typically includes bids/asks
            bids = orderbook_data.get("bids", [])
            asks = orderbook_data.get("asks", [])
            
            # Convert to list of [price, size] tuples if needed
            if bids and isinstance(bids[0], dict):
                bids = [[float(b["price"]), float(b["size"])] for b in bids]
            if asks and isinstance(asks[0], dict):
                asks = [[float(a["price"]), float(a["size"])] for a in asks]
            
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
                metadata={"source": "rtds", "raw_data": orderbook_data},
            )
            
            logger.debug(f"Saved orderbook snapshot for token {token_id}")
            
        except Exception as e:
            logger.error(f"Error saving orderbook update for {token_id}: {e}", exc_info=True)
            # Log the orderbook data structure for debugging
            logger.debug(f"Orderbook data structure: {type(orderbook_data)}, keys: {list(orderbook_data.keys()) if isinstance(orderbook_data, dict) else 'N/A'}")
    
    async def start(self):
        """Start logging orderbook updates."""
        self.stream = OrderbookStream(on_orderbook_update=self._on_orderbook_update)
        
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

