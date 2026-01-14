"""
Configuration loader for market maker strategy.

Standalone config - only includes fields needed for market maker.
"""
import json
import os
import logging
from typing import Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class MarketMakerConfig:
    """
    Configuration for market maker strategy.
    
    Only includes fields actually used by the market maker.
    """
    
    def __init__(self, config_path: str):
        """
        Load configuration from JSON file.
        
        Args:
            config_path: Path to JSON config file
        """
        self.config_path = config_path
        self.config = self._load_config()
        self._validate_config()
    
    def _load_config(self) -> Dict:
        """Load config from JSON file."""
        config_path = Path(self.config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        logger.info(f"Loaded config from {config_path}")
        return config
    
    def _validate_config(self):
        """Validate configuration."""
        
        # Validate market maker specific fields
        split_amount = self.config.get('split_amount')
        if split_amount is None:
            raise ValueError("Missing required config field: split_amount")
        if not isinstance(split_amount, (int, float)) or split_amount <= 0.0:
            raise ValueError(f"split_amount must be a positive float, got {split_amount}")
        
        offset_above_midpoint = self.config.get('offset_above_midpoint')
        if offset_above_midpoint is None:
            raise ValueError("Missing required config field: offset_above_midpoint")
        if not isinstance(offset_above_midpoint, (int, float)) or not (0.0 < offset_above_midpoint <= 1.0):
            raise ValueError(f"offset_above_midpoint must be a float between 0.0 and 1.0, got {offset_above_midpoint}")
        
        price_step = self.config.get('price_step')
        if price_step is None:
            raise ValueError("Missing required config field: price_step")
        if not isinstance(price_step, (int, float)) or not (0.0 < price_step <= 1.0):
            raise ValueError(f"price_step must be a float between 0.0 and 1.0, got {price_step}")
        
        wait_after_fill = self.config.get('wait_after_fill')
        if wait_after_fill is None:
            raise ValueError("Missing required config field: wait_after_fill")
        if not isinstance(wait_after_fill, (int, float)) or wait_after_fill < 0.0:
            raise ValueError(f"wait_after_fill must be a non-negative float, got {wait_after_fill}")
        
        poll_interval = self.config.get('poll_interval')
        if poll_interval is None:
            raise ValueError("Missing required config field: poll_interval")
        if not isinstance(poll_interval, (int, float)) or poll_interval <= 0.0:
            raise ValueError(f"poll_interval must be a positive float, got {poll_interval}")
        
        min_minutes_before_resolution = self.config.get('min_minutes_before_resolution')
        if min_minutes_before_resolution is not None:
            if not isinstance(min_minutes_before_resolution, (int, float)) or min_minutes_before_resolution < 0.0:
                raise ValueError(f"min_minutes_before_resolution must be a non-negative float, got {min_minutes_before_resolution}")
        
        # Validate optional fields that might be inherited from TradingConfig but not used
        # (These are optional - just validate if present)
        market_type = self.config.get('market_type')
        if market_type is not None:
            if market_type not in ['15m', '1h']:
                raise ValueError(f"market_type must be '15m' or '1h', got {market_type}")
        
        max_minutes_before_resolution = self.config.get('max_minutes_before_resolution')
        if max_minutes_before_resolution is not None:
            if not isinstance(max_minutes_before_resolution, (int, float)) or max_minutes_before_resolution <= 0.0:
                raise ValueError(f"max_minutes_before_resolution must be a positive float, got {max_minutes_before_resolution}")
        
        # Validate WebSocket config (optional, defaults to True)
        use_websocket_orderbook = self.config.get('use_websocket_orderbook', True)
        if not isinstance(use_websocket_orderbook, bool):
            raise ValueError(f"use_websocket_orderbook must be a boolean, got {use_websocket_orderbook}")
        
        websocket_reconnect_delay = self.config.get('websocket_reconnect_delay', 5.0)
        if not isinstance(websocket_reconnect_delay, (int, float)) or websocket_reconnect_delay < 0.0:
            raise ValueError(f"websocket_reconnect_delay must be a non-negative float, got {websocket_reconnect_delay}")
        
        websocket_health_check_timeout = self.config.get('websocket_health_check_timeout', 14.0)
        if not isinstance(websocket_health_check_timeout, (int, float)) or websocket_health_check_timeout <= 0.0:
            raise ValueError(f"websocket_health_check_timeout must be a positive float, got {websocket_health_check_timeout}")
        
        # Validate WebSocket order status config (optional, defaults to True)
        use_websocket_order_status = self.config.get('use_websocket_order_status', True)
        if not isinstance(use_websocket_order_status, bool):
            raise ValueError(f"use_websocket_order_status must be a boolean, got {use_websocket_order_status}")
        
        websocket_order_status_reconnect_delay = self.config.get('websocket_order_status_reconnect_delay', 5.0)
        if not isinstance(websocket_order_status_reconnect_delay, (int, float)) or websocket_order_status_reconnect_delay < 0.0:
            raise ValueError(f"websocket_order_status_reconnect_delay must be a non-negative float, got {websocket_order_status_reconnect_delay}")
        
        websocket_order_status_health_check_timeout = self.config.get('websocket_order_status_health_check_timeout', 14.0)
        if not isinstance(websocket_order_status_health_check_timeout, (int, float)) or websocket_order_status_health_check_timeout <= 0.0:
            raise ValueError(f"websocket_order_status_health_check_timeout must be a positive float, got {websocket_order_status_health_check_timeout}")
        
        # Validate weighted midpoint config (optional, defaults to False)
        use_weighted_midpoint = self.config.get('use_weighted_midpoint', False)
        if not isinstance(use_weighted_midpoint, bool):
            raise ValueError(f"use_weighted_midpoint must be a boolean, got {use_weighted_midpoint}")
        
        midpoint_depth_levels = self.config.get('midpoint_depth_levels', 5)
        if not isinstance(midpoint_depth_levels, int) or midpoint_depth_levels < 1:
            raise ValueError(f"midpoint_depth_levels must be a positive integer, got {midpoint_depth_levels}")
        
        logger.info("✓ Market maker config validation passed")
    
    @property
    def market_type(self) -> str:
        """Market type ('15m' or '1h')."""
        return str(self.config.get('market_type', '1h'))
    
    @property
    def max_minutes_before_resolution(self) -> Optional[float]:
        """Maximum minutes before resolution to allow new positions. None means no limit."""
        value = self.config.get('max_minutes_before_resolution')
        return float(value) if value is not None else None
    
    @property
    def split_amount(self) -> float:
        """Amount of USDC to split per cycle."""
        return float(self.config['split_amount'])
    
    @property
    def offset_above_midpoint(self) -> float:
        """Initial offset above midpoint for sell orders."""
        return float(self.config['offset_above_midpoint'])
    
    @property
    def price_step(self) -> float:
        """Amount to lower price when adjusting."""
        return float(self.config['price_step'])
    
    @property
    def wait_after_fill(self) -> float:
        """Seconds to wait after one side fills before adjusting."""
        return float(self.config['wait_after_fill'])
    
    @property
    def poll_interval(self) -> float:
        """How often to check order status (seconds)."""
        return float(self.config['poll_interval'])
    
    @property
    def min_minutes_before_resolution(self) -> Optional[float]:
        """Minimum minutes before resolution to create new positions. None means no limit."""
        value = self.config.get('min_minutes_before_resolution')
        return float(value) if value is not None else None
    
    @property
    def use_websocket_orderbook(self) -> bool:
        """Whether to use WebSocket for orderbook updates."""
        return bool(self.config.get('use_websocket_orderbook', True))
    
    @property
    def websocket_health_check_timeout(self) -> float:
        """WebSocket health check timeout in seconds."""
        return float(self.config.get('websocket_health_check_timeout', 14.0))
    
    @property
    def websocket_reconnect_delay(self) -> float:
        """WebSocket reconnect delay in seconds."""
        return float(self.config.get('websocket_reconnect_delay', 5.0))
    
    @property
    def use_websocket_order_status(self) -> bool:
        """Whether to use WebSocket for order status updates."""
        return bool(self.config.get('use_websocket_order_status', True))
    
    @property
    def websocket_order_status_health_check_timeout(self) -> float:
        """WebSocket order status health check timeout in seconds."""
        return float(self.config.get('websocket_order_status_health_check_timeout', 14.0))
    
    @property
    def websocket_order_status_reconnect_delay(self) -> float:
        """WebSocket order status reconnect delay in seconds."""
        return float(self.config.get('websocket_order_status_reconnect_delay', 5.0))
    
    @property
    def use_weighted_midpoint(self) -> bool:
        """Whether to use volume-weighted midpoint instead of simple midpoint."""
        return bool(self.config.get('use_weighted_midpoint', False))
    
    @property
    def midpoint_depth_levels(self) -> int:
        """Number of orderbook levels to consider for weighted midpoint calculation."""
        return int(self.config.get('midpoint_depth_levels', 5))
