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
from scripts.python.monitor_btc_15m import BTC15mMonitor
from scripts.python.monitor_eth_15m import ETH15mMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    """Run both BTC and ETH monitors concurrently."""
    from agents.polymarket.orderbook_db import OrderbookDatabase
    
    # Initialize database (will use DATABASE_URL from env if set, otherwise SQLite)
    # Use per-market tables for better organization
    db = OrderbookDatabase(per_market_tables=True)
    
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

