"""
Configuration loader for threshold trading strategy.

Loads and validates JSON configuration files.
"""
import json
import os
import logging
from typing import Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class TradingConfig:
    """Trading configuration."""
    
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
        required_fields = [
            'threshold',
            'upper_threshold',
            'margin',
            'threshold_sell',
            'margin_sell',
            'kelly_fraction',
            'kelly_scale_factor',
            'market_type',
            'initial_principal',
            'dollar_bet_limit',
        ]
        
        for field in required_fields:
            if field not in self.config:
                raise ValueError(f"Missing required config field: {field}")
        
        # Validate types and ranges
        threshold = self.config['threshold']
        if not isinstance(threshold, (int, float)) or not (0.0 < threshold <= 1.0):
            raise ValueError(f"threshold must be a float between 0.0 and 1.0, got {threshold}")
        
        upper_threshold = self.config['upper_threshold']
        if not isinstance(upper_threshold, (int, float)) or not (0.0 < upper_threshold <= 1.0):
            raise ValueError(f"upper_threshold must be a float between 0.0 and 1.0, got {upper_threshold}")
        
        # Ensure upper_threshold > threshold
        if upper_threshold <= threshold:
            raise ValueError(f"upper_threshold ({upper_threshold}) must be greater than threshold ({threshold})")
        
        margin = self.config['margin']
        if not isinstance(margin, (int, float)) or margin < 0.0:
            raise ValueError(f"margin must be a non-negative float, got {margin}")
        
        threshold_sell = self.config['threshold_sell']
        # Allow 0.0 as a special value to disable threshold sell
        if not isinstance(threshold_sell, (int, float)) or not (0.0 <= threshold_sell <= 1.0):
            raise ValueError(f"threshold_sell must be a float between 0.0 and 1.0 (0.0 disables it), got {threshold_sell}")
        
        margin_sell = self.config['margin_sell']
        if not isinstance(margin_sell, (int, float)) or margin_sell < 0.0:
            raise ValueError(f"margin_sell must be a non-negative float, got {margin_sell}")
        
        kelly_fraction = self.config['kelly_fraction']
        if not isinstance(kelly_fraction, (int, float)) or not (0.0 <= kelly_fraction <= 1.0):
            raise ValueError(f"kelly_fraction must be a float between 0.0 and 1.0, got {kelly_fraction}")
        
        kelly_scale_factor = self.config['kelly_scale_factor']
        if not isinstance(kelly_scale_factor, (int, float)) or kelly_scale_factor <= 0.0:
            raise ValueError(f"kelly_scale_factor must be a positive float, got {kelly_scale_factor}")
        
        market_type = self.config['market_type']
        if market_type not in ['15m', '1h']:
            raise ValueError(f"market_type must be '15m' or '1h', got {market_type}")
        
        initial_principal = self.config['initial_principal']
        if not isinstance(initial_principal, (int, float)) or initial_principal <= 0.0:
            raise ValueError(f"initial_principal must be a positive float, got {initial_principal}")
        
        dollar_bet_limit = self.config['dollar_bet_limit']
        if not isinstance(dollar_bet_limit, (int, float)) or dollar_bet_limit <= 0.0:
            raise ValueError(f"dollar_bet_limit must be a positive float, got {dollar_bet_limit}")
        
        # Validate max_minutes_before_resolution (optional)
        max_minutes = self.config.get('max_minutes_before_resolution')
        if max_minutes is not None:
            if not isinstance(max_minutes, (int, float)) or max_minutes <= 0.0:
                raise ValueError(f"max_minutes_before_resolution must be a positive float or null, got {max_minutes}")
        
        # Validate orderbook_poll_interval (optional, defaults to 1.0)
        orderbook_poll_interval = self.config.get('orderbook_poll_interval', 1.0)
        if not isinstance(orderbook_poll_interval, (int, float)) or orderbook_poll_interval <= 0.0:
            raise ValueError(f"orderbook_poll_interval must be a positive float, got {orderbook_poll_interval}")
        
        # Validate threshold_confirmation_seconds (optional, defaults to 0.0 - no confirmation)
        threshold_confirmation_seconds = self.config.get('threshold_confirmation_seconds', 0.0)
        if not isinstance(threshold_confirmation_seconds, (int, float)) or threshold_confirmation_seconds < 0.0:
            raise ValueError(f"threshold_confirmation_seconds must be a non-negative float, got {threshold_confirmation_seconds}")
        
        # Validate threshold_sell_confirmation_seconds (optional, defaults to 0.0 - no confirmation)
        threshold_sell_confirmation_seconds = self.config.get('threshold_sell_confirmation_seconds', 0.0)
        if not isinstance(threshold_sell_confirmation_seconds, (int, float)) or threshold_sell_confirmation_seconds < 0.0:
            raise ValueError(f"threshold_sell_confirmation_seconds must be a non-negative float, got {threshold_sell_confirmation_seconds}")
        
        # Validate always_use_initial_principal (optional, defaults to False)
        always_use_initial_principal = self.config.get('always_use_initial_principal', False)
        if not isinstance(always_use_initial_principal, bool):
            raise ValueError(f"always_use_initial_principal must be a boolean, got {always_use_initial_principal}")
        
        # Validate use_websocket_orderbook (optional, defaults to True)
        use_websocket_orderbook = self.config.get('use_websocket_orderbook', True)
        if not isinstance(use_websocket_orderbook, bool):
            raise ValueError(f"use_websocket_orderbook must be a boolean, got {use_websocket_orderbook}")
        
        # Validate websocket_reconnect_delay (optional, defaults to 5.0)
        websocket_reconnect_delay = self.config.get('websocket_reconnect_delay', 5.0)
        try:
            websocket_reconnect_delay = float(websocket_reconnect_delay)
            if websocket_reconnect_delay < 0:
                raise ValueError("websocket_reconnect_delay must be >= 0")
        except (ValueError, TypeError):
            raise ValueError(f"websocket_reconnect_delay must be a number >= 0, got {websocket_reconnect_delay}")
        
        # Validate websocket_health_check_timeout (optional, defaults to 14.0)
        websocket_health_check_timeout = self.config.get('websocket_health_check_timeout', 14.0)
        try:
            websocket_health_check_timeout = float(websocket_health_check_timeout)
            if websocket_health_check_timeout <= 0:
                raise ValueError("websocket_health_check_timeout must be > 0")
        except (ValueError, TypeError):
            raise ValueError(f"websocket_health_check_timeout must be a number > 0, got {websocket_health_check_timeout}")
        
        # Validate order_status_check_interval (optional, defaults to 10.0)
        order_status_check_interval = self.config.get('order_status_check_interval', 10.0)
        try:
            order_status_check_interval = float(order_status_check_interval)
            if order_status_check_interval <= 0:
                raise ValueError("order_status_check_interval must be > 0")
        except (ValueError, TypeError):
            raise ValueError(f"order_status_check_interval must be a number > 0, got {order_status_check_interval}")
        
        # Validate use_websocket_order_status (optional, defaults to True)
        use_websocket_order_status = self.config.get('use_websocket_order_status', True)
        if not isinstance(use_websocket_order_status, bool):
            raise ValueError(f"use_websocket_order_status must be a boolean, got {use_websocket_order_status}")
        
        # Validate websocket_order_status_reconnect_delay (optional, defaults to 5.0)
        websocket_order_status_reconnect_delay = self.config.get('websocket_order_status_reconnect_delay', 5.0)
        try:
            websocket_order_status_reconnect_delay = float(websocket_order_status_reconnect_delay)
            if websocket_order_status_reconnect_delay < 0:
                raise ValueError("websocket_order_status_reconnect_delay must be >= 0")
        except (ValueError, TypeError):
            raise ValueError(f"websocket_order_status_reconnect_delay must be a number >= 0, got {websocket_order_status_reconnect_delay}")
        
        # Validate websocket_order_status_health_check_timeout (optional, defaults to 14.0)
        websocket_order_status_health_check_timeout = self.config.get('websocket_order_status_health_check_timeout', 14.0)
        try:
            websocket_order_status_health_check_timeout = float(websocket_order_status_health_check_timeout)
            if websocket_order_status_health_check_timeout <= 0:
                raise ValueError("websocket_order_status_health_check_timeout must be > 0")
        except (ValueError, TypeError):
            raise ValueError(f"websocket_order_status_health_check_timeout must be a number > 0, got {websocket_order_status_health_check_timeout}")
        
        logger.info("✓ Config validation passed")
    
    @property
    def threshold(self) -> float:
        return float(self.config['threshold'])
    
    @property
    def upper_threshold(self) -> float:
        return float(self.config['upper_threshold'])
    
    @property
    def margin(self) -> float:
        return float(self.config['margin'])
    
    @property
    def threshold_sell(self) -> float:
        return float(self.config['threshold_sell'])
    
    @property
    def margin_sell(self) -> float:
        return float(self.config['margin_sell'])
    
    @property
    def kelly_fraction(self) -> float:
        return float(self.config['kelly_fraction'])
    
    @property
    def kelly_scale_factor(self) -> float:
        return float(self.config['kelly_scale_factor'])
    
    @property
    def market_type(self) -> str:
        return self.config['market_type']
    
    @property
    def initial_principal(self) -> float:
        return float(self.config['initial_principal'])
    
    @property
    def dollar_bet_limit(self) -> float:
        return float(self.config['dollar_bet_limit'])
    
    @property
    def max_minutes_before_resolution(self) -> Optional[float]:
        """Maximum minutes before resolution to allow buying. None means no limit."""
        value = self.config.get('max_minutes_before_resolution')
        return float(value) if value is not None else None
    
    @property
    def orderbook_poll_interval(self) -> float:
        """Orderbook polling interval in seconds. How frequently to check prices for threshold triggers."""
        return float(self.config.get('orderbook_poll_interval', 1.0))
    
    @property
    def threshold_confirmation_seconds(self) -> float:
        """Seconds to wait after threshold triggers before placing buy order. 0.0 means no confirmation."""
        return float(self.config.get('threshold_confirmation_seconds', 0.0))
    
    @property
    def threshold_sell_confirmation_seconds(self) -> float:
        """Seconds to wait after threshold sell triggers before placing sell order. 0.0 means no confirmation."""
        return float(self.config.get('threshold_sell_confirmation_seconds', 0.0))
    
    @property
    def always_use_initial_principal(self) -> bool:
        """If True, always use initial_principal for bet sizing calculations, regardless of current principal."""
        return bool(self.config.get('always_use_initial_principal', False))
    
    @property
    def use_websocket_orderbook(self) -> bool:
        """If True, use WebSocket for real-time orderbook updates instead of HTTP polling."""
        return bool(self.config.get('use_websocket_orderbook', True))
    
    @property
    def websocket_reconnect_delay(self) -> float:
        """Initial delay before reconnecting WebSocket (exponential backoff)."""
        return float(self.config.get('websocket_reconnect_delay', 5.0))
    
    @property
    def websocket_health_check_timeout(self) -> float:
        """Seconds of silence before considering WebSocket connection dead."""
        return float(self.config.get('websocket_health_check_timeout', 14.0))
    
    @property
    def order_status_check_interval(self) -> float:
        """Seconds between order status checks. How frequently to check if orders are filled."""
        return float(self.config.get('order_status_check_interval', 10.0))
    
    @property
    def use_websocket_order_status(self) -> bool:
        """If True, use WebSocket for real-time order status updates instead of HTTP polling."""
        return bool(self.config.get('use_websocket_order_status', True))
    
    @property
    def websocket_order_status_reconnect_delay(self) -> float:
        """Initial delay before reconnecting order status WebSocket (exponential backoff)."""
        return float(self.config.get('websocket_order_status_reconnect_delay', 5.0))
    
    @property
    def websocket_order_status_health_check_timeout(self) -> float:
        """Seconds of silence before considering order status WebSocket connection dead."""
        return float(self.config.get('websocket_order_status_health_check_timeout', 14.0))
    
    def get_amount_invested(self, principal: float) -> float:
        """
        Calculate amount to invest based on Kelly sizing, capped by dollar_bet_limit.
        
        Args:
            principal: Current principal amount (ignored if always_use_initial_principal is True)
            
        Returns:
            Amount to invest (capped at dollar_bet_limit)
        """
        # Use initial_principal if configured, otherwise use the passed principal
        principal_to_use = self.initial_principal if self.always_use_initial_principal else principal
        
        kelly_amount = principal_to_use * self.kelly_fraction * self.kelly_scale_factor
        # Cap at dollar_bet_limit even if Kelly suggests more
        return min(kelly_amount, self.dollar_bet_limit)
