"""
Market resolution calculation utilities for threshold strategy.

Pure functions for calculating ROI, payout, and principal updates.
"""
from typing import Tuple, Optional
from agents.backtesting.backtesting_utils import calculate_polymarket_fee


def calculate_roi(net_payout: float, dollars_spent: float, fee: float) -> float:
    """
    Calculate ROI (Return on Investment).
    
    ROI = (after - before) / before
    where after = payout - sell_fee, before = dollars_spent + fee
    
    Args:
        net_payout: Net payout after all fees (can be negative)
        dollars_spent: Dollars spent on buy order
        fee: Buy fee
    
    Returns:
        ROI as a decimal (e.g., 0.1 for 10% return, -0.5 for 50% loss)
    """
    total_cost = dollars_spent + fee
    if total_cost <= 0:
        return 0.0
    return net_payout / total_cost


def calculate_payout_for_filled_sell(
    sell_dollars_received: float,
    sell_fee: float,
    dollars_spent: float,
    buy_fee: float,
) -> Tuple[float, float, float]:
    """
    Calculate payout, net_payout, and ROI for a trade where sell order filled.
    
    Args:
        sell_dollars_received: Dollars received from sell order
        sell_fee: Fee paid on sell order
        dollars_spent: Dollars spent on buy order
        buy_fee: Fee paid on buy order
    
    Returns:
        Tuple of (payout, net_payout, roi)
    """
    payout = sell_dollars_received
    net_payout = sell_dollars_received - sell_fee - dollars_spent - buy_fee
    roi = calculate_roi(net_payout, dollars_spent, buy_fee)
    return payout, net_payout, roi


def calculate_payout_for_unfilled_sell(
    outcome_price: float,
    filled_shares: float,
    order_side: str,
    dollars_spent: float,
    buy_fee: float,
    winning_side: Optional[str] = None,
) -> Tuple[bool, float, float, float]:
    """
    Calculate payout, net_payout, and ROI for a trade where sell order didn't fill.
    
    NOTE: This function is ONLY used when the sell order didn't fill. When the sell order
    fills, we use actual proceeds and don't need winning_side.
    
    Determines win/loss based on winning_side (preferred) or outcome_price (fallback):
    - If we lost: payout = $0, net_payout = -(dollars_spent + fee)
    - If we won: payout = $1 per share (claimable), net_payout = payout - estimated_sell_fee - dollars_spent - fee
    
    Args:
        outcome_price: Price of the token we bet on (0.0 to 1.0) - used for payout calculation
        filled_shares: Number of shares filled
        order_side: 'YES' or 'NO' - the side we bet on
        dollars_spent: Dollars spent on buy order
        buy_fee: Fee paid on buy order
        winning_side: 'YES' or 'NO' - the side that won the market (preferred method)
    
    Returns:
        Tuple of (bet_won, payout, net_payout, roi)
    """
    # Determine win/loss: compare our order_side to the winning_side
    # This is the most reliable method as it uses the actual market resolution
    if winning_side is not None:
        bet_won = (order_side == winning_side)
    else:
        # Fallback: use outcome_price (less reliable, but works if winning_side not available)
        # outcome_price is the price of the token we bet on
        # If we won, the token price should be close to 1.0 (e.g., > 0.5)
        bet_won = outcome_price > 0.5
    
    if not bet_won:
        # We lost - shares are worthless
        payout = 0.0
        net_payout = -dollars_spent - buy_fee  # Lost the entire bet (buy cost + buy fee)
        roi = calculate_roi(net_payout, dollars_spent, buy_fee)
    else:
        # We won but sell order didn't fill
        # Market resolved in our favor - shares are worth $1 each (outcome_price = 1.0)
        # Calculate as if we claim at $1 per share, accounting for sell fees
        payout = outcome_price * filled_shares  # Should be $1 * shares = total claimable
        
        # Calculate sell fee as if we sold at $1 per share
        estimated_sell_fee = calculate_polymarket_fee(1.0, payout)  # Fee for selling at $1
        
        # Net payout accounts for both buy and sell fees
        net_payout = payout - estimated_sell_fee - dollars_spent - buy_fee
        roi = calculate_roi(net_payout, dollars_spent, buy_fee)
    
    return bet_won, payout, net_payout, roi


