"""
Sports market detection module for threshold strategy.

Handles discovery and tracking of live sports markets from Polymarket.
"""
import logging
from typing import Dict, List, Set, Callable, Optional
from datetime import datetime, timezone, timedelta

from agents.polymarket.gamma import GammaMarketClient
from agents.polymarket.market_finder import get_token_ids_from_market
from agents.utils.proxy_config import get_proxy_dict

logger = logging.getLogger(__name__)


class SportsMarketDetector:
    """Detects and tracks live sports markets for trading."""
    
    def __init__(
        self,
        config,
        monitored_markets: Dict[str, Dict],
        markets_with_bets: Set[str],
        is_running: Callable[[], bool],
        topics: List[str] = None,
        min_liquidity: float = 10000.0,
    ):
        """
        Initialize sports market detector.
        
        Args:
            config: TradingConfig instance (or similar config object)
            monitored_markets: Dict to store monitored markets (market_id -> market_info)
            markets_with_bets: Set of market IDs we've bet on
            is_running: Callable that returns current running status
            topics: List of sports topics to monitor (e.g., ["nfl", "nba", "nhl"])
            min_liquidity: Minimum liquidity required to trade a market
        """
        self.config = config
        self.monitored_markets = monitored_markets
        self.markets_with_bets = markets_with_bets
        self.is_running = is_running
        self.topics = topics or ["nfl", "nba", "nhl"]
        self.min_liquidity = min_liquidity
        self.gamma = GammaMarketClient()
        self.proxies = get_proxy_dict()
    
    async def detection_loop(self):
        """Continuously detect new sports markets."""
        check_interval = 60.0  # Check for new markets every 60 seconds
        
        while self.is_running():
            try:
                await self.check_for_new_markets()
            except Exception as e:
                logger.error(f"Error in sports market detection: {e}", exc_info=True)
            
            import asyncio
            await asyncio.sleep(check_interval)
    
    async def check_for_new_markets(self):
        """Check for new markets across all configured topics."""
        for topic in self.topics:
            try:
                await self._check_topic_markets(topic)
            except Exception as e:
                logger.error(f"Error checking {topic} markets: {e}", exc_info=True)
    
    async def _check_topic_markets(self, topic: str):
        """Check for new markets in a specific topic."""
        try:
            # Query Gamma API for active markets in this topic
            params = {
                "topic": topic,
                "active": True,
                "closed": False,
                "archived": False,
                "limit": 100,
                "enableOrderBook": True,  # Only markets with orderbooks
            }
            
            markets = self.gamma.get_markets(querystring_params=params, parse_pydantic=False)
            
            if not markets:
                logger.debug(f"No active markets found for topic '{topic}'")
                return
            
            logger.info(f"Found {len(markets)} active markets for topic '{topic}'")
            
            for market in markets:
                market_id = str(market.get("id"))
                
                # Skip if already monitoring or already bet on
                if market_id in self.monitored_markets or market_id in self.markets_with_bets:
                    continue
                
                # Check liquidity
                liquidity = market.get("liquidity", 0)
                try:
                    liquidity = float(liquidity) if liquidity else 0.0
                except (ValueError, TypeError):
                    liquidity = 0.0
                
                if liquidity < self.min_liquidity:
                    logger.debug(
                        f"Market {market_id} has insufficient liquidity: ${liquidity:,.2f} < ${self.min_liquidity:,.2f}"
                    )
                    continue
                
                # Check if market has valid token IDs
                token_ids = get_token_ids_from_market(market)
                if not token_ids or len(token_ids) < 2:
                    logger.debug(f"Market {market_id} has invalid token IDs")
                    continue
                
                # Check if market is ending soon (within max_minutes_before_resolution)
                # For sports, we want markets that are live or ending soon
                end_date = market.get("endDate") or market.get("endDateIso")
                if end_date:
                    try:
                        from agents.polymarket.btc_market_detector import _parse_datetime_safe
                        end_dt = _parse_datetime_safe(end_date)
                        if end_dt:
                            now_utc = datetime.now(timezone.utc)
                            minutes_remaining = (end_dt - now_utc).total_seconds() / 60.0
                            
                            # Only monitor markets ending within reasonable time
                            # (e.g., within next 4 hours for live games)
                            if minutes_remaining < 0 or minutes_remaining > 240:
                                logger.debug(
                                    f"Market {market_id} ends in {minutes_remaining:.1f} minutes - "
                                    f"outside monitoring window"
                                )
                                continue
                    except Exception as e:
                        logger.debug(f"Could not parse endDate for market {market_id}: {e}")
                
                # Add to monitored markets
                self.monitored_markets[market_id] = {
                    "market": market,
                    "token_ids": token_ids,
                    "yes_token_id": token_ids[0],
                    "no_token_id": token_ids[1],
                    "topic": topic,
                    "liquidity": liquidity,
                }
                logger.info(
                    f"Added new {topic} market to monitoring: {market_id} - "
                    f"{market.get('question', 'N/A')[:60]}... "
                    f"(liquidity: ${liquidity:,.2f})"
                )
        
        except Exception as e:
            logger.error(f"Error checking {topic} markets: {e}", exc_info=True)
    
    def get_market_by_id(self, market_id: str) -> Optional[Dict]:
        """Get market info by ID."""
        return self.monitored_markets.get(market_id)
