"""
Test script to verify proxy configuration for Binance and Polymarket APIs.

Usage:
    # Using environment variables:
    export OXYLABS_USERNAME="your_username"
    export OXYLABS_PASSWORD="your_password"
    export OXYLABS_PORT="8001"
    python scripts/python/test_proxy.py
    
    # Or pass proxy URL directly:
    python scripts/python/test_proxy.py --proxy "http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001"
"""
import argparse
import logging
from agents.utils.proxy_config import (
    get_oxylabs_proxy_url,
    get_proxy_from_env,
    verify_proxy_ip,
    get_proxy_dict
)
from agents.connectors.btc_data import BTCDataFetcher
from agents.backtesting.market_fetcher import HistoricalMarketFetcher
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_proxy_connection(proxy_url: str = None):
    """Test proxy connection and verify IP location."""
    print("\n" + "="*60)
    print("PROXY CONNECTION TEST")
    print("="*60)
    
    if proxy_url is None:
        proxy_url = get_proxy_from_env()
    
    if not proxy_url:
        print("❌ No proxy configured!")
    print("\nTo configure proxy, use one of:")
    print("  1. Simple environment variables (recommended):")
    print("     export PROXY_USER='your_username'")
    print("     export PROXY_PASS='your_password'")
    print("     export PROXY_PORT='8001'  # optional, defaults to 8001")
    print("\n  2. Or set HTTPS_PROXY directly:")
    print("     export HTTPS_PROXY='http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001'")
    print("\n  3. Or legacy Oxylabs format:")
    print("     export OXYLABS_USERNAME='your_username'")
    print("     export OXYLABS_PASSWORD='your_password'")
    print("     export OXYLABS_PORT='8001'")
    return False
    
    print(f"✓ Proxy URL configured: {proxy_url.split('@')[1] if '@' in proxy_url else 'configured'}")
    
    # Verify IP location
    print("\nVerifying proxy IP location...")
    ip_info = verify_proxy_ip(proxy_url)
    
    if ip_info:
        print("✓ Proxy is working!")
        return True
    else:
        print("❌ Failed to verify proxy")
        return False


def test_binance_api(proxy_url: str = None):
    """Test Binance API access through proxy."""
    print("\n" + "="*60)
    print("BINANCE API TEST")
    print("="*60)
    
    try:
        fetcher = BTCDataFetcher(proxy=proxy_url)
        
        # Test fetching recent BTC data
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=1)
        
        print(f"Fetching BTC data from {start_time} to {end_time}...")
        df = fetcher.get_prices(start_time, end_time, interval="1m")
        
        if not df.empty:
            print(f"✓ Successfully fetched {len(df)} data points")
            print(f"  Latest price: ${df.iloc[-1]['close']:.2f}")
            print(f"  Time range: {df.iloc[0]['timestamp']} to {df.iloc[-1]['timestamp']}")
            return True
        else:
            print("❌ No data returned from Binance")
            return False
            
    except Exception as e:
        print(f"❌ Binance API test failed: {e}")
        return False


def test_polymarket_api(proxy_url: str = None):
    """Test Polymarket API access through proxy."""
    print("\n" + "="*60)
    print("POLYMARKET API TEST")
    print("="*60)
    
    try:
        fetcher = HistoricalMarketFetcher(proxy=proxy_url)
        
        # Test fetching recent BTC markets
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=1)
        
        print(f"Fetching BTC 15-minute markets from {start_date.date()} to {end_date.date()}...")
        markets = fetcher.fetch_closed_btc_15m_markets(start_date, end_date, max_markets=5)
        
        if markets:
            print(f"✓ Successfully fetched {len(markets)} markets")
            for i, market in enumerate(markets[:3], 1):
                print(f"  Market {i}: {market.get('question', 'N/A')[:50]}...")
            return True
        else:
            print("⚠ No markets found (this may be normal if no markets exist in the date range)")
            return True  # Not necessarily a failure
            
    except Exception as e:
        print(f"❌ Polymarket API test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description="Test proxy configuration for Binance and Polymarket")
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="Proxy URL (e.g., 'http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001')"
    )
    parser.add_argument(
        "--oxylabs",
        action="store_true",
        help="Use Oxylabs proxy from environment variables"
    )
    parser.add_argument(
        "--username",
        type=str,
        help="Oxylabs username (requires --password and --port)"
    )
    parser.add_argument(
        "--password",
        type=str,
        help="Oxylabs password"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Oxylabs port (default: 8001)"
    )
    
    args = parser.parse_args()
    
    # Determine proxy URL
    proxy_url = args.proxy
    
    if args.oxylabs or (args.username and args.password):
        if args.username and args.password:
            proxy_url = get_oxylabs_proxy_url(args.username, args.password, args.port)
        else:
            proxy_url = get_proxy_from_env()
    
    # Run tests
    print("\n" + "="*60)
    print("PROXY CONFIGURATION TEST SUITE")
    print("="*60)
    
    results = []
    
    # Test 1: Proxy connection
    results.append(("Proxy Connection", test_proxy_connection(proxy_url)))
    
    # Test 2: Binance API
    results.append(("Binance API", test_binance_api(proxy_url)))
    
    # Test 3: Polymarket API
    results.append(("Polymarket API", test_polymarket_api(proxy_url)))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    for test_name, passed in results:
        status = "✓ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(result[1] for result in results)
    print("\n" + ("="*60))
    if all_passed:
        print("✓ All tests passed! Proxy is configured correctly.")
    else:
        print("⚠ Some tests failed. Check the output above for details.")
    print("="*60 + "\n")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())

