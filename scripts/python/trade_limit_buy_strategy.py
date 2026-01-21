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
        
        # Validate sell_price_lower_bound if present
        if 'sell_price_lower_bound' in self.config:
            lower_bound = self.config['sell_price_lower_bound']
            if not (0.01 <= lower_bound <= 0.99):
                raise ValueError(f"sell_price_lower_bound must be between 0.01 and 0.99")
            if lower_bound > self.config['sell_price']:
                logger.warning(
                    f"sell_price_lower_bound ({lower_bound}) is greater than sell_price ({self.config['sell_price']}). "
                    f"This may prevent selling at the configured sell_price."
                )
        
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
    def best_bid_margin(self) -> float:
        """Margin below best bid for limit sell (e.g., 0.01 = 1 cent below best bid)"""
        return float(self.config.get('best_bid_margin', 0.0))
    
    @property
    def sell_price_lower_bound(self) -> float:
        """Minimum sell price when placing new sell order after cancel_threshold_minutes reached.
        
        Prevents selling too low even if best_bid - margin is very low.
        Must be between 0.01 and 0.99, and typically should be >= buy_price to avoid losses.
        """
        return float(self.config.get('sell_price_lower_bound', 0.10))
    
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
        
        # Track sell orders not found (for retry logic)
        self.sell_orders_not_found: Dict[str, int] = {}  # sell_order_id -> retry_count
        self.max_order_not_found_retries = 5  # Max retries before clearing sell_order_id
        
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
        logger.info(f"Found {len(unresolved_trades)} unresolved trades from current deployment")
        
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
        
        # CRITICAL: Also load ALL open sell orders from database (regardless of deployment)
        # This ensures sell orders from previous deployments are tracked and converted
        all_unresolved_trades = self.db.get_unresolved_limit_buy_trades(deployment_id=None)
        open_sell_count = 0
        for trade in all_unresolved_trades:
            if trade.sell_order_id and trade.sell_order_status in ["open", "partial"]:
                if trade.sell_order_id not in self.open_sell_orders:
                    self.open_sell_orders[trade.sell_order_id] = trade.id
                    open_sell_count += 1
                    logger.info(
                        f"📋 Loaded open sell order {trade.sell_order_id[:10]}... from previous deployment "
                        f"(trade_id={trade.id}, market={trade.market_slug})"
                    )
        
        if open_sell_count > 0:
            logger.info(f"✅ Loaded {open_sell_count} open sell order(s) from previous deployments")
        logger.info(f"📊 Total open sell orders being tracked: {len(self.open_sell_orders)}")
    
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
        logger.info(f"🔄 Order status loop started (check interval: {self.config.order_status_check_interval}s)")
        iteration = 0
        while self.running:
            try:
                iteration += 1
                # Log heartbeat every 60 iterations (every ~60 seconds with 1s interval) to confirm loop is running
                if iteration % 60 == 0:
                    logger.info(
                        f"🔄 Order status loop heartbeat (iteration {iteration}): "
                        f"active_orders={len(self.active_orders)}, "
                        f"open_sell_orders={len(self.open_sell_orders)}"
                    )
                
                await self._check_order_statuses()
                await self._check_sell_order_statuses()  # Check sell orders via HTTP polling
                await self._check_cancel_thresholds()
                await self._check_sell_orders_for_market_conversion()
                
                # Retry placing sell orders for trades that need them (every 10 iterations = ~10 seconds)
                if iteration % 10 == 0:
                    await self._retry_missing_sell_orders()
            except Exception as e:
                logger.error(f"Error checking order status: {e}", exc_info=True)
            
            await asyncio.sleep(self.config.order_status_check_interval)
    
    async def _check_order_statuses(self):
        """Check status of all active buy orders."""
        if not self.active_orders:
            logger.debug("No active orders to check")
            return
        
        logger.info(f"🔍 Checking order statuses for {len(self.active_orders)} market(s)")
        for market_slug, order_info in list(self.active_orders.items()):
            yes_order_id = order_info.get("yes_order_id")
            no_order_id = order_info.get("no_order_id")
            yes_trade_id = order_info.get("yes_trade_id")
            no_trade_id = order_info.get("no_trade_id")
            
            logger.info(
                f"  📋 Market {market_slug}: "
                f"YES={yes_order_id[:10] if yes_order_id else 'None'}..., "
                f"NO={no_order_id[:10] if no_order_id else 'None'}..."
            )
            
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
    
    def _sync_open_sell_orders_from_db(self, current_market_slug: Optional[str] = None):
        """Sync open_sell_orders with database to ensure all open sell orders are tracked.
        
        This catches cases where sell orders were placed but not added to open_sell_orders
        (e.g., due to order ID extraction failure, exceptions, or script restart).
        
        Only syncs orders from current deployment AND current market (both conditions must be true).
        
        Args:
            current_market_slug: Optional current market slug. If provided, only syncs orders from this market.
                               If None, gets the current market automatically.
        """
        # Get current market if not provided
        if current_market_slug is None:
            if self.config.market_type == "15m":
                latest_market = get_latest_btc_15m_market_proactive()
            else:
                latest_market = get_latest_btc_1h_market_proactive()
            
            if latest_market:
                current_market_slug = latest_market.get("_event_slug", "")
            else:
                # No current market - don't sync anything
                logger.debug("No current market found - skipping sync")
                return
        
        if not current_market_slug:
            logger.debug("No current market slug - skipping sync")
            return
        
        # Get unresolved trades with open sell orders from CURRENT deployment AND CURRENT market
        unresolved_trades = self.db.get_unresolved_limit_buy_trades(deployment_id=self.deployment_id)
        synced_count = 0
        
        for trade in unresolved_trades:
            # BOTH conditions must be true: current deployment AND current market
            if (trade.sell_order_id and 
                trade.sell_order_status in ["open", "partial"] and
                trade.market_slug == current_market_slug):
                if trade.sell_order_id not in self.open_sell_orders:
                    self.open_sell_orders[trade.sell_order_id] = trade.id
                    synced_count += 1
                    logger.info(
                        f"📋 Synced open sell order {trade.sell_order_id[:10]}... from database "
                        f"(trade_id={trade.id}, market={trade.market_slug})"
                    )
        
        if synced_count > 0:
            logger.info(f"✅ Synced {synced_count} open sell order(s) from database (deployment={self.deployment_id}, market={current_market_slug}). Total tracked: {len(self.open_sell_orders)}")
    
    async def _check_sell_order_statuses(self):
        """Check status of all open sell orders via HTTP polling (backup to WebSocket).
        
        Only checks orders for the CURRENT market. Removes orders from resolved/ended markets.
        """
        # Get current market - only check orders for this market
        if self.config.market_type == "15m":
            latest_market = get_latest_btc_15m_market_proactive()
        else:
            latest_market = get_latest_btc_1h_market_proactive()
        
        current_market_slug = None
        if latest_market:
            current_market_slug = latest_market.get("_event_slug", "")
        
        # CRITICAL: First, sync with database to ensure we're tracking all open sell orders
        # Only syncs orders from current deployment AND current market
        if current_market_slug:
            self._sync_open_sell_orders_from_db(current_market_slug)
        
        # Remove orders from other markets (they're from old/resolved markets)
        if current_market_slug:
            for sell_order_id, trade_id in list(self.open_sell_orders.items()):
                trade = self.db.get_limit_buy_trade_by_id(trade_id)
                if trade and trade.market_slug != current_market_slug:
                    logger.debug(
                        f"  🗑️ Removing sell order {sell_order_id[:10]}... from tracking "
                        f"(not for current market: {trade.market_slug} != {current_market_slug})"
                    )
                    self.open_sell_orders.pop(sell_order_id, None)
        
        if not self.open_sell_orders:
            logger.debug("No open sell orders to check (open_sell_orders is empty)")
            return
        
        logger.info(f"🔍 Checking sell order statuses for {len(self.open_sell_orders)} order(s) (current market: {current_market_slug}): {list(self.open_sell_orders.keys())[:3]}...")
        for sell_order_id, trade_id in list(self.open_sell_orders.items()):
            try:
                trade = self.db.get_limit_buy_trade_by_id(trade_id)
                if not trade:
                    logger.warning(f"Trade {trade_id} not found for sell order {sell_order_id[:10]}...")
                    continue
                
                # Only check orders for current market (should already be filtered, but double-check)
                if current_market_slug and trade.market_slug != current_market_slug:
                    logger.debug(f"  ⏭️ Skipping sell order {sell_order_id[:10]}... - not for current market ({trade.market_slug} != {current_market_slug})")
                    continue
                
                logger.debug(f"  🔍 Checking sell order {sell_order_id[:10]}... (trade_id={trade_id})")
                order_status = self.pm.get_order_status(sell_order_id)
                if not order_status:
                    # Order not found - track retry count
                    retry_count = self.sell_orders_not_found.get(sell_order_id, 0)
                    
                    if retry_count < self.max_order_not_found_retries:
                        self.sell_orders_not_found[sell_order_id] = retry_count + 1
                        logger.debug(
                            f"  ⚠️ Sell order {sell_order_id[:10]}... not found in API "
                            f"(retry {retry_count + 1}/{self.max_order_not_found_retries}) - will retry on next check"
                        )
                        continue
                    
                    # Order not found after max retries - clear sell_order_id so it can be retried
                    logger.warning(
                        f"⚠️⚠️⚠️ Sell order {sell_order_id[:10]}... (trade {trade_id}) not found in API after {self.max_order_not_found_retries} retries. "
                        f"Order may never have been placed successfully. Clearing sell_order_id from database to allow retry."
                    )
                    
                    # Clear sell_order_id from database
                    try:
                        self.db.update_limit_buy_sell_order(
                            trade_id=trade_id,
                            sell_order_id=None,
                            sell_order_price=None,
                            sell_order_size=None,
                            sell_order_status=None,
                        )
                        logger.info(
                            f"✅ Cleared sell_order_id for trade {trade_id}. "
                            f"Will retry placing the sell order."
                        )
                    except Exception as e:
                        logger.error(f"Error clearing sell_order_id from database: {e}", exc_info=True)
                    
                    # Remove from tracking
                    self.open_sell_orders.pop(sell_order_id, None)
                    self.sell_orders_not_found.pop(sell_order_id, None)
                    continue
                
                # Order found - clear retry count
                self.sell_orders_not_found.pop(sell_order_id, None)
                
                status, filled_amount, total_amount = parse_order_status(order_status)
                is_filled = is_order_filled(status, filled_amount, total_amount)
                is_cancelled = is_order_cancelled(status) or "CANCELED" in status.upper() or "CANCELLED" in status.upper()
                
                logger.info(
                    f"  📊 Sell order {sell_order_id[:10]}... status: {status}, "
                    f"filled={filled_amount}, total={total_amount}, is_filled={is_filled}, is_cancelled={is_cancelled}"
                )
                
                if is_filled:
                    # Order filled - update trade
                    filled_shares = filled_amount if filled_amount else (trade.sell_order_size or 0)
                    # Use actual sell price from trade (could be contingency sell price)
                    sell_price = trade.sell_order_price or self.config.sell_price
                    dollars_received = filled_shares * sell_price
                    
                    from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                    fee = calculate_polymarket_fee(sell_price, dollars_received)
                    
                    self.db.update_limit_buy_sell_order_fill(
                        trade_id=trade_id,
                        sell_order_status="filled",
                        sell_shares_filled=filled_shares,
                        sell_dollars_received=dollars_received,
                        sell_fee=fee,
                    )
                    
                    self.open_sell_orders.pop(sell_order_id, None)
                    logger.info(
                        f"✅ Sell order {sell_order_id[:10]}... filled via HTTP polling "
                        f"(trade_id={trade_id}, price=${sell_price:.4f}, shares={filled_shares:.2f})"
                    )
                elif is_cancelled:
                    # Order cancelled (e.g., CANCELED_MARKET_RESOLVED) - remove from tracking
                    self.open_sell_orders.pop(sell_order_id, None)
                    logger.info(
                        f"🗑️ Sell order {sell_order_id[:10]}... cancelled (status: {status}) - removed from tracking"
                    )
            except Exception as e:
                logger.error(f"Error checking sell order {sell_order_id[:10]}... status: {e}", exc_info=True)
    
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
            logger.info(f"  🔍 Checking {side} order {order_id[:10]}... for market {market_slug} (trade_id={trade_id})")
            order_status = self.pm.get_order_status(order_id)
            if not order_status:
                logger.warning(f"  ⚠️ No order status returned for {side} order {order_id[:10]}... (order may not exist or API error)")
                return
            
            status, filled_amount, total_amount = parse_order_status(order_status)
            is_filled = is_order_filled(status, filled_amount, total_amount)
            
            logger.info(
                f"  📊 {side} order {order_id[:10]}... status: {status}, "
                f"filled={filled_amount}, total={total_amount}, is_filled={is_filled}"
            )
            
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
                
                # Final balance check - if balance is 0 or None, don't try to place order
                if balance is None:
                    logger.warning(
                        f"  ⚠️ Could not check balance (may be rate limited). "
                        f"Will attempt sell order anyway, but it may fail."
                    )
                elif balance == 0:
                    logger.error(
                        f"  ❌ Cannot place sell order: balance is 0.0 shares. "
                        f"Shares may not have settled yet or may be in a different wallet."
                    )
                    if attempt < max_retries - 1:
                        delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                        logger.info(f"  Waiting {delay}s before retry (shares may still be settling)...")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error(f"  ❌ Failed after {max_retries} attempts - balance is still 0")
                        return
                elif sell_size_int > balance:
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
                balance_info = f"balance={balance:.6f}" if balance is not None else "balance=N/A"
                if balance is not None:
                    # Add warning if balance is from proxy wallet but we're using direct wallet client
                    # (This will be handled by _get_client_for_order, but log it for debugging)
                    logger.info(
                        f"  📤 Placing SELL order: price=${self.config.sell_price:.4f}, "
                        f"size={sell_size_int} shares (filled_shares={trade.filled_shares}, {balance_info})"
                    )
                else:
                    logger.info(
                        f"  📤 Placing SELL order: price=${self.config.sell_price:.4f}, "
                        f"size={sell_size_int} shares (filled_shares={trade.filled_shares}, {balance_info})"
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
                        # CRITICAL: Verify order actually exists before saving to database
                        # Wait a moment for order to propagate, then verify
                        logger.info(f"  🔍 Verifying sell order {sell_order_id} exists...")
                        await asyncio.sleep(2.0)  # Wait for order to propagate
                        
                        order_status = self.pm.get_order_status(sell_order_id)
                        if not order_status:
                            logger.warning(
                                f"  ⚠️ Sell order {sell_order_id} not found in API after placement. "
                                f"This may indicate the order was not actually placed. Will retry..."
                            )
                            # Don't save to database - will retry on next attempt
                            if attempt < max_retries - 1:
                                delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                                logger.info(f"  Waiting {delay}s before retry...")
                                await asyncio.sleep(delay)
                                continue
                            else:
                                logger.error(
                                    f"  ❌ Order {sell_order_id} not found after {max_retries} attempts. "
                                    f"Order may not have been placed successfully."
                                )
                                return
                        
                        # Order verified - save to database
                        self.db.update_limit_buy_sell_order(
                            trade_id=trade_id,
                            sell_order_id=sell_order_id,
                            sell_order_price=self.config.sell_price,
                            sell_order_size=sell_size_int,
                            sell_order_status="open",
                        )
                        # CRITICAL: Add to tracking immediately after database update
                        self.open_sell_orders[sell_order_id] = trade_id
                        logger.info(
                            f"✅✅✅ SELL ORDER PLACED AND VERIFIED ✅✅✅\n"
                            f"  Sell Order ID: {sell_order_id}\n"
                            f"  Trade ID: {trade_id}\n"
                            f"  Price: ${self.config.sell_price:.4f}\n"
                            f"  Size: {sell_size_int} shares\n"
                            f"  Status: Verified via API\n"
                            f"  Now tracking {len(self.open_sell_orders)} sell order(s) in open_sell_orders"
                        )
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
        """Convert limit sell orders to market orders if cancel_threshold_minutes reached.
        
        Only processes sell orders for the CURRENT market. Skips orders from resolved/ended markets.
        """
        import time
        
        # Get current market - only process orders for this market
        if self.config.market_type == "15m":
            latest_market = get_latest_btc_15m_market_proactive()
        else:
            latest_market = get_latest_btc_1h_market_proactive()
        
        current_market_slug = None
        if latest_market:
            current_market_slug = latest_market.get("_event_slug", "")
        
        if not current_market_slug:
            logger.debug("No current market found - skipping conversion check")
            return
        
        # CRITICAL: Sync with database first to ensure we're tracking all open sell orders
        # Only syncs orders from current deployment AND current market
        self._sync_open_sell_orders_from_db(current_market_slug)
        
        if not self.open_sell_orders:
            logger.debug("No open sell orders to check for conversion (open_sell_orders is empty)")
            return
        
        # Log that we're checking (this confirms the function is called every iteration)
        logger.debug(f"🔄 [CONVERSION CHECK] Checking {len(self.open_sell_orders)} sell order(s) for conversion threshold (current market: {current_market_slug})")
        logger.info(f"🔄 Checking {len(self.open_sell_orders)} sell order(s) for conversion threshold (current market: {current_market_slug}): {list(self.open_sell_orders.keys())[:3]}...")
        
        for sell_order_id, trade_id in list(self.open_sell_orders.items()):
            try:
                trade = self.db.get_limit_buy_trade_by_id(trade_id)
                if not trade:
                    logger.warning(f"Trade {trade_id} not found for sell order {sell_order_id[:10]}...")
                    self.open_sell_orders.pop(sell_order_id, None)  # Remove orphaned order
                    continue
                
                # CRITICAL: Only process orders for the current market
                if trade.market_slug != current_market_slug:
                    # Remove orders from other markets (they're from old markets or different markets)
                    logger.debug(
                        f"  ⏭️ Skipping sell order {sell_order_id[:10]}... - not for current market "
                        f"(order market: {trade.market_slug}, current market: {current_market_slug})"
                    )
                    self.open_sell_orders.pop(sell_order_id, None)
                    continue
                
                # Quick status check - skip if already filled
                # For cancelled orders, we'll still try to place a new sell if threshold reached
                # (cancellation might have happened externally, but we still hold shares)
                order_status = self.pm.get_order_status(sell_order_id)
                order_already_cancelled = False
                if order_status:
                    status, filled_amount, total_amount = parse_order_status(order_status)
                    is_filled = is_order_filled(status, filled_amount, total_amount)
                    is_cancelled = is_order_cancelled(status) or "CANCELED" in status.upper() or "CANCELLED" in status.upper()
                    
                    if is_filled:
                        logger.info(
                            f"  ⏭️ Skipping sell order {sell_order_id[:10]}... - already filled "
                            f"(removing from tracking)"
                        )
                        self.open_sell_orders.pop(sell_order_id, None)
                        continue
                    
                    if is_cancelled:
                        logger.info(
                            f"  ℹ️ Sell order {sell_order_id[:10]}... is already cancelled (status: {status}). "
                            f"Will still check threshold and place new sell if needed (we may still hold shares)."
                        )
                        order_already_cancelled = True
                
                logger.debug(f"  📋 Checking sell order {sell_order_id[:10]}... for market {trade.market_slug}")
                
                # Use cached market data if available and fresh
                current_time = time.time()
                market = None
                
                if trade.market_slug in self.market_cache:
                    cache_age = current_time - self.market_cache_timestamps.get(trade.market_slug, 0)
                    if cache_age < self.market_cache_ttl:
                        market = self.market_cache[trade.market_slug]
                        logger.debug(f"  ✓ Using cached market data (age: {cache_age:.1f}s)")
                
                if not market:
                    logger.debug(f"  🔍 Fetching fresh market data for {trade.market_slug}...")
                    market = get_market_by_slug(trade.market_slug)
                    if market:
                        self.market_cache[trade.market_slug] = market
                        self.market_cache_timestamps[trade.market_slug] = current_time
                        logger.debug(f"  ✓ Market data fetched and cached")
                    else:
                        logger.warning(
                            f"  ⚠️ Could not fetch market data for {trade.market_slug}, skipping conversion check. "
                            f"This prevents checking minutes_remaining for sell order {sell_order_id[:10]}..."
                        )
                        continue
                
                # ALWAYS log minutes_remaining when we successfully get market data
                # This ensures we see it every iteration (every second)
                minutes_remaining = get_minutes_until_resolution(market)
                
                minutes_str = f"{minutes_remaining:.2f}" if minutes_remaining is not None else "None"
                logger.info(
                    f"  ⏱️  Sell order {sell_order_id[:10]}... (market {trade.market_slug}): "
                    f"minutes_remaining={minutes_str}, "
                    f"threshold={self.config.cancel_threshold_minutes:.1f}"
                )
                
                # Handle cases where we can't determine time or market has ended
                if minutes_remaining is None:
                    # Can't determine time - skip this iteration but log for debugging
                    logger.warning(f"  ⚠️ Could not determine minutes_remaining for market {trade.market_slug}, skipping conversion check")
                    continue
                
                # If market has ended (negative minutes), we should still try to convert if we haven't already
                # This handles the case where the market ended between check intervals
                market_ended = minutes_remaining < 0
                
                # When cancel threshold is reached (or market has ended), cancel the original high-priced sell
                # and place a new limit sell at best bid minus margin
                threshold_reached = minutes_remaining <= self.config.cancel_threshold_minutes
                
                if threshold_reached or market_ended:
                    # CRITICAL LOG: Sell order did not fill by cancel_threshold_minutes
                    if market_ended:
                        logger.warning(
                            f"🚨 SELL ORDER DID NOT FILL BY CANCEL THRESHOLD: "
                            f"Market has ended (minutes_remaining={minutes_remaining:.2f}) but sell order {sell_order_id[:10]}... "
                            f"(trade {trade_id}, market {trade.market_slug}) has not filled. "
                            f"Converting to lower limit sell immediately!"
                        )
                    else:
                        logger.warning(
                            f"🚨 SELL ORDER DID NOT FILL BY CANCEL THRESHOLD: "
                            f"Order {sell_order_id[:10]}... (trade {trade_id}, market {trade.market_slug}) "
                            f"did not fill by cancel_threshold_minutes ({self.config.cancel_threshold_minutes:.1f} min). "
                            f"Current minutes_remaining={minutes_remaining:.2f}. "
                            f"Cancelling original sell order and placing new limit sell at best bid minus margin."
                        )
                    
                    # Cancel the original limit sell order (if not already cancelled)
                    cancel_response = False
                    if not order_already_cancelled:
                        logger.info(f"🔄 Attempting to cancel original limit sell order {sell_order_id}...")
                        cancel_response = self.pm.cancel_order(sell_order_id)
                    else:
                        logger.info(
                            f"⏭️ Skipping cancellation - order {sell_order_id[:10]}... is already cancelled. "
                            f"Proceeding directly to place new sell order."
                        )
                    
                    if cancel_response:
                        logger.info(f"✅ Successfully cancelled original limit sell order {sell_order_id}")
                        self.db.update_limit_buy_sell_order(
                            trade_id=trade_id,
                            sell_order_id=sell_order_id,
                            sell_order_price=trade.sell_order_price or 0.0,
                            sell_order_size=trade.sell_order_size or 0.0,
                            sell_order_status="cancelled",
                        )
                        # DON'T remove from open_sell_orders yet - wait until new sell is successfully placed
                        
                        # Wait a moment for cancelled order to settle before placing new sell
                        # Skip wait if market has already ended (time is critical)
                        if not market_ended:
                            logger.info("⏳ Waiting 3 seconds for cancelled order to settle before placing new limit sell at best bid...")
                            await asyncio.sleep(3.0)
                        else:
                            logger.warning("⚠️ Market has ended - skipping settlement wait and placing new sell immediately!")
                            await asyncio.sleep(0.5)  # Minimal wait for cancellation to propagate
                    else:
                        logger.warning(
                            f"⚠️ Failed to cancel limit sell order {sell_order_id}. "
                            f"Order may have already filled or been cancelled. "
                            f"Will still attempt to place new limit sell at best bid..."
                        )
                        # Still attempt to place new sell - order might have already filled or been cancelled
                        # If shares are locked, the new sell will fail with balance error and retry
                        logger.info("⏳ Waiting 3 seconds before attempting new limit sell (order may still be settling)...")
                        await asyncio.sleep(3.0)
                    
                    # Place new sell order - only remove from tracking if successful
                    logger.info(f"📤 Placing new limit sell order at best bid minus margin for trade {trade_id}...")
                    new_sell_placed = await self._place_market_sell_order(trade_id)
                    
                    if new_sell_placed:
                        # CRITICAL LOG: New sell order successfully placed
                        logger.info(
                            f"✅ NEW SELL ORDER PLACED: Successfully placed new limit sell order at best bid minus margin "
                            f"for trade {trade_id} (original order {sell_order_id[:10]}...). "
                            f"Removing original order from tracking."
                        )
                        # New sell order successfully placed - remove old order from tracking
                        self.open_sell_orders.pop(sell_order_id, None)
                    else:
                        # CRITICAL LOG: Failed to place new sell order
                        logger.error(
                            f"❌ FAILED TO PLACE NEW SELL ORDER: "
                            f"Could not place new limit sell order for trade {trade_id} "
                            f"(original order {sell_order_id[:10]}...). "
                            f"Will retry on next check iteration (minutes_remaining: {minutes_remaining:.2f}). "
                            f"Original order remains in tracking."
                        )
                        # Keep in open_sell_orders so it gets checked again
                else:
                    # Threshold not reached yet - log for debugging (only log occasionally to avoid spam)
                    if int(minutes_remaining * 10) % 10 == 0:  # Log every 0.1 minutes (6 seconds)
                        logger.debug(
                            f"  ✓ Sell order {sell_order_id[:10]}... not yet at threshold "
                            f"({minutes_remaining:.2f} > {self.config.cancel_threshold_minutes:.1f} minutes remaining)"
                        )
            except Exception as e:
                logger.error(
                    f"❌ Error checking sell order {sell_order_id[:10]}... for conversion: {e}",
                    exc_info=True
                )
                # Continue checking other orders even if this one fails
    
    async def _retry_missing_sell_orders(self):
        """Retry placing sell orders for trades with filled buy orders but no sell orders."""
        try:
            # Get current market - only retry for current market
            if self.config.market_type == "15m":
                latest_market = get_latest_btc_15m_market_proactive()
            else:
                latest_market = get_latest_btc_1h_market_proactive()
            
            current_market_slug = None
            if latest_market:
                current_market_slug = latest_market.get("_event_slug", "")
            
            if not current_market_slug:
                return  # No current market - skip retry
            
            # Find trades with filled buy orders but no sell orders (or sell orders that don't exist)
            session = self.db.SessionLocal()
            try:
                # Trades with no sell_order_id
                trades_needing_sell = session.query(RealTradeLimitBuy).filter(
                    RealTradeLimitBuy.deployment_id == self.deployment_id,
                    RealTradeLimitBuy.order_status == "filled",
                    RealTradeLimitBuy.filled_shares.isnot(None),
                    RealTradeLimitBuy.filled_shares > 0,
                    RealTradeLimitBuy.sell_order_id.is_(None),
                    RealTradeLimitBuy.market_resolved_at.is_(None),
                    RealTradeLimitBuy.market_slug == current_market_slug,
                ).all()
                
                # Also check trades with sell_order_id that don't actually exist
                trades_with_invalid_sell = session.query(RealTradeLimitBuy).filter(
                    RealTradeLimitBuy.deployment_id == self.deployment_id,
                    RealTradeLimitBuy.order_status == "filled",
                    RealTradeLimitBuy.filled_shares.isnot(None),
                    RealTradeLimitBuy.filled_shares > 0,
                    RealTradeLimitBuy.sell_order_id.isnot(None),
                    RealTradeLimitBuy.market_resolved_at.is_(None),
                    RealTradeLimitBuy.market_slug == current_market_slug,
                ).all()
                
                # Verify sell orders actually exist
                for trade in trades_with_invalid_sell:
                    if trade.sell_order_id:
                        order_status = self.pm.get_order_status(trade.sell_order_id)
                        if not order_status:
                            # Order doesn't exist - add to retry list
                            logger.warning(
                                f"Found trade {trade.id} with sell_order_id {trade.sell_order_id} that doesn't exist. "
                                f"Will retry placing sell order."
                            )
                            trades_needing_sell.append(trade)
                
                for trade in trades_needing_sell:
                    # Only retry if buy order filled more than 30 seconds ago
                    if trade.order_filled_at:
                        time_since_fill = datetime.now(timezone.utc) - trade.order_filled_at
                        if time_since_fill.total_seconds() < 30:
                            continue
                    
                    logger.info(
                        f"Found trade {trade.id} with filled buy order but no valid sell order. "
                        f"Retrying sell order placement..."
                    )
                    
                    # Determine side from trade
                    side = trade.order_side or "yes"  # Default to yes if not set
                    await self._place_sell_order(trade.id, side)
            finally:
                session.close()
        except Exception as e:
            logger.debug(f"Error checking for missing sell orders: {e}", exc_info=True)
    
    async def _place_market_sell_order(self, trade_id: int) -> bool:
        """Place a limit sell order at best bid minus margin.
        
        Returns:
            True if order was successfully placed, False otherwise.
        """
        trade = self.db.get_limit_buy_trade_by_id(trade_id)
        if not trade:
            logger.error(f"Trade {trade_id} not found")
            return False
        
        if not trade.filled_shares or trade.filled_shares <= 0:
            logger.warning(f"Trade {trade_id} has no filled shares")
            return False
        
        if not trade.token_id:
            logger.error(f"Trade {trade_id} has no token_id")
            return False
        
        # Retry logic for market sell orders (similar to limit sell)
        max_retries = 3
        retry_delays = [2.0, 5.0, 10.0]  # Shorter delays for market orders (time-sensitive)
        
        for attempt in range(max_retries):
            try:
                # Reload trade to ensure we have latest data
                trade = self.db.get_limit_buy_trade_by_id(trade_id)
                if not trade:
                    logger.error(f"Trade {trade_id} not found during retry")
                    return False
                
                # Check conditional token balance before attempting to sell
                # Use retry_on_rate_limit=False for time-sensitive market sell orders
                # (the outer retry loop will handle retries, avoiding nested retry delays)
                balance = None
                if hasattr(self.pm, 'get_conditional_token_balance'):
                    logger.info(f"  🔍 Checking conditional token balance for market sell (attempt {attempt + 1}/{max_retries})...")
                    try:
                        # Check direct wallet first (where execute_order expects shares for SELL)
                        direct_wallet = self.pm.get_address_for_private_key()
                        balance = self.pm.get_conditional_token_balance(
                            trade.token_id, 
                            wallet_address=direct_wallet,
                            retry_on_rate_limit=False  # Disable retries - outer loop handles retries
                        )
                        
                        # If no balance in direct wallet and proxy wallet exists, check proxy wallet too
                        if (balance is None or balance == 0) and self.pm.proxy_wallet_address:
                            logger.info(f"  🔍 No balance in direct wallet, checking proxy wallet...")
                            proxy_balance = self.pm.get_conditional_token_balance(
                                trade.token_id, 
                                wallet_address=self.pm.proxy_wallet_address,
                                retry_on_rate_limit=False  # Disable retries - outer loop handles retries
                            )
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
                        logger.warning(f"  ⚠️ Error checking balance: {e}. Will attempt limit sell order anyway.", exc_info=True)
                
                # Check conditional token allowances (critical for selling)
                if attempt == 0:  # Check on first attempt
                    logger.info("  🔍 Checking conditional token allowances before limit sell...")
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
                    return False
                
                # Log orderbook details for debugging
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])
                logger.info(
                    f"📊 Orderbook data for token {trade.token_id[:20]}...: "
                    f"{len(bids)} bids, {len(asks)} asks"
                )
                
                if bids:
                    # Log top 5 bids for debugging
                    top_bids = sorted([float(b[0]) for b in bids if isinstance(b, (list, tuple)) and len(b) >= 1], reverse=True)[:5]
                    logger.info(f"  Top bids: {[f'${b:.4f}' for b in top_bids]}")
                else:
                    logger.warning(f"  ⚠️ No bids in orderbook!")
                
                best_bid = get_highest_bid(orderbook)
                if best_bid is None or best_bid <= 0:
                    logger.error(
                        f"No valid best bid found in orderbook. "
                        f"Bids count: {len(bids)}, Token ID: {trade.token_id[:30]}..."
                    )
                    # Get market info for context
                    market = get_market_by_slug(trade.market_slug)
                    if market:
                        minutes_remaining = get_minutes_until_resolution(market)
                        logger.error(f"  Market: {trade.market_slug}, Minutes remaining: {minutes_remaining:.2f}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delays[min(attempt, len(retry_delays) - 1)])
                        continue
                    return False
                
                # Warn if best bid seems suspiciously low near resolution
                market = get_market_by_slug(trade.market_slug)
                if market:
                    minutes_remaining = get_minutes_until_resolution(market)
                    if minutes_remaining is not None and minutes_remaining <= 5.0 and best_bid < 0.10:
                        logger.warning(
                            f"⚠️ SUSPICIOUS: Best bid (${best_bid:.4f}) is very low with {minutes_remaining:.2f} minutes remaining. "
                            f"This seems unusual - orderbook may be stale or token ID may be incorrect. "
                            f"Token ID: {trade.token_id[:30]}..., Market: {trade.market_slug}"
                        )
                
                # Calculate limit sell price: best bid minus margin
                margin = self.config.best_bid_margin
                sell_price = best_bid - margin
                
                # Apply lower bound to prevent selling too low
                lower_bound = self.config.sell_price_lower_bound
                original_sell_price = sell_price
                sell_price = max(lower_bound, sell_price)
                
                logger.info(
                    f"Calculating limit sell price: best_bid=${best_bid:.4f}, "
                    f"margin=${margin:.4f}, calculated=${original_sell_price:.4f}, "
                    f"lower_bound=${lower_bound:.4f}, final_sell_price=${sell_price:.4f}"
                )
                
                # Ensure price is within Polymarket's valid range [0.01, 0.99]
                sell_price = max(0.01, min(0.99, sell_price))
                
                # Check if lower bound was applied
                if sell_price == lower_bound and original_sell_price < lower_bound:
                    # Get minutes remaining for context
                    market = get_market_by_slug(trade.market_slug)
                    minutes_remaining = get_minutes_until_resolution(market) if market else None
                    minutes_str = f"{minutes_remaining:.2f}" if minutes_remaining else "unknown"
                    logger.warning(
                        f"⚠️ Lower bound applied: Calculated sell price (${original_sell_price:.4f}) "
                        f"was below lower_bound (${lower_bound:.4f}), using ${lower_bound:.4f}. "
                        f"best_bid=${best_bid:.4f}, margin=${margin:.4f}. "
                        f"Minutes remaining: {minutes_str}. "
                        f"This may prevent the order from filling if best_bid is too low."
                    )
                elif sell_price == 0.01 and original_sell_price < 0.01:
                    # Get minutes remaining for context
                    market = get_market_by_slug(trade.market_slug)
                    minutes_remaining = get_minutes_until_resolution(market) if market else None
                    minutes_str = f"{minutes_remaining:.2f}" if minutes_remaining else "unknown"
                    logger.error(
                        f"🚨 CRITICAL: Forced to sell at minimum price (0.01) because "
                        f"best_bid (${best_bid:.4f}) - margin (${margin:.4f}) = ${original_sell_price:.4f} < 0.01. "
                        f"Minutes remaining: {minutes_str}. "
                        f"This is very bad - consider adjusting best_bid_margin or cancel_threshold_minutes!"
                    )
                elif best_bid < 0.01:
                    logger.warning(
                        f"⚠️ Best bid ({best_bid:.6f}) is below minimum price (0.01). "
                        f"Using minimum price (0.01) for sell order."
                    )
                elif best_bid > 0.99:
                    logger.warning(
                        f"⚠️ Best bid ({best_bid:.6f}) exceeds maximum price (0.99). "
                        f"Using maximum price (0.99) for sell order."
                    )
                
                if sell_price < best_bid:
                    logger.info(
                        f"📊 Limit sell price (${sell_price:.4f}) is ${best_bid - sell_price:.4f} "
                        f"below best bid (${best_bid:.4f}). Order will fill when price reaches ${sell_price:.4f}."
                    )
                
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
                    f"Placing LIMIT sell order (attempt {attempt + 1}/{max_retries}): "
                    f"price=${sell_price:.4f} (best_bid=${best_bid:.4f}, margin=${margin:.4f}), "
                    f"size={sell_size} shares (trade_id={trade_id}, balance={balance if balance is not None else 'N/A'})"
                )
                
                # Place limit sell order (GTC)
                try:
                    order_response = self.pm.execute_order(
                        price=sell_price,
                        size=sell_size,
                        side=SELL,
                        token_id=trade.token_id,
                        order_type=OrderType.GTC,
                    )
                    
                    if order_response:
                        order_id = self.pm.extract_order_id(order_response)
                        if order_id:
                            # Update trade with sell order info
                            self.db.update_limit_buy_sell_order(
                                trade_id=trade_id,
                                sell_order_id=order_id,
                                sell_order_price=sell_price,
                                sell_order_size=sell_size,
                                sell_order_status="open",
                            )
                            self.open_sell_orders[order_id] = trade_id
                            logger.info(f"✅ LIMIT sell order placed: {order_id} (status=open)")
                            return True  # Success - exit retry loop
                        else:
                            logger.error(f"❌ Could not extract order ID from response: {order_response}")
                    else:
                        logger.error(f"❌ LIMIT sell order placement failed: no response")
                
                except Exception as order_error:
                    error_str = str(order_error).lower()
                    is_balance_error = 'not enough balance' in error_str or 'allowance' in error_str
                    
                    if is_balance_error:
                        logger.error(
                            f"❌ Error placing {order_type_str} sell order (attempt {attempt + 1}/{max_retries}): {order_error}"
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
                            return False  # Give up
                    else:
                        # Non-balance error - log and re-raise
                        logger.error(f"❌ Error placing LIMIT sell order: {order_error}", exc_info=True)
                        raise  # Re-raise to be caught by outer except
            
            except Exception as e:
                logger.error(f"❌ Unexpected error in LIMIT sell retry loop: {e}", exc_info=True)
                if attempt < max_retries - 1:
                    delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(f"❌ Failed after {max_retries} attempts")
                    return False
        
        # If we somehow exit the loop without returning, all retries failed
        logger.error(f"❌ Failed to place limit sell order after {max_retries} attempts (unexpected loop exit)")
        return False
    
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
