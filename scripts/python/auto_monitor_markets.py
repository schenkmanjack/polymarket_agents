"""
Automatically monitor and log orderbooks for new 15-minute and 1-hour markets.

This script continuously checks for new markets matching your criteria and
automatically starts logging their orderbooks.

Usage:
    # Monitor both 15-minute and 1-hour markets (WebSocket)
    python scripts/python/auto_monitor_markets.py
    
    # Only 15-minute markets
    python scripts/python/auto_monitor_markets.py --no-1hour
    
    # Only 1-hour markets
    python scripts/python/auto_monitor_markets.py --no-15min
    
    # Use polling instead of WebSocket
    python scripts/python/auto_monitor_markets.py --mode poll --check-interval 30
"""
import asyncio
import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.polymarket.auto_monitor import run_auto_monitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(
        description="Automatically monitor and log orderbooks for new markets"
    )
    parser.add_argument(
        "--check-interval",
        type=float,
        default=60.0,
        help="Seconds between checks for new markets (default: 60)",
    )
    parser.add_argument(
        "--no-15min",
        action="store_true",
        help="Don't monitor 15-minute markets",
    )
    parser.add_argument(
        "--no-1hour",
        action="store_true",
        help="Don't monitor 1-hour markets",
    )
    parser.add_argument(
        "--mode",
        choices=["websocket", "poll"],
        default="websocket",
        help="Mode: websocket (real-time) or poll (default: websocket)",
    )
    parser.add_argument(
        "--db-path",
        help="Path to SQLite database (default: ./orderbook.db) - deprecated, use DATABASE_URL env var",
    )
    parser.add_argument(
        "--database-url",
        help="Database URL (overrides DATABASE_URL env var)",
    )
    
    args = parser.parse_args()
    
    monitor_15min = not args.no_15min
    monitor_1hour = not args.no_1hour
    
    if not monitor_15min and not monitor_1hour:
        logger.error("Must monitor at least one market type (15min or 1hour)")
        return
    
    logger.info(
        f"Starting auto monitor:\n"
        f"  - 15-minute markets: {monitor_15min}\n"
        f"  - 1-hour markets: {monitor_1hour}\n"
        f"  - Check interval: {args.check_interval}s\n"
        f"  - Mode: {args.mode}"
    )
    
    # Get database URL from args or environment
    database_url = args.database_url or os.getenv("DATABASE_URL")
    
    await run_auto_monitor(
        check_interval=args.check_interval,
        monitor_15min=monitor_15min,
        monitor_1hour=monitor_1hour,
        mode=args.mode,
        database_url=database_url,
        db_path=args.db_path,  # Legacy support
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)

