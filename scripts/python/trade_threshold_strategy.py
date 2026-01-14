"""
Live trading script for threshold strategy.

Monitors BTC markets, checks threshold conditions, places orders, and tracks outcomes.

Usage:
    python scripts/python/trade_threshold_strategy.py --config config/trading_config.json
"""
import asyncio
import logging
import sys
import os
import argparse
import uuid
import time
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Set
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

# Configure proxy BEFORE importing modules that use httpx/requests
# This ensures environment variables are set before ClobClient initializes
from agents.utils.proxy_config import configure_proxy, get_proxy
configure_proxy(auto_detect=True)
proxy_url = get_proxy()
if proxy_url:
    os.environ['HTTPS_PROXY'] = proxy_url
    os.environ['HTTP_PROXY'] = proxy_url

from agents.trading.trade_db import TradeDatabase, RealTradeThreshold
from agents.trading.config_loader import TradingConfig
from agents.trading.orderbook_helper import (
    fetch_orderbook,
    check_threshold_triggered,
    get_lowest_ask,
    get_highest_bid,
    set_websocket_service,
)
from agents.polymarket.polymarket import Polymarket
from py_clob_client.order_builder.constants import SELL
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
from agents.backtesting.backtesting_utils import parse_outcome_price, enrich_market_from_api
from agents.backtesting.market_fetcher import HistoricalMarketFetcher
from agents.trading.utils import (
    calculate_order_size_with_fees,
    calculate_kelly_amount,
    calculate_payout_for_filled_sell,
    calculate_payout_for_unfilled_sell,
    calculate_payout_for_partial_fill,
    parse_order_status,
    is_order_filled,
    is_order_cancelled,
    is_order_partial_fill,
    validate_trade_for_resolution,
    check_order_belongs_to_market,
    get_minutes_until_resolution,
)
from agents.trading.market_detector import MarketDetector
from agents.trading.orderbook_monitor import OrderbookMonitor
from agents.trading.order_manager import OrderManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True
)
# Ensure all loggers are set to INFO level (including child loggers)
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("agents").setLevel(logging.INFO)
logging.getLogger("agents.polymarket").setLevel(logging.INFO)
logging.getLogger("agents.polymarket.polymarket").setLevel(logging.INFO)
logging.getLogger("agents.trading").setLevel(logging.INFO)
logging.getLogger("agents.backtesting").setLevel(logging.INFO)
# Ensure logs go to stdout
logging.getLogger().handlers[0].stream = sys.stdout
# Force flush after each log (important for Railway/streaming logs)
for handler in logging.getLogger().handlers:
    handler.flush()
logger = logging.getLogger(__name__)

# Test logging configuration
logger.info("=" * 80)
logger.info("LOGGING CONFIGURATION TEST")
logger.info("=" * 80)
logger.info("Root logger level: %s", logging.getLogger().level)
logger.info("agents.polymarket.polymarket logger level: %s", logging.getLogger("agents.polymarket.polymarket").level)
logger.info("trade_threshold_strategy logger level: %s", logger.level)
logger.info("=" * 80)

# Minimum bet size
MIN_BET_SIZE = 1.0


