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
    Tries multiple approaches since API might filter results.
    
    Returns:
        List of event dicts matching btc-updown-15m pattern
    """
    events_url = "https://gamma-api.polymarket.com/events"
    
    # Try without filters first (might get more results)
    all_events = []
    
    # Approach 1: Search with filters
    response = httpx.get(events_url, params={
        "active": True,
        "closed": False,
        "archived": False,
        "limit": limit,
    })
    
    if response.status_code == 200:
        all_events.extend(response.json())
    
    # Approach 2: Search without closed filter (recently closed might still be active)
    response2 = httpx.get(events_url, params={
        "active": True,
        "archived": False,
        "limit": limit,
    })
    
    if response2.status_code == 200:
        events2 = response2.json()
        # Add events not already in list
        existing_slugs = {e.get("slug") for e in all_events}
        for event in events2:
            if event.get("slug") not in existing_slugs:
                all_events.append(event)
    
    # Approach 3: Search markets directly (might find them there)
    markets_url = "https://gamma-api.polymarket.com/markets"
    response3 = httpx.get(markets_url, params={
        "active": True,
        "limit": limit * 2,  # Check more markets
        "enableOrderBook": True,
    })
    
    if response3.status_code == 200:
        markets = response3.json()
        # Look for BTC markets in question/slug
        for market in markets:
            question = (market.get("question") or "").lower()
            slug = (market.get("slug") or "").lower()
            
            # Check for BTC updown pattern
            if ("bitcoin" in question or "btc" in question) and ("up" in question or "down" in question):
                # Check if it's a 15-minute market
                if "15" in question or "15m" in slug or extract_timestamp_from_slug(slug):
                    # Try to find/create event for this market
                    event_slug = market.get("slug", "").split("-")[0] if "-" in market.get("slug", "") else None
                    if event_slug and "btc-updown-15m" in slug:
                        # Create event-like dict
                        event = {
                            "id": market.get("id"),
                            "slug": slug,
                            "title": market.get("question", ""),
                            "markets": [market],
                        }
                        if event not in all_events:
                            all_events.append(event)
    
    # Filter for BTC updown 15-minute markets
    btc_events = []
    for event in all_events:
        slug = (event.get("slug") or "").lower()
        title = (event.get("title") or "").lower()
        
        # Look for btc-updown-15m pattern in slug
        if "btc-updown-15m" in slug:
            btc_events.append(event)
            logger.info(f"Found BTC 15m event: {event.get('slug')}")
        # Also check title/question for BTC updown pattern
        elif ("bitcoin" in title or "btc" in title) and ("up" in title or "down" in title):
            # Check if it's 15-minute (look for time ranges or 15m)
            if "15" in title or "15m" in slug or extract_timestamp_from_slug(slug):
                btc_events.append(event)
                logger.info(f"Found BTC 15m event by pattern: {event.get('slug')}")
    
    logger.debug(f"Found {len(btc_events)} BTC 15-minute events out of {len(all_events)} total events")
    return btc_events


def get_market_by_event_slug(slug: str) -> Optional[Dict]:
    """
    Fetch a specific market by event slug.
    
    Args:
        slug: Event slug (e.g., 'btc-updown-15m-1767393900')
    
    Returns:
        Market dict or None
    """
    events_url = "https://gamma-api.polymarket.com/events"
    response = httpx.get(events_url, params={"slug": slug, "limit": 1})
    
    if response.status_code == 200:
        events = response.json()
        if events:
            event = events[0]
            markets = event.get("markets", [])
            if markets:
                market = markets[0]
                market["_event_slug"] = event.get("slug")
                market["_event_title"] = event.get("title")
                return market
    
    return None


def get_latest_btc_15m_market() -> Optional[Dict]:
    """
    Get the most recent BTC 15-minute market.
    Tries multiple approaches:
    1. Search events API
    2. Try constructing slug from current timestamp (15-minute intervals)
    
    Returns:
        Market dict for the latest BTC updown 15-minute market, or None
    """
    # Approach 1: Search events API
    events = find_btc_updown_15m_events(limit=200)
    
    if events:
        # Sort by creation date or ID to get latest
        latest_event = max(events, key=lambda e: int(e.get("id", 0)))
        markets = latest_event.get("markets", [])
        if markets:
            market = markets[0]
            market["_event_slug"] = latest_event.get("slug")
            market["_event_title"] = latest_event.get("title")
            return market
    
    # Approach 2: Try constructing slug from current time
    # BTC 15-minute markets are created at 15-minute intervals
    # Slug pattern: btc-updown-15m-{timestamp}
    # Timestamp is usually the start time of the 15-minute window
    
    import time
    current_time = int(time.time())
    
    # Try last few 15-minute intervals (markets might be created slightly before start)
    for offset_minutes in [0, -15, -30, -45, -60]:
        test_timestamp = current_time + (offset_minutes * 60)
        # Round down to nearest 15-minute mark
        test_timestamp = (test_timestamp // 900) * 900
        
        slug = f"btc-updown-15m-{test_timestamp}"
        logger.debug(f"Trying constructed slug: {slug}")
        
        market = get_market_by_event_slug(slug)
        if market:
            logger.info(f"Found market using constructed slug: {slug}")
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

