"""
Order status parsing utilities for threshold strategy.

Pure functions for parsing order status from API responses.
"""
from typing import Dict, Optional, Tuple


def parse_order_status(order_status: Dict) -> Tuple[str, float, float]:
    """
    Parse order status from API response.
    
    Handles multiple field name variations from different API versions.
    
    Args:
        order_status: Order status dictionary from API
    
    Returns:
        Tuple of (status, filled_amount, total_amount)
    """
    status = order_status.get("status", "unknown")
    
    # Try multiple field names for filled amount
    filled_amount = (
        order_status.get("size_matched") or  # Gemini's code uses this
        order_status.get("filledAmount") or 
        order_status.get("filled_amount") or 
        0
    )
    
    # Try multiple field names for total amount
    total_amount = (
        order_status.get("original_size") or  # Gemini's code uses this
        order_status.get("totalAmount") or 
        order_status.get("total_amount") or 
        0
    )
    
    # Convert to float if they're strings
    try:
        filled_amount = float(filled_amount) if filled_amount else 0
    except (ValueError, TypeError):
        filled_amount = 0
    
    try:
        total_amount = float(total_amount) if total_amount else 0
    except (ValueError, TypeError):
        total_amount = 0
    
    return status, filled_amount, total_amount


def is_order_filled(status: str, filled_amount: float, total_amount: float) -> bool:
    """
    Check if an order is filled based on status and amounts.
    
    Args:
        status: Order status string
        filled_amount: Amount filled
        total_amount: Total order amount
    
    Returns:
        True if order is filled, False otherwise
    """
    # Check status-based fill indicators
    is_filled_by_status = status in ["filled", "FILLED", "complete", "COMPLETE", "matched", "MATCHED"]
    
    # Check amount-based fill indicators
    is_filled_by_amount = (
        filled_amount > 0 and 
        total_amount > 0 and 
        filled_amount >= total_amount
    )
    
    return is_filled_by_status or is_filled_by_amount


def is_order_cancelled(status: str) -> bool:
    """
    Check if an order is cancelled based on status.
    
    Args:
        status: Order status string
    
    Returns:
        True if order is cancelled, False otherwise
    """
    return status in ["cancelled", "CANCELLED", "canceled", "CANCELED"]


def is_order_partial_fill(status: str, filled_amount: float, total_amount: float) -> bool:
    """
    Check if an order has a partial fill.
    
    Args:
        status: Order status string
        filled_amount: Amount filled
        total_amount: Total order amount
    
    Returns:
        True if order has partial fill, False otherwise
    """
    return (
        status in ["open", "OPEN", "live", "LIVE", "partial", "PARTIAL"] and
        filled_amount > 0 and
        total_amount > 0 and
        filled_amount < total_amount
    )
