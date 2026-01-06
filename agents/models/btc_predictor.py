"""
BTC Price Predictor using Lag-Llama and Chronos-Bolt models.

Tests integration with time-series forecasting models for BTC price prediction.
"""
import logging
import numpy as np
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class BTCPredictor:
    """
    Wrapper for BTC price prediction models (Lag-Llama, Chronos-Bolt).
    
    Handles model loading, data preprocessing, and prediction.
    """
    
    def __init__(self, model_name: str = "lag-llama"):
        """
        Initialize predictor with specified model.
        
        Args:
            model_name: 'lag-llama' or 'chronos-bolt'
        """
        self.model_name = model_name.lower()
        self.model = None
        self.tokenizer = None
        self._load_model()
    
    def _load_model(self):
        """Load the specified model from HuggingFace."""
        try:
            if self.model_name == "lag-llama":
                self._load_lag_llama()
            elif self.model_name == "chronos-bolt":
                self._load_chronos_bolt()
            else:
                raise ValueError(f"Unknown model: {self.model_name}")
        except Exception as e:
            logger.error(f"Error loading model {self.model_name}: {e}")
            logger.info("Falling back to simple baseline predictor")
            self.model = "baseline"
    
    def _load_lag_llama(self):
        """Load Lag-Llama model from HuggingFace."""
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            
            # Lag-Llama model ID (check HuggingFace for exact ID)
            # Common IDs: "time-series-foundation-models/Lag-Llama", "amazon/chronos-t5-tiny"
            model_id = "time-series-foundation-models/Lag-Llama"
            
            logger.info(f"Loading Lag-Llama from {model_id}...")
            
            # Try to load model (may require torch)
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(model_id)
                self.model = AutoModelForCausalLM.from_pretrained(model_id)
                logger.info("✓ Lag-Llama loaded successfully")
            except Exception as e:
                logger.warning(f"Could not load Lag-Llama: {e}")
                logger.info("This may require torch and transformers packages")
                raise
                
        except ImportError:
            logger.warning("transformers package not installed. Install with: pip install transformers torch")
            raise
        except Exception as e:
            logger.error(f"Error loading Lag-Llama: {e}")
            raise
    
    def _load_chronos_bolt(self):
        """Load Chronos-Bolt model from HuggingFace."""
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            
            # Chronos-Bolt model ID (check HuggingFace for exact ID)
            # Common IDs: "amazon/chronos-t5-tiny", "amazon/chronos-t5-small"
            model_id = "amazon/chronos-t5-tiny"  # Smallest/fastest version
            
            logger.info(f"Loading Chronos-Bolt from {model_id}...")
            
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(model_id)
                self.model = AutoModelForCausalLM.from_pretrained(model_id)
                logger.info("✓ Chronos-Bolt loaded successfully")
            except Exception as e:
                logger.warning(f"Could not load Chronos-Bolt: {e}")
                logger.info("This may require torch and transformers packages")
                raise
                
        except ImportError:
            logger.warning("transformers package not installed. Install with: pip install transformers torch")
            raise
        except Exception as e:
            logger.error(f"Error loading Chronos-Bolt: {e}")
            raise
    
    def predict(
        self,
        price_sequence: List[float],
        prediction_horizon: int = 15,
        return_confidence: bool = False
    ) -> Dict:
        """
        Predict BTC price N minutes ahead.
        
        Args:
            price_sequence: List of historical prices (oldest to newest)
            prediction_horizon: Minutes ahead to predict (default: 15)
            return_confidence: Whether to return confidence intervals (Lag-Llama only)
        
        Returns:
            Dict with:
                - 'predicted_price': Predicted price
                - 'confidence_interval' (optional): [lower, upper] bounds
                - 'model': Model name used
        """
        if not price_sequence:
            raise ValueError("Price sequence cannot be empty")
        
        if self.model == "baseline":
            return self._baseline_predict(price_sequence, prediction_horizon)
        
        if self.model_name == "lag-llama":
            return self._predict_lag_llama(price_sequence, prediction_horizon, return_confidence)
        elif self.model_name == "chronos-bolt":
            return self._predict_chronos_bolt(price_sequence, prediction_horizon)
        else:
            return self._baseline_predict(price_sequence, prediction_horizon)
    
    def _predict_lag_llama(
        self,
        price_sequence: List[float],
        prediction_horizon: int,
        return_confidence: bool
    ) -> Dict:
        """
        Predict using Lag-Llama (probabilistic model).
        
        Lag-Llama outputs a Student's t-distribution, so we can get confidence intervals.
        """
        try:
            # Convert price sequence to tensor format expected by model
            # Lag-Llama expects normalized data
            prices_array = np.array(price_sequence)
            
            # Normalize (zero mean, unit variance)
            mean = np.mean(prices_array)
            std = np.std(prices_array)
            normalized = (prices_array - mean) / (std + 1e-8)
            
            # Prepare input for model
            # Note: Actual implementation depends on Lag-Llama's API
            # This is a placeholder - you'll need to check Lag-Llama's documentation
            
            # For now, return a simple prediction with placeholder confidence
            # TODO: Implement actual Lag-Llama prediction logic
            
            logger.warning("Lag-Llama prediction not fully implemented - using baseline")
            return self._baseline_predict(price_sequence, prediction_horizon)
            
        except Exception as e:
            logger.error(f"Error in Lag-Llama prediction: {e}")
            return self._baseline_predict(price_sequence, prediction_horizon)
    
    def _predict_chronos_bolt(
        self,
        price_sequence: List[float],
        prediction_horizon: int
    ) -> Dict:
        """
        Predict using Chronos-Bolt (point prediction model).
        
        Chronos-Bolt is fast and outputs a single prediction.
        """
        try:
            # Convert price sequence to format expected by Chronos
            prices_array = np.array(price_sequence)
            
            # Normalize
            mean = np.mean(prices_array)
            std = np.std(prices_array)
            normalized = (prices_array - mean) / (std + 1e-8)
            
            # Prepare input for model
            # Note: Actual implementation depends on Chronos-Bolt's API
            # This is a placeholder - you'll need to check Chronos documentation
            
            # For now, return a simple prediction
            # TODO: Implement actual Chronos-Bolt prediction logic
            
            logger.warning("Chronos-Bolt prediction not fully implemented - using baseline")
            return self._baseline_predict(price_sequence, prediction_horizon)
            
        except Exception as e:
            logger.error(f"Error in Chronos-Bolt prediction: {e}")
            return self._baseline_predict(price_sequence, prediction_horizon)
    
    def _baseline_predict(
        self,
        price_sequence: List[float],
        prediction_horizon: int
    ) -> Dict:
        """
        Simple baseline predictor (momentum-based).
        
        Uses recent trend to predict future price.
        """
        if len(price_sequence) < 2:
            # Not enough data - return last price
            return {
                "predicted_price": price_sequence[-1],
                "model": "baseline"
            }
        
        # Simple momentum: use last 10 prices to estimate trend
        recent_prices = price_sequence[-10:]
        trend = (recent_prices[-1] - recent_prices[0]) / len(recent_prices)
        
        # Extrapolate trend
        current_price = price_sequence[-1]
        predicted_price = current_price + (trend * prediction_horizon)
        
        return {
            "predicted_price": predicted_price,
            "model": "baseline"
        }
    
    def predict_polymarket_outcome(
        self,
        price_sequence: List[float],
        current_price: float,
        prediction_horizon_minutes: int = 15
    ) -> Dict:
        """
        Predict Polymarket outcome (Up/Down) based on BTC price prediction.
        
        Args:
            price_sequence: Historical BTC prices
            current_price: Current BTC price (at market start)
            prediction_horizon_minutes: Minutes ahead to predict (default: 15)
        
        Returns:
            Dict with:
                - 'predicted_price': Predicted BTC price in 15 minutes
                - 'direction': 'up' or 'down'
                - 'confidence': Confidence level (0-1)
        """
        prediction = self.predict(price_sequence, prediction_horizon_minutes)
        predicted_price = prediction["predicted_price"]
        
        # Determine direction
        price_change = predicted_price - current_price
        direction = "up" if price_change > 0 else "down"
        
        # Calculate confidence (based on magnitude of predicted change)
        # Normalize to 0-1 range
        price_change_pct = abs(price_change / current_price) if current_price > 0 else 0
        confidence = min(price_change_pct * 10, 1.0)  # Scale to 0-1
        
        return {
            "predicted_price": predicted_price,
            "current_price": current_price,
            "predicted_change": price_change,
            "predicted_change_pct": price_change_pct * 100,
            "direction": direction,
            "confidence": confidence,
            "model": prediction.get("model", "unknown")
        }

