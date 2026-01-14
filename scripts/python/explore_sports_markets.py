"""
Explore Polymarket sports markets for potential market making.

This script queries the Polymarket Gamma API to find live sports markets
that could be used for market making strategies.
"""
import sys
import os
import logging
from typing import List, Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

# Configure proxy BEFORE importing modules that use httpx/requests
from agents.utils.proxy_config import configure_proxy, get_proxy
configure_proxy(auto_detect=True)
proxy_url = get_proxy()
if proxy_url:
    os.environ['HTTPS_PROXY'] = proxy_url
    os.environ['HTTP_PROXY'] = proxy_url
    print(f"✓ Using proxy: {proxy_url.split('@')[1] if '@' in proxy_url else 'configured'}")

import httpx
from agents.utils.proxy_config import get_proxy_dict
from agents.polymarket.gamma import GammaMarketClient
from agents.polymarket.market_finder import get_token_ids_from_market

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_sports_markets(limit: int = 50, active_only: bool = True) -> List[Dict]:
    """
    Fetch live sports markets from Polymarket.
    
    Args:
        limit: Maximum number of markets to fetch
        active_only: Only return active markets
        
    Returns:
        List of market dictionaries
    """
    gamma = GammaMarketClient()
    proxies = get_proxy_dict()
    
    params = {
        "active": active_only,
        "closed": False,
        "archived": False,
        "limit": limit,
        "enableOrderBook": True,  # Only markets with orderbooks (tradable)
    }
    
    # Try filtering by topic/category - Polymarket API may support these
    # Common topics: "sports", "nfl", "nba", "nhl", "crypto", "politics", etc.
    # Note: These parameters may vary - checking what works
    
    print(f"🔍 Fetching sports markets with params: {params}")
    
    try:
        markets = gamma.get_markets(querystring_params=params, parse_pydantic=False)
        print(f"✓ Retrieved {len(markets)} markets")
        
        # Filter for sports-related markets by checking tags, category, or question text
        sports_markets = []
        for market in markets:
            # Check various fields that might indicate sports
            question = market.get("question", "").lower()
            description = market.get("description", "").lower()
            tags = market.get("tags", [])
            category = market.get("category", "").lower()
            
            # Keywords that suggest sports markets
            sports_keywords = [
                "nfl", "nba", "nhl", "mlb", "ncaa", "cbb", "cbb", "cfb",
                "soccer", "football", "basketball", "hockey", "baseball",
                "tennis", "golf", "ufc", "boxing", "cricket", "rugby",
                "score", "points", "win", "lose", "game", "match", "playoff",
                "championship", "super bowl", "world cup", "stanley cup"
            ]
            
            is_sports = False
            # Check if any sports keywords appear in question or description
            for keyword in sports_keywords:
                if keyword in question or keyword in description:
                    is_sports = True
                    break
            
            # Check tags
            if tags:
                tag_names = [tag.get("name", "").lower() if isinstance(tag, dict) else str(tag).lower() for tag in tags]
                for tag_name in tag_names:
                    if any(keyword in tag_name for keyword in sports_keywords):
                        is_sports = True
                        break
            
            # Check category
            if "sport" in category:
                is_sports = True
            
            if is_sports:
                sports_markets.append(market)
        
        print(f"✓ Found {len(sports_markets)} sports-related markets")
        return sports_markets
        
    except Exception as e:
        logger.error(f"Error fetching markets: {e}", exc_info=True)
        return []


def get_markets_by_topic(topic: str, limit: int = 50) -> List[Dict]:
    """
    Try to fetch markets filtered by topic (if API supports it).
    
    Args:
        topic: Topic name (e.g., "sports", "nfl", "nba")
        limit: Maximum number of markets
        
    Returns:
        List of market dictionaries
    """
    gamma = GammaMarketClient()
    proxies = get_proxy_dict()
    
    # Try different parameter names that might work
    param_variations = [
        {"topic": topic, "active": True, "limit": limit, "enableOrderBook": True},
        {"category": topic, "active": True, "limit": limit, "enableOrderBook": True},
        {"tags": topic, "active": True, "limit": limit, "enableOrderBook": True},
    ]
    
    for params in param_variations:
        try:
            print(f"🔍 Trying params: {params}")
            markets = gamma.get_markets(querystring_params=params, parse_pydantic=False)
            if markets:
                print(f"✓ Found {len(markets)} markets with topic={topic}")
                return markets
        except Exception as e:
            logger.debug(f"Params {params} didn't work: {e}")
            continue
    
    print(f"⚠️ Could not filter by topic '{topic}' - API may not support this parameter")
    return []


