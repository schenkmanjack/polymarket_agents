"""
Orderbook helper functions for live trading.

Provides functions to fetch orderbook data and check threshold conditions.
Supports both WebSocket (real-time) and HTTP (fallback) orderbook fetching.
"""
import logging
import httpx
from typing import Optional, Tuple, Dict
from agents.utils.proxy_config import get_proxy_dict

logger = logging.getLogger(__name__)

# Global WebSocket service instance (set by ThresholdTrader)
_websocket_service = None
_fallback_logged = False  # Track if we've logged fallback message


def set_websocket_service(service):
    """Set the global WebSocket service instance."""
    global _websocket_service
    _websocket_service = service


def fetch_orderbook(token_id: str) -> Optional[Dict]:
    """
    Fetch orderbook from WebSocket cache (if available) or HTTP API (fallback).
    
    Args:
        token_id: CLOB token ID
        
    Returns:
        Dict with 'bids' and 'asks' (lists of [price, size] tuples), or None if error
    """
    global _websocket_service, _fallback_logged
    
    # Try WebSocket cache first if service is available and connected
    if _websocket_service and _websocket_service.is_connected():
        orderbook = _websocket_service.get_orderbook(token_id)
        if orderbook:
            # WebSocket is working - log once if we were previously falling back
            if _fallback_logged:
                logger.info("✓ WebSocket orderbook service is working again - switching back to WebSocket")
                _fallback_logged = False
            return orderbook
        # Cache miss or stale - fall through to HTTP
    
    # Fallback to HTTP
    if not _fallback_logged:
        logger.warning("⚠️ Falling back to HTTP for orderbook data (WebSocket unavailable or cache miss)")
        _fallback_logged = True
    
    try:
        url = "https://clob.polymarket.com/book"
        proxies = get_proxy_dict()
        response = httpx.get(url, params={"token_id": token_id}, proxies=proxies, timeout=10.0)
        
        if response.status_code == 200:
            data = response.json()
            bids = [[float(b["price"]), float(b["size"])] for b in data.get("bids", [])]
            asks = [[float(a["price"]), float(a["size"])] for a in data.get("asks", [])]
            return {"bids": bids, "asks": asks}
        else:
            logger.warning(f"Failed to fetch orderbook for {token_id}: HTTP {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching orderbook for {token_id}: {e}")
        return None


def get_lowest_ask(orderbook: Dict) -> Optional[float]:
    """
    Get the lowest ask price from orderbook.
    
    Args:
        orderbook: Dict with 'asks' key (list of [price, size] tuples)
        
    Returns:
        Lowest ask price or None if not found
    """
    asks = orderbook.get("asks", [])
    if not asks:
        return None
    
    lowest_ask = None
    for ask in asks:
        if isinstance(ask, (list, tuple)) and len(ask) >= 1:
            try:
                ask_price = float(ask[0])
                if lowest_ask is None or ask_price < lowest_ask:
                    lowest_ask = ask_price
            except (ValueError, TypeError):
                continue
    
    return lowest_ask


def get_highest_bid(orderbook: Dict) -> Optional[float]:
    """
    Get the highest bid price from orderbook.
    
    Always takes the MAXIMUM of all bids to ensure we get the true best bid,
    regardless of how the orderbook is sorted.
    
    Args:
        orderbook: Dict with 'bids' key (list of [price, size] tuples)
        
    Returns:
        Highest bid price (maximum of all bids) or None if not found
    """
    bids = orderbook.get("bids", [])
    if not bids:
        return None
    
    # Always iterate through ALL bids to find the maximum
    # Don't assume orderbook is sorted correctly
    highest_bid = None
    for bid in bids:
        if isinstance(bid, (list, tuple)) and len(bid) >= 1:
            try:
                bid_price = float(bid[0])
                if highest_bid is None or bid_price > highest_bid:
                    highest_bid = bid_price
            except (ValueError, TypeError):
                continue
    
    return highest_bid


