"""
Script to check and log Polymarket cash balance.

Usage:
    python scripts/python/check_balance.py
"""
import sys
import os
import logging
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from agents.polymarket.polymarket import Polymarket

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_balances():
    """Check and log Polymarket balances."""
    try:
        # Initialize Polymarket client
        pm = Polymarket()
        
        if not pm.private_key:
            logger.error("❌ POLYGON_WALLET_PRIVATE_KEY not set in environment")
            logger.info("   Set it in .env file or as environment variable")
            return
        
        logger.info("=" * 70)
        logger.info("CHECKING POLYMARKET BALANCES")
        logger.info("=" * 70)
        logger.info("")
        
        # Get wallet address
        wallet_address = pm.get_address_for_private_key()
        logger.info(f"Wallet Address: {wallet_address}")
        logger.info("")
        
        # Check Polygon wallet USDC balance (direct wallet)
        try:
            polygon_balance = pm.get_usdc_balance()
            logger.info(f"✓ Polygon Wallet USDC Balance: ${polygon_balance:,.2f}")
        except Exception as e:
            logger.warning(f"⚠ Could not get Polygon wallet balance: {e}")
            polygon_balance = None
        
        logger.info("")
        
        # Check Polymarket trading balance (proxy wallet)
        proxy_address = os.getenv("POLYMARKET_PROXY_WALLET_ADDRESS")
        if not proxy_address:
            logger.info("")
            logger.info("💡 To check proxy wallet balance, set POLYMARKET_PROXY_WALLET_ADDRESS")
            logger.info("   Get this address from Polymarket deposit page")
            logger.info("   Add to .env: POLYMARKET_PROXY_WALLET_ADDRESS=0x...")
        
        try:
            polymarket_balance = pm.get_polymarket_balance()
            if polymarket_balance is not None:
                logger.info(f"✓ Polymarket Trading Balance: ${polymarket_balance:,.2f}")
                if proxy_address:
                    logger.info(f"  (Proxy wallet: {proxy_address[:10]}...{proxy_address[-8:]})")
                logger.info("  (Available for trading on Polymarket)")
            else:
                if proxy_address:
                    logger.info("⚠ Polymarket trading balance: $0.00 or unavailable")
                    logger.info(f"  (Proxy wallet: {proxy_address[:10]}...{proxy_address[-8:]})")
                else:
                    logger.info("⚠ Polymarket trading balance: Not available")
                    logger.info("  (Set POLYMARKET_PROXY_WALLET_ADDRESS to check)")
        except Exception as e:
            logger.warning(f"⚠ Could not get Polymarket balance: {e}")
            polymarket_balance = None
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("SUMMARY")
        logger.info("=" * 70)
        
        if polygon_balance is not None:
            logger.info(f"Polygon Wallet: ${polygon_balance:,.2f}")
        
        if polymarket_balance is not None:
            logger.info(f"Polymarket Trading: ${polymarket_balance:,.2f}")
            logger.info("")
            logger.info("💡 Tip: Deposit USDC to proxy wallet for gasless trading")
        else:
            logger.info("")
            logger.info("💡 Tip: Use proxy wallet address from Polymarket deposit page")
        
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"❌ Error checking balances: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    check_balances()

