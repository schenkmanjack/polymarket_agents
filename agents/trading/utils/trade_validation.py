"""
Trade validation utilities for threshold strategy.

Pure functions for validating trades and orders.
"""
from typing import Dict, Optional, Tuple
from agents.trading.trade_db import RealTradeThreshold


def validate_trade_for_resolution(trade: RealTradeThreshold) -> Tuple[bool, Optional[str]]:
    """
    Validate that a trade is ready for market resolution processing.
    
    Args:
        trade: Trade object to validate
    
    Returns:
        Tuple of (is_valid, error_message)
        If is_valid is False, error_message contains the reason
    """
    # Only process trades that actually executed
    if not trade.order_id:
        return False, "Trade has no order_id - order never placed"
    
    if trade.order_status in ["cancelled", "failed"]:
        return False, f"Trade has status '{trade.order_status}' - order did not execute"
    
    # Check if order was actually filled
    filled_shares = trade.filled_shares or 0.0
    dollars_spent = trade.dollars_spent or 0.0
    
    if filled_shares <= 0 or dollars_spent <= 0:
        return False, f"Trade was not filled (filled_shares={filled_shares}, dollars_spent=${dollars_spent:.2f})"
    
    return True, None


def check_order_belongs_to_market(
    order_status: Dict,
    trade_market_id: str,
    trade_token_id: str,
) -> bool:
    """
    Check if an order belongs to a specific market/trade.
    
    Args:
        order_status: Order status dictionary from API
        trade_market_id: Market ID from trade record
        trade_token_id: Token ID from trade record
    
    Returns:
        True if order belongs to the market, False otherwise
    """
    order_market = order_status.get("market")
    order_asset_id = order_status.get("asset_id")
    
    # Check if order's market or asset_id matches this trade's market
    return order_market == trade_market_id or order_asset_id == trade_token_id