class ThresholdTrader:
    """Main trading class for threshold strategy."""
    
    def __init__(self, config_path: str):
        """Initialize trader with config."""
        # Proxy is already configured at module level before imports
        # Use global proxy_url (defined at module level)
        global proxy_url
        if proxy_url:
            logger.info(f"Proxy configured for trading: {proxy_url.split('@')[1] if '@' in proxy_url else 'configured'}")
        else:
            logger.warning("No proxy configured - trading requests may be blocked by Cloudflare")
        
        self.config = TradingConfig(config_path)
        self.db = TradeDatabase()
        self.pm = Polymarket()
        self.market_fetcher = HistoricalMarketFetcher()
        
        # Generate deployment ID
        self.deployment_id = str(uuid.uuid4())
        logger.info(f"Deployment ID: {self.deployment_id}")
        
        # Load or initialize principal
        # If always_use_initial_principal is True, always use initial_principal regardless of database
        # Otherwise, use principal from resolved trades from THIS deployment
        if self.config.always_use_initial_principal:
            self.principal = self.config.initial_principal
            logger.info(f"✓ Using initial principal from config (always_use_initial_principal=true): ${self.principal:.2f}")
            logger.info(f"  Note: Principal will remain at ${self.principal:.2f} regardless of trade outcomes")
        else:
            # Only use principal from resolved trades from THIS deployment
            # If no trades from current deployment, use initial_principal from config
            latest_principal = self.db.get_latest_principal(deployment_id=self.deployment_id)
            if latest_principal is not None and latest_principal > 0:
                self.principal = latest_principal
                logger.info(f"✓ Loaded principal from database (deployment {self.deployment_id[:8]}...): ${self.principal:.2f}")
            else:
                # Check if there are any resolved trades from previous deployments (for logging only)
                any_principal = self.db.get_latest_principal(deployment_id=None)
                if any_principal is not None and any_principal > 0:
                    logger.info(
                        f"Found principal ${any_principal:.2f} from previous deployment, "
                        f"but using initial_principal from config for new deployment"
                    )
                
                self.principal = self.config.initial_principal
                logger.info(f"✓ Using initial principal from config: ${self.principal:.2f}")
                logger.info("(No resolved trades found for current deployment, using initial principal)")
        
        # Log final principal value for debugging
        logger.info(f"📊 Starting principal: ${self.principal:.2f}")
        
        # Track markets we're monitoring
        self.monitored_markets: Dict[str, Dict] = {}  # market_slug -> market info
        self.markets_with_bets: Set[str] = set()  # market_slugs we've bet on
        
        # Track open orders
        self.open_trades: Dict[str, int] = {}  # order_id -> trade_id
        self.open_sell_orders: Dict[str, int] = {}  # sell_order_id -> trade_id
        
        # Track orders that weren't found (for retry)
        self.orders_not_found: Dict[str, int] = {}  # order_id -> retry_count
        self.sell_orders_not_found: Dict[str, int] = {}  # sell_order_id -> retry_count
        self.max_order_not_found_retries = 3  # Retry 3 times before giving up
        
        # Track orders that were checked and found to be open (for cancellation after 5 checks)
        self.orders_checked_open: Dict[str, int] = {}  # order_id -> check_count (cancel after 5 checks)
        
        # Timing
        self.orderbook_poll_interval = self.config.orderbook_poll_interval  # seconds - configurable polling interval
        self.order_status_check_interval = self.config.order_status_check_interval  # seconds - configurable order status check interval
        self.market_resolution_check_interval = 30.0  # seconds
        
        self.running = False
        
        # Initialize WebSocket orderbook service if enabled
        self.websocket_service = None
        if self.config.use_websocket_orderbook:
            try:
                from agents.trading.websocket_orderbook_service import WebSocketOrderbookService
                # Use module-level proxy_url (already configured)
                self.websocket_service = WebSocketOrderbookService(
                    proxy_url=proxy_url,  # Use module-level proxy_url
                    health_check_timeout=self.config.websocket_health_check_timeout,
                    reconnect_delay=self.config.websocket_reconnect_delay,
                )
                logger.info("✓ WebSocket orderbook service initialized")
            except ImportError as e:
                logger.warning(f"⚠️ WebSocket orderbook service not available: {e}. Falling back to HTTP polling.")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize WebSocket orderbook service: {e}. Falling back to HTTP polling.")
        
        # Set WebSocket service in orderbook_helper for fallback logic
        if self.websocket_service:
            set_websocket_service(self.websocket_service)
        
        # Initialize modules
        self.market_detector = MarketDetector(
            config=self.config,
            monitored_markets=self.monitored_markets,
            markets_with_bets=self.markets_with_bets,
            is_running=lambda: self.running,
            websocket_service=self.websocket_service,
        )
        
        self.orderbook_monitor = OrderbookMonitor(
            config=self.config,
            monitored_markets=self.monitored_markets,
            markets_with_bets=self.markets_with_bets,
            open_trades=self.open_trades,
            open_sell_orders=self.open_sell_orders,
            db=self.db,
            pm=self.pm,
            get_principal=lambda: self.config.initial_principal if self.config.always_use_initial_principal else self.principal,
            deployment_id=self.deployment_id,
            is_running=lambda: self.running,
            order_placed_callback=self._place_order,
            place_early_sell_callback=self._place_early_sell_order,
            get_minutes_until_resolution=get_minutes_until_resolution,
            websocket_service=self.websocket_service,
        )
        
        # Initialize OrderManager first (needed for WebSocket callbacks)
        self.order_manager = OrderManager(
            config=self.config,
            open_trades=self.open_trades,
            open_sell_orders=self.open_sell_orders,
            db=self.db,
            pm=self.pm,
            get_principal=lambda: self.config.initial_principal if self.config.always_use_initial_principal else self.principal,
            deployment_id=self.deployment_id,
            is_running=lambda: self.running,
            place_sell_order_callback=self._place_initial_sell_order,
            websocket_order_status_service=None,  # Will be set below
        )
        
        # Initialize WebSocket order status service if enabled
        self.websocket_order_status_service = None
        if self.config.use_websocket_order_status:
            try:
                from agents.trading.websocket_order_status_service import WebSocketOrderStatusService
                
                # Get API credentials from Polymarket client
                if not hasattr(self.pm, 'credentials') or not self.pm.credentials:
                    logger.warning("⚠️ No API credentials available for WebSocket order status. Falling back to HTTP polling.")
                else:
                    # Use module-level proxy_url (already configured)
                    self.websocket_order_status_service = WebSocketOrderStatusService(
                        api_key=self.pm.credentials.api_key,
                        api_secret=self.pm.credentials.api_secret,
                        api_passphrase=self.pm.credentials.api_passphrase,
                        proxy_url=proxy_url,  # Use module-level proxy_url
                        health_check_timeout=self.config.websocket_order_status_health_check_timeout,
                        reconnect_delay=self.config.websocket_order_status_reconnect_delay,
                        on_order_update=self.order_manager._handle_websocket_order_update,
                        on_trade_update=self.order_manager._handle_websocket_trade_update,
                    )
                    # Set WebSocket service in OrderManager
                    self.order_manager.websocket_order_status_service = self.websocket_order_status_service
                    logger.info("✓ WebSocket order status service initialized")
            except ImportError as e:
                logger.warning(f"⚠️ WebSocket order status service not available: {e}. Falling back to HTTP polling.")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize WebSocket order status service: {e}. Falling back to HTTP polling.")
    
    async def start(self):
        """Start the trading loop."""
        logger.info("=" * 80)
        logger.info("STARTING THRESHOLD STRATEGY TRADER")
        logger.info("=" * 80)
        logger.info(f"Market type: {self.config.market_type}")
        logger.info(f"Threshold: {self.config.threshold:.4f}")
        logger.info(f"Upper threshold: {self.config.upper_threshold:.4f}")
        logger.info(f"Margin: {self.config.margin:.4f}")
        if self.config.threshold_sell > 0.0:
            logger.info(f"Threshold sell (stop-loss): {self.config.threshold_sell:.4f}")
        else:
            logger.info("Threshold sell (stop-loss): DISABLED (set to 0)")
        logger.info(f"Margin sell: {self.config.margin_sell:.4f}")
        logger.info(f"Kelly fraction: {self.config.kelly_fraction:.4f}")
        logger.info(f"Kelly scale factor: {self.config.kelly_scale_factor:.4f}")
        logger.info(f"Current principal: ${self.principal:.2f}")
        
        # Check wallet balance
        try:
            wallet_balance = self.pm.get_polymarket_balance()
            if wallet_balance is not None:
                logger.info(f"Wallet balance: ${wallet_balance:.2f}")
                amount_invested = self.config.get_amount_invested(self.principal)
                if wallet_balance < amount_invested:
                    logger.warning(
                        f"⚠ INSUFFICIENT WALLET BALANCE: "
                        f"${wallet_balance:.2f} < ${amount_invested:.2f} (required for next order)"
                    )
                    logger.warning("Please fund your proxy wallet to enable trading")
                else:
                    logger.info(f"✓ Wallet balance sufficient for next order (${amount_invested:.2f})")
            else:
                logger.warning("Could not check wallet balance - ensure proxy wallet is configured")
        except Exception as e:
            logger.warning(f"Could not check wallet balance: {e}")
        
        logger.info("=" * 80)
        
        # Start WebSocket services if enabled
        # Start orderbook WebSocket service (before resuming monitoring so we can subscribe)
        if self.websocket_service:
            try:
                await self.websocket_service.start()
                logger.info("✓ WebSocket orderbook service started")
            except Exception as e:
                logger.error(f"Failed to start WebSocket orderbook service: {e}", exc_info=True)
                logger.warning("Continuing with HTTP polling fallback")
        
        # Start order status WebSocket service
        if self.websocket_order_status_service:
            try:
                await self.websocket_order_status_service.start()
                logger.info("✓ WebSocket order status service started")
            except Exception as e:
                logger.error(f"Failed to start WebSocket order status service: {e}", exc_info=True)
                logger.warning("Continuing with HTTP polling fallback")
        
        # Resume monitoring markets we've bet on
        await self._resume_monitoring()
        
        # Subscribe resumed markets to WebSocket if service is available
        if self.websocket_service:
            for market_slug, market_info in self.monitored_markets.items():
                token_ids = market_info.get("token_ids", [])
                if token_ids:
                    self.websocket_service.subscribe_tokens(token_ids, market_slug=market_slug)
        
        self.running = True
        
        # Map of task names to their coroutines
        task_coros = {
            "market_detection": self._market_detection_loop,
            "orderbook_monitoring": self._orderbook_monitoring_loop,
            "order_status": self._order_status_loop,
            "market_resolution": self._market_resolution_loop,
        }
        
        # Start all tasks
        tasks = {}
        for name, coro in task_coros.items():
            task = asyncio.create_task(coro())
            task.set_name(name)
            tasks[name] = task
            logger.info(f"Started background task: {name}")
        
        sys.stdout.flush()
        sys.stderr.flush()
        
        try:
            # Monitor tasks and restart them if they crash
            # This keeps the system running even if individual tasks fail
            while self.running:
                await asyncio.sleep(5.0)  # Check every 5 seconds
                
                # Check each task and restart if it crashed
                for name, task in list(tasks.items()):
                    if task.done():
                        # Task completed (either normally or crashed)
                        try:
                            exception = task.exception()
                            if exception:
                                # Task crashed with an exception
                                logger.error("=" * 80)
                                logger.error(f"⚠️ Background task '{name}' crashed - restarting...")
                                logger.error("=" * 80)
                                logger.error(f"Error type: {type(exception).__name__}")
                                logger.error(f"Error message: {str(exception)}")
                                import traceback
                                tb_str = ''.join(traceback.format_exception(type(exception), exception, exception.__traceback__))
                                logger.error(f"Full traceback:\n{tb_str}")
                                sys.stdout.flush()
                                sys.stderr.flush()
                            else:
                                # Task completed normally (shouldn't happen with while loops)
                                logger.warning(f"⚠️ Background task '{name}' completed normally (unexpected) - restarting...")
                        except Exception as e:
                            logger.error(f"Error checking task '{name}' status: {e}", exc_info=True)
                        
                        # Restart the crashed task
                        if self.running and name in task_coros:
                            logger.info(f"🔄 Restarting background task: {name}")
                            new_task = asyncio.create_task(task_coros[name]())
                            new_task.set_name(name)
                            tasks[name] = new_task
                            logger.info(f"✅ Successfully restarted background task: {name}")
                            sys.stdout.flush()
                            sys.stderr.flush()
                    
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error("=" * 80)
            logger.error("CRITICAL ERROR: Unexpected exception in trading loop")
            logger.error("=" * 80)
            logger.error(f"Error type: {type(e).__name__}")
            logger.error(f"Error message: {str(e)}")
            logger.error("Full traceback:", exc_info=True)
            sys.stdout.flush()
            sys.stderr.flush()
            raise
        finally:
            self.running = False
            logger.info("Trading stopped - cancelling all background tasks...")
            
            # Stop WebSocket services
            if self.websocket_service:
                try:
                    await self.websocket_service.stop()
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
                    except Exception as e:
                        logger.error(f"Error cancelling task '{name}': {e}")
            
            logger.info("All tasks cancelled")
            sys.stdout.flush()
            sys.stderr.flush()
    
    async def _resume_monitoring(self):
        """Resume monitoring markets we've bet on (for script restart recovery)."""
        unresolved_trades = self.db.get_unresolved_trades(deployment_id=self.deployment_id)
        logger.info(f"Found {len(unresolved_trades)} unresolved trades")
        
        for trade in unresolved_trades:
            market_slug = trade.market_slug
            self.markets_with_bets.add(market_slug)
            
            # Get market info
            market = get_market_by_slug(market_slug)
            if market:
                token_ids = get_token_ids_from_market(market)
                if token_ids:
                    self.monitored_markets[market_slug] = {
                        "market": market,
                        "token_ids": token_ids,
                        "yes_token_id": token_ids[0] if len(token_ids) > 0 else None,
                        "no_token_id": token_ids[1] if len(token_ids) > 1 else None,
                    }
                    logger.info(f"Resuming monitoring for market: {market_slug}")
                    
                    # Note: WebSocket subscription will happen after start() is called
            
            # Track open buy orders
            if trade.order_id and trade.order_status in ["open", "partial"]:
                self.open_trades[trade.order_id] = trade.id
            
            # Track open sell orders
            if trade.sell_order_id and trade.sell_order_status in ["open", "partial"]:
                self.open_sell_orders[trade.sell_order_id] = trade.id
            
            # If buy order is filled but no sell order exists, place initial sell order at 0.99
            if (trade.order_status == "filled" and 
                trade.filled_shares and trade.filled_shares > 0 and 
                not trade.sell_order_id):
                logger.info(
                    f"Found filled buy order without sell order for trade {trade.id} - "
                    f"placing initial sell order at $0.99"
                )
                await self._place_initial_sell_order(trade)
    
    async def _market_detection_loop(self):
        """Continuously detect new markets - delegates to MarketDetector module."""
        await self.market_detector.detection_loop()
    
    async def _orderbook_monitoring_loop(self):
        """Poll orderbooks and check for threshold triggers - delegates to OrderbookMonitor module."""
        await self.orderbook_monitor.monitoring_loop()
    
    async def _check_orderbooks_for_triggers(self):
        """Check all monitored markets for threshold triggers - delegates to OrderbookMonitor module."""
        await self.orderbook_monitor.check_orderbooks_for_triggers()
    
    async def _check_early_sell_conditions(self, monitored_market_slugs: List[str]):
        """Check if filled buy orders should trigger early sell (stop-loss) - delegates to OrderbookMonitor module."""
        await self.orderbook_monitor.check_early_sell_conditions(monitored_market_slugs)
    
    async def _place_initial_sell_order(self, trade: RealTradeThreshold):
        """Place initial sell order at 0.99 immediately when buy order fills.
        
        Retries with delays to handle share settlement delays after buy order fills.
        """
        trade_id = trade.id  # Save ID before reloading
        max_retries = 5
        initial_delay = 5.0  # Wait 5 seconds before first attempt (shares need to settle)
        retry_delays = [10.0, 20.0, 30.0, 60.0]  # Increasing delays for retries (shares may take time to settle)
        
        # Log entry point - this should always appear if function is called
        logger.info(
            f"🔵 _place_initial_sell_order() called for trade {trade_id} "
            f"(current trade.sell_order_id={trade.sell_order_id if hasattr(trade, 'sell_order_id') else 'N/A'}, "
            f"filled_shares={trade.filled_shares if hasattr(trade, 'filled_shares') else 'N/A'})"
        )
        
        try:
            # Reload trade from database to ensure we have latest data
            trade = self.db.get_trade_by_id(trade_id)
            if not trade:
                logger.error(f"❌ Trade {trade_id} not found in database when placing sell order")
                return
            
            # Skip if already have a sell order
            if trade.sell_order_id:
                logger.info(
                    f"⏭️ Trade {trade.id} already has sell order {trade.sell_order_id}, skipping placement"
                )
                return
            
            # Skip if no filled shares
            if not trade.filled_shares or trade.filled_shares <= 0:
                logger.warning(
                    f"⚠️ Trade {trade.id} has no filled shares (filled_shares={trade.filled_shares}), "
                    f"cannot place sell order"
                )
                return
            
            if not trade.token_id:
                logger.error(f"❌ Trade {trade.id} has no token_id, cannot place sell order")
                return
            
            # Wait initial delay to allow shares to settle after buy order fills
            logger.info(
                f"Waiting {initial_delay}s for shares to settle before placing sell order "
                f"at $0.99 for {trade.filled_shares} shares (trade {trade.id})"
            )
            await asyncio.sleep(initial_delay)
            
            # Retry loop for placing sell order
            for attempt in range(max_retries):
                sell_size_int = None  # Initialize for error logging
                try:
                    logger.info(
                        f"Attempt {attempt + 1}/{max_retries}: Placing initial sell order at $0.99 "
                        f"for {trade.filled_shares} shares (trade {trade.id}, token_id={trade.token_id})"
                    )
                    
                    # Reload trade to ensure we have latest data
                    trade = self.db.get_trade_by_id(trade_id)
                    if not trade:
                        logger.error(f"Trade {trade_id} not found during retry")
                        return
                    
                    # Check again if sell order was already placed (by another process or retry)
                    if trade.sell_order_id:
                        logger.info(f"Trade {trade.id} already has sell order {trade.sell_order_id}, skipping")
                        return
                    
                    # Check conditional token balance before attempting to sell
                    # This helps diagnose "not enough balance" errors
                    logger.info(
                        f"  🔍 Pre-sell checks for trade {trade.id}: "
                        f"token_id={trade.token_id[:20]}..., "
                        f"filled_shares={trade.filled_shares}, "
                        f"order_price=$0.99"
                    )
                    
                    balance = None
                    if hasattr(self.pm, 'get_conditional_token_balance'):
                        logger.info(f"  🔍 Checking conditional token balance for token_id={trade.token_id}...")
                        try:
                            balance = self.pm.get_conditional_token_balance(trade.token_id)
                            logger.info(f"  📊 Balance check returned: {balance} (type: {type(balance)})")
                            if balance is not None:
                                logger.info(
                                    f"  📊 Balance check result: {balance:.6f} shares available "
                                    f"(need {trade.filled_shares} shares, "
                                    f"difference: {balance - trade.filled_shares:.6f})"
                                )
                                if balance < trade.filled_shares:
                                    shortfall = trade.filled_shares - balance
                                    logger.warning(
                                        f"  ⚠️ INSUFFICIENT BALANCE: have {balance:.6f}, need {trade.filled_shares}. "
                                        f"Shortfall: {shortfall:.6f} shares. "
                                        f"Shares may still be settling after buy order fill..."
                                    )
                                    # Still try to place order (balance might update during order placement)
                                else:
                                    logger.info(
                                        f"  ✅ Sufficient balance available "
                                        f"({balance:.6f} >= {trade.filled_shares})"
                                    )
                            else:
                                logger.warning(
                                    f"  ⚠️ Could not retrieve balance (returned None). "
                                    f"Will attempt sell order anyway."
                                )
                        except Exception as e:
                            logger.warning(
                                f"  ⚠️ Error checking conditional token balance: {e}. "
                                f"Will attempt sell order anyway.",
                                exc_info=True
                            )
                    else:
                        logger.warning("  ⚠️ get_conditional_token_balance method not available - cannot check balance")
                    
                    # Check conditional token allowances (critical for selling)
                    # According to py-clob-client docs, exchange contracts need approval to transfer conditional tokens
                    if attempt == 0:  # Only check on first attempt to avoid spam
                        logger.info("  🔍 Checking conditional token allowances (first attempt only)...")
                        if hasattr(self.pm, 'ensure_conditional_token_allowances'):
                            try:
                                allowances_ok = self.pm.ensure_conditional_token_allowances()
                                if not allowances_ok:
                                    logger.warning(
                                        "  ⚠️ Conditional token allowances may not be set. "
                                        "This could cause 'not enough balance / allowance' errors when selling."
                                    )
                                else:
                                    logger.info("  ✅ Conditional token allowances verified")
                            except Exception as e:
                                logger.warning(
                                    f"  ⚠️ Could not check conditional token allowances: {e}. "
                                    f"Will attempt sell order anyway.",
                                    exc_info=True
                                )
                        else:
                            logger.debug("  ensure_conditional_token_allowances method not available")
                    
                    # Determine sell size: use actual balance if available, otherwise use filled_shares
                    # This handles cases where fees reduce the actual shares received
                    sell_size = trade.filled_shares
                    if balance is not None and balance > 0:
                        # Use the actual available balance (may be less than filled_shares due to fees)
                        sell_size = min(balance, trade.filled_shares)
                        if sell_size < trade.filled_shares:
                            logger.warning(
                                f"  ⚠️ Adjusting sell size from {trade.filled_shares} to {sell_size:.6f} "
                                f"shares (actual balance). Difference likely due to fees."
                            )
                    
                    # Round down to integer (floor) to ensure we don't exceed balance
                    # Must be at least 1 share
                    import math
                    sell_size_int = max(1, math.floor(sell_size))
                    
                    # Final safety check: ensure we're not trying to sell more than available balance
                    if balance is not None and sell_size_int > balance:
                        logger.warning(
                            f"  ⚠️ sell_size_int ({sell_size_int}) exceeds balance ({balance:.6f}). "
                            f"Adjusting to floor of balance."
                        )
                        sell_size_int = max(1, math.floor(balance))
                    
                    if sell_size_int < sell_size:
                        logger.warning(
                            f"  ⚠️ Rounding sell size down from {sell_size:.6f} to {sell_size_int} "
                            f"shares (must be integer, cannot exceed balance)"
                        )
                    
                    # Final balance check right before placing order (avoid race conditions)
                    if balance is not None and sell_size_int > balance:
                        logger.error(
                            f"  ❌ Cannot place sell order: sell_size_int ({sell_size_int}) > balance ({balance:.6f}). "
                            f"Skipping this attempt."
                        )
                        raise ValueError(
                            f"Insufficient balance: trying to sell {sell_size_int} shares but only {balance:.6f} available"
                        )
                    
                    # Attempt to place sell order
                    balance_str = f"{balance:.6f}" if balance is not None else "N/A"
                    logger.info(
                        f"  📤 Placing SELL order: price=$0.99, size={sell_size_int} shares "
                        f"(filled_shares={trade.filled_shares}, balance={balance_str}), "
                        f"token_id={trade.token_id[:20]}..."
                    )
                    
                    sell_order_response = self.pm.execute_order(
                        price=0.99,
                        size=sell_size_int,  # Use actual available balance
                        side=SELL,
                        token_id=trade.token_id,
                    )
                    
                    logger.debug(f"  📥 Sell order response: {sell_order_response}")
                    
                    if sell_order_response:
                        sell_order_id = self.pm.extract_order_id(sell_order_response)
                        if sell_order_id:
                            # Log sell order to database (use actual sell size, not filled_shares)
                            self.db.update_sell_order(
                                trade_id=trade.id,
                                sell_order_id=sell_order_id,
                                sell_order_price=0.99,
                                sell_order_size=float(sell_size_int),  # Use actual sell size
                                sell_order_status="open",
                            )
                            # Track in memory
                            self.open_sell_orders[sell_order_id] = trade.id
                            logger.info(
                                f"✅✅✅ SELL ORDER PLACED SUCCESSFULLY ✅✅✅\n"
                                f"  Sell Order ID: {sell_order_id}\n"
                                f"  Trade ID: {trade.id}\n"
                                f"  Price: $0.99\n"
                                f"  Size: {sell_size_int} shares\n"
                                f"  Filled Shares: {trade.filled_shares}\n"
                                f"  Balance Used: {balance_str}\n"
                                f"  Attempt: {attempt + 1}/5"
                            )
                            return  # Success!
                        else:
                            logger.warning(
                                f"Initial sell order placed but could not extract order ID. "
                                f"Response: {sell_order_response}"
                            )
                    else:
                        logger.warning(
                            f"Attempt {attempt + 1}: execute_order returned None or empty response"
                        )
                
                except Exception as e:
                    error_str = str(e)
                    error_message = getattr(e, 'error_message', None)
                    error_dict = getattr(e, '__dict__', {})
                    
                    # Try to get full exception details
                    import traceback
                    full_traceback = traceback.format_exc()
                    
                    logger.error(
                        f"  ❌ Sell order attempt {attempt + 1}/{max_retries} FAILED for trade {trade.id}:"
                    )
                    logger.error(f"    Error type: {type(e).__name__}")
                    logger.error(f"    Error class: {type(e)}")
                    logger.error(f"    Error message (str): {error_str}")
                    logger.error(f"    Error message (repr): {repr(error_str)}")
                    logger.error(f"    Error message length: {len(error_str)}")
                    if error_message:
                        logger.error(f"    Error details (error_message attr): {error_message}")
                        logger.error(f"    Error details (repr): {repr(error_message)}")
                        logger.error(f"    Error details (type): {type(error_message)}")
                    logger.error(f"    Error dict: {error_dict}")
                    logger.error(f"    Error args: {e.args}")
                    logger.error(f"    Full traceback:\n{full_traceback}")
                    logger.error(
                        f"    Trade details: token_id={trade.token_id}, "
                        f"filled_shares={trade.filled_shares}, "
                        f"order_price=$0.99, "
                        f"sell_size_attempted={sell_size_int if sell_size_int is not None else 'N/A'}"
                    )
                    if balance is not None:
                        logger.error(f"    Balance at time of error: {balance:.6f} shares")
                    else:
                        logger.error(f"    Balance at time of error: Not checked or unavailable")
                    
                    # Check if it's a minimum size error (e.g., "Size (2) lower than the minimum: 5")
                    is_min_size_error = (
                        'size' in error_str.lower() and 
                        'minimum' in error_str.lower() and
                        ('lower than' in error_str.lower() or 'less than' in error_str.lower())
                    )
                    
                    if is_min_size_error:
                        # Extract minimum size from error message
                        import re
                        min_size_match = re.search(r'minimum[:\s]+(\d+)', error_str, re.IGNORECASE)
                        if min_size_match:
                            min_size_required = int(min_size_match.group(1))
                            logger.error(
                                f"  ❌ MINIMUM SIZE ERROR: Trying to sell {sell_size_int} shares, "
                                f"but market requires minimum {min_size_required} shares. "
                                f"Available balance: {balance:.6f} shares. "
                                f"Cannot place sell order - insufficient shares."
                            )
                            # Don't retry - we don't have enough shares
                            logger.error(
                                f"  ⚠️ Skipping sell order placement. "
                                f"Will wait for market resolution or try again if balance increases."
                            )
                            # Log to database that sell order failed due to minimum size
                            try:
                                error_sell_size = float(sell_size_int) if sell_size_int is not None else trade.filled_shares
                                self.db.update_sell_order(
                                    trade_id=trade.id,
                                    sell_order_id=None,
                                    sell_order_price=0.99,
                                    sell_order_size=error_sell_size,
                                    sell_order_status="failed",
                                )
                                session = self.db.SessionLocal()
                                try:
                                    from agents.trading.trade_db import RealTradeThreshold
                                    trade_obj = session.query(RealTradeThreshold).filter(
                                        RealTradeThreshold.id == trade.id
                                    ).first()
                                    if trade_obj:
                                        trade_obj.error_message = (
                                            f"Sell order failed: minimum size {min_size_required} shares required, "
                                            f"but only {balance:.6f} shares available (attempt {attempt + 1})"
                                        )
                                        session.commit()
                                finally:
                                    session.close()
                            except Exception as db_error:
                                logger.debug(f"Could not log minimum size error to database: {db_error}")
                            return  # Don't retry - we can't meet minimum size
                    
                    # Check if it's a balance/allowance error
                    is_balance_error = (
                        'not enough balance' in error_str.lower() or
                        'not enough allowance' in error_str.lower() or
                        'balance' in str(error_message).lower() or
                        'allowance' in str(error_message).lower()
                    )
                    
                    if is_balance_error:
                        logger.warning(
                            f"  🔍 Detected balance/allowance error. "
                            f"This usually means: (1) shares haven't settled yet, or "
                            f"(2) conditional token allowances aren't set for exchange contracts."
                        )
                        
                        if attempt < max_retries - 1:
                            # Balance/allowance error - retry with delay (shares might still be settling)
                            delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                            logger.warning(
                                f"  ⏳ Waiting {delay}s before retry {attempt + 2}/{max_retries} "
                                f"(shares may still be settling or allowances may need time to propagate)..."
                            )
                            await asyncio.sleep(delay)
                            continue
                        else:
                            logger.error(
                                f"  ❌ Max retries reached for balance/allowance error. "
                                f"Will retry via _retry_missing_sell_orders() on next order status check."
                            )
                    else:
                        # Other error
                        logger.error(
                            f"  ❌ Non-balance/allowance error. "
                            f"This may be a different issue (API error, network issue, etc.)"
                        )
                    
                    # Log error to database for tracking
                    try:
                        # Use sell_size_int if available, otherwise use filled_shares
                        error_sell_size = float(sell_size_int) if sell_size_int is not None else trade.filled_shares
                        self.db.update_sell_order(
                            trade_id=trade.id,
                            sell_order_id=None,
                            sell_order_price=0.99,
                            sell_order_size=error_sell_size,
                            sell_order_status="failed",
                        )
                        # Update error message
                        session = self.db.SessionLocal()
                        try:
                            from agents.trading.trade_db import RealTradeThreshold
                            trade_obj = session.query(RealTradeThreshold).filter(
                                RealTradeThreshold.id == trade.id
                            ).first()
                            if trade_obj:
                                trade_obj.error_message = f"Sell order failed (attempt {attempt + 1}): {error_str}"
                                session.commit()
                        finally:
                            session.close()
                    except Exception as db_error:
                        logger.debug(f"Could not log error to database: {db_error}")
                        if attempt == max_retries - 1:
                            logger.error(
                                f"❌ FAILED to place sell order for trade {trade.id} after {max_retries} attempts. "
                                f"Error: {error_str}. "
                                f"Will retry via _retry_missing_sell_orders() on next order status check. "
                                f"Trade details: filled_shares={trade.filled_shares}, token_id={trade.token_id}"
                            )
                            # Log failure to database error_message field for tracking
                            try:
                                self.db.update_order_status(
                                    trade.id,
                                    trade.order_status or "filled",
                                    error_message=f"Sell order placement failed after {max_retries} attempts: {error_str}"
                                )
                            except Exception as db_error:
                                logger.error(f"Could not log sell order failure to database: {db_error}")
                            # Don't raise - we'll retry later when checking order statuses
                            return
                        else:
                            # Wait before next retry
                            delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                            await asyncio.sleep(delay)
            
            logger.error(
                f"❌❌❌ SELL ORDER FAILED AFTER ALL RETRIES ❌❌❌\n"
                f"  Trade ID: {trade.id}\n"
                f"  Attempts: {max_retries}/5\n"
                f"  Filled Shares: {trade.filled_shares}\n"
                f"  Token ID: {trade.token_id}\n"
                f"  No sell order logged to database.\n"
                f"  Will retry via _retry_missing_sell_orders() on next order status check."
            )
            # Log failure to database for tracking
            try:
                trade = self.db.get_trade_by_id(trade_id)
                if trade:
                    self.db.update_order_status(
                        trade.id,
                        trade.order_status or "filled",
                        error_message=f"Sell order placement failed: exhausted all {max_retries} retry attempts"
                    )
            except Exception as db_error:
                logger.error(f"Could not log sell order failure to database: {db_error}")
            
        except Exception as e:
            logger.error(
                f"Error placing initial sell order for trade {trade.id}: {e}", 
                exc_info=True
            )
    
    async def _place_early_sell_order(self, trade: RealTradeThreshold, sell_price: float):
        """Place an early sell order (stop-loss), canceling the 0.99 order first if it exists."""
        try:
            # If there's an existing sell order at 0.99, cancel it first
            # Only proceed with threshold sell if cancellation succeeds
            order_cancelled = False
            if trade.sell_order_id and trade.sell_order_status == "open":
                logger.info(
                    f"Canceling existing sell order {trade.sell_order_id} at $0.99 "
                    f"before placing early sell at ${sell_price:.4f}"
                )
                cancel_response = self.pm.cancel_order(trade.sell_order_id)
                if cancel_response:
                    logger.info(
                        f"🚫 ORDER CANCELLED: Sell order {trade.sell_order_id} (trade {trade.id}, market {trade.market_slug}) "
                        f"was cancelled to place threshold sell order at ${sell_price:.4f}"
                    )
                    # Remove from tracking
                    self.open_sell_orders.pop(trade.sell_order_id, None)
                    # Update database to mark as cancelled AND clear sell_order_id
                    # This prevents _place_initial_sell_order() from placing a new 0.99 order
                    # We'll set the new sell_order_id when we place the threshold sell order below
                    session = self.db.SessionLocal()
                    try:
                        trade_obj = session.query(RealTradeThreshold).filter_by(id=trade.id).first()
                        if trade_obj:
                            trade_obj.sell_order_status = "cancelled"
                            trade_obj.sell_order_id = None  # Clear it so we can place new order
                            session.commit()
                            logger.debug(f"Updated database: cancelled sell order and cleared sell_order_id for trade {trade.id}")
                            order_cancelled = True
                    except Exception as e:
                        session.rollback()
                        logger.error(f"Error updating database after cancellation: {e}")
                    finally:
                        session.close()
                else:
                    logger.warning(
                        f"❌ Failed to cancel sell order {trade.sell_order_id}. "
                        f"Cannot place threshold sell order - keeping existing 0.99 order. "
                        f"The 0.99 order will remain active and will be handled normally."
                    )
                    # Don't clear sell_order_id - keep the existing order
                    # Don't place threshold sell - return early
                    return
            
            # Only proceed if we successfully cancelled the order (or there was no order to cancel)
            if not order_cancelled and trade.sell_order_id:
                # This shouldn't happen, but be safe
                logger.warning(
                    f"⚠️ Cannot place threshold sell: order cancellation status unclear. "
                    f"Keeping existing sell order {trade.sell_order_id}."
                )
                return
            
            # Check actual balance before placing early sell order
            balance = None
            if hasattr(self.pm, 'get_conditional_token_balance'):
                try:
                    balance = self.pm.get_conditional_token_balance(trade.token_id)
                    if balance is not None:
                        logger.info(
                            f"  📊 Balance check for early sell: {balance:.6f} shares available "
                            f"(filled_shares={trade.filled_shares})"
                        )
                except Exception as e:
                    logger.warning(f"  ⚠️ Could not check balance for early sell: {e}")
            
            # Determine sell size: use actual balance if available
            sell_size = trade.filled_shares
            if balance is not None and balance > 0:
                sell_size = min(balance, trade.filled_shares)
                if sell_size < trade.filled_shares:
                    logger.warning(
                        f"  ⚠️ Adjusting early sell size from {trade.filled_shares} to {sell_size:.6f} "
                        f"shares (actual balance)"
                    )
            
            # Round down to integer (floor) to ensure we don't exceed balance
            # Must be at least 1 share
            import math
            sell_size_int = max(1, math.floor(sell_size))
            
            # Final safety check: ensure we're not trying to sell more than available balance
            if balance is not None and sell_size_int > balance:
                logger.warning(
                    f"  ⚠️ sell_size_int ({sell_size_int}) exceeds balance ({balance:.6f}). "
                    f"Adjusting to floor of balance."
                )
                sell_size_int = max(1, math.floor(balance))
            
            if sell_size_int < sell_size:
                logger.warning(
                    f"  ⚠️ Rounding early sell size down from {sell_size:.6f} to {sell_size_int} "
                    f"shares (must be integer, cannot exceed balance)"
                )
            
            # Final balance check right before placing order (avoid race conditions)
            if balance is not None and sell_size_int > balance:
                logger.error(
                    f"  ❌ Cannot place early sell order: sell_size_int ({sell_size_int}) > balance ({balance:.6f}). "
                    f"Skipping."
                )
                raise ValueError(
                    f"Insufficient balance: trying to sell {sell_size_int} shares but only {balance:.6f} available"
                )
            
            # Reload trade from database right before placing order to ensure we have latest state
            # This prevents race conditions where another process might have placed a sell order
            trade = self.db.get_trade_by_id(trade.id)
            if not trade:
                logger.error(f"Trade {trade.id} not found in database when placing early sell order")
                return
            
            # Double-check: if a sell order already exists (shouldn't happen, but be safe)
            if trade.sell_order_id and trade.sell_order_status == "open":
                logger.warning(
                    f"⚠️ Trade {trade.id} already has an open sell order {trade.sell_order_id} "
                    f"when trying to place early sell. Skipping to avoid overwriting."
                )
                return
            
            # Place new early sell order
            balance_str = f"{balance:.6f}" if balance is not None else "N/A"
            logger.info(
                f"  📤 Placing EARLY SELL order: price=${sell_price:.4f}, size={sell_size_int} shares "
                f"(filled_shares={trade.filled_shares}, balance={balance_str})"
            )
            sell_order_response = self.pm.execute_order(
                price=sell_price,
                size=sell_size_int,  # Use actual available balance
                side=SELL,
                token_id=trade.token_id,
            )
            
            if sell_order_response:
                sell_order_id = self.pm.extract_order_id(sell_order_response)
                if sell_order_id:
                    # Log sell order to database (use actual sell size)
                    # IMPORTANT: This MUST update sell_order_price to the threshold price, not 0.99
                    self.db.update_sell_order(
                        trade_id=trade.id,
                        sell_order_id=sell_order_id,
                        sell_order_price=sell_price,  # Use threshold sell price, not 0.99
                        sell_order_size=float(sell_size_int),  # Use actual sell size
                        sell_order_status="open",
                    )
                    # Track in memory
                    self.open_sell_orders[sell_order_id] = trade.id
                    # Mark market as bet on (don't buy again in this market)
                    self.markets_with_bets.add(trade.market_slug)
                    logger.info(
                        f"✓ Early sell order placed: order_id={sell_order_id}, "
                        f"price={sell_price:.4f}, size={sell_size_int} shares "
                        f"(filled_shares={trade.filled_shares})"
                    )
                else:
                    logger.warning(f"Early sell order placed but could not extract order ID")
            else:
                logger.warning(f"Failed to place early sell order for trade {trade.id}")
        except Exception as e:
            logger.error(f"Error placing early sell order for trade {trade.id}: {e}", exc_info=True)
    
    async def _place_order(
        self,
        market_slug: str,
        market_info: Dict,
        side: str,
        trigger_price: float,
    ) -> bool:
        """Place a limit order when threshold is triggered - delegates to OrderManager module.
        
        Returns:
            True if order was successfully placed, False otherwise
            (False can occur due to time restriction, order size issues, etc.)
        """
        return await self.order_manager.place_buy_order(market_slug, market_info, side, trigger_price)
    
    async def _order_status_loop(self):
        """Check order status - delegates to OrderManager module."""
        await self.order_manager.status_check_loop()
    
    async def _check_order_statuses(self):
        """Check status of all open orders - delegates to OrderManager module."""
        await self.order_manager.check_order_statuses()
    
    async def _check_sell_order_statuses(self):
        """Check status of all open sell orders - delegates to OrderManager module."""
        await self.order_manager.check_sell_order_statuses()
    
    async def _retry_missing_sell_orders(self):
        """Retry placing sell orders for trades with filled buy orders but no sell orders - delegates to OrderManager module."""
        await self.order_manager.retry_missing_sell_orders()
    
    async def _market_resolution_loop(self):
        """Check market resolution every 30 seconds."""
        while self.running:
            try:
                await self._check_market_resolutions()
            except Exception as e:
                logger.error(f"Error checking market resolution: {e}", exc_info=True)
            
            await asyncio.sleep(self.market_resolution_check_interval)
    
    async def _check_market_resolutions(self):
        """Check if markets we've bet on have resolved."""
        unresolved_trades = self.db.get_unresolved_trades()
        
        for trade in unresolved_trades:
            try:
                market_slug = trade.market_slug
                market = get_market_by_slug(market_slug)
                
                if not market:
                    logger.warning(f"Could not find market {market_slug}")
                    continue
                
                # Check if market is still active
                if is_market_active(market):
                    continue  # Market hasn't resolved yet
                
                # Market has resolved - get outcome
                await self._process_market_resolution(trade, market)
            
            except Exception as e:
                logger.error(f"Error processing market resolution for trade {trade.id}: {e}", exc_info=True)
    
    async def _process_market_resolution(self, trade: RealTradeThreshold, market: Dict):
        """Process market resolution and update principal."""
        try:
            # Validate trade using utility function
            is_valid, error_message = validate_trade_for_resolution(trade)
            if not is_valid:
                logger.warning(f"Trade {trade.id}: {error_message} - skipping resolution")
                return
            
            # Wait 5 seconds after market resolution to allow API to update
            logger.info(f"Market resolved for trade {trade.id}, waiting 5 seconds before checking sell order status...")
            await asyncio.sleep(5.0)
            
            # ALWAYS check sell order status via API when market resolves (don't trust database)
            # Retry up to 10 times (every 3 seconds) to catch fills that happen right as market resolves
            # The database field might be stale or incorrect
            sell_order_filled_via_api = False
            sell_order_api_status = None
            sell_order_filled_amount = 0.0
            sell_order_total_amount = 0.0
            
            # Reload trade to get latest sell_order_status (may have been cancelled)
            trade = self.db.get_trade_by_id(trade.id)
            if not trade:
                logger.error(f"Trade {trade.id} not found when processing market resolution")
                return
            
            # Check if sell order was cancelled or failed
            is_sell_order_cancelled = (trade.sell_order_status == "cancelled")
            is_sell_order_failed = (trade.sell_order_status == "failed")
            
            # If cancelled or failed, remove from open_sell_orders immediately
            if (is_sell_order_cancelled or is_sell_order_failed) and trade.sell_order_id:
                self.open_sell_orders.pop(trade.sell_order_id, None)
                status_type = "cancelled" if is_sell_order_cancelled else "failed"
                logger.info(
                    f"✅ Sell order was {status_type} for trade {trade.id}. "
                    f"Removed from open_sell_orders (now {len(self.open_sell_orders)} open sell orders)"
                )
            
            if trade.sell_order_id and not is_sell_order_cancelled and not is_sell_order_failed:
                max_retries = 10
                retry_delay = 3.0  # seconds
                
                for attempt in range(max_retries):
                    try:
                        if attempt == 0:
                            logger.info(
                                f"🔍 Market resolved: Checking sell order status via API (attempt {attempt + 1}/{max_retries}) "
                                f"(sell_order_id={trade.sell_order_id}, db_status={trade.sell_order_status})"
                            )
                        else:
                            logger.info(
                                f"🔍 Retrying sell order status check (attempt {attempt + 1}/{max_retries}) "
                                f"for trade {trade.id}, sell_order_id={trade.sell_order_id}"
                            )
                        
                        order_status = self.pm.get_order_status(trade.sell_order_id)
                        if order_status:
                            # Parse order status using utility function
                            sell_order_api_status, sell_order_filled_amount, sell_order_total_amount = parse_order_status(order_status)
                            
                            # Determine if order is filled based on API response using utility function
                            sell_order_filled_via_api = is_order_filled(sell_order_api_status, sell_order_filled_amount, sell_order_total_amount)
                            
                            # Check for partial fills using utility function
                            is_partial_fill = is_order_partial_fill(sell_order_api_status, sell_order_filled_amount, sell_order_total_amount)
                            
                            logger.info(
                                f"🔍 Sell order API check (attempt {attempt + 1}/{max_retries}): "
                                f"Trade {trade.id}, sell_order_id={trade.sell_order_id}, "
                                f"api_status={sell_order_api_status}, "
                                f"filled_amount={sell_order_filled_amount}, "
                                f"total_amount={sell_order_total_amount}, "
                                f"is_filled={sell_order_filled_via_api}, "
                                f"is_partial={is_partial_fill}, "
                                f"db_status={trade.sell_order_status}"
                            )
                            
                            if sell_order_filled_via_api:
                                # Order is fully filled - update database and break out of retry loop
                                logger.info(
                                    f"✅ Sell order FULLY FILLED on attempt {attempt + 1}/{max_retries} - updating database"
                                )
                                
                                # Update database with API result - use sell_order_size as sell_shares_filled for full fills
                                sell_shares_filled = sell_order_filled_amount if sell_order_filled_amount > 0 else trade.sell_order_size
                                if trade.sell_order_status != "filled":
                                    logger.info(
                                        f"⚠️ Database says sell order is '{trade.sell_order_status}' but API says it's filled. "
                                        f"Updating database to match API."
                                    )
                                    session = self.db.SessionLocal()
                                    try:
                                        trade_obj = session.query(RealTradeThreshold).filter_by(id=trade.id).first()
                                        if trade_obj:
                                            trade_obj.sell_order_status = "filled"
                                            trade_obj.sell_shares_filled = sell_shares_filled  # Update filled shares
                                            # If we don't have sell_dollars_received, estimate it
                                            if not trade_obj.sell_dollars_received and trade_obj.sell_order_price:
                                                trade_obj.sell_dollars_received = sell_shares_filled * trade_obj.sell_order_price
                                            session.commit()
                                            # Reload trade object to get updated values
                                            trade = self.db.get_trade_by_id(trade.id)
                                    except Exception as e:
                                        session.rollback()
                                        logger.error(f"Error updating sell order status from API: {e}")
                                    finally:
                                        session.close()
                                
                                # Remove from open_sell_orders so new trades can proceed
                                self.open_sell_orders.pop(trade.sell_order_id, None)
                                logger.info(
                                    f"✅ Removed sell order {trade.sell_order_id} from open_sell_orders "
                                    f"(now {len(self.open_sell_orders)} open sell orders)"
                                )
                                
                                break  # Exit retry loop - order is fully filled
                            elif is_partial_fill:
                                # Partial fill detected - update database and retry a few times to see if it fully fills
                                logger.info(
                                    f"⏳ Partial fill detected on attempt {attempt + 1}/{max_retries}: "
                                    f"{sell_order_filled_amount} / {sell_order_total_amount} shares filled. "
                                    f"Updating database and will retry to check if order fully fills."
                                )
                                
                                # Update database with partial fill information
                                session = self.db.SessionLocal()
                                try:
                                    trade_obj = session.query(RealTradeThreshold).filter_by(id=trade.id).first()
                                    if trade_obj:
                                        # Update sell_shares_filled with current filled amount
                                        trade_obj.sell_shares_filled = sell_order_filled_amount
                                        trade_obj.sell_order_status = "partial"
                                        # Update sell_dollars_received based on filled shares
                                        if trade_obj.sell_order_price:
                                            trade_obj.sell_dollars_received = sell_order_filled_amount * trade_obj.sell_order_price
                                            # Recalculate sell_fee based on actual filled amount
                                            from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                                            trade_obj.sell_fee = calculate_polymarket_fee(
                                                trade_obj.sell_order_price, 
                                                trade_obj.sell_dollars_received
                                            )
                                        session.commit()
                                        # Reload trade object to get updated values
                                        trade = self.db.get_trade_by_id(trade.id)
                                        logger.info(
                                            f"✅ Updated database: sell_shares_filled={sell_order_filled_amount}, "
                                            f"status=partial"
                                        )
                                except Exception as e:
                                    session.rollback()
                                    logger.error(f"Error updating partial fill: {e}")
                                finally:
                                    session.close()
                                
                                # Retry a few more times (up to max_retries) to see if order fully fills
                                # Only retry if we haven't exhausted all attempts
                                if attempt < max_retries - 1:
                                    logger.info(
                                        f"⏳ Retrying in {retry_delay} seconds to check if partial fill becomes full fill... "
                                        f"({attempt + 1}/{max_retries})"
                                    )
                                    await asyncio.sleep(retry_delay)
                                else:
                                    # Max retries reached - use partial fill for calculations
                                    logger.info(
                                        f"⚠️ Max retries reached. Using partial fill ({sell_order_filled_amount} shares) "
                                        f"for ROI and principal calculations."
                                    )
                                    break  # Exit retry loop - use partial fill
                            else:
                                # Order not filled yet (no fill or partial) - retry if we haven't exhausted attempts
                                if attempt < max_retries - 1:
                                    logger.info(
                                        f"⏳ Sell order not filled yet (status={sell_order_api_status}), "
                                        f"retrying in {retry_delay} seconds... ({attempt + 1}/{max_retries})"
                                    )
                                    await asyncio.sleep(retry_delay)
                                else:
                                    # Max retries reached - order still not filled
                                    logger.warning(
                                        f"⚠️ Sell order {trade.sell_order_id} still not filled after {max_retries} attempts. "
                                        f"Market resolved - cancelling order to allow trading in next market."
                                    )
                                    # Cancel the order via API if it's still open
                                    # Verify order belongs to this market before canceling
                                    if sell_order_api_status in ["live", "LIVE", "open", "OPEN"]:
                                        # Verify order belongs to this market
                                        order_belongs_to_market = False
                                        try:
                                            order_status_check = self.pm.get_order_status(trade.sell_order_id)
                                            if order_status_check:
                                                order_market = order_status_check.get("market")
                                                order_asset_id = order_status_check.get("asset_id")
                                                # Check if order's market or asset_id matches this trade's market
                                                if order_market == trade.market_id or order_asset_id == trade.token_id:
                                                    order_belongs_to_market = True
                                                else:
                                                    logger.warning(
                                                        f"⚠️ Order {trade.sell_order_id} does not belong to market {trade.market_slug} "
                                                        f"(order_market={order_market}, trade_market_id={trade.market_id}). Skipping cancellation."
                                                    )
                                        except Exception as e:
                                            logger.warning(f"Could not verify order market before cancellation: {e}")
                                            # If verification fails, assume it belongs (order_id came from this trade)
                                            order_belongs_to_market = True
                                        
                                        if order_belongs_to_market:
                                            logger.info(f"🔄 Cancelling sell order {trade.sell_order_id} for market {trade.market_slug} via API...")
                                            cancel_result = self.pm.cancel_order(trade.sell_order_id)
                                            if cancel_result:
                                                logger.info(
                                                    f"🚫 ORDER CANCELLED: Sell order {trade.sell_order_id} (trade {trade.id}, market {trade.market_slug}) "
                                                    f"was cancelled because market resolved and order did not fill after {max_retries} retry attempts."
                                                )
                                            else:
                                                logger.warning(f"⚠️ Failed to cancel sell order {trade.sell_order_id} via API")
                                    # Update database to reflect that order is cancelled
                                    session = self.db.SessionLocal()
                                    try:
                                        trade_obj = session.query(RealTradeThreshold).filter_by(id=trade.id).first()
                                        if trade_obj:
                                            trade_obj.sell_order_status = "cancelled"
                                            session.commit()
                                            trade = self.db.get_trade_by_id(trade.id)
                                            logger.info(f"✅ Updated database: sell order {trade.sell_order_id} marked as cancelled")
                                    except Exception as e:
                                        session.rollback()
                                        logger.error(f"Error updating sell order status: {e}")
                                    finally:
                                        session.close()
                                    
                                    # Remove from open_sell_orders so new trades can proceed
                                    self.open_sell_orders.pop(trade.sell_order_id, None)
                                    logger.info(
                                        f"✅ Removed cancelled sell order {trade.sell_order_id} from open_sell_orders "
                                        f"(now {len(self.open_sell_orders)} open sell orders)"
                                    )
                        else:
                            # Order not found in API - retry if we haven't exhausted attempts
                            if attempt < max_retries - 1:
                                logger.warning(
                                    f"⚠️ Sell order {trade.sell_order_id} not found in API (attempt {attempt + 1}/{max_retries}), "
                                    f"retrying in {retry_delay} seconds..."
                                )
                                await asyncio.sleep(retry_delay)
                            else:
                                logger.warning(
                                    f"⚠️ Sell order {trade.sell_order_id} not found in API after {max_retries} attempts. "
                                    f"Market resolved - marking as cancelled to allow trading in next market."
                                )
                                # Update database to mark as cancelled
                                session = self.db.SessionLocal()
                                try:
                                    trade_obj = session.query(RealTradeThreshold).filter_by(id=trade.id).first()
                                    if trade_obj:
                                        trade_obj.sell_order_status = "cancelled"
                                        session.commit()
                                        trade = self.db.get_trade_by_id(trade.id)
                                        logger.info(f"✅ Updated database: sell order {trade.sell_order_id} marked as cancelled (not found in API)")
                                except Exception as e:
                                    session.rollback()
                                    logger.error(f"Error updating sell order status: {e}")
                                finally:
                                    session.close()
                                
                                # Remove from open_sell_orders so new trades can proceed
                                self.open_sell_orders.pop(trade.sell_order_id, None)
                                logger.info(
                                    f"✅ Removed cancelled sell order {trade.sell_order_id} from open_sell_orders "
                                    f"(now {len(self.open_sell_orders)} open sell orders)"
                                )
                    except Exception as e:
                        logger.error(
                            f"Error checking sell order status via API (attempt {attempt + 1}/{max_retries}): {e}",
                            exc_info=True
                        )
                        # Retry on error unless this is the last attempt
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                
                # After retry loop completes, if order is still not filled and still open, cancel it
                if not sell_order_filled_via_api and trade.sell_order_id:
                    # Final check: is the order still open?
                    try:
                        final_order_status = self.pm.get_order_status(trade.sell_order_id)
                        if final_order_status:
                            final_status = final_order_status.get("status", "unknown")
                            if final_status in ["live", "LIVE", "open", "OPEN"]:
                                # Verify order belongs to this market before canceling using utility function
                                order_belongs_to_market = check_order_belongs_to_market(
                                    final_order_status,
                                    trade.market_id,
                                    trade.token_id,
                                )
                                if not order_belongs_to_market:
                                    logger.warning(
                                        f"⚠️ Order {trade.sell_order_id} does not belong to market {trade.market_slug} "
                                        f"(order_market={final_order_status.get('market')}, trade_market_id={trade.market_id}). Skipping cancellation."
                                    )
                                
                                if order_belongs_to_market:
                                    logger.info(
                                        f"🔄 Market {trade.market_slug} resolved but sell order {trade.sell_order_id} is still open. "
                                        f"Cancelling to allow trading in next market..."
                                    )
                                    cancel_result = self.pm.cancel_order(trade.sell_order_id)
                                    if cancel_result:
                                        logger.info(f"✅ Successfully cancelled sell order {trade.sell_order_id} for market {trade.market_slug}")
                                    else:
                                        logger.warning(f"⚠️ Failed to cancel sell order {trade.sell_order_id} via API")
                                
                                # Update database
                                session = self.db.SessionLocal()
                                try:
                                    trade_obj = session.query(RealTradeThreshold).filter_by(id=trade.id).first()
                                    if trade_obj:
                                        trade_obj.sell_order_status = "cancelled"
                                        session.commit()
                                        trade = self.db.get_trade_by_id(trade.id)
                                        logger.info(f"✅ Updated database: sell order {trade.sell_order_id} marked as cancelled")
                                except Exception as e:
                                    session.rollback()
                                    logger.error(f"Error updating sell order status: {e}")
                                finally:
                                    session.close()
                                
                                # Remove from open_sell_orders so new trades can proceed
                                self.open_sell_orders.pop(trade.sell_order_id, None)
                                logger.info(
                                    f"✅ Removed cancelled sell order {trade.sell_order_id} from open_sell_orders "
                                    f"(now {len(self.open_sell_orders)} open sell orders)"
                                )
                    except Exception as e:
                        logger.warning(f"Could not check final order status for cancellation: {e}")
            
            # Validate trade again (in case state changed)
            is_valid, error_message = validate_trade_for_resolution(trade)
            if not is_valid:
                logger.warning(f"Trade {trade.id}: {error_message} - skipping resolution")
                # Mark as cancelled/unfilled if not already marked
                if trade.order_status not in ["cancelled", "failed"]:
                    self.db.update_order_status(
                        trade.id,
                        "cancelled",
                        error_message="Order never filled before market resolution"
                    )
                return
            
            filled_shares = trade.filled_shares or 0.0
            dollars_spent = trade.dollars_spent or 0.0
            
            # Get outcome prices
            outcome_prices_raw = market.get("outcomePrices")
            if not outcome_prices_raw:
                # Try to fetch from API
                market_info = enrich_market_from_api(trade.market_id, self.market_fetcher)
                if market_info:
                    outcome_prices_raw = market_info.get("outcomePrices", {})
            
            if not outcome_prices_raw:
                logger.error(f"Could not determine outcome for market {trade.market_slug}")
                self.db.update_order_status(
                    trade.id,
                    "error",
                    error_message="Could not determine market outcome"
                )
                return
            
            # Log raw market resolution data for debugging
            logger.info(
                f"📊 Market resolution data for trade {trade.id} (market: {trade.market_slug}): "
                f"outcome_prices_raw={outcome_prices_raw} (type: {type(outcome_prices_raw).__name__})"
            )
            
            # Parse outcome price for the side we bet on
            outcome_price = parse_outcome_price(
                outcome_prices_raw,
                trade.order_side,
                trade.market_id,
                self.market_fetcher,
            )
            
            if outcome_price is None:
                logger.error(f"Could not parse outcome price for trade {trade.id}")
                return
            
            # Determine winning side first - try orderbook prices, fallback to outcomePrices
            winning_side = None
            
            # Primary method: Use orderbook prices at resolution
            # YES won: YES highest bid ≥ 0.98 (buyers willing to pay close to $1 for YES token)
            # NO won: NO highest bid ≥ 0.98 (buyers willing to pay close to $1 for NO token)
            last_prices = self.orderbook_monitor.get_last_orderbook_prices(trade.market_slug)
            if last_prices:
                yes_highest_bid = last_prices.get("yes_highest_bid")
                no_highest_bid = last_prices.get("no_highest_bid")
                
                if yes_highest_bid is not None and no_highest_bid is not None:
                    logger.info(
                        f"📊 Using orderbook prices for resolution determination: "
                        f"YES highest_bid={yes_highest_bid:.4f}, NO highest_bid={no_highest_bid:.4f}"
                    )
                    
                    if yes_highest_bid >= 0.98:
                        winning_side = "YES"
                        logger.info(f"✅ YES won (YES highest_bid={yes_highest_bid:.4f} ≥ 0.98)")
                    elif no_highest_bid >= 0.98:
                        winning_side = "NO"
                        logger.info(f"✅ NO won (NO highest_bid={no_highest_bid:.4f} ≥ 0.98)")
                    else:
                        logger.info(
                            f"⚠️ Orderbook prices inconclusive: YES highest_bid={yes_highest_bid:.4f} < 0.98 "
                            f"and NO highest_bid={no_highest_bid:.4f} < 0.98. Falling back to outcomePrices."
                        )
                else:
                    logger.info(
                        f"⚠️ Incomplete orderbook price data: yes_highest_bid={yes_highest_bid}, "
                        f"no_highest_bid={no_highest_bid}. Falling back to outcomePrices."
                    )
            else:
                logger.info(
                    f"⚠️ No orderbook prices tracked for {trade.market_slug}. "
                    f"Falling back to outcomePrices for resolution determination."
                )
            
            # Fallback: Use outcomePrices if orderbooks are closed (404) or inconclusive
            if winning_side is None:
                logger.info(f"📊 Using outcomePrices fallback for resolution determination")
                if isinstance(outcome_prices_raw, list) and len(outcome_prices_raw) >= 2:
                    # List format: [YES_price, NO_price]
                    # Example: ["0", "1"] means NO won, ["1", "0"] means YES won
                    try:
                        yes_price = float(outcome_prices_raw[0])
                        no_price = float(outcome_prices_raw[1])
                        if yes_price == 1.0:
                            winning_side = "YES"
                        elif no_price == 1.0:
                            winning_side = "NO"
                        logger.info(
                            f"📊 Parsed winning side from list format: YES_price={yes_price}, NO_price={no_price}, "
                            f"winning_side={winning_side}"
                        )
                    except (ValueError, TypeError, IndexError) as e:
                        logger.warning(f"Could not parse winning side from list format: {e}")
                elif isinstance(outcome_prices_raw, dict):
                    # Dict format: {"Yes": 1, "No": 0} or similar
                    yes_value = outcome_prices_raw.get("Yes") or outcome_prices_raw.get("yes") or outcome_prices_raw.get("YES")
                    no_value = outcome_prices_raw.get("No") or outcome_prices_raw.get("no") or outcome_prices_raw.get("NO")
                    if yes_value == 1:
                        winning_side = "YES"
                    elif no_value == 1:
                        winning_side = "NO"
                    logger.info(
                        f"📊 Parsed winning side from dict format: Yes={yes_value}, No={no_value}, "
                        f"winning_side={winning_side}"
                    )
            
            if winning_side is None:
                logger.warning(
                    f"⚠️ Could not determine winning_side from orderbook prices or outcome_prices_raw={outcome_prices_raw} "
                    f"(type: {type(outcome_prices_raw).__name__}). Will use outcome_price fallback."
                )
            
            # Calculate payout and ROI based on actual proceeds from selling
            # ROI accounts for fees on both buy and sell orders
            # Use API result (sell_order_filled_via_api) not database field
            fee = trade.fee or 0.0
            sell_fee = trade.sell_fee or 0.0
            
            # Check if order was actually filled
            filled_shares = trade.filled_shares or 0.0
            dollars_spent = trade.dollars_spent or 0.0
            
            if filled_shares <= 0 or dollars_spent <= 0:
                logger.warning(
                    f"Trade {trade.id} was not filled (filled_shares={filled_shares}, "
                    f"dollars_spent=${dollars_spent:.2f}) - skipping resolution (order did not execute)"
                )
                return
            
            # Reload trade from database to get latest sell_shares_filled after retry loop
            trade = self.db.get_trade_by_id(trade.id)
            if not trade:
                logger.error(f"Trade {trade.id} not found after market resolution check")
                return
            
            if trade.sell_order_id and sell_order_filled_via_api:
                # Sell order filled (verified via API) - use actual proceeds
                # Use sell_shares_filled from database (which may be partial or full)
                # Default to sell_order_size if sell_shares_filled is not set (for backwards compatibility)
                sell_price = trade.sell_order_price or 0.99
                filled_shares_sold = trade.sell_shares_filled if trade.sell_shares_filled is not None else (trade.sell_order_size or filled_shares)
                sell_dollars_received = trade.sell_dollars_received or (filled_shares_sold * sell_price)
                
                # Calculate sell fee if not already set
                if not sell_fee:
                    from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                    sell_fee = calculate_polymarket_fee(sell_price, sell_dollars_received)
                
                # Check if this is a partial fill (some shares sold, but not all)
                is_partial_fill = filled_shares_sold < filled_shares - 0.01  # Use small epsilon for float comparison
                
                if is_partial_fill:
                    # Partial fill - need to account for remaining unfilled shares at market resolution
                    payout, net_payout, roi = calculate_payout_for_partial_fill(
                        sell_dollars_received=sell_dollars_received,
                        sell_fee=sell_fee,
                        filled_shares=filled_shares,
                        sell_shares_filled=filled_shares_sold,
                        outcome_price=outcome_price,
                        order_side=trade.order_side,
                        dollars_spent=dollars_spent,
                        buy_fee=fee,
                        winning_side=winning_side,
                    )
                    
                    remaining_shares = filled_shares - filled_shares_sold
                    logger.info(
                        f"Trade {trade.id} sell order PARTIAL FILL (verified via API): "
                        f"sell_price=${sell_price:.4f}, sold_shares={filled_shares_sold}/{filled_shares}, "
                        f"remaining_shares={remaining_shares:.2f}, "
                        f"payout=${payout:.2f} (${sell_dollars_received:.2f} from sale + ${payout - sell_dollars_received:.2f} from remaining), "
                        f"sell_fee=${sell_fee:.4f}, net_payout=${net_payout:.2f}, roi={roi*100:.2f}%"
                    )
                else:
                    # Full fill - all shares were sold
                    payout, net_payout, roi = calculate_payout_for_filled_sell(
                        sell_dollars_received=sell_dollars_received,
                        sell_fee=sell_fee,
                        dollars_spent=dollars_spent,
                        buy_fee=fee,
                    )
                    
                    logger.info(
                        f"Trade {trade.id} sell order FULLY FILLED (verified via API): "
                        f"sell_price=${sell_price:.4f}, filled_shares={filled_shares_sold}, "
                        f"payout=${payout:.2f}, sell_fee=${sell_fee:.4f}, "
                        f"net_payout=${net_payout:.2f}, roi={roi*100:.2f}%"
                    )
            elif not trade.sell_order_id or not sell_order_filled_via_api or trade.sell_order_status == "cancelled" or trade.sell_order_status == "failed":
                # Sell order didn't fill, doesn't exist, was cancelled, or failed
                # Calculate payout, net_payout, and ROI using utility function
                bet_won, payout, net_payout, roi = calculate_payout_for_unfilled_sell(
                    outcome_price=outcome_price,
                    filled_shares=filled_shares,
                    order_side=trade.order_side,
                    dollars_spent=dollars_spent,
                    buy_fee=fee,
                    winning_side=winning_side,
                )
                
                if not bet_won:
                    logger.info(
                        f"Trade {trade.id} lost - market resolved against us (outcome_price={outcome_price:.4f}). "
                        f"Sell order did not fill, was cancelled, or failed (sell_order_id={trade.sell_order_id}, "
                        f"status={trade.sell_order_status}, api_status={sell_order_api_status or 'N/A'}). "
                        f"All shares lost. Calculating ROI and updating principal."
                    )
                else:
                    # Update sell_fee in database if not already set (for tracking)
                    # Calculate estimated sell fee for logging
                    from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                    estimated_sell_fee = calculate_polymarket_fee(1.0, payout)  # Fee for selling at $1
                    
                    if not trade.sell_fee:
                        session = self.db.SessionLocal()
                        try:
                            trade_obj = session.query(RealTradeThreshold).filter_by(id=trade.id).first()
                            if trade_obj:
                                trade_obj.sell_fee = estimated_sell_fee
                                session.commit()
                                logger.debug(f"Updated sell_fee to ${estimated_sell_fee:.2f} for trade {trade.id}")
                        except Exception as e:
                            session.rollback()
                            logger.warning(f"Could not update sell_fee: {e}")
                        finally:
                            session.close()
                    
                    logger.info(
                        f"Trade {trade.id} won but sell order did not fill, was cancelled, or failed "
                        f"(sell_order_id={trade.sell_order_id}, status={trade.sell_order_status}, "
                        f"api_status={sell_order_api_status or 'N/A'}) - market resolved. "
                        f"Assuming claim at $1 per share (outcome_price={outcome_price:.4f}, "
                        f"filled_shares={filled_shares}). "
                        f"Calculating ROI with estimated sell fee (${estimated_sell_fee:.2f}) and updating principal."
                    )
            
            # Log outcome for debugging
            logger.info(
                f"Trade {trade.id} outcome: side={trade.order_side}, winning_side={winning_side}, "
                f"outcome_price={outcome_price:.4f}, "
                f"payout=${payout:.2f}, net_payout=${net_payout:.2f}, roi={roi*100:.2f}%"
            )
            
            # Update self.principal ONCE - only after market resolves and sell order status is verified via API
            # This is the SINGLE SOURCE OF TRUTH for principal updates
            # The database principal_after will be set to match this value
            # BUT: if always_use_initial_principal is True, never update self.principal
            new_principal = self.principal  # Default: keep current principal
            principal_updated = False
            
            if self.config.always_use_initial_principal:
                # Never update principal when always_use_initial_principal is True
                # Always use initial_principal for future trades
                logger.info(
                    f"always_use_initial_principal=true: Keeping principal at ${self.config.initial_principal:.2f} "
                    f"(net_payout=${net_payout:.2f} not applied to principal)"
                )
                new_principal = self.config.initial_principal
                # Reset self.principal to initial_principal to ensure it's never negative
                self.principal = self.config.initial_principal
                principal_updated = False  # Don't mark as updated since we're keeping it constant
            elif trade.sell_order_id and sell_order_filled_via_api:
                # Sell order filled (verified via API) - update principal based on actual net_payout
                # This is the ONLY place where self.principal is updated (single source of truth)
                old_principal = self.principal
                new_principal = self.principal + net_payout
                self.principal = new_principal
                principal_updated = True
                logger.info(
                    f"Sell order filled (verified via API) - updating principal at market resolution: "
                    f"${old_principal:.2f} -> ${new_principal:.2f} "
                    f"(net_payout=${net_payout:.2f}, market resolution for trade {trade.id})"
                )
            else:
                # Sell order didn't fill (or doesn't exist) - update principal based on calculated net_payout
                # net_payout was already calculated above based on whether we won or lost (using outcome_price)
                old_principal = self.principal
                new_principal = self.principal + net_payout
                self.principal = new_principal
                principal_updated = True
                if net_payout < 0:
                    logger.info(
                        f"Market ended - we lost. Updating principal: ${old_principal:.2f} -> ${new_principal:.2f} "
                        f"(net_payout=${net_payout:.2f})"
                    )
                else:
                    logger.info(
                        f"Market ended - we won but sell order didn't fill. "
                        f"Updating principal based on estimated claim value: ${old_principal:.2f} -> ${new_principal:.2f} "
                        f"(net_payout=${net_payout:.2f}, estimated sell_fee included)"
                    )
            
            # Log principal state after update for debugging
            logger.info(
                f"📊 Principal after trade {trade.id}: ${self.principal:.2f} "
                f"(was ${old_principal:.2f}, net_payout=${net_payout:.2f})"
            )
            
            # IMPORTANT: principal_after_value should be the new principal after this trade
            # This is what gets saved to the database for this specific trade
            # It should reflect the actual principal after processing this trade's outcome
            # Formula: principal_after = principal_before + net_payout
            # We use self.principal (which was updated above) as the source of truth
            
            # Verify principal consistency: check the absolute latest principal_after in database
            # (including negative values) to see what the database actually has
            # This is just for logging/debugging - we don't overwrite principal_after_value
            # because that should reflect THIS trade's outcome
            session = self.db.SessionLocal()
            try:
                latest_trade_db = session.query(RealTradeThreshold).filter(
                    RealTradeThreshold.deployment_id == self.deployment_id,
                    RealTradeThreshold.principal_after.isnot(None),
                    RealTradeThreshold.order_id.isnot(None),
                    RealTradeThreshold.market_resolved_at.isnot(None),
                    RealTradeThreshold.order_status != "failed",
                ).order_by(RealTradeThreshold.market_resolved_at.desc()).first()
                
                if latest_trade_db and latest_trade_db.id != trade.id:
                    # Check previous trade's principal_after (not the current one we're processing)
                    db_previous_principal = latest_trade_db.principal_after
                    db_positive_principal = self.db.get_latest_principal(deployment_id=self.deployment_id)
                    
                    # Check if in-memory principal matches what we expect based on previous trade
                    expected_principal = db_previous_principal + net_payout
                    if abs(expected_principal - self.principal) > 0.01:
                        logger.warning(
                            f"⚠️ Principal calculation mismatch detected! "
                            f"In-memory principal: ${self.principal:.2f}, "
                            f"Previous trade {latest_trade_db.id} principal_after: ${db_previous_principal:.2f}, "
                            f"Expected after this trade: ${expected_principal:.2f}, "
                            f"Database latest positive principal: ${db_positive_principal:.2f if db_positive_principal else 'N/A'}. "
                            f"Using calculated value (${new_principal:.2f}) for this trade."
                        )
                    else:
                        logger.debug(
                            f"✓ Principal calculation consistent: "
                            f"previous=${db_previous_principal:.2f}, "
                            f"after this trade=${self.principal:.2f}"
                        )
            except Exception as e:
                logger.error(f"Error checking principal consistency: {e}", exc_info=True)
            finally:
                session.close()
            
            # Calculate principal_after_value correctly: principal_before + net_payout
            # Use trade.principal_before (stored when trade was created) + net_payout
            # This ensures we're using the correct starting point, not relying on self.principal
            # which might have been updated incorrectly
            if principal_updated:
                # Use the trade's principal_before as the starting point to avoid any state issues
                principal_before_from_trade = trade.principal_before or self.principal
                principal_after_calculated = principal_before_from_trade + net_payout
                
                # Verify the calculation matches self.principal (with small tolerance for rounding)
                if abs(principal_after_calculated - self.principal) > 0.01:
                    logger.warning(
                        f"⚠️ Principal calculation discrepancy! "
                        f"principal_before (from trade): ${principal_before_from_trade:.2f}, "
                        f"net_payout: ${net_payout:.2f}, "
                        f"calculated principal_after: ${principal_after_calculated:.2f}, "
                        f"self.principal: ${self.principal:.2f}. "
                        f"Using calculated value to ensure correctness."
                    )
                    principal_after_value = principal_after_calculated
                    # Also update self.principal to match
                    self.principal = principal_after_calculated
                else:
                    principal_after_value = self.principal
                
                logger.info(
                    f"💾 Saving principal_after=${principal_after_value:.2f} to database for trade {trade.id} "
                    f"(principal_before=${principal_before_from_trade:.2f}, net_payout=${net_payout:.2f})"
                )
            else:
                principal_after_value = None
            
            # If sell order already filled (verified via API), use the recalculated values from API
            if trade.sell_order_id and sell_order_filled_via_api:
                # Use the values we just calculated based on API-verified sell order fill
                # These are the correct values based on actual sell proceeds
                self.db.update_trade_outcome(
                    trade_id=trade.id,
                    outcome_price=outcome_price,
                    payout=payout,  # Use recalculated payout from API
                    net_payout=net_payout,  # Use recalculated net_payout from API
                    roi=roi,  # Use recalculated ROI from API
                    is_win=None,  # Leave is_win as NULL
                    principal_after=principal_after_value,  # Use recalculated principal_after
                    winning_side=winning_side,
                )
            else:
                # Sell order didn't fill - use calculated values
                self.db.update_trade_outcome(
                    trade_id=trade.id,
                    outcome_price=outcome_price,
                    payout=payout,
                    net_payout=net_payout,
                    roi=roi,
                    is_win=None,  # Leave is_win as NULL
                    principal_after=principal_after_value,
                    winning_side=winning_side,
                )
            
            logger.info(
                f"Market {trade.market_slug} resolved: "
                f"side={trade.order_side}, outcome={outcome_price:.4f}, "
                f"payout=${payout:.2f}, net=${net_payout:.2f}, "
                f"ROI={roi*100:.2f}%, new_principal=${new_principal:.2f}"
            )
            
            # Remove from monitored markets if still there
            self.monitored_markets.pop(trade.market_slug, None)
        
        except Exception as e:
            logger.error(f"Error processing market resolution: {e}", exc_info=True)


