"""
Live market maker script for BTC 1-hour markets.

Uses split position strategy: split USDC into YES + NO shares, place sell orders
slightly above midpoint, and adjust prices when one side fills.

Usage:
    python scripts/python/trade_market_maker.py --config config/market_maker_config.json
"""
import asyncio
import logging
import sys
import os
import argparse
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

# Configure proxy BEFORE importing modules that use httpx/requests
from agents.utils.proxy_config import configure_proxy, get_proxy
configure_proxy(auto_detect=True)
proxy_url = get_proxy()
if proxy_url:
    os.environ['HTTPS_PROXY'] = proxy_url
    os.environ['HTTP_PROXY'] = proxy_url

from agents.trading.market_maker import MarketMaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True
)
# Ensure all loggers are set to INFO level
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("agents").setLevel(logging.INFO)
logging.getLogger("agents.polymarket").setLevel(logging.INFO)
logging.getLogger("agents.polymarket.polymarket").setLevel(logging.INFO)
logging.getLogger("agents.trading").setLevel(logging.INFO)
# Ensure logs go to stdout
logging.getLogger().handlers[0].stream = sys.stdout
# Force flush after each log
for handler in logging.getLogger().handlers:
    handler.flush()
logger = logging.getLogger(__name__)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Market maker for BTC 1-hour markets")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to market maker config JSON file"
    )
    
    args = parser.parse_args()
    
    if proxy_url:
        logger.info(f"Proxy configured: {proxy_url.split('@')[1] if '@' in proxy_url else 'configured'}")
    else:
        logger.warning("No proxy configured - requests may be blocked by Cloudflare")
    
    # Initialize and start market maker
    market_maker = MarketMaker(args.config)
    
    try:
        asyncio.run(market_maker.start())
    except KeyboardInterrupt:
        logger.info("Market maker stopped by user")
    except Exception as e:
        logger.error("Fatal error in market maker", exc_info=True)
        raise


if __name__ == "__main__":
    main()
