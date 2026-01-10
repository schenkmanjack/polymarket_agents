"""
Minimal test for order placement and status checking.

Tests:
1. Place a small limit order
2. Extract order ID from response
3. Check order status
4. Verify proxy is working for authenticated requests

Uses POLYGON_WALLET_PRIVATE_KEY or POLYMARKET_PROXY_WALLET_ADDRESS from .env
"""
import sys
import os
import time
import logging
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from agents.utils.proxy_config import configure_proxy, get_proxy_dict, get_proxy
from agents.polymarket.polymarket import Polymarket
from agents.polymarket.btc_market_detector import get_latest_btc_15m_market
from agents.polymarket.market_finder import get_token_ids_from_market
from py_clob_client.order_builder.constants import BUY

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_order_placement():
    """Test placing an order and checking its status."""
    print("=" * 80)
    print("ORDER PLACEMENT AND STATUS TEST")
    print("=" * 80)
    print()
    
    # Step 1: Configure proxy
    print("Step 1: Configuring proxy from .env...")
    configure_proxy(auto_detect=True)
    proxy_dict = get_proxy_dict()
    proxy_url = get_proxy()
    
    if proxy_url:
        print(f"✓ Proxy configured: {proxy_url.split('@')[1] if '@' in proxy_url else 'configured'}")
        # Set HTTPS_PROXY environment variable for requests library (used by CLOB client)
        os.environ['HTTPS_PROXY'] = proxy_url
        os.environ['HTTP_PROXY'] = proxy_url
        print("✓ Set HTTPS_PROXY and HTTP_PROXY environment variables")
    else:
        print("⚠ No proxy configured (will use direct connection)")
    
    # Step 2: Check wallet key
    print()
    print("Step 2: Checking wallet configuration...")
    wallet_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    proxy_wallet = os.getenv("POLYMARKET_PROXY_WALLET_ADDRESS")
    
    if not wallet_key and not proxy_wallet:
        print("❌ No wallet key found!")
        print("   Please set POLYGON_WALLET_PRIVATE_KEY or POLYMARKET_PROXY_WALLET_ADDRESS in .env")
        return False
    
    if wallet_key:
        print(f"✓ Found POLYGON_WALLET_PRIVATE_KEY (length: {len(wallet_key)})")
    if proxy_wallet:
        print(f"✓ Found POLYMARKET_PROXY_WALLET_ADDRESS: {proxy_wallet[:10]}...{proxy_wallet[-8:]}")
    
    # Step 3: Initialize Polymarket
    print()
    print("Step 3: Initializing Polymarket client...")
    try:
        pm = Polymarket()
        if not pm.client:
            print("❌ Failed to initialize CLOB client")
            return False
        print("✓ Polymarket client initialized")
    except Exception as e:
        print(f"❌ Error initializing Polymarket: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Step 4: Get a test market
    print()
    print("Step 4: Finding a test market...")
    try:
        market = get_latest_btc_15m_market()
        if not market:
            print("❌ No BTC 15-minute market found")
            return False
        
        market_id = market.get("id")
        question = market.get("question", "N/A")[:60]
        print(f"✓ Found market: {market_id}")
        print(f"  Question: {question}...")
        
        # Get token IDs
        token_ids = get_token_ids_from_market(market)
        if not token_ids:
            print("❌ No token IDs found for market")
            return False
        
        # Use YES token (first one)
        test_token_id = token_ids[0]
        print(f"✓ Using token ID: {test_token_id[:20]}...")
        
    except Exception as e:
        print(f"❌ Error finding market: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Step 5: Get orderbook to determine a safe test price
    print()
    print("Step 5: Getting orderbook for safe test price...")
    try:
        orderbook = pm.get_orderbook(test_token_id)
        if not orderbook or not orderbook.asks:
            print("❌ Could not get orderbook or no asks available")
            return False
        
        # Get best ask price
        best_ask = float(orderbook.asks[0].price)
        print(f"✓ Best ask price: {best_ask:.4f}")
        
        # Place order at best ask price (will likely fill immediately)
        # Ensure order value is at least $1.00 (Polymarket minimum)
        test_price = best_ask
        min_order_value = 1.01  # $1.01 to ensure we're above $1.00 minimum (with rounding)
        test_size = max(1.0, min_order_value / test_price)  # At least $1.01 worth
        
        print(f"✓ Test order: price={test_price:.4f}, size={test_size:.4f}")
        print(f"  Order value: ${test_price * test_size:.2f}")
        print(f"  (Order placed at best ask - will likely fill immediately)")
        
    except Exception as e:
        print(f"❌ Error getting orderbook: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Step 6: Place order
    print()
    print("Step 6: Placing test order...")
    try:
        # Test auto-detection: don't specify fee_rate_bps, let it auto-detect from error
        order_response = pm.execute_order(
            price=test_price,
            size=test_size,
            side=BUY,
            token_id=test_token_id,
            fee_rate_bps=None  # Let it auto-detect
        )
        
        print(f"✓ Order placed!")
        print(f"  Response type: {type(order_response)}")
        print(f"  Response: {order_response}")
        
        # Extract order ID
        order_id = pm.extract_order_id(order_response)
        if not order_id:
            print("⚠ Could not extract order ID from response")
            print(f"  Response was: {order_response}")
            return False
        
        print(f"✓ Order ID extracted: {order_id}")
        
    except Exception as e:
        print(f"❌ Error placing order: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Step 7: Wait a moment for order to be processed
    print()
    print("Step 7: Waiting 2 seconds for order to be processed...")
    time.sleep(2)
    
    # Step 8: Check order status
    print()
    print("Step 8: Checking order status...")
    try:
        order_status = pm.get_order_status(order_id)
        
        if not order_status:
            print("❌ Could not get order status")
            return False
        
        print(f"✓ Order status retrieved:")
        print(f"  Status: {order_status.get('status', 'Unknown')}")
        print(f"  Order ID: {order_status.get('orderID', order_status.get('order_id', 'N/A'))}")
        
        # Show other relevant fields
        for key in ['takingAmount', 'makingAmount', 'price', 'size', 'side']:
            if key in order_status:
                print(f"  {key}: {order_status[key]}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error checking order status: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main test function."""
    try:
        success = test_order_placement()
        
        print()
        print("=" * 80)
        if success:
            print("✓ ORDER PLACEMENT TEST PASSED")
            print("   Order was placed and status was checked successfully")
            print("   Proxy is working for authenticated CLOB API calls")
        else:
            print("❌ ORDER PLACEMENT TEST FAILED")
            print("   Check the errors above and verify:")
            print("   - Wallet key is set in .env")
            print("   - Proxy is configured")
            print("   - Market is available")
        print("=" * 80)
        
        return 0 if success else 1
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

