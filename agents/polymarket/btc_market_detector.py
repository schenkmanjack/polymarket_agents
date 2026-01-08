"""
Detector for new BTC updown 15-minute and 1-hour markets.
Uses event slug pattern matching since API filtering might hide these markets.
"""
import httpx
import logging
from typing import List, Optional, Dict
from datetime import datetime, timezone, timedelta
import re

logger = logging.getLogger(__name__)


def find_btc_15m_via_clob_api() -> List[Dict]:
    """
    Find BTC 15-minute markets using CLOB API directly.
    This might return markets that Gamma API filters out.
    
    Returns:
        List of market dicts
    """
    clob_url = "https://clob.polymarket.com/markets"
    
    try:
        response = httpx.get(clob_url, params={"limit": 1000}, timeout=30.0)
        if response.status_code != 200:
            logger.warning(f"CLOB API returned {response.status_code}")
            return []
        
        data = response.json()
        markets = data.get("data", [])
        
        # Filter for BTC updown 15-minute markets
        btc_markets = []
        for market in markets:
            # Check various fields for BTC/15m indicators
            question = (market.get("question") or "").lower()
            slug = (market.get("slug") or "").lower()
            description = (market.get("description") or "").lower()
            
            # Look for BTC updown pattern
            is_btc = "bitcoin" in question or "btc" in question or "btc" in slug
            is_updown = "up" in question and "down" in question
            is_15m = "15m" in slug or "15m" in question or "15 min" in question
            
            if is_btc and is_updown and is_15m:
                btc_markets.append(market)
                logger.debug(f"Found BTC 15m via CLOB: {market.get('slug', market.get('id'))}")
        
        logger.info(f"Found {len(btc_markets)} BTC 15-minute markets via CLOB API")
        return btc_markets
        
    except Exception as e:
        logger.error(f"Error fetching from CLOB API: {e}")
        return []


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
                market_id = market.get("id")
                
                # Fetch full market details to ensure we have clobTokenIds
                # Events API might not include all fields
                if market_id:
                    markets_url = "https://gamma-api.polymarket.com/markets"
                    market_response = httpx.get(markets_url, params={"id": market_id, "limit": 1})
                    if market_response.status_code == 200:
                        full_markets = market_response.json()
                        if full_markets:
                            full_market = full_markets[0]
                            # Merge full market data with event data
                            market.update(full_market)
                            logger.debug(f"Fetched full market details for ID={market_id}")
                
                market["_event_slug"] = event.get("slug")
                market["_event_title"] = event.get("title")
                logger.debug(f"Found market via slug {slug}: ID={market.get('id')}, clobTokenIds={market.get('clobTokenIds')}")
                return market
    
    return None


def _parse_datetime_safe(date_str_or_obj) -> Optional[datetime]:
    """Safely parse a datetime string, handling microsecond precision issues."""
    if date_str_or_obj is None:
        return None
    
    try:
        if isinstance(date_str_or_obj, str):
            # Handle microsecond precision issues
            date_str = date_str_or_obj.replace("Z", "+00:00")
            
            # Fix invalid microsecond precision - Python's fromisoformat expects exactly 0-6 digits
            if "." in date_str:
                # Split on decimal point
                parts = date_str.split(".", 1)
                if len(parts) == 2:
                    # Find where timezone starts (+ or -)
                    decimal_part = parts[1]
                    tz_pos = -1
                    for i, char in enumerate(decimal_part):
                        if char in ['+', '-']:
                            tz_pos = i
                            break
                    
                    if tz_pos > 0:
                        # Extract decimal seconds and timezone
                        seconds_part = decimal_part[:tz_pos]
                        tz_part = decimal_part[tz_pos:]
                        
                        # Normalize to 6 digits: pad if less, truncate if more
                        if len(seconds_part) > 6:
                            seconds_part = seconds_part[:6]
                        elif len(seconds_part) < 6:
                            seconds_part = seconds_part.ljust(6, '0')
                        
                        date_str = parts[0] + "." + seconds_part + tz_part
            
            dt = datetime.fromisoformat(date_str)
        else:
            dt = date_str_or_obj
        
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        return dt
    except Exception as e:
        logger.debug(f"Error parsing datetime: {e}, input: {date_str_or_obj}")
        return None