def calculate_payout_for_partial_fill(
    sell_dollars_received: float,
    sell_fee: float,
    filled_shares: float,
    sell_shares_filled: float,
    outcome_price: float,
    order_side: str,
    dollars_spent: float,
    buy_fee: float,
    winning_side: Optional[str] = None,
) -> Tuple[float, float, float]:
    """
    Calculate payout, net_payout, and ROI for a trade with partial sell order fill.
    
    NOTE: This function is used when the sell order partially filled. We need winning_side
    to determine the value of the remaining unsold shares. For full fills, we use actual
    proceeds and don't need winning_side.
    
    Combines:
    - Proceeds from filled shares (already sold) - uses actual proceeds
    - Value of remaining unfilled shares at market resolution - needs winning_side to determine
    
    Args:
        sell_dollars_received: Dollars received from filled portion of sell order
        sell_fee: Fee paid on filled portion of sell order
        filled_shares: Total shares bought (from buy order)
        sell_shares_filled: Shares that were sold (partial fill)
        outcome_price: Price of the token we bet on (0.0 to 1.0) - used for payout calculation
        order_side: 'YES' or 'NO' - the side we bet on
        dollars_spent: Dollars spent on buy order
        buy_fee: Fee paid on buy order
        winning_side: 'YES' or 'NO' - the side that won the market (needed to value remaining shares)
    
    Returns:
        Tuple of (payout, net_payout, roi)
    """
    # Calculate remaining unfilled shares
    remaining_shares = filled_shares - sell_shares_filled
    
    # Determine win/loss for remaining shares: compare our order_side to the winning_side
    # This is the most reliable method as it uses the actual market resolution
    if winning_side is not None:
        bet_won = (order_side == winning_side)
    else:
        # Fallback: use outcome_price (less reliable, but works if winning_side not available)
        # outcome_price is the price of the token we bet on
        # If we won, the token price should be close to 1.0 (e.g., > 0.5)
        bet_won = outcome_price > 0.5
    
    # Calculate value of remaining shares at market resolution
    if not bet_won:
        # We lost - remaining shares are worthless
        remaining_shares_value = 0.0
        remaining_sell_fee = 0.0
    else:
        # We won - remaining shares are worth outcome_price per share
        remaining_shares_value = outcome_price * remaining_shares
        # Calculate estimated sell fee if we were to sell remaining shares at outcome_price
        remaining_sell_fee = calculate_polymarket_fee(outcome_price, remaining_shares_value)
    
    # Total payout = proceeds from sold shares + value of remaining shares
    payout = sell_dollars_received + remaining_shares_value
    
    # Total fees = sell fee on filled portion + estimated sell fee on remaining + buy fee
    total_sell_fee = sell_fee + remaining_sell_fee
    
    # Net payout = total payout - all fees - buy cost
    net_payout = payout - total_sell_fee - dollars_spent - buy_fee
    
    # Calculate ROI
    roi = calculate_roi(net_payout, dollars_spent, buy_fee)
    
    return payout, net_payout, roi


def determine_bet_outcome(order_side: str, winning_side: Optional[str] = None, outcome_price: Optional[float] = None) -> bool:
    """
    Determine if a bet won based on winning side (preferred) or outcome price (fallback).
    
    Args:
        order_side: 'YES' or 'NO' - the side we bet on
        winning_side: 'YES' or 'NO' - the side that won the market (preferred method)
        outcome_price: Price of the token we bet on (0.0 to 1.0) - used as fallback if winning_side not available
    
    Returns:
        True if bet won, False if bet lost
    
    Note: Using winning_side is the most reliable method as it uses the actual market resolution.
          outcome_price is only used as a fallback if winning_side is not provided.
    """
    # Preferred method: compare our order_side to the winning_side
    if winning_side is not None:
        return order_side == winning_side
    
    # Fallback: use outcome_price (less reliable)
    if outcome_price is not None:
        # outcome_price is the price of the token we bet on
        # If we won, the token price should be close to 1.0 (e.g., > 0.5)
        return outcome_price > 0.5
    
    # If neither is provided, we can't determine - return False to be safe
    return False
