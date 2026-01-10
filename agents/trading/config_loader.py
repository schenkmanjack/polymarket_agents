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
            'margin',
            'kelly_fraction',
            'kelly_scale_factor',
            'market_type',
            'initial_principal',
        ]
        
        for field in required_fields:
            if field not in self.config:
                raise ValueError(f"Missing required config field: {field}")
        
        # Validate types and ranges
        threshold = self.config['threshold']
        if not isinstance(threshold, (int, float)) or not (0.0 < threshold <= 1.0):
            raise ValueError(f"threshold must be a float between 0.0 and 1.0, got {threshold}")
        
        margin = self.config['margin']
        if not isinstance(margin, (int, float)) or margin < 0.0:
            raise ValueError(f"margin must be a non-negative float, got {margin}")
        
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
        
        logger.info("✓ Config validation passed")
    
    @property
    def threshold(self) -> float:
        return float(self.config['threshold'])
    
    @property
    def margin(self) -> float:
        return float(self.config['margin'])
    
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
    
    def get_amount_invested(self, principal: float) -> float:
        """
        Calculate amount to invest based on Kelly sizing.
        
        Args:
            principal: Current principal amount
            
        Returns:
            Amount to invest
        """
        return principal * self.kelly_fraction * self.kelly_scale_factor
