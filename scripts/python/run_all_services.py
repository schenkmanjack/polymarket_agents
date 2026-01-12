"""
Master script to run both monitoring and trading services concurrently.

This script runs:
1. BTC markets monitoring (monitor_btc_markets.py)
2. Threshold strategy trading (trade_threshold_strategy.py)

Usage:
    python scripts/python/run_all_services.py --config config/trading_config.json
"""
import asyncio
import logging
import sys
import os
import argparse
import signal
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True
)
logging.getLogger().handlers[0].stream = sys.stdout
logger = logging.getLogger(__name__)

# Import the main functions from both scripts
# We need to import them as modules since they're in the same directory
import importlib.util
import importlib

# Import monitor_btc_markets
monitor_path = os.path.join(os.path.dirname(__file__), "monitor_btc_markets.py")
spec = importlib.util.spec_from_file_location("monitor_btc_markets", monitor_path)
monitor_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor_module)

# Import trade_threshold_strategy
trade_path = os.path.join(os.path.dirname(__file__), "trade_threshold_strategy.py")
spec = importlib.util.spec_from_file_location("trade_threshold_strategy", trade_path)
trade_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trade_module)

monitor_main = monitor_module.main
ThresholdTrader = trade_module.ThresholdTrader


async def run_monitoring():
    """Run the BTC markets monitoring service."""
    logger.info("=" * 80)
    logger.info("STARTING BTC MARKETS MONITORING SERVICE")
    logger.info("=" * 80)
    try:
        await monitor_main()
    except Exception as e:
        logger.error(f"Monitoring service error: {e}", exc_info=True)
        raise


async def run_trading(config_path: str):
    """Run the threshold strategy trading service."""
    logger.info("=" * 80)
    logger.info("STARTING THRESHOLD STRATEGY TRADING SERVICE")
    logger.info("=" * 80)
    try:
        trader = ThresholdTrader(config_path)
        await trader.start()
    except Exception as e:
        logger.error(f"Trading service error: {e}", exc_info=True)
        raise


async def run_all(config_path: str, enable_monitoring: bool = False):
    """Run services. By default, only runs trading (monitoring disabled)."""
    logger.info("=" * 80)
    logger.info("STARTING SERVICES")
    logger.info("=" * 80)
    logger.info("Services:")
    if enable_monitoring:
        logger.info("  1. BTC Markets Monitoring")
        logger.info("  2. Threshold Strategy Trading")
    else:
        logger.info("  1. Threshold Strategy Trading (Monitoring disabled)")
    logger.info("=" * 80)
    
    # Run services
    try:
        if enable_monitoring:
            # Run both services concurrently
            await asyncio.gather(
                run_monitoring(),
                run_trading(config_path),
                return_exceptions=True
            )
        else:
            # Run only trading
            await run_trading(config_path)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


def main():
    parser = argparse.ArgumentParser(description="Run monitoring and trading services")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to JSON config file for trading",
    )
    parser.add_argument(
        "--enable-monitoring",
        action="store_true",
        default=False,
        help="Enable BTC markets monitoring (disabled by default)",
    )
    
    args = parser.parse_args()
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Received interrupt signal, shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        asyncio.run(run_all(args.config, enable_monitoring=args.enable_monitoring))
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
