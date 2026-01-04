"""
Detector for new BTC updown 15-minute markets.
Uses event slug pattern matching since API filtering might hide these markets.
"""
import httpx
import logging
from typing import List, Optional, Dict
from datetime import datetime
import re

logger = logging.getLogger(__name__)


def find_btc_updown_15m_events(limit: int = 100) -> List[Dict]:
    """
    Find BTC updown 15-minute events by searching event slugs.
    
    Returns:
        List of event dicts matching btc-updown-15m pattern
    """
    events_url = "https://gamma-api.polymarket.com/events"
    
    # Search for events - we'll filter by slug pattern
    response = httpx.get(events_url, params={
        "active": True,
        "closed": False,
        "archived": False,
        "limit": limit,
    })
    
    if response.status_code != 200:
        logger.error(f"Failed to fetch events: HTTP {response.status_code}")
        return []
    
    events = response.json()
    
    # Filter for BTC updown 15-minute markets
    btc_events = []
    for event in events:
        slug = (event.get("slug") or "").lower()
        title = (event.get("title") or "").lower()
        
        # Look for btc-updown-15m pattern in slug
        if "btc-updown-15m" in slug or ("btc" in slug and "updown" in slug and "15m" in slug):
            btc_events.append(event)
            logger.debug(f"Found BTC 15m event: {event.get('slug')}")
    
    return btc_events


def get_latest_btc_15m_market() -> Optional[Dict]:
    """
    Get the most recent BTC 15-minute market.
    
    Returns:
        Market dict for the latest BTC updown 15-minute market, or None
    """
    events = find_btc_updown_15m_events(limit=200)
    
    if not events:
        return None
    
    # Sort by creation date or ID to get latest
    # Events with higher IDs are usually newer
    latest_event = max(events, key=lambda e: int(e.get("id", 0)))
    
    # Get markets from the event
    markets = latest_event.get("markets", [])
    if markets:
        # Return the first market (usually there's one per event)
        market = markets[0]
        # Add event info to market
        market["_event_slug"] = latest_event.get("slug")
        market["_event_title"] = latest_event.get("title")
        return market
    
    return None


def get_all_active_btc_15m_markets() -> List[Dict]:
    """
    Get all active BTC 15-minute markets.
    
    Returns:
        List of market dicts for active BTC updown 15-minute markets
    """
    events = find_btc_updown_15m_events(limit=500)
    
    all_markets = []
    for event in events:
        markets = event.get("markets", [])
        for market in markets:
            # Add event info
            market["_event_slug"] = event.get("slug")
            market["_event_title"] = event.get("title")
            all_markets.append(market)
    
    return all_markets


def extract_timestamp_from_slug(slug: str) -> Optional[int]:
    """
    Extract timestamp from event slug like 'btc-updown-15m-1767393900'.
    
    Returns:
        Timestamp as int, or None if not found
    """
    # Pattern: btc-updown-15m-{timestamp}
    match = re.search(r'btc-updown-15m-(\d+)', slug)
    if match:
        return int(match.group(1))
    return None


def is_market_active(market: Dict) -> bool:
    """Check if market is currently active."""
    end_date = market.get("endDate") or market.get("endDateIso")
    if not end_date:
        return True  # Assume active if no end date
    
    try:
        if isinstance(end_date, str):
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        else:
            end_dt = end_date
        
        return datetime.now(end_dt.tzinfo) < end_dt
    except:
        return True  # Assume active if can't parse

