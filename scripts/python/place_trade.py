"""
Script to place an automated trade on Polymarket.
Checks current positions, buys one "yes" share of current BTC 15-minute market,
then shows updated positions and balance.

Usage:
    python scripts/python/place_trade.py
"""
import sys
import os
import logging
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from agents.polymarket.polymarket import Polymarket
from agents.polymarket.btc_market_detector import get_latest_btc_15m_market
from agents.polymarket.market_finder import get_token_ids_from_market
from py_clob_client.clob_types import OrderArgs
from py_clob_client.order_builder.constants import BUY

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_positions(pm: Polymarket):
    """Get current positions from Polymarket."""
    try:
        if not pm.client:
            logger.warning("CLOB client not initialized - cannot get positions")
            return None
        
        # Try to get positions via CLOB client
        # Check if client has a get_positions or similar method
        if hasattr(pm.client, 'get_positions'):
            try:
                positions = pm.client.get_positions()
                return positions
            except Exception as e:
                logger.debug(f"get_positions() failed: {e}")
        
        # Alternative: Try to get fills/orders to infer positions
        if hasattr(pm.client, 'get_fills'):
            try:
                fills = pm.client.get_fills()
                return fills
            except Exception as e:
                logger.debug(f"get_fills() failed: {e}")
        
        # Try get_orders to see open orders
        if hasattr(pm.client, 'get_orders'):
            try:
                orders = pm.client.get_orders()
                return orders
            except Exception as e:
                logger.debug(f"get_orders() failed: {e}")
        
        logger.warning("No method found to get positions")
        return None
    except Exception as e:
        logger.error(f"Error getting positions: {e}")
        return None


def print_positions(positions):
    """Print current positions in a readable format."""
    logger.info("=" * 70)
    logger.info("CURRENT POSITIONS")
    logger.info("=" * 70)
    
    if positions is None:
        logger.info("⚠ Could not retrieve positions")
        logger.info("  (This may be normal if you have no open positions)")
        return
    
    if isinstance(positions, list):
        if len(positions) == 0:
            logger.info("No open positions")
        else:
            logger.info(f"Found {len(positions)} position(s):")
            for i, pos in enumerate(positions, 1):
                logger.info(f"  {i}. {pos}")
    elif isinstance(positions, dict):
        logger.info("Positions:")
        for key, value in positions.items():
            logger.info(f"  {key}: {value}")
    else:
        logger.info(f"Positions: {positions}")
    
    logger.info("=" * 70)


def get_yes_token_id(market_data: dict) -> str:
    """
    Extract the "yes" token ID from market data.
    For BTC updown markets, "yes" is typically the first token (index 0).
    """
    try:
        # Use the existing helper function to get all token IDs
        token_ids = get_token_ids_from_market(market_data)
        
        if not token_ids or len(token_ids) < 2:
            logger.error(f"Could not extract token IDs from market. Got: {token_ids}")
            return None
        
        # First token is "yes" for updown markets
        yes_token_id = token_ids[0]
        logger.info(f"  Token IDs: {token_ids}")
        logger.info(f"  Using first token as 'yes': {yes_token_id}")
        return yes_token_id
        
    except Exception as e:
        logger.error(f"Error extracting token ID: {e}", exc_info=True)
        return None


