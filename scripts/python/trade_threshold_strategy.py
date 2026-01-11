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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True
)
logging.getLogger().handlers[0].stream = sys.stdout
logger = logging.getLogger(__name__)

# Minimum bet size
MIN_BET_SIZE = 1.0


class ThresholdTrader:
    """Main trading class for threshold strategy."""
    
    def __init__(self, config_path: str):
        """Initialize trader with config."""
        # Proxy is already configured at module level before imports
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
        # Only use principal from resolved trades from THIS deployment
        # If no trades from current deployment, use initial_principal from config
        latest_principal = self.db.get_latest_principal(deployment_id=self.deployment_id)
        if latest_principal is not None and latest_principal > 0:
            self.principal = latest_principal
            logger.info(f"Loaded principal from database (deployment {self.deployment_id[:8]}...): ${self.principal:.2f}")
        else:
            # Check if there are any resolved trades from previous deployments
            any_principal = self.db.get_latest_principal(deployment_id=None)
            if any_principal is not None and any_principal > 0:
                logger.info(
                    f"Found principal ${any_principal:.2f} from previous deployment, "
                    f"but using initial_principal from config for new deployment"
                )
            
            self.principal = self.config.initial_principal
            logger.info(f"Using initial principal from config: ${self.principal:.2f}")
            logger.info("(No resolved trades found for current deployment, using initial principal)")
        
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
        
        # Timing
        self.orderbook_poll_interval = 10.0  # seconds
        self.order_status_check_interval = 10.0  # seconds
        self.market_resolution_check_interval = 30.0  # seconds
        
        self.running = False
    
    async def start(self):
        """Start the trading loop."""
        logger.info("=" * 80)
        logger.info("STARTING THRESHOLD STRATEGY TRADER")
        logger.info("=" * 80)
        logger.info(f"Market type: {self.config.market_type}")
        logger.info(f"Threshold: {self.config.threshold:.4f}")
        logger.info(f"Upper threshold: {self.config.upper_threshold:.4f}")
        logger.info(f"Margin: {self.config.margin:.4f}")
        logger.info(f"Threshold sell (stop-loss): {self.config.threshold_sell:.4f}")
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
        
        # Resume monitoring markets we've bet on
        await self._resume_monitoring()
        
        self.running = True
        
        # Start background tasks
        tasks = [
            asyncio.create_task(self._market_detection_loop()),
            asyncio.create_task(self._orderbook_monitoring_loop()),
            asyncio.create_task(self._order_status_loop()),
            asyncio.create_task(self._market_resolution_loop()),
        ]
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        finally:
            self.running = False
            logger.info("Trading stopped")
    
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
        """Continuously detect new markets (like monitor_btc_markets.py)."""
        check_interval = 60.0  # Check for new markets every 60 seconds
        
        while self.running:
            try:
                await self._check_for_new_markets()
            except Exception as e:
                logger.error(f"Error in market detection: {e}", exc_info=True)
            
            await asyncio.sleep(check_interval)
    
    async def _check_for_new_markets(self):
        """Check for new markets of the configured type."""
        if self.config.market_type == "15m":
            await self._check_15m_markets()
        else:
            await self._check_1h_markets()
    
    async def _check_15m_markets(self):
        """Check for new 15-minute markets."""
        try:
            latest_market = get_latest_btc_15m_market_proactive()
            markets = [latest_market] if latest_market else get_all_active_btc_15m_markets()
            
            for market in markets:
                event_slug = market.get("_event_slug", "")
                if not event_slug:
                    continue
                
                # Skip if already monitoring or already bet on
                if event_slug in self.monitored_markets or event_slug in self.markets_with_bets:
                    continue
                
                # Only monitor currently running markets
                if not is_market_currently_running(market):
                    continue
                
                # Extract token IDs
                token_ids = get_token_ids_from_market(market)
                if not token_ids or len(token_ids) < 2:
                    continue
                
                # Add to monitored markets
                self.monitored_markets[event_slug] = {
                    "market": market,
                    "token_ids": token_ids,
                    "yes_token_id": token_ids[0],
                    "no_token_id": token_ids[1],
                }
                logger.info(f"Added new 15m market to monitoring: {event_slug}")
        
        except Exception as e:
            logger.error(f"Error checking 15m markets: {e}", exc_info=True)
    
    async def _check_1h_markets(self):
        """Check for new 1-hour markets."""
        try:
            latest_market = get_latest_btc_1h_market_proactive()
            markets = [latest_market] if latest_market else get_all_active_btc_1h_markets()
            
            for market in markets:
                event_slug = market.get("_event_slug", "")
                if not event_slug:
                    continue
                
                # Skip if already monitoring or already bet on
                if event_slug in self.monitored_markets or event_slug in self.markets_with_bets:
                    continue
                
                # Only monitor currently running markets
                if not is_market_currently_running(market):
                    continue
                
                # Extract token IDs
                token_ids = get_token_ids_from_market(market)
                if not token_ids or len(token_ids) < 2:
                    continue
                
                # Add to monitored markets
                self.monitored_markets[event_slug] = {
                    "market": market,
                    "token_ids": token_ids,
                    "yes_token_id": token_ids[0],
                    "no_token_id": token_ids[1],
                }
                logger.info(f"Added new 1h market to monitoring: {event_slug}")
        
        except Exception as e:
            logger.error(f"Error checking 1h markets: {e}", exc_info=True)
    
    async def _orderbook_monitoring_loop(self):
        """Poll orderbooks and check for threshold triggers."""
        while self.running:
            try:
                await self._check_orderbooks_for_triggers()
            except Exception as e:
                logger.error(f"Error in orderbook monitoring: {e}", exc_info=True)
            
            await asyncio.sleep(self.orderbook_poll_interval)
    
    async def _check_orderbooks_for_triggers(self):
        """Check all monitored markets for threshold triggers."""
        # Don't place new bets if we have open buy orders
        if self.open_trades:
            return
        
        # Don't place new bets if we have open sell orders (wait for proceeds to be claimed)
        if self.open_sell_orders:
            return
        
        # Also check database for open sell orders (in case script restarted)
        open_sell_orders_from_db = self.db.get_open_sell_orders(deployment_id=self.deployment_id)
        if open_sell_orders_from_db:
            return
        
        # Don't place new bets if principal is too low
        if self.principal < MIN_BET_SIZE:
            logger.warning(f"Principal ${self.principal:.2f} is below minimum bet size ${MIN_BET_SIZE:.2f}")
            return
        
        # Check wallet balance before placing orders
        try:
            wallet_balance = self.pm.get_polymarket_balance()
            if wallet_balance is None:
                logger.warning("Could not check wallet balance - skipping order placement")
                return
            
            amount_invested = self.config.get_amount_invested(self.principal)
            if wallet_balance < amount_invested:
                logger.warning(
                    f"Insufficient wallet balance: ${wallet_balance:.2f} < ${amount_invested:.2f} "
                    f"(required for order). Skipping order placement."
                )
                return
        except Exception as e:
            logger.error(f"Error checking wallet balance: {e}")
            # Continue anyway - let the order fail if balance is insufficient
        
        for market_slug, market_info in list(self.monitored_markets.items()):
            # Skip if already bet on (check both memory and database)
            if market_slug in self.markets_with_bets:
                continue
            
            # Also check database to prevent duplicate orders across ALL deployments for THIS market
            # This prevents duplicate orders when:
            # - Script is redeployed and restarted
            # - Multiple deployments are running simultaneously
            # Only checks the CURRENT market - other markets' positions don't matter
            if self.db.has_bet_on_market(market_slug):
                logger.info(
                    f"Market {market_slug} already has an active bet (open order or unresolved position) "
                    f"in database from ANY deployment, skipping to prevent duplicate orders"
                )
                self.markets_with_bets.add(market_slug)  # Add to memory set
                continue
            
            # Skip if market is not active
            market = market_info["market"]
            if not is_market_active(market):
                logger.info(f"Market {market_slug} is no longer active, removing from monitoring")
                self.monitored_markets.pop(market_slug, None)
                continue
            
            # Fetch orderbooks for YES and NO
            yes_token_id = market_info["yes_token_id"]
            no_token_id = market_info["no_token_id"]
            
            yes_orderbook = fetch_orderbook(yes_token_id)
            no_orderbook = fetch_orderbook(no_token_id)
            
            if not yes_orderbook or not no_orderbook:
                continue
            
            # Check if threshold is triggered
            trigger = check_threshold_triggered(
                yes_orderbook,
                no_orderbook,
                self.config.threshold,
            )
            
            if trigger:
                side, lowest_ask = trigger
                
                # Check upper threshold - don't place order if price is too high
                if lowest_ask > self.config.upper_threshold:
                    logger.info(
                        f"Threshold triggered for {market_slug}: {side} side, lowest_ask={lowest_ask:.4f}, "
                        f"but above upper_threshold={self.config.upper_threshold:.4f} - skipping order"
                    )
                    continue
                
                logger.info(f"Threshold triggered for {market_slug}: {side} side, lowest_ask={lowest_ask:.4f}")
                
                # Mark market as bet on IMMEDIATELY to prevent buying both YES and NO
                # This prevents race condition where both sides trigger in same loop iteration
                self.markets_with_bets.add(market_slug)
                
                # Place order
                await self._place_order(market_slug, market_info, side, lowest_ask)
        
        # Check for early sell conditions on filled buy orders
        await self._check_early_sell_conditions()
    
    async def _check_early_sell_conditions(self):
        """Check if the most recent filled buy order should trigger early sell (stop-loss)."""
        # Get only the most recent filled trade from current deployment that doesn't have a sell order yet
        trade = self.db.get_most_recent_filled_trade_without_sell(deployment_id=self.deployment_id)
        
        if not trade:
            return
        
        try:
            # Skip if market already resolved
            if trade.market_resolved_at:
                return
            
            # Skip if already have a sell order (double-check)
            if trade.sell_order_id:
                return
            
            # Skip if no filled shares
            if not trade.filled_shares or trade.filled_shares <= 0:
                return
            
            # Fetch orderbook for the token we bought
            orderbook = fetch_orderbook(trade.token_id)
            if not orderbook:
                return
            
            # Get highest bid
            highest_bid = get_highest_bid(orderbook)
            if highest_bid is None:
                return
            
            # Check if highest_bid < threshold_sell
            if highest_bid < self.config.threshold_sell:
                # Place early sell order
                sell_price = self.config.threshold_sell - self.config.margin_sell
                if sell_price < 0.01:
                    sell_price = 0.01  # Minimum price
                
                logger.info(
                    f"Early sell triggered for trade {trade.id} (most recent): "
                    f"highest_bid={highest_bid:.4f} < threshold_sell={self.config.threshold_sell:.4f}, "
                    f"placing sell order at {sell_price:.4f}"
                )
                
                await self._place_early_sell_order(trade, sell_price)
        
        except Exception as e:
            logger.error(f"Error checking early sell condition for trade {trade.id}: {e}", exc_info=True)
    
    async def _place_initial_sell_order(self, trade: RealTradeThreshold):
        """Place initial sell order at 0.99 immediately when buy order fills."""
        trade_id = trade.id  # Save ID before reloading
        try:
            # Reload trade from database to ensure we have latest data
            trade = self.db.get_trade_by_id(trade_id)
            if not trade:
                logger.error(f"Trade {trade_id} not found in database when placing sell order")
                return
            
            # Skip if already have a sell order
            if trade.sell_order_id:
                logger.debug(f"Trade {trade.id} already has sell order {trade.sell_order_id}, skipping")
                return
            
            # Skip if no filled shares
            if not trade.filled_shares or trade.filled_shares <= 0:
                logger.warning(
                    f"Trade {trade.id} has no filled shares (filled_shares={trade.filled_shares}), "
                    f"cannot place sell order"
                )
                return
            
            if not trade.token_id:
                logger.error(f"Trade {trade.id} has no token_id, cannot place sell order")
                return
            
            logger.info(
                f"Placing initial sell order at $0.99 for {trade.filled_shares} shares "
                f"(trade {trade.id}, token_id={trade.token_id})"
            )
            
            sell_order_response = self.pm.execute_order(
                price=0.99,
                size=int(trade.filled_shares),  # Ensure integer size
                side=SELL,
                token_id=trade.token_id,
            )
            
            if sell_order_response:
                sell_order_id = self.pm.extract_order_id(sell_order_response)
                if sell_order_id:
                    # Log sell order to database
                    self.db.update_sell_order(
                        trade_id=trade.id,
                        sell_order_id=sell_order_id,
                        sell_order_price=0.99,
                        sell_order_size=trade.filled_shares,
                        sell_order_status="open",
                    )
                    # Track in memory
                    self.open_sell_orders[sell_order_id] = trade.id
                    logger.info(
                        f"✓ Initial sell order placed at $0.99: order_id={sell_order_id}, "
                        f"size={trade.filled_shares} shares, logged to database"
                    )
                else:
                    logger.error(
                        f"Initial sell order placed but could not extract order ID. "
                        f"Response: {sell_order_response}"
                    )
            else:
                logger.error(
                    f"Failed to place initial sell order for trade {trade.id}. "
                    f"execute_order returned None or empty response"
                )
        except Exception as e:
            logger.error(
                f"Error placing initial sell order for trade {trade.id}: {e}", 
                exc_info=True
            )
    
    async def _place_early_sell_order(self, trade: RealTradeThreshold, sell_price: float):
        """Place an early sell order (stop-loss), canceling the 0.99 order first if it exists."""
        try:
            # If there's an existing sell order at 0.99, cancel it first
            if trade.sell_order_id and trade.sell_order_status == "open":
                logger.info(
                    f"Canceling existing sell order {trade.sell_order_id} at $0.99 "
                    f"before placing early sell at ${sell_price:.4f}"
                )
                cancel_response = self.pm.cancel_order(trade.sell_order_id)
                if cancel_response:
                    logger.info(f"✓ Canceled sell order {trade.sell_order_id}")
                    # Remove from tracking
                    self.open_sell_orders.pop(trade.sell_order_id, None)
                    # Update database to mark as cancelled
                    self.db.update_sell_order_fill(
                        trade_id=trade.id,
                        sell_order_status="cancelled",
                    )
                else:
                    logger.warning(f"Failed to cancel sell order {trade.sell_order_id}, proceeding anyway")
            
            # Place new early sell order
            sell_order_response = self.pm.execute_order(
                price=sell_price,
                size=trade.filled_shares,
                side=SELL,
                token_id=trade.token_id,
            )
            
            if sell_order_response:
                sell_order_id = self.pm.extract_order_id(sell_order_response)
                if sell_order_id:
                    # Log sell order to database
                    self.db.update_sell_order(
                        trade_id=trade.id,
                        sell_order_id=sell_order_id,
                        sell_order_price=sell_price,
                        sell_order_size=trade.filled_shares,
                        sell_order_status="open",
                    )
                    # Track in memory
                    self.open_sell_orders[sell_order_id] = trade.id
                    # Mark market as bet on (don't buy again in this market)
                    self.markets_with_bets.add(trade.market_slug)
                    logger.info(
                        f"✓ Early sell order placed: order_id={sell_order_id}, "
                        f"price={sell_price:.4f}, size={trade.filled_shares} shares"
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
    ):
        """Place a limit order when threshold is triggered."""
        market = market_info["market"]
        market_id = market.get("id", "unknown")
        token_id = market_info["yes_token_id"] if side == "YES" else market_info["no_token_id"]
        
        # Calculate order parameters
        order_price = trigger_price + self.config.margin
        if order_price > 0.99:
            order_price = 0.99  # Cap at 0.99
        
        amount_invested = self.config.get_amount_invested(self.principal)
        
        # Log if bet limit is capping the Kelly-calculated amount
        kelly_amount = self.principal * self.config.kelly_fraction * self.config.kelly_scale_factor
        if amount_invested < kelly_amount:
            logger.info(
                f"Bet size capped by dollar_bet_limit: Kelly suggests ${kelly_amount:.2f}, "
                f"but limited to ${amount_invested:.2f} (dollar_bet_limit=${self.config.dollar_bet_limit:.2f})"
            )
        
        order_size = int(amount_invested / order_price)  # Round down to whole shares
        
        # Calculate actual order value
        order_value = order_size * order_price
        
        if order_size < 1:
            logger.warning(f"Order size too small: {order_size} shares (amount_invested=${amount_invested:.2f})")
            return
        
        # Verify market is still active before placing order
        if not is_market_active(market):
            logger.warning(f"Market {market_slug} is no longer active, skipping order")
            return
        
        logger.info(
            f"Placing LIMIT order: {side} side, limit_price={order_price:.4f}, size={order_size} shares, "
            f"order_value=${order_value:.2f} (amount_invested=${amount_invested:.2f}, principal=${self.principal:.2f})"
        )
        logger.info(
            f"  Order details: trigger_price={trigger_price:.4f}, margin={self.config.margin:.4f}, "
            f"calculated_limit_price={order_price:.4f}"
        )
        
        # Place order with retry logic
        order_response = None
        for attempt in range(3):
            try:
                order_response = self.pm.execute_order(
                    price=order_price,
                    size=order_size,
                    side="BUY",
                    token_id=token_id,
                )
                
                if order_response:
                    break
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Order placement attempt {attempt + 1} failed: {e}")
                
                # Check for specific error types
                if "not enough balance" in error_msg.lower() or "allowance" in error_msg.lower():
                    # Check current balance for better error message
                    try:
                        current_balance = self.pm.get_polymarket_balance()
                        if current_balance is not None:
                            logger.error(
                                f"Insufficient balance/allowance: wallet has ${current_balance:.2f}, "
                                f"need ${amount_invested:.2f} (order_size={order_size}, price={order_price:.4f})"
                            )
                        else:
                            logger.error(
                                f"Insufficient balance/allowance: could not check balance. "
                                f"Order requires ${amount_invested:.2f} (order_size={order_size}, price={order_price:.4f})"
                            )
                    except Exception as balance_error:
                        logger.error(f"Could not check balance: {balance_error}")
                    
                    # Don't retry balance errors - they won't resolve quickly
                    logger.error("Stopping retries due to balance/allowance error")
                    return
                
                if attempt < 2:
                    await asyncio.sleep(5.0)  # Wait 5 seconds before retry
                else:
                    logger.error(f"Failed to place order after 3 attempts - not creating trade record")
                    # Don't create trade record for failed orders - principal shouldn't change
                    # Only log the error without creating a database entry
                    return
        
        if not order_response:
            return
        
        # Extract order ID
        order_id = self.pm.extract_order_id(order_response)
        if not order_id:
            logger.error("Could not extract order ID from response")
            return
        
        # Create trade record
        trade_id = self.db.create_trade(
            deployment_id=self.deployment_id,
            threshold=self.config.threshold,
            margin=self.config.margin,
            kelly_fraction=self.config.kelly_fraction,
            kelly_scale_factor=self.config.kelly_scale_factor,
            market_type=self.config.market_type,
            market_id=market_id,
            market_slug=market_slug,
            token_id=token_id,
            order_id=order_id,
            order_price=order_price,
            order_size=order_size,
            order_side=side,
            principal_before=self.principal,
            order_status="open",
        )
        
        # Track order (market already marked as bet on BEFORE placing order to prevent buying both YES and NO)
        self.open_trades[order_id] = trade_id
        # Note: markets_with_bets.add() is called in _check_orderbooks_for_triggers BEFORE _place_order
        logger.info(f"Order placed: order_id={order_id}, trade_id={trade_id}")
    
    async def _order_status_loop(self):
        """Check order status every 10 seconds."""
        while self.running:
            try:
                await self._check_order_statuses()
            except Exception as e:
                logger.error(f"Error checking order status: {e}", exc_info=True)
            
            await asyncio.sleep(self.order_status_check_interval)
    
    async def _check_order_statuses(self):
        """Check status of all open orders."""
        # Get all open trades from database for current deployment (not just those in self.open_trades)
        # This handles cases where script restarted or order filled quickly
        open_trades_from_db = self.db.get_open_trades(deployment_id=self.deployment_id)
        
        # Also check trades in memory
        all_trades_to_check = {}
        for trade in open_trades_from_db:
            if trade.order_id:
                all_trades_to_check[trade.order_id] = trade.id
        
        # Add any trades from memory that might not be in DB yet
        for order_id, trade_id in self.open_trades.items():
            if order_id not in all_trades_to_check:
                all_trades_to_check[order_id] = trade_id
        
        if not all_trades_to_check:
            return
        
        # First, check fills/trades to see if any orders have been filled
        # This is more reliable than get_order_status for filled orders
        # Note: get_order() has a known issue (py-clob-client #217) where it doesn't return filled orders
        try:
            fills = self.pm.get_trades()
            if fills:
                for fill in fills:
                    # Try multiple field names for order ID (taker_order_id is the actual field name)
                    fill_order_id = (
                        fill.get("taker_order_id") or 
                        fill.get("orderID") or 
                        fill.get("order_id") or 
                        fill.get("id")
                    )
                    if fill_order_id in all_trades_to_check:
                        trade_id = all_trades_to_check[fill_order_id]
                        trade = self.db.get_trade_by_id(trade_id)
                        if trade and not trade.filled_shares:
                            # Order was filled - extract fill details
                            # The 'size' field contains the filled shares
                            filled_shares = fill.get("size")
                            fill_price_from_record = fill.get("price")
                            
                            # Convert to float if they're strings
                            if filled_shares:
                                filled_shares = float(filled_shares)
                            else:
                                filled_shares = trade.order_size  # Fallback to order size
                            
                            # Use the actual fill price from the fill record (what Polymarket UI shows)
                            # This is the real execution price, even if it seems different from limit price
                            if fill_price_from_record:
                                fill_price = float(fill_price_from_record)
                            else:
                                fill_price = trade.order_price  # Fallback to order price
                            
                            # Log if fill price differs significantly from limit price (investigation)
                            price_diff = abs(fill_price - trade.order_price)
                            if price_diff > 0.01:
                                logger.warning(
                                    f"⚠️ Fill price ({fill_price:.4f}) differs significantly from limit price ({trade.order_price:.4f}) "
                                    f"for order {fill_order_id}. Difference: {price_diff:.4f}. "
                                    f"Limit BUY orders should only fill at limit price or better (lower). "
                                    f"Using actual fill price from Polymarket ({fill_price:.4f}) as shown in UI."
                                )
                            else:
                                logger.info(
                                    f"✓ Fill price ({fill_price:.4f}) matches limit price ({trade.order_price:.4f}) "
                                    f"for order {fill_order_id}"
                                )
                            
                            dollars_spent = filled_shares * fill_price
                            
                            from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                            fee = calculate_polymarket_fee(fill_price, dollars_spent)
                            
                            self.db.update_trade_fill(
                                trade_id=trade_id,
                                filled_shares=filled_shares,
                                fill_price=fill_price,
                                dollars_spent=dollars_spent,
                                fee=fee,
                                order_status="filled",
                            )
                            self.open_trades.pop(fill_order_id, None)
                            self.orders_not_found.pop(fill_order_id, None)
                            logger.info(f"Order {fill_order_id} filled (found in trades): {filled_shares} shares at ${fill_price:.4f}")
                            
                            # Reload trade from database to get updated filled_shares
                            trade = self.db.get_trade_by_id(trade_id)
                            if trade:
                                # Immediately place sell order at 0.99 when buy fills
                                await self._place_initial_sell_order(trade)
                            
                            # Remove from all_trades_to_check so we don't check it again below
                            all_trades_to_check.pop(fill_order_id, None)
        except Exception as e:
            logger.debug(f"Could not check fills/trades: {e}")
        
        # Also check open orders - if order is NOT in open orders, it's likely filled
        try:
            open_orders = self.pm.get_open_orders()
            open_order_ids = set()
            if open_orders:
                for o in open_orders:
                    oid = o.get("orderID") or o.get("order_id") or o.get("id")
                    if oid:
                        open_order_ids.add(oid)
            
            # Check orders that are NOT in open orders list
            for order_id, trade_id in list(all_trades_to_check.items()):
                if order_id not in open_order_ids:
                    # Order is not in open orders - check if we already marked it as filled
                    trade = self.db.get_trade_by_id(trade_id)
                    if trade and not trade.filled_shares and trade.order_status == "open":
                        # Not filled yet in DB - mark as filled
                        logger.info(f"Order {order_id} not in open orders - marking as filled")
                        # Use order_size as filled_shares (best guess)
                        if trade.order_size:
                            from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                            dollars_spent = trade.order_size * trade.order_price
                            fee = calculate_polymarket_fee(trade.order_price, dollars_spent)
                            self.db.update_trade_fill(
                                trade_id=trade_id,
                                filled_shares=trade.order_size,
                                fill_price=trade.order_price,
                                dollars_spent=dollars_spent,
                                fee=fee,
                                order_status="filled",
                            )
                            self.open_trades.pop(order_id, None)
                            self.orders_not_found.pop(order_id, None)
                            logger.info(f"Order {order_id} marked as filled (not in open orders): {trade.order_size} shares")
                            
                            # Reload trade from database to get updated filled_shares
                            trade = self.db.get_trade_by_id(trade_id)
                            if trade:
                                # Immediately place sell order at 0.99 when buy fills
                                await self._place_initial_sell_order(trade)
                            
                            # Remove from all_trades_to_check so we don't check it again below
                            all_trades_to_check.pop(order_id, None)
        except Exception as e:
            logger.debug(f"Could not check open orders: {e}")
        
        # Now check individual order statuses for orders that are still open
        for order_id, trade_id in list(all_trades_to_check.items()):
            try:
                order_status = self.pm.get_order_status(order_id)
                if not order_status:
                    # If order not found, check retry count
                    retry_count = self.orders_not_found.get(order_id, 0)
                    
                    if retry_count < self.max_order_not_found_retries:
                        # Increment retry count and try again next time
                        self.orders_not_found[order_id] = retry_count + 1
                        logger.debug(
                            f"Order {order_id} not found in API (retry {retry_count + 1}/{self.max_order_not_found_retries}) - "
                            f"will retry on next check"
                        )
                        continue
                    else:
                        # Max retries reached - already checked fills and open orders above
                        # If still not found, remove from tracking
                        self.open_trades.pop(order_id, None)
                        self.orders_not_found.pop(order_id, None)
                    continue
                
                # Order found - clear retry count
                self.orders_not_found.pop(order_id, None)
                
                # Parse order status
                status = order_status.get("status", "unknown")
                filled_amount = order_status.get("filledAmount", order_status.get("filled_amount", 0))
                total_amount = order_status.get("totalAmount", order_status.get("total_amount", 0))
                
                trade = self.db.get_trade_by_id(trade_id)
                if not trade:
                    continue
                
                # Check if order is filled or cancelled
                # Handle various status values that might indicate filled
                is_filled = status in ["filled", "FILLED", "complete", "COMPLETE"] or (
                    filled_amount and total_amount and float(filled_amount) >= float(total_amount)
                )
                is_cancelled = status in ["cancelled", "CANCELLED", "canceled", "CANCELED"]
                
                if is_filled or is_cancelled:
                    if is_filled and not trade.filled_shares:
                        # Update trade with fill information
                        # For limit orders, fill price is the limit price (or better)
                        filled_shares = float(filled_amount) if filled_amount else trade.order_size
                        fill_price = trade.order_price  # Use limit order price as fill price
                        dollars_spent = filled_shares * fill_price
                        
                        # Calculate fee (simplified - actual fee may vary)
                        from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                        fee = calculate_polymarket_fee(fill_price, dollars_spent)
                        
                        self.db.update_trade_fill(
                            trade_id=trade_id,
                            filled_shares=filled_shares,
                            fill_price=fill_price,
                            dollars_spent=dollars_spent,
                            fee=fee,
                            order_status="filled",
                        )
                        logger.info(f"Order {order_id} filled: {filled_shares} shares")
                        
                        # Reload trade from database to get updated filled_shares
                        trade = self.db.get_trade_by_id(trade_id)
                        if trade:
                            # Immediately place sell order at 0.99 when buy fills
                            await self._place_initial_sell_order(trade)
                    
                    # Remove from open trades and clear retry count
                    self.open_trades.pop(order_id, None)
                    self.orders_not_found.pop(order_id, None)
                
                elif status == "open" and filled_amount and float(filled_amount) > 0:
                    # Partial fill - update trade
                    filled_shares = float(filled_amount)
                    fill_price = trade.order_price
                    dollars_spent = filled_shares * fill_price
                    
                    from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                    fee = calculate_polymarket_fee(fill_price, dollars_spent)
                    
                    self.db.update_trade_fill(
                        trade_id=trade_id,
                        filled_shares=filled_shares,
                        fill_price=fill_price,
                        dollars_spent=dollars_spent,
                        fee=fee,
                        order_status="partial",
                    )
                    
                    # Try to cancel remaining portion
                    try:
                        self.pm.cancel_order(order_id)
                        logger.info(f"Cancelled remaining portion of order {order_id}")
                    except Exception as e:
                        logger.warning(f"Could not cancel order {order_id}: {e}")
            
            except Exception as e:
                logger.error(f"Error checking order {order_id}: {e}", exc_info=True)
        
        # Also check sell orders
        await self._check_sell_order_statuses()
    
    async def _check_sell_order_statuses(self):
        """Check status of all open sell orders."""
        # Get all open sell orders from database for current deployment
        open_sell_orders_from_db = self.db.get_open_sell_orders(deployment_id=self.deployment_id)
        
        # Also check sell orders in memory
        all_sell_orders_to_check = {}
        for trade in open_sell_orders_from_db:
            if trade.sell_order_id:
                all_sell_orders_to_check[trade.sell_order_id] = trade.id
        
        # Add any sell orders from memory that might not be in DB yet
        for sell_order_id, trade_id in self.open_sell_orders.items():
            if sell_order_id not in all_sell_orders_to_check:
                all_sell_orders_to_check[sell_order_id] = trade_id
        
        if not all_sell_orders_to_check:
            return
        
        # First, check fills/trades to see if any sell orders have been filled
        # This is more reliable than get_order_status for filled orders
        try:
            fills = self.pm.get_trades()
            if fills:
                for fill in fills:
                    # Try multiple field names for order ID (taker_order_id is the actual field name)
                    fill_order_id = (
                        fill.get("taker_order_id") or 
                        fill.get("orderID") or 
                        fill.get("order_id") or 
                        fill.get("id")
                    )
                    if fill_order_id in all_sell_orders_to_check:
                        trade_id = all_sell_orders_to_check[fill_order_id]
                        trade = self.db.get_trade_by_id(trade_id)
                        if trade and trade.sell_order_status == "open":
                            # Sell order was filled - extract fill details
                            filled_shares = fill.get("size")
                            fill_price_from_record = fill.get("price")
                            
                            # Convert to float if they're strings
                            if filled_shares:
                                filled_shares = float(filled_shares)
                            else:
                                filled_shares = trade.sell_order_size  # Fallback to order size
                            
                            # Use the actual fill price from the fill record
                            if fill_price_from_record:
                                sell_price = float(fill_price_from_record)
                            else:
                                sell_price = trade.sell_order_price or 0.99  # Fallback to order price
                            
                            sell_dollars_received = filled_shares * sell_price
                            
                            from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                            sell_fee = calculate_polymarket_fee(sell_price, sell_dollars_received)
                            
                            self.db.update_sell_order_fill(
                                trade_id=trade_id,
                                sell_order_status="filled",
                                sell_dollars_received=sell_dollars_received,
                                sell_fee=sell_fee,
                            )
                            
                            # Update principal now that sell order filled
                            net_proceeds = sell_dollars_received - sell_fee - (trade.dollars_spent or 0) - (trade.fee or 0)
                            new_principal = self.principal + net_proceeds
                            self.principal = new_principal
                            
                            # Update trade with final principal
                            if trade.market_resolved_at:
                                # Market already resolved - update with outcome info
                                self.db.update_trade_outcome(
                                    trade_id=trade_id,
                                    outcome_price=trade.outcome_price,
                                    payout=sell_dollars_received,
                                    net_payout=net_proceeds,
                                    roi=net_proceeds / ((trade.dollars_spent or 0) + (trade.fee or 0)) if (trade.dollars_spent or 0) + (trade.fee or 0) > 0 else 0.0,
                                    is_win=trade.is_win,
                                    principal_after=new_principal,
                                    winning_side=trade.winning_side,
                                )
                            else:
                                # Early sell - market not resolved yet, just update principal
                                session = self.db.SessionLocal()
                                try:
                                    trade_obj = session.query(RealTradeThreshold).filter_by(id=trade_id).first()
                                    if trade_obj:
                                        trade_obj.principal_after = new_principal
                                        session.commit()
                                except Exception as e:
                                    session.rollback()
                                    logger.error(f"Error updating principal for early sell: {e}")
                                finally:
                                    session.close()
                            
                            logger.info(
                                f"Sell order {fill_order_id} filled (found in trades): "
                                f"{filled_shares} shares at ${sell_price:.4f}, "
                                f"received ${sell_dollars_received:.2f}, net_proceeds=${net_proceeds:.2f}, "
                                f"new principal: ${new_principal:.2f}"
                            )
                            
                            self.open_sell_orders.pop(fill_order_id, None)
                            self.sell_orders_not_found.pop(fill_order_id, None)
                            # Remove from all_sell_orders_to_check so we don't check it again below
                            all_sell_orders_to_check.pop(fill_order_id, None)
        except Exception as e:
            logger.debug(f"Could not check fills/trades for sell orders: {e}")
        
        for sell_order_id, trade_id in list(all_sell_orders_to_check.items()):
            try:
                order_status = self.pm.get_order_status(sell_order_id)
                if not order_status:
                    # If sell order not found, check retry count
                    retry_count = self.sell_orders_not_found.get(sell_order_id, 0)
                    
                    if retry_count < self.max_order_not_found_retries:
                        # Increment retry count and try again next time
                        self.sell_orders_not_found[sell_order_id] = retry_count + 1
                        logger.debug(
                            f"Sell order {sell_order_id} not found in API (retry {retry_count + 1}/{self.max_order_not_found_retries}) - "
                            f"will retry on next check"
                        )
                        continue
                    
                    # Max retries reached - check if it's in open orders
                    trade = self.db.get_trade_by_id(trade_id)
                    if trade and trade.sell_order_status == "open":
                        logger.warning(
                            f"Sell order {sell_order_id} not found in API after {self.max_order_not_found_retries} retries - "
                            f"checking open orders list"
                        )
                        # Check if it's in open orders
                        try:
                            open_orders = self.pm.get_open_orders()
                            if open_orders:
                                order_ids = [o.get("orderID") or o.get("order_id") for o in open_orders]
                                if sell_order_id not in order_ids:
                                    # Order is not in open orders - likely filled
                                    logger.info(f"Sell order {sell_order_id} not in open orders - marking as filled")
                                    # Update sell order as filled
                                    if trade.sell_order_size:
                                        from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                                        sell_dollars_received = trade.sell_order_size * trade.sell_order_price
                                        sell_fee = calculate_polymarket_fee(trade.sell_order_price, sell_dollars_received)
                                        self.db.update_sell_order_fill(
                                            trade_id=trade_id,
                                            sell_order_status="filled",
                                            sell_dollars_received=sell_dollars_received,
                                            sell_fee=sell_fee,
                                        )
                                        
                                        # Update principal now that sell order filled
                                        # Calculate actual net payout from sell (works for both early sells and claim proceeds)
                                        net_proceeds = sell_dollars_received - sell_fee - (trade.dollars_spent or 0) - (trade.fee or 0)
                                        new_principal = self.principal + net_proceeds
                                        self.principal = new_principal
                                        
                                        # Update trade with final principal
                                        # If market already resolved, update with outcome info
                                        # If market not resolved yet (early sell), just update principal
                                        if trade.market_resolved_at:
                                            # Market already resolved - update with outcome info
                                            self.db.update_trade_outcome(
                                                trade_id=trade_id,
                                                outcome_price=trade.outcome_price,
                                                payout=sell_dollars_received,
                                                net_payout=net_proceeds,
                                                roi=net_proceeds / ((trade.dollars_spent or 0) + (trade.fee or 0)) if (trade.dollars_spent or 0) + (trade.fee or 0) > 0 else 0.0,
                                                is_win=trade.is_win,
                                                principal_after=new_principal,
                                                winning_side=trade.winning_side,
                                            )
                                        else:
                                            # Early sell - market not resolved yet, just update principal
                                            session = self.db.SessionLocal()
                                            try:
                                                trade_obj = session.query(RealTradeThreshold).filter_by(id=trade_id).first()
                                                if trade_obj:
                                                    trade_obj.principal_after = new_principal
                                                    session.commit()
                                            except Exception as e:
                                                session.rollback()
                                                logger.error(f"Error updating principal for early sell: {e}")
                                            finally:
                                                session.close()
                                        
                                        logger.info(
                                            f"Sell order {sell_order_id} filled (not found in API): "
                                            f"received ${sell_dollars_received:.2f}, net_proceeds=${net_proceeds:.2f}, "
                                            f"new principal: ${new_principal:.2f}"
                                        )
                                        
                                        self.open_sell_orders.pop(sell_order_id, None)
                                        self.sell_orders_not_found.pop(sell_order_id, None)  # Clear retry count
                                else:
                                    # Order is in open orders but get_order_status failed - keep retrying
                                    logger.warning(f"Sell order {sell_order_id} is in open orders but status check failed - will retry")
                                    continue
                        except Exception as e:
                            logger.warning(f"Could not check open orders for sell: {e} - will retry")
                            continue
                    else:
                        # Trade not found or already resolved - remove from tracking
                        self.open_sell_orders.pop(sell_order_id, None)
                        self.sell_orders_not_found.pop(sell_order_id, None)
                    continue
                
                # Sell order found - clear retry count
                self.sell_orders_not_found.pop(sell_order_id, None)
                
                # Parse order status
                status = order_status.get("status", "unknown")
                filled_amount = order_status.get("filledAmount", order_status.get("filled_amount", 0))
                total_amount = order_status.get("totalAmount", order_status.get("total_amount", 0))
                
                trade = self.db.get_trade_by_id(trade_id)
                if not trade:
                    continue
                
                # Check if sell order is filled or cancelled
                is_filled = status in ["filled", "FILLED", "complete", "COMPLETE"] or (
                    filled_amount and total_amount and float(filled_amount) >= float(total_amount)
                )
                is_cancelled = status in ["cancelled", "CANCELLED", "canceled", "CANCELED"]
                
                if is_filled or is_cancelled:
                    if is_filled:
                        # Update sell order with fill information
                        filled_shares = float(filled_amount) if filled_amount else trade.sell_order_size
                        sell_price = trade.sell_order_price or 0.99
                        sell_dollars_received = filled_shares * sell_price
                        
                        # Calculate fee
                        from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                        sell_fee = calculate_polymarket_fee(sell_price, sell_dollars_received)
                        
                        self.db.update_sell_order_fill(
                            trade_id=trade_id,
                            sell_order_status="filled",
                            sell_dollars_received=sell_dollars_received,
                            sell_fee=sell_fee,
                        )
                        
                        # Update principal now that sell order filled
                        # Calculate actual net payout from sell (works for both early sells and claim proceeds)
                        net_proceeds = sell_dollars_received - sell_fee - (trade.dollars_spent or 0) - (trade.fee or 0)
                        new_principal = self.principal + net_proceeds
                        self.principal = new_principal
                        
                        # Update trade with final principal
                        # If market already resolved, update with outcome info
                        # If market not resolved yet (early sell), just update principal
                        if trade.market_resolved_at:
                            # Market already resolved - update with outcome info
                            self.db.update_trade_outcome(
                                trade_id=trade_id,
                                outcome_price=trade.outcome_price,
                                payout=sell_dollars_received,
                                net_payout=net_proceeds,
                                roi=net_proceeds / ((trade.dollars_spent or 0) + (trade.fee or 0)) if (trade.dollars_spent or 0) + (trade.fee or 0) > 0 else 0.0,
                                is_win=trade.is_win,
                                principal_after=new_principal,
                                winning_side=trade.winning_side,
                            )
                        else:
                            # Early sell - market not resolved yet, just update principal
                            # We'll update outcome when market resolves
                            session = self.db.SessionLocal()
                            try:
                                trade_obj = session.query(RealTradeThreshold).filter_by(id=trade_id).first()
                                if trade_obj:
                                    trade_obj.principal_after = new_principal
                                    session.commit()
                            except Exception as e:
                                session.rollback()
                                logger.error(f"Error updating principal for early sell: {e}")
                            finally:
                                session.close()
                        
                        logger.info(
                            f"Sell order {sell_order_id} filled: {filled_shares} shares, "
                            f"received ${sell_dollars_received:.2f} (fee: ${sell_fee:.2f}), "
                            f"net_proceeds=${net_proceeds:.2f}, new principal: ${new_principal:.2f}"
                        )
                    
                    # Remove from open sell orders and clear retry count
                    self.open_sell_orders.pop(sell_order_id, None)
                    self.sell_orders_not_found.pop(sell_order_id, None)
                
                elif status == "open" and filled_amount and float(filled_amount) > 0:
                    # Partial fill - update sell order
                    filled_shares = float(filled_amount)
                    sell_price = trade.sell_order_price or 0.99
                    sell_dollars_received = filled_shares * sell_price
                    
                    from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                    sell_fee = calculate_polymarket_fee(sell_price, sell_dollars_received)
                    
                    self.db.update_sell_order_fill(
                        trade_id=trade_id,
                        sell_order_status="partial",
                        sell_dollars_received=sell_dollars_received,
                        sell_fee=sell_fee,
                    )
            
            except Exception as e:
                logger.error(f"Error checking sell order {sell_order_id}: {e}", exc_info=True)
    
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
            # Only process trades that actually executed
            # Skip if order was never placed, cancelled, or never filled
            if not trade.order_id:
                logger.warning(f"Trade {trade.id} has no order_id - skipping resolution (order never placed)")
                return
            
            if trade.order_status in ["cancelled", "failed"]:
                logger.info(f"Trade {trade.id} has status '{trade.order_status}' - skipping resolution (order did not execute)")
                return
            
            # Check if order was actually filled
            filled_shares = trade.filled_shares or 0.0
            dollars_spent = trade.dollars_spent or 0.0
            
            if filled_shares <= 0 or dollars_spent <= 0:
                logger.warning(
                    f"Trade {trade.id} was not filled (filled_shares={filled_shares}, "
                    f"dollars_spent=${dollars_spent:.2f}) - skipping resolution (order did not execute)"
                )
                # Mark as cancelled/unfilled if not already marked
                if trade.order_status not in ["cancelled", "failed"]:
                    self.db.update_order_status(
                        trade.id,
                        "cancelled",
                        error_message="Order never filled before market resolution"
                    )
                return
            
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
            
            # Determine winning side first
            winning_side = None
            if isinstance(outcome_prices_raw, list) and len(outcome_prices_raw) >= 2:
                if float(outcome_prices_raw[0]) == 1.0:
                    winning_side = "YES"
                elif float(outcome_prices_raw[1]) == 1.0:
                    winning_side = "NO"
            elif isinstance(outcome_prices_raw, dict):
                if outcome_prices_raw.get("Yes") == 1:
                    winning_side = "YES"
                elif outcome_prices_raw.get("No") == 1:
                    winning_side = "NO"
            
            # Calculate is_win: check if we bet on the winning side (YES/NO question)
            # This is independent of ROI - it's simply: did we bet on the side that won?
            is_win = (winning_side is not None and trade.order_side == winning_side)
            
            # Calculate payout and ROI based on actual proceeds from selling
            # ROI is calculated AFTER we execute the sell (0.99 limit, early sell, or market resolution)
            fee = trade.fee or 0.0
            sell_fee = trade.sell_fee or 0.0
            
            if trade.sell_order_id and trade.sell_order_status == "filled" and trade.sell_dollars_received:
                # We already sold (either at 0.99 or early sell) - use actual proceeds
                payout = trade.sell_dollars_received
                net_payout = payout - sell_fee - dollars_spent - fee
                roi = net_payout / (dollars_spent + fee) if (dollars_spent + fee) > 0 else 0.0
            elif not is_win:
                # We lost - shares are worthless, no sell order needed
                payout = 0.0
                net_payout = -dollars_spent - fee
                roi = net_payout / (dollars_spent + fee) if (dollars_spent + fee) > 0 else 0.0
            else:
                # We won but sell order hasn't filled yet (should execute at 0.99 soon)
                # Use outcome_price as placeholder - ROI will be updated when sell fills
                payout = outcome_price * filled_shares
                net_payout = payout - dollars_spent - fee  # Don't include sell_fee yet
                roi = net_payout / (dollars_spent + fee) if (dollars_spent + fee) > 0 else 0.0
                logger.info(
                    f"Trade {trade.id} won but sell order not filled yet - using outcome_price as placeholder. "
                    f"ROI will be updated when sell order fills."
                )
            
            # Log outcome for debugging
            logger.info(
                f"Trade {trade.id} outcome: side={trade.order_side}, winning_side={winning_side}, "
                f"is_win={is_win}, outcome_price={outcome_price:.4f}, "
                f"payout=${payout:.2f}, net_payout=${net_payout:.2f}, roi={roi*100:.2f}%"
            )
            
            # Update principal ONLY when:
            # 1. Sell order already filled (principal_after was set when sell filled)
            # 2. OR we lost (market ended, no sell order needed)
            # Do NOT update principal if we won but sell order hasn't filled yet - wait for sell to fill
            new_principal = self.principal  # Default: keep current principal
            principal_updated = False
            
            if trade.sell_order_id and trade.sell_order_status == "filled" and trade.principal_after is not None:
                # Sell order already filled - principal_after was set when sell filled
                # Preserve that value (don't recalculate)
                new_principal = trade.principal_after
                self.principal = new_principal
                principal_updated = True
                logger.info(
                    f"Preserving principal_after from sell order: ${new_principal:.2f} "
                    f"(market resolution for trade {trade.id})"
                )
            elif not is_win:
                # We lost - market ended, shares are worthless, no sell order needed
                # Update principal now (this is the only time we update principal at market resolution)
                new_principal = self.principal + net_payout
                self.principal = new_principal
                principal_updated = True
                logger.info(
                    f"Market ended - we lost. Updating principal: ${self.principal:.2f} -> ${new_principal:.2f} "
                    f"(net_payout=${net_payout:.2f})"
                )
            else:
                # We won but sell order hasn't filled yet
                # Do NOT update principal - wait for sell order to fill
                # principal_after will be set when sell order fills
                logger.info(
                    f"Market ended - we won but sell order hasn't filled yet. "
                    f"Principal will be updated when sell order fills. "
                    f"Current principal: ${self.principal:.2f}"
                )
            
            # Update trade in database
            # Only set principal_after if we actually updated it (sell filled or we lost)
            principal_after_value = new_principal if principal_updated else None
            self.db.update_trade_outcome(
                trade_id=trade.id,
                outcome_price=outcome_price,
                payout=payout,
                net_payout=net_payout,
                roi=roi,
                is_win=is_win,
                principal_after=principal_after_value,  # Only set if we updated principal
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
    parser = argparse.ArgumentParser(description="Trade threshold strategy")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to JSON config file",
    )
    
    args = parser.parse_args()
    
    trader = ThresholdTrader(args.config)
    
    try:
        asyncio.run(trader.start())
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
