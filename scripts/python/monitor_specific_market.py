"""
Monitor a specific market or event by slug/ID.
Useful when markets aren't showing up in general API queries.

Usage:
    # Monitor by event slug
    python scripts/python/monitor_specific_market.py --event-slug btc-updown-15m-1767393900
    
    # Monitor by market ID
    python scripts/python/monitor_specific_market.py --market-id 123456
    
    # Monitor multiple events
    python scripts/python/monitor_specific_market.py --event-slug btc-updown-15m-1767393900 --event-slug btc-updown-15m-1767394000
"""
import asyncio
import argparse
import logging
import sys
import os
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.polymarket.orderbook_db import OrderbookDatabase
from agents.polymarket.orderbook_poller import OrderbookPoller
from agents.polymarket.market_finder import get_token_ids_from_market, get_market_info_for_logging
import httpx
import ast
import asyncio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_market_by_slug(slug: str) -> dict:
    """Get market by event slug."""
    # Try events endpoint first
    events_url = "https://gamma-api.polymarket.com/events"
    response = httpx.get(events_url, params={"slug": slug, "limit": 1})
    
    if response.status_code == 200:
        events = response.json()
        if events:
            event = events[0]
            markets = event.get("markets", [])
            if markets:
                return markets[0]  # Return first market from event
    
    # Try markets endpoint
    markets_url = "https://gamma-api.polymarket.com/markets"
    response = httpx.get(markets_url, params={"slug": slug, "limit": 1})
    
    if response.status_code == 200:
        markets = response.json()
        if markets:
            return markets[0]
    
    return None


def get_orderbook_direct(token_id: str):
    """Get orderbook directly from CLOB API without authentication."""
    # CLOB API endpoint for orderbook (public, no auth needed)
    url = f"https://clob.polymarket.com/book"
    response = httpx.get(url, params={"token_id": token_id})
    
    if response.status_code == 200:
        data = response.json()
        # Parse the response into bids/asks format
        bids = [[float(b["price"]), float(b["size"])] for b in data.get("bids", [])]
        asks = [[float(a["price"]), float(a["size"])] for a in data.get("asks", [])]
        return {"bids": bids, "asks": asks}
    
    return None


def get_market_by_id(market_id: str) -> dict:
    """Get market by ID."""
    markets_url = f"https://gamma-api.polymarket.com/markets/{market_id}"
    response = httpx.get(markets_url)
    
    if response.status_code == 200:
        return response.json()
    
    return None


async def monitor_markets(event_slugs: List[str], market_ids: List[str]):
    """Monitor specific markets/events."""
    db = OrderbookDatabase()
    
    all_token_ids = []
    all_market_info = {}
    
    # Process event slugs
    for slug in event_slugs:
        logger.info(f"Fetching event/market with slug: {slug}")
        market = get_market_by_slug(slug)
        
        if not market:
            logger.error(f"Could not find market/event with slug: {slug}")
            continue
        
        logger.info(f"Found market: ID={market.get('id')}, Question={market.get('question', 'N/A')[:60]}...")
        
        token_ids = get_token_ids_from_market(market)
        market_info = get_market_info_for_logging(market)
        
        all_token_ids.extend(token_ids)
        all_market_info.update(market_info)
        
        logger.info(f"  Token IDs: {token_ids}")
    
    # Process market IDs
    for market_id in market_ids:
        logger.info(f"Fetching market with ID: {market_id}")
        market = get_market_by_id(market_id)
        
        if not market:
            logger.error(f"Could not find market with ID: {market_id}")
            continue
        
        logger.info(f"Found market: ID={market.get('id')}, Question={market.get('question', 'N/A')[:60]}...")
        
        token_ids = get_token_ids_from_market(market)
        market_info = get_market_info_for_logging(market)
        
        all_token_ids.extend(token_ids)
        all_market_info.update(market_info)
        
        logger.info(f"  Token IDs: {token_ids}")
    
    if not all_token_ids:
        logger.error("No token IDs found. Cannot start monitoring.")
        return
    
    logger.info(f"Starting to monitor {len(all_token_ids)} tokens")
    logger.info(f"Token IDs: {all_token_ids}")
    
    # Initialize database (will use DATABASE_URL from env if set, otherwise SQLite)
    from agents.polymarket.orderbook_db import OrderbookDatabase
    db = OrderbookDatabase()  # Will log which database it's using
    
    # Check if we have wallet key for WebSocket (lower latency), otherwise use polling
    has_wallet_key = bool(os.getenv("POLYGON_WALLET_PRIVATE_KEY"))
    
    if has_wallet_key:
        # Use WebSocket (lower latency, more real-time)
        from agents.polymarket.orderbook_stream import OrderbookLogger
        logger.info("✓ Wallet key found - Using WebSocket mode (lower latency)")
        logger.info("  WebSocket provides sub-second updates vs polling every 2+ seconds")
        logger_service = OrderbookLogger(db, all_token_ids, market_info=all_market_info)
        try:
            await logger_service.start()
        except KeyboardInterrupt:
            logger.info("Stopping monitor...")
            await logger_service.stop()
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            await logger_service.stop()
            raise
    else:
        # Use polling (no wallet key needed, but higher latency)
        logger.info("⚠ No wallet key found - Using polling mode (higher latency)")
        logger.info("  Polling checks every 2 seconds vs WebSocket's sub-second updates")
        logger.info("  Add POLYGON_WALLET_PRIVATE_KEY to use WebSocket for better performance")
        poller = OrderbookPoller(db, all_token_ids, poll_interval=2.0, market_info=all_market_info)
        try:
            await poller.poll_loop()
        except KeyboardInterrupt:
            logger.info("Stopping monitor...")
            poller.stop()
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            poller.stop()
            raise


async def main():
    parser = argparse.ArgumentParser(
        description="Monitor specific markets/events by slug or ID"
    )
    parser.add_argument(
        "--event-slug",
        action="append",
        help="Event slug to monitor (can specify multiple times)",
        default=[],
    )
    parser.add_argument(
        "--market-id",
        action="append",
        help="Market ID to monitor (can specify multiple times)",
        default=[],
    )
    parser.add_argument(
        "--db-path",
        help="Path to SQLite database (default: uses DATABASE_URL env var)",
    )
    
    args = parser.parse_args()
    
    if not args.event_slug and not args.market_id:
        parser.error("Must provide at least one --event-slug or --market-id")
    
    await monitor_markets(args.event_slug, args.market_id)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)

