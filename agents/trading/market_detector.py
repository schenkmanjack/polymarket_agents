"""
Market detection module for threshold strategy.

Handles discovery and tracking of new BTC markets.
"""
import logging
from typing import Dict, Set, Callable

from agents.polymarket.btc_market_detector import (
    get_latest_btc_15m_market_proactive,
    get_latest_btc_1h_market_proactive,
    get_all_active_btc_15m_markets,
    get_all_active_btc_1h_markets,
    is_market_currently_running,
)
from agents.polymarket.market_finder import get_token_ids_from_market

logger = logging.getLogger(__name__)


class MarketDetector:
    """Detects and tracks new BTC markets for trading."""
    
    def __init__(
        self,
        config,
        monitored_markets: Dict[str, Dict],
        markets_with_bets: Set[str],
        is_running: Callable[[], bool],
    ):
        """
        Initialize market detector.
        
        Args:
            config: TradingConfig instance
            monitored_markets: Dict to store monitored markets (market_slug -> market_info)
            markets_with_bets: Set of market slugs we've bet on
            is_running: Callable that returns current running status (allows real-time updates)
        """
        self.config = config
        self.monitored_markets = monitored_markets
        self.markets_with_bets = markets_with_bets
        self.is_running = is_running
    
    async def detection_loop(self):
        """Continuously detect new markets."""
        check_interval = 60.0  # Check for new markets every 60 seconds
        
        while self.is_running():
            try:
                await self.check_for_new_markets()
            except Exception as e:
                logger.error(f"Error in market detection: {e}", exc_info=True)
            
            import asyncio
            await asyncio.sleep(check_interval)
    
    async def check_for_new_markets(self):
        """Check for new markets of the configured type."""
        if self.config.market_type == "15m":
            await self._check_15m_markets()
        else:
            await self._check_1h_markets()
    
    async def _check_15m_markets(self):
        """Check for new 15-minute markets."""
        try:
            latest_market = get_latest_btc_15m_market_proactive()
            markets = [latest_market] if latest_market else get_all_active_btc_15m_markets()
            
            for market in markets:
                event_slug = market.get("_event_slug", "")
                if not event_slug:
                    continue
                
                # Skip if already monitoring or already bet on
                if event_slug in self.monitored_markets or event_slug in self.markets_with_bets:
                    continue
                
                # Only monitor currently running markets
                if not is_market_currently_running(market):
                    continue
                
                # Extract token IDs
                token_ids = get_token_ids_from_market(market)
                if not token_ids or len(token_ids) < 2:
                    continue
                
                # Add to monitored markets
                self.monitored_markets[event_slug] = {
                    "market": market,
                    "token_ids": token_ids,
                    "yes_token_id": token_ids[0],
                    "no_token_id": token_ids[1],
                }
                logger.info(f"Added new 15m market to monitoring: {event_slug}")
        
        except Exception as e:
            logger.error(f"Error checking 15m markets: {e}", exc_info=True)
    
    async def _check_1h_markets(self):
        """Check for new 1-hour markets."""
        try:
            latest_market = get_latest_btc_1h_market_proactive()
            markets = [latest_market] if latest_market else get_all_active_btc_1h_markets()
            
            for market in markets:
                event_slug = market.get("_event_slug", "")
                if not event_slug:
                    continue
                
                # Skip if already monitoring or already bet on
                if event_slug in self.monitored_markets or event_slug in self.markets_with_bets:
                    continue
                
                # Only monitor currently running markets
                if not is_market_currently_running(market):
                    continue
                
                # Extract token IDs
                token_ids = get_token_ids_from_market(market)
                if not token_ids or len(token_ids) < 2:
                    continue
                
                # Add to monitored markets
                self.monitored_markets[event_slug] = {
                    "market": market,
                    "token_ids": token_ids,
                    "yes_token_id": token_ids[0],
                    "no_token_id": token_ids[1],
                }
                logger.info(f"Added new 1h market to monitoring: {event_slug}")
        
        except Exception as e:
            logger.error(f"Error checking 1h markets: {e}", exc_info=True)
