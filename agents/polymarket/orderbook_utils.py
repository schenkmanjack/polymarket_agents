"""
Utilities for getting meaningful prices from orderbook data.
The raw CLOB best_bid/best_ask can be misleading due to sparse liquidity.
"""
import httpx
import logging
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)


def get_market_price_from_clob(token_id: str) -> Optional[float]:
    """
    Get the actual market price from CLOB API.
    Priority: last_trade_price > weighted_mid > simple_mid
    
    Args:
        token_id: CLOB token ID
        
    Returns:
        Market price (float) or None if unavailable
    """
    try:
        url = "https://clob.polymarket.com/book"
        response = httpx.get(url, params={"token_id": token_id}, timeout=10.0)
        
        if response.status_code == 200:
            data = response.json()
            
            # First priority: last_trade_price (most accurate)
            last_trade_price = data.get("last_trade_price")
            if last_trade_price:
                return float(last_trade_price)
            
            # Second priority: weighted mid price (if orderbook has depth)
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            
            if bids and asks:
                # Calculate weighted mid price from top N levels
                weighted_price = calculate_weighted_mid_price(bids, asks, depth_levels=5)
                if weighted_price:
                    return weighted_price
                
                # Fallback: simple mid price
                best_bid = float(bids[0]["price"])
                best_ask = float(asks[0]["price"])
                return (best_bid + best_ask) / 2
        
        return None
    except Exception as e:
        logger.error(f"Error getting market price from CLOB: {e}")
        return None


def calculate_weighted_mid_price(bids: list, asks: list, depth_levels: int = 5) -> Optional[float]:
    """
    Calculate weighted mid price from orderbook depth.
    This gives a better price than simple best_bid/best_ask when orderbook is sparse.
    
    Args:
        bids: List of bid orders [{"price": "0.01", "size": "100"}, ...]
        asks: List of ask orders [{"price": "0.99", "size": "100"}, ...]
        depth_levels: Number of levels to consider
        
    Returns:
        Weighted mid price or None
    """
    try:
        if not bids or not asks:
            return None
        
        # Get top N levels
        top_bids = bids[:depth_levels]
        top_asks = asks[:depth_levels]
        
        # Calculate weighted average bid (weighted by size)
        total_bid_value = 0.0
        total_bid_size = 0.0
        for bid in top_bids:
            price = float(bid["price"])
            size = float(bid["size"])
            total_bid_value += price * size
            total_bid_size += size
        
        # Calculate weighted average ask (weighted by size)
        total_ask_value = 0.0
        total_ask_size = 0.0
        for ask in top_asks:
            price = float(ask["price"])
            size = float(ask["size"])
            total_ask_value += price * size
            total_ask_size += size
        
        if total_bid_size > 0 and total_ask_size > 0:
            weighted_bid = total_bid_value / total_bid_size
            weighted_ask = total_ask_value / total_ask_size
            return (weighted_bid + weighted_ask) / 2
        
        return None
    except Exception as e:
        logger.error(f"Error calculating weighted mid price: {e}")
        return None


def get_best_bid_ask_near_price(
    bids: list,
    asks: list,
    reference_price: float,
    max_spread_pct: float = 0.15
) -> Tuple[Optional[float], Optional[float]]:
    """
    Find the best bid/ask prices near a reference price (like UI does).
    Filters out orders that are too far from the market price.
    
    Args:
        bids: List of bid orders [{"price": "0.44", "size": "100"}, ...]
        asks: List of ask orders [{"price": "0.53", "size": "100"}, ...]
        reference_price: Reference price (e.g., outcome_price or last_trade_price)
        max_spread_pct: Maximum spread from reference price to consider (default: 15%)
        
    Returns:
        Tuple of (best_bid_near_price, best_ask_near_price) or (None, None)
    """
    try:
        if not bids or not asks or not reference_price:
            return None, None
        
        max_spread = reference_price * max_spread_pct
        min_price = max(0.01, reference_price - max_spread)
        max_price = min(0.99, reference_price + max_spread)
        
        # Find best bid (highest price) within range
        best_bid = None
        for bid in bids:
            price = float(bid["price"])
            if min_price <= price <= max_price:
                if best_bid is None or price > best_bid:
                    best_bid = price
        
        # Find best ask (lowest price) within range
        best_ask = None
        for ask in asks:
            price = float(ask["price"])
            if min_price <= price <= max_price:
                if best_ask is None or price < best_ask:
                    best_ask = price
        
        return best_bid, best_ask
    except Exception as e:
        logger.error(f"Error finding best bid/ask near price: {e}")
        return None, None


def get_order_price(
    token_id: str,
    side: str,
    market_id: Optional[str] = None,
    outcome_price: Optional[float] = None,
) -> Optional[float]:
    """
    Get appropriate order price for placing a limit order.
    
    Priority:
    1. last_trade_price from CLOB (most accurate recent price)
    2. outcome_price from Gamma API (what website shows)
    3. weighted_mid_price from CLOB orderbook depth
    4. simple_mid_price from CLOB best_bid/best_ask
    
    Args:
        token_id: CLOB token ID
        side: "BUY" or "SELL"
        market_id: Optional market ID (for fetching outcome_price)
        outcome_price: Optional outcome price from Gamma API
        
    Returns:
        Recommended limit order price or None
    """
    # Get market price from CLOB
    clob_price = get_market_price_from_clob(token_id)
    
    # Determine best price to use
    if clob_price:
        # Use CLOB price (last_trade_price or weighted mid)
        base_price = clob_price
    elif outcome_price:
        # Fallback to outcome_price from Gamma API
        base_price = outcome_price
    else:
        logger.warning(f"No price available for token {token_id[:20]}...")
        return None
    
    # For BUY: use base_price or slightly above to match immediately
    # For SELL: use base_price or slightly below to match immediately
    if side == "BUY":
        # Round up slightly to ensure order matches
        order_price = round(base_price + 0.001, 4)  # Add 0.1% to match
    else:  # SELL
        # Round down slightly to ensure order matches
        order_price = round(base_price - 0.001, 4)  # Subtract 0.1% to match
    
    # Ensure price is within valid range [0.01, 0.99]
    order_price = max(0.01, min(0.99, order_price))
    
    return order_price


def find_best_price_in_range(
    bids: list,
    asks: list,
    target_price: float,
    max_spread: float = 0.05
) -> Tuple[Optional[float], Optional[float]]:
    """
    Find the best bid/ask prices within a reasonable range of target_price.
    Useful when orderbook has sparse liquidity at top levels.
    
    Args:
        bids: List of bid orders
        asks: List of ask orders
        target_price: Target price (e.g., from last_trade_price or outcome_price)
        max_spread: Maximum spread from target_price to consider
        
    Returns:
        Tuple of (best_bid_in_range, best_ask_in_range) or (None, None)
    """
    try:
        best_bid = None
        best_ask = None
        
        # Find best bid within range
        for bid in bids:
            price = float(bid["price"])
            if target_price - max_spread <= price <= target_price + max_spread:
                if best_bid is None or price > best_bid:
                    best_bid = price
        
        # Find best ask within range
        for ask in asks:
            price = float(ask["price"])
            if target_price - max_spread <= price <= target_price + max_spread:
                if best_ask is None or price < best_ask:
                    best_ask = price
        
        return best_bid, best_ask
    except Exception as e:
        logger.error(f"Error finding best price in range: {e}")
        return None, None

