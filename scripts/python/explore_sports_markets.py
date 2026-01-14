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


def filter_live_markets(markets: List[Dict], max_hours_ahead: int = 4) -> List[Dict]:
    """
    Filter markets to find truly live ones (ending within next few hours).
    
    Args:
        markets: List of market dictionaries
        max_hours_ahead: Maximum hours ahead to consider "live" (default: 4 hours)
        
    Returns:
        List of live market dictionaries
    """
    from datetime import datetime, timezone, timedelta
    from agents.polymarket.btc_market_detector import _parse_datetime_safe
    
    live_markets = []
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
            
            # Check if market ends within the next max_hours_ahead
            time_until_end = end_dt - now_utc
            
            # Market must end in the future (not already ended)
            # and within max_hours_ahead
            if timedelta(0) < time_until_end <= max_time_ahead:
                market["_minutes_until_end"] = time_until_end.total_seconds() / 60.0
                live_markets.append(market)
        except Exception as e:
            logger.debug(f"Error parsing endDate for market {market.get('id')}: {e}")
            continue
    
    # Sort by time until end (soonest first)
    live_markets.sort(key=lambda m: m.get("_minutes_until_end", float('inf')))
    
    return live_markets


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
    
    # Try specific sports topics and filter for truly live markets
    print("\n1. Finding LIVE markets (ending within next 4 hours)...")
    
    all_live_markets = []
    
    for topic in ["nfl", "nba", "nhl", "soccer", "sports"]:
        print(f"\n  Checking {topic.upper()} markets...")
        markets = get_markets_by_topic(topic, limit=100)
        
        if markets:
            # Filter for truly live markets (ending within 4 hours)
            live_markets = filter_live_markets(markets, max_hours_ahead=4)
            all_live_markets.extend(live_markets)
            print(f"  ✓ Found {len(live_markets)} LIVE markets for {topic} (ending within 4 hours)")
    
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
        
        # Try with longer time window
        for topic in ["nfl", "nba", "nhl", "soccer"]:
            markets = get_markets_by_topic(topic, limit=50)
            if markets:
                live_markets = filter_live_markets(markets, max_hours_ahead=24)
                if live_markets:
                    print(f"\n✓ Found {len(live_markets)} markets for {topic} ending within 24 hours:")
                    for market in live_markets[:10]:
                        display_market_info(market, show_details=False)


if __name__ == "__main__":
    main()
