"""
Backtesting Framework for BTC Price Prediction Models.

Processes historical BTC 15-minute markets sequentially, runs predictions,
and calculates performance metrics.
"""
import logging
from typing import List, Dict, Optional, Callable
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

from agents.backtesting.market_fetcher import HistoricalMarketFetcher
from agents.connectors.btc_data import BTCDataFetcher
from agents.models.btc_predictor import BTCPredictor

logger = logging.getLogger(__name__)


class BTCBacktester:
    """
    Backtesting framework for BTC price prediction models.
    
    Processes markets sequentially to maximize cache efficiency.
    """
    
    def __init__(
        self,
        model_name: str = "baseline",
        lookback_minutes: int = 200,
        prediction_horizon_minutes: int = 15
    ):
        """
        Initialize backtester.
        
        Args:
            model_name: Model to use ('lag-llama', 'chronos-bolt', or 'baseline')
            lookback_minutes: Minutes of history to use for prediction (default: 200)
            prediction_horizon_minutes: Minutes ahead to predict (default: 15)
        """
        self.model_name = model_name
        self.lookback_minutes = lookback_minutes
        self.prediction_horizon_minutes = prediction_horizon_minutes
        
        # Initialize components
        self.market_fetcher = HistoricalMarketFetcher()
        self.btc_fetcher = BTCDataFetcher()
        
        # Initialize predictor (may fall back to baseline if model unavailable)
        try:
            self.predictor = BTCPredictor(model_name=model_name)
        except Exception as e:
            logger.warning(f"Could not load model {model_name}, using baseline: {e}")
            self.predictor = BTCPredictor(model_name="baseline")
        
        # Results storage
        self.results: List[Dict] = []
    
    def process_market(self, market: Dict) -> Optional[Dict]:
        """
        Process a single market: get prediction and compare to actual outcome.
        
        Args:
            market: Market dict with market information
            
        Returns:
            Result dict with prediction and metrics, or None if processing failed
        """
        try:
            market_start_time = market.get("_market_start_time")
            if not market_start_time:
                # Try to extract from timestamp
                timestamp = market.get("_market_start_timestamp")
                if timestamp:
                    market_start_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                else:
                    logger.warning(f"Market {market.get('id')} has no start time")
                    return None
            
            # Get BTC price sequence up to market start
            price_sequence = self.btc_fetcher.get_price_sequence(
                timestamp=market_start_time,
                lookback_minutes=self.lookback_minutes,
                interval='1m'
            )
            
            if len(price_sequence) < 10:
                logger.warning(f"Market {market.get('id')}: insufficient price data")
                return None
            
            # Get current price (at market start)
            current_price = price_sequence[-1]
            
            # Run prediction
            prediction = self.predictor.predict_polymarket_outcome(
                price_sequence=price_sequence,
                current_price=current_price,
                prediction_horizon_minutes=self.prediction_horizon_minutes
            )
            
            # Get actual outcome
            actual_direction = market.get("_btc_actual_direction")
            if not actual_direction:
                # Try to get from market outcome
                market_outcome = self.market_fetcher.get_market_outcome(market)
                if market_outcome:
                    actual_direction = market_outcome
            
            # Get actual price change
            actual_price_change = market.get("_btc_price_change")
            actual_price_change_pct = market.get("_btc_price_change_pct")
            
            # Calculate metrics
            predicted_direction = prediction.get("direction", "unknown")
            is_correct = (predicted_direction == actual_direction) if actual_direction else None
            
            # Calculate P&L (simplified: assume we bet $1 on predicted outcome)
            # If correct, we win based on odds; if wrong, we lose $1
            pnl = None
            if actual_direction and is_correct is not None:
                # Get outcome prices to calculate potential payout
                outcome_prices = market.get("outcomePrices")
                if isinstance(outcome_prices, str):
                    try:
                        import json
                        outcome_prices = json.loads(outcome_prices)
                    except:
                        outcome_prices = None
                
                if isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
                    # Find the price for our predicted outcome
                    # Convert to float if string
                    try:
                        if predicted_direction == "up":
                            bet_price = float(outcome_prices[0]) if outcome_prices[0] else 0.5
                        else:
                            bet_price = float(outcome_prices[1]) if outcome_prices[1] else 0.5
                        
                        # Ensure bet_price is valid
                        if bet_price <= 0 or bet_price > 1:
                            bet_price = 0.5  # Default to 0.5 if invalid
                        
                        # P&L calculation: if we bet $1 at price p, we get $1/p if we win, lose $1 if we lose
                        if is_correct:
                            pnl = (1.0 / bet_price) - 1.0  # Profit
                        else:
                            pnl = -1.0  # Loss
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Error calculating P&L: {e}")
                        pnl = None
            
            result = {
                "market_id": market.get("id"),
                "market_slug": market.get("_event_slug"),
                "market_start_time": market_start_time.isoformat() if market_start_time else None,
                "current_price": current_price,
                "predicted_price": prediction.get("predicted_price"),
                "predicted_direction": predicted_direction,
                "predicted_change": prediction.get("predicted_change"),
                "predicted_change_pct": prediction.get("predicted_change_pct"),
                "confidence": prediction.get("confidence", 0.0),
                "actual_direction": actual_direction,
                "actual_price_change": actual_price_change,
                "actual_price_change_pct": actual_price_change_pct,
                "is_correct": is_correct,
                "pnl": pnl,
                "model": prediction.get("model", self.model_name),
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error processing market {market.get('id')}: {e}", exc_info=True)
            return None
    
    def run_backtest(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_markets: Optional[int] = None,
        enrich_with_btc_data: bool = True
    ) -> pd.DataFrame:
        """
        Run backtest on historical markets.
        
        Args:
            start_date: Start date for markets (UTC)
            end_date: End date for markets (UTC)
            max_markets: Maximum number of markets to process
            enrich_with_btc_data: Whether to enrich markets with BTC price data
            
        Returns:
            DataFrame with backtest results
        """
        logger.info("=" * 70)
        logger.info("Starting BTC Backtest")
        logger.info("=" * 70)
        logger.info(f"Model: {self.model_name}")
        logger.info(f"Lookback: {self.lookback_minutes} minutes")
        logger.info(f"Prediction horizon: {self.prediction_horizon_minutes} minutes")
        
        # Fetch historical markets
        markets = self.market_fetcher.get_closed_btc_15m_markets(
            start_date=start_date,
            end_date=end_date,
            max_markets=max_markets
        )
        
        if not markets:
            logger.warning("No markets found for backtesting")
            return pd.DataFrame()
        
        logger.info(f"Found {len(markets)} markets to process")
        
        # Enrich markets with BTC data if requested
        if enrich_with_btc_data:
            logger.info("Enriching markets with BTC price data...")
            for i, market in enumerate(markets):
                markets[i] = self.market_fetcher.enrich_market_with_btc_data(market, self.btc_fetcher)
                if (i + 1) % 10 == 0:
                    logger.debug(f"Enriched {i + 1}/{len(markets)} markets...")
        
        # Process markets sequentially
        logger.info("Processing markets sequentially...")
        self.results = []
        
        for i, market in enumerate(markets):
            result = self.process_market(market)
            if result:
                self.results.append(result)
            
            if (i + 1) % 10 == 0:
                logger.info(f"Processed {i + 1}/{len(markets)} markets...")
        
        logger.info(f"✓ Processed {len(self.results)} markets successfully")
        
        # Convert to DataFrame
        if not self.results:
            return pd.DataFrame()
        
        df = pd.DataFrame(self.results)
        
        # Calculate aggregate metrics
        self._calculate_metrics(df)
        
        return df
    
    def _calculate_metrics(self, df: pd.DataFrame):
        """Calculate and log aggregate performance metrics."""
        if df.empty:
            return
        
        logger.info("\n" + "=" * 70)
        logger.info("Backtest Results Summary")
        logger.info("=" * 70)
        
        # Basic counts
        total_markets = len(df)
        markets_with_outcome = df[df["actual_direction"].notna()]
        markets_with_prediction = df[df["predicted_direction"].notna()]
        
        logger.info(f"\nTotal markets processed: {total_markets}")
        logger.info(f"Markets with actual outcome: {len(markets_with_outcome)}")
        logger.info(f"Markets with prediction: {len(markets_with_prediction)}")
        
        # Win rate
        correct_predictions = df[df["is_correct"] == True]
        incorrect_predictions = df[df["is_correct"] == False]
        
        if len(markets_with_outcome) > 0:
            win_rate = len(correct_predictions) / len(markets_with_outcome) * 100
            logger.info(f"\nWin Rate: {win_rate:.2f}% ({len(correct_predictions)}/{len(markets_with_outcome)})")
        
        # P&L metrics
        pnl_data = df[df["pnl"].notna()]["pnl"]
        if len(pnl_data) > 0:
            total_pnl = pnl_data.sum()
            avg_pnl = pnl_data.mean()
            sharpe_ratio = self._calculate_sharpe_ratio(pnl_data)
            profit_factor = self._calculate_profit_factor(pnl_data)
            
            logger.info(f"\nP&L Metrics:")
            logger.info(f"  Total P&L: ${total_pnl:.2f}")
            logger.info(f"  Average P&L per trade: ${avg_pnl:.4f}")
            logger.info(f"  Sharpe Ratio: {sharpe_ratio:.2f}")
            logger.info(f"  Profit Factor: {profit_factor:.2f}")
        
        # Prediction accuracy metrics
        if len(markets_with_outcome) > 0:
            predicted_changes = df[df["predicted_change"].notna()]["predicted_change"]
            actual_changes = df[df["actual_price_change"].notna()]["actual_price_change"]
            
            if len(predicted_changes) > 0 and len(actual_changes) > 0:
                # Mean Absolute Error
                mae = np.mean(np.abs(predicted_changes - actual_changes))
                logger.info(f"\nPrediction Accuracy:")
                logger.info(f"  Mean Absolute Error: ${mae:.2f}")
        
        # Confidence analysis
        if "confidence" in df.columns:
            high_confidence = df[df["confidence"] > 0.7]
            if len(high_confidence) > 0:
                high_conf_correct = high_confidence[high_confidence["is_correct"] == True]
                if len(high_confidence) > 0:
                    high_conf_win_rate = len(high_conf_correct) / len(high_confidence) * 100
                    logger.info(f"\nHigh Confidence (>0.7) Win Rate: {high_conf_win_rate:.2f}%")
        
        logger.info("\n" + "=" * 70)
    
    def _calculate_sharpe_ratio(self, returns: pd.Series, risk_free_rate: float = 0.0) -> float:
        """Calculate Sharpe ratio."""
        if len(returns) == 0 or returns.std() == 0:
            return 0.0
        excess_returns = returns - risk_free_rate
        return (excess_returns.mean() / returns.std()) * np.sqrt(252)  # Annualized
    
    def _calculate_profit_factor(self, pnl: pd.Series) -> float:
        """Calculate profit factor (gross profit / gross loss)."""
        profits = pnl[pnl > 0].sum()
        losses = abs(pnl[pnl < 0].sum())
        if losses == 0:
            return float('inf') if profits > 0 else 0.0
        return profits / losses
    
    def save_results(self, filepath: str):
        """Save backtest results to CSV."""
        if not self.results:
            logger.warning("No results to save")
            return
        
        df = pd.DataFrame(self.results)
        df.to_csv(filepath, index=False)
        logger.info(f"✓ Saved results to {filepath}")


def run_backtest(
    model_name: str = "baseline",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    max_markets: Optional[int] = None,
    output_file: Optional[str] = None
) -> pd.DataFrame:
    """
    Convenience function to run a backtest.
    
    Args:
        model_name: Model to use ('lag-llama', 'chronos-bolt', or 'baseline')
        start_date: Start date for markets (UTC)
        end_date: End date for markets (UTC)
        max_markets: Maximum number of markets to process
        output_file: Optional path to save results CSV
        
    Returns:
        DataFrame with backtest results
    """
    backtester = BTCBacktester(model_name=model_name)
    results_df = backtester.run_backtest(
        start_date=start_date,
        end_date=end_date,
        max_markets=max_markets
    )
    
    if output_file:
        backtester.save_results(output_file)
    
    return results_df

