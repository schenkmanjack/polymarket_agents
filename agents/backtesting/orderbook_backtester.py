"""
Orderbook-Based Backtesting Framework for BTC Price Prediction Models.

For each market with recorded orderbook data:
1. One minute before market start, predict BTC price
2. Use prediction to decide which side to bet on
3. Execute trade at orderbook price (buy N shares)
4. Calculate P&L based on actual outcome
5. Calculate metrics: win rate, average return, Sharpe ratio
"""
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

from agents.backtesting.market_fetcher import HistoricalMarketFetcher
from agents.connectors.btc_data import BTCDataFetcher
from agents.models.btc_predictor import BTCPredictor
from agents.polymarket.orderbook_db import OrderbookDatabase
from agents.polymarket.orderbook_query import OrderbookQuery

logger = logging.getLogger(__name__)


class OrderbookBacktester:
    """
    Orderbook-based backtesting framework.
    
    Only processes markets that have orderbook data recorded.
    Executes trades at orderbook prices 1 minute before market start.
    """
    
    def __init__(
        self,
        model_name: str = "baseline",
        lookback_minutes: int = 200,
        prediction_horizon_minutes: int = 15,
        shares_per_trade: float = 1.0,
        proxy: Optional[str] = None
    ):
        """
        Initialize orderbook backtester.
        
        Args:
            model_name: Model to use ('lag-llama', 'chronos-bolt', or 'baseline')
            lookback_minutes: Minutes of history to use for prediction (default: 200)
            prediction_horizon_minutes: Minutes ahead to predict (default: 15)
            shares_per_trade: Number of shares to buy per trade (default: 1.0)
            proxy: Optional proxy URL for VPN/routing
        """
        self.model_name = model_name
        self.lookback_minutes = lookback_minutes
        self.prediction_horizon_minutes = prediction_horizon_minutes
        self.shares_per_trade = shares_per_trade
        
        # Initialize components
        self.market_fetcher = HistoricalMarketFetcher(proxy=proxy)
        self.btc_fetcher = BTCDataFetcher(proxy=proxy)
        
        # Initialize orderbook database (uses btc_eth_table)
        self.orderbook_db = OrderbookDatabase(use_btc_eth_table=True)
        self.orderbook_query = OrderbookQuery(db=self.orderbook_db)
        
        # Initialize predictor
        try:
            self.predictor = BTCPredictor(model_name=model_name)
        except Exception as e:
            logger.warning(f"Could not load model {model_name}, using baseline: {e}")
            self.predictor = BTCPredictor(model_name="baseline")
        
        # Results storage
        self.results: List[Dict] = []
    
    def get_markets_with_orderbooks(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_markets: Optional[int] = None
    ) -> List[Dict]:
        """
        Get markets that have orderbook data recorded.
        
        Returns:
            List of market dicts with orderbook data
        """
        # Query markets directly from database to avoid limit issues
        from sqlalchemy import text
        
        db = self.orderbook_db
        markets_by_id = {}
        
        with db.get_session() as session:
            # Build query to get distinct markets with their date ranges
            query = """
                SELECT market_id,
                       COUNT(*) as snapshot_count,
                       MIN(timestamp) as first_snapshot,
                       MAX(timestamp) as last_snapshot,
                       COUNT(DISTINCT token_id) as token_count
                FROM btc_eth_table
            """
            
            conditions = []
            if start_date:
                conditions.append(f"timestamp >= '{start_date.isoformat()}'")
            if end_date:
                conditions.append(f"timestamp <= '{end_date.isoformat()}'")
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            
            query += " GROUP BY market_id ORDER BY first_snapshot"
            
            result = session.execute(text(query))
            
            for row in result:
                market_id = str(row[0])
                snapshot_count = row[1]
                first_snapshot = row[2]
                last_snapshot = row[3]
                token_count = row[4]
                
                markets_by_id[market_id] = {
                    "id": market_id,
                    "token_ids": set(),  # Will be populated when we fetch snapshots
                    "snapshots": [],  # Will be populated when needed
                    "first_snapshot": first_snapshot,
                    "last_snapshot": last_snapshot,
                    "_snapshot_count": snapshot_count,
                    "_token_count": token_count,
                }
        
        if not markets_by_id:
            logger.warning("No markets with orderbook data found in database")
            return []
        
        # Get token IDs for each market (sample from snapshots)
        # We don't need all snapshots, just enough to get token IDs
        for market_id in list(markets_by_id.keys()):
            market_snapshots = self.orderbook_query.get_snapshots(
                market_id=market_id,
                limit=100  # Just enough to get token IDs
            )
            if market_snapshots:
                markets_by_id[market_id]["token_ids"] = set(s.token_id for s in market_snapshots)
                markets_by_id[market_id]["snapshots"] = market_snapshots  # Store sample snapshots
        
        # Convert to list and enrich with market data
        markets = []
        for market_id, market_data in markets_by_id.items():
            # Try to get market details from API
            try:
                # Try to fetch market from API
                import httpx
                url = f"{self.market_fetcher.gamma_markets_endpoint}/{market_id}"
                response = httpx.get(url, proxies=self.market_fetcher.proxy, timeout=10.0)
                if response.status_code == 200:
                    market_info = response.json()
                    if isinstance(market_info, list) and len(market_info) > 0:
                        market_info = market_info[0]
                    market_data.update(market_info)
            except Exception as e:
                logger.debug(f"Could not fetch market {market_id} details: {e}")
            
            # Extract market start time from snapshots (assume first snapshot is near market start)
            market_data["_market_start_time"] = market_data["first_snapshot"]
            market_data["token_ids"] = list(market_data["token_ids"])
            
            markets.append(market_data)
        
        # Sort by market start time
        markets.sort(key=lambda m: m.get("_market_start_time", datetime.min.replace(tzinfo=timezone.utc)))
        
        # Filter by date range if specified
        if start_date:
            markets = [m for m in markets if m.get("_market_start_time", datetime.min.replace(tzinfo=timezone.utc)) >= start_date]
        if end_date:
            markets = [m for m in markets if m.get("_market_start_time", datetime.min.replace(tzinfo=timezone.utc)) <= end_date]
        
        # Limit if specified
        if max_markets:
            markets = markets[:max_markets]
        
        logger.info(f"Found {len(markets)} markets with orderbook data")
        return markets
    
    def process_market(self, market: Dict) -> Optional[Dict]:
        """
        Process a single market with orderbook data.
        
        1. Get orderbook snapshot 1 minute before market start
        2. Predict BTC price
        3. Decide which side to bet on
        4. Execute trade at orderbook price
        5. Calculate P&L based on actual outcome
        
        Args:
            market: Market dict with orderbook data
            
        Returns:
            Result dict with trade and metrics, or None if processing failed
        """
        try:
            market_id = market.get("id")
            market_start_time = market.get("_market_start_time")
            
            if not market_start_time:
                logger.warning(f"Market {market_id} has no start time")
                return None
            
            # Get orderbook snapshot 1 minute before market start
            prediction_time = market_start_time - timedelta(minutes=1)
            
            # Get token IDs for this market
            token_ids = market.get("token_ids", [])
            if not token_ids:
                logger.warning(f"Market {market_id} has no token IDs")
                return None
            
            # Get orderbook snapshot closest to prediction time
            orderbook_snapshot = None
            for token_id in token_ids:
                snapshot = self.orderbook_query.get_orderbook_at_time(
                    token_id=token_id,
                    target_time=prediction_time,
                    tolerance_seconds=120  # 2 minute tolerance
                )
                if snapshot:
                    orderbook_snapshot = snapshot
                    break
            
            if not orderbook_snapshot:
                logger.debug(f"Market {market_id}: No orderbook snapshot found near {prediction_time}")
                return None
            
            # Get BTC price sequence up to prediction time
            price_sequence = self.btc_fetcher.get_price_sequence(
                timestamp=prediction_time,
                lookback_minutes=self.lookback_minutes,
                interval='1m'
            )
            
            if len(price_sequence) < 10:
                logger.warning(f"Market {market_id}: insufficient price data")
                return None
            
            # Get current price at prediction time
            current_price = price_sequence[-1]
            
            # Run prediction
            prediction = self.predictor.predict_polymarket_outcome(
                price_sequence=price_sequence,
                current_price=current_price,
                prediction_horizon_minutes=self.prediction_horizon_minutes
            )
            
            predicted_direction = prediction.get("direction", "unknown")
            predicted_price = prediction.get("predicted_price")
            
            if predicted_direction == "unknown":
                logger.debug(f"Market {market_id}: Could not determine prediction direction")
                return None
            
            # Determine which token to buy (YES for up, NO for down)
            # For BTC up/down markets: token[0] is usually YES (up), token[1] is NO (down)
            # We need to figure out which token corresponds to our prediction
            buy_token_id = None
            buy_price = None
            
            # Get orderbook prices for both tokens
            token_orderbooks = {}
            for token_id in token_ids:
                snapshot = self.orderbook_query.get_orderbook_at_time(
                    token_id=token_id,
                    target_time=prediction_time,
                    tolerance_seconds=120
                )
                if snapshot and snapshot.best_ask_price:
                    token_orderbooks[token_id] = snapshot.best_ask_price
            
            # For simplicity, assume first token is YES (up) and second is NO (down)
            # In production, you'd check the market metadata to determine this
            if len(token_ids) >= 2:
                if predicted_direction == "up":
                    # Buy YES token (first token)
                    buy_token_id = token_ids[0]
                    buy_price = token_orderbooks.get(token_ids[0])
                else:
                    # Buy NO token (second token)
                    buy_token_id = token_ids[1] if len(token_ids) > 1 else token_ids[0]
                    buy_price = token_orderbooks.get(buy_token_id)
            else:
                # Fallback: use first token
                buy_token_id = token_ids[0]
                buy_price = token_orderbooks.get(token_ids[0])
            
            if not buy_price:
                logger.warning(f"Market {market_id}: No orderbook price available")
                return None
            
            # Execute trade: buy N shares at orderbook price
            trade_cost = self.shares_per_trade * buy_price
            
            # Get actual outcome
            # Try to enrich market with BTC data to get actual outcome
            actual_direction = None
            try:
                enriched_market = self.market_fetcher.enrich_market_with_btc_data(market, self.btc_fetcher)
                actual_direction = enriched_market.get("_btc_actual_direction")
            except Exception as e:
                logger.debug(f"Market {market_id}: Error enriching with BTC data: {e}")
            
            # Fallback: try to get outcome from market data directly
            if not actual_direction:
                # Check outcomePrices directly - this tells us which outcome won
                outcome_prices = market.get("outcomePrices")
                if outcome_prices:
                    # Handle both string and list formats
                    if isinstance(outcome_prices, str):
                        try:
                            import json
                            outcome_prices = json.loads(outcome_prices)
                        except:
                            pass
                    
                    if isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
                        # If first outcome price is 1.0 (or close), up won; if second is 1.0, down won
                        try:
                            price0 = float(outcome_prices[0])
                            price1 = float(outcome_prices[1])
                            if price0 >= 0.99:
                                actual_direction = "up"
                            elif price1 >= 0.99:
                                actual_direction = "down"
                        except (ValueError, TypeError) as e:
                            logger.debug(f"Market {market_id}: Error parsing outcomePrices: {e}")
            
            if not actual_direction:
                logger.debug(f"Market {market_id}: Could not determine actual outcome")
                return None
            
            # Calculate P&L
            # If we predicted correctly, we win; if wrong, we lose
            is_correct = (predicted_direction == actual_direction)
            
            # P&L calculation:
            # - If correct: payout is 1.0 per share (market resolves to $1)
            # - If wrong: payout is 0.0 per share (market resolves to $0)
            # - Cost: shares * buy_price
            # - P&L = (payout * shares) - cost
            if is_correct:
                payout = 1.0 * self.shares_per_trade
                pnl = payout - trade_cost
                return_pct = (pnl / trade_cost) * 100 if trade_cost > 0 else 0.0
            else:
                payout = 0.0
                pnl = -trade_cost  # Lose the entire cost
                return_pct = -100.0
            
            result = {
                "market_id": market_id,
                "market_start_time": market_start_time.isoformat(),
                "prediction_time": prediction_time.isoformat(),
                "current_price": current_price,
                "predicted_price": predicted_price,
                "predicted_direction": predicted_direction,
                "buy_token_id": buy_token_id,
                "buy_price": buy_price,
                "shares": self.shares_per_trade,
                "trade_cost": trade_cost,
                "actual_direction": actual_direction,
                "is_correct": is_correct,
                "pnl": pnl,
                "return_pct": return_pct,
                "payout": payout,
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
        max_markets: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Run orderbook-based backtest.
        
        Args:
            start_date: Start date for markets (UTC)
            end_date: End date for markets (UTC)
            max_markets: Maximum number of markets to process
            
        Returns:
            DataFrame with backtest results
        """
        logger.info("=" * 70)
        logger.info("Starting Orderbook-Based BTC Backtest")
        logger.info("=" * 70)
        logger.info(f"Model: {self.model_name}")
        logger.info(f"Lookback: {self.lookback_minutes} minutes")
        logger.info(f"Prediction horizon: {self.prediction_horizon_minutes} minutes")
        logger.info(f"Shares per trade: {self.shares_per_trade}")
        
        # Get markets with orderbook data
        markets = self.get_markets_with_orderbooks(
            start_date=start_date,
            end_date=end_date,
            max_markets=max_markets
        )
        
        if not markets:
            logger.warning("No markets with orderbook data found")
            return pd.DataFrame()
        
        logger.info(f"Processing {len(markets)} markets...")
        
        # Process markets sequentially
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
        
        total_trades = len(df)
        winning_trades = df[df["is_correct"] == True]
        losing_trades = df[df["is_correct"] == False]
        
        # Win rate
        win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0.0
        logger.info(f"\nWin Rate: {win_rate:.2f}% ({len(winning_trades)}/{total_trades})")
        
        # P&L metrics
        total_pnl = df["pnl"].sum()
        avg_pnl = df["pnl"].mean()
        total_cost = df["trade_cost"].sum()
        
        logger.info(f"\nP&L Metrics:")
        logger.info(f"  Total P&L: ${total_pnl:.2f}")
        logger.info(f"  Total Cost: ${total_cost:.2f}")
        logger.info(f"  Net Return: ${total_pnl:.2f}")
        logger.info(f"  Average P&L per trade: ${avg_pnl:.4f}")
        
        # Return metrics
        returns = df["return_pct"]
        avg_return = returns.mean()
        logger.info(f"\nReturn Metrics:")
        logger.info(f"  Average Return: {avg_return:.2f}%")
        logger.info(f"  Total Return: {(total_pnl / total_cost * 100) if total_cost > 0 else 0:.2f}%")
        
        # Sharpe ratio
        sharpe_ratio = self._calculate_sharpe_ratio(returns)
        logger.info(f"  Sharpe Ratio: {sharpe_ratio:.2f}")
        
        # Additional metrics
        if len(winning_trades) > 0:
            avg_win = winning_trades["pnl"].mean()
            logger.info(f"\nWinning Trades:")
            logger.info(f"  Average Win: ${avg_win:.4f}")
            logger.info(f"  Average Win Return: {winning_trades['return_pct'].mean():.2f}%")
        
        if len(losing_trades) > 0:
            avg_loss = losing_trades["pnl"].mean()
            logger.info(f"\nLosing Trades:")
            logger.info(f"  Average Loss: ${avg_loss:.4f}")
            logger.info(f"  Average Loss Return: {losing_trades['return_pct'].mean():.2f}%")
        
        logger.info("\n" + "=" * 70)
    
    def _calculate_sharpe_ratio(self, returns: pd.Series, risk_free_rate: float = 0.0) -> float:
        """Calculate Sharpe ratio from returns."""
        if len(returns) == 0 or returns.std() == 0:
            return 0.0
        
        excess_returns = returns - risk_free_rate
        # Annualize: assume ~96 trades per day (15-min markets) * 365 days
        # But for simplicity, use per-trade Sharpe and annualize
        annualized_sharpe = (excess_returns.mean() / returns.std()) * np.sqrt(96 * 365)
        return annualized_sharpe
    
    def save_results(self, filepath: str):
        """Save backtest results to CSV."""
        if not self.results:
            logger.warning("No results to save")
            return
        
        df = pd.DataFrame(self.results)
        df.to_csv(filepath, index=False)
        logger.info(f"✓ Saved results to {filepath}")


def run_orderbook_backtest(
    model_name: str = "baseline",
    shares_per_trade: float = 1.0,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    max_markets: Optional[int] = None,
    output_file: Optional[str] = None
) -> pd.DataFrame:
    """
    Convenience function to run orderbook-based backtest.
    
    Args:
        model_name: Model to use ('lag-llama', 'chronos-bolt', or 'baseline')
        shares_per_trade: Number of shares to buy per trade
        start_date: Start date for markets (UTC)
        end_date: End date for markets (UTC)
        max_markets: Maximum number of markets to process
        output_file: Optional path to save results CSV
        
    Returns:
        DataFrame with backtest results
    """
    backtester = OrderbookBacktester(
        model_name=model_name,
        shares_per_trade=shares_per_trade
    )
    results_df = backtester.run_backtest(
        start_date=start_date,
        end_date=end_date,
        max_markets=max_markets
    )
    
    if output_file:
        backtester.save_results(output_file)
    
    return results_df

