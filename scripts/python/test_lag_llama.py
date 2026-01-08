#!/usr/bin/env python3
"""Test Lag-Llama installation and availability."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

def test_gluonts():
    """Check if gluonts is installed."""
    try:
        import gluonts
        print(f"✓ gluonts installed: {gluonts.__version__}")
        return True
    except ImportError:
        print("✗ gluonts not installed")
        print("  Install with: pip install gluonts[torch]")
        return False

def test_lag_llama_loading():
    """Test if Lag-Llama can be loaded."""
    print("\n=== Testing Lag-Llama Loading ===")
    
    # Check gluonts first
    if not test_gluonts():
        print("\n⚠️  Lag-Llama requires gluonts[torch]")
        return False
    
    try:
        from gluonts.torch.model.lag_llama import LagLlamaEstimator
        print("✓ LagLlamaEstimator can be imported")
        
        # Try to create an estimator instance
        try:
            import torch
            estimator = LagLlamaEstimator(
                freq="1min",
                prediction_length=15,
                context_length=200,
            )
            print("✓ LagLlamaEstimator can be instantiated")
            return True
        except Exception as e:
            print(f"⚠️  Could not instantiate LagLlamaEstimator: {e}")
            return False
            
    except ImportError as e:
        print(f"✗ Cannot import LagLlamaEstimator: {e}")
        return False

def test_transformers_approach():
    """Test if Lag-Llama can be loaded via transformers."""
    print("\n=== Testing Transformers Approach ===")
    
    try:
        from transformers import AutoModelForCausalLM
        model_id = "time-series-foundation-models/Lag-Llama"
        print(f"Attempting to load {model_id}...")
        
        # This will likely fail, but let's see the error
        model = AutoModelForCausalLM.from_pretrained(model_id)
        print("✓ Lag-Llama loaded via transformers")
        return True
    except Exception as e:
        print(f"✗ Cannot load via transformers: {e}")
        return False

def test_btc_predictor():
    """Test BTCPredictor with Lag-Llama."""
    print("\n=== Testing BTCPredictor ===")
    
    try:
        from agents.models.btc_predictor import BTCPredictor
        import logging
        logging.basicConfig(level=logging.INFO)
        
        print("Attempting to load Lag-Llama via BTCPredictor...")
        predictor = BTCPredictor(model_name='lag-llama')
        
        if predictor.model == "baseline":
            print("⚠️  Lag-Llama fell back to baseline")
            return False
        elif predictor.model == "lag-llama-gluonts":
            print("✓ Lag-Llama loaded (gluonts-based)")
            return True
        else:
            print(f"✓ Lag-Llama loaded: {type(predictor.model)}")
            return True
            
    except Exception as e:
        print(f"✗ Error loading Lag-Llama: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Lag-Llama Status Check")
    print("=" * 60)
    
    gluonts_ok = test_gluonts()
    transformers_ok = test_transformers_approach()
    predictor_ok = test_btc_predictor()
    
    print("\n" + "=" * 60)
    print("Summary:")
    print(f"  gluonts installed: {'✓' if gluonts_ok else '✗'}")
    print(f"  transformers approach: {'✓' if transformers_ok else '✗'}")
    print(f"  BTCPredictor: {'✓' if predictor_ok else '✗'}")
    print("=" * 60)
    
    if not gluonts_ok:
        print("\n📝 To install gluonts:")
        print("   pip install gluonts[torch]")
        print("   or")
        print("   conda install -c conda-forge gluonts")

