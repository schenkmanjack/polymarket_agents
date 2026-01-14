"""
Master script to run both monitoring and trading services concurrently.

This script runs:
1. BTC markets monitoring (monitor_btc_markets.py) - optional
2. Trading strategy (threshold strategy or market maker)

Usage:
    # Run threshold strategy:
    python scripts/python/run_all_services.py --config config/trading_config.json --strategy threshold
    
    # Run market maker:
    python scripts/python/run_all_services.py --config config/market_maker_config.json --strategy market_maker
    
    # Run with monitoring:
    python scripts/python/run_all_services.py --config config/trading_config.json --strategy threshold --enable-monitoring
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

# Import trade_market_maker (this will import MarketMaker from agents.trading.market_maker)
market_maker_path = os.path.join(os.path.dirname(__file__), "trade_market_maker.py")
spec = importlib.util.spec_from_file_location("trade_market_maker", market_maker_path)
market_maker_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(market_maker_module)

monitor_main = monitor_module.main
ThresholdTrader = trade_module.ThresholdTrader
MarketMaker = market_maker_module.MarketMaker  # Available after module import


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


async def run_threshold_strategy(config_path: str):
    """Run the threshold strategy trading service."""
    logger.info("=" * 80)
    logger.info("STARTING THRESHOLD STRATEGY TRADING SERVICE")
    logger.info("=" * 80)
    try:
        trader = ThresholdTrader(config_path)
        await trader.start()
    except Exception as e:
        logger.error(f"Threshold strategy error: {e}", exc_info=True)
        raise


async def run_market_maker(config_path: str):
    """Run the market maker trading service."""
    logger.info("=" * 80)
    logger.info("STARTING MARKET MAKER TRADING SERVICE")
    logger.info("=" * 80)
    try:
        # MarketMaker needs proxy_url, get it from proxy_config
        from agents.utils.proxy_config import configure_proxy, get_proxy
        configure_proxy(auto_detect=True)
        proxy_url = get_proxy()
        
        market_maker = MarketMaker(config_path, proxy_url=proxy_url)
        await market_maker.start()
    except Exception as e:
        logger.error(f"Market maker error: {e}", exc_info=True)
        raise


async def run_all(config_path: str, strategy: str = "threshold", enable_monitoring: bool = False):
    """Run services. By default, only runs trading (monitoring disabled)."""
    logger.info("=" * 80)
    logger.info("STARTING SERVICES")
    logger.info("=" * 80)
    logger.info("Services:")
    if enable_monitoring:
        logger.info("  1. BTC Markets Monitoring")
        logger.info(f"  2. {strategy.upper().replace('_', ' ')} Trading")
    else:
        logger.info(f"  1. {strategy.upper().replace('_', ' ')} Trading (Monitoring disabled)")
    logger.info("=" * 80)
    
    # Select trading function based on strategy
    if strategy == "market_maker":
        trading_func = lambda: run_market_maker(config_path)
    elif strategy == "threshold":
        trading_func = lambda: run_threshold_strategy(config_path)
    else:
        raise ValueError(f"Unknown strategy: {strategy}. Must be 'threshold' or 'market_maker'")
    
    # Run services
    try:
        if enable_monitoring:
            # Run both services concurrently
            await asyncio.gather(
                run_monitoring(),
                trading_func(),
                return_exceptions=True
            )
        else:
            # Run only trading
            await trading_func()
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
        "--strategy",
        type=str,
        choices=["threshold", "market_maker"],
        default="threshold",
        help="Trading strategy to run (default: threshold)",
    )
    parser.add_argument(
        "--enable-monitoring",
        action="store_true",
        default=False,
        help="Enable BTC markets monitoring (disabled by default)",
    )
    
    args = parser.parse_args()
    
    # Handle graceful shutdown
    # Note: We don't set up signal handlers here because asyncio.run() handles SIGINT/SIGTERM
    # by raising KeyboardInterrupt, which allows tasks to be cancelled gracefully.
    # Setting up signal handlers that call sys.exit(0) directly interrupts ongoing operations
    # and causes "Task exception was never retrieved" errors.
    
    try:
        asyncio.run(run_all(args.config, strategy=args.strategy, enable_monitoring=args.enable_monitoring))
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
