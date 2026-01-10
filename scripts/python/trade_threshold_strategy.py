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
)
from agents.polymarket.polymarket import Polymarket
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
        latest_principal = self.db.get_latest_principal()
        if latest_principal is not None:
            self.principal = latest_principal
            logger.info(f"Loaded principal from database: ${self.principal:.2f}")
        else:
            self.principal = self.config.initial_principal
            logger.info(f"Using initial principal from config: ${self.principal:.2f}")
        
        # Track markets we're monitoring
        self.monitored_markets: Dict[str, Dict] = {}  # market_slug -> market info
        self.markets_with_bets: Set[str] = set()  # market_slugs we've bet on
        
        # Track open orders
        self.open_trades: Dict[str, int] = {}  # order_id -> trade_id
        
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
        logger.info(f"Margin: {self.config.margin:.4f}")
        logger.info(f"Kelly fraction: {self.config.kelly_fraction:.4f}")
        logger.info(f"Kelly scale factor: {self.config.kelly_scale_factor:.4f}")
        logger.info(f"Current principal: ${self.principal:.2f}")
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
        unresolved_trades = self.db.get_unresolved_trades()
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
            
            # Track open orders
            if trade.order_id and trade.order_status in ["open", "partial"]:
                self.open_trades[trade.order_id] = trade.id
    
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
        # Don't place new bets if we have open orders
        if self.open_trades:
            return
        
        # Don't place new bets if principal is too low
        if self.principal < MIN_BET_SIZE:
            logger.warning(f"Principal ${self.principal:.2f} is below minimum bet size ${MIN_BET_SIZE:.2f}")
            return
        
        for market_slug, market_info in list(self.monitored_markets.items()):
            # Skip if already bet on
            if market_slug in self.markets_with_bets:
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
                logger.info(f"Threshold triggered for {market_slug}: {side} side, lowest_ask={lowest_ask:.4f}")
                
                # Place order
                await self._place_order(market_slug, market_info, side, lowest_ask)
    
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
        order_size = int(amount_invested / order_price)  # Round down to whole shares
        
        if order_size < 1:
            logger.warning(f"Order size too small: {order_size} shares (amount_invested=${amount_invested:.2f})")
            return
        
        # Verify market is still active before placing order
        if not is_market_active(market):
            logger.warning(f"Market {market_slug} is no longer active, skipping order")
            return
        
        logger.info(f"Placing order: {side} side, price={order_price:.4f}, size={order_size} shares")
        
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
                logger.error(f"Order placement attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(5.0)  # Wait 5 seconds before retry
                else:
                    logger.error(f"Failed to place order after 3 attempts")
                    # Log error to database
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
                        order_id=None,
                        order_price=order_price,
                        order_size=order_size,
                        order_side=side,
                        principal_before=self.principal,
                        order_status="failed",
                        error_message=str(e),
                    )
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
        
        # Track order and market
        self.open_trades[order_id] = trade_id
        self.markets_with_bets.add(market_slug)
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
        if not self.open_trades:
            return
        
        for order_id, trade_id in list(self.open_trades.items()):
            try:
                order_status = self.pm.get_order_status(order_id)
                if not order_status:
                    continue
                
                # Parse order status
                status = order_status.get("status", "unknown")
                filled_amount = order_status.get("filledAmount", 0)
                total_amount = order_status.get("totalAmount", 0)
                
                trade = self.db.get_trade_by_id(trade_id)
                if not trade:
                    continue
                
                # Check if order is filled or cancelled
                if status in ["filled", "cancelled"]:
                    if status == "filled":
                        # Update trade with fill information
                        filled_shares = float(filled_amount) if filled_amount else trade.order_size
                        fill_price = trade.order_price  # Use order price as fill price (limit order)
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
                    
                    # Remove from open trades
                    self.open_trades.pop(order_id, None)
                
                elif status == "open" and filled_amount > 0:
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
            
            # Calculate payout
            filled_shares = trade.filled_shares or trade.order_size
            payout = outcome_price * filled_shares
            
            # Calculate net payout and ROI
            dollars_spent = trade.dollars_spent or (filled_shares * trade.order_price)
            fee = trade.fee or 0.0
            net_payout = payout - dollars_spent - fee
            roi = net_payout / (dollars_spent + fee) if (dollars_spent + fee) > 0 else 0.0
            is_win = roi > 0
            
            # Determine winning side
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
            
            # Update principal
            new_principal = self.principal + net_payout
            self.principal = new_principal
            
            # Update trade in database
            self.db.update_trade_outcome(
                trade_id=trade.id,
                outcome_price=outcome_price,
                payout=payout,
                net_payout=net_payout,
                roi=roi,
                is_win=is_win,
                principal_after=new_principal,
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
