"""
Orderbook monitoring module for threshold strategy.

Handles threshold checking and early sell (stop-loss) conditions.
"""
import logging
from typing import Dict, Set, List, Callable, Awaitable, Optional

from agents.trading.orderbook_helper import (
    fetch_orderbook,
    check_threshold_triggered,
    get_highest_bid,
)
from agents.polymarket.btc_market_detector import is_market_active
from agents.trading.trade_db import RealTradeThreshold

logger = logging.getLogger(__name__)

# Minimum bet size
MIN_BET_SIZE = 1.0


class OrderbookMonitor:
    """Monitors orderbooks for threshold triggers and early sell conditions."""
    
    def __init__(
        self,
        config,
        monitored_markets: Dict[str, Dict],
        markets_with_bets: Set[str],
        open_trades: Dict[str, int],
        open_sell_orders: Dict[str, int],
        db,
        pm,
        get_principal: Callable[[], float],
        deployment_id: str,
        is_running: Callable[[], bool],
        order_placed_callback: Callable[[str, Dict, str, float], Awaitable[bool]],
        place_early_sell_callback: Callable[[RealTradeThreshold, float], Awaitable[None]],
    ):
        """
        Initialize orderbook monitor.
        
        Args:
            config: TradingConfig instance
            monitored_markets: Dict of monitored markets (market_slug -> market_info)
            markets_with_bets: Set of market slugs we've bet on
            open_trades: Dict of open buy orders (order_id -> trade_id)
            open_sell_orders: Dict of open sell orders (sell_order_id -> trade_id)
            db: TradeDatabase instance
            pm: Polymarket instance
            get_principal: Callable that returns current principal amount (allows real-time updates)
            deployment_id: Deployment ID for database queries
            is_running: Callable that returns current running status (allows real-time updates)
            order_placed_callback: Async function(market_slug, market_info, side, lowest_ask) -> bool
            place_early_sell_callback: Async function(trade, sell_price) -> None
        """
        self.config = config
        self.monitored_markets = monitored_markets
        self.markets_with_bets = markets_with_bets
        self.open_trades = open_trades
        self.open_sell_orders = open_sell_orders
        self.db = db
        self.pm = pm
        self.get_principal = get_principal
        self.deployment_id = deployment_id
        self.is_running = is_running
        self.order_placed_callback = order_placed_callback
        self.place_early_sell_callback = place_early_sell_callback
        self.orderbook_poll_interval = config.orderbook_poll_interval
    
    async def monitoring_loop(self):
        """Poll orderbooks and check for threshold triggers."""
        import asyncio
        while self.is_running():
            try:
                await self.check_orderbooks_for_triggers()
            except Exception as e:
                logger.error(f"Error in orderbook monitoring: {e}", exc_info=True)
            
            await asyncio.sleep(self.orderbook_poll_interval)
    
    async def check_orderbooks_for_triggers(self):
        """Check all monitored markets for threshold triggers."""
        # ALWAYS check for early sell conditions first (threshold sell/stop-loss)
        # This should run independently of whether we have open trades or sell orders
        # because it's checking existing filled trades to see if they need to be sold early
        await self.check_early_sell_conditions(list(self.monitored_markets.keys()))
        
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
        # Note: We only check this after confirming there are no open trades/sell orders,
        # so principal reflects the actual available capital (all previous trades have resolved)
        current_principal = self.get_principal()
        if current_principal < MIN_BET_SIZE:
            logger.warning(f"Principal ${current_principal:.2f} is below minimum bet size ${MIN_BET_SIZE:.2f}")
            return
        
        # Check wallet balance before placing orders
        try:
            wallet_balance = self.pm.get_polymarket_balance()
            if wallet_balance is None:
                logger.warning("Could not check wallet balance - skipping order placement")
                return
            
            amount_invested = self.config.get_amount_invested(current_principal)
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
                continue  # Already bet on this market
            
            # Also check database to prevent duplicate orders across ALL deployments for THIS market
            # This prevents duplicate orders when:
            # - Script is redeployed and restarted
            # - Multiple deployments are running simultaneously
            # Only checks the CURRENT market - other markets' positions don't matter
            if self.db.has_bet_on_market(market_slug):
                logger.info(
                    f"Market {market_slug} already has a bet (active or resolved) "
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
                
                # Final check: verify no bet exists in database (double-check before placing)
                # This prevents race conditions where order fills immediately and database hasn't updated yet
                if self.db.has_bet_on_market(market_slug):
                    logger.warning(
                        f"⚠️ Threshold triggered for {market_slug}, but database shows active bet exists. "
                        f"Skipping order to prevent duplicate."
                    )
                    self.markets_with_bets.add(market_slug)  # Add to memory set
                    continue
                
                # Mark market as bet on IMMEDIATELY to prevent buying both YES and NO
                # This prevents race condition where both sides trigger in same loop iteration
                # NOTE: We'll remove it if order placement fails (e.g., due to time restriction)
                self.markets_with_bets.add(market_slug)
                
                # Place order - returns True if order was placed, False otherwise
                order_placed = await self.order_placed_callback(market_slug, market_info, side, lowest_ask)
                
                # If order was not placed (e.g., due to time restriction), remove from markets_with_bets
                # so it can be checked again in future iterations
                if not order_placed:
                    self.markets_with_bets.discard(market_slug)
                    logger.info(
                        f"🔄 Order not placed for {market_slug} - removed from markets_with_bets. "
                        f"Will check again on next iteration."
                    )
    
    async def check_early_sell_conditions(self, monitored_market_slugs: List[str]):
        """Check if filled buy orders should trigger early sell (stop-loss) for currently monitored markets."""
        # Skip if threshold sell is disabled (set to 0)
        if self.config.threshold_sell <= 0.0:
            return
        
        if not monitored_market_slugs:
            return
        
        session = self.db.SessionLocal()
        try:
            # Check trades without sell orders first - only for monitored markets
            trades_without_sell = session.query(RealTradeThreshold).filter(
                RealTradeThreshold.deployment_id == self.deployment_id,
                RealTradeThreshold.order_status == "filled",
                RealTradeThreshold.filled_shares.isnot(None),
                RealTradeThreshold.filled_shares > 0,
                RealTradeThreshold.sell_order_id.is_(None),  # No sell order yet
                RealTradeThreshold.market_resolved_at.is_(None),  # Market not resolved yet
                RealTradeThreshold.market_slug.in_(monitored_market_slugs),  # Only current markets
            ).order_by(RealTradeThreshold.order_placed_at.desc()).all()
            
            logger.info(
                f"🔍 Checking {len(trades_without_sell)} trades without sell orders for threshold sell "
                f"(monitored markets: {len(monitored_market_slugs)})"
            )
            
            for trade in trades_without_sell:
                try:
                    logger.info(
                        f"🔍 Checking threshold sell for trade {trade.id} (market: {trade.market_slug}, "
                        f"token_id: {trade.token_id[:20] if trade.token_id else 'N/A'}...)"
                    )
                    
                    # Fetch orderbook for the token we bought
                    orderbook = fetch_orderbook(trade.token_id)
                    if not orderbook:
                        logger.info(
                            f"⚠️ Could not fetch orderbook for trade {trade.id} (token_id: {trade.token_id[:20] if trade.token_id else 'N/A'}...)"
                        )
                        continue
                    
                    # Get highest bid
                    highest_bid = get_highest_bid(orderbook)
                    if highest_bid is None:
                        logger.info(
                            f"⚠️ No highest_bid found in orderbook for trade {trade.id} "
                            f"(market: {trade.market_slug})"
                        )
                        continue
                    
                    logger.info(
                        f"📊 Trade {trade.id}: highest_bid={highest_bid:.4f}, "
                        f"threshold_sell={self.config.threshold_sell:.4f}, "
                        f"condition: {highest_bid:.4f} < {self.config.threshold_sell:.4f} = {highest_bid < self.config.threshold_sell}"
                    )
                    
                    # Check if highest_bid < threshold_sell
                    if highest_bid < self.config.threshold_sell:
                        # Place early sell order
                        sell_price = self.config.threshold_sell - self.config.margin_sell
                        if sell_price < 0.01:
                            sell_price = 0.01  # Minimum price
                        
                        logger.info(
                            f"Early sell triggered for trade {trade.id} (market: {trade.market_slug}, no sell order yet): "
                            f"highest_bid={highest_bid:.4f} < threshold_sell={self.config.threshold_sell:.4f}, "
                            f"placing sell order at {sell_price:.4f}"
                        )
                        
                        await self.place_early_sell_callback(trade, sell_price)
                except Exception as e:
                    logger.error(f"Error checking early sell condition for trade {trade.id}: {e}", exc_info=True)
            
            # Also check trades with open $0.99 sell orders - if price drops below threshold,
            # cancel the $0.99 order and place an early sell order
            trades_with_099_sell = session.query(RealTradeThreshold).filter(
                RealTradeThreshold.deployment_id == self.deployment_id,
                RealTradeThreshold.order_status == "filled",
                RealTradeThreshold.filled_shares.isnot(None),
                RealTradeThreshold.filled_shares > 0,
                RealTradeThreshold.sell_order_id.isnot(None),
                RealTradeThreshold.sell_order_status == "open",
                RealTradeThreshold.sell_order_price == 0.99,
                RealTradeThreshold.market_resolved_at.is_(None),  # Market not resolved yet
                RealTradeThreshold.market_slug.in_(monitored_market_slugs),  # Only current markets
            ).order_by(RealTradeThreshold.order_placed_at.desc()).all()
            
            logger.info(
                f"🔍 Checking {len(trades_with_099_sell)} trades with open $0.99 sell orders for threshold sell"
            )
            
            # Also check trades that were incorrectly marked as "filled" but might not actually be filled
            # This handles cases where API delays caused false positives
            # Only check if order was marked filled very recently (within last 2 minutes) and market hasn't resolved
            from datetime import datetime, timezone, timedelta
            recent_time = datetime.now(timezone.utc) - timedelta(minutes=2)
            trades_incorrectly_filled = session.query(RealTradeThreshold).filter(
                RealTradeThreshold.deployment_id == self.deployment_id,
                RealTradeThreshold.order_status == "filled",
                RealTradeThreshold.filled_shares.isnot(None),
                RealTradeThreshold.filled_shares > 0,
                RealTradeThreshold.sell_order_id.isnot(None),
                RealTradeThreshold.sell_order_status == "filled",  # Marked as filled
                RealTradeThreshold.market_resolved_at.is_(None),  # Market not resolved yet
                RealTradeThreshold.market_slug.in_(monitored_market_slugs),  # Only current markets
                RealTradeThreshold.sell_order_placed_at.isnot(None),
                RealTradeThreshold.sell_order_placed_at >= recent_time,  # Recently marked as filled
            ).order_by(RealTradeThreshold.order_placed_at.desc()).all()
            
            # Verify these trades are actually still open by checking the API
            for trade in trades_incorrectly_filled:
                try:
                    # Check if order is actually still open in the API
                    order_status = self.pm.get_order_status(trade.sell_order_id)
                    if order_status:
                        status = order_status.get("status", "unknown")
                        if status in ["live", "LIVE", "open", "OPEN"]:
                            # Order is still open - it was incorrectly marked as filled
                            logger.warning(
                                f"⚠️ Trade {trade.id} sell order {trade.sell_order_id} was incorrectly marked as 'filled' "
                                f"but is still OPEN in API (status={status}). Resetting to 'open' and checking early sell."
                            )
                            # Reset sell_order_status to "open" so it can be checked for early sell
                            trade.sell_order_status = "open"
                            session.commit()
                            # Add to trades_with_099_sell list so it gets checked
                            trades_with_099_sell.append(trade)
                except Exception as e:
                    logger.debug(f"Could not verify order status for trade {trade.id}: {e}")
            
            for trade in trades_with_099_sell:
                try:
                    logger.info(
                        f"🔍 Checking threshold sell for trade {trade.id} with $0.99 sell order "
                        f"(market: {trade.market_slug}, token_id: {trade.token_id[:20] if trade.token_id else 'N/A'}...)"
                    )
                    
                    # Fetch orderbook for the token we bought
                    orderbook = fetch_orderbook(trade.token_id)
                    if not orderbook:
                        logger.info(
                            f"⚠️ Could not fetch orderbook for trade {trade.id} with $0.99 sell order "
                            f"(token_id: {trade.token_id[:20] if trade.token_id else 'N/A'}...)"
                        )
                        continue
                    
                    # Get highest bid
                    highest_bid = get_highest_bid(orderbook)
                    if highest_bid is None:
                        logger.info(
                            f"⚠️ No highest_bid found in orderbook for trade {trade.id} with $0.99 sell order "
                            f"(market: {trade.market_slug})"
                        )
                        continue
                    
                    logger.info(
                        f"📊 Trade {trade.id} ($0.99 sell): highest_bid={highest_bid:.4f}, "
                        f"threshold_sell={self.config.threshold_sell:.4f}, "
                        f"condition: {highest_bid:.4f} < {self.config.threshold_sell:.4f} = {highest_bid < self.config.threshold_sell}"
                    )
                    
                    # Check if highest_bid < threshold_sell
                    if highest_bid < self.config.threshold_sell:
                        # Place early sell order (this will cancel the $0.99 order first)
                        sell_price = self.config.threshold_sell - self.config.margin_sell
                        if sell_price < 0.01:
                            sell_price = 0.01  # Minimum price
                        
                        logger.info(
                            f"Early sell triggered for trade {trade.id} (market: {trade.market_slug}, has $0.99 sell order): "
                            f"highest_bid={highest_bid:.4f} < threshold_sell={self.config.threshold_sell:.4f}, "
                            f"canceling $0.99 order and placing early sell at {sell_price:.4f}"
                        )
                        
                        await self.place_early_sell_callback(trade, sell_price)
                except Exception as e:
                    logger.error(f"Error checking early sell condition for trade {trade.id}: {e}", exc_info=True)
        finally:
            session.close()
