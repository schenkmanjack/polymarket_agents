"""
Orderbook helper functions for live trading.

Provides functions to fetch orderbook data and check threshold conditions.
"""
import logging
import httpx
from typing import Optional, Tuple, Dict
from agents.utils.proxy_config import get_proxy_dict

logger = logging.getLogger(__name__)


def fetch_orderbook(token_id: str) -> Optional[Dict]:
    """
    Fetch orderbook from CLOB API (same as monitoring script).
    
    Args:
        token_id: CLOB token ID
        
    Returns:
        Dict with 'bids' and 'asks' (lists of [price, size] tuples), or None if error
    """
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
    
    Args:
        orderbook: Dict with 'bids' key (list of [price, size] tuples)
        
    Returns:
        Highest bid price or None if not found
    """
    bids = orderbook.get("bids", [])
    if not bids:
        return None
    
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
