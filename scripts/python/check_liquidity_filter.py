"""
Quick script to check what markets are returned with $100k liquidity threshold.
"""
import sys
import os
import logging
from datetime import datetime, timezone, timedelta

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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_datetime_safe(date_str_or_obj):
    """Safely parse datetime string."""
    from datetime import datetime, timezone
    if isinstance(date_str_or_obj, datetime):
        return date_str_or_obj
    if not date_str_or_obj:
        return None
    try:
        # Try ISO format first
        if isinstance(date_str_or_obj, str):
            # Remove timezone info if present and parse
            date_str = date_str_or_obj.replace('Z', '+00:00')
            return datetime.fromisoformat(date_str)
        return None
    except Exception:
        return None


def main():
    """Check what markets are returned with $100k liquidity threshold."""
    print("=" * 80)
    print("CHECKING MARKETS WITH $100,000 LIQUIDITY THRESHOLD")
    print("=" * 80)
    print()
    
    gamma_url = "https://gamma-api.polymarket.com"
    markets_endpoint = f"{gamma_url}/markets"
    min_liquidity = 100000.0
    
    print(f"Querying live sports markets with min_liquidity = ${min_liquidity:,.2f}")
    print()
    
    proxies = get_proxy_dict()
    
    # Get markets with liquidity filtering
    try:
        all_markets = []
        seen_ids = set()
        
        topics = ["nfl", "nba", "nhl", "soccer", "sports", "esports"]
        
        # Filter for markets ending within next 6 hours (truly "live")
        now_utc = datetime.now(timezone.utc)
        max_end_time = now_utc + timedelta(hours=6)
        end_date_min = now_utc.isoformat()
        end_date_max = max_end_time.isoformat()
        
        for topic in topics:
            params = {
                "topic": topic,
                "active": True,
                "closed": False,
                "archived": False,
                "limit": 100,
                "enableOrderBook": True,
                "liquidity_num_min": min_liquidity,
                "end_date_min": end_date_min,  # Markets ending after now
                "end_date_max": end_date_max,  # Markets ending within 6 hours
            }
            
            try:
                request_kwargs = {"timeout": 30.0}
                if proxies:
                    request_kwargs.update(proxies)
                response = httpx.get(markets_endpoint, params=params, **request_kwargs)
                if response.status_code == 200:
                    markets = response.json()
                    for market in markets:
                        market_id = market.get("id")
                        if market_id and market_id not in seen_ids:
                            seen_ids.add(market_id)
                            all_markets.append(market)
                    print(f"  {topic.upper()}: Found {len(markets)} markets")
                else:
                    print(f"  {topic.upper()}: HTTP {response.status_code}")
            except Exception as e:
                print(f"  {topic.upper()}: Error - {e}")
        
        markets = all_markets
        
        print(f"✓ Found {len(markets)} markets meeting liquidity threshold")
        print()
        
        if not markets:
            print("⚠️  No markets found with liquidity >= $100,000")
            print()
            print("Trying with lower threshold ($10,000) to see what's available...")
            markets_lower = []
            seen_ids_lower = set()
            
            for topic in topics:
                params = {
                    "topic": topic,
                    "active": True,
                    "closed": False,
                    "archived": False,
                    "limit": 100,
                    "enableOrderBook": True,
                    "liquidity_num_min": 10000.0,
                }
                
                try:
                    request_kwargs = {"timeout": 30.0}
                    if proxies:
                        request_kwargs.update(proxies)
                    response = httpx.get(markets_endpoint, params=params, **request_kwargs)
                    if response.status_code == 200:
                        topic_markets = response.json()
                        for market in topic_markets:
                            market_id = market.get("id")
                            if market_id and market_id not in seen_ids_lower:
                                seen_ids_lower.add(market_id)
                                markets_lower.append(market)
                except Exception:
                    continue
            print(f"✓ Found {len(markets_lower)} markets with liquidity >= $10,000")
            
            if markets_lower:
                print("\nSample markets (with liquidity >= $10k):")
                print("-" * 80)
                for i, market in enumerate(markets_lower[:10], 1):
                    market_id = market.get("id")
                    question = market.get("question", "N/A")
                    liquidity = market.get("liquidity", 0)
                    try:
                        liquidity = float(liquidity) if liquidity else 0.0
                    except (ValueError, TypeError):
                        liquidity = 0.0
                    
                    end_date = market.get("endDate") or market.get("endDateIso")
                    time_str = "N/A"
                    if end_date:
                        end_dt = parse_datetime_safe(end_date)
                        if end_dt:
                            now_utc = datetime.now(timezone.utc)
                            time_until_end = end_dt - now_utc
                            if time_until_end > timedelta(0):
                                hours = time_until_end.total_seconds() / 3600.0
                                if hours < 24:
                                    time_str = f"{hours:.1f}h"
                                else:
                                    days = hours / 24
                                    time_str = f"{days:.1f}d"
                    
                    print(f"{i}. [{market_id}] ${liquidity:>10,.0f} | {time_str:>6} | {question[:60]}...")
            
            return
        
        # Filter for truly "live" markets (ending within next 24 hours)
        now_utc = datetime.now(timezone.utc)
        live_markets = []
        future_markets = []
        
        for market in markets:
            end_date = market.get("endDate") or market.get("endDateIso")
            if end_date:
                end_dt = parse_datetime_safe(end_date)
                if end_dt:
                    time_until_end = end_dt - now_utc
                    if time_until_end > timedelta(0):
                        hours_until_end = time_until_end.total_seconds() / 3600.0
                        market["_hours_until_end"] = hours_until_end
                        if hours_until_end <= 24:
                            live_markets.append(market)
                        else:
                            future_markets.append(market)
        
        print(f"\n📊 BREAKDOWN:")
        print(f"  Live markets (ending within 24h): {len(live_markets)}")
        print(f"  Future markets (beyond 24h): {len(future_markets)}")
        print()
        
        # Show markets found
        if live_markets:
            print("=" * 80)
            print("LIVE MARKETS (ending within 24 hours, liquidity >= $100,000):")
            print("=" * 80)
            # Sort by time until end (soonest first)
            live_markets_sorted = sorted(
                live_markets,
                key=lambda m: m.get("_hours_until_end", float('inf'))
            )
        else:
            live_markets_sorted = []
        
        # Sort by liquidity (descending) for display
        markets_sorted = sorted(
            markets,
            key=lambda m: float(m.get("liquidity", 0) or 0),
            reverse=True
        )
        
        now_utc = datetime.now(timezone.utc)
        
        for i, market in enumerate(markets_sorted[:20], 1):  # Show top 20
            market_id = market.get("id")
            question = market.get("question", "N/A")
            liquidity = market.get("liquidity", 0)
            try:
                liquidity = float(liquidity) if liquidity else 0.0
            except (ValueError, TypeError):
                liquidity = 0.0
            
            # Get time until end
            end_date = market.get("endDate") or market.get("endDateIso")
            time_str = "N/A"
            if end_date:
                end_dt = parse_datetime_safe(end_date)
                if end_dt:
                    time_until_end = end_dt - now_utc
                    if time_until_end > timedelta(0):
                        hours = time_until_end.total_seconds() / 3600.0
                        if hours < 24:
                            time_str = f"{hours:.1f}h"
                        else:
                            days = hours / 24
                            time_str = f"{days:.1f}d"
                    else:
                        time_str = "ENDED"
            
            # Get topic/category
            tags = market.get("tags", [])
            topic = "unknown"
            if tags:
                tag_names = [tag.get("name", "") if isinstance(tag, dict) else str(tag) for tag in tags]
                sports_tags = [t for t in tag_names if t.lower() in ["nfl", "nba", "nhl", "soccer", "sports"]]
                if sports_tags:
                    topic = sports_tags[0].upper()
            
            print(f"{i:2d}. [{topic:>4}] [{market_id:>8}] ${liquidity:>12,.0f} | {time_str:>6} | {question[:55]}...")
        
        if live_markets_sorted:
            print()
            print("=" * 80)
            print("TOP LIVE MARKETS (ending within 24 hours):")
            print("=" * 80)
            for i, market in enumerate(live_markets_sorted[:15], 1):
                market_id = market.get("id")
                question = market.get("question", "N/A")
                liquidity = float(market.get("liquidity", 0) or 0)
                hours = market.get("_hours_until_end", 0)
                mins = int((hours % 1) * 60)
                hrs = int(hours)
                time_str = f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m"
                
                print(f"{i:2d}. [{market_id:>8}] ${liquidity:>12,.0f} | {time_str:>6} | {question[:55]}...")
            
            if len(live_markets_sorted) > 15:
                print(f"\n... and {len(live_markets_sorted) - 15} more live markets")
        
        if len(markets_sorted) > 20:
            print(f"\n... and {len(markets_sorted) - 20} more total markets")
        
        # Summary statistics
        print()
        print("=" * 80)
        print("SUMMARY STATISTICS")
        print("=" * 80)
        
        liquidities = [float(m.get("liquidity", 0) or 0) for m in markets_sorted]
        if liquidities:
            print(f"Total markets: {len(markets_sorted)}")
            print(f"Min liquidity: ${min(liquidities):,.2f}")
            print(f"Max liquidity: ${max(liquidities):,.2f}")
            print(f"Avg liquidity: ${sum(liquidities) / len(liquidities):,.2f}")
            print(f"Median liquidity: ${sorted(liquidities)[len(liquidities) // 2]:,.2f}")
        
        # Group by topic
        print()
        print("MARKETS BY TOPIC:")
        print("-" * 80)
        topic_counts = {}
        for market in markets_sorted:
            tags = market.get("tags", [])
            if tags:
                tag_names = [tag.get("name", "") if isinstance(tag, dict) else str(tag) for tag in tags]
                sports_tags = [t for t in tag_names if t.lower() in ["nfl", "nba", "nhl", "soccer", "sports"]]
                if sports_tags:
                    topic = sports_tags[0].upper()
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1
                else:
                    topic_counts["OTHER"] = topic_counts.get("OTHER", 0) + 1
            else:
                topic_counts["UNKNOWN"] = topic_counts.get("UNKNOWN", 0) + 1
        
        for topic, count in sorted(topic_counts.items()):
            print(f"  {topic}: {count} markets")
        
    except Exception as e:
        logger.error(f"Error checking markets: {e}", exc_info=True)
        print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    main()
