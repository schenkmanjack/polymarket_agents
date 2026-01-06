"""
Test integration with Lag-Llama and Chronos-Bolt models.

This script tests if we can:
1. Load the models
2. Format BTC price data correctly
3. Get predictions

Usage:
    python scripts/python/test_model_integration.py
"""
import sys
import os
from datetime import datetime, timedelta, timezone
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.connectors.btc_data import BTCDataFetcher
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_lag_llama():
    """Test Lag-Llama model integration."""
    print("=" * 70)
    print("Testing Lag-Llama Integration")
    print("=" * 70)
    
    try:
        # Try importing Lag-Llama
        # Lag-Llama is typically available via HuggingFace
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch
        
        print("\n✓ Successfully imported transformers")
        
        # Lag-Llama model name on HuggingFace
        model_name = "time-series-foundation-models/Lag-Llama"
        
        print(f"\nAttempting to load model: {model_name}")
        print("(This may take a while on first run - downloading model)")
        
        # Load model and tokenizer
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForCausalLM.from_pretrained(model_name)
        
        print("✓ Model loaded successfully!")
        
        # Get sample BTC data
        fetcher = BTCDataFetcher()
        timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
        sequence = fetcher.get_price_sequence(timestamp, lookback_minutes=200, interval='1m')
        
        if not sequence:
            print("✗ No BTC data available for testing")
            return False
        
        print(f"\n✓ Got BTC price sequence: {len(sequence)} data points")
        print(f"  Price range: ${min(sequence):.2f} - ${max(sequence):.2f}")
        
        # Format data for Lag-Llama
        # Lag-Llama expects specific input format - this is a simplified test
        # In practice, you'd need to use the proper Lag-Llama inference pipeline
        
        print("\n⚠ Note: Full Lag-Llama integration requires proper data formatting")
        print("  and using the model's specific inference pipeline.")
        print("  This test confirms the model can be loaded.")
        
        return True
        
    except ImportError as e:
        print(f"\n✗ Import error: {e}")
        print("\nTo install Lag-Llama dependencies:")
        print("  pip install transformers torch")
        return False
    except Exception as e:
        print(f"\n✗ Error loading Lag-Llama: {e}")
        print("\nThis might be due to:")
        print("  - Model not found on HuggingFace")
        print("  - Network issues downloading model")
        print("  - Missing dependencies")
        return False


def test_chronos():
    """Test Chronos model integration."""
    print("\n" + "=" * 70)
    print("Testing Chronos Integration")
    print("=" * 70)
    
    try:
        # Chronos models are available via HuggingFace
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch
        
        print("\n✓ Successfully imported transformers")
        
        # Chronos-Bolt is a smaller/faster version
        # Try chronos-t5-tiny first (smallest, fastest)
        model_name = "amazon/chronos-t5-tiny"
        
        print(f"\nAttempting to load model: {model_name}")
        print("(This may take a while on first run - downloading model)")
        
        # Load model
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForCausalLM.from_pretrained(model_name)
        
        print("✓ Model loaded successfully!")
        
        # Get sample BTC data
        fetcher = BTCDataFetcher()
        timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
        sequence = fetcher.get_price_sequence(timestamp, lookback_minutes=200, interval='1m')
        
        if not sequence:
            print("✗ No BTC data available for testing")
            return False
        
        print(f"\n✓ Got BTC price sequence: {len(sequence)} data points")
        print(f"  Price range: ${min(sequence):.2f} - ${max(sequence):.2f}")
        
        # Chronos expects normalized data
        # Convert to numpy array and normalize
        data_array = np.array(sequence)
        mean = np.mean(data_array)
        std = np.std(data_array)
        normalized_data = (data_array - mean) / std if std > 0 else data_array
        
        print(f"\n✓ Normalized data: mean={mean:.2f}, std={std:.2f}")
        print(f"  Normalized range: {normalized_data.min():.2f} - {normalized_data.max():.2f}")
        
        # Format for Chronos (simplified - actual usage needs proper pipeline)
        # Chronos expects context_length input
        context_length = len(normalized_data)
        prediction_length = 15  # Predict 15 minutes ahead
        
        print(f"\n  Context length: {context_length}")
        print(f"  Prediction length: {prediction_length}")
        
        print("\n⚠ Note: Full Chronos integration requires using the proper")
        print("  inference pipeline with correct data formatting.")
        print("  This test confirms the model can be loaded and data formatted.")
        
        return True
        
    except ImportError as e:
        print(f"\n✗ Import error: {e}")
        print("\nTo install Chronos dependencies:")
        print("  pip install transformers torch numpy")
        return False
    except Exception as e:
        print(f"\n✗ Error loading Chronos: {e}")
        print("\nThis might be due to:")
        print("  - Model not found on HuggingFace")
        print("  - Network issues downloading model")
        print("  - Missing dependencies")
        return False


