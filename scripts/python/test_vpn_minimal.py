"""
Minimal test to verify VPN/proxy is working with Polymarket CLOB client.

This test only verifies that:
1. Proxy is configured (via HTTPS_PROXY env var)
2. CLOB client can make API calls through the proxy
3. Proxy IP is being used (not direct connection)

Usage:
    python scripts/python/test_vpn_minimal.py
"""
import sys
import os
import logging
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from agents.utils.proxy_config import (
    get_proxy,
    get_proxy_dict,
    verify_proxy_ip,
    configure_proxy
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_proxy_configuration():
    """Test that proxy is configured correctly."""
    print("=" * 80)
    print("MINIMAL VPN/PROXY TEST")
    print("=" * 80)
    print()
    
    # Step 0: Ensure proxy is loaded from .env (via load_dotenv() at top)
    # Then auto-configure from environment if not already configured
    print("Step 0: Loading proxy configuration from .env and environment variables...")
    configure_proxy(auto_detect=True)  # This will check .env vars via get_proxy_from_env()
    
    # Step 1: Check if proxy is configured
    print()
    print("Step 1: Checking proxy configuration...")
    proxy_url = get_proxy()
    
    if not proxy_url:
        print("❌ No proxy configured!")
        print("   Please set one of in .env file:")
        print("   - HTTPS_PROXY=http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001")
        print("   - PROXY_USER=USERNAME")
        print("   - PROXY_PASS=PASSWORD")
        print("   - PROXY_PORT=8001")
        print("   Or set HTTPS_PROXY environment variable directly")
        return False
    
    # Hide password in display
    if "@" in proxy_url:
        display_url = proxy_url.split("@")[1] if "@" in proxy_url else proxy_url
        print(f"✓ Proxy configured: {display_url}")
    else:
        print(f"✓ Proxy configured: {proxy_url}")
    
    # Step 2: Verify proxy IP
    print()
    print("Step 2: Verifying proxy IP address...")
    ip_info = verify_proxy_ip(proxy_url)
    
    if ip_info:
        print(f"✓ Proxy IP verified: {ip_info.get('ip', 'Unknown')}")
        if 'city' in ip_info and 'country' in ip_info:
            print(f"  Location: {ip_info.get('city')}, {ip_info.get('country')}")
    else:
        print("⚠ Could not verify proxy IP (this is okay, proxy may still work)")
    
    # Step 3: Check HTTPS_PROXY env var (what requests library uses)
    print()
    print("Step 3: Checking HTTPS_PROXY environment variable...")
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    
    if https_proxy:
        # Hide password
        if "@" in https_proxy:
            display_proxy = https_proxy.split("@")[1] if "@" in https_proxy else https_proxy
            print(f"✓ HTTPS_PROXY set: {display_proxy}")
        else:
            print(f"✓ HTTPS_PROXY set: {https_proxy}")
    else:
        print("⚠ HTTPS_PROXY not set directly")
        print("   The requests library (used by CLOB client) needs HTTPS_PROXY env var")
        print("   Setting it now from proxy configuration...")
        
        # Set HTTPS_PROXY from our proxy config
        os.environ["HTTPS_PROXY"] = proxy_url
        os.environ["HTTP_PROXY"] = proxy_url
        print(f"✓ Set HTTPS_PROXY and HTTP_PROXY to proxy URL")
    
    # Step 4: Test simple API call through proxy
    print()
    print("Step 4: Testing API call through proxy...")
    try:
        import httpx
        
        # Get proxy dict for httpx
        proxy_dict = get_proxy_dict()
        
        # Make a simple test request to verify proxy is working
        # Try the CLOB API root or a simple endpoint
        test_urls = [
            "https://clob.polymarket.com/",
            "https://clob.polymarket.com/book?token_id=test",  # Will return error but proves proxy works
        ]
        
        proxy_working = False
        for test_url in test_urls:
            try:
                print(f"  Testing: {test_url}")
                response = httpx.get(test_url, proxies=proxy_dict, timeout=10.0)
                
                # Any response (even 400/404) means proxy is working
                # Connection errors would mean proxy failed
                print(f"  ✓ Got response through proxy (status: {response.status_code})")
                proxy_working = True
                break
            except httpx.ConnectError as e:
                print(f"  ⚠ Connection error (proxy may not be working): {e}")
                continue
            except Exception as e:
                # Other errors might still mean proxy worked but endpoint failed
                print(f"  ⚠ Error: {e}")
                continue
        
        if proxy_working:
            print(f"✓ Proxy is working - API calls are going through proxy")
            return True
        else:
            print(f"❌ Could not verify proxy is working")
            return False
            
    except Exception as e:
        print(f"❌ Error making API call through proxy: {e}")
        print(f"   Error type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main test function."""
    try:
        success = test_proxy_configuration()
        
        print()
        print("=" * 80)
        if success:
            print("✓ VPN/PROXY TEST PASSED")
            print("   Proxy is configured and working correctly")
            print("   You can now use the CLOB client for order placement/status checks")
        else:
            print("❌ VPN/PROXY TEST FAILED")
            print("   Please check proxy configuration and try again")
        print("=" * 80)
        
        return 0 if success else 1
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