def get_all_live_markets(limit: int = 200) -> List[Dict]:
    """
    Get all live markets (not filtered by topic) to find what's actually live.
    
    Args:
        limit: Maximum number of markets
        
    Returns:
        List of market dictionaries
    """
    gamma = GammaMarketClient()
    
    # Try different approaches to find truly live markets
    param_variations = [
        # Approach 1: Standard active/closed filters
        {"active": True, "closed": False, "archived": False, "limit": limit, "enableOrderBook": True},
        # Approach 2: Try without active filter
        {"closed": False, "archived": False, "limit": limit, "enableOrderBook": True},
        # Approach 3: Try with resolved=false if it exists
        {"closed": False, "resolved": False, "limit": limit, "enableOrderBook": True},
    ]
    
    all_markets = []
    seen_ids = set()
    
    for params in param_variations:
        try:
            print(f"🔍 Trying params: {params}")
            markets = gamma.get_markets(querystring_params=params, parse_pydantic=False)
            if markets:
                print(f"✓ Found {len(markets)} markets")
                for market in markets:
                    market_id = market.get("id")
                    if market_id and market_id not in seen_ids:
                        seen_ids.add(market_id)
                        all_markets.append(market)
        except Exception as e:
            logger.debug(f"Params {params} didn't work: {e}")
            continue
    
    return all_markets


def filter_live_markets(markets: List[Dict], max_hours_ahead: int = 4) -> tuple:
    """
    Filter markets to find truly live ones (ending within next few hours).
    
    Args:
        markets: List of market dictionaries
        max_hours_ahead: Maximum hours ahead to consider "live" (default: 4 hours)
        
    Returns:
        Tuple of (live_markets, future_markets) where:
        - live_markets: Markets ending within max_hours_ahead
        - future_markets: Markets ending beyond max_hours_ahead but in the future
    """
    from datetime import datetime, timezone, timedelta
    from agents.polymarket.btc_market_detector import _parse_datetime_safe
    
    live_markets = []
    future_markets = []  # Markets ending in the future (beyond max_hours_ahead)
    now_utc = datetime.now(timezone.utc)
    max_time_ahead = timedelta(hours=max_hours_ahead)
    
    for market in markets:
        end_date = market.get("endDate") or market.get("endDateIso")
        if not end_date:
            continue
        
        try:
            end_dt = _parse_datetime_safe(end_date)
            if not end_dt:
                continue
            
            # Check if market ends in the future
            time_until_end = end_dt - now_utc
            
            # Market must end in the future (not already ended)
            if time_until_end > timedelta(0):
                market["_minutes_until_end"] = time_until_end.total_seconds() / 60.0
                market["_hours_until_end"] = time_until_end.total_seconds() / 3600.0
                
                # Check if within max_hours_ahead
                if time_until_end <= max_time_ahead:
                    live_markets.append(market)
                else:
                    # Store for potential display
                    future_markets.append(market)
        except Exception as e:
            logger.debug(f"Error parsing endDate for market {market.get('id')}: {e}")
            continue
    
    # Sort by time until end (soonest first)
    live_markets.sort(key=lambda m: m.get("_minutes_until_end", float('inf')))
    future_markets.sort(key=lambda m: m.get("_minutes_until_end", float('inf')))
    
    return live_markets, future_markets