def is_market_currently_running(market: Dict) -> bool:
    """
    Check if market is currently running (between actual start and end times).
    For 15-minute markets, uses timestamp from slug (actual window start).
    For 1-hour markets, uses endDate and calculates start from it.
    
    Args:
        market: Market dict
        
    Returns:
        True if market is currently in its active window
    """
    now_utc = datetime.now(timezone.utc)
    
    # For 15-minute markets, extract actual start time from slug timestamp
    event_slug = market.get("_event_slug", "")
    if event_slug and "btc-updown-15m-" in event_slug:
        # Extract timestamp from slug: btc-updown-15m-{timestamp}
        timestamp = extract_timestamp_from_slug(event_slug)
        if timestamp:
            # Timestamp is the START of the 15-minute window (in UTC)
            start_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            # End is 15 minutes later
            end_dt = start_dt.replace(second=0, microsecond=0) + timedelta(minutes=15)
            
            # Check if we're within the actual window
            if now_utc < start_dt:
                return False  # Market hasn't started yet
            if now_utc >= end_dt:
                return False  # Market has ended
            
            return True
    
    # For 1-hour markets, use endDate and calculate start (1 hour before)
    # Or use startDate if it's actually the market start time
    end_date = market.get("endDate") or market.get("endDateIso")
    if end_date:
        end_dt = _parse_datetime_safe(end_date)
        if end_dt:
            # For 1-hour markets, start is 1 hour before end
            start_dt = end_dt - timedelta(hours=1)
            
            if now_utc < start_dt:
                return False  # Market hasn't started yet
            if now_utc >= end_dt:
                return False  # Market has ended
            
            return True
    
    # Fallback: use startDate/endDate if available
    start_date = market.get("startDate") or market.get("startDateIso")
    if start_date:
        start_dt = _parse_datetime_safe(start_date)
        if start_dt and now_utc < start_dt:
            return False
    
    if end_date:
        end_dt = _parse_datetime_safe(end_date)
        if end_dt and now_utc >= end_dt:
            return False
    
    return True


def is_market_still_trading(market: Dict) -> bool:
    """
    Check if market is still actively trading (not resolved).
    A market can be "currently running" but already resolved (prices at extremes).
    
    Args:
        market: Market dict
        
    Returns:
        True if market appears to still be trading (not resolved)
    """
    try:
        from agents.polymarket.market_finder import get_token_ids_from_market
        import httpx
        
        token_ids = get_token_ids_from_market(market)
        if not token_ids:
            logger.debug("No token IDs found for market")
            return False
        
        # Check orderbook for first token to see if market is resolved
        token_id = token_ids[0]
        clob_url = f"https://clob.polymarket.com/book?token_id={token_id}"
        
        try:
            response = httpx.get(clob_url, timeout=5.0)
        except Exception as e:
            logger.debug(f"HTTP error checking orderbook: {e}")
            # If we can't check, be conservative and assume it's resolved (skip it)
            return False
        
        if response.status_code != 200:
            logger.debug(f"Orderbook API returned {response.status_code}")
            # If we can't check, be conservative and assume it's resolved (skip it)
            return False
        
        try:
            book = response.json()
        except Exception as e:
            logger.debug(f"Error parsing orderbook JSON: {e}")
            return False
        
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        
        if not bids or not asks:
            logger.debug("No bids or asks in orderbook")
            return False
        
        # Handle different orderbook formats
        try:
            if isinstance(bids, list) and len(bids) > 0:
                if isinstance(bids[0], (list, tuple)) and len(bids[0]) >= 2:
                    # Format: [[price, size], ...]
                    best_bid = float(bids[0][0])
                    best_ask = float(asks[0][0]) if asks and isinstance(asks[0], (list, tuple)) and len(asks[0]) >= 2 else None
                elif isinstance(bids[0], dict):
                    # Format: [{"price": "0.01", "size": "..."}, ...] - CLOB API format
                    best_bid = float(bids[0].get("price", 0))
                    best_ask = float(asks[0].get("price", 0)) if asks and isinstance(asks[0], dict) else None
                else:
                    logger.debug(f"Unexpected bids format: {type(bids[0])}")
                    return False
            elif isinstance(bids, dict):
                # Format: {"price": size, ...}
                best_bid = float(max(bids.keys())) if bids else None
                best_ask = float(min(asks.keys())) if asks and isinstance(asks, dict) else None
            else:
                logger.debug(f"Unexpected bids type: {type(bids)}")
                return False
        except (ValueError, TypeError, IndexError, KeyError) as e:
            logger.debug(f"Error extracting prices: {e}")
            return False
        
        if best_bid is None or best_ask is None:
            logger.debug("Could not extract prices from orderbook")
            return False
        
        # Market is resolved if:
        # 1. Ask price is very low (<= 0.02) - YES side won
        # 2. Bid price is very high (>= 0.98) - NO side won  
        # 3. Spread is huge (ask - bid > 0.95) - market resolved
        spread = best_ask - best_bid
        
        if best_ask <= 0.02:
            # Market resolved - YES side won (ask at 0.01)
            logger.debug(f"Market resolved: ask at {best_ask} (YES won)")
            return False
        elif best_bid >= 0.98:
            # Market resolved - NO side won (bid at 0.99)
            logger.debug(f"Market resolved: bid at {best_bid} (NO won)")
            return False
        elif spread > 0.95:
            # Market resolved - huge spread indicates resolution
            logger.debug(f"Market resolved: huge spread {spread} (bid={best_bid}, ask={best_ask})")
            return False
        
        # Market appears to still be trading
        logger.debug(f"Market still trading: bid={best_bid}, ask={best_ask}, spread={spread:.4f}")
        return True
    except Exception as e:
        logger.warning(f"Error checking if market is still trading: {e}", exc_info=True)
        # If we can't check, be conservative and assume it's resolved (skip it)
        return False


