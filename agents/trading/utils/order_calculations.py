"""
Order calculation utilities for threshold strategy.

Pure functions for calculating order sizes, fees, and Kelly amounts.
"""
import math
from typing import Tuple, Optional


def calculate_fee_multiplier(order_price: float, fee_rate: float = 0.25, exponent: int = 2) -> float:
    """
    Calculate the fee multiplier for a given order price.
    
    Polymarket fee formula: fee_rate * (p * (1 - p))^exponent
    
    Args:
        order_price: The order price (0.0 to 1.0)
        fee_rate: Base fee rate (default 0.25)
        exponent: Fee exponent (default 2)
    
    Returns:
        Fee multiplier (0.0 to 1.0)
    """
    p_times_one_minus_p = order_price * (1.0 - order_price)
    return fee_rate * (p_times_one_minus_p ** exponent)


def calculate_order_size_with_fees(
    amount_invested: float,
    order_price: float,
    dollar_bet_limit: float,
    min_order_value: float = 1.0,
    fee_rate: float = 0.25,
    exponent: int = 2,
) -> Tuple[Optional[int], Optional[float], Optional[float], Optional[float]]:
    """
    Calculate order size accounting for fees.
    
    IMPORTANT: Fees reduce the SHARES RECEIVED, not just add to cost.
    If we order X shares at price P:
      - Cost = X * P
      - Fee = (X * P) * fee_multiplier
      - Shares lost = Fee / P = X * fee_multiplier
      - Shares received = X - X * fee_multiplier = X * (1 - fee_multiplier)
    
    To get N shares after fees: N = X * (1 - fee_multiplier)
    Therefore: X = N / (1 - fee_multiplier)
    
    Args:
        amount_invested: Amount to invest (after Kelly calculation)
        order_price: Limit order price (0.0 to 1.0)
        dollar_bet_limit: Maximum bet size limit
        min_order_value: Minimum order value (default $1.00)
        fee_rate: Base fee rate (default 0.25)
        exponent: Fee exponent (default 2)
    
    Returns:
        Tuple of (order_size, order_value, estimated_shares_received, estimated_fee)
        Returns (None, None, None, None) if order is too small or invalid
    """
    # Start with desired shares based on amount_invested (this is what we want AFTER fees)
    desired_shares_after_fee = amount_invested / order_price
    
    # Calculate fee multiplier
    fee_multiplier = calculate_fee_multiplier(order_price, fee_rate, exponent)
    
    # Calculate how many shares we need to ORDER to get desired_shares_after_fee
    if fee_multiplier < 1.0:  # Avoid division by zero
        shares_to_order = desired_shares_after_fee / (1.0 - fee_multiplier)
        
        # Calculate order value and cap at dollar_bet_limit
        order_value_with_fee = shares_to_order * order_price
        if order_value_with_fee > dollar_bet_limit:
            # Cap at dollar_bet_limit, recalculate shares
            order_value_with_fee = dollar_bet_limit
            shares_to_order = order_value_with_fee / order_price
            # Recalculate what we'll get after fees
            desired_shares_after_fee = shares_to_order * (1.0 - fee_multiplier)
        
        estimated_fee = order_value_with_fee * fee_multiplier
        estimated_shares_received = shares_to_order * (1.0 - fee_multiplier)
    else:
        shares_to_order = desired_shares_after_fee
        order_value_with_fee = shares_to_order * order_price
        estimated_fee = order_value_with_fee * fee_multiplier
        estimated_shares_received = shares_to_order * (1.0 - fee_multiplier)
    
    # Round UP to whole shares to ensure we get at least the desired shares after fees
    # (Polymarket requires whole shares - fractional shares not supported)
    order_size = math.ceil(shares_to_order)
    
    # Calculate actual order value
    order_value = order_size * order_price
    
    # Check if order size is valid
    if order_size < 1:
        return None, None, None, None
    
    # Check if order value meets minimum requirement
    if order_value < min_order_value:
        # Try to increase order size to meet minimum
        min_order_size = math.ceil(min_order_value / order_price)
        new_order_value = min_order_size * order_price
        
        # Check if rounded-up amount exceeds dollar_bet_limit
        if new_order_value > dollar_bet_limit:
            return None, None, None, None
        
        order_size = min_order_size
        order_value = new_order_value
        # Recalculate estimated values with new order size
        estimated_shares_received = order_size * (1.0 - fee_multiplier)
        estimated_fee = order_value * fee_multiplier
    
    return int(order_size), order_value, estimated_shares_received, estimated_fee


def calculate_kelly_amount(
    principal: float,
    kelly_fraction: float,
    kelly_scale_factor: float,
) -> float:
    """
    Calculate Kelly-calculated bet amount.
    
    Args:
        principal: Current principal
        kelly_fraction: Kelly fraction (0.0 to 1.0)
        kelly_scale_factor: Kelly scale factor (0.0 to 1.0)
    
    Returns:
        Kelly-calculated amount to invest
    """
    return principal * kelly_fraction * kelly_scale_factor