def test_simple_prediction():
    """Test a simple prediction approach (baseline)."""
    print("\n" + "=" * 70)
    print("Testing Simple Prediction (Baseline)")
    print("=" * 70)
    
    try:
        # Get BTC data
        fetcher = BTCDataFetcher()
        timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
        sequence = fetcher.get_price_sequence(timestamp, lookback_minutes=200, interval='1m')
        
        if len(sequence) < 10:
            print("✗ Not enough data")
            return False
        
        print(f"\n✓ Got {len(sequence)} data points")
        
        # Simple prediction: last price (baseline)
        last_price = sequence[-1]
        print(f"\nBaseline prediction (last price): ${last_price:.2f}")
        
        # Simple prediction: moving average
        ma_window = 20
        if len(sequence) >= ma_window:
            ma = sum(sequence[-ma_window:]) / ma_window
            print(f"Moving average ({ma_window} periods): ${ma:.2f}")
        
        # Simple prediction: linear trend
        if len(sequence) >= 10:
            recent_prices = sequence[-10:]
            x = np.arange(len(recent_prices))
            coeffs = np.polyfit(x, recent_prices, 1)
            trend_prediction = np.polyval(coeffs, len(recent_prices) + 15)  # 15 minutes ahead
            print(f"Linear trend projection (15 min ahead): ${trend_prediction:.2f}")
        
        print("\n✓ Simple predictions work (baseline for comparison)")
        return True
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("Model Integration Test")
    print("=" * 70)
    print("\nTesting integration with AI forecasting models...")
    print("This will test:")
    print("  1. Lag-Llama (probabilistic forecasting)")
    print("  2. Chronos (time series forecasting)")
    print("  3. Simple baseline predictions")
    
    results = {
        "Lag-Llama": False,
        "Chronos": False,
        "Simple Baseline": False
    }
    
    # Test Lag-Llama
    try:
        results["Lag-Llama"] = test_lag_llama()
    except Exception as e:
        logger.error(f"Lag-Llama test failed: {e}", exc_info=True)
    
    # Test Chronos
    try:
        results["Chronos"] = test_chronos()
    except Exception as e:
        logger.error(f"Chronos test failed: {e}", exc_info=True)
    
    # Test simple baseline
    try:
        results["Simple Baseline"] = test_simple_prediction()
    except Exception as e:
        logger.error(f"Simple baseline test failed: {e}", exc_info=True)
    
    # Summary
    print("\n" + "=" * 70)
    print("Test Results Summary")
    print("=" * 70)
    
    for model, success in results.items():
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status}: {model}")
    
    print("\n" + "=" * 70)
    
    if any(results.values()):
        print("\nAt least one approach works! You can proceed with integration.")
    else:
        print("\n⚠ No models loaded successfully.")
        print("You may need to install dependencies:")
        print("  pip install transformers torch numpy")


if __name__ == "__main__":
    main()