def get_latest_btc_15m_market_proactive() -> Optional[Dict]:
    """
    Proactive detection: Prioritize CURRENT window (offset 0), then future windows.
    Only returns markets that are currently running (between startDate and endDate).
    
    Returns:
        Market dict for the CURRENT BTC updown 15-minute market, or None
    """
    import time
    from datetime import datetime, timezone
    
    # Get current UTC time
    now_utc = datetime.now(timezone.utc)
    current_timestamp = int(now_utc.timestamp())
    
    # Round down to nearest 15-minute mark (900 seconds = 15 minutes)
    window_start_timestamp = (current_timestamp // 900) * 900
    
    # PRIORITY: Check CURRENT window first (offset 0), then future windows [+1, +2], then past [-1, -2]
    # This ensures we monitor the market that's actually running RIGHT NOW
    for window_offset in [0, 1, 2, -1, -2]:
        test_timestamp = window_start_timestamp + (window_offset * 900)
        slug = f"btc-updown-15m-{test_timestamp}"
        
        logger.debug(f"Trying constructed slug for window {window_offset}: {slug}")
        market = get_market_by_event_slug(slug)
        
        if market:
            # CRITICAL: Only return markets that are CURRENTLY RUNNING
            # (between startDate and endDate, and we're past startDate)
            if is_market_currently_running(market):
                logger.info(f"Found CURRENTLY RUNNING market: {slug} (window {window_offset})")
                return market
            else:
                logger.debug(f"Market {slug} found but not currently running (may be future or past)")
    
    return None


def get_latest_btc_15m_market() -> Optional[Dict]:
    """
    Get the most recent BTC 15-minute market.
    Tries multiple approaches in order:
    1. CLOB API directly (most reliable, bypasses Gamma filtering)
    2. Search events API
    3. Try constructing slug from current timestamp (15-minute intervals)
    
    Returns:
        Market dict for the latest BTC updown 15-minute market, or None
    """
    # Approach 1: CLOB API directly (bypasses Gamma API filtering)
    clob_markets = find_btc_15m_via_clob_api()
    if clob_markets:
        # Filter for active markets and get latest
        active_markets = [m for m in clob_markets if m.get("active") and not m.get("closed")]
        if active_markets:
            # Sort by ID (higher = newer) or creation time
            latest = max(active_markets, key=lambda m: int(m.get("id", 0)))
            logger.info(f"Found latest BTC 15m market via CLOB API: {latest.get('slug', latest.get('id'))}")
            # Convert to format expected by rest of code
            latest["_event_slug"] = latest.get("slug", f"btc-updown-15m-{latest.get('id')}")
            latest["_event_title"] = latest.get("question", "")
            logger.debug(f"CLOB market data: ID={latest.get('id')}, clobTokenIds={latest.get('clobTokenIds')}")
            return latest
    
    # Approach 2: Search events API
    events = find_btc_updown_15m_events(limit=200)
    
    if events:
        # Sort by creation date or ID to get latest
        latest_event = max(events, key=lambda e: int(e.get("id", 0)))
        markets = latest_event.get("markets", [])
        if markets:
            market = markets[0]
            market["_event_slug"] = latest_event.get("slug")
            market["_event_title"] = latest_event.get("title")
            logger.info(f"Found latest BTC 15m market via Events API: {latest_event.get('slug')}")
            return market
    
    # Approach 3: Calculate current 15-minute window and construct slug
    # BTC 15-minute markets use timestamps that represent the START of the 15-minute window
    # Example: btc-updown-15m-1767555900 = market for 2:45PM-3:00PM ET (timestamp 1767555900 = 2:45PM start)
    
    import time
    from datetime import datetime, timezone
    
    # Get current UTC time
    now_utc = datetime.now(timezone.utc)
    current_timestamp = int(now_utc.timestamp())
    
    # Round down to nearest 15-minute mark (900 seconds = 15 minutes)
    window_start_timestamp = (current_timestamp // 900) * 900
    
    # Try current window and previous 2 windows (markets might be created slightly before start)
    # This covers: current window, previous window, and window before that
    for window_offset in [0, -1, -2]:
        test_timestamp = window_start_timestamp + (window_offset * 900)
        slug = f"btc-updown-15m-{test_timestamp}"
        
        logger.debug(f"Trying constructed slug for window {window_offset}: {slug}")
        market = get_market_by_event_slug(slug)
        
        if market:
            # Verify it's still active
            if is_market_active(market):
                logger.info(f"Found active market using constructed slug: {slug}")
                return market
            else:
                logger.debug(f"Market {slug} found but not active")
    
    return None
    
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


# ============================================================================
# BTC 1-HOUR MARKET DETECTION
# ============================================================================

def get_market_by_slug(slug: str) -> Optional[Dict]:
    """
    Fetch a specific market by slug (works for both 15m and 1h markets).
    
    Args:
        slug: Event slug (e.g., 'bitcoin-up-or-down-january-8-4pm-et')
    
    Returns:
        Market dict or None
    """
    events_url = "https://gamma-api.polymarket.com/events"
    response = httpx.get(events_url, params={"slug": slug, "limit": 1}, timeout=10.0)
    
    if response.status_code == 200:
        events = response.json()
        if events:
            event = events[0]
            markets = event.get("markets", [])
            if markets:
                market = markets[0]
                market_id = market.get("id")
                
                # Fetch full market details to ensure we have clobTokenIds
                if market_id:
                    markets_url = "https://gamma-api.polymarket.com/markets"
                    market_response = httpx.get(markets_url, params={"id": market_id, "limit": 1}, timeout=10.0)
                    if market_response.status_code == 200:
                        full_markets = market_response.json()
                        if full_markets:
                            full_market = full_markets[0]
                            market.update(full_market)
                            logger.debug(f"Fetched full market details for ID={market_id}")
                
                market["_event_slug"] = event.get("slug")
                market["_event_title"] = event.get("title")
                logger.debug(f"Found market via slug {slug}: ID={market.get('id')}, clobTokenIds={market.get('clobTokenIds')}")
                return market
    
    return None


def get_latest_btc_1h_market_proactive() -> Optional[Dict]:
    """
    Proactive detection for BTC 1-hour markets.
    Prioritizes CURRENT hour (offset 0), then future hours.
    Only returns markets that are currently running (between startDate and endDate).
    
    Returns:
        Market dict for the CURRENT BTC updown 1-hour market, or None
    """
    now_utc = datetime.now(timezone.utc)
    
    # PRIORITY: Check CURRENT hour first (offset 0), then future hours [+1, +2]
    # This ensures we monitor the market that's actually running RIGHT NOW
    for hour_offset in [0, 1, 2]:
        test_time = now_utc + timedelta(hours=hour_offset)
        
        # Format: "bitcoin-up-or-down-january-8-4pm-et"
        month_name = test_time.strftime("%B").lower()  # january, february, etc.
        day = test_time.day
        hour_12 = int(test_time.strftime("%I").lstrip("0") or "12")  # 1-12
        am_pm = test_time.strftime("%p").lower()  # am/pm
        
        # Construct slug
        slug = f"bitcoin-up-or-down-{month_name}-{day}-{hour_12}{am_pm}-et"
        
        logger.debug(f"Trying BTC 1h slug for hour {hour_offset}: {slug}")
        market = get_market_by_slug(slug)
        
        if market:
            # CRITICAL: Only return markets that are CURRENTLY RUNNING
            # (between startDate and endDate, and we're past startDate)
            if is_market_currently_running(market):
                logger.info(f"Found CURRENTLY RUNNING BTC 1h market: {slug} (hour {hour_offset})")
                return market
            else:
                logger.debug(f"Market {slug} found but not currently running (may be future or past)")
    
    return None


def get_latest_btc_1h_market() -> Optional[Dict]:
    """
    Get the most recent BTC 1-hour market.
    Tries proactive detection first, then falls back to searching.
    
    Returns:
        Market dict for the latest BTC updown 1-hour market, or None
    """
    # Try proactive detection first
    market = get_latest_btc_1h_market_proactive()
    if market:
        return market
    
    # Fallback: search Events API
    events_url = "https://gamma-api.polymarket.com/events"
    response = httpx.get(events_url, params={"active": True, "limit": 500}, timeout=30.0)
    
    if response.status_code == 200:
        events = response.json()
        
        # Filter for BTC 1-hour markets
        btc_1h_events = []
        for event in events:
            slug = (event.get("slug") or "").lower()
            title = (event.get("title") or "").lower()
            
            # Check for BTC updown 1-hour pattern
            is_btc = "bitcoin" in title or "btc" in title or "btc" in slug
            is_updown = ("up" in title and "down" in title) or "up-or-down" in slug
            
            # Check for 1-hour pattern (hourly time windows like "4pm-5pm" or "4-5pm")
            is_1h = (
                "up-or-down" in slug or
                re.search(r'\d{1,2}(pm|am)-\d{1,2}(pm|am)', title) or
                re.search(r'\d{1,2}-\d{1,2}(pm|am)', title)
            )
            
            if is_btc and is_updown and is_1h:
                btc_1h_events.append(event)
        
        if btc_1h_events:
            # Sort by ID (higher = newer) and get latest
            latest_event = max(btc_1h_events, key=lambda e: int(e.get("id", 0)))
            markets = latest_event.get("markets", [])
            if markets:
                market = markets[0]
                market["_event_slug"] = latest_event.get("slug")
                market["_event_title"] = latest_event.get("title")
                logger.info(f"Found latest BTC 1h market via Events API: {latest_event.get('slug')}")
                return market
    
    return None


def get_all_active_btc_1h_markets() -> List[Dict]:
    """
    Get all active BTC 1-hour markets.
    
    Returns:
        List of market dicts for active BTC updown 1-hour markets
    """
    events_url = "https://gamma-api.polymarket.com/events"
    response = httpx.get(events_url, params={"active": True, "limit": 500}, timeout=30.0)
    
    if response.status_code != 200:
        return []
    
    events = response.json()
    
    all_markets = []
    for event in events:
        slug = (event.get("slug") or "").lower()
        title = (event.get("title") or "").lower()
        
        # Check for BTC updown 1-hour pattern
        is_btc = "bitcoin" in title or "btc" in title or "btc" in slug
        is_updown = ("up" in title and "down" in title) or "up-or-down" in slug
        
        # Check for 1-hour pattern
        is_1h = (
            "up-or-down" in slug or
            re.search(r'\d{1,2}(pm|am)-\d{1,2}(pm|am)', title) or
            re.search(r'\d{1,2}-\d{1,2}(pm|am)', title)
        )
        
        if is_btc and is_updown and is_1h:
            markets = event.get("markets", [])
            for market in markets:
                market["_event_slug"] = event.get("slug")
                market["_event_title"] = event.get("title")
                all_markets.append(market)
    
    return all_markets

