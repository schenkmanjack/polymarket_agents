"""
Sports market detection module for market making strategy.

Handles discovery and tracking of live sports markets from Polymarket.
Filters for games that have already begun (not just ending soon).
"""
import logging
from typing import Dict, List, Set, Callable, Optional, Tuple
from datetime import datetime, timezone, timedelta

from agents.polymarket.gamma import GammaMarketClient
from agents.polymarket.market_finder import get_token_ids_from_market
from agents.utils.proxy_config import get_proxy_dict
from agents.polymarket.btc_market_detector import _parse_datetime_safe

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
        min_liquidity: float = 100000.0,
        game_start_buffer_minutes: float = 5.0,
    ):
        """
        Initialize sports market detector.
        
        Args:
            config: TradingConfig instance (or similar config object)
            monitored_markets: Dict to store monitored markets (market_id -> market_info)
            markets_with_bets: Set of market IDs we've bet on
            is_running: Callable that returns current running status
            topics: List of sports topics to monitor (e.g., ["nfl", "nba", "nhl", "soccer"])
            min_liquidity: Minimum liquidity required to trade a market
            game_start_buffer_minutes: Buffer time in minutes after game start to consider it "begun"
        """
        self.config = config
        self.monitored_markets = monitored_markets
        self.markets_with_bets = markets_with_bets
        self.is_running = is_running
        self.topics = topics or ["nfl", "nba", "nhl", "soccer"]
        self.min_liquidity = min_liquidity
        self.game_start_buffer_minutes = game_start_buffer_minutes
        self.gamma = GammaMarketClient()
        self.proxies = get_proxy_dict()
        
        # Estimated game durations by sport (in hours)
        self.game_durations = {
            "nfl": 3.0,      # ~3 hours including halftime
            "nba": 2.5,      # ~2.5 hours
            "nhl": 2.5,      # ~2.5 hours
            "soccer": 2.0,   # ~2 hours (90 min + stoppage)
        }
    
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
    
    def _estimate_game_start_time(self, market: Dict, topic: str) -> Optional[datetime]:
        """
        Estimate game start time from endDate if startDate is not available.
        
        Args:
            market: Market dict
            topic: Sports topic (nfl, nba, nhl, soccer)
            
        Returns:
            Estimated start datetime or None
        """
        end_date = market.get("endDate") or market.get("endDateIso")
        if not end_date:
            return None
        
        end_dt = _parse_datetime_safe(end_date)
        if not end_dt:
            return None
        
        # Estimate start time based on typical game duration
        duration_hours = self.game_durations.get(topic, 2.5)
        estimated_start = end_dt - timedelta(hours=duration_hours)
        
        return estimated_start
    
    def _has_game_started(self, market: Dict, topic: str) -> Tuple[bool, Optional[datetime]]:
        """
        Check if the game has already started.
        
        Args:
            market: Market dict
            topic: Sports topic
            
        Returns:
            Tuple of (has_started, start_datetime)
        """
        now_utc = datetime.now(timezone.utc)
        
        # First, try to get actual startDate
        start_date = market.get("startDate") or market.get("startDateIso")
        if start_date:
            start_dt = _parse_datetime_safe(start_date)
            if start_dt:
                # Game has started if current time >= start time + buffer
                buffer = timedelta(minutes=self.game_start_buffer_minutes)
                has_started = now_utc >= (start_dt + buffer)
                return has_started, start_dt
        
        # Fallback: estimate from endDate
        estimated_start = self._estimate_game_start_time(market, topic)
        if estimated_start:
            buffer = timedelta(minutes=self.game_start_buffer_minutes)
            has_started = now_utc >= (estimated_start + buffer)
            return has_started, estimated_start
        
        # If we can't determine, return False (don't include)
        return False, None
    
    async def _check_topic_markets(self, topic: str):
        """Check for new markets in a specific topic."""
        try:
            # Query Gamma API for active markets in this topic
            # Filter for markets ending within next 6 hours (truly "live")
            now_utc = datetime.now(timezone.utc)
            max_end_time = now_utc + timedelta(hours=6)  # Markets ending within 6 hours
            
            # Format dates for API (ISO format)
            end_date_min = now_utc.isoformat()
            end_date_max = max_end_time.isoformat()
            
            params = {
                "topic": topic,
                "active": True,
                "closed": False,
                "archived": False,
                "limit": 100,
                "enableOrderBook": True,  # Only markets with orderbooks
                "liquidity_num_min": self.min_liquidity,  # Filter by liquidity at API level
                "end_date_min": end_date_min,  # Markets ending after now
                "end_date_max": end_date_max,  # Markets ending within 6 hours
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
                
                # Check liquidity (API should have filtered, but verify)
                liquidity = market.get("liquidity", 0)
                try:
                    liquidity = float(liquidity) if liquidity else 0.0
                except (ValueError, TypeError):
                    liquidity = 0.0
                
                # Double-check liquidity (API filter may not be perfect)
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
                
                # CRITICAL: Check if game has already started
                has_started, start_dt = self._has_game_started(market, topic)
                if not has_started:
                    start_str = start_dt.isoformat() if start_dt else "unknown"
                    logger.debug(
                        f"Market {market_id} game has not started yet (start: {start_str}) - skipping"
                    )
                    continue
                
                # Check if market is ending soon (within reasonable time)
                end_date = market.get("endDate") or market.get("endDateIso")
                if end_date:
                    try:
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
                
                # Calculate time until resolution for prioritization
                end_date = market.get("endDate") or market.get("endDateIso")
                minutes_until_resolution = None
                if end_date:
                    end_dt = _parse_datetime_safe(end_date)
                    if end_dt:
                        now_utc = datetime.now(timezone.utc)
                        minutes_until_resolution = (end_dt - now_utc).total_seconds() / 60.0
                
                # Add to monitored markets
                self.monitored_markets[market_id] = {
                    "market": market,
                    "token_ids": token_ids,
                    "yes_token_id": token_ids[0],
                    "no_token_id": token_ids[1],
                    "topic": topic,
                    "liquidity": liquidity,
                    "start_datetime": start_dt,
                    "minutes_until_resolution": minutes_until_resolution,
                }
                logger.info(
                    f"✅ Added LIVE {topic} market (game started): {market_id} - "
                    f"{market.get('question', 'N/A')[:60]}... "
                    f"(liquidity: ${liquidity:,.2f}, "
                    f"time until resolution: {minutes_until_resolution:.1f}m)"
                )
        
        except Exception as e:
            logger.error(f"Error checking {topic} markets: {e}", exc_info=True)
    
    def get_market_by_id(self, market_id: str) -> Optional[Dict]:
        """Get market info by ID."""
        return self.monitored_markets.get(market_id)
    
    def get_best_market(self) -> Optional[Tuple[str, Dict]]:
        """
        Get the best market to trade based on prioritization.
        
        Prioritizes by:
        1. Liquidity (higher is better)
        2. Time until resolution (shorter is better - more urgency)
        
        Returns:
            Tuple of (market_id, market_info) or None
        """
        if not self.monitored_markets:
            return None
        
        # Sort by liquidity (descending), then by minutes_until_resolution (ascending)
        sorted_markets = sorted(
            self.monitored_markets.items(),
            key=lambda x: (
                -x[1].get("liquidity", 0),  # Negative for descending
                x[1].get("minutes_until_resolution", float('inf'))  # Ascending
            )
        )
        
        return sorted_markets[0] if sorted_markets else None