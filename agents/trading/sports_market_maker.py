"""
Sports market maker module.

Uses split position strategy similar to BTC market maker, but adapted for live sports events.
- Filters for games that have already begun
- Limits concurrent positions (default: 1)
- Exits positions before resolution (configurable)
- Handles overtime scenarios
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Set

from agents.trading.market_maker_config import MarketMakerConfig
from agents.trading.market_maker import MarketMaker, MarketMakerPosition
from agents.trading.sports_market_detector import SportsMarketDetector
from agents.trading.trade_db import TradeDatabase
from agents.polymarket.polymarket import Polymarket
from agents.polymarket.market_finder import get_token_ids_from_market
from agents.polymarket.btc_market_detector import _parse_datetime_safe
from agents.trading.utils.market_time_helpers import get_minutes_until_resolution

logger = logging.getLogger(__name__)


class SportsMarketMakerConfig(MarketMakerConfig):
    """Extended config for sports market maker with sports-specific settings."""
    
    def __init__(self, config_path: str):
        """Load sports market maker config."""
        super().__init__(config_path)
        self._validate_sports_config()
    
    def _validate_sports_config(self):
        """Validate sports-specific config fields."""
        min_liquidity = self.config.get('min_liquidity', 100000.0)
        if not isinstance(min_liquidity, (int, float)) or min_liquidity < 0.0:
            raise ValueError(f"min_liquidity must be a non-negative float, got {min_liquidity}")
        
        max_concurrent_positions = self.config.get('max_concurrent_positions', 1)
        if not isinstance(max_concurrent_positions, int) or max_concurrent_positions < 1:
            raise ValueError(f"max_concurrent_positions must be a positive integer, got {max_concurrent_positions}")
        
        exit_minutes_before_resolution = self.config.get('exit_minutes_before_resolution', 5.0)
        if not isinstance(exit_minutes_before_resolution, (int, float)) or exit_minutes_before_resolution < 0.0:
            raise ValueError(f"exit_minutes_before_resolution must be a non-negative float, got {exit_minutes_before_resolution}")
        
        game_start_buffer_minutes = self.config.get('game_start_buffer_minutes', 5.0)
        if not isinstance(game_start_buffer_minutes, (int, float)) or game_start_buffer_minutes < 0.0:
            raise ValueError(f"game_start_buffer_minutes must be a non-negative float, got {game_start_buffer_minutes}")
        
        detection_interval_seconds = self.config.get('detection_interval_seconds', 60.0)
        if not isinstance(detection_interval_seconds, (int, float)) or detection_interval_seconds <= 0.0:
            raise ValueError(f"detection_interval_seconds must be a positive float, got {detection_interval_seconds}")
        
        topics = self.config.get('topics', [])
        if not isinstance(topics, list) or len(topics) == 0:
            raise ValueError(f"topics must be a non-empty list, got {topics}")
    
    @property
    def min_liquidity(self) -> float:
        """Minimum liquidity required to trade a market."""
        return float(self.config.get('min_liquidity', 100000.0))
    
    @property
    def max_concurrent_positions(self) -> int:
        """Maximum number of concurrent positions."""
        return int(self.config.get('max_concurrent_positions', 1))
    
    @property
    def exit_minutes_before_resolution(self) -> float:
        """Minutes before resolution to exit positions."""
        return float(self.config.get('exit_minutes_before_resolution', 5.0))
    
    @property
    def game_start_buffer_minutes(self) -> float:
        """Buffer minutes after game start to consider it 'begun'."""
        return float(self.config.get('game_start_buffer_minutes', 5.0))
    
    @property
    def detection_interval_seconds(self) -> float:
        """Interval between market detection checks."""
        return float(self.config.get('detection_interval_seconds', 60.0))
    
    @property
    def topics(self) -> list:
        """List of sports topics to monitor."""
        return list(self.config.get('topics', ['nfl', 'nba', 'nhl', 'soccer']))


class SportsMarketMaker(MarketMaker):
    """Market maker for live sports events using split position strategy."""
    
    def __init__(self, config_path: str, proxy_url: Optional[str] = None):
        """Initialize sports market maker with config."""
        # Use sports-specific config
        self.sports_config = SportsMarketMakerConfig(config_path)
        
        # Initialize parent with same config (it will load MarketMakerConfig)
        # We'll override config access to use sports_config
        super().__init__(config_path, proxy_url)
        
        # Override config to use sports config
        self.config = self.sports_config
        
        # Sports-specific tracking
        self.markets_with_positions: Set[str] = set()  # market_ids we have positions in
        self.monitored_markets: Dict[str, Dict] = {}  # market_id -> market_info
        
        # Initialize sports market detector
        self.detector = SportsMarketDetector(
            config=self.config,
            monitored_markets=self.monitored_markets,
            markets_with_bets=self.markets_with_positions,
            is_running=lambda: self.running,
            topics=self.config.topics,
            min_liquidity=self.config.min_liquidity,
            game_start_buffer_minutes=self.config.game_start_buffer_minutes,
        )
        
        logger.info(f"Sports Market Maker initialized:")
        logger.info(f"  Topics: {self.config.topics}")
        logger.info(f"  Min liquidity: ${self.config.min_liquidity:,.2f}")
        logger.info(f"  Max concurrent positions: {self.config.max_concurrent_positions}")
        logger.info(f"  Exit before resolution: {self.config.exit_minutes_before_resolution} minutes")
        logger.info(f"  Game start buffer: {self.config.game_start_buffer_minutes} minutes")
    
    async def start(self):
        """Start the sports market maker."""
        logger.info("=" * 80)
        logger.info("STARTING SPORTS MARKET MAKER")
        logger.info("=" * 80)
        logger.info(f"Topics: {self.config.topics}")
        logger.info(f"Split amount: ${self.config.split_amount:.2f}")
        logger.info(f"Offset above midpoint: {self.config.offset_above_midpoint:.4f}")
        logger.info(f"Min liquidity: ${self.config.min_liquidity:,.2f}")
        logger.info(f"Max concurrent positions: {self.config.max_concurrent_positions}")
        logger.info(f"Exit before resolution: {self.config.exit_minutes_before_resolution} minutes")
        logger.info("=" * 80)
        
        # Check wallet balances (reuse parent logic)
        try:
            wallet_address = self.pm.get_address_for_private_key()
            logger.info(f"Wallet address: {wallet_address}")
            
            direct_balance = self.pm.get_usdc_balance()
            logger.info(f"Direct Polygon wallet USDC balance: ${direct_balance:.2f}")
            
            if direct_balance < self.config.split_amount:
                logger.warning(
                    f"⚠ INSUFFICIENT DIRECT WALLET BALANCE: "
                    f"${direct_balance:.2f} < ${self.config.split_amount:.2f} (required for split)"
                )
            else:
                logger.info(f"✓ Direct wallet balance sufficient for split")
        except Exception as e:
            logger.warning(f"Could not check wallet balance: {e}")
        
        logger.info("=" * 80)
        
        # Start WebSocket services if enabled (reuse parent logic)
        if self.websocket_service:
            try:
                await self.websocket_service.start()
                logger.info("✓ WebSocket orderbook service started")
            except Exception as e:
                logger.error(f"Failed to start WebSocket orderbook service: {e}", exc_info=True)
        
        if self.websocket_order_status_service:
            try:
                await self.websocket_order_status_service.start()
                logger.info("✓ WebSocket order status service started")
            except Exception as e:
                logger.error(f"Failed to start WebSocket order status service: {e}", exc_info=True)
        
        # Merge most recent positions to free up capital (one-time on startup)
        # This checks the most recent N splits in the database and merges them if they have equal YES/NO shares
        logger.info("Merging recent positions to free up USDC capital (past 20)...")
        try:
            await self._merge_resolved_positions_for_capital(limit=20)
        except Exception as e:
            logger.error(f"Error merging positions for capital: {e}", exc_info=True)
        logger.info("")
        
        # Resume monitoring existing positions (reuse parent logic)
        await self._resume_positions()
        
        # Subscribe resumed positions to WebSocket if service is available
        if self.websocket_service:
            for market_slug, position in self.active_positions.items():
                token_ids = [position.yes_token_id, position.no_token_id]
                self.websocket_service.subscribe_tokens(token_ids, market_slug=market_slug)
        
        self.running = True
        
        # Start background tasks: sports detection loop + sports market maker loop
        detection_task = asyncio.create_task(self.detector.detection_loop())
        market_maker_task = asyncio.create_task(self._sports_market_maker_loop())
        
        try:
            # Run both tasks concurrently
            await asyncio.gather(detection_task, market_maker_task)
        except asyncio.CancelledError:
            logger.info("Sports Market Maker stopping...")
        finally:
            await self.stop()
    
    async def _sports_market_maker_loop(self):
        """Main sports market maker loop - detects markets and manages positions."""
        while self.running:
            try:
                # Check if we can add a new position
                if len(self.active_positions) < self.config.max_concurrent_positions:
                    await self._try_start_new_position()
                
                # Process existing positions
                for market_slug, position in list(self.active_positions.items()):
                    await self._process_position(market_slug, position)
                    
                    # Check if we need to exit before resolution
                    await self._check_exit_before_resolution(market_slug, position)
                    
                    # Check for market resolution
                    await self._check_market_resolution(market_slug, position)
                
                # Track orderbook prices for markets near resolution
                await self._track_orderbook_prices_near_resolution()
                
            except Exception as e:
                logger.error(f"Error in sports market maker loop: {e}", exc_info=True)
            
            await asyncio.sleep(self.config.poll_interval)
    
    async def _try_start_new_position(self):
        """Try to start a new position if we have available capacity."""
        try:
            # Get best market from detector
            best_market = self.detector.get_best_market()
            if not best_market:
                return  # No markets available
            
            market_id, market_info = best_market
            
            # Check if we already have a position in this market
            if market_id in self.markets_with_positions:
                return
            
            # Get market details
            market = market_info.get("market")
            if not market:
                return
            
            # Get token IDs
            yes_token_id = market_info.get("yes_token_id")
            no_token_id = market_info.get("no_token_id")
            if not yes_token_id or not no_token_id:
                logger.warning(f"Market {market_id} missing token IDs")
                return
            
            # Get condition ID
            condition_id = market.get("conditionId")
            if not condition_id:
                logger.warning(f"Market {market_id} missing conditionId")
                return
            
            # Use market_id as slug (sports markets use ID, not slug)
            market_slug = f"sports-{market_id}"
            
            logger.info(f"🎯 Starting new position for market {market_id}: {market.get('question', 'N/A')[:60]}...")
            
            # Start market making (reuse parent class method)
            success = await self._start_market_making(
                market=market,
                market_slug=market_slug,
                condition_id=condition_id,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id
            )
            
            if success:
                self.markets_with_positions.add(market_id)
                logger.info(f"✅ Successfully started position for market {market_id}")
            else:
                logger.warning(f"❌ Failed to start position for market {market_id}")
        
        except Exception as e:
            logger.error(f"Error trying to start new position: {e}", exc_info=True)
    
    async def _check_exit_before_resolution(self, market_slug: str, position: MarketMakerPosition):
        """
        Check if we need to exit position before resolution.
        
        For sports markets, we exit at (endDate - exit_minutes_before_resolution)
        regardless of overtime, to avoid holding through uncertain resolution periods.
        """
        try:
            market = position.market
            end_date = market.get("endDate") or market.get("endDateIso")
            if not end_date:
                return  # Can't determine exit time
            
            end_dt = _parse_datetime_safe(end_date)
            if not end_dt:
                return
            
            # Calculate exit time (endDate - buffer)
            exit_time = end_dt - timedelta(minutes=self.config.exit_minutes_before_resolution)
            now_utc = datetime.now(timezone.utc)
            
            # Check if it's time to exit
            if now_utc >= exit_time:
                minutes_until_end = (end_dt - now_utc).total_seconds() / 60.0
                logger.info(
                    f"⏰ Exiting position for {market_slug} before resolution "
                    f"({minutes_until_end:.1f} minutes until endDate)"
                )
                await self._close_position(position, reason="exit_before_resolution")
        
        except Exception as e:
            logger.error(f"Error checking exit before resolution for {market_slug}: {e}", exc_info=True)
    
    async def _handle_both_filled(self, position: MarketMakerPosition):
        """
        Handle when both sides are filled - check for best market instead of auto re-splitting same one.
        
        Overrides parent to use market prioritization logic instead of immediately re-splitting same market.
        """
        try:
            logger.info(f"Both sides filled for {position.market_slug}, closing position")
            
            # Get market ID from slug
            market_slug = position.market_slug
            current_market_id = None
            if market_slug.startswith("sports-"):
                current_market_id = market_slug.replace("sports-", "")
            
            # Close position (mark as both_filled)
            await self._close_position(position, reason="both_filled")
            
            # Instead of immediately re-splitting same market, let market selection logic decide
            # This allows us to potentially switch to a better market (higher liquidity, more time remaining)
            logger.info(
                f"Position closed. Market selection logic will pick the best available market "
                f"(may be same market or a better one based on liquidity/time until resolution)"
            )
            
            # The _try_start_new_position() method will be called in the next loop iteration
            # It will use get_best_market() which prioritizes by:
            # 1. Liquidity (higher is better)
            # 2. Time until resolution (shorter is better)
            # This ensures we trade the best available market, not necessarily the same one
            
        except Exception as e:
            logger.error(f"Error handling both filled: {e}", exc_info=True)
    
    async def _close_position(self, position: MarketMakerPosition, reason: str = "unknown"):
        """
        Close a position and clean up tracking.
        
        Overrides parent method to also clean up sports-specific tracking.
        """
        # Get market ID from slug (format: "sports-{market_id}")
        market_slug = position.market_slug
        if market_slug.startswith("sports-"):
            market_id = market_slug.replace("sports-", "")
            self.markets_with_positions.discard(market_id)
            self.monitored_markets.pop(market_id, None)
        
        # Call parent close method (it handles order cancellation and DB updates)
        await super()._close_position(position, reason=reason)
    
    async def stop(self):
        """Stop the sports market maker."""
        logger.info("Stopping Sports Market Maker...")
        self.running = False
        
        # Stop WebSocket services
        if self.websocket_service:
            await self.websocket_service.stop()
        
        if self.websocket_order_status_service:
            await self.websocket_order_status_service.stop()
        
        logger.info("Sports Market Maker stopped")
