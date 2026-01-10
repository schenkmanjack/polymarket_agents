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
        if not isinstance(threshold_sell, (int, float)) or not (0.0 < threshold_sell <= 1.0):
            raise ValueError(f"threshold_sell must be a float between 0.0 and 1.0, got {threshold_sell}")
        
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
    
    def get_amount_invested(self, principal: float) -> float:
        """
        Calculate amount to invest based on Kelly sizing, capped by dollar_bet_limit.
        
        Args:
            principal: Current principal amount
            
        Returns:
            Amount to invest (capped at dollar_bet_limit)
        """
        kelly_amount = principal * self.kelly_fraction * self.kelly_scale_factor
        # Cap at dollar_bet_limit even if Kelly suggests more
        return min(kelly_amount, self.dollar_bet_limit)
