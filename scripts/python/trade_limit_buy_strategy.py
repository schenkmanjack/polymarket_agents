"""
Live trading script for limit buy strategy.

When a new market is detected, places limit buy orders for both YES and NO at configured prices.
If one fills, cancels the other and places a limit sell order.
If neither fills and cancel_threshold_minutes reached, cancels both.

Usage:
    python scripts/python/trade_limit_buy_strategy.py --config config/limit_buy_config.json
"""
import asyncio
import logging
import sys
import os
import argparse
import uuid
import json
from datetime import datetime, timezone
from typing import Optional, Dict, List, Set, Tuple
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

# Configure proxy BEFORE importing modules that use httpx/requests
from agents.utils.proxy_config import configure_proxy, get_proxy
configure_proxy(auto_detect=True)
proxy_url = get_proxy()
if proxy_url:
    os.environ['HTTPS_PROXY'] = proxy_url
    os.environ['HTTP_PROXY'] = proxy_url

from agents.trading.trade_db import TradeDatabase, RealTradeLimitBuy
from agents.polymarket.polymarket import Polymarket
from py_clob_client.order_builder.constants import SELL, BUY
from py_clob_client.clob_types import OrderType
from agents.polymarket.btc_market_detector import (
    get_latest_btc_15m_market_proactive,
    get_latest_btc_1h_market_proactive,
    get_all_active_btc_15m_markets,
    get_all_active_btc_1h_markets,
    is_market_currently_running,
    is_market_active,
    get_market_by_slug,
)
from agents.polymarket.market_finder import get_token_ids_from_market
from agents.trading.utils import (
    parse_order_status,
    is_order_filled,
    is_order_cancelled,
    is_order_partial_fill,
    get_minutes_until_resolution,
    calculate_payout_for_filled_sell,
    calculate_payout_for_unfilled_sell,
    determine_bet_outcome,
)
from agents.trading.orderbook_helper import (
    fetch_orderbook,
    get_highest_bid,
    set_websocket_service,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True
)
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("agents").setLevel(logging.INFO)
logger = logging.getLogger(__name__)


class LimitBuyConfig:
    """Configuration loader for limit buy strategy."""
    
    def __init__(self, config_path: str):
        """Load configuration from JSON file."""
        config_path_obj = Path(config_path)
        if not config_path_obj.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with open(config_path_obj, 'r') as f:
            self.config = json.load(f)
        
        self._validate_config()
        logger.info(f"Loaded config from {config_path}")
    
    def _validate_config(self):
        """Validate configuration."""
        required_fields = [
            'yes_buy_price',
            'no_buy_price',
            'sell_price',
            'order_size',
            'market_type',
            'min_minutes_before_resolution',
            'cancel_threshold_minutes',
        ]
        
        for field in required_fields:
            if field not in self.config:
                raise ValueError(f"Missing required config field: {field}")
        
        # Validate prices
        if not (0.01 <= self.config['yes_buy_price'] <= 0.99):
            raise ValueError(f"yes_buy_price must be between 0.01 and 0.99")
        if not (0.01 <= self.config['no_buy_price'] <= 0.99):
            raise ValueError(f"no_buy_price must be between 0.01 and 0.99")
        if not (0.01 <= self.config['sell_price'] <= 0.99):
            raise ValueError(f"sell_price must be between 0.01 and 0.99")
        
        # Validate order size
        if self.config['order_size'] <= 0:
            raise ValueError(f"order_size must be positive")
        
        # Validate market type
        if self.config['market_type'] not in ['15m', '1h']:
            raise ValueError(f"market_type must be '15m' or '1h'")
        
        # Validate time thresholds
        if self.config['min_minutes_before_resolution'] <= 0:
            raise ValueError(f"min_minutes_before_resolution must be positive")
        if self.config['cancel_threshold_minutes'] <= 0:
            raise ValueError(f"cancel_threshold_minutes must be positive")
        
        logger.info("✓ Config validation passed")
    
    @property
    def yes_buy_price(self) -> float:
        return float(self.config['yes_buy_price'])
    
    @property
    def no_buy_price(self) -> float:
        return float(self.config['no_buy_price'])
    
    @property
    def sell_price(self) -> float:
        return float(self.config['sell_price'])
    
    @property
    def order_size(self) -> float:
        return float(self.config['order_size'])
    
    @property
    def market_type(self) -> str:
        return self.config['market_type']
    
    @property
    def min_minutes_before_resolution(self) -> float:
        return float(self.config['min_minutes_before_resolution'])
    
    @property
    def cancel_threshold_minutes(self) -> float:
        return float(self.config['cancel_threshold_minutes'])
    
    @property
    def convert_limit_sell_to_market(self) -> bool:
        return bool(self.config.get('convert_limit_sell_to_market', False))
    
    @property
    def order_status_check_interval(self) -> float:
        return float(self.config.get('order_status_check_interval', 1.0))
    
    @property
    def use_websocket_order_status(self) -> bool:
        return bool(self.config.get('use_websocket_order_status', True))
    
    @property
    def websocket_order_status_reconnect_delay(self) -> float:
        return float(self.config.get('websocket_order_status_reconnect_delay', 5.0))
    
    @property
    def websocket_order_status_health_check_timeout(self) -> float:
        return float(self.config.get('websocket_order_status_health_check_timeout', 14.0))
    
    @property
    def use_websocket_orderbook(self) -> bool:
        return bool(self.config.get('use_websocket_orderbook', True))
    
    @property
    def websocket_reconnect_delay(self) -> float:
        return float(self.config.get('websocket_reconnect_delay', 5.0))
    
    @property
    def websocket_health_check_timeout(self) -> float:
        return float(self.config.get('websocket_health_check_timeout', 14.0))


