"""
Automatically monitor new BTC and ETH updown 15-minute markets.
Runs both monitors concurrently to track all crypto 15-minute markets.

Usage:
    python scripts/python/monitor_crypto_15m.py
"""
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.polymarket.orderbook_db import OrderbookDatabase
from agents.polymarket.polymarket import Polymarket
from scripts.python.monitor_btc_15m import BTC15mMonitor
from scripts.python.monitor_eth_15m import ETH15mMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def log_balances():
    """Check and log Polymarket balances."""
    try:
        pm = Polymarket()
        
        if not pm.private_key:
            logger.debug("POLYGON_WALLET_PRIVATE_KEY not set - skipping balance check")
            return
        
        # Get wallet address
        wallet_address = pm.get_address_for_private_key()
        
        # Check Polygon wallet USDC balance
        try:
            polygon_balance = pm.get_usdc_balance()
            logger.info(f"💰 Polygon Wallet USDC Balance: ${polygon_balance:,.2f} (Address: {wallet_address[:10]}...{wallet_address[-8:]})")
        except Exception as e:
            logger.debug(f"Could not get Polygon wallet balance: {e}")
        
        # Check Polymarket trading balance (proxy wallet)
        try:
            polymarket_balance = pm.get_polymarket_balance()
            if polymarket_balance is not None:
                logger.info(f"💰 Polymarket Trading Balance: ${polymarket_balance:,.2f} (Proxy wallet - available for trading)")
            else:
                logger.debug("Polymarket trading balance not available (may need to deposit to proxy wallet)")
        except Exception as e:
            logger.debug(f"Could not get Polymarket balance: {e}")
            
    except Exception as e:
        logger.debug(f"Error checking balances: {e}")


async def main():
    """Run both BTC and ETH monitors concurrently."""
    from agents.polymarket.orderbook_db import OrderbookDatabase
    
    # Initialize database (will use DATABASE_URL from env if set, otherwise SQLite)
    # Use btc_eth_table - single table for all BTC/ETH markets (simpler, no race conditions)
    db = OrderbookDatabase(use_btc_eth_table=True)
    
    # Create both monitors
    btc_monitor = BTC15mMonitor(db, check_interval=60.0)
    eth_monitor = ETH15mMonitor(db, check_interval=60.0)
    
    logger.info("=" * 70)
    logger.info("Starting Crypto 15-Minute Market Monitor")
    logger.info("  - BTC 15-minute markets")
    logger.info("  - ETH 15-minute markets")
    logger.info("  - Check interval: 60 seconds")
    logger.info("  - Per-market tables: Enabled")
    logger.info("=" * 70)
    logger.info("")
    
    # Check and log balances
    log_balances()
    logger.info("")
    
    try:
        # Run both monitors concurrently
        await asyncio.gather(
            btc_monitor.run(),
            eth_monitor.run(),
        )
    except KeyboardInterrupt:
        logger.info("Stopping monitors...")
        btc_monitor.stop()
        eth_monitor.stop()
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        btc_monitor.stop()
        eth_monitor.stop()
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)

