"""
Example script for logging real-time Polymarket orderbook data to a database.

Usage:
    # Using WebSocket (most real-time):
    python scripts/python/orderbook_logger.py --mode websocket --tokens TOKEN_ID1 TOKEN_ID2
    
    # Using polling (fallback):
    python scripts/python/orderbook_logger.py --mode poll --tokens TOKEN_ID1 TOKEN_ID2 --interval 2.0
    
    # Get token IDs from a market:
    python scripts/python/orderbook_logger.py --mode websocket --market MARKET_ID
"""
import asyncio
import argparse
import logging
import sys
import os
from typing import List

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.polymarket.orderbook_stream import run_orderbook_logger
from agents.polymarket.orderbook_poller import run_orderbook_poller
from agents.polymarket.orderbook_query import get_market_token_ids
from agents.polymarket.polymarket import Polymarket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_token_ids_from_market(market_id: str) -> List[str]:
    """Get token IDs for a market."""
    polymarket = Polymarket()
    market = polymarket.get_market(market_id)
    
    if not market:
        logger.error(f"Market {market_id} not found")
        return []
    
    import ast
    try:
        token_ids = ast.literal_eval(market.clob_token_ids)
        if isinstance(token_ids, list):
            return token_ids
        else:
            return [token_ids]
    except Exception as e:
        logger.error(f"Error parsing token IDs: {e}")
        return []


async def main():
    parser = argparse.ArgumentParser(
        description="Log Polymarket orderbook data to database"
    )
    parser.add_argument(
        "--mode",
        choices=["websocket", "poll"],
        default="websocket",
        help="Mode: websocket (real-time) or poll (polling-based)",
    )
    parser.add_argument(
        "--tokens",
        nargs="+",
        help="CLOB token IDs to monitor",
    )
    parser.add_argument(
        "--market",
        help="Polymarket market ID (will extract token IDs automatically)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (for poll mode, default: 1.0)",
    )
    parser.add_argument(
        "--db-path",
        help="Path to SQLite database (default: ./orderbook.db)",
    )
    
    args = parser.parse_args()
    
    # Get token IDs
    token_ids = []
    if args.tokens:
        token_ids = args.tokens
    elif args.market:
        token_ids = get_token_ids_from_market(args.market)
        if not token_ids:
            logger.error("No token IDs found for market")
            return
        logger.info(f"Found {len(token_ids)} token IDs for market {args.market}")
    else:
        logger.error("Must provide either --tokens or --market")
        return
    
    logger.info(f"Monitoring {len(token_ids)} tokens: {token_ids}")
    
    # Run the appropriate logger
    if args.mode == "websocket":
        logger.info("Starting WebSocket-based orderbook logger (most real-time)")
        await run_orderbook_logger(token_ids, db_path=args.db_path)
    else:
        logger.info(f"Starting polling-based orderbook logger (interval: {args.interval}s)")
        await run_orderbook_poller(
            token_ids,
            poll_interval=args.interval,
            db_path=args.db_path,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)

