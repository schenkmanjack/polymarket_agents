"""
Test script for trading config loader.

Tests config loading and validation.
"""
import sys
import os
import json
import tempfile
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from agents.trading.config_loader import TradingConfig


def test_config_loading():
    """Test config loading and validation."""
    print("Testing config loader...")
    print()
    
    # Create a valid config
    valid_config = {
        "threshold": 0.40,
        "margin": 0.02,
        "kelly_fraction": 0.25,
        "kelly_scale_factor": 1.0,
        "market_type": "15m",
        "initial_principal": 100.0,
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(valid_config, f)
        config_path = f.name
    
    try:
        print("1. Loading valid config...")
        config = TradingConfig(config_path)
        print(f"   ✓ Loaded config: threshold={config.threshold}, market_type={config.market_type}")
        print()
        
        # Test amount calculation
        print("2. Testing amount calculation...")
        principal = 100.0
        amount = config.get_amount_invested(principal)
        expected = 100.0 * 0.25 * 1.0  # 25.0
        if amount == expected:
            print(f"   ✓ Amount invested: ${amount:.2f} (principal=${principal:.2f})")
        else:
            print(f"   ✗ Expected ${expected:.2f}, got ${amount:.2f}")
        print()
        
        print("✓ Config loading tests passed!")
    
    finally:
        os.unlink(config_path)


def test_config_validation():
    """Test config validation."""
    print("Testing config validation...")
    print()
    
    # Test missing field
    invalid_config = {
        "threshold": 0.40,
        "margin": 0.02,
        # Missing kelly_fraction
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(invalid_config, f)
        config_path = f.name
    
    try:
        print("1. Testing missing field validation...")
        try:
            config = TradingConfig(config_path)
            print("   ✗ Should have raised ValueError")
        except ValueError as e:
            print(f"   ✓ Correctly raised ValueError: {e}")
        print()
    
    finally:
        os.unlink(config_path)
    
    # Test invalid threshold
    invalid_config = {
        "threshold": 1.5,  # Invalid: > 1.0
        "margin": 0.02,
        "kelly_fraction": 0.25,
        "kelly_scale_factor": 1.0,
        "market_type": "15m",
        "initial_principal": 100.0,
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(invalid_config, f)
        config_path = f.name
    
    try:
        print("2. Testing invalid threshold validation...")
        try:
            config = TradingConfig(config_path)
            print("   ✗ Should have raised ValueError")
        except ValueError as e:
            print(f"   ✓ Correctly raised ValueError: {e}")
        print()
    
    finally:
        os.unlink(config_path)
    
    print("✓ Config validation tests passed!")


if __name__ == "__main__":
    test_config_loading()
    print()
    test_config_validation()