def display_market_info(market: Dict, show_details: bool = True):
    """Display key information about a market."""
    market_id = market.get("id")
    question = market.get("question", "N/A")
    outcomes = market.get("outcomes", [])
    
    # Show time until end if available
    minutes_until_end = market.get("_minutes_until_end")
    if minutes_until_end is not None:
        hours = int(minutes_until_end // 60)
        mins = int(minutes_until_end % 60)
        time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
        print(f"[{time_str} until end] {question}")
    else:
        print(f"{question}")
    
    if not show_details:
        return
    
    # Handle liquidity and volume - might be string or number
    liquidity = market.get("liquidity", 0)
    try:
        liquidity = float(liquidity) if liquidity else 0.0
    except (ValueError, TypeError):
        liquidity = 0.0
    
    volume = market.get("volume", 0)
    try:
        volume = float(volume) if volume else 0.0
    except (ValueError, TypeError):
        volume = 0.0
    
    # Get token IDs if available
    token_ids = get_token_ids_from_market(market)
    
    print(f"\n{'='*80}")
    print(f"Market ID: {market_id}")
    print(f"Question: {question}")
    print(f"Outcomes: {outcomes}")
    print(f"Liquidity: ${liquidity:,.2f}")
    print(f"Volume: ${volume:,.2f}")
    if token_ids and len(token_ids) >= 2:
        print(f"Token IDs: YES={token_ids[0][:20]}..., NO={token_ids[1][:20]}...")
    
    # Check if market is active
    active = market.get("active", False)
    closed = market.get("closed", False)
    print(f"Status: {'Active' if active and not closed else 'Inactive/Closed'}")


def main():
    """Main function to explore sports markets."""
    print("="*80)
    print("POLYMARKET LIVE SPORTS MARKETS EXPLORER")
    print("="*80)
    
    # First, try getting ALL live markets to see what's actually live
    print("\n0. Getting ALL live markets (not filtered by topic)...")
    all_markets = get_all_live_markets(limit=500)
    
    if all_markets:
        print(f"\n✓ Found {len(all_markets)} total markets")
        
        # Filter for markets ending in the future
        from datetime import datetime, timezone, timedelta
        from agents.polymarket.btc_market_detector import _parse_datetime_safe
        
        now_utc = datetime.now(timezone.utc)
        future_markets = []
        
        for market in all_markets:
            end_date = market.get("endDate") or market.get("endDateIso")
            if not end_date:
                continue
            
            try:
                end_dt = _parse_datetime_safe(end_date)
                if end_dt:
                    time_until_end = end_dt - now_utc
                    if time_until_end > timedelta(0):
                        market["_hours_until_end"] = time_until_end.total_seconds() / 3600.0
                        market["_minutes_until_end"] = time_until_end.total_seconds() / 60.0
                        future_markets.append(market)
            except Exception as e:
                continue
        
        # Sort by time until end
        future_markets.sort(key=lambda m: m.get("_minutes_until_end", float('inf')))
        
        print(f"✓ Found {len(future_markets)} markets ending in the future")
        
        # Filter for sports-related markets
        sports_keywords = [
            "nfl", "nba", "nhl", "mlb", "ncaa", "cbb", "cfb",
            "soccer", "football", "basketball", "hockey", "baseball",
            "tennis", "golf", "ufc", "boxing", "cricket", "rugby",
            "score", "points", "win", "lose", "game", "match", "playoff",
            "championship", "super bowl", "world cup", "stanley cup",
            "bills", "chiefs", "lakers", "warriors", "celtics", "heat",
            "cowboys", "packers", "patriots", "steelers"
        ]
        
        sports_future = []
        for market in future_markets:
            question = market.get("question", "").lower()
            description = market.get("description", "").lower()
            
            for keyword in sports_keywords:
                if keyword in question or keyword in description:
                    sports_future.append(market)
                    break
        
        print(f"✓ Found {len(sports_future)} SPORTS markets ending in the future")
        
        if sports_future:
            print("\n" + "="*80)
            print("LIVE SPORTS MARKET TITLES (sorted by time until end):")
            print("="*80)
            
            for market in sports_future[:50]:  # Show first 50
                hours = market.get("_hours_until_end", 0)
                days = int(hours // 24)
                hrs = int(hours % 24)
                if days > 0:
                    time_str = f"{days}d {hrs}h"
                elif hrs > 0:
                    time_str = f"{hrs}h"
                else:
                    mins = int(market.get("_minutes_until_end", 0))
                    time_str = f"{mins}m"
                
                question = market.get("question", "N/A")
                market_id = market.get("id")
                liquidity = market.get("liquidity", 0)
                try:
                    liquidity = float(liquidity) if liquidity else 0.0
                except (ValueError, TypeError):
                    liquidity = 0.0
                
                print(f"[{time_str:>8}] [{market_id}] ${liquidity:>10,.0f} - {question}")
        else:
            print("\n⚠️ No sports markets found ending in the future")
    
    # Try specific sports topics and filter for truly live markets
    print("\n" + "="*80)
    print("1. Finding LIVE markets by topic (ending within next 4 hours)...")
    print("="*80)
    
    all_live_markets = []
    
    for topic in ["nfl", "nba", "nhl", "soccer", "sports"]:
        print(f"\n  Checking {topic.upper()} markets...")
        markets = get_markets_by_topic(topic, limit=100)
        
        if markets:
            # Filter for truly live markets (ending within 4 hours)
            live_markets, future_markets = filter_live_markets(markets, max_hours_ahead=4)
            all_live_markets.extend(live_markets)
            print(f"  ✓ Found {len(live_markets)} LIVE markets for {topic} (ending within 4 hours)")
            if future_markets:
                print(f"  ℹ Found {len(future_markets)} future markets (ending beyond 4 hours)")
    
    if all_live_markets:
        # Remove duplicates (same market might appear in multiple topics)
        seen_ids = set()
        unique_live = []
        for market in all_live_markets:
            market_id = market.get("id")
            if market_id not in seen_ids:
                seen_ids.add(market_id)
                unique_live.append(market)
        
        print(f"\n{'='*80}")
        print(f"✓ Found {len(unique_live)} UNIQUE LIVE markets (ending within 4 hours)")
        print(f"{'='*80}")
        print("\nLIVE MARKET TITLES:")
        print("-" * 80)
        
        for i, market in enumerate(unique_live[:30], 1):  # Show up to 30 live markets
            display_market_info(market, show_details=False)
        
        if len(unique_live) > 30:
            print(f"\n... and {len(unique_live) - 30} more live markets")
        
        # Show detailed info for first 5
        print(f"\n{'='*80}")
        print("DETAILED INFO FOR FIRST 5 LIVE MARKETS:")
        print("="*80)
        for i, market in enumerate(unique_live[:5], 1):
            display_market_info(market, show_details=True)
    else:
        print("\n⚠️ No live markets found (ending within 4 hours)")
        print("\nTrying to find markets ending within 24 hours...")
        
        # Try with longer time window and show future markets
        print("\n" + "="*80)
        print("2. Finding markets ending within 24 hours...")
        print("="*80)
        
        all_future_24h = []
        for topic in ["nfl", "nba", "nhl", "soccer"]:
            markets = get_markets_by_topic(topic, limit=100)
            if markets:
                live_markets, future_markets = filter_live_markets(markets, max_hours_ahead=24)
                all_future_24h.extend(live_markets)
                if live_markets:
                    print(f"\n✓ Found {len(live_markets)} markets for {topic} ending within 24 hours:")
                    for market in live_markets[:15]:
                        display_market_info(market, show_details=False)
        
        if all_future_24h:
            # Remove duplicates
            seen_ids = set()
            unique_24h = []
            for market in all_future_24h:
                market_id = market.get("id")
                if market_id not in seen_ids:
                    seen_ids.add(market_id)
                    unique_24h.append(market)
            
            print(f"\n{'='*80}")
            print(f"✓ Found {len(unique_24h)} UNIQUE markets ending within 24 hours")
            print(f"{'='*80}")
            print("\nMARKET TITLES (ending within 24 hours):")
            print("-" * 80)
            for market in unique_24h[:30]:
                display_market_info(market, show_details=False)
        else:
            print("\n⚠️ No markets found ending within 24 hours")
            print("\nChecking for ANY future markets (regardless of time)...")
            
            # Show markets with future endDates regardless of time
            print("\n" + "="*80)
            print("3. Finding ANY future markets (regardless of time)...")
            print("="*80)
            
            all_future_markets = []
            for topic in ["nfl", "nba", "nhl", "soccer"]:
                markets = get_markets_by_topic(topic, limit=100)
                if markets:
                    _, future_markets = filter_live_markets(markets, max_hours_ahead=8760)  # 1 year
                    all_future_markets.extend(future_markets)
                    if future_markets:
                        print(f"\n{topic.upper()} - Found {len(future_markets)} future markets")
            
            if all_future_markets:
                # Remove duplicates and sort by time until end
                seen_ids = set()
                unique_future = []
                for market in all_future_markets:
                    market_id = market.get("id")
                    if market_id not in seen_ids:
                        seen_ids.add(market_id)
                        unique_future.append(market)
                
                unique_future.sort(key=lambda m: m.get("_minutes_until_end", float('inf')))
                
                print(f"\n✓ Found {len(unique_future)} UNIQUE future markets")
                print("\nFUTURE MARKET TITLES (sorted by time until end):")
                print("-" * 80)
                
                for market in unique_future[:50]:  # Show first 50
                    hours = market.get("_hours_until_end", 0)
                    days = int(hours // 24)
                    hrs = int(hours % 24)
                    if days > 0:
                        time_str = f"{days}d {hrs}h"
                    else:
                        time_str = f"{hrs}h"
                    question = market.get("question", "N/A")
                    print(f"[{time_str:>8}] {question}")
            else:
                print("\n⚠️ No future markets found")
                print("\nDebug: Showing sample of markets returned by API (first 20):")
                print("-" * 80)
                for topic in ["nfl", "nba"]:
                    markets = get_markets_by_topic(topic, limit=20)
                    if markets:
                        print(f"\n{topic.upper()} - Sample markets:")
                        for market in markets[:10]:
                            question = market.get("question", "N/A")
                            end_date = market.get("endDate") or market.get("endDateIso")
                            market_id = market.get("id")
                            print(f"  [{market_id}] {question[:70]}")
                            print(f"      endDate: {end_date}")


if __name__ == "__main__":
    main()