def place_buy_order(pm: Polymarket, token_id: str, size: float = 1.0):
    """
    Place a buy order for the specified token.
    
    Args:
        pm: Polymarket instance
        token_id: Token ID to buy
        size: Number of shares to buy (default: 1.0)
    
    Returns:
        Order response or None if failed
    """
    try:
        if not pm.client:
            logger.error("CLOB client not initialized - cannot place order")
            return None
        
        # Get current best ask price from orderbook for limit order
        price = None
        limit_price = None
        try:
            logger.info("Fetching orderbook to get best ask price...")
            # Use Polymarket's get_orderbook method (which calls client.get_order_book)
            orderbook = pm.get_orderbook(token_id)
            
            if orderbook:
                # Extract best ask price - handle different orderbook formats
                if hasattr(orderbook, 'asks') and orderbook.asks:
                    best_ask = orderbook.asks[0]
                    if hasattr(best_ask, 'price'):
                        price = float(best_ask.price)
                    elif isinstance(best_ask, dict):
                        price = float(best_ask.get('price', 0))
                    elif isinstance(best_ask, (list, tuple)) and len(best_ask) >= 2:
                        # Format: [price, size]
                        price = float(best_ask[0])
                
                # Also check if it's a dict with 'asks' key
                elif isinstance(orderbook, dict) and 'asks' in orderbook:
                    asks = orderbook['asks']
                    if asks and len(asks) > 0:
                        best_ask = asks[0]
                        if isinstance(best_ask, dict):
                            price = float(best_ask.get('price', 0))
                        elif isinstance(best_ask, (list, tuple)) and len(best_ask) >= 2:
                            price = float(best_ask[0])
                
                if price and price > 0:
                    logger.info(f"✓ Best ask price: ${price:.4f}")
                    # Use exactly the best ask price for limit order
                    limit_price = price
                    logger.info(f"  Using limit price: ${limit_price:.4f}")
                    # Add delay after orderbook fetch to space out requests
                    import time
                    time.sleep(3)
                else:
                    logger.warning("Could not extract valid price from orderbook")
                    logger.debug(f"Orderbook structure: {type(orderbook)}")
            else:
                logger.warning("Orderbook is None or empty")
                
        except Exception as e:
            logger.error(f"Error getting orderbook: {e}")
            logger.info("Cannot place limit order without price")
            return None
        
        if not limit_price or limit_price <= 0:
            logger.error("Could not determine valid price - cannot place limit order")
            return None
        
        # Calculate minimum order size (Polymarket requires $1 minimum)
        min_order_value = 1.0  # $1 minimum
        order_value = size * limit_price
        
        if order_value < min_order_value:
            # Calculate minimum shares needed to meet $1 minimum
            min_shares = min_order_value / limit_price
            # Round up to next whole share
            size = int(min_shares) + 1 if min_shares % 1 > 0 else int(min_shares)
            logger.info(f"  Order size adjusted: ${order_value:.2f} → ${size * limit_price:.2f} ({size} shares)")
            logger.info(f"  (Polymarket requires minimum $1 order size)")
        
        # Check and update balance allowance if needed
        logger.info("Checking balance allowance...")
        try:
            # Check current allowance
            if hasattr(pm.client, 'get_balance_allowance'):
                try:
                    current_allowance = pm.client.get_balance_allowance()
                    logger.info(f"  Current allowance: {current_allowance}")
                except Exception as e:
                    logger.debug(f"  Could not get current allowance: {e}")
            
            # Update balance allowance (required for first trade or when using proxy wallet)
            if hasattr(pm.client, 'update_balance_allowance'):
                logger.info("  Updating balance allowance for proxy wallet...")
                try:
                    allowance_result = pm.client.update_balance_allowance()
                    logger.info(f"  ✓ Allowance updated: {allowance_result}")
                    # Wait a moment for transaction to process
                    time.sleep(3)
                except Exception as e:
                    error_str = str(e).lower()
                    if 'signature_type' in error_str or 'nonetype' in error_str:
                        logger.warning(f"  Allowance update skipped (signature_type issue): {e}")
                        logger.info("  This may be normal if using proxy wallet - will try order anyway")
                    else:
                        logger.warning(f"  Could not update allowance: {e}")
        except Exception as e:
            logger.warning(f"  Allowance check failed: {e}")
        
        # Place limit buy order
        logger.info(f"Placing limit buy order: {size} share(s) at ${limit_price:.4f}")
        logger.info(f"  Total order value: ${size * limit_price:.2f}")
        logger.info(f"  Token: {token_id[:20]}...")
        
        # Add longer delay to avoid Cloudflare rate limiting
        # Cloudflare is very sensitive to rapid order placement requests
        import time
        logger.info("  Waiting 10 seconds before placing order to avoid Cloudflare blocks...")
        time.sleep(10)  # Wait 10 seconds before placing order
        
        try:
            order_response = pm.execute_order(
                price=limit_price,
                size=size,
                side=BUY,
                token_id=token_id
            )
            
            logger.info(f"✓ Limit order placed successfully!")
            logger.info(f"  Order response: {order_response}")
            return order_response
            
        except Exception as e:
            logger.error(f"Error placing limit order: {e}")
            # Check if it's a Cloudflare error
            error_str = str(e).lower()
            if 'cloudflare' in error_str or '403' in error_str or 'blocked' in error_str:
                logger.warning("⚠ Cloudflare blocked the request")
                logger.info("  This may be temporary - try again in a few minutes")
                logger.info("  Cloudflare protects Polymarket's API from:")
                logger.info("    • Rate limiting abuse")
                logger.info("    • Bot detection")
                logger.info("    • DDoS attacks")
            raise e
        
        logger.info(f"✓ Order placed successfully!")
        logger.info(f"  Response: {order_response}")
        return order_response
        
    except Exception as e:
        logger.error(f"Error placing order: {e}", exc_info=True)
        return None