def main():
    """Main entry point with comprehensive error handling."""
    try:
        parser = argparse.ArgumentParser(description="Trade threshold strategy")
        parser.add_argument(
            "--config",
            type=str,
            required=True,
            help="Path to JSON config file",
        )
        
        args = parser.parse_args()
        
        logger.info("=" * 80)
        logger.info("STARTING TRADE THRESHOLD STRATEGY")
        logger.info("=" * 80)
        logger.info(f"Config file: {args.config}")
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Working directory: {os.getcwd()}")
        logger.info("=" * 80)
        
        # Flush logs immediately
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Initialize trader (this can fail)
        logger.info("Initializing trader...")
        try:
            trader = ThresholdTrader(args.config)
            logger.info("Trader initialized successfully")
        except Exception as e:
            logger.error("=" * 80)
            logger.error("CRITICAL ERROR: Failed to initialize trader")
            logger.error("=" * 80)
            logger.error(f"Error type: {type(e).__name__}")
            logger.error(f"Error message: {str(e)}")
            logger.error("Full traceback:", exc_info=True)
            sys.stdout.flush()
            sys.stderr.flush()
            raise
        
        # Start trading loop (this can also fail)
        logger.info("Starting trading loop...")
        sys.stdout.flush()
        sys.stderr.flush()
        
        try:
            asyncio.run(trader.start())
        except KeyboardInterrupt:
            logger.info("=" * 80)
            logger.info("Received shutdown signal (KeyboardInterrupt)")
            logger.info("=" * 80)
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception as e:
            logger.error("=" * 80)
            logger.error("CRITICAL ERROR: Trading loop crashed")
            logger.error("=" * 80)
            logger.error(f"Error type: {type(e).__name__}")
            logger.error(f"Error message: {str(e)}")
            logger.error("Full traceback:", exc_info=True)
            sys.stdout.flush()
            sys.stderr.flush()
            raise
            
    except Exception as e:
        # Catch-all for any unhandled exceptions
        logger.error("=" * 80)
        logger.error("FATAL ERROR: Unhandled exception in main()")
        logger.error("=" * 80)
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        logger.error("Full traceback:", exc_info=True)
        sys.stdout.flush()
        sys.stderr.flush()
        # Re-raise to ensure process exits with error code
        raise
    finally:
        logger.info("=" * 80)
        logger.info("Script exiting")
        logger.info("=" * 80)
        sys.stdout.flush()
        sys.stderr.flush()


if __name__ == "__main__":
    main()
