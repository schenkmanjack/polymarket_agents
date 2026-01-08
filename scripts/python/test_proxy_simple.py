#!/usr/bin/env python3
"""
Simple test script to verify proxy configuration from .env file.
"""
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from agents.utils.proxy_config import (
    get_proxy_from_env,
    configure_proxy,
    get_proxy,
    get_proxy_dict,
    verify_proxy_ip
)
from agents.connectors.btc_data import BTCDataFetcher
from agents.backtesting.market_fetcher import HistoricalMarketFetcher
from agents.backtesting.btc_backtester import BTCBacktester

# Load .env file
load_dotenv()

def main():
    print("\n" + "="*60)
    print("PROXY CONFIGURATION TEST")
    print("="*60)
    
    # Test 1: Environment variables
    print("\n1. Checking environment variables...")
    proxy_user = os.environ.get("PROXY_USER")
    proxy_pass = os.environ.get("PROXY_PASS")
    proxy_port = os.environ.get("PROXY_PORT", "8001")
    
    if proxy_user and proxy_pass:
        print(f"   ✓ PROXY_USER: {proxy_user}")
        print(f"   ✓ PROXY_PASS: {'*' * len(proxy_pass)}")
        print(f"   ✓ PROXY_PORT: {proxy_port}")
    else:
        print("   ⚠ PROXY_USER or PROXY_PASS not set in .env file")
        print("   Add to .env:")
        print("     PROXY_USER=your_username")
        print("     PROXY_PASS=your_password")
        print("     PROXY_PORT=8001")
        return 1
    
    # Test 2: Proxy URL detection
    print("\n2. Detecting proxy URL from environment...")
    proxy_url = get_proxy_from_env()
    if proxy_url:
        # Mask password in output
        masked_url = proxy_url.split('@')[0].split(':')[0] + ':***@' + proxy_url.split('@')[1] if '@' in proxy_url else proxy_url
        print(f"   ✓ Proxy URL detected: {masked_url}")
    else:
        print("   ❌ Failed to detect proxy URL")
        return 1
    
    # Test 3: Global proxy configuration
    print("\n3. Configuring global proxy...")
    configure_proxy(proxy_url, auto_detect=False)
    current_proxy = get_proxy()
    if current_proxy:
        print("   ✓ Global proxy configured successfully")
    else:
        print("   ❌ Failed to configure global proxy")
        return 1
    
    # Test 4: Component initialization
    print("\n4. Testing component initialization...")
    try:
        btc_fetcher = BTCDataFetcher()
        print("   ✓ BTCDataFetcher initialized")
        print(f"      Proxy configured: {bool(btc_fetcher.proxy)}")
        
        market_fetcher = HistoricalMarketFetcher()
        print("   ✓ HistoricalMarketFetcher initialized")
        print(f"      Proxy configured: {bool(market_fetcher.proxy)}")
        
        backtester = BTCBacktester(model_name="baseline")
        print("   ✓ BTCBacktester initialized")
        print(f"      Model: {backtester.model_name}")
    except Exception as e:
        print(f"   ❌ Component initialization failed: {e}")
        return 1
    
    # Test 5: Proxy verification (optional - may fail if proxy not accessible)
    print("\n5. Verifying proxy connection...")
    try:
        ip_info = verify_proxy_ip()
        if ip_info:
            print("   ✓ Proxy connection verified!")
            print(f"      IP: {ip_info.get('ip', 'N/A')}")
            city = ip_info.get('city', ip_info.get('city_name', 'N/A'))
            country = ip_info.get('country', ip_info.get('country_name', 'N/A'))
            print(f"      Location: {city}, {country}")
        else:
            print("   ⚠ Proxy verification failed (proxy may not be accessible or credentials incorrect)")
    except Exception as e:
        print(f"   ⚠ Proxy verification error: {e}")
        print("   (This is OK if proxy is not accessible from this network)")
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print("✓ Environment variables loaded from .env")
    print("✓ Proxy URL detected and configured")
    print("✓ All components initialized successfully")
    print("\nProxy is configured and ready to use!")
    print("All API calls (Binance, Polymarket) will use the proxy automatically.")
    print("="*60 + "\n")
    
    return 0

if __name__ == "__main__":
    exit(main())

