"""
Direct test of Polymarket API for sports markets.

Tests if we can fetch sports markets from the API without full dependencies.
"""
import sys
import os
import logging
import httpx
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

# Configure proxy BEFORE importing modules
from agents.utils.proxy_config import configure_proxy, get_proxy_dict
configure_proxy(auto_detect=True)
proxy_dict = get_proxy_dict()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_datetime_safe(date_str_or_obj):
    """Safely parse datetime string."""
    if date_str_or_obj is None:
        return None
    
    try:
        if isinstance(date_str_or_obj, str):
            date_str = date_str_or_obj.replace("Z", "+00:00")
            dt = datetime.fromisoformat(date_str)
        else:
            dt = date_str_or_obj
        
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        return dt
    except Exception as e:
        logger.debug(f"Error parsing datetime: {e}")
        return None


def test_sports_api():
    """Test fetching sports markets from Polymarket API."""
    print("=" * 80)
    print("TESTING POLYMARKET SPORTS MARKETS API")
    print("=" * 80)
    print()
    
    gamma_url = "https://gamma-api.polymarket.com"
    markets_endpoint = f"{gamma_url}/markets"
    
    topics = ["nfl", "nba", "nhl", "soccer"]
    now_utc = datetime.now(timezone.utc)
    
    all_markets = []
    
    for topic in topics:
        print(f"Fetching {topic.upper()} markets...")
        
        params = {
            "topic": topic,
            "active": True,
            "closed": False,
            "archived": False,
            "limit": 100,
            "enableOrderBook": True,
        }
        
        try:
            # Use httpx.get directly (proxy is set via environment variables)
            response = httpx.get(markets_endpoint, params=params, timeout=30.0)
            
            if response.status_code != 200:
                print(f"  ❌ Error: HTTP {response.status_code}")
                continue
            
            markets = response.json()
            print(f"  ✓ Found {len(markets)} active markets")
            
            # Filter for markets with good liquidity
            qualifying = []
            for market in markets:
                market_id = market.get("id")
                question = market.get("question", "N/A")
                
                # Check liquidity
                liquidity = market.get("liquidity", 0)
                try:
                    liquidity = float(liquidity) if liquidity else 0.0
                except (ValueError, TypeError):
                    liquidity = 0.0
                
                if liquidity < 10000.0:
                    if len(qualifying) == 0:  # Only show first few for debugging
                        print(f"    Skipping {market_id}: liquidity ${liquidity:,.2f} < $10K")
                    continue
                
                # Check start date
                start_date = market.get("startDate") or market.get("startDateIso")
                end_date = market.get("endDate") or market.get("endDateIso")
                
                has_started = False
                start_dt = None
                
                if start_date:
                    start_dt = parse_datetime_safe(start_date)
                    if start_dt:
                        buffer = timedelta(minutes=5)
                        has_started = now_utc >= (start_dt + buffer)
                
                # Estimate from end date if no start date
                if not start_dt and end_date:
                    end_dt = parse_datetime_safe(end_date)
                    if end_dt:
                        # Estimate: NFL=3h, NBA/NHL=2.5h, Soccer=2h
                        duration_hours = 3.0 if topic == "nfl" else (2.5 if topic in ["nba", "nhl"] else 2.0)
                        estimated_start = end_dt - timedelta(hours=duration_hours)
                        buffer = timedelta(minutes=5)
                        has_started = now_utc >= (estimated_start + buffer)
                        start_dt = estimated_start
                
                if not has_started:
                    if len(qualifying) == 0:  # Only show first few for debugging
                        start_str = start_dt.isoformat() if start_dt else "unknown"
                        print(f"    Skipping {market_id}: game not started yet (start: {start_str})")
                    continue
                
                # Check time until resolution
                minutes_until_resolution = None
                if end_date:
                    end_dt = parse_datetime_safe(end_date)
                    if end_dt:
                        minutes_until_resolution = (end_dt - now_utc).total_seconds() / 60.0
                        if minutes_until_resolution < 0 or minutes_until_resolution > 240:
                            if len(qualifying) == 0:  # Only show first few for debugging
                                print(f"    Skipping {market_id}: resolution in {minutes_until_resolution:.1f} min (outside 0-240m window)")
                            continue  # Too far in past or future
                
                qualifying.append({
                    "market_id": str(market_id),
                    "question": question,
                    "topic": topic,
                    "liquidity": liquidity,
                    "start_datetime": start_dt,
                    "minutes_until_resolution": minutes_until_resolution,
                    "market": market,
                })
            
            print(f"  ✓ {len(qualifying)} markets qualify (game started, liquidity >= $10K)")
            all_markets.extend(qualifying)
            
        except Exception as e:
            print(f"  ❌ Error fetching {topic}: {e}")
            import traceback
            traceback.print_exc()
    
    print()
    print("=" * 80)
    print(f"TOTAL RESULTS: {len(all_markets)} qualifying markets found")
    print("=" * 80)
    print()
    
    if not all_markets:
        print("⚠️  No qualifying markets found.")
        print("\nPossible reasons:")
        print("  - No games currently happening")
        print("  - Games haven't started yet (need 5 min buffer)")
        print("  - Markets don't meet liquidity threshold ($10K)")
        print("  - Markets ending too far in future (>4 hours)")
        return
    
    # Show all qualifying markets
    print("QUALIFYING MARKETS:")
    print("-" * 80)
    
    for i, mkt in enumerate(all_markets, 1):
        print(f"\n{i}. [{mkt['topic'].upper()}] Market ID: {mkt['market_id']}")
        print(f"   Question: {mkt['question'][:70]}...")
        print(f"   Liquidity: ${mkt['liquidity']:,.2f}")
        
        if mkt['start_datetime']:
            print(f"   Game started: {mkt['start_datetime'].strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        if mkt['minutes_until_resolution'] is not None:
            hours = int(mkt['minutes_until_resolution'] // 60)
            mins = int(mkt['minutes_until_resolution'] % 60)
            if hours > 0:
                print(f"   Time until resolution: {hours}h {mins}m")
            else:
                print(f"   Time until resolution: {mins}m")
    
    # Show best market (highest liquidity, then shortest time)
    print()
    print("=" * 80)
    print("BEST MARKET (by prioritization)")
    print("=" * 80)
    
    if all_markets:
        best = max(
            all_markets,
            key=lambda x: (x['liquidity'], -x['minutes_until_resolution'] if x['minutes_until_resolution'] else 0)
        )
        
        print(f"\n✅ Best market:")
        print(f"   Market ID: {best['market_id']}")
        print(f"   Question: {best['question'][:70]}...")
        print(f"   Liquidity: ${best['liquidity']:,.2f}")
        print(f"   Topic: {best['topic'].upper()}")
        if best['minutes_until_resolution'] is not None:
            hours = int(best['minutes_until_resolution'] // 60)
            mins = int(best['minutes_until_resolution'] % 60)
            if hours > 0:
                print(f"   Time until resolution: {hours}h {mins}m")
            else:
                print(f"   Time until resolution: {mins}m")
    
    # Summary by topic
    print()
    print("=" * 80)
    print("SUMMARY BY TOPIC")
    print("=" * 80)
    
    by_topic = {}
    for mkt in all_markets:
        topic = mkt['topic']
        if topic not in by_topic:
            by_topic[topic] = []
        by_topic[topic].append(mkt)
    
    for topic in sorted(by_topic.keys()):
        markets = by_topic[topic]
        total_liq = sum(m['liquidity'] for m in markets)
        print(f"\n{topic.upper()}: {len(markets)} markets, ${total_liq:,.2f} total liquidity")


if __name__ == "__main__":
    test_sports_api()
