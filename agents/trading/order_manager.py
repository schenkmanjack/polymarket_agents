"""
Order management module for threshold strategy.

Handles order placement, status checking, and cancellation.
"""
import logging
import asyncio
import math
from typing import Dict, Set, Callable, Awaitable, Optional
from datetime import datetime, timezone

from agents.trading.trade_db import RealTradeThreshold
from agents.trading.utils.order_calculations import (
    calculate_order_size_with_fees,
    calculate_kelly_amount,
)
from agents.trading.utils.order_status_helpers import (
    parse_order_status,
    is_order_filled,
    is_order_cancelled,
    is_order_partial_fill,
)
from agents.trading.utils.market_time_helpers import get_minutes_until_resolution
from agents.polymarket.btc_market_detector import is_market_active, get_market_by_slug
from agents.backtesting.backtesting_utils import calculate_polymarket_fee

logger = logging.getLogger(__name__)

# Constants
SELL = "SELL"
MAX_ORDER_NOT_FOUND_RETRIES = 3
ORDER_STATUS_CHECK_INTERVAL = 10.0  # Default 10 seconds when no open orders


class OrderManager:
    """Manages order placement, status checking, and cancellation."""
    
    def __init__(
        self,
        config,
        open_trades: Dict[str, int],
        open_sell_orders: Dict[str, int],
        db,
        pm,
        get_principal: Callable[[], float],
        deployment_id: str,
        is_running: Callable[[], bool],
        place_sell_order_callback: Callable[[RealTradeThreshold], Awaitable[None]],
        websocket_order_status_service=None,
    ):
        """
        Initialize order manager.
        
        Args:
            config: TradingConfig instance
            open_trades: Dict of open buy orders (order_id -> trade_id)
            open_sell_orders: Dict of open sell orders (sell_order_id -> trade_id)
            db: TradeDatabase instance
            pm: Polymarket instance
            get_principal: Callable that returns current principal amount
            deployment_id: Deployment ID for database queries
            is_running: Callable that returns current running status
            place_sell_order_callback: Async function(trade) -> None (called when buy order fills)
            websocket_order_status_service: Optional WebSocketOrderStatusService instance for real-time order updates
        """
        self.config = config
        self.open_trades = open_trades
        self.open_sell_orders = open_sell_orders
        self.db = db
        self.pm = pm
        self.get_principal = get_principal
        self.deployment_id = deployment_id
        self.is_running = is_running
        self.place_sell_order_callback = place_sell_order_callback
        self.websocket_order_status_service = websocket_order_status_service
        
        # Tracking dictionaries
        self.orders_not_found = {}  # order_id -> retry_count
        self.sell_orders_not_found = {}  # sell_order_id -> retry_count
        self.orders_checked_open = {}  # order_id -> check_count
        self.max_order_not_found_retries = MAX_ORDER_NOT_FOUND_RETRIES
        self.order_status_check_interval = config.order_status_check_interval
        
        # Re-pricing tracking for threshold sell orders
        self._threshold_sell_reprice_attempts = {}  # trade_id -> attempt_count
        
        # Track if we're using WebSocket (for fallback logic)
        self._using_websocket = False
        self._websocket_fallback_logged = False
    
    async def _handle_websocket_order_update(self, order_data: Dict):
        """
        Handle order status update from WebSocket.
        
        Called when WebSocket receives an order update (placement, cancellation, fill).
        
        Args:
            order_data: Order update data from WebSocket
        """
        try:
            order_id = order_data.get("id") or order_data.get("order_id") or order_data.get("orderID")
            order_status = order_data.get("status", "unknown")
            
            if not order_id:
                logger.warning(f"⚠️ WebSocket order update missing order_id: {order_data}")
                return
            
            logger.info(
                f"📋 WebSocket order update | Order: {order_id[:20]}... | Status: {order_status} | "
                f"Size: {order_data.get('size', 'N/A')} | Price: {order_data.get('price', 'N/A')}"
            )
            
            # Check if this is a buy order we're tracking
            if order_id in self.open_trades:
                trade_id = self.open_trades[order_id]
                trade = self.db.get_trade_by_id(trade_id)
                
                if trade:
                    # Update order status in database
                    if order_status in ["filled", "FILLED", "complete", "COMPLETE"]:
                        # Order filled - update database
                        filled_shares = order_data.get("size") or order_data.get("filled_amount") or trade.order_size
                        if filled_shares:
                            filled_shares = float(filled_shares)
                        else:
                            filled_shares = trade.order_size
                        
                        fill_price = order_data.get("price") or trade.order_price
                        if fill_price:
                            fill_price = float(fill_price)
                        else:
                            fill_price = trade.order_price
                        
                        logger.info(
                            f"✅ WebSocket: Buy order {order_id[:20]}... FILLED | "
                            f"Trade ID: {trade_id} | Filled shares: {filled_shares} @ ${fill_price:.4f}"
                        )
                        
                        # Update database using update_trade_fill (same as HTTP polling)
                        from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                        dollars_spent = filled_shares * fill_price
                        fee = calculate_polymarket_fee(fill_price, dollars_spent)
                        
                        self.db.update_trade_fill(
                            trade_id=trade_id,
                            filled_shares=filled_shares,
                            fill_price=fill_price,
                            dollars_spent=dollars_spent,
                            fee=fee,
                            order_status="filled",
                        )
                        
                        # Remove from open_trades
                        self.open_trades.pop(order_id, None)
                        self.orders_not_found.pop(order_id, None)
                        self.orders_checked_open.pop(order_id, None)
                        
                        # Trigger sell order placement
                        if self.place_sell_order_callback:
                            try:
                                await self.place_sell_order_callback(trade)
                            except Exception as e:
                                logger.error(f"Error placing sell order after WebSocket fill: {e}", exc_info=True)
                    
                    elif order_status in ["cancelled", "CANCELLED", "canceled"]:
                        logger.info(f"❌ WebSocket: Buy order {order_id[:20]}... CANCELLED")
                        self.db.update_order_status(
                            trade_id=trade_id,
                            order_status="cancelled",
                            order_id=order_id,
                        )
                        self.open_trades.pop(order_id, None)
            
            # Check if this is a sell order we're tracking
            elif order_id in self.open_sell_orders:
                trade_id = self.open_sell_orders[order_id]
                trade = self.db.get_trade_by_id(trade_id)
                
                if trade:
                    if order_status in ["filled", "FILLED", "complete", "COMPLETE"]:
                        filled_shares = order_data.get("size") or order_data.get("filled_amount") or trade.sell_order_size
                        if filled_shares:
                            filled_shares = float(filled_shares)
                        else:
                            filled_shares = trade.sell_order_size
                        
                        fill_price = order_data.get("price") or trade.sell_order_price
                        if fill_price:
                            fill_price = float(fill_price)
                        else:
                            fill_price = trade.sell_order_price
                        
                        logger.info(
                            f"✅ WebSocket: Sell order {order_id[:20]}... FILLED | "
                            f"Trade ID: {trade_id} | Filled shares: {filled_shares} @ ${fill_price:.4f}"
                        )
                        
                        # Update database using update_sell_order_fill
                        from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                        dollars_received = filled_shares * fill_price
                        fee = calculate_polymarket_fee(fill_price, dollars_received)
                        
                        self.db.update_sell_order_fill(
                            trade_id=trade_id,
                            sell_order_status="filled",
                            sell_shares_filled=filled_shares,
                            sell_dollars_received=dollars_received,
                            sell_fee=fee,
                        )
                        self.open_sell_orders.pop(order_id, None)
                    
                    elif order_status in ["cancelled", "CANCELLED", "canceled"]:
                        logger.info(f"❌ WebSocket: Sell order {order_id[:20]}... CANCELLED")
                        # Update sell order status via update_sell_order
                        self.db.update_sell_order(
                            trade_id=trade_id,
                            sell_order_id=order_id,
                            sell_order_price=trade.sell_order_price or 0.0,
                            sell_order_size=trade.sell_order_size or 0.0,
                            sell_order_status="cancelled",
                        )
                        self.open_sell_orders.pop(order_id, None)
        
        except Exception as e:
            logger.error(f"Error handling WebSocket order update: {e}", exc_info=True)
    
    async def _handle_websocket_trade_update(self, trade_data: Dict):
        """
        Handle trade/fill update from WebSocket.
        
        Called when WebSocket receives a trade event (order was matched/filled).
        
        Args:
            trade_data: Trade update data from WebSocket
        """
        try:
            order_id = trade_data.get("order_id") or trade_data.get("orderID")
            trade_id_ws = trade_data.get("id") or trade_data.get("trade_id")
            size = trade_data.get("size", 0)
            price = trade_data.get("price", 0)
            
            logger.info(
                f"💰 WebSocket TRADE/FILL | Order: {order_id[:20] if order_id else 'N/A'}... | "
                f"Size: {size} | Price: {price:.4f} | Trade ID: {trade_id_ws[:20] if trade_id_ws else 'N/A'}..."
            )
            
            # Check if this is a buy order fill
            if order_id and order_id in self.open_trades:
                trade_id = self.open_trades[order_id]
                trade = self.db.get_trade_by_id(trade_id)
                
                if trade:
                    filled_shares = float(size) if size else trade.order_size
                    
                    logger.info(
                        f"✅ WebSocket TRADE: Buy order {order_id[:20]}... FILLED | "
                        f"Trade ID: {trade_id} | Filled: {filled_shares} @ ${price:.4f}"
                    )
                    
                    # Update database using update_trade_fill (same as HTTP polling)
                    from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                    dollars_spent = filled_shares * price
                    fee = calculate_polymarket_fee(price, dollars_spent)
                    
                    self.db.update_trade_fill(
                        trade_id=trade_id,
                        filled_shares=filled_shares,
                        fill_price=price,
                        dollars_spent=dollars_spent,
                        fee=fee,
                        order_status="filled",
                    )
                    
                    # Remove from open_trades
                    self.open_trades.pop(order_id, None)
                    self.orders_not_found.pop(order_id, None)
                    self.orders_checked_open.pop(order_id, None)
                    
                    # Trigger sell order placement immediately
                    if self.place_sell_order_callback:
                        try:
                            await self.place_sell_order_callback(trade)
                        except Exception as e:
                            logger.error(f"Error placing sell order after WebSocket trade: {e}", exc_info=True)
            
            # Check if this is a sell order fill
            elif order_id and order_id in self.open_sell_orders:
                trade_id = self.open_sell_orders[order_id]
                trade = self.db.get_trade_by_id(trade_id)
                
                if trade:
                    filled_shares = float(size) if size else trade.sell_order_size
                    
                    logger.info(
                        f"✅ WebSocket TRADE: Sell order {order_id[:20]}... FILLED | "
                        f"Trade ID: {trade_id} | Filled: {filled_shares} @ ${price:.4f}"
                    )
                    
                    # Update database using update_sell_order_fill
                    from agents.backtesting.backtesting_utils import calculate_polymarket_fee
                    dollars_received = filled_shares * price
                    fee = calculate_polymarket_fee(price, dollars_received)
                    
                    self.db.update_sell_order_fill(
                        trade_id=trade_id,
                        sell_order_status="filled",
                        sell_shares_filled=filled_shares,
                        sell_dollars_received=dollars_received,
                        sell_fee=fee,
                    )
                    self.open_sell_orders.pop(order_id, None)
        
        except Exception as e:
            logger.error(f"Error handling WebSocket trade update: {e}", exc_info=True)
    
    async def place_buy_order(
        self,
        market_slug: str,
        market_info: Dict,
        side: str,
        trigger_price: float,
    ) -> bool:
        """Place a limit buy order when threshold is triggered.
        
        Returns:
            True if order was successfully placed, False otherwise
        """
        market = market_info["market"]
        market_id = market.get("id", "unknown")
        token_id = market_info["yes_token_id"] if side == "YES" else market_info["no_token_id"]
        
        # Check if we're within the allowed time window before resolution
        if self.config.max_minutes_before_resolution is not None:
            minutes_remaining = get_minutes_until_resolution(market)
            if minutes_remaining is None:
                logger.warning(
                    f"Could not determine time remaining for market {market_slug}. "
                    f"Skipping order to be safe."
                )
                return False
            
            if minutes_remaining > self.config.max_minutes_before_resolution:
                logger.info(
                    f"⏰ Threshold triggered for {market_slug} ({side} side, lowest_ask={trigger_price:.4f}), "
                    f"but trade NOT PLACED: {minutes_remaining:.2f} minutes remaining exceeds "
                    f"max_minutes_before_resolution ({self.config.max_minutes_before_resolution:.1f} minutes). "
                    f"Skipping order. Will check again when time remaining decreases."
                )
                return False
            
            logger.info(
                f"⏰ Time check passed: {minutes_remaining:.2f} minutes remaining <= "
                f"{self.config.max_minutes_before_resolution:.1f} minutes limit - proceeding with order"
            )
        
        # Calculate order parameters
        # Use fixed pricing: threshold + margin (not trigger_price + margin)
        order_price = self.config.threshold + self.config.margin
        # Cap at upper_threshold or 0.99, whichever is lower
        order_price = min(order_price, self.config.upper_threshold, 0.99)
        
        # Log trigger_price for informational purposes
        logger.info(
            f"Order pricing: threshold={self.config.threshold:.4f}, margin={self.config.margin:.4f}, "
            f"calculated_order_price={order_price:.4f}, trigger_price={trigger_price:.4f} (informational)"
        )
        
        current_principal = self.get_principal()
        amount_invested = self.config.get_amount_invested(current_principal)
        
        # Log if bet limit is capping the Kelly-calculated amount
        kelly_amount = calculate_kelly_amount(
            current_principal,
            self.config.kelly_fraction,
            self.config.kelly_scale_factor,
        )
        if amount_invested < kelly_amount:
            logger.info(
                f"Bet size capped by dollar_bet_limit: Kelly suggests ${kelly_amount:.2f}, "
                f"but limited to ${amount_invested:.2f} (dollar_bet_limit=${self.config.dollar_bet_limit:.2f})"
            )
        
        # Calculate order size accounting for fees
        order_size, order_value, estimated_shares_received, estimated_fee = calculate_order_size_with_fees(
            amount_invested=amount_invested,
            order_price=order_price,
            dollar_bet_limit=self.config.dollar_bet_limit,
            min_order_value=1.0,
        )
        
        if order_size is None:
            logger.warning(f"Order size calculation failed (amount_invested=${amount_invested:.2f})")
            return False
        
        # Log fee adjustment
        logger.info(
            f"Fee adjustment for shares: ordering {order_size} shares to get ~{estimated_shares_received:.4f} shares after fees. "
            f"Order value: ${order_value:.2f}, estimated fee: ${estimated_fee:.4f}"
        )
        
        # Update amount_invested to reflect the actual order value
        amount_invested = order_value
        
        # Verify market is still active before placing order
        if not is_market_active(market):
            logger.warning(f"Market {market_slug} is no longer active, skipping order")
            return False
        
        logger.info(
            f"Placing LIMIT order: {side} side, limit_price={order_price:.4f}, size={order_size} shares, "
            f"order_value=${order_value:.2f} (amount_invested=${amount_invested:.2f}, principal=${current_principal:.2f})"
        )
        logger.info(
            f"  Order details: trigger_price={trigger_price:.4f}, margin={self.config.margin:.4f}, "
            f"calculated_limit_price={order_price:.4f}"
        )
        
        # Place order with retry logic
        order_response = None
        for attempt in range(3):
            try:
                logger.info(
                    f"🟢 BUY ORDER ATTEMPT {attempt + 1}/3: "
                    f"Placing BUY order at ${order_price:.4f} for {order_size} shares "
                    f"(market={market_slug}, side={side}, token_id={token_id[:20]}...)"
                )
                order_response = self.pm.execute_order(
                    price=order_price,
                    size=order_size,
                    side="BUY",
                    token_id=token_id,
                )
                
                if order_response:
                    logger.info(
                        f"✅ BUY ORDER SUCCESS: Received response from execute_order: {order_response}"
                    )
                    break
                else:
                    logger.warning(f"⚠️ BUY ORDER ATTEMPT {attempt + 1}: execute_order returned None/empty")
            except Exception as e:
                error_msg = str(e)
                error_message = getattr(e, 'error_message', {})
                error_str = str(error_message) if error_message else error_msg
                
                logger.error(f"Order placement attempt {attempt + 1} failed: {e}")
                
                # Check for minimum order size error
                if "min size" in error_msg.lower() or "invalid amount" in error_msg.lower() or "min size" in error_str.lower():
                    logger.error(
                        f"Order value ${order_value:.2f} below Polymarket minimum. "
                        f"This should have been caught earlier. Order details: "
                        f"size={order_size}, price={order_price:.4f}, value=${order_value:.2f}"
                    )
                    return False
                
                # Check for balance/allowance errors
                if "not enough balance" in error_msg.lower() or "allowance" in error_msg.lower():
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
                    
                    return False
                
                if attempt < 2:
                    await asyncio.sleep(5.0)  # Wait 5 seconds before retry
                else:
                    logger.error(f"Failed to place order after 3 attempts - not creating trade record")
                    return False
        
        if not order_response:
            return False
        
        # Extract order ID
        logger.info(f"🔍 Extracting order ID from response: {order_response}")
        order_id = self.pm.extract_order_id(order_response)
        if not order_id:
            logger.error(f"❌ Could not extract order ID from response: {order_response}")
            return False
        logger.info(f"✅ Extracted order ID: {order_id}")
        
        # Log buy order placement
        logger.info(
            f"✅✅✅ BUY ORDER PLACED ✅✅✅\n"
            f"  Order ID: {order_id}\n"
            f"  Market: {market_slug}\n"
            f"  Side: {side}\n"
            f"  Price: ${order_price:.4f}\n"
            f"  Size: {order_size} shares\n"
            f"  Order Value: ${order_value:.2f}\n"
            f"  Principal: ${current_principal:.2f}"
        )
        
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
            principal_before=current_principal,
            order_status="open",
        )
        
        # Track order
        self.open_trades[order_id] = trade_id
        logger.info(
            f"✅✅✅ BUY ORDER PLACED SUCCESSFULLY ✅✅✅\n"
            f"  Order ID: {order_id}\n"
            f"  Trade ID: {trade_id}\n"
            f"  Market: {market_slug}\n"
            f"  Side: {side}\n"
            f"  Price: ${order_price:.4f}\n"
            f"  Size: {order_size} shares\n"
            f"  Order Value: ${order_value:.2f}\n"
            f"  Principal: ${current_principal:.2f}"
        )
        logger.info(
            f"📋 Next steps: System will monitor buy order status. "
            f"When buy order fills, a limit sell order at $0.99 will be placed automatically."
        )
        
        return True
    
    async def status_check_loop(self):
        """Check order status via WebSocket (if available) or HTTP polling (fallback)."""
        # If WebSocket is available and connected, rely on it for real-time updates
        # Still do periodic HTTP checks as backup
        while self.is_running():
            try:
                # Check if WebSocket is working
                if self.websocket_order_status_service and self.websocket_order_status_service.is_connected():
                    self._using_websocket = True
                    if self._websocket_fallback_logged:
                        logger.info("✓ WebSocket order status service is working again - using real-time updates")
                        self._websocket_fallback_logged = False
                    
                    # WebSocket is handling updates in real-time, but still do periodic HTTP checks as backup
                    # Use longer interval since WebSocket provides instant updates
                    await asyncio.sleep(self.order_status_check_interval)
                    # Still do HTTP check as backup
                    await self.check_order_statuses()
                else:
                    # Fallback to HTTP polling
                    if not self._websocket_fallback_logged:
                        logger.warning("⚠️ Falling back to HTTP polling for order status (WebSocket unavailable)")
                        self._websocket_fallback_logged = True
                    self._using_websocket = False
                    
                    await self.check_order_statuses()
                    
                    # Use 2 seconds if there are open orders, otherwise use default interval
                    has_open_orders = self.open_trades or self.open_sell_orders
                    if not has_open_orders:
                        # Check database only if memory is empty (e.g., after script restart)
                        open_trades_db = self.db.get_open_trades(deployment_id=self.deployment_id)
                        open_sell_orders_db = self.db.get_open_sell_orders(deployment_id=self.deployment_id)
                        has_open_orders = bool(open_trades_db or open_sell_orders_db)
                    
                    if has_open_orders:
                        await asyncio.sleep(2.0)  # Check every 2 seconds when orders are open
                    else:
                        await asyncio.sleep(self.order_status_check_interval)  # Default interval when no open orders
            except Exception as e:
                logger.error(f"Error checking order status: {e}", exc_info=True)
                await asyncio.sleep(1.0)
    
    async def check_order_statuses(self):
        """Check status of all open orders."""
        # Get all open trades from database for current deployment
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
        
        logger.debug(
            f"🔍 Checking status of {len(all_trades_to_check)} open buy orders: {list(all_trades_to_check.keys())[:5]}..."
        )
        
        # First, check fills/trades to see if any orders have been filled
        await self._check_buy_order_fills(all_trades_to_check)
        
        # Also check open orders - if order is NOT in open orders, it's likely filled
        await self._check_buy_orders_via_open_orders(all_trades_to_check)
        
        # Now check individual order statuses for orders that are still open
        await self._check_individual_buy_order_statuses(all_trades_to_check)
        
        # Also check sell orders
        await self.check_sell_order_statuses()
        
        # Check for trades with filled buy orders but no sell orders (retry sell order placement)
        await self.retry_missing_sell_orders()
    
    async def _check_buy_order_fills(self, all_trades_to_check: Dict[str, int]):
        """Check fills/trades to see if any buy orders have been filled."""
        try:
            # Get our wallet address for filtering
            wallet_address = None
            if hasattr(self.pm, 'proxy_wallet_address') and self.pm.proxy_wallet_address:
                wallet_address = self.pm.proxy_wallet_address
            elif hasattr(self.pm, 'get_address_for_private_key'):
                wallet_address = self.pm.get_address_for_private_key()
            
            # Check both maker and taker addresses
            # Note: get_trades() only supports maker_address filter, not taker_address
            # So we get maker fills and also get all trades to check for taker fills
            fills = []
            try:
                maker_fills = self.pm.get_trades(maker_address=wallet_address) or []
                logger.debug(f"Found {len(maker_fills)} maker fills for wallet {wallet_address[:10] if wallet_address else 'N/A'}...")
            except Exception as e:
                logger.error(f"Error getting maker fills: {e}", exc_info=True)
                maker_fills = []
            
            # Get all trades (no filter) to find trades where we're the taker
            try:
                all_trades = self.pm.get_trades() or []
                logger.debug(f"Retrieved {len(all_trades)} total trades to check for taker fills")
            except Exception as e:
                logger.error(f"Error getting all trades: {e}", exc_info=True)
                all_trades = []
            
            # Filter all_trades to find ones where we're the taker
            taker_fills = []
            if wallet_address:
                wallet_address_lower = wallet_address.lower()
                for trade in all_trades:
                    # Check if we're the taker in this trade
                    taker = trade.get("taker") or trade.get("taker_address")
                    if taker and str(taker).lower() == wallet_address_lower:
                        taker_fills.append(trade)
                logger.debug(f"Found {len(taker_fills)} taker fills for wallet {wallet_address[:10]}...")
            
            # Deduplicate fills (same fill might appear in both lists)
            seen_fill_ids = set()
            for fill in maker_fills:
                fill_id = fill.get("id") or fill.get("trade_id") or str(fill)
                if fill_id not in seen_fill_ids:
                    fills.append(fill)
                    seen_fill_ids.add(fill_id)
            
            for fill in taker_fills:
                fill_id = fill.get("id") or fill.get("trade_id") or str(fill)
                if fill_id not in seen_fill_ids:
                    fills.append(fill)
                    seen_fill_ids.add(fill_id)
            
            if fills:
                logger.info(
                    f"📊 Checking fills for {len(all_trades_to_check)} open buy orders. "
                    f"Found {len(fills)} total fills (maker: {len(maker_fills)}, taker: {len(taker_fills)}, "
                    f"after dedup: {len(fills)}). "
                    f"Open order IDs: {list(all_trades_to_check.keys())[:3]}..."
                )
                for fill in fills:
                    # Try multiple field names for order ID
                    fill_order_id = (
                        fill.get("taker_order_id") or 
                        fill.get("orderID") or 
                        fill.get("order_id") or 
                        fill.get("id")
                    )
                    
                    if fill_order_id and fill_order_id in all_trades_to_check:
                        trade_id = all_trades_to_check[fill_order_id]
                        trade = self.db.get_trade_by_id(trade_id)
                        if trade and not trade.filled_shares:
                            # Verify this fill belongs to this trade
                            if trade.order_id != fill_order_id:
                                logger.warning(
                                    f"⚠️ Fill order_id {fill_order_id} doesn't match trade.order_id {trade.order_id} "
                                    f"for trade {trade_id}. Skipping."
                                )
                                continue
                            
                            # Order was filled - extract fill details
                            filled_shares = fill.get("size")
                            fill_price_from_record = fill.get("price")
                            
                            # Convert to float if they're strings
                            if filled_shares:
                                filled_shares = float(filled_shares)
                            else:
                                filled_shares = trade.order_size
                            
                            if fill_price_from_record:
                                fill_price = float(fill_price_from_record)
                            else:
                                fill_price = trade.order_price
                            
                            # Log if fill price differs significantly from limit price
                            price_diff = abs(fill_price - trade.order_price)
                            if price_diff > 0.01:
                                logger.warning(
                                    f"⚠️ Fill price ({fill_price:.4f}) differs significantly from limit price ({trade.order_price:.4f}) "
                                    f"for order {fill_order_id}. Difference: {price_diff:.4f}."
                                )
                            
                            dollars_spent = filled_shares * fill_price
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
                            self.orders_checked_open.pop(fill_order_id, None)
                            logger.info(
                                f"✅✅✅ BUY ORDER FILLED ✅✅✅\n"
                                f"  Order ID: {fill_order_id}\n"
                                f"  Trade ID: {trade_id}\n"
                                f"  Filled Shares: {filled_shares}\n"
                                f"  Fill Price: ${fill_price:.4f}\n"
                                f"  Dollars Spent: ${dollars_spent:.2f}\n"
                                f"  Fee: ${fee:.4f}"
                            )
                            
                            # Reload trade and place sell order
                            trade = self.db.get_trade_by_id(trade_id)
                            if trade:
                                logger.info(f"🔄 Placing initial sell order for trade {trade.id} after buy fill...")
                                try:
                                    await self.place_sell_order_callback(trade)
                                except Exception as sell_error:
                                    logger.error(
                                        f"❌ Failed to place initial sell order for trade {trade.id} after buy fill: {sell_error}",
                                        exc_info=True
                                    )
                            
                            # Remove from all_trades_to_check
                            all_trades_to_check.pop(fill_order_id, None)
            else:
                # No fills found - log this for debugging
                if all_trades_to_check:
                    logger.debug(
                        f"📊 No fills found for {len(all_trades_to_check)} open buy orders. "
                        f"Will check via open orders list and individual order status."
                    )
        except Exception as e:
            logger.error(f"Error checking fills/trades for buy orders: {e}", exc_info=True)
    
    async def _check_buy_orders_via_open_orders(self, all_trades_to_check: Dict[str, int]):
        """Check if buy orders are still in open orders list."""
        try:
            open_orders = self.pm.get_open_orders()
            open_order_ids = set()
            if open_orders:
                for o in open_orders:
                    oid = o.get("orderID") or o.get("order_id") or o.get("id")
                    if oid:
                        open_order_ids.add(oid)
            
            # Check orders that are NOT in open orders list
            # Only mark as filled if we can verify via get_order_status() that it's actually filled
            # Don't assume filled just because it's missing from open orders (could be cancelled, expired, or API issue)
            for order_id, trade_id in list(all_trades_to_check.items()):
                if order_id not in open_order_ids:
                    # Order is not in open orders - verify via get_order_status() before marking as filled
                    trade = self.db.get_trade_by_id(trade_id)
                    if trade and not trade.filled_shares and trade.order_status == "open":
                        # Check order status via API to verify it's actually filled
                        try:
                            order_status = self.pm.get_order_status(order_id)
                            if order_status:
                                # Parse order status to check if it's filled
                                status, filled_amount, total_amount = parse_order_status(order_status)
                                is_filled = is_order_filled(status, filled_amount, total_amount)
                                
                                if is_filled:
                                    # Verified via API that order is filled
                                    logger.info(
                                        f"✅✅✅ BUY ORDER FILLED (detected via open orders check + API verification) ✅✅✅\n"
                                        f"  Order ID: {order_id}\n"
                                        f"  Trade ID: {trade_id}\n"
                                        f"  API Status: {status}\n"
                                        f"  Filled Amount: {filled_amount}\n"
                                        f"  Total Amount: {total_amount}"
                                    )
                                    filled_shares = float(filled_amount) if filled_amount else trade.order_size
                                    fill_price = trade.order_price  # Use limit order price as fill price
                                    dollars_spent = filled_shares * fill_price
                                    fee = calculate_polymarket_fee(fill_price, dollars_spent)
                                    
                                    self.db.update_trade_fill(
                                        trade_id=trade_id,
                                        filled_shares=filled_shares,
                                        fill_price=fill_price,
                                        dollars_spent=dollars_spent,
                                        fee=fee,
                                        order_status="filled",
                                    )
                                    self.open_trades.pop(order_id, None)
                                    self.orders_not_found.pop(order_id, None)
                                    self.orders_checked_open.pop(order_id, None)
                                    logger.info(
                                        f"✅✅✅ BUY ORDER FILLED ✅✅✅\n"
                                        f"  Order ID: {order_id}\n"
                                        f"  Trade ID: {trade_id}\n"
                                        f"  Filled Shares: {filled_shares}\n"
                                        f"  Fill Price: ${fill_price:.4f}\n"
                                        f"  Dollars Spent: ${dollars_spent:.2f}\n"
                                        f"  Fee: ${fee:.4f}\n"
                                        f"  Detection Method: Open orders check + API verification"
                                    )
                                    
                                    # Reload trade and place sell order
                                    trade = self.db.get_trade_by_id(trade_id)
                                    if trade:
                                        logger.info(f"🔄 Placing initial sell order for trade {trade.id} after buy fill (detected via open orders check)...")
                                        try:
                                            await self.place_sell_order_callback(trade)
                                        except Exception as sell_error:
                                            logger.error(
                                                f"❌ Failed to place initial sell order for trade {trade.id} after buy fill: {sell_error}",
                                                exc_info=True
                                            )
                                    
                                    # Remove from all_trades_to_check
                                    all_trades_to_check.pop(order_id, None)
                                else:
                                    # Order not filled according to API - might be cancelled or expired
                                    logger.debug(
                                        f"Order {order_id} not in open orders but API status={status} doesn't indicate filled. "
                                        f"Will check again on next iteration."
                                    )
                            else:
                                # Order status not found - might be API issue, don't assume filled
                                logger.debug(
                                    f"Order {order_id} not in open orders and get_order_status() returned None. "
                                    f"Will check again on next iteration."
                                )
                        except Exception as e:
                            logger.debug(f"Error checking order status for {order_id}: {e}")
                            # Don't assume filled on error
        except Exception as e:
            logger.debug(f"Could not check open orders: {e}")
    
    async def _check_individual_buy_order_statuses(self, all_trades_to_check: Dict[str, int]):
        """Check individual order statuses for orders that are still open."""
        for order_id, trade_id in list(all_trades_to_check.items()):
            try:
                order_status = self.pm.get_order_status(order_id)
                if not order_status:
                    # If order not found, check retry count
                    retry_count = self.orders_not_found.get(order_id, 0)
                    
                    if retry_count < self.max_order_not_found_retries:
                        self.orders_not_found[order_id] = retry_count + 1
                        logger.debug(
                            f"Order {order_id} not found in API (retry {retry_count + 1}/{self.max_order_not_found_retries}) - "
                            f"will retry on next check"
                        )
                        continue
                    else:
                        # Max retries reached - remove from tracking
                        self.open_trades.pop(order_id, None)
                        self.orders_not_found.pop(order_id, None)
                        self.orders_checked_open.pop(order_id, None)
                    continue
                
                # Order found - clear retry count
                self.orders_not_found.pop(order_id, None)
                
                # Parse order status
                status, filled_amount, total_amount = parse_order_status(order_status)
                
                trade = self.db.get_trade_by_id(trade_id)
                if not trade:
                    continue
                
                # Check if order is filled or cancelled
                is_filled = is_order_filled(status, filled_amount, total_amount)
                is_cancelled = is_order_cancelled(status)
                
                if is_filled or is_cancelled:
                    if is_filled and not trade.filled_shares:
                        # Update trade with fill information
                        filled_shares = float(filled_amount) if filled_amount else trade.order_size
                        fill_price = trade.order_price
                        dollars_spent = filled_shares * fill_price
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
                        
                        # Reload trade and place sell order
                        trade = self.db.get_trade_by_id(trade_id)
                        if trade:
                            await self.place_sell_order_callback(trade)
                    
                    # Remove from open trades
                    self.open_trades.pop(order_id, None)
                    self.orders_not_found.pop(order_id, None)
                    self.orders_checked_open.pop(order_id, None)
                
                elif status == "open" and filled_amount and float(filled_amount) > 0:
                    # Partial fill - update trade
                    filled_shares = float(filled_amount)
                    fill_price = trade.order_price
                    dollars_spent = filled_shares * fill_price
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
                
                elif status == "open" and (not filled_amount or float(filled_amount) == 0):
                    # Order is still open with no fills - check if it should be cancelled
                    # 1. Check if market has resolved
                    market = get_market_by_slug(trade.market_slug) if trade.market_slug else None
                    if market and not is_market_active(market):
                        # Market has resolved but order never filled - cancel it
                        logger.warning(
                            f"⚠️ Buy order {order_id} (trade {trade_id}) is still open but market {trade.market_slug} "
                            f"has resolved. Cancelling order and marking as cancelled."
                        )
                        try:
                            cancel_result = self.pm.cancel_order(order_id)
                            if cancel_result:
                                logger.info(f"✅ Successfully cancelled buy order {order_id} via API")
                            else:
                                logger.warning(f"⚠️ Failed to cancel buy order {order_id} via API")
                        except Exception as e:
                            logger.warning(f"⚠️ Error cancelling buy order {order_id}: {e}")
                        
                        # Mark as cancelled in database
                        self.db.update_order_status(
                            trade_id,
                            "cancelled",
                            error_message="Order never filled before market resolution - cancelled"
                        )
                        # Remove from tracking
                        self.open_trades.pop(order_id, None)
                        self.orders_not_found.pop(order_id, None)
                        self.orders_checked_open.pop(order_id, None)
                        logger.info(
                            f"🚫 ORDER CANCELLED: Buy order {order_id} (trade {trade_id}, market {trade.market_slug}) "
                            f"was cancelled because market resolved before order filled. "
                            f"Order never filled, so principal remains unchanged."
                        )
                        continue
                    
                    # 2. Track how many times we've checked this order - cancel after 5 checks
                    check_count = self.orders_checked_open.get(order_id, 0) + 1
                    self.orders_checked_open[order_id] = check_count
                    
                    if check_count >= 5:
                        # Order has been checked 5 times and still open - cancel it
                        logger.warning(
                            f"⚠️ Buy order {order_id} (trade {trade_id}) is still open after {check_count} status checks. "
                            f"Cancelling order immediately."
                        )
                        try:
                            cancel_result = self.pm.cancel_order(order_id)
                            if cancel_result:
                                logger.info(f"✅ Successfully cancelled buy order {order_id} via API")
                            else:
                                logger.warning(f"⚠️ Failed to cancel buy order {order_id} via API")
                        except Exception as e:
                            logger.warning(f"⚠️ Error cancelling buy order {order_id}: {e}")
                        
                        # Mark as cancelled in database
                        self.db.update_order_status(
                            trade_id,
                            "cancelled",
                            error_message=f"Order still open after {check_count} status checks - cancelled"
                        )
                        # Remove from tracking
                        self.open_trades.pop(order_id, None)
                        self.orders_not_found.pop(order_id, None)
                        self.orders_checked_open.pop(order_id, None)
                        logger.info(
                            f"🚫 ORDER CANCELLED: Buy order {order_id} (trade {trade_id}, market {trade.market_slug}) "
                            f"was cancelled after {check_count} status checks (still open, never filled). "
                            f"Order never filled, so principal remains unchanged."
                        )
                        continue
                    else:
                        # Not yet at 5 checks - log progress
                        logger.debug(
                            f"📝 Buy order {order_id} (trade {trade_id}) is open - check {check_count}/5 "
                            f"(will cancel after 5 checks if still open)"
                        )
            
            except Exception as e:
                logger.error(f"Error checking order {order_id}: {e}", exc_info=True)
    
    async def check_sell_order_statuses(self):
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
        
        # Log heartbeat periodically
        if not hasattr(self, '_sell_order_check_count'):
            self._sell_order_check_count = 0
        self._sell_order_check_count += 1
        
        if self._sell_order_check_count % 10 == 0:
            logger.info(
                f"🔍 Monitoring {len(all_sell_orders_to_check)} sell order(s) (check #{self._sell_order_check_count}): "
                f"{list(all_sell_orders_to_check.keys())[:3]}..."
            )
        else:
            logger.debug(
                f"🔍 Checking {len(all_sell_orders_to_check)} sell order(s): {list(all_sell_orders_to_check.keys())[:3]}..."
            )
        
        # Check fills/trades to see if any sell orders have been filled
        await self._check_sell_order_fills(all_sell_orders_to_check)
        
        # Check individual sell order statuses
        await self._check_individual_sell_order_statuses(all_sell_orders_to_check)
    
    async def _check_sell_order_fills(self, all_sell_orders_to_check: Dict[str, int]):
        """Check fills/trades to see if any sell orders have been filled."""
        try:
            # Get our wallet address for filtering
            wallet_address = None
            if hasattr(self.pm, 'proxy_wallet_address') and self.pm.proxy_wallet_address:
                wallet_address = self.pm.proxy_wallet_address
            elif hasattr(self.pm, 'get_address_for_private_key'):
                wallet_address = self.pm.get_address_for_private_key()
            
            # First try with maker_address filter
            fills = self.pm.get_trades(maker_address=wallet_address)
            
            # Also get trades without filter to catch cases where we're the taker
            if fills:
                found_any_match = False
                for fill in fills:
                    maker_orders = fill.get("maker_orders") or []
                    taker_order_id = fill.get("taker_order_id")
                    maker_order_ids = []
                    if isinstance(maker_orders, list):
                        for maker_order in maker_orders:
                            if isinstance(maker_order, dict):
                                maker_order_id = maker_order.get("order_id") or maker_order.get("orderID")
                                if maker_order_id:
                                    maker_order_ids.append(maker_order_id)
                    for sell_order_id in all_sell_orders_to_check.keys():
                        if sell_order_id in maker_order_ids or taker_order_id == sell_order_id:
                            found_any_match = True
                            break
                    if found_any_match:
                        break
                
                if not found_any_match:
                    fills_unfiltered = self.pm.get_trades()
                    if fills_unfiltered:
                        existing_order_ids = {f.get("taker_order_id") for f in fills if f.get("taker_order_id")}
                        fills.extend([f for f in fills_unfiltered if f.get("taker_order_id") not in existing_order_ids])
            else:
                fills = self.pm.get_trades()
            
            if fills:
                logger.info(f"📊 Checking {len(fills)} trade records for sell order fills...")
                
                for fill in fills:
                    maker_orders = fill.get("maker_orders") or []
                    if not isinstance(maker_orders, list):
                        maker_orders = []
                    
                    taker_order_id = fill.get("taker_order_id")
                    
                    # Extract all order IDs from maker_orders
                    maker_order_ids = []
                    if isinstance(maker_orders, list):
                        for maker_order in maker_orders:
                            if isinstance(maker_order, dict):
                                maker_order_id = maker_order.get("order_id") or maker_order.get("orderID")
                                if maker_order_id:
                                    maker_order_ids.append(maker_order_id)
                    
                    # Check if any tracked sell order matches this trade record
                    fill_order_id = None
                    for sell_order_id in all_sell_orders_to_check.keys():
                        if sell_order_id in maker_order_ids:
                            fill_order_id = sell_order_id
                            break
                        elif taker_order_id == sell_order_id:
                            fill_order_id = sell_order_id
                            break
                    
                    # Check trade status
                    trade_status = fill.get("status") or fill.get("trade_status") or ""
                    trade_status_upper = str(trade_status).upper()
                    
                    if fill_order_id and fill_order_id in all_sell_orders_to_check:
                        trade_id = all_sell_orders_to_check[fill_order_id]
                        trade = self.db.get_trade_by_id(trade_id)
                        if trade and trade.sell_order_status == "open":
                            is_successful_trade = trade_status_upper in ["MATCHED", "MINED", "CONFIRMED"]
                            is_failed_trade = trade_status_upper == "FAILED"
                            
                            if is_failed_trade:
                                logger.warning(
                                    f"⚠️ Sell order {fill_order_id} (trade {trade_id}) trade record shows FAILED status"
                                )
                                session = self.db.SessionLocal()
                                try:
                                    trade_obj = session.query(RealTradeThreshold).filter_by(id=trade_id).first()
                                    if trade_obj:
                                        trade_obj.sell_order_status = "failed"
                                        trade_obj.error_message = f"Sell order trade failed: status={trade_status}"
                                        session.commit()
                                except Exception as e:
                                    session.rollback()
                                    logger.error(f"Error marking sell order as failed: {e}")
                                finally:
                                    session.close()
                                continue
                            
                            if is_successful_trade:
                                # Sell order was filled - extract fill details
                                filled_shares = fill.get("size")
                                fill_price_from_record = fill.get("price")
                                
                                if filled_shares:
                                    filled_shares = float(filled_shares)
                                else:
                                    filled_shares = trade.sell_order_size
                                
                                if fill_price_from_record:
                                    sell_price = float(fill_price_from_record)
                                else:
                                    sell_price = trade.sell_order_price or 0.99
                                
                                sell_dollars_received = filled_shares * sell_price
                                sell_fee = calculate_polymarket_fee(sell_price, sell_dollars_received)
                                
                                # Determine if this is a partial or full fill
                                is_full_fill = (trade.sell_order_size and abs(filled_shares - trade.sell_order_size) < 0.01)
                                sell_status = "filled" if is_full_fill else "partial"
                                
                                # Update sell order fill information
                                self.db.update_sell_order_fill(
                                    trade_id=trade_id,
                                    sell_order_status=sell_status,
                                    sell_shares_filled=filled_shares,
                                    sell_dollars_received=sell_dollars_received,
                                    sell_fee=sell_fee,
                                )
                                
                                logger.info(
                                    f"✅✅✅ SELL ORDER FILLED (via trade record) ✅✅✅\n"
                                    f"  Sell Order ID: {fill_order_id}\n"
                                    f"  Trade ID: {trade_id}\n"
                                    f"  Trade Status: {trade_status_upper}\n"
                                    f"  Filled Shares: {filled_shares} / {trade.sell_order_size or 'N/A'}\n"
                                    f"  Sell Price: ${sell_price:.4f}\n"
                                    f"  Dollars Received: ${sell_dollars_received:.2f}\n"
                                    f"  Sell Fee: ${sell_fee:.4f}\n"
                                    f"  Status: {sell_status}"
                                )
                                
                                self.open_sell_orders.pop(fill_order_id, None)
                                self.sell_orders_not_found.pop(fill_order_id, None)
                                all_sell_orders_to_check.pop(fill_order_id, None)
                                
                                # Clear re-price counter if it exists
                                if hasattr(self, '_threshold_sell_reprice_attempts'):
                                    self._threshold_sell_reprice_attempts.pop(trade_id, None)
        except Exception as e:
            logger.error(f"Error checking fills/trades for sell orders: {e}", exc_info=True)
    
    async def _check_individual_sell_order_statuses(self, all_sell_orders_to_check: Dict[str, int]):
        """Check individual sell order statuses."""
        for sell_order_id, trade_id in list(all_sell_orders_to_check.items()):
            try:
                order_status = self.pm.get_order_status(sell_order_id)
                if not order_status:
                    # If sell order not found, check retry count
                    retry_count = self.sell_orders_not_found.get(sell_order_id, 0)
                    
                    if retry_count < self.max_order_not_found_retries:
                        self.sell_orders_not_found[sell_order_id] = retry_count + 1
                        logger.debug(
                            f"Sell order {sell_order_id} not found in API (retry {retry_count + 1}/{self.max_order_not_found_retries}) - "
                            f"will retry on next check"
                        )
                        continue
                    
                    logger.warning(
                        f"Sell order {sell_order_id} not found in API after {self.max_order_not_found_retries} retries. "
                        f"Will continue checking via trade records."
                    )
                    continue
                
                # Sell order found - clear retry count
                self.sell_orders_not_found.pop(sell_order_id, None)
                
                # Parse order status
                status, filled_amount, total_amount = parse_order_status(order_status)
                
                trade = self.db.get_trade_by_id(trade_id)
                if not trade:
                    continue
                
                # Check if sell order is filled or cancelled
                is_filled = is_order_filled(status, filled_amount, total_amount)
                is_cancelled = is_order_cancelled(status)
                
                # Check for threshold sell re-pricing
                if trade.sell_order_status == "open" and status in ["live", "LIVE", "open", "OPEN"]:
                    if trade.sell_order_price and trade.sell_order_price < 0.99 and trade.sell_order_placed_at:
                        time_open = datetime.now(timezone.utc) - trade.sell_order_placed_at
                        if trade.sell_order_placed_at.tzinfo is None:
                            time_open = datetime.now(timezone.utc) - trade.sell_order_placed_at.replace(tzinfo=timezone.utc)
                        
                        seconds_open = time_open.total_seconds()
                        
                        # If threshold sell order has been open > 5 seconds without filling, re-price lower
                        if seconds_open > 5.0:
                            reprice_count = self._threshold_sell_reprice_attempts.get(trade_id, 0)
                            max_reprice_attempts = 3
                            
                            if reprice_count < max_reprice_attempts:
                                # Calculate new lower price
                                current_price = trade.sell_order_price
                                price_reduction = max(self.config.margin_sell, 0.01)
                                new_price = max(0.01, current_price - price_reduction)
                                
                                logger.info(
                                    f"⏰ Threshold sell order {sell_order_id} (trade {trade_id}) has been open for {seconds_open:.1f} seconds "
                                    f"(> 5 seconds) without filling. Cancelling and re-pricing at ${new_price:.4f} (was ${current_price:.4f}). "
                                    f"Re-price attempt {reprice_count + 1}/{max_reprice_attempts}"
                                )
                                
                                # Cancel the current order
                                try:
                                    cancel_result = self.pm.cancel_order(sell_order_id)
                                    if cancel_result:
                                        logger.info(f"✅ Successfully cancelled threshold sell order {sell_order_id} for re-pricing")
                                        self.open_sell_orders.pop(sell_order_id, None)
                                        
                                        # Update database
                                        session = self.db.SessionLocal()
                                        try:
                                            trade_obj = session.query(RealTradeThreshold).filter_by(id=trade_id).first()
                                            if trade_obj:
                                                trade_obj.sell_order_status = "cancelled"
                                                trade_obj.sell_order_id = None
                                                session.commit()
                                        except Exception as e:
                                            session.rollback()
                                            logger.error(f"Error updating database after cancellation: {e}")
                                        finally:
                                            session.close()
                                        
                                        # Reload trade and place new order at lower price
                                        trade = self.db.get_trade_by_id(trade_id)
                                        if trade:
                                            # This will be handled by the callback - we need to pass it
                                            # For now, we'll need to call the early sell callback
                                            # But we don't have access to it here - need to refactor
                                            # For now, skip re-pricing and let the main loop handle it
                                            logger.warning(
                                                f"Cannot re-price threshold sell order - need callback access. "
                                                f"Will be handled by orderbook monitor."
                                            )
                                        
                                        # Increment re-price counter
                                        self._threshold_sell_reprice_attempts[trade_id] = reprice_count + 1
                                        
                                        # Skip further processing
                                        continue
                                    else:
                                        logger.warning(f"⚠️ Failed to cancel threshold sell order {sell_order_id} for re-pricing")
                                except Exception as e:
                                    logger.error(f"Error cancelling threshold sell order for re-pricing: {e}", exc_info=True)
                            else:
                                logger.info(
                                    f"⏰ Threshold sell order {sell_order_id} (trade {trade_id}) has been open for {seconds_open:.1f} seconds "
                                    f"(> 5 seconds) but max re-price attempts ({max_reprice_attempts}) reached. Keeping current order."
                                )
                
                if is_filled or is_cancelled:
                    if is_filled:
                        # Update sell order with fill information
                        filled_shares = float(filled_amount) if filled_amount else trade.sell_order_size
                        sell_price = trade.sell_order_price or 0.99
                        sell_dollars_received = filled_shares * sell_price
                        sell_fee = calculate_polymarket_fee(sell_price, sell_dollars_received)
                        
                        # Determine if this is a partial or full fill
                        is_full_fill = (trade.sell_order_size and abs(filled_shares - trade.sell_order_size) < 0.01)
                        sell_status = "filled" if is_full_fill else "partial"
                        
                        # Update sell order fill information
                        self.db.update_sell_order_fill(
                            trade_id=trade_id,
                            sell_order_status=sell_status,
                            sell_shares_filled=filled_shares,
                            sell_dollars_received=sell_dollars_received,
                            sell_fee=sell_fee,
                        )
                        
                        logger.info(
                            f"Sell order {sell_order_id} filled: {filled_shares} shares / {trade.sell_order_size or 'N/A'}, "
                            f"received ${sell_dollars_received:.2f} (fee: ${sell_fee:.2f}), "
                            f"status: {sell_status}."
                        )
                        
                        # Clear re-price counter
                        if hasattr(self, '_threshold_sell_reprice_attempts'):
                            self._threshold_sell_reprice_attempts.pop(trade_id, None)
                    
                    # Remove from open sell orders
                    self.open_sell_orders.pop(sell_order_id, None)
                    self.sell_orders_not_found.pop(sell_order_id, None)
                
                elif is_order_partial_fill(status, filled_amount, total_amount):
                    # Partial fill - update sell order
                    filled_shares = filled_amount
                    sell_price = trade.sell_order_price or 0.99
                    sell_dollars_received = filled_shares * sell_price
                    sell_fee = calculate_polymarket_fee(sell_price, sell_dollars_received)
                    
                    # Update sell order fill information with partial fill
                    self.db.update_sell_order_fill(
                        trade_id=trade_id,
                        sell_order_status="partial",
                        sell_shares_filled=filled_shares,
                        sell_dollars_received=sell_dollars_received,
                        sell_fee=sell_fee,
                    )
                    
                    logger.info(
                        f"Partial fill detected for sell order {sell_order_id}: {filled_shares} shares filled "
                        f"(order size: {trade.sell_order_size or 'N/A'})"
                    )
            
            except Exception as e:
                logger.error(f"Error checking sell order {sell_order_id}: {e}", exc_info=True)
    
    async def retry_missing_sell_orders(self):
        """Retry placing sell orders for trades with filled buy orders but no sell orders."""
        try:
            session = self.db.SessionLocal()
            try:
                trades_needing_sell = session.query(RealTradeThreshold).filter(
                    RealTradeThreshold.deployment_id == self.deployment_id,
                    RealTradeThreshold.order_status == "filled",
                    RealTradeThreshold.filled_shares.isnot(None),
                    RealTradeThreshold.filled_shares > 0,
                    RealTradeThreshold.sell_order_id.is_(None),
                    RealTradeThreshold.market_resolved_at.is_(None),
                ).all()
                
                for trade in trades_needing_sell:
                    # Only retry if buy order filled more than 30 seconds ago
                    if trade.order_filled_at:
                        time_since_fill = datetime.now(timezone.utc) - trade.order_filled_at
                        if time_since_fill.total_seconds() < 30:
                            continue
                    
                    logger.info(
                        f"Found trade {trade.id} with filled buy order but no sell order. "
                        f"Retrying sell order placement..."
                    )
                    await self.place_sell_order_callback(trade)
            finally:
                session.close()
        except Exception as e:
            logger.debug(f"Error checking for missing sell orders: {e}")
