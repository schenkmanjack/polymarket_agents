"""
BTC Price Prediction Models

Provides integration with time-series forecasting models:
- Lag-Llama: Probabilistic forecasting with confidence intervals
- Chronos-Bolt: Fast point predictions

Usage:
    from agents.models.btc_predictor import BTCPredictor
    
    predictor = BTCPredictor(model_name="lag-llama")
    result = predictor.predict(price_sequence, prediction_horizon=15)
"""

from agents.models.btc_predictor import BTCPredictor

__all__ = ["BTCPredictor"]