def main():
    """Main function to execute the trade."""
    try:
        logger.info("=" * 70)
        logger.info("POLYMARKET AUTOMATED TRADE")
        logger.info("=" * 70)
        logger.info("")
        
        # Initialize Polymarket client
        pm = Polymarket()
        
        if not pm.private_key:
            logger.error("❌ POLYGON_WALLET_PRIVATE_KEY not set in environment")
            logger.info("   Set it in .env file or as environment variable")
            return
        
        if not pm.client:
            logger.error("❌ Could not initialize CLOB client")
            logger.info("   Check your POLYGON_WALLET_PRIVATE_KEY")
            return
        
        logger.info("✓ Polymarket client initialized")
        logger.info("")
        
        # Step 1: Check current positions
        logger.info("Step 1: Checking current positions...")
        positions_before = get_positions(pm)
        print_positions(positions_before)
        logger.info("")
        
        # Step 2: Get current balance
        logger.info("Step 2: Checking balances...")
        try:
            polygon_balance = pm.get_usdc_balance()
            logger.info(f"Polygon Wallet: ${polygon_balance:,.2f}")
        except Exception as e:
            logger.warning(f"Could not get Polygon balance: {e}")
        
        try:
            polymarket_balance = pm.get_polymarket_balance()
            if polymarket_balance is not None:
                logger.info(f"Polymarket Trading Balance: ${polymarket_balance:,.2f}")
        except Exception as e:
            logger.warning(f"Could not get Polymarket balance: {e}")
        
        logger.info("")
        
        # Add delay to avoid rapid requests
        import time
        logger.info("Waiting 5 seconds before market detection...")
        time.sleep(5)
        
        # Step 3: Find current BTC 15-minute market
        logger.info("Step 3: Finding current BTC 15-minute market...")
        market_data = get_latest_btc_15m_market()
        
        if not market_data:
            logger.error("❌ Could not find current BTC 15-minute market")
            return
        
        logger.info(f"✓ Found market: {market_data.get('question', 'Unknown')}")
        logger.info(f"  Market ID: {market_data.get('id', 'Unknown')}")
        logger.info(f"  Slug: {market_data.get('slug', 'Unknown')}")
        logger.info("")
        
        # Step 4: Extract "yes" token ID
        logger.info("Step 4: Extracting 'yes' token ID...")
        yes_token_id = get_yes_token_id(market_data)
        
        if not yes_token_id:
            logger.error("❌ Could not extract 'yes' token ID from market")
            logger.info(f"  Market data keys: {list(market_data.keys())}")
            return
        
        logger.info(f"✓ 'Yes' token ID: {yes_token_id}")
        logger.info("")
        
        # Add delay before order placement
        logger.info("Waiting 5 seconds before placing order...")
        time.sleep(5)
        
        # Step 5: Place buy order for 1 share
        logger.info("Step 5: Placing buy order for 1 'yes' share...")
        logger.info("  ⚠️  WARNING: This will execute a real trade!")
        logger.info("")
        
        order_response = place_buy_order(pm, yes_token_id, size=1.0)
        
        if order_response:
            logger.info("")
            logger.info("Step 6: Waiting a moment for order to process...")
            import time
            time.sleep(3)  # Wait 3 seconds for order to process
            
            # Step 6: Check positions again
            logger.info("Step 7: Checking positions after trade...")
            positions_after = get_positions(pm)
            print_positions(positions_after)
            logger.info("")
            
            # Step 7: Check balance again
            logger.info("Step 8: Checking balances after trade...")
            try:
                polygon_balance_after = pm.get_usdc_balance()
                logger.info(f"Polygon Wallet: ${polygon_balance_after:,.2f}")
            except Exception as e:
                logger.warning(f"Could not get Polygon balance: {e}")
            
            try:
                polymarket_balance_after = pm.get_polymarket_balance()
                if polymarket_balance_after is not None:
                    logger.info(f"Polymarket Trading Balance: ${polymarket_balance_after:,.2f}")
            except Exception as e:
                logger.warning(f"Could not get Polymarket balance: {e}")
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("TRADE COMPLETE")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"❌ Error executing trade: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

