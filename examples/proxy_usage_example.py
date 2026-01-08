"""
Example: Using Proxy/VPN with BTC Backtesting

This example shows how to configure and use Oxylabs Static ISP proxies
for Binance and Polymarket API access.
"""
import os
from datetime import datetime, timedelta, timezone
from agents.utils.proxy_config import get_oxylabs_proxy_url, verify_proxy_ip
from agents.connectors.btc_data import BTCDataFetcher
from agents.backtesting.market_fetcher import HistoricalMarketFetcher
from agents.backtesting.btc_backtester import BTCBacktester


def example_1_environment_variables():
    """Example 1: Configure proxy via environment variables (Recommended)"""
    print("="*60)
    print("Example 1: Using Environment Variables")
    print("="*60)
    
    # Set environment variables (in production, use .env file or export)
    os.environ["PROXY_USER"] = "your_username"  # Will be prefixed with "user-" automatically
    os.environ["PROXY_PASS"] = "your_password"
    os.environ["PROXY_PORT"] = "8001"  # Dutch IP port (optional, defaults to 8001)
    
    # Components will automatically use proxy from environment
    btc_fetcher = BTCDataFetcher()  # Uses proxy from env
    market_fetcher = HistoricalMarketFetcher()  # Uses proxy from env
    backtester = BTCBacktester(model_name="chronos-bolt")  # Uses proxy from env
    
    print("✓ Components initialized with proxy from environment")


def example_2_direct_proxy_url():
    """Example 2: Pass proxy URL directly"""
    print("\n" + "="*60)
    print("Example 2: Direct Proxy URL")
    print("="*60)
    
    # Create Oxylabs proxy URL
    proxy_url = get_oxylabs_proxy_url(
        username="your_username",
        password="your_password",
        port=8001  # First Dutch IP
    )
    
    # Pass proxy to components
    btc_fetcher = BTCDataFetcher(proxy=proxy_url)
    market_fetcher = HistoricalMarketFetcher(proxy=proxy_url)
    backtester = BTCBacktester(model_name="chronos-bolt", proxy=proxy_url)
    
    print(f"✓ Components initialized with proxy: {proxy_url.split('@')[1] if '@' in proxy_url else 'configured'}")


def example_3_verify_proxy():
    """Example 3: Verify proxy is working"""
    print("\n" + "="*60)
    print("Example 3: Verify Proxy Connection")
    print("="*60)
    
    # Get proxy from environment or create directly
    proxy_url = get_oxylabs_proxy_url(
        username="your_username",
        password="your_password",
        port=8001
    )
    
    # Verify proxy IP location
    ip_info = verify_proxy_ip(proxy_url)
    
    if ip_info:
        print(f"✓ Proxy is working!")
        print(f"  IP: {ip_info.get('ip', 'N/A')}")
        print(f"  Location: {ip_info.get('city', 'N/A')}, {ip_info.get('country', 'N/A')}")
    else:
        print("❌ Proxy verification failed")


def example_4_backtest_with_proxy():
    """Example 4: Run backtest with proxy"""
    print("\n" + "="*60)
    print("Example 4: Running Backtest with Proxy")
    print("="*60)
    
    # Configure proxy
    proxy_url = get_oxylabs_proxy_url(
        username="your_username",
        password="your_password",
        port=8001
    )
    
    # Initialize backtester with proxy
    backtester = BTCBacktester(
        model_name="chronos-bolt",
        proxy=proxy_url
    )
    
    # Run backtest
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=7)
    
    print(f"Running backtest from {start_date.date()} to {end_date.date()}...")
    
    try:
        results_df = backtester.run_backtest(
            start_date=start_date,
            end_date=end_date,
            max_markets=10,
            enrich_with_btc_data=True
        )
        
        if not results_df.empty:
            print(f"✓ Backtest completed! Processed {len(results_df)} markets")
            print(f"  Win rate: {results_df['is_correct'].mean()*100:.1f}%")
        else:
            print("⚠ No markets found in date range")
            
    except Exception as e:
        print(f"❌ Backtest failed: {e}")


def example_5_test_binance_access():
    """Example 5: Test Binance API access through proxy"""
    print("\n" + "="*60)
    print("Example 5: Test Binance API Access")
    print("="*60)
    
    proxy_url = get_oxylabs_proxy_url(
        username="your_username",
        password="your_password",
        port=8001
    )
    
    fetcher = BTCDataFetcher(proxy=proxy_url)
    
    # Fetch recent BTC data
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=1)
    
    try:
        df = fetcher.get_prices(start_time, end_time, interval="1m")
        
        if not df.empty:
            print(f"✓ Successfully fetched {len(df)} data points")
            print(f"  Latest BTC price: ${df.iloc[-1]['close']:.2f}")
        else:
            print("❌ No data returned")
            
    except Exception as e:
        print(f"❌ Binance API access failed: {e}")


def example_6_test_polymarket_access():
    """Example 6: Test Polymarket API access through proxy"""
    print("\n" + "="*60)
    print("Example 6: Test Polymarket API Access")
    print("="*60)
    
    proxy_url = get_oxylabs_proxy_url(
        username="your_username",
        password="your_password",
        port=8001
    )
    
    fetcher = HistoricalMarketFetcher(proxy=proxy_url)
    
    # Fetch recent BTC markets
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=1)
    
    try:
        markets = fetcher.fetch_closed_btc_15m_markets(
            start_date=start_date,
            end_date=end_date,
            max_markets=5
        )
        
        if markets:
            print(f"✓ Successfully fetched {len(markets)} markets")
            for i, market in enumerate(markets[:3], 1):
                question = market.get('question', 'N/A')[:50]
                print(f"  Market {i}: {question}...")
        else:
            print("⚠ No markets found (may be normal)")
            
    except Exception as e:
        print(f"❌ Polymarket API access failed: {e}")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("PROXY USAGE EXAMPLES")
    print("="*60)
    print("\nNote: Replace 'your_username' and 'your_password' with actual Oxylabs credentials")
    print("      and ensure OXYLABS_PORT matches your configured port (8001, 8002, etc.)\n")
    
    # Uncomment the examples you want to run:
    
    # example_1_environment_variables()
    # example_2_direct_proxy_url()
    # example_3_verify_proxy()
    # example_4_backtest_with_proxy()
    # example_5_test_binance_access()
    # example_6_test_polymarket_access()
    
    print("\n" + "="*60)
    print("To run examples, uncomment them in the main section")
    print("="*60 + "\n")

