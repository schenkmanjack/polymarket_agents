"""
Threshold-Based Backtesting Strategy.

Strategy: When one side (YES or NO) reaches a threshold (e.g., 90%),
place a limit buy order at threshold + margin (e.g., 94%).

Grid search over:
- Threshold: 60% to 100% (increments of 1%)
- Margin: 1% to (99% - threshold)%

Metrics: ROI, Sharpe ratio, wins/losses
"""
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

from agents.backtesting.market_fetcher import HistoricalMarketFetcher
from agents.polymarket.orderbook_db import OrderbookDatabase
from agents.polymarket.orderbook_query import OrderbookQuery

logger = logging.getLogger(__name__)


class ThresholdBacktester:
    """
    Backtest threshold-based strategy: buy when one side reaches threshold.
    
    For each market:
    1. Monitor YES and NO best_ask prices at every snapshot
    2. When one side reaches threshold, place limit buy at threshold + margin
    3. Check if limit order would fill (limit_price >= best_ask at some point)
    4. Calculate ROI based on outcome prices
    """
    
    def __init__(self, proxy: Optional[str] = None):
        """Initialize threshold backtester."""
        self.market_fetcher = HistoricalMarketFetcher(proxy=proxy)
        self.orderbook_db = OrderbookDatabase(use_btc_eth_table=True)
        self.orderbook_query = OrderbookQuery(db=self.orderbook_db)
    
    def get_markets_with_orderbooks(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_markets: Optional[int] = None
    ) -> List[Dict]:
        """Get markets that have orderbook data recorded."""
        from sqlalchemy import text
        
        db = self.orderbook_db
        markets_by_id = {}
        
        with db.get_session() as session:
            query = """
                SELECT market_id,
                       COUNT(*) as snapshot_count,
                       MIN(timestamp) as first_snapshot,
                       MAX(timestamp) as last_snapshot
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
                
                markets_by_id[market_id] = {
                    "id": market_id,
                    "first_snapshot": first_snapshot,
                    "last_snapshot": last_snapshot,
                    "_snapshot_count": snapshot_count,
                }
        
        if not markets_by_id:
            logger.warning("No markets with orderbook data found in database")
            return []
        
        # Enrich with market data from API
        markets = []
        for market_id, market_data in markets_by_id.items():
            try:
                import httpx
                from agents.utils.proxy_config import get_proxy_dict
                url = f"{self.market_fetcher.gamma_markets_endpoint}/{market_id}"
                proxies = get_proxy_dict()
                response = httpx.get(url, proxies=proxies, timeout=10.0)
                if response.status_code == 200:
                    market_info = response.json()
                    if isinstance(market_info, list) and len(market_info) > 0:
                        market_info = market_info[0]
                    market_data.update(market_info)
            except Exception as e:
                logger.debug(f"Could not fetch market {market_id} details: {e}")
            
            market_data["_market_start_time"] = market_data["first_snapshot"]
            markets.append(market_data)
        
        markets.sort(key=lambda m: m.get("_market_start_time", datetime.min.replace(tzinfo=timezone.utc)))
        
        if start_date:
            markets = [m for m in markets if m.get("_market_start_time", datetime.min.replace(tzinfo=timezone.utc)) >= start_date]
        if end_date:
            markets = [m for m in markets if m.get("_market_start_time", datetime.min.replace(tzinfo=timezone.utc)) <= end_date]
        if max_markets:
            markets = markets[:max_markets]
        
        logger.info(f"Found {len(markets)} markets with orderbook data")
        return markets
    
    def process_market(
        self,
        market: Dict,
        threshold: float,
        margin: float
    ) -> Optional[Dict]:
        """
        Process a single market with threshold strategy.
        
        Args:
            market: Market dict with id, token_ids, etc.
            threshold: Threshold percentage (0.60 to 1.00)
            margin: Margin percentage to add to threshold (0.01 to 0.99-threshold)
        
        Returns:
            Dict with trade results or None if no trade executed
        """
        market_id = market.get("id")
        if not market_id:
            return None
        
        # Get market active period (startDate to endDate)
        market_start = None
        market_end = None
        
        start_date_str = market.get("startDate") or market.get("startDateIso")
        end_date_str = market.get("endDate") or market.get("endDateIso")
        
        if start_date_str:
            try:
                if isinstance(start_date_str, str):
                    start_str = start_date_str.replace("Z", "+00:00")
                    market_start = datetime.fromisoformat(start_str)
                    if market_start.tzinfo is None:
                        market_start = market_start.replace(tzinfo=timezone.utc)
            except Exception as e:
                logger.debug(f"Could not parse startDate: {e}")
        
        if end_date_str:
            try:
                if isinstance(end_date_str, str):
                    end_str = end_date_str.replace("Z", "+00:00")
                    market_end = datetime.fromisoformat(end_str)
                    if market_end.tzinfo is None:
                        market_end = market_end.replace(tzinfo=timezone.utc)
            except Exception as e:
                logger.debug(f"Could not parse endDate: {e}")
        
        # Get all snapshots for this market, sorted by timestamp
        snapshots = self.orderbook_query.get_snapshots(
            market_id=market_id,
            start_time=market_start,  # Only get snapshots from market start
            end_time=market_end,  # Only get snapshots until market end
            limit=100000  # Get all snapshots
        )
        
        if not snapshots:
            return None
        
        # Sort by timestamp
        snapshots.sort(key=lambda s: s.timestamp)
        
        # Additional filter: only use snapshots during active period
        if market_start and market_end:
            filtered_snapshots = []
            for s in snapshots:
                snapshot_time = s.timestamp
                # Ensure timezone-aware
                if snapshot_time.tzinfo is None:
                    snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)
                if market_start <= snapshot_time <= market_end:
                    filtered_snapshots.append(s)
            snapshots = filtered_snapshots
        
        if not snapshots:
            logger.debug(f"Market {market_id}: No snapshots during active period")
            return None
        
        # Group snapshots by outcome (Outcome 1 = YES/up, Outcome 2 = NO/down)
        outcome1_snapshots = []
        outcome2_snapshots = []
        
        for snapshot in snapshots:
            outcome = snapshot.outcome or ""
            outcome_lower = outcome.lower()
            if "outcome 1" in outcome_lower or outcome == "1":
                outcome1_snapshots.append(snapshot)
            elif "outcome 2" in outcome_lower or outcome == "2":
                outcome2_snapshots.append(snapshot)
            elif "yes" in outcome_lower:
                outcome1_snapshots.append(snapshot)  # Fallback: YES = Outcome 1
            elif "no" in outcome_lower:
                outcome2_snapshots.append(snapshot)  # Fallback: NO = Outcome 2
        
        if not outcome1_snapshots or not outcome2_snapshots:
            logger.debug(f"Market {market_id}: Missing Outcome 1 or Outcome 2 snapshots")
            return None
        
        # For BTC markets: Outcome 1 = YES (up), Outcome 2 = NO (down)
        yes_snapshots = outcome1_snapshots
        no_snapshots = outcome2_snapshots
        
        # Monitor both sides: check when threshold is reached
        trigger_side = None
        trigger_time = None
        trigger_price = None
        
        # Check YES side
        for snapshot in yes_snapshots:
            if snapshot.best_ask_price and snapshot.best_ask_price >= threshold:
                trigger_side = "YES"
                trigger_time = snapshot.timestamp
                trigger_price = snapshot.best_ask_price
                break
        
        # Check NO side (only if YES didn't trigger)
        if trigger_side is None:
            for snapshot in no_snapshots:
                if snapshot.best_ask_price and snapshot.best_ask_price >= threshold:
                    trigger_side = "NO"
                    trigger_time = snapshot.timestamp
                    trigger_price = snapshot.best_ask_price
                    break
        
        if trigger_side is None:
            return None  # Threshold never reached
        
        # Calculate limit order price
        limit_price = threshold + margin
        if limit_price > 0.99:
            limit_price = 0.99  # Cap at 99%
        
        # Determine which token to buy
        if trigger_side == "YES":
            buy_token_snapshots = yes_snapshots
        else:
            buy_token_snapshots = no_snapshots
        
        # Check if limit order would fill
        # For low margins, use a time window (e.g., 1 minute) to be more realistic
        # For high margins, order likely fills immediately
        order_filled = False
        fill_time = None
        
        # Use time window based on margin size
        # Low margin (< 2%): require fill within 1 minute
        # High margin (>= 2%): allow any time after trigger
        if margin < 0.02:
            fill_window = timedelta(minutes=1)
        else:
            fill_window = timedelta(days=365)  # Effectively "any time"
        
        fill_deadline = trigger_time + fill_window
        
        for snapshot in buy_token_snapshots:
            if snapshot.timestamp < trigger_time:
                continue  # Only check after trigger
            
            if snapshot.timestamp > fill_deadline:
                break  # Past fill window
            
            if snapshot.best_ask_price and snapshot.best_ask_price <= limit_price:
                order_filled = True
                fill_time = snapshot.timestamp
                break
        
        if not order_filled:
            return None  # Order never filled
        
        # Get outcome prices (can be dict, list, or JSON string)
        outcome_prices_raw = market.get("outcomePrices", {})
        if not outcome_prices_raw:
            # Try to fetch from API
            try:
                import httpx
                from agents.utils.proxy_config import get_proxy_dict
                url = f"{self.market_fetcher.gamma_markets_endpoint}/{market_id}"
                proxies = get_proxy_dict()
                response = httpx.get(url, proxies=proxies, timeout=10.0)
                if response.status_code == 200:
                    market_info = response.json()
                    if isinstance(market_info, list) and len(market_info) > 0:
                        market_info = market_info[0]
                    outcome_prices_raw = market_info.get("outcomePrices", {})
            except Exception as e:
                logger.debug(f"Could not fetch outcome prices for market {market_id}: {e}")
        
        if not outcome_prices_raw:
            return None
        
        # Parse if it's a JSON string
        if isinstance(outcome_prices_raw, str):
            try:
                import json
                outcome_prices_raw = json.loads(outcome_prices_raw)
            except (json.JSONDecodeError, ValueError):
                logger.debug(f"Could not parse outcomePrices JSON string: {outcome_prices_raw}")
                return None
        
        # Parse outcome prices (can be list ["0", "1"] or dict {"Yes": 1, "No": 0})
        outcome_price = 0.0
        if isinstance(outcome_prices_raw, list) and len(outcome_prices_raw) >= 2:
            # List format: [outcome1_price, outcome2_price]
            # Outcome 1 = YES, Outcome 2 = NO
            if trigger_side == "YES":
                try:
                    outcome_price = float(outcome_prices_raw[0])
                except (ValueError, TypeError):
                    outcome_price = 0.0
            else:  # NO
                try:
                    outcome_price = float(outcome_prices_raw[1])
                except (ValueError, TypeError):
                    outcome_price = 0.0
        elif isinstance(outcome_prices_raw, dict):
            # Dict format: {"Yes": 1, "No": 0}
            if trigger_side == "YES":
                outcome_price = outcome_prices_raw.get("Yes", 0.0)
            else:
                outcome_price = outcome_prices_raw.get("No", 0.0)
        else:
            return None
        
        # ROI = (outcome_price - limit_price) / limit_price
        roi = (outcome_price - limit_price) / limit_price if limit_price > 0 else 0.0
        
        # Determine win/loss
        is_win = roi > 0
        
        return {
            "market_id": market_id,
            "threshold": threshold,
            "margin": margin,
            "trigger_side": trigger_side,
            "trigger_time": trigger_time,
            "trigger_price": trigger_price,
            "limit_price": limit_price,
            "fill_time": fill_time,
            "outcome_price": outcome_price,
            "roi": roi,
            "is_win": is_win,
        }
    
    def _preprocess_market_snapshots(self, market: Dict) -> Optional[Dict]:
        """
        Pre-process market snapshots once and cache them.
        
        Returns:
            Dict with yes_snapshots, no_snapshots, outcome_prices, or None if invalid
        """
        market_id = market.get("id")
        if not market_id:
            return None
        
        # Get market active period (startDate to endDate)
        market_start = None
        market_end = None
        
        start_date_str = market.get("startDate") or market.get("startDateIso")
        end_date_str = market.get("endDate") or market.get("endDateIso")
        
        if start_date_str:
            try:
                if isinstance(start_date_str, str):
                    start_str = start_date_str.replace("Z", "+00:00")
                    market_start = datetime.fromisoformat(start_str)
                    if market_start.tzinfo is None:
                        market_start = market_start.replace(tzinfo=timezone.utc)
            except Exception as e:
                logger.debug(f"Could not parse startDate: {e}")
        
        if end_date_str:
            try:
                if isinstance(end_date_str, str):
                    end_str = end_date_str.replace("Z", "+00:00")
                    market_end = datetime.fromisoformat(end_str)
                    if market_end.tzinfo is None:
                        market_end = market_end.replace(tzinfo=timezone.utc)
            except Exception as e:
                logger.debug(f"Could not parse endDate: {e}")
        
        # Get all snapshots for this market (only during active period)
        snapshots = self.orderbook_query.get_snapshots(
            market_id=market_id,
            start_time=market_start,  # Only get snapshots from market start
            end_time=market_end,  # Only get snapshots until market end
            limit=100000
        )
        
        if not snapshots:
            return None
        
        # Sort by timestamp
        snapshots.sort(key=lambda s: s.timestamp)
        
        # Additional filter: only use snapshots during active period
        if market_start and market_end:
            filtered_snapshots = []
            for s in snapshots:
                snapshot_time = s.timestamp
                # Ensure timezone-aware
                if snapshot_time.tzinfo is None:
                    snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)
                if market_start <= snapshot_time <= market_end:
                    filtered_snapshots.append(s)
            snapshots = filtered_snapshots
        
        if not snapshots:
            return None
        
        # Group snapshots by outcome (Outcome 1 = YES/up, Outcome 2 = NO/down)
        outcome1_snapshots = []
        outcome2_snapshots = []
        
        for snapshot in snapshots:
            outcome = snapshot.outcome or ""
            outcome_lower = outcome.lower()
            if "outcome 1" in outcome_lower or outcome == "1":
                outcome1_snapshots.append(snapshot)
            elif "outcome 2" in outcome_lower or outcome == "2":
                outcome2_snapshots.append(snapshot)
            elif "yes" in outcome_lower:
                outcome1_snapshots.append(snapshot)  # Fallback: YES = Outcome 1
            elif "no" in outcome_lower:
                outcome2_snapshots.append(snapshot)  # Fallback: NO = Outcome 2
        
        if not outcome1_snapshots or not outcome2_snapshots:
            return None
        
        # For BTC markets: Outcome 1 = YES (up), Outcome 2 = NO (down)
        yes_snapshots = outcome1_snapshots
        no_snapshots = outcome2_snapshots
        
        # Get outcome prices (can be dict, list, or JSON string)
        outcome_prices_raw = market.get("outcomePrices", {})
        if not outcome_prices_raw:
            # Try to fetch from API
            try:
                import httpx
                from agents.utils.proxy_config import get_proxy_dict
                url = f"{self.market_fetcher.gamma_markets_endpoint}/{market_id}"
                proxies = get_proxy_dict()
                response = httpx.get(url, proxies=proxies, timeout=10.0)
                if response.status_code == 200:
                    market_info = response.json()
                    if isinstance(market_info, list) and len(market_info) > 0:
                        market_info = market_info[0]
                    outcome_prices_raw = market_info.get("outcomePrices", {})
            except Exception as e:
                logger.debug(f"Could not fetch outcome prices for market {market_id}: {e}")
        
        # Parse if it's a JSON string
        if isinstance(outcome_prices_raw, str):
            try:
                import json
                outcome_prices_raw = json.loads(outcome_prices_raw)
            except (json.JSONDecodeError, ValueError):
                logger.debug(f"Could not parse outcomePrices JSON string: {outcome_prices_raw}")
                outcome_prices_raw = None
        
        return {
            "market_id": market_id,
            "yes_snapshots": yes_snapshots,
            "no_snapshots": no_snapshots,
            "outcome_prices": outcome_prices_raw,  # Store raw, will parse in process_market_with_snapshots
        }
    
    def process_market_with_snapshots(
        self,
        market_data: Dict,
        threshold: float,
        margin: float
    ) -> Optional[Dict]:
        """
        Process market with pre-fetched snapshots (faster for grid search).
        
        Args:
            market_data: Pre-processed market data from _preprocess_market_snapshots
            threshold: Threshold percentage
            margin: Margin percentage
        
        Returns:
            Dict with trade results or None
        """
        yes_snapshots = market_data["yes_snapshots"]
        no_snapshots = market_data["no_snapshots"]
        outcome_prices_raw = market_data["outcome_prices"]
        market_id = market_data["market_id"]
        
        if not outcome_prices_raw:
            return None
        
        # Monitor both sides: check when threshold is reached
        trigger_side = None
        trigger_time = None
        trigger_price = None
        
        # Check YES side
        for snapshot in yes_snapshots:
            if snapshot.best_ask_price and snapshot.best_ask_price >= threshold:
                trigger_side = "YES"
                trigger_time = snapshot.timestamp
                trigger_price = snapshot.best_ask_price
                break
        
        # Check NO side (only if YES didn't trigger)
        if trigger_side is None:
            for snapshot in no_snapshots:
                if snapshot.best_ask_price and snapshot.best_ask_price >= threshold:
                    trigger_side = "NO"
                    trigger_time = snapshot.timestamp
                    trigger_price = snapshot.best_ask_price
                    break
        
        if trigger_side is None:
            return None  # Threshold never reached
        
        # Calculate limit order price
        limit_price = threshold + margin
        if limit_price > 0.99:
            limit_price = 0.99
        
        # Determine which token to buy
        if trigger_side == "YES":
            buy_token_snapshots = yes_snapshots
        else:
            buy_token_snapshots = no_snapshots
        
        # Check if limit order would fill
        order_filled = False
        fill_time = None
        
        if margin < 0.02:
            fill_window = timedelta(minutes=1)
        else:
            fill_window = timedelta(days=365)
        
        fill_deadline = trigger_time + fill_window
        
        for snapshot in buy_token_snapshots:
            if snapshot.timestamp < trigger_time:
                continue
            
            if snapshot.timestamp > fill_deadline:
                break
            
            if snapshot.best_ask_price and snapshot.best_ask_price <= limit_price:
                order_filled = True
                fill_time = snapshot.timestamp
                break
        
        if not order_filled:
            return None
        
        # Parse outcome prices based on trigger side
        outcome_price = 0.0
        if isinstance(outcome_prices_raw, list) and len(outcome_prices_raw) >= 2:
            # List format: [outcome1_price, outcome2_price]
            # Outcome 1 = YES, Outcome 2 = NO
            if trigger_side == "YES":
                try:
                    outcome_price = float(outcome_prices_raw[0])
                except (ValueError, TypeError):
                    outcome_price = 0.0
            else:  # NO
                try:
                    outcome_price = float(outcome_prices_raw[1])
                except (ValueError, TypeError):
                    outcome_price = 0.0
        elif isinstance(outcome_prices_raw, dict):
            # Dict format: {"Yes": 1, "No": 0}
            if trigger_side == "YES":
                outcome_price = outcome_prices_raw.get("Yes", 0.0)
            else:
                outcome_price = outcome_prices_raw.get("No", 0.0)
        else:
            return None
        
        roi = (outcome_price - limit_price) / limit_price if limit_price > 0 else 0.0
        is_win = roi > 0
        
        return {
            "market_id": market_id,
            "threshold": threshold,
            "margin": margin,
            "trigger_side": trigger_side,
            "trigger_time": trigger_time,
            "trigger_price": trigger_price,
            "limit_price": limit_price,
            "fill_time": fill_time,
            "outcome_price": outcome_price,
            "roi": roi,
            "is_win": is_win,
        }
    
    def run_grid_search(
        self,
        markets: List[Dict],
        threshold_min: float = 0.60,
        threshold_max: float = 1.00,
        threshold_step: float = 0.01,
        margin_min: float = 0.01,
        margin_max: Optional[float] = None,
        margin_step: float = 0.01
    ) -> pd.DataFrame:
        """
        Run grid search over threshold and margin parameters.
        
        Optimized: Pre-fetches snapshots once per market instead of per parameter combination.
        
        Args:
            markets: List of market dicts to test
            threshold_min: Minimum threshold (default: 0.60)
            threshold_max: Maximum threshold (default: 1.00)
            threshold_step: Threshold increment (default: 0.01)
            margin_min: Minimum margin (default: 0.01)
            margin_max: Maximum margin (default: None = auto-calculate)
            margin_step: Margin increment (default: 0.01)
        
        Returns:
            DataFrame with results for each parameter combination
        """
        results = []
        
        threshold_values = np.arange(threshold_min, threshold_max + threshold_step/2, threshold_step)
        
        logger.info(f"Running grid search on {len(markets)} markets")
        logger.info(f"Threshold range: {threshold_min:.2f} to {threshold_max:.2f} (step {threshold_step:.2f})")
        
        # Pre-process all markets (fetch snapshots once)
        logger.info("Pre-processing markets (fetching snapshots)...")
        processed_markets = []
        for i, market in enumerate(markets):
            if (i + 1) % 50 == 0:
                logger.info(f"Pre-processing: {i+1}/{len(markets)} markets")
            market_data = self._preprocess_market_snapshots(market)
            if market_data:
                processed_markets.append(market_data)
        
        logger.info(f"Pre-processed {len(processed_markets)} markets with valid snapshots")
        
        # Calculate total combinations
        total_combinations = 0
        for threshold in threshold_values:
            if margin_max is None:
                max_margin = 0.99 - threshold
            else:
                max_margin = min(margin_max, 0.99 - threshold)
            
            if max_margin < margin_min:
                continue
            
            margin_values = np.arange(margin_min, max_margin + margin_step/2, margin_step)
            total_combinations += len(margin_values)
        
        logger.info(f"Total parameter combinations: {total_combinations}")
        
        combination_count = 0
        for threshold in threshold_values:
            if margin_max is None:
                max_margin = 0.99 - threshold
            else:
                max_margin = min(margin_max, 0.99 - threshold)
            
            if max_margin < margin_min:
                continue
            
            margin_values = np.arange(margin_min, max_margin + margin_step/2, margin_step)
            
            for margin in margin_values:
                combination_count += 1
                if combination_count % 100 == 0:
                    logger.info(f"Progress: {combination_count}/{total_combinations} combinations")
                
                # Process all markets with this parameter combination
                trades = []
                for market_data in processed_markets:
                    trade_result = self.process_market_with_snapshots(market_data, threshold, margin)
                    if trade_result:
                        trades.append(trade_result)
                
                if not trades:
                    continue  # Skip if no trades executed
                
                # Calculate metrics
                rois = [t["roi"] for t in trades]
                wins = sum(1 for t in trades if t["is_win"])
                losses = len(trades) - wins
                
                avg_roi = np.mean(rois)
                sharpe_ratio = np.mean(rois) / np.std(rois) if np.std(rois) > 0 else 0.0
                win_rate = wins / len(trades) if trades else 0.0
                
                results.append({
                    "threshold": threshold,
                    "margin": margin,
                    "limit_price": threshold + margin,
                    "num_trades": len(trades),
                    "wins": wins,
                    "losses": losses,
                    "win_rate": win_rate,
                    "avg_roi": avg_roi,
                    "sharpe_ratio": sharpe_ratio,
                    "total_roi": sum(rois),
                })
        
        df = pd.DataFrame(results)
        if not df.empty:
            df = df.sort_values(["threshold", "margin"])
        
        return df
    
    def run_backtest(
        self,
        threshold: float,
        margin: float,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_markets: Optional[int] = None
    ) -> Dict:
        """
        Run backtest with specific parameters.
        
        Returns:
            Dict with results and metrics
        """
        markets = self.get_markets_with_orderbooks(
            start_date=start_date,
            end_date=end_date,
            max_markets=max_markets
        )
        
        trades = []
        for market in markets:
            trade_result = self.process_market(market, threshold, margin)
            if trade_result:
                trades.append(trade_result)
        
        if not trades:
            return {
                "threshold": threshold,
                "margin": margin,
                "num_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "avg_roi": 0.0,
                "sharpe_ratio": 0.0,
                "total_roi": 0.0,
            }
        
        rois = [t["roi"] for t in trades]
        wins = sum(1 for t in trades if t["is_win"])
        losses = len(trades) - wins
        
        return {
            "threshold": threshold,
            "margin": margin,
            "num_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / len(trades),
            "avg_roi": np.mean(rois),
            "sharpe_ratio": np.mean(rois) / np.std(rois) if np.std(rois) > 0 else 0.0,
            "total_roi": sum(rois),
            "trades": trades,
        }

