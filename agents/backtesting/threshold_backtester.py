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
from agents.backtesting.backtesting_utils import (
    parse_market_dates,
    enrich_market_from_api,
    parse_outcome_price,
    group_snapshots_by_outcome,
    get_highest_bid_from_orderbook,
    get_lowest_ask_from_orderbook,
    calculate_metrics,
    get_markets_with_orderbooks as get_markets_with_orderbooks_util,
    walk_orderbook_upward_from_bid,
)
from agents.polymarket.orderbook_db import OrderbookDatabase
from agents.polymarket.orderbook_query import OrderbookQuery

logger = logging.getLogger(__name__)


class ThresholdBacktester:
    """
    Backtest threshold-based strategy: buy when one side reaches threshold.
    
    For each market:
    1. Monitor YES and NO highest bid prices from bids column at every snapshot
    2. When one side reaches threshold, place limit BID order at threshold + margin
    3. Check if BID order would fill (bid_price >= lowest_ask and bid_price >= highest_bid)
    4. Calculate ROI based on outcome prices
    """
    
    def __init__(self, proxy: Optional[str] = None, use_15m_table: bool = True, use_1h_table: bool = True):
        """
        Initialize threshold backtester.
        
        Args:
            proxy: Optional proxy URL for API calls
            use_15m_table: If True, query btc_15_min_table (default: True)
            use_1h_table: If True, query btc_1_hour_table (default: True)
        """
        self.market_fetcher = HistoricalMarketFetcher(proxy=proxy)
        self.use_15m_table = use_15m_table
        self.use_1h_table = use_1h_table
        
        # Initialize database connections for the tables we'll use
        self.orderbook_db_15m = OrderbookDatabase(use_btc_15_min_table=True) if use_15m_table else None
        self.orderbook_db_1h = OrderbookDatabase(use_btc_1_hour_table=True) if use_1h_table else None
        
        # For querying snapshots, we'll use the appropriate db based on market type
        # Default to 15m table for OrderbookQuery (will be overridden per-market)
        self.orderbook_query = OrderbookQuery(db=self.orderbook_db_15m or self.orderbook_db_1h)
    
    def get_markets_with_orderbooks(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_markets: Optional[int] = None
    ) -> List[Dict]:
        """Get markets that have orderbook data recorded from btc_15_min_table and/or btc_1_hour_table."""
        return get_markets_with_orderbooks_util(
            use_15m_table=self.use_15m_table,
            use_1h_table=self.use_1h_table,
            orderbook_db_15m=self.orderbook_db_15m,
            orderbook_db_1h=self.orderbook_db_1h,
            market_fetcher=self.market_fetcher,
            start_date=start_date,
            end_date=end_date,
            max_markets=max_markets
        )
    
    def _process_markets_parallel(
        self,
        processed_markets: List[Dict],
        threshold: float,
        margin: float,
        dollar_amount: float,
        max_workers: Optional[int] = None
    ) -> List[Dict]:
        """
        Process multiple markets in parallel for better performance.
        
        Args:
            processed_markets: List of pre-processed market data
            threshold: Threshold parameter
            margin: Margin parameter
            dollar_amount: Dollar amount parameter
            max_workers: Maximum number of worker processes (default: CPU count)
        
        Returns:
            List of trade results
        """
        # For small numbers of markets, sequential is faster (no overhead)
        if len(processed_markets) < 10:
            trades = []
            for market_data in processed_markets:
                trade_result = self.process_market_with_snapshots(
                    market_data, threshold, margin, dollar_amount
                )
                if trade_result:
                    trades.append(trade_result)
            return trades
        
        try:
            from multiprocessing import Pool, cpu_count
            
            if max_workers is None:
                max_workers = max(1, cpu_count() - 1)  # Leave one core free
            
            # Create a static function that can be pickled
            def _process_single_market_static(args):
                """Static wrapper for multiprocessing."""
                market_data, threshold, margin, dollar_amount = args
                # Recreate the processing logic here (can't pickle instance methods)
                # For now, fall back to sequential for simplicity
                # TODO: Refactor to make this truly parallelizable
                return None
            
            # For now, use sequential but with optimizations
            # True parallelization requires refactoring to avoid pickling issues
            trades = []
            for market_data in processed_markets:
                trade_result = self.process_market_with_snapshots(
                    market_data, threshold, margin, dollar_amount
                )
                if trade_result:
                    trades.append(trade_result)
            return trades
            
        except Exception as e:
            # Fallback to sequential processing if anything fails
            logger.debug(f"Parallel processing not available ({e}), using sequential")
            trades = []
            for market_data in processed_markets:
                trade_result = self.process_market_with_snapshots(
                    market_data, threshold, margin, dollar_amount
                )
                if trade_result:
                    trades.append(trade_result)
            return trades
    
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
        
        # Determine which database to use based on market type
        market_type = market.get("_market_type", "15m")
        if market_type == "1h" and self.orderbook_db_1h:
            # Use 1-hour table
            query_db = OrderbookQuery(db=self.orderbook_db_1h)
        elif market_type == "15m" and self.orderbook_db_15m:
            # Use 15-minute table
            query_db = OrderbookQuery(db=self.orderbook_db_15m)
        else:
            # Fallback to default
            query_db = self.orderbook_query
        
        # Get all snapshots for this market, sorted by timestamp
        snapshots = query_db.get_snapshots(
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
        yes_snapshots, no_snapshots = group_snapshots_by_outcome(snapshots)
        
        if not yes_snapshots or not no_snapshots:
            logger.debug(f"Market {market_id}: Missing Outcome 1 or Outcome 2 snapshots")
            return None
        
        # Monitor both sides: check when threshold is reached using HIGHEST BID from bids column
        trigger_side = None
        trigger_time = None
        trigger_price = None
        
        # Check YES side - trigger when highest bid >= threshold
        for snapshot in yes_snapshots:
            highest_bid = get_highest_bid_from_orderbook(snapshot)
            if highest_bid is not None and highest_bid >= threshold:
                trigger_side = "YES"
                trigger_time = snapshot.timestamp
                trigger_price = highest_bid
                break
        
        # Check NO side (only if YES didn't trigger) - trigger when highest bid >= threshold
        if trigger_side is None:
            for snapshot in no_snapshots:
                highest_bid = get_highest_bid_from_orderbook(snapshot)
                if highest_bid is not None and highest_bid >= threshold:
                    trigger_side = "NO"
                    trigger_time = snapshot.timestamp
                    trigger_price = highest_bid
                    break
        
        if trigger_side is None:
            return None  # Threshold never reached
        
        # Calculate BID order price (we're buying/placing a bid order)
        bid_price = threshold + margin
        if bid_price > 0.99:
            bid_price = 0.99  # Cap at 99%
        
        # Determine which token to place bid order on
        if trigger_side == "YES":
            buy_token_snapshots = yes_snapshots
        else:
            buy_token_snapshots = no_snapshots
        
        # Check if BID order would fill
        # Conditions:
        # 1. threshold + margin (our bid price) should NOT be less than the lowest ask (i.e., >= lowest_ask)
        # 2. Our bid should be >= some ask price (so we can buy from someone)
        # 3. Our bid should be the highest bid (or would get filled, meaning no other bidder outcompetes us)
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
        
        # Skip a few timesteps after trigger to check if order fills
        # Start checking after trigger (not immediately)
        for snapshot in buy_token_snapshots:
            if snapshot.timestamp <= trigger_time:
                continue  # Skip trigger snapshot and earlier
            
            if snapshot.timestamp > fill_deadline:
                break  # Past fill window
            
            # Use pre-computed orderbook metrics (performance optimization)
            lowest_ask = snapshot._lowest_ask
            highest_bid = snapshot._highest_bid
            
            if lowest_ask is None or highest_bid is None:
                continue
            
            # Condition 1: Our bid price should NOT be less than the lowest ask
            # (i.e., threshold + margin >= lowest_ask)
            if bid_price < lowest_ask:
                continue  # Our bid is too low, skip
            
            # Check if order fills:
            # - Our bid >= lowest ask (we can buy)
            # - Our bid is the highest bid (or at least competitive - if there's a higher bid, 
            #   we'd still get filled if there's enough ask volume at prices <= our bid)
            # For simplicity, check if our bid >= highest bid (we're the highest) OR
            # if our bid >= lowest ask (we'd get filled even if not highest, as long as we're competitive)
            if bid_price >= highest_bid or bid_price >= lowest_ask:
                order_filled = True
                fill_time = snapshot.timestamp
                break
        
        if not order_filled:
            return None  # Order never filled
        
        # Parse outcome price
        outcome_price = parse_outcome_price(
            market.get("outcomePrices", {}),
            trigger_side,
            market_id=market_id,
            market_fetcher=self.market_fetcher
        )
        
        if outcome_price is None:
            return None
        
        # ROI = (outcome_price - bid_price) / bid_price
        # We place a BID order at bid_price, so if outcome_price > bid_price, we profit
        roi = (outcome_price - bid_price) / bid_price if bid_price > 0 else 0.0
        
        # Determine win/loss
        is_win = roi > 0
        
        return {
            "market_id": market_id,
            "threshold": threshold,
            "margin": margin,
            "trigger_side": trigger_side,
            "trigger_time": trigger_time,
            "trigger_price": trigger_price,
            "limit_price": bid_price,  # This is the bid price we place
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
        market_start, market_end = parse_market_dates(market)
        
        # Determine which database to use based on market type
        market_type = market.get("_market_type", "15m")
        if market_type == "1h" and self.orderbook_db_1h:
            # Use 1-hour table
            query_db = OrderbookQuery(db=self.orderbook_db_1h)
        elif market_type in ("15m", "both") and self.orderbook_db_15m:
            # Use 15-minute table (or prefer 15m if found in both)
            query_db = OrderbookQuery(db=self.orderbook_db_15m)
        else:
            # Fallback to default
            query_db = self.orderbook_query
        
        # Get all snapshots for this market (only during active period)
        snapshots = query_db.get_snapshots(
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
        yes_snapshots, no_snapshots = group_snapshots_by_outcome(snapshots)
        
        if not yes_snapshots or not no_snapshots:
            return None
        
        # Pre-compute orderbook metrics to avoid repeated parsing (performance optimization)
        for snapshot in yes_snapshots + no_snapshots:
            snapshot._highest_bid = get_highest_bid_from_orderbook(snapshot)
            snapshot._lowest_ask = get_lowest_ask_from_orderbook(snapshot)
        
        # Pre-compute max/min values for early termination checks
        yes_max_bid = max((s._highest_bid for s in yes_snapshots if s._highest_bid is not None), default=None)
        no_max_bid = max((s._highest_bid for s in no_snapshots if s._highest_bid is not None), default=None)
        
        # Get outcome prices (can be dict, list, or JSON string)
        outcome_prices_raw = market.get("outcomePrices", {})
        if not outcome_prices_raw:
            # Try to fetch from API
            market_info = enrich_market_from_api(market_id, self.market_fetcher)
            if market_info:
                outcome_prices_raw = market_info.get("outcomePrices", {})
        
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
            "_yes_max_bid": yes_max_bid,  # For early termination
            "_no_max_bid": no_max_bid,  # For early termination
        }
    
    def process_market_with_snapshots(
        self,
        market_data: Dict,
        threshold: float,
        margin: float,
        dollar_amount: float = 100.0
    ) -> Optional[Dict]:
        """
        Process market with pre-fetched snapshots (faster for grid search).
        
        Args:
            market_data: Pre-processed market data from _preprocess_market_snapshots
            threshold: Threshold percentage
            margin: Margin percentage
            dollar_amount: Dollar amount to bet (full principal for ROI calculation)
        
        Returns:
            Dict with trade results or None
        """
        yes_snapshots = market_data["yes_snapshots"]
        no_snapshots = market_data["no_snapshots"]
        outcome_prices_raw = market_data["outcome_prices"]
        market_id = market_data["market_id"]
        
        if not outcome_prices_raw:
            return None
        
        # Monitor both sides: check when threshold is reached using HIGHEST BID from bids column
        trigger_side = None
        trigger_time = None
        trigger_price = None
        
        # Check YES side - trigger when highest bid >= threshold
        for snapshot in yes_snapshots:
            highest_bid = get_highest_bid_from_orderbook(snapshot)
            if highest_bid is not None and highest_bid >= threshold:
                trigger_side = "YES"
                trigger_time = snapshot.timestamp
                trigger_price = highest_bid
                break
        
        # Check NO side (only if YES didn't trigger) - trigger when highest bid >= threshold
        if trigger_side is None:
            for snapshot in no_snapshots:
                highest_bid = get_highest_bid_from_orderbook(snapshot)
                if highest_bid is not None and highest_bid >= threshold:
                    trigger_side = "NO"
                    trigger_time = snapshot.timestamp
                    trigger_price = highest_bid
                    break
        
        if trigger_side is None:
            return None  # Threshold never reached
        
        # Calculate BID order price (we're buying/placing a bid order)
        bid_price = threshold + margin
        if bid_price > 0.99:
            bid_price = 0.99
        
        # Determine which token to place bid order on
        if trigger_side == "YES":
            buy_token_snapshots = yes_snapshots
        else:
            buy_token_snapshots = no_snapshots
        
        # Check if BID order would fill by walking the orderbook upward from bid_price
        # Walks all the way up to 0.99 to maximize fill, continues across snapshots until filled
        if margin < 0.02:
            fill_window = timedelta(minutes=1)
        else:
            fill_window = timedelta(days=365)
        
        fill_deadline = trigger_time + fill_window
        
        # Skip a few timesteps after trigger to check if order fills
        # Start checking after trigger (not immediately)
        # Continue trying to fill across multiple snapshots until order is fully filled
        remaining_dollars = dollar_amount
        total_filled_shares = 0.0
        total_cost = 0.0
        fill_time = None
        weighted_avg_fill_price = None
        
        for snapshot in buy_token_snapshots:
            if snapshot.timestamp <= trigger_time:
                continue  # Skip trigger snapshot and earlier
            
            if snapshot.timestamp > fill_deadline:
                break  # Past fill window
            
            # Walk orderbook upward from bid_price to spend remaining_dollars
            # Walks all the way up to 0.99 to maximize fill
            result = walk_orderbook_upward_from_bid(
                snapshot, 
                bid_price, 
                remaining_dollars,
                max_price=0.99  # Walk all the way up to Polymarket max
            )
            
            if result[0] is not None and result[1] > 0:
                # Order can fill (at least partially)
                avg_price, filled, spent = result
                
                # Accumulate fills across snapshots
                if fill_time is None:
                    fill_time = snapshot.timestamp
                
                # Calculate weighted average across all fills
                total_cost += spent
                total_filled_shares += filled
                remaining_dollars -= spent
                
                # Update weighted average fill price
                weighted_avg_fill_price = total_cost / total_filled_shares if total_filled_shares > 0 else None
                
                # If order is fully filled, we're done
                if remaining_dollars <= 0.01:  # Small tolerance for floating point
                    order_filled = True
                    break
        
        if weighted_avg_fill_price is None or total_filled_shares == 0:
            return None
        
        # Use accumulated values
        filled_shares = total_filled_shares
        dollars_spent = total_cost
        order_filled = True
        
        # Calculate fill rate (what % of dollar amount spent)
        # Note: If fill_rate < 1.0, it means the orderbook snapshot doesn't have enough depth
        # recorded (data limitation) - we walked all the way up to 0.99 but still couldn't fill.
        # This is realistic as orderbook snapshots may only record top N levels.
        fill_rate = dollars_spent / dollar_amount if dollar_amount > 0 else 0.0
        
        # Parse outcome prices based on trigger side
        outcome_price = parse_outcome_price(
            outcome_prices_raw,
            trigger_side,
            market_id=market_id,
            market_fetcher=self.market_fetcher
        )
        
        if outcome_price is None:
            return None
        
        # Calculate fee based on fill price and trade value
        from agents.backtesting.backtesting_utils import calculate_polymarket_fee
        fee = calculate_polymarket_fee(weighted_avg_fill_price, dollars_spent)
        
        # Calculate total cost (dollars spent + fee)
        total_cost = dollars_spent + fee
        
        # Calculate total revenue (outcome price * shares)
        total_revenue = outcome_price * filled_shares
        
        # Calculate ROI on the full principal (requested dollar_amount)
        # This penalizes partial fills appropriately - if you request $1000 but only $5 executes,
        # ROI is calculated as if you deployed the full $1000
        # ROI = (revenue - cost) / requested_amount
        roi = (total_revenue - total_cost) / dollar_amount if dollar_amount > 0 else 0.0
        is_win = roi > 0
        
        return {
            "market_id": market_id,
            "threshold": threshold,
            "margin": margin,
            "dollar_amount": dollar_amount,
            "trigger_side": trigger_side,
            "trigger_time": trigger_time,
            "trigger_price": trigger_price,
            "bid_price": bid_price,  # The bid price we place
            "fill_price": weighted_avg_fill_price,  # Weighted average fill price (>= bid_price)
            "filled_shares": filled_shares,
            "dollars_spent": dollars_spent,
            "fee": fee,  # Fee paid for this trade
            "total_cost": total_cost,  # dollars_spent + fee
            "fill_rate": fill_rate,
            "fill_time": fill_time,
            "outcome_price": outcome_price,
            "total_revenue": total_revenue,  # outcome_price * filled_shares
            "roi": roi,  # ROI calculated on full principal (dollar_amount), penalizes partial fills
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
        margin_step: float = 0.01,
        min_dollar_amount: float = 1.0,
        max_dollar_amount: float = 1000.0,
        dollar_amount_interval: float = 50.0,
        return_individual_trades: bool = False
    ) -> pd.DataFrame:
        """
        Run grid search over threshold, margin, and dollar_amount parameters.
        
        Optimized: Pre-fetches snapshots once per market instead of per parameter combination.
        
        Args:
            markets: List of market dicts to test
            threshold_min: Minimum threshold (default: 0.60)
            threshold_max: Maximum threshold (default: 1.00)
            threshold_step: Threshold increment (default: 0.01)
            margin_min: Minimum margin (default: 0.01)
            margin_max: Maximum margin (default: None = auto-calculate)
            margin_step: Margin increment (default: 0.01)
            min_dollar_amount: Minimum dollar amount to test (default: 1.0)
            max_dollar_amount: Maximum dollar amount to test (default: 1000.0)
            dollar_amount_interval: Dollar amount increment (default: 50.0)
            return_individual_trades: If True, return individual trades dict (default: False)
            
        Note: ROI is calculated on the full principal (dollar_amount), not the actual amount filled.
              This means partial fills are penalized appropriately - if you request $1000 but only
              $5 executes, ROI is calculated as (revenue - cost) / $1000, not / $5.
        
        Returns:
            DataFrame with results for each parameter combination
        """
        results = []
        
        threshold_values = np.arange(threshold_min, threshold_max + threshold_step/2, threshold_step)
        dollar_amount_values = np.arange(min_dollar_amount, max_dollar_amount + dollar_amount_interval/2, dollar_amount_interval)
        
        logger.info(f"Running grid search on {len(markets)} markets")
        logger.info(f"Threshold range: {threshold_min:.2f} to {threshold_max:.2f} (step {threshold_step:.2f})")
        logger.info(f"Dollar amount range: ${min_dollar_amount:.0f} to ${max_dollar_amount:.0f} (step ${dollar_amount_interval:.0f})")
        logger.info(f"Note: ROI calculated on full principal (requested amount), penalizing partial fills")
        
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
            total_combinations += len(margin_values) * len(dollar_amount_values)
        
        print(f"Total parameter combinations: {total_combinations}", flush=True)
        
        # Store individual trades if requested
        individual_trades_dict = {} if return_individual_trades else None
        
        combination_count = 0
        import time
        start_time = time.time()
        
        for threshold in threshold_values:
            if margin_max is None:
                max_margin = 0.99 - threshold
            else:
                max_margin = min(margin_max, 0.99 - threshold)
            
            if max_margin < margin_min:
                continue
            
            margin_values = np.arange(margin_min, max_margin + margin_step/2, margin_step)
            
            for margin in margin_values:
                for dollar_amount in dollar_amount_values:
                    combination_count += 1
                    
                    # Print progress more frequently (every 10 combinations or every threshold)
                    if combination_count % 10 == 0 or combination_count == 1:
                        elapsed = time.time() - start_time
                        if combination_count > 1:
                            avg_time_per_comb = elapsed / combination_count
                            remaining_comb = total_combinations - combination_count
                            eta_seconds = avg_time_per_comb * remaining_comb
                            eta_minutes = eta_seconds / 60
                            print(f"Progress: {combination_count}/{total_combinations} combinations ({combination_count*100/total_combinations:.1f}%) | "
                                  f"Threshold: {threshold:.3f}, Margin: {margin:.3f}, Dollar: ${dollar_amount:.0f} | "
                                  f"ETA: {eta_minutes:.1f} min", flush=True)
                        else:
                            print(f"Progress: {combination_count}/{total_combinations} combinations | "
                                  f"Threshold: {threshold:.3f}, Margin: {margin:.3f}, Dollar: ${dollar_amount:.0f}", flush=True)
                    
                    # Process all markets with this parameter combination
                    # Note: Parallel processing infrastructure is in place but uses sequential
                    # processing for now due to pickling constraints. The pre-computed orderbook
                    # metrics and early termination provide significant speedups already.
                    trades = self._process_markets_parallel(
                        processed_markets, threshold, margin, dollar_amount
                    )
                    
                    if not trades:
                        continue  # Skip if no trades executed
                    
                    # Calculate metrics
                    metrics = calculate_metrics(trades)
                    
                    results.append({
                        "threshold": threshold,
                        "margin": margin,
                        "dollar_amount": dollar_amount,
                        "limit_price": threshold + margin,  # This is the bid price
                        **metrics,
                    })
                    
                    # Store individual ROI values if requested
                    if return_individual_trades:
                        key = (threshold, margin, dollar_amount)
                        roi_values = [t.get("roi", 0.0) for t in trades]
                        individual_trades_dict[key] = roi_values
        
        df = pd.DataFrame(results)
        if not df.empty:
            df = df.sort_values(["threshold", "margin", "dollar_amount"])
        
        # Store individual trades in a custom attribute if requested
        if return_individual_trades:
            df.attrs['individual_trades'] = individual_trades_dict
        
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
        
        metrics = calculate_metrics(trades)
        
        return {
            "threshold": threshold,
            "margin": margin,
            **metrics,
            "trades": trades,
        }

