"""
Live sports market maker script.

Uses split position strategy: split USDC into YES + NO shares, place sell orders
slightly above midpoint, and adjust prices when one side fills.

Usage:
    python scripts/python/trade_sports_market_maker.py --config config/sports_market_maker_config.json
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

from agents.trading.sports_market_maker import SportsMarketMaker

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
    parser = argparse.ArgumentParser(description="Sports market maker for live sports/esports events")
    parser.add_argument(
        "--config",
        type=str,
        default="config/sports_market_maker_config.json",
        help="Path to sports market maker config JSON file (default: config/sports_market_maker_config.json)"
    )
    
    args = parser.parse_args()
    
    if proxy_url:
        logger.info(f"Proxy configured: {proxy_url.split('@')[1] if '@' in proxy_url else 'configured'}")
    else:
        logger.warning("No proxy configured - requests may be blocked by Cloudflare")
    
    # Initialize and start sports market maker
    market_maker = SportsMarketMaker(args.config, proxy_url=proxy_url)
    
    try:
        asyncio.run(market_maker.start())
    except KeyboardInterrupt:
        logger.info("Sports market maker stopped by user")
    except Exception as e:
        logger.error("Fatal error in sports market maker", exc_info=True)
        raise


if __name__ == "__main__":
    main()
