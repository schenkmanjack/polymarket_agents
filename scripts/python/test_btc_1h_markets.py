"""
Test script to find BTC 1-hour markets on Polymarket.
Tries multiple detection methods to see if they exist.
"""
import httpx
import logging
import sys
import os
from typing import List, Optional, Dict
from datetime import datetime, timezone
import re

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def find_btc_1h_via_clob_api() -> List[Dict]:
    """
    Find BTC 1-hour markets using CLOB API directly.
    
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
        
        # Filter for BTC updown 1-hour markets
        btc_markets = []
        for market in markets:
            question = (market.get("question") or "").lower()
            slug = (market.get("slug") or "").lower()
            description = (market.get("description") or "").lower()
            
            # Look for BTC updown pattern
            is_btc = "bitcoin" in question or "btc" in question or "btc" in slug
            is_updown = "up" in question and "down" in question
            
            # Check for 1-hour patterns
            # Also check for "4pm-5pm" or "4-5pm" pattern (hourly windows)
            is_1h = (
                "1h" in slug or "1h" in question or 
                "1 hour" in question or "60m" in slug or 
                "60 min" in question or "60-minute" in question or
                # Check for hourly time ranges like "4pm-5pm" or "4-5pm"
                (re.search(r'\d{1,2}(pm|am)-\d{1,2}(pm|am)', question) or
                 re.search(r'\d{1,2}-\d{1,2}(pm|am)', question))
            )
            
            if is_btc and is_updown and is_1h:
                btc_markets.append(market)
                logger.info(f"Found BTC 1h via CLOB: {market.get('slug', market.get('id'))}")
                logger.info(f"  Question: {market.get('question', 'N/A')}")
        
        logger.info(f"Found {len(btc_markets)} BTC 1-hour markets via CLOB API")
        return btc_markets
        
    except Exception as e:
        logger.error(f"Error fetching from CLOB API: {e}")
        return []


def find_btc_1h_via_gamma_api() -> List[Dict]:
    """
    Find BTC 1-hour markets using Gamma API.
    
    Returns:
        List of market dicts
    """
    markets_url = "https://gamma-api.polymarket.com/markets"
    
    try:
        response = httpx.get(markets_url, params={
            "active": True,
            "limit": 1000,
            "enableOrderBook": True,
        }, timeout=30.0)
        
        if response.status_code != 200:
            logger.warning(f"Gamma API returned {response.status_code}")
            return []
        
        markets = response.json()
        
        # Filter for BTC updown 1-hour markets
        btc_markets = []
        for market in markets:
            question = (market.get("question") or "").lower()
            slug = (market.get("slug") or "").lower()
            
            is_btc = "bitcoin" in question or "btc" in question or "btc" in slug
            is_updown = "up" in question and "down" in question
            
            # Check for 1-hour patterns
            # Also check for "4pm-5pm" or "4-5pm" pattern (hourly windows)
            is_1h = (
                "1h" in slug or "1h" in question or 
                "1 hour" in question or "60m" in slug or 
                "60 min" in question or "60-minute" in question or
                # Check for hourly time ranges like "4pm-5pm" or "4-5pm"
                (re.search(r'\d{1,2}(pm|am)-\d{1,2}(pm|am)', question) or
                 re.search(r'\d{1,2}-\d{1,2}(pm|am)', question))
            )
            
            if is_btc and is_updown and is_1h:
                btc_markets.append(market)
                logger.info(f"Found BTC 1h via Gamma: {market.get('slug', market.get('id'))}")
                logger.info(f"  Question: {market.get('question', 'N/A')}")
        
        logger.info(f"Found {len(btc_markets)} BTC 1-hour markets via Gamma API")
        return btc_markets
        
    except Exception as e:
        logger.error(f"Error fetching from Gamma API: {e}")
        return []


def find_btc_1h_via_events_api() -> List[Dict]:
    """
    Find BTC 1-hour markets via Events API.
    
    Returns:
        List of market dicts
    """
    events_url = "https://gamma-api.polymarket.com/events"
    
    try:
        response = httpx.get(events_url, params={
            "active": True,
            "limit": 500,
        }, timeout=30.0)
        
        if response.status_code != 200:
            logger.warning(f"Events API returned {response.status_code}")
            return []
        
        events = response.json()
        
        btc_markets = []
        for event in events:
            slug = (event.get("slug") or "").lower()
            title = (event.get("title") or "").lower()
            
            # Check for BTC updown 1-hour pattern
            is_btc = "bitcoin" in title or "btc" in title or "btc" in slug
            is_updown = "up" in title and "down" in title or "updown" in slug
            
            # Check for 1-hour patterns
            is_1h = (
                "1h" in slug or "1h" in title or 
                "1 hour" in title or "60m" in slug or 
                "60 min" in title or "60-minute" in title
            )
            
            if is_btc and is_updown and is_1h:
                markets = event.get("markets", [])
                for market in markets:
                    market["_event_slug"] = event.get("slug")
                    market["_event_title"] = event.get("title")
                    btc_markets.append(market)
                    logger.info(f"Found BTC 1h via Events: {event.get('slug')}")
                    logger.info(f"  Title: {event.get('title', 'N/A')}")
        
        logger.info(f"Found {len(btc_markets)} BTC 1-hour markets via Events API")
        return btc_markets
        
    except Exception as e:
        logger.error(f"Error fetching from Events API: {e}")
        return []


def try_slug_patterns() -> List[Dict]:
    """
    Try constructing slugs for BTC 1-hour markets using different patterns.
    Based on actual Polymarket URL: bitcoin-up-or-down-january-8-4pm-et
    
    Returns:
        List of found markets
    """
    from datetime import timedelta
    
    now_utc = datetime.now(timezone.utc)
    found_markets = []
    events_url = "https://gamma-api.polymarket.com/events"
    
    # Pattern 1: Try the actual pattern from Polymarket URL
    # Format: bitcoin-up-or-down-{month}-{day}-{hour}pm-et
    # Example: bitcoin-up-or-down-january-8-4pm-et
    
    # Try current and next few hours
    for hour_offset in [0, 1, 2, -1, -2]:
        test_time = now_utc + timedelta(hours=hour_offset)
        
        # Format: "january-8-4pm-et"
        month_name = test_time.strftime("%B").lower()  # january, february, etc.
        day = test_time.day
        hour_12 = test_time.strftime("%I").lstrip("0")  # 1-12
        am_pm = test_time.strftime("%p").lower()  # am/pm
        
        # Try different formats
        patterns = [
            f"bitcoin-up-or-down-{month_name}-{day}-{hour_12}{am_pm}-et",
            f"bitcoin-up-or-down-{month_name}-{day}-{hour_12}-{am_pm}-et",
            f"btc-up-or-down-{month_name}-{day}-{hour_12}{am_pm}-et",
        ]
        
        for slug in patterns:
            try:
                response = httpx.get(events_url, params={"slug": slug, "limit": 1}, timeout=10.0)
                if response.status_code == 200:
                    events = response.json()
                    if events:
                        event = events[0]
                        markets = event.get("markets", [])
                        if markets:
                            market = markets[0]
                            market["_event_slug"] = event.get("slug")
                            market["_event_title"] = event.get("title")
                            found_markets.append(market)
                            logger.info(f"✓ Found market via slug pattern: {slug}")
                            logger.info(f"  Question: {market.get('question', 'N/A')}")
            except Exception as e:
                logger.debug(f"Error checking slug {slug}: {e}")
    
    # Pattern 2: Also try timestamp-based patterns (like 15m markets)
    current_timestamp = int(now_utc.timestamp())
    window_start_timestamp = (current_timestamp // 3600) * 3600
    
    timestamp_patterns = [
        f"btc-updown-1h-{window_start_timestamp}",
        f"btc-updown-60m-{window_start_timestamp}",
        f"btc-updown-1hour-{window_start_timestamp}",
        f"bitcoin-up-or-down-1h-{window_start_timestamp}",
    ]
    
    for hour_offset in [-1, -2, -3]:
        test_timestamp = window_start_timestamp + (hour_offset * 3600)
        timestamp_patterns.extend([
            f"btc-updown-1h-{test_timestamp}",
            f"btc-updown-60m-{test_timestamp}",
            f"bitcoin-up-or-down-1h-{test_timestamp}",
        ])
    
    for slug in timestamp_patterns:
        try:
            response = httpx.get(events_url, params={"slug": slug, "limit": 1}, timeout=10.0)
            if response.status_code == 200:
                events = response.json()
                if events:
                    event = events[0]
                    markets = event.get("markets", [])
                    if markets:
                        market = markets[0]
                        market["_event_slug"] = event.get("slug")
                        market["_event_title"] = event.get("title")
                        found_markets.append(market)
                        logger.info(f"✓ Found market via timestamp slug: {slug}")
                        logger.info(f"  Question: {market.get('question', 'N/A')}")
        except Exception as e:
            logger.debug(f"Error checking slug {slug}: {e}")
    
    return found_markets


def main():
    """Run all detection methods."""
    logger.info("=" * 80)
    logger.info("Testing BTC 1-Hour Market Detection")
    logger.info("=" * 80)
    logger.info("")
    
    all_markets = {}
    
    # Method 1: CLOB API
    logger.info("Method 1: Searching CLOB API...")
    clob_markets = find_btc_1h_via_clob_api()
    for market in clob_markets:
        market_id = market.get("id")
        if market_id:
            all_markets[market_id] = market
    logger.info("")
    
    # Method 2: Gamma API
    logger.info("Method 2: Searching Gamma API...")
    gamma_markets = find_btc_1h_via_gamma_api()
    for market in gamma_markets:
        market_id = market.get("id")
        if market_id:
            all_markets[market_id] = market
    logger.info("")
    
    # Method 3: Events API
    logger.info("Method 3: Searching Events API...")
    events_markets = find_btc_1h_via_events_api()
    for market in events_markets:
        market_id = market.get("id")
        if market_id:
            all_markets[market_id] = market
    logger.info("")
    
    # Method 4: Slug pattern matching
    logger.info("Method 4: Trying slug patterns (btc-updown-1h-*, btc-updown-60m-*)...")
    slug_markets = try_slug_patterns()
    for market in slug_markets:
        market_id = market.get("id")
        if market_id:
            all_markets[market_id] = market
    logger.info("")
    
    # Summary
    logger.info("=" * 80)
    logger.info("SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total unique BTC 1-hour markets found: {len(all_markets)}")
    logger.info("")
    
    if all_markets:
        logger.info("Found markets:")
        for i, (market_id, market) in enumerate(all_markets.items(), 1):
            logger.info(f"\n{i}. Market ID: {market_id}")
            logger.info(f"   Slug: {market.get('slug', market.get('_event_slug', 'N/A'))}")
            logger.info(f"   Question: {market.get('question', market.get('_event_title', 'N/A'))}")
            logger.info(f"   Active: {market.get('active', 'N/A')}")
            logger.info(f"   Closed: {market.get('closed', 'N/A')}")
            if market.get('startDate'):
                logger.info(f"   Start: {market.get('startDate')}")
            if market.get('endDate'):
                logger.info(f"   End: {market.get('endDate')}")
    else:
        logger.warning("No BTC 1-hour markets found using any method.")
        logger.info("")
        logger.info("Possible reasons:")
        logger.info("  1. BTC 1-hour markets don't exist on Polymarket")
        logger.info("  2. They use a different slug pattern than tested")
        logger.info("  3. They're not currently active")
        logger.info("  4. They're filtered out by the API")


if __name__ == "__main__":
    main()

