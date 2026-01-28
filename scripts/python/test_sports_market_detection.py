"""
Test script to verify sports market detection works.

Tests the SportsMarketDetector to ensure it can:
1. Fetch markets from Polymarket API
2. Filter for games that have started
3. Prioritize markets correctly
"""
import sys
import os
import logging
import asyncio
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

# Configure proxy BEFORE importing modules
from agents.utils.proxy_config import configure_proxy, get_proxy
configure_proxy(auto_detect=True)
proxy_url = get_proxy()
if proxy_url:
    os.environ['HTTPS_PROXY'] = proxy_url
    os.environ['HTTP_PROXY'] = proxy_url
    print(f"✓ Using proxy: {proxy_url.split('@')[1] if '@' in proxy_url else 'configured'}")

from agents.trading.sports_market_detector import SportsMarketDetector

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MockConfig:
    """Mock config for testing."""
    pass


def test_sports_market_detection():
    """Test sports market detection."""
    print("=" * 80)
    print("TESTING SPORTS MARKET DETECTION")
    print("=" * 80)
    print()
    
    # Initialize detector
    monitored_markets = {}
    markets_with_bets = set()
    is_running = lambda: True
    
    detector = SportsMarketDetector(
        config=MockConfig(),
        monitored_markets=monitored_markets,
        markets_with_bets=markets_with_bets,
        is_running=is_running,
        topics=["nfl", "nba", "nhl", "soccer"],
        min_liquidity=100000.0,
        game_start_buffer_minutes=5.0,
    )
    
    print(f"Topics: {detector.topics}")
    print(f"Min liquidity: ${detector.min_liquidity:,.2f}")
    print(f"Game start buffer: {detector.game_start_buffer_minutes} minutes")
    print()
    
    # Run detection once
    print("Fetching markets from Polymarket API...")
    print("-" * 80)
    
    async def run_test():
        try:
            await detector.check_for_new_markets()
            
            print()
            print("=" * 80)
            print(f"RESULTS: Found {len(monitored_markets)} qualifying markets")
            print("=" * 80)
            print()
            
            if not monitored_markets:
                print("⚠️  No markets found. Possible reasons:")
                print("   - No games currently happening")
                print("   - Games haven't started yet (need 5 min buffer)")
                print("   - Markets don't meet liquidity threshold")
                print("   - API connection issues")
                return
            
            # Show all found markets
            print("QUALIFYING MARKETS:")
            print("-" * 80)
            
            for market_id, market_info in monitored_markets.items():
                market = market_info.get("market", {})
                question = market.get("question", "N/A")
                topic = market_info.get("topic", "unknown")
                liquidity = market_info.get("liquidity", 0)
                minutes_until_resolution = market_info.get("minutes_until_resolution")
                start_dt = market_info.get("start_datetime")
                
                print(f"\n[{topic.upper()}] Market ID: {market_id}")
                print(f"  Question: {question[:80]}...")
                print(f"  Liquidity: ${liquidity:,.2f}")
                
                if start_dt:
                    print(f"  Game started: {start_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                
                if minutes_until_resolution is not None:
                    hours = int(minutes_until_resolution // 60)
                    mins = int(minutes_until_resolution % 60)
                    if hours > 0:
                        print(f"  Time until resolution: {hours}h {mins}m")
                    else:
                        print(f"  Time until resolution: {mins}m")
            
            # Test prioritization
            print()
            print("=" * 80)
            print("PRIORITIZATION TEST")
            print("=" * 80)
            
            best_market = detector.get_best_market()
            if best_market:
                market_id, market_info = best_market
                market = market_info.get("market", {})
                question = market.get("question", "N/A")
                liquidity = market_info.get("liquidity", 0)
                minutes_until_resolution = market_info.get("minutes_until_resolution")
                
                print(f"\n✅ Best market selected:")
                print(f"  Market ID: {market_id}")
                print(f"  Question: {question[:80]}...")
                print(f"  Liquidity: ${liquidity:,.2f}")
                if minutes_until_resolution is not None:
                    print(f"  Time until resolution: {minutes_until_resolution:.1f} minutes")
            else:
                print("\n⚠️  No best market found (shouldn't happen if markets were found)")
            
            # Show summary by topic
            print()
            print("=" * 80)
            print("SUMMARY BY TOPIC")
            print("=" * 80)
            
            by_topic = {}
            for market_id, market_info in monitored_markets.items():
                topic = market_info.get("topic", "unknown")
                if topic not in by_topic:
                    by_topic[topic] = []
                by_topic[topic].append(market_info)
            
            for topic, markets in sorted(by_topic.items()):
                total_liquidity = sum(m.get("liquidity", 0) for m in markets)
                print(f"\n{topic.upper()}: {len(markets)} markets, ${total_liquidity:,.2f} total liquidity")
            
        except Exception as e:
            logger.error(f"Error during detection test: {e}", exc_info=True)
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
    
    # Run async test
    asyncio.run(run_test())


if __name__ == "__main__":
    test_sports_market_detection()