class LimitBuyTrader:
    """Main trading class for limit buy strategy."""
    
    def __init__(self, config_path: str):
        """Initialize trader with config."""
        global proxy_url
        if proxy_url:
            logger.info(f"Proxy configured for trading: {proxy_url.split('@')[1] if '@' in proxy_url else 'configured'}")
        else:
            logger.warning("No proxy configured - trading requests may be blocked by Cloudflare")
        
        self.config = LimitBuyConfig(config_path)
        self.db = TradeDatabase()
        self.pm = Polymarket()
        
        # Generate deployment ID
        self.deployment_id = str(uuid.uuid4())
        logger.info(f"Deployment ID: {self.deployment_id}")
        
        # Track markets we've attempted to trade (even if orders failed)
        self.attempted_markets: Set[str] = set()  # market_slugs we've attempted
        
        # Track active orders: market_slug -> {"yes_order_id": str, "no_order_id": str, "yes_trade_id": int, "no_trade_id": int}
        self.active_orders: Dict[str, Dict] = {}
        
        # Track open sell orders: sell_order_id -> trade_id
        self.open_sell_orders: Dict[str, int] = {}
        
        # Track orderbook prices before resolution for markets with open sell orders
        self.last_orderbook_prices: Dict[str, Dict] = {}  # market_slug -> {"yes_highest_bid": float, "no_highest_bid": float}
        
        # Cache market data to avoid repeated API calls
        self.market_cache: Dict[str, Dict] = {}  # market_slug -> market dict
        self.market_cache_timestamps: Dict[str, float] = {}  # market_slug -> timestamp when cached
        self.market_cache_ttl: float = 30.0  # Cache for 30 seconds
        
        self.running = False
        
        # Initialize WebSocket order status service if enabled
        self.websocket_order_status_service = None
        if self.config.use_websocket_order_status:
            try:
                from agents.trading.websocket_order_status_service import WebSocketOrderStatusService
                
                if not hasattr(self.pm, 'credentials') or not self.pm.credentials:
                    logger.warning("⚠️ No API credentials available for WebSocket order status. Falling back to HTTP polling.")
                else:
                    self.websocket_order_status_service = WebSocketOrderStatusService(
                        api_key=self.pm.credentials.api_key,
                        api_secret=self.pm.credentials.api_secret,
                        api_passphrase=self.pm.credentials.api_passphrase,
                        proxy_url=proxy_url,
                        health_check_timeout=self.config.websocket_order_status_health_check_timeout,
                        reconnect_delay=self.config.websocket_order_status_reconnect_delay,
                        on_order_update=self._handle_websocket_order_update,
                        on_trade_update=self._handle_websocket_trade_update,
                    )
                    logger.info("✓ WebSocket order status service initialized")
            except ImportError as e:
                logger.warning(f"⚠️ WebSocket order status service not available: {e}. Falling back to HTTP polling.")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize WebSocket order status service: {e}. Falling back to HTTP polling.")
        
        # Initialize WebSocket orderbook service if enabled
        self.websocket_orderbook_service = None
        if self.config.use_websocket_orderbook:
            try:
                from agents.trading.websocket_orderbook_service import WebSocketOrderbookService
                self.websocket_orderbook_service = WebSocketOrderbookService(
                    proxy_url=proxy_url,
                    health_check_timeout=self.config.websocket_health_check_timeout,
                    reconnect_delay=self.config.websocket_reconnect_delay,
                )
                set_websocket_service(self.websocket_orderbook_service)
                logger.info("✓ WebSocket orderbook service initialized")
            except ImportError as e:
                logger.warning(f"⚠️ WebSocket orderbook service not available: {e}. Falling back to HTTP polling.")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize WebSocket orderbook service: {e}. Falling back to HTTP polling.")
    
    async def start(self):
        """Start the trading loop."""
        logger.info("=" * 80)
        logger.info("STARTING LIMIT BUY STRATEGY TRADER")
        logger.info("=" * 80)
        logger.info(f"Market type: {self.config.market_type}")
        logger.info(f"YES buy price: ${self.config.yes_buy_price:.4f}")
        logger.info(f"NO buy price: ${self.config.no_buy_price:.4f}")
        logger.info(f"Sell price: ${self.config.sell_price:.4f}")
        logger.info(f"Order size: {self.config.order_size} shares")
        logger.info(f"Min minutes before resolution: {self.config.min_minutes_before_resolution:.1f}")
        logger.info(f"Cancel threshold minutes: {self.config.cancel_threshold_minutes:.1f}")
        logger.info("=" * 80)
        
        # Start WebSocket services if enabled
        if self.websocket_orderbook_service:
            try:
                await self.websocket_orderbook_service.start()
                logger.info("✓ WebSocket orderbook service started")
            except Exception as e:
                logger.error(f"Failed to start WebSocket orderbook service: {e}", exc_info=True)
        
        if self.websocket_order_status_service:
            try:
                await self.websocket_order_status_service.start()
                logger.info("✓ WebSocket order status service started")
            except Exception as e:
                logger.error(f"Failed to start WebSocket order status service: {e}", exc_info=True)
        
        # Resume monitoring markets we've bet on
        await self._resume_monitoring()
        
        self.running = True
        
        # Start background tasks
        tasks = {
            "market_detection": asyncio.create_task(self._market_detection_loop()),
            "order_status": asyncio.create_task(self._order_status_loop()),
            "market_resolution": asyncio.create_task(self._market_resolution_loop()),
        }
        
        for name, task in tasks.items():
            task.set_name(name)
            logger.info(f"Started background task: {name}")
        
        try:
            # Monitor tasks and restart if they crash
            while self.running:
                await asyncio.sleep(5.0)
                
                for name, task in list(tasks.items()):
                    if task.done():
                        try:
                            exception = task.exception()
                            if exception:
                                logger.error(f"⚠️ Background task '{name}' crashed - restarting...", exc_info=True)
                            else:
                                logger.warning(f"⚠️ Background task '{name}' completed normally - restarting...")
                        except Exception as e:
                            logger.error(f"Error checking task '{name}' status: {e}", exc_info=True)
                        
                        if self.running:
                            logger.info(f"🔄 Restarting background task: {name}")
                            new_task = asyncio.create_task(getattr(self, f"_{name}")())
                            new_task.set_name(name)
                            tasks[name] = new_task
                            logger.info(f"✅ Successfully restarted background task: {name}")
        
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error("CRITICAL ERROR: Unexpected exception in trading loop", exc_info=True)
            raise
        finally:
            self.running = False
            logger.info("Trading stopped - cancelling all background tasks...")
            
            # Stop WebSocket services
            if self.websocket_orderbook_service:
                try:
                    await self.websocket_orderbook_service.stop()
                    logger.info("✓ WebSocket orderbook service stopped")
                except Exception as e:
                    logger.error(f"Error stopping WebSocket orderbook service: {e}")
            
            if self.websocket_order_status_service:
                try:
                    await self.websocket_order_status_service.stop()
                    logger.info("✓ WebSocket order status service stopped")
                except Exception as e:
                    logger.error(f"Error stopping WebSocket order status service: {e}")
            
            # Cancel all tasks
            for name, task in tasks.items():
                if not task.done():
                    logger.info(f"Cancelling task: {name}")
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
    
    async def _resume_monitoring(self):
        """Resume monitoring markets we've bet on (for script restart recovery)."""
        unresolved_trades = self.db.get_unresolved_limit_buy_trades(deployment_id=self.deployment_id)
        logger.info(f"Found {len(unresolved_trades)} unresolved trades")
        
        # Group trades by market_slug
        trades_by_market: Dict[str, List[RealTradeLimitBuy]] = {}
        for trade in unresolved_trades:
            market_slug = trade.market_slug
            if market_slug not in trades_by_market:
                trades_by_market[market_slug] = []
            trades_by_market[market_slug].append(trade)
        
        for market_slug, trades in trades_by_market.items():
            self.attempted_markets.add(market_slug)
            
            # Find YES and NO trades
            yes_trade = next((t for t in trades if t.order_side == "YES"), None)
            no_trade = next((t for t in trades if t.order_side == "NO"), None)
            
            if yes_trade or no_trade:
                # Track active orders
                self.active_orders[market_slug] = {
                    "yes_order_id": yes_trade.order_id if yes_trade else None,
                    "no_order_id": no_trade.order_id if no_trade else None,
                    "yes_trade_id": yes_trade.id if yes_trade else None,
                    "no_trade_id": no_trade.id if no_trade else None,
                }
                
                # Track open sell orders
                for trade in trades:
                    if trade.sell_order_id and trade.sell_order_status in ["open", "partial"]:
                        self.open_sell_orders[trade.sell_order_id] = trade.id
                
                logger.info(f"Resuming monitoring for market: {market_slug}")
    
    async def _market_detection_loop(self):
        """Continuously detect new markets."""
        check_interval = 60.0  # Check every 60 seconds
        
        while self.running:
            try:
                await self._check_for_new_markets()
            except Exception as e:
                logger.error(f"Error in market detection: {e}", exc_info=True)
            
            await asyncio.sleep(check_interval)
    
    async def _check_for_new_markets(self):
        """Check for new markets of the configured type."""
        if self.config.market_type == "15m":
            latest_market = get_latest_btc_15m_market_proactive()
            markets = [latest_market] if latest_market else get_all_active_btc_15m_markets()
        else:
            latest_market = get_latest_btc_1h_market_proactive()
            markets = [latest_market] if latest_market else get_all_active_btc_1h_markets()
        
        for market in markets:
            event_slug = market.get("_event_slug", "")
            if not event_slug:
                continue
            
            # Skip if already attempted or already have active orders (current deployment)
            if event_slug in self.attempted_markets or event_slug in self.active_orders:
                continue
            
            # Check if ANY deployment has already bet on this market (prevents duplicates on redeploy)
            if self.db.has_limit_buy_bet_on_market(event_slug):
                logger.info(f"Market {event_slug} already has limit buy trades from a previous deployment, skipping")
                self.attempted_markets.add(event_slug)  # Mark as attempted to avoid checking again
                continue
            
            # Only trade currently running markets
            if not is_market_currently_running(market):
                continue
            
            # Check minimum time before resolution
            minutes_remaining = get_minutes_until_resolution(market)
            if minutes_remaining is None:
                logger.warning(f"Could not determine time remaining for {event_slug}, skipping")
                continue
            
            if minutes_remaining < self.config.min_minutes_before_resolution:
                logger.info(
                    f"Market {event_slug} has {minutes_remaining:.2f} minutes remaining, "
                    f"less than min_minutes_before_resolution ({self.config.min_minutes_before_resolution:.1f}), skipping"
                )
                continue
            
            # Extract token IDs
            token_ids = get_token_ids_from_market(market)
            if not token_ids or len(token_ids) < 2:
                continue
            
            yes_token_id = token_ids[0]
            no_token_id = token_ids[1]
            
            # Mark as attempted (even if orders fail)
            self.attempted_markets.add(event_slug)
            
            # Place initial limit buy orders
            logger.info(f"🆕 New market detected: {event_slug}")
            logger.info(f"  Minutes remaining: {minutes_remaining:.2f}")
            await self._place_initial_orders(event_slug, market, yes_token_id, no_token_id)
    
    async def _place_initial_orders(self, market_slug: str, market: Dict, yes_token_id: str, no_token_id: str):
        """Place initial YES and NO limit buy orders."""
        market_id = str(market.get("id", "unknown"))
        
        # Place YES order
        yes_order_id = None
        yes_trade_id = None
        try:
            logger.info(f"Placing YES limit buy order: price=${self.config.yes_buy_price:.4f}, size={self.config.order_size}")
            yes_order_response = self.pm.execute_order(
                price=self.config.yes_buy_price,
                size=self.config.order_size,
                side=BUY,
                token_id=yes_token_id,
            )
            
            if yes_order_response:
                yes_order_id = self.pm.extract_order_id(yes_order_response)
                if yes_order_id:
                    # Create trade record
                    yes_trade_id = self.db.create_limit_buy_trade(
                        deployment_id=self.deployment_id,
                        yes_buy_price=self.config.yes_buy_price,
                        no_buy_price=self.config.no_buy_price,
                        sell_price=self.config.sell_price,
                        order_size=self.config.order_size,
                        market_type=self.config.market_type,
                        market_id=market_id,
                        market_slug=market_slug,
                        token_id=yes_token_id,
                        order_id=yes_order_id,
                        order_price=self.config.yes_buy_price,
                        order_size_ordered=self.config.order_size,
                        order_side="YES",
                    )
                    logger.info(f"✅ YES order placed: {yes_order_id} (trade_id={yes_trade_id})")
                else:
                    logger.error(f"❌ Could not extract YES order ID from response: {yes_order_response}")
            else:
                logger.error(f"❌ YES order placement failed: no response")
        except Exception as e:
            logger.error(f"❌ Error placing YES order: {e}", exc_info=True)
        
        # Place NO order
        no_order_id = None
        no_trade_id = None
        try:
            logger.info(f"Placing NO limit buy order: price=${self.config.no_buy_price:.4f}, size={self.config.order_size}")
            no_order_response = self.pm.execute_order(
                price=self.config.no_buy_price,
                size=self.config.order_size,
                side=BUY,
                token_id=no_token_id,
            )
            
            if no_order_response:
                no_order_id = self.pm.extract_order_id(no_order_response)
                if no_order_id:
                    # Create trade record
                    no_trade_id = self.db.create_limit_buy_trade(
                        deployment_id=self.deployment_id,
                        yes_buy_price=self.config.yes_buy_price,
                        no_buy_price=self.config.no_buy_price,
                        sell_price=self.config.sell_price,
                        order_size=self.config.order_size,
                        market_type=self.config.market_type,
                        market_id=market_id,
                        market_slug=market_slug,
                        token_id=no_token_id,
                        order_id=no_order_id,
                        order_price=self.config.no_buy_price,
                        order_size_ordered=self.config.order_size,
                        order_side="NO",
                    )
                    logger.info(f"✅ NO order placed: {no_order_id} (trade_id={no_trade_id})")
                else:
                    logger.error(f"❌ Could not extract NO order ID from response: {no_order_response}")
            else:
                logger.error(f"❌ NO order placement failed: no response")
        except Exception as e:
            logger.error(f"❌ Error placing NO order: {e}", exc_info=True)
        
        # Track active orders if at least one was placed
        if yes_order_id or no_order_id:
            self.active_orders[market_slug] = {
                "yes_order_id": yes_order_id,
                "no_order_id": no_order_id,
                "yes_trade_id": yes_trade_id,
                "no_trade_id": no_trade_id,
            }
    
    async def _order_status_loop(self):
        """Check order status periodically."""
        while self.running:
            try:
                await self._check_order_statuses()
                await self._check_cancel_thresholds()
                await self._check_sell_orders_for_market_conversion()
            except Exception as e:
                logger.error(f"Error checking order status: {e}", exc_info=True)
            
            await asyncio.sleep(self.config.order_status_check_interval)
    
    async def _check_order_statuses(self):
        """Check status of all active buy orders."""
        for market_slug, order_info in list(self.active_orders.items()):
            yes_order_id = order_info.get("yes_order_id")
            no_order_id = order_info.get("no_order_id")
            yes_trade_id = order_info.get("yes_trade_id")
            no_trade_id = order_info.get("no_trade_id")
            
            # Check YES order
            if yes_order_id:
                await self._check_and_handle_order_fill(
                    market_slug, yes_order_id, yes_trade_id, "YES", no_order_id, no_trade_id
                )
            
            # Check NO order
            if no_order_id:
                await self._check_and_handle_order_fill(
                    market_slug, no_order_id, no_trade_id, "NO", yes_order_id, yes_trade_id
                )
    
    async def _check_and_handle_order_fill(
        self,
        market_slug: str,
        order_id: str,
        trade_id: Optional[int],
        side: str,
        other_order_id: Optional[str],
        other_trade_id: Optional[int],
    ):
        """Check if order is filled and handle accordingly."""
        try:
            order_status = self.pm.get_order_status(order_id)
            if not order_status:
                return
            
            status, filled_amount, total_amount = parse_order_status(order_status)
            is_filled = is_order_filled(status, filled_amount, total_amount)
            
            if is_filled and trade_id:
                # Order filled - update trade and handle
                logger.info(f"✅ {side} order {order_id} filled for market {market_slug}")
                
                # Get trade to access order_size
                trade = self.db.get_limit_buy_trade_by_id(trade_id)
                if not trade:
                    logger.error(f"Trade {trade_id} not found")
                    return
                
                # filled_amount from API is the actual shares received (after fees)
                # total_amount is the original order size (before fees)
                filled_shares = filled_amount if filled_amount else (total_amount if total_amount else trade.order_size)
                fill_price = self.config.yes_buy_price if side == "YES" else self.config.no_buy_price
                
                # Calculate dollars_spent based on original order size (what we actually paid)
                # Fees reduce shares received, not the cost
                order_size = total_amount if total_amount else trade.order_size
                dollars_spent = order_size * fill_price
                
                # Calculate fee based on what we paid
                from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                fee = calculate_polymarket_fee(fill_price, dollars_spent)
                
                logger.info(
                    f"Order fill details: ordered {order_size} shares, received {filled_shares} shares "
                    f"(fee reduced by {order_size - filled_shares:.4f} shares), "
                    f"cost=${dollars_spent:.4f}, fee=${fee:.4f}"
                )
                
                self.db.update_limit_buy_trade_fill(
                    trade_id=trade_id,
                    filled_shares=filled_shares,
                    fill_price=fill_price,
                    dollars_spent=dollars_spent,
                    fee=fee,
                    order_status="filled",
                )
                
                # Cancel the other order if it exists
                if other_order_id:
                    logger.info(f"Cancelling {('NO' if side == 'YES' else 'YES')} order {other_order_id}")
                    cancel_response = self.pm.cancel_order(other_order_id)
                    if cancel_response:
                        logger.info(f"✅ Cancelled {('NO' if side == 'YES' else 'YES')} order {other_order_id}")
                        if other_trade_id:
                            self.db.update_limit_buy_order_status(
                                trade_id=other_trade_id,
                                order_status="cancelled",
                            )
                    else:
                        logger.warning(f"⚠️ Failed to cancel {('NO' if side == 'YES' else 'YES')} order {other_order_id}")
                
                # Place limit sell order
                await self._place_sell_order(trade_id, side)
                
                # Remove from active orders
                self.active_orders.pop(market_slug, None)
        
        except Exception as e:
            logger.error(f"Error checking order {order_id}: {e}", exc_info=True)
    
    async def _place_sell_order(self, trade_id: int, side: str):
        """Place limit sell order after buy order fills.
        
        Retries with delays to handle share settlement delays after buy order fills.
        """
        trade = self.db.get_limit_buy_trade_by_id(trade_id)
        if not trade:
            logger.error(f"Trade {trade_id} not found")
            return
        
        if not trade.filled_shares or trade.filled_shares <= 0:
            logger.warning(f"Trade {trade_id} has no filled shares")
            return
        
        if not trade.token_id:
            logger.error(f"Trade {trade_id} has no token_id, cannot place sell order")
            return
        
        max_retries = 5
        initial_delay = 5.0  # Wait 5 seconds before first attempt (shares need to settle)
        retry_delays = [10.0, 20.0, 30.0, 60.0]  # Increasing delays for retries
        last_error_was_allowance = False  # Track if last error was allowance-related
        
        logger.info(
            f"Waiting {initial_delay}s for shares to settle before placing sell order "
            f"at ${self.config.sell_price:.4f} for {trade.filled_shares} shares (trade {trade_id})"
        )
        await asyncio.sleep(initial_delay)
        
        # Check allowances BEFORE starting retry loop (one-time check)
        # This matches the threshold strategy approach - check once before attempting orders
        logger.info("🔍 Pre-flight check: Verifying conditional token allowances before placing sell order...")
        if hasattr(self.pm, 'ensure_conditional_token_allowances'):
            try:
                allowances_ok = self.pm.ensure_conditional_token_allowances()
                if allowances_ok:
                    logger.info("✅ Conditional token allowances verified - ready to place sell order")
                else:
                    logger.warning("⚠️ Conditional token allowances may not be set - sell order may fail")
            except Exception as e:
                logger.error(f"❌ Error checking allowances in pre-flight check: {e}", exc_info=True)
        else:
            logger.warning("⚠️ ensure_conditional_token_allowances method not available")
        
        # Retry loop for placing sell order
        for attempt in range(max_retries):
            try:
                logger.info(
                    f"Attempt {attempt + 1}/{max_retries}: Placing limit sell order at ${self.config.sell_price:.4f} "
                    f"for {trade.filled_shares} shares (trade {trade_id}, token_id={trade.token_id[:20]}...)"
                )
                
                # Reload trade to ensure we have latest data
                trade = self.db.get_limit_buy_trade_by_id(trade_id)
                if not trade:
                    logger.error(f"Trade {trade_id} not found during retry")
                    return
                
                # Check again if sell order was already placed
                if trade.sell_order_id:
                    logger.info(f"Trade {trade_id} already has sell order {trade.sell_order_id}, skipping")
                    return
                
                # Check conditional token balance before attempting to sell
                # IMPORTANT: Shares from CLOB buy orders may be in proxy wallet OR direct wallet
                # But execute_order uses direct_wallet_client for SELL, so we should check direct wallet
                # However, if shares are actually in proxy wallet, we need to check there too
                balance = None
                if hasattr(self.pm, 'get_conditional_token_balance'):
                    logger.info(f"  🔍 Checking conditional token balance for token_id={trade.token_id[:20]}...")
                    try:
                        # First check direct wallet (where execute_order expects shares for SELL)
                        direct_wallet = self.pm.get_address_for_private_key()
                        balance = self.pm.get_conditional_token_balance(trade.token_id, wallet_address=direct_wallet)
                        logger.info(f"  📊 Direct wallet balance: {balance:.6f} shares" if balance is not None else "  📊 Direct wallet balance: None")
                        
                        # If no balance in direct wallet and proxy wallet exists, check proxy wallet too
                        if (balance is None or balance == 0) and self.pm.proxy_wallet_address:
                            logger.info(f"  🔍 No balance in direct wallet, checking proxy wallet...")
                            proxy_balance = self.pm.get_conditional_token_balance(trade.token_id, wallet_address=self.pm.proxy_wallet_address)
                            logger.info(f"  📊 Proxy wallet balance: {proxy_balance:.6f} shares" if proxy_balance is not None else "  📊 Proxy wallet balance: None")
                            if proxy_balance and proxy_balance > 0:
                                logger.warning(
                                    f"  ⚠️ Shares are in PROXY wallet ({proxy_balance:.6f} shares) but execute_order "
                                    f"will use DIRECT wallet client. This may cause 'not enough balance' errors!"
                                )
                                # Use proxy balance for now, but this indicates a mismatch
                                balance = proxy_balance
                        
                        if balance is not None:
                            logger.info(
                                f"  📊 Final balance: {balance:.6f} shares available "
                                f"(need {trade.filled_shares} shares)"
                            )
                            if balance < trade.filled_shares:
                                shortfall = trade.filled_shares - balance
                                logger.warning(
                                    f"  ⚠️ INSUFFICIENT BALANCE: have {balance:.6f}, need {trade.filled_shares}. "
                                    f"Shortfall: {shortfall:.6f} shares. "
                                    f"Shares may still be settling..."
                                )
                            else:
                                logger.info(f"  ✅ Sufficient balance available")
                    except Exception as e:
                        logger.warning(f"  ⚠️ Error checking balance: {e}. Will attempt sell order anyway.", exc_info=True)
                
                # Check conditional token allowances (critical for selling)
                # Always check on first attempt, and ALWAYS check again if we got an allowance error on previous attempt
                should_check_allowances = (attempt == 0) or last_error_was_allowance
                if should_check_allowances:
                    logger.info(f"  🔍 Checking conditional token allowances (attempt {attempt + 1})...")
                    logger.info(f"  📋 Last error was allowance-related: {last_error_was_allowance}")
                    if hasattr(self.pm, 'ensure_conditional_token_allowances'):
                        try:
                            logger.info(f"  🔧 Calling ensure_conditional_token_allowances()...")
                            allowances_ok = self.pm.ensure_conditional_token_allowances()
                            logger.info(f"  📊 ensure_conditional_token_allowances() returned: {allowances_ok}")
                            if not allowances_ok:
                                logger.warning(
                                    "  ⚠️ Conditional token allowances may not be set. "
                                    "This could cause 'not enough balance / allowance' errors."
                                )
                                # Wait a bit for approvals to propagate if they were just set
                                if attempt > 0:
                                    logger.info("  ⏳ Waiting 5 seconds for approvals to propagate on blockchain...")
                                    await asyncio.sleep(5.0)
                            else:
                                logger.info("  ✅ Conditional token allowances verified and set")
                        except Exception as e:
                            logger.error(f"  ❌ Error checking/setting allowances: {e}", exc_info=True)
                            logger.warning("  ⚠️ Will attempt sell order anyway, but it may fail if allowances are not set.")
                    else:
                        logger.warning("  ⚠️ ensure_conditional_token_allowances method not available")
                else:
                    logger.debug(f"  ⏭️ Skipping allowance check (attempt {attempt + 1}, last_error_was_allowance={last_error_was_allowance})")
                
                # Determine sell size: use actual balance if available, otherwise use filled_shares
                sell_size = trade.filled_shares
                if balance is not None and balance > 0:
                    sell_size = min(balance, trade.filled_shares)
                    if sell_size < trade.filled_shares:
                        logger.warning(
                            f"  ⚠️ Adjusting sell size from {trade.filled_shares} to {sell_size:.6f} "
                            f"shares (actual balance)"
                        )
                
                # Round down to integer shares
                import math
                sell_size_int = max(1, math.floor(sell_size))
                
                if sell_size_int < sell_size:
                    logger.warning(
                        f"  ⚠️ Rounding sell size down from {sell_size:.6f} to {sell_size_int} shares"
                    )
                
                # Final balance check
                if balance is not None and sell_size_int > balance:
                    logger.error(
                        f"  ❌ Cannot place sell order: sell_size_int ({sell_size_int}) > balance ({balance:.6f})"
                    )
                    if attempt < max_retries - 1:
                        delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                        logger.info(f"  Waiting {delay}s before retry...")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error(f"  ❌ Failed after {max_retries} attempts - insufficient balance")
                        return
                
                # Place sell order
                logger.info(
                    f"  📤 Placing SELL order: price=${self.config.sell_price:.4f}, "
                    f"size={sell_size_int} shares (filled_shares={trade.filled_shares}, balance={balance if balance is not None else 'N/A'})"
                )
                
                sell_order_response = self.pm.execute_order(
                    price=self.config.sell_price,
                    size=sell_size_int,
                    side=SELL,
                    token_id=trade.token_id,
                )
                
                if sell_order_response:
                    sell_order_id = self.pm.extract_order_id(sell_order_response)
                    if sell_order_id:
                        self.db.update_limit_buy_sell_order(
                            trade_id=trade_id,
                            sell_order_id=sell_order_id,
                            sell_order_price=self.config.sell_price,
                            sell_order_size=sell_size_int,
                            sell_order_status="open",
                        )
                        self.open_sell_orders[sell_order_id] = trade_id
                        logger.info(f"✅ Sell order placed: {sell_order_id}")
                        return  # Success!
                    else:
                        logger.error(f"❌ Could not extract sell order ID from response: {sell_order_response}")
                else:
                    logger.error(f"❌ Sell order placement failed: no response")
            
            except Exception as e:
                error_msg = str(e).lower()
                logger.error(f"❌ Error placing sell order (attempt {attempt + 1}/{max_retries}): {e}")
                
                # Check if it's a balance/allowance error
                is_allowance_error = "not enough balance" in error_msg or "allowance" in error_msg
                if is_allowance_error:
                    last_error_was_allowance = True  # Set flag for next attempt
                    if attempt < max_retries - 1:
                        delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                        logger.info(
                            f"  ⚠️ Balance/allowance error detected. "
                            f"Will check and set allowances on next attempt. "
                            f"Waiting {delay}s before retry..."
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error(f"  ❌ Failed after {max_retries} attempts due to balance/allowance issues")
                        logger.error(f"  💡 Suggestion: Check if conditional token allowances are set for exchange contracts")
                        return
                else:
                    # Other error - don't retry
                    last_error_was_allowance = False
                    logger.error(f"  ❌ Non-retryable error, aborting sell order placement")
                    return
        
        logger.error(f"❌ Failed to place sell order after {max_retries} attempts")
    
    async def _check_cancel_thresholds(self):
        """Cancel orders if cancel_threshold_minutes reached and neither has filled."""
        import time
        
        for market_slug, order_info in list(self.active_orders.items()):
            yes_order_id = order_info.get("yes_order_id")
            no_order_id = order_info.get("no_order_id")
            
            # Skip if both orders are None
            if not yes_order_id and not no_order_id:
                continue
            
            # Use cached market data if available and fresh, otherwise fetch
            current_time = time.time()
            market = None
            
            if market_slug in self.market_cache:
                cache_age = current_time - self.market_cache_timestamps.get(market_slug, 0)
                if cache_age < self.market_cache_ttl:
                    market = self.market_cache[market_slug]
                    logger.debug(f"Using cached market data for {market_slug} (age: {cache_age:.1f}s)")
            
            if not market:
                # Fetch fresh market data
                market = get_market_by_slug(market_slug)
                if market:
                    self.market_cache[market_slug] = market
                    self.market_cache_timestamps[market_slug] = current_time
                else:
                    continue
            
            minutes_remaining = get_minutes_until_resolution(market)
            if minutes_remaining is None:
                continue
            
            # Check if we should cancel
            if minutes_remaining <= self.config.cancel_threshold_minutes:
                logger.info(
                    f"⏰ Cancel threshold reached for {market_slug}: "
                    f"{minutes_remaining:.2f} minutes <= {self.config.cancel_threshold_minutes:.1f} minutes"
                )
                
                # Cancel both orders
                if yes_order_id:
                    logger.info(f"Cancelling YES order {yes_order_id}")
                    cancel_response = self.pm.cancel_order(yes_order_id)
                    if cancel_response:
                        logger.info(f"✅ Cancelled YES order {yes_order_id}")
                        yes_trade_id = order_info.get("yes_trade_id")
                        if yes_trade_id:
                            self.db.update_limit_buy_order_status(
                                trade_id=yes_trade_id,
                                order_status="cancelled",
                            )
                
                if no_order_id:
                    logger.info(f"Cancelling NO order {no_order_id}")
                    cancel_response = self.pm.cancel_order(no_order_id)
                    if cancel_response:
                        logger.info(f"✅ Cancelled NO order {no_order_id}")
                        no_trade_id = order_info.get("no_trade_id")
                        if no_trade_id:
                            self.db.update_limit_buy_order_status(
                                trade_id=no_trade_id,
                                order_status="cancelled",
                            )
                
                # Remove from active orders
                self.active_orders.pop(market_slug, None)
    
    async def _check_sell_orders_for_market_conversion(self):
        """Convert limit sell orders to market orders if cancel_threshold_minutes reached."""
        import time
        
        for sell_order_id, trade_id in list(self.open_sell_orders.items()):
            trade = self.db.get_limit_buy_trade_by_id(trade_id)
            if not trade:
                continue
            
            # Use cached market data if available and fresh
            current_time = time.time()
            market = None
            
            if trade.market_slug in self.market_cache:
                cache_age = current_time - self.market_cache_timestamps.get(trade.market_slug, 0)
                if cache_age < self.market_cache_ttl:
                    market = self.market_cache[trade.market_slug]
            
            if not market:
                market = get_market_by_slug(trade.market_slug)
                if market:
                    self.market_cache[trade.market_slug] = market
                    self.market_cache_timestamps[trade.market_slug] = current_time
                else:
                    continue
            
            minutes_remaining = get_minutes_until_resolution(market)
            if minutes_remaining is None:
                continue
            
            # Check if we should convert to market order
            if minutes_remaining <= self.config.cancel_threshold_minutes:
                if self.config.convert_limit_sell_to_market:
                    logger.info(
                        f"⏰ Cancel threshold reached for sell order {sell_order_id} (market {trade.market_slug}): "
                        f"{minutes_remaining:.2f} minutes <= {self.config.cancel_threshold_minutes:.1f} minutes. "
                        f"Converting limit sell to market sell (convert_limit_sell_to_market=true)."
                    )
                    
                    # Cancel the limit sell order
                    cancel_response = self.pm.cancel_order(sell_order_id)
                    if cancel_response:
                        logger.info(f"✅ Cancelled limit sell order {sell_order_id}")
                        self.db.update_limit_buy_sell_order(
                            trade_id=trade_id,
                            sell_order_id=sell_order_id,
                            sell_order_price=trade.sell_order_price or 0.0,
                            sell_order_size=trade.sell_order_size or 0.0,
                            sell_order_status="cancelled",
                        )
                        self.open_sell_orders.pop(sell_order_id, None)
                        
                        # Wait a moment for cancelled order to settle before placing market sell
                        if self.config.convert_limit_sell_to_market:
                            logger.info("⏳ Waiting 3 seconds for cancelled order to settle before placing market sell...")
                            await asyncio.sleep(3.0)
                            await self._place_market_sell_order(trade_id)
                    else:
                        logger.warning(f"⚠️ Failed to cancel limit sell order {sell_order_id}")
                else:
                    logger.info(
                        f"⏰ Cancel threshold reached for sell order {sell_order_id} (market {trade.market_slug}): "
                        f"{minutes_remaining:.2f} minutes <= {self.config.cancel_threshold_minutes:.1f} minutes. "
                        f"Keeping limit sell order open (convert_limit_sell_to_market=false)."
                    )
    
    async def _place_market_sell_order(self, trade_id: int):
        """Place a market sell order (FOK) at best bid price."""
        trade = self.db.get_limit_buy_trade_by_id(trade_id)
        if not trade:
            logger.error(f"Trade {trade_id} not found")
            return
        
        if not trade.filled_shares or trade.filled_shares <= 0:
            logger.warning(f"Trade {trade_id} has no filled shares")
            return
        
        if not trade.token_id:
            logger.error(f"Trade {trade_id} has no token_id")
            return
        
        # Retry logic for market sell orders (similar to limit sell)
        max_retries = 3
        retry_delays = [2.0, 5.0, 10.0]  # Shorter delays for market orders (time-sensitive)
        
        for attempt in range(max_retries):
            try:
                # Reload trade to ensure we have latest data
                trade = self.db.get_limit_buy_trade_by_id(trade_id)
                if not trade:
                    logger.error(f"Trade {trade_id} not found during retry")
                    return
                
                # Check conditional token balance before attempting to sell
                balance = None
                if hasattr(self.pm, 'get_conditional_token_balance'):
                    logger.info(f"  🔍 Checking conditional token balance for market sell (attempt {attempt + 1}/{max_retries})...")
                    try:
                        # Check direct wallet first (where execute_order expects shares for SELL)
                        direct_wallet = self.pm.get_address_for_private_key()
                        balance = self.pm.get_conditional_token_balance(trade.token_id, wallet_address=direct_wallet)
                        
                        # If no balance in direct wallet and proxy wallet exists, check proxy wallet too
                        if (balance is None or balance == 0) and self.pm.proxy_wallet_address:
                            logger.info(f"  🔍 No balance in direct wallet, checking proxy wallet...")
                            proxy_balance = self.pm.get_conditional_token_balance(trade.token_id, wallet_address=self.pm.proxy_wallet_address)
                            if proxy_balance and proxy_balance > 0:
                                logger.warning(
                                    f"  ⚠️ Shares are in PROXY wallet ({proxy_balance:.6f} shares) but execute_order "
                                    f"will use DIRECT wallet client. This may cause 'not enough balance' errors!"
                                )
                                balance = proxy_balance
                        
                        if balance is not None:
                            logger.info(f"  📊 Balance: {balance:.6f} shares available (need {trade.filled_shares} shares)")
                            if balance < trade.filled_shares:
                                logger.warning(
                                    f"  ⚠️ INSUFFICIENT BALANCE: have {balance:.6f}, need {trade.filled_shares}. "
                                    f"Shares may still be settling after order cancellation..."
                                )
                    except Exception as e:
                        logger.warning(f"  ⚠️ Error checking balance: {e}. Will attempt market sell order anyway.", exc_info=True)
                
                # Check conditional token allowances (critical for selling)
                if attempt == 0:  # Check on first attempt
                    logger.info("  🔍 Checking conditional token allowances before market sell...")
                    if hasattr(self.pm, 'ensure_conditional_token_allowances'):
                        try:
                            allowances_ok = self.pm.ensure_conditional_token_allowances()
                            if allowances_ok:
                                logger.info("  ✅ Conditional token allowances verified")
                            else:
                                logger.warning("  ⚠️ Conditional token allowances may not be set")
                        except Exception as e:
                            logger.error(f"  ❌ Error checking allowances: {e}", exc_info=True)
                
                # Get orderbook to find best bid price
                orderbook = fetch_orderbook(trade.token_id)
                if not orderbook:
                    logger.error(f"Could not fetch orderbook for token {trade.token_id[:20]}...")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delays[min(attempt, len(retry_delays) - 1)])
                        continue
                    return
                
                best_bid = get_highest_bid(orderbook)
                if best_bid is None or best_bid <= 0:
                    logger.error(f"No valid best bid found in orderbook")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delays[min(attempt, len(retry_delays) - 1)])
                        continue
                    return
                
                # Use best bid price for market sell (will fill immediately)
                market_price = best_bid
                
                # Round down to integer shares
                import math
                sell_size = max(1, math.floor(trade.filled_shares))
                
                # Final balance check
                if balance is not None and sell_size > balance:
                    logger.warning(
                        f"  ⚠️ sell_size ({sell_size}) exceeds balance ({balance:.6f}). "
                        f"Adjusting to floor of balance."
                    )
                    sell_size = max(1, math.floor(balance))
                
                logger.info(
                    f"Placing MARKET sell order (attempt {attempt + 1}/{max_retries}): "
                    f"price=${market_price:.4f} (best bid), "
                    f"size={sell_size} shares (trade_id={trade_id}, balance={balance if balance is not None else 'N/A'})"
                )
                
                # Place market order using FOK (Fill or Kill) - fills immediately or cancels
                try:
                    market_order_response = self.pm.execute_order(
                        price=market_price,
                        size=sell_size,
                        side=SELL,
                        token_id=trade.token_id,
                        order_type=OrderType.FOK,  # Market order - Fill or Kill
                    )
                    
                    if market_order_response:
                        market_order_id = self.pm.extract_order_id(market_order_response)
                        if market_order_id:
                            # Update trade with market sell order info
                            self.db.update_limit_buy_sell_order(
                                trade_id=trade_id,
                                sell_order_id=market_order_id,
                                sell_order_price=market_price,
                                sell_order_size=sell_size,
                                sell_order_status="filled",  # FOK orders fill immediately or fail
                            )
                            self.open_sell_orders[market_order_id] = trade_id
                            logger.info(f"✅ Market sell order placed: {market_order_id}")
                            return  # Success - exit retry loop
                        else:
                            logger.error(f"❌ Could not extract market order ID from response: {market_order_response}")
                    else:
                        logger.error(f"❌ Market sell order placement failed: no response")
                
                except Exception as order_error:
                    error_str = str(order_error).lower()
                    is_balance_error = 'not enough balance' in error_str or 'allowance' in error_str
                    
                    if is_balance_error:
                        logger.error(
                            f"❌ Error placing market sell order (attempt {attempt + 1}/{max_retries}): {order_error}"
                        )
                        logger.info(
                            f"⚠️ Balance/allowance error detected. "
                            f"Shares may still be settling after order cancellation..."
                        )
                        
                        if attempt < max_retries - 1:
                            delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                            logger.info(f"  ⏳ Waiting {delay}s before retry...")
                            await asyncio.sleep(delay)
                            continue  # Retry
                        else:
                            logger.error(f"❌ Failed after {max_retries} attempts due to balance/allowance issues")
                            return  # Give up
                    else:
                        # Non-balance error - log and re-raise
                        logger.error(f"❌ Error placing market sell order: {order_error}", exc_info=True)
                        raise  # Re-raise to be caught by outer except
            
            except Exception as e:
                logger.error(f"❌ Unexpected error in market sell retry loop: {e}", exc_info=True)
                if attempt < max_retries - 1:
                    delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(f"❌ Failed after {max_retries} attempts")
                    return
    
    def _handle_websocket_order_update(self, order_id: str, order_data: Dict):
        """Handle WebSocket order status update."""
        try:
            # Check if this is a buy order we're tracking
            for market_slug, order_info in list(self.active_orders.items()):
                if order_id == order_info.get("yes_order_id"):
                    asyncio.create_task(self._check_and_handle_order_fill(
                        market_slug, order_id, order_info.get("yes_trade_id"), "YES",
                        order_info.get("no_order_id"), order_info.get("no_trade_id")
                    ))
                    return
                elif order_id == order_info.get("no_order_id"):
                    asyncio.create_task(self._check_and_handle_order_fill(
                        market_slug, order_id, order_info.get("no_trade_id"), "NO",
                        order_info.get("yes_order_id"), order_info.get("yes_trade_id")
                    ))
                    return
            
            # Check if this is a sell order we're tracking
            if order_id in self.open_sell_orders:
                trade_id = self.open_sell_orders[order_id]
                asyncio.create_task(self._handle_sell_order_update(trade_id, order_id, order_data))
        
        except Exception as e:
            logger.error(f"Error handling WebSocket order update: {e}", exc_info=True)
    
    def _handle_websocket_trade_update(self, trade_data: Dict):
        """Handle WebSocket trade update."""
        try:
            order_id = trade_data.get("orderID") or trade_data.get("order_id")
            if not order_id:
                return
            
            # Check if this is a sell order we're tracking
            if order_id in self.open_sell_orders:
                trade_id = self.open_sell_orders[order_id]
                asyncio.create_task(self._handle_sell_order_update(trade_id, order_id, trade_data))
        
        except Exception as e:
            logger.error(f"Error handling WebSocket trade update: {e}", exc_info=True)
    
    async def _handle_sell_order_update(self, trade_id: int, order_id: str, order_data: Dict):
        """Handle sell order status update."""
        try:
            trade = self.db.get_limit_buy_trade_by_id(trade_id)
            if not trade:
                return
            
            # Parse order status
            status = order_data.get("status") or order_data.get("orderStatus")
            filled_amount = order_data.get("filledAmount") or order_data.get("filled_amount")
            total_amount = order_data.get("totalAmount") or order_data.get("total_amount")
            
            if filled_amount is not None and total_amount is not None:
                filled_amount = float(filled_amount)
                total_amount = float(total_amount)
            
            is_filled = is_order_filled(status, filled_amount, total_amount)
            
            if is_filled:
                # Update trade with fill information
                filled_shares = filled_amount if filled_amount else (trade.sell_order_size or 0)
                dollars_received = filled_shares * self.config.sell_price
                
                from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                fee = calculate_polymarket_fee(self.config.sell_price, dollars_received)
                
                self.db.update_limit_buy_sell_order_fill(
                    trade_id=trade_id,
                    sell_order_status="filled",
                    sell_shares_filled=filled_shares,
                    sell_dollars_received=dollars_received,
                    sell_fee=fee,
                )
                
                self.open_sell_orders.pop(order_id, None)
                logger.info(f"✅ Sell order {order_id} filled (trade_id={trade_id})")
        
        except Exception as e:
            logger.error(f"Error handling sell order update: {e}", exc_info=True)
    
    async def _market_resolution_loop(self):
        """Check market resolution periodically."""
        check_interval = 30.0  # Check every 30 seconds
        
        while self.running:
            try:
                await self._check_market_resolutions()
                await self._track_orderbook_prices_near_resolution()
            except Exception as e:
                logger.error(f"Error checking market resolution: {e}", exc_info=True)
            
            await asyncio.sleep(check_interval)
    
    async def _track_orderbook_prices_near_resolution(self):
        """Track orderbook prices for markets with open sell orders."""
        import time
        
        for sell_order_id, trade_id in list(self.open_sell_orders.items()):
            trade = self.db.get_limit_buy_trade_by_id(trade_id)
            if not trade:
                continue
            
            # Use cached market data if available and fresh
            current_time = time.time()
            market = None
            
            if trade.market_slug in self.market_cache:
                cache_age = current_time - self.market_cache_timestamps.get(trade.market_slug, 0)
                if cache_age < self.market_cache_ttl:
                    market = self.market_cache[trade.market_slug]
            
            if not market:
                market = get_market_by_slug(trade.market_slug)
                if market:
                    self.market_cache[trade.market_slug] = market
                    self.market_cache_timestamps[trade.market_slug] = current_time
                else:
                    continue
            
            minutes_remaining = get_minutes_until_resolution(market)
            if minutes_remaining is None or minutes_remaining > 2.0:  # Only track within 2 minutes
                continue
            
            try:
                # Get token IDs for both sides
                token_ids = get_token_ids_from_market(market)
                if not token_ids or len(token_ids) < 2:
                    continue
                
                yes_token_id = token_ids[0]
                no_token_id = token_ids[1]
                
                yes_orderbook = fetch_orderbook(yes_token_id)
                no_orderbook = fetch_orderbook(no_token_id)
                
                if yes_orderbook and no_orderbook:
                    yes_highest_bid = get_highest_bid(yes_orderbook)
                    no_highest_bid = get_highest_bid(no_orderbook)
                    
                    if yes_highest_bid is not None and no_highest_bid is not None:
                        self.last_orderbook_prices[trade.market_slug] = {
                            "yes_highest_bid": yes_highest_bid,
                            "no_highest_bid": no_highest_bid,
                            "timestamp": datetime.now(timezone.utc),
                        }
            except Exception as e:
                logger.debug(f"Error tracking orderbook prices for {trade.market_slug}: {e}")
    
    async def _check_market_resolutions(self):
        """Check if markets with open sell orders have resolved."""
        import time
        
        for sell_order_id, trade_id in list(self.open_sell_orders.items()):
            trade = self.db.get_limit_buy_trade_by_id(trade_id)
            if not trade:
                continue
            
            # Use cached market data if available and fresh
            current_time = time.time()
            market = None
            
            if trade.market_slug in self.market_cache:
                cache_age = current_time - self.market_cache_timestamps.get(trade.market_slug, 0)
                if cache_age < self.market_cache_ttl:
                    market = self.market_cache[trade.market_slug]
            
            if not market:
                market = get_market_by_slug(trade.market_slug)
                if market:
                    self.market_cache[trade.market_slug] = market
                    self.market_cache_timestamps[trade.market_slug] = current_time
                else:
                    continue
            
            # Check if market is still active
            if is_market_active(market):
                continue  # Market hasn't resolved yet
            
            # Market has resolved - process resolution
            await self._process_market_resolution(trade, market)
    
    async def _process_market_resolution(self, trade: RealTradeLimitBuy, market: Dict):
        """Process market resolution for trade with unfilled sell order."""
        try:
            logger.info(f"Market resolved for trade {trade.id}, checking sell order status...")
            
            # Wait a bit for API to update
            await asyncio.sleep(5.0)
            
            # Reload trade
            trade = self.db.get_trade_by_id(trade.id)
            if not trade:
                return
            
            # Check sell order status via API
            if trade.sell_order_id:
                order_status = self.pm.get_order_status(trade.sell_order_id)
                if order_status:
                    status, filled_amount, total_amount = parse_order_status(order_status)
                    is_filled = is_order_filled(status, filled_amount, total_amount)
                    
                    if is_filled:
                        # Already handled by order status check
                        return
            
            # Sell order didn't fill - determine outcome based on highest bid before resolution
            last_prices = self.last_orderbook_prices.get(trade.market_slug)
            winning_side = None
            
            if last_prices:
                yes_highest_bid = last_prices.get("yes_highest_bid")
                no_highest_bid = last_prices.get("no_highest_bid")
                
                if yes_highest_bid is not None and no_highest_bid is not None:
                    if yes_highest_bid >= 0.98:
                        winning_side = "YES"
                    elif no_highest_bid >= 0.98:
                        winning_side = "NO"
            
            # If we couldn't determine from orderbook, try outcome prices
            if winning_side is None:
                try:
                    enrich_market_from_api(market)
                    outcome_prices = market.get("outcomePrices", [])
                    if outcome_prices and len(outcome_prices) >= 2:
                        yes_price = parse_outcome_price(outcome_prices[0])
                        no_price = parse_outcome_price(outcome_prices[1])
                        
                        if yes_price is not None and yes_price > 0.5:
                            winning_side = "YES"
                        elif no_price is not None and no_price > 0.5:
                            winning_side = "NO"
                except Exception as e:
                    logger.warning(f"Could not determine winning side from outcome prices: {e}")
            
            # Calculate payout
            if winning_side:
                bet_won = determine_bet_outcome(trade.order_side, winning_side)
                
                if bet_won:
                    # We won - assume payout at sell_price
                    payout = (trade.filled_shares or 0) * self.config.sell_price
                    dollars_spent = trade.dollars_spent or 0
                    buy_fee = trade.fee or 0
                    net_payout = payout - dollars_spent - buy_fee
                    roi = (net_payout / dollars_spent) if dollars_spent > 0 else 0.0
                    
                    logger.info(
                        f"Trade {trade.id} won (side={trade.order_side}, winning_side={winning_side}). "
                        f"Payout: ${payout:.2f}, Net: ${net_payout:.2f}, ROI: {roi*100:.2f}%"
                    )
                else:
                    # We lost - no payout
                    payout = 0.0
                    dollars_spent = trade.dollars_spent or 0
                    net_payout = -dollars_spent
                    roi = -1.0
                    
                    logger.info(
                        f"Trade {trade.id} lost (side={trade.order_side}, winning_side={winning_side}). "
                        f"Loss: ${dollars_spent:.2f}"
                    )
                
                # Update trade
                outcome_price = 1.0 if bet_won else 0.0
                self.db.update_limit_buy_trade_outcome(
                    trade_id=trade.id,
                    outcome_price=outcome_price,
                    payout=payout,
                    net_payout=net_payout,
                    roi=roi,
                    is_win=bet_won,
                    winning_side=winning_side,
                )
                
                # Remove from open sell orders
                if trade.sell_order_id:
                    self.open_sell_orders.pop(trade.sell_order_id, None)
            else:
                logger.warning(f"Could not determine winning side for trade {trade.id}")
        
        except Exception as e:
            logger.error(f"Error processing market resolution for trade {trade.id}: {e}", exc_info=True)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Limit buy trading strategy")
    parser.add_argument(
        "--config",
        type=str,
        default="config/limit_buy_config.json",
        help="Path to config file",
    )
    
    args = parser.parse_args()
    
    trader = LimitBuyTrader(args.config)
    
    try:
        asyncio.run(trader.start())
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