def calculate_midpoint(orderbook: Dict, weighted: bool = False, depth_levels: int = 5) -> Optional[float]:
    """
    Calculate midpoint price from orderbook.
    
    Simple midpoint: (highest_bid + lowest_ask) / 2
    Weighted midpoint: Volume-weighted average of top N levels on each side
    
    Args:
        orderbook: Dict with 'bids' and 'asks' keys (lists of [price, size] tuples)
        weighted: If True, use volume-weighted midpoint (default: False)
        depth_levels: Number of orderbook levels to consider for weighted calculation (default: 5)
        
    Returns:
        Midpoint price or None if orderbook incomplete
    """
    if weighted:
        return calculate_weighted_midpoint(orderbook, depth_levels=depth_levels)
    
    # Simple midpoint
    highest_bid = get_highest_bid(orderbook)
    lowest_ask = get_lowest_ask(orderbook)
    
    if highest_bid is None or lowest_ask is None:
        return None
    
    return (highest_bid + lowest_ask) / 2.0


def calculate_weighted_midpoint(orderbook: Dict, depth_levels: int = 5) -> Optional[float]:
    """
    Calculate volume-weighted midpoint price from orderbook depth.
    
    This gives a better price than simple best_bid/best_ask when orderbook is sparse
    or has large orders at certain price levels. It calculates the volume-weighted
    average price for the top N levels on each side, then takes the midpoint.
    
    Args:
        orderbook: Dict with 'bids' and 'asks' keys (lists of [price, size] tuples)
        depth_levels: Number of orderbook levels to consider (default: 5)
        
    Returns:
        Weighted midpoint price or None if orderbook incomplete
    """
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])
    
    if not bids or not asks:
        return None
    
    try:
        # Get top N levels (they should already be sorted by price)
        top_bids = bids[:depth_levels]
        top_asks = asks[:depth_levels]
        
        # Calculate weighted average bid (weighted by size)
        total_bid_value = 0.0
        total_bid_size = 0.0
        for bid in top_bids:
            if isinstance(bid, (list, tuple)) and len(bid) >= 2:
                try:
                    price = float(bid[0])
                    size = float(bid[1])
                    total_bid_value += price * size
                    total_bid_size += size
                except (ValueError, TypeError):
                    continue
        
        # Calculate weighted average ask (weighted by size)
        total_ask_value = 0.0
        total_ask_size = 0.0
        for ask in top_asks:
            if isinstance(ask, (list, tuple)) and len(ask) >= 2:
                try:
                    price = float(ask[0])
                    size = float(ask[1])
                    total_ask_value += price * size
                    total_ask_size += size
                except (ValueError, TypeError):
                    continue
        
        if total_bid_size > 0 and total_ask_size > 0:
            weighted_bid = total_bid_value / total_bid_size
            weighted_ask = total_ask_value / total_ask_size
            return (weighted_bid + weighted_ask) / 2.0
        
        # Fallback to simple midpoint if weighted calculation fails
        highest_bid = get_highest_bid(orderbook)
        lowest_ask = get_lowest_ask(orderbook)
        if highest_bid is not None and lowest_ask is not None:
            return (highest_bid + lowest_ask) / 2.0
        
        return None
    except Exception as e:
        logger.warning(f"Error calculating weighted midpoint: {e}, falling back to simple midpoint")
        # Fallback to simple midpoint
        highest_bid = get_highest_bid(orderbook)
        lowest_ask = get_lowest_ask(orderbook)
        if highest_bid is not None and lowest_ask is not None:
            return (highest_bid + lowest_ask) / 2.0
        return None


def check_threshold_triggered(
    yes_orderbook: Optional[Dict],
    no_orderbook: Optional[Dict],
    threshold: float,
) -> Optional[Tuple[str, float]]:
    """
    Check if threshold is triggered for either YES or NO side.
    
    Args:
        yes_orderbook: Orderbook for YES token (dict with 'asks' key)
        no_orderbook: Orderbook for NO token (dict with 'asks' key)
        threshold: Threshold value (0.0 to 1.0)
        
    Returns:
        Tuple of (side, lowest_ask) if triggered, None otherwise.
        Side is 'YES' or 'NO', lowest_ask is the price that triggered.
    """
    # Check YES side first (as per requirement: bet on first trigger)
    if yes_orderbook:
        yes_lowest_ask = get_lowest_ask(yes_orderbook)
        if yes_lowest_ask is not None and yes_lowest_ask >= threshold:
            return ("YES", yes_lowest_ask)
    
    # Check NO side
    if no_orderbook:
        no_lowest_ask = get_lowest_ask(no_orderbook)
        if no_lowest_ask is not None and no_lowest_ask >= threshold:
            return ("NO", no_lowest_ask)
    
    return None
