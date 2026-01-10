"""
Split Strategy Backtester

Strategy:
1. Split $X USDC → X YES + X NO shares (cost = $X)
2. Monitor both sides - when highest bid < threshold for one side, sell that side
3. Sell at threshold - margin (walk down bid book if needed)
4. Hold the other side until market resolution
5. ROI = (cash from sale + final value of held side - split cost) / split cost

Grid search over:
- threshold: When to sell (highest bid < threshold)
- margin: Sell at threshold - margin
- dollar_amount: Amount to split ($X)
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from agents.polymarket.orderbook_db import OrderbookDatabase
from agents.backtesting.market_fetcher import HistoricalMarketFetcher
from agents.backtesting.backtesting_utils import (
    parse_market_dates,
    enrich_market_from_api,
    parse_outcome_price,
    group_snapshots_by_outcome,
    get_highest_bid_from_orderbook,
    get_lowest_ask_from_orderbook,
    walk_orderbook_downward_from_ask,
    get_markets_with_orderbooks,
    calculate_metrics,
)

logger = logging.getLogger(__name__)


class SplitStrategyBacktester:
    """
    Backtest split strategy: split, monitor, sell dropping side, hold other side.
    
    For each market:
    1. Split $X → X YES + X NO shares (cost = $X)
    2. Monitor both YES and NO highest bid prices
    3. When one side's highest bid < threshold, sell that side at threshold - margin
    4. Walk down bid book if needed to sell all shares
    5. Hold the other side until market resolution
    6. Calculate ROI based on cash received + final value of held side
    """
    
    def __init__(
        self,
        use_15m_table: bool = True,
        use_1h_table: bool = True,
        market_fetcher: Optional[HistoricalMarketFetcher] = None
    ):
        """
        Initialize split strategy backtester.
        
        Args:
            use_15m_table: Use btc_15_min_table for 15-minute markets
            use_1h_table: Use btc_1_hour_table for 1-hour markets
            market_fetcher: Optional HistoricalMarketFetcher for enriching market data
        """
        self.use_15m_table = use_15m_table
        self.use_1h_table = use_1h_table
        
        # Initialize database connections
        self.orderbook_db_15m = None
        self.orderbook_db_1h = None
        
        if use_15m_table:
            self.orderbook_db_15m = OrderbookDatabase(use_btc_15_min_table=True)
        if use_1h_table:
            self.orderbook_db_1h = OrderbookDatabase(use_btc_1_hour_table=True)
        
        self.market_fetcher = market_fetcher or HistoricalMarketFetcher()
    
    def get_markets_with_orderbooks(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_markets: Optional[int] = None
    ) -> List[Dict]:
        """Get markets with orderbook data."""
        return get_markets_with_orderbooks(
            use_15m_table=self.use_15m_table,
            use_1h_table=self.use_1h_table,
            orderbook_db_15m=self.orderbook_db_15m,
            orderbook_db_1h=self.orderbook_db_1h,
            market_fetcher=self.market_fetcher,
            start_date=start_date,
            end_date=end_date,
            max_markets=max_markets
        )
    
    def process_market_with_snapshots(
        self,
        market: Dict,
        threshold: float,
        margin: float,
        dollar_amount: float
    ) -> Optional[Dict]:
        """
        Process a single market with pre-loaded snapshots.
        
        Args:
            market: Market dict with snapshots and metadata
            threshold: Threshold for selling (highest bid < threshold)
            margin: Sell at threshold - margin
            dollar_amount: Amount to split ($X → X YES + X NO)
            
        Returns:
            Trade result dict or None if no trade occurred
        """
        # Check if threshold - margin < 0 (invalid)
        if threshold - margin < 0:
            return None
        
        market_id = market.get("id")
        snapshots = market.get("snapshots", [])
        
        if not snapshots:
            return None
        
        # Group snapshots by outcome
        yes_snapshots, no_snapshots = group_snapshots_by_outcome(snapshots)
        
        if not yes_snapshots or not no_snapshots:
            return None
        
        # Split: $X → X YES + X NO shares
        # Cost = $X, we get X shares of each
        shares_per_side = dollar_amount  # 1 share = $1, so $X = X shares
        
        # Monitor both sides for threshold breach (highest bid < threshold)
        # Track which side(s) drop below threshold and when
        yes_triggered = False
        no_triggered = False
        yes_trigger_time = None
        no_trigger_time = None
        
        # Check YES side
        for snapshot in yes_snapshots:
            highest_bid = get_highest_bid_from_orderbook(snapshot)
            if highest_bid is not None and highest_bid < threshold:
                yes_triggered = True
                yes_trigger_time = snapshot.timestamp
                break
        
        # Check NO side
        for snapshot in no_snapshots:
            highest_bid = get_highest_bid_from_orderbook(snapshot)
            if highest_bid is not None and highest_bid < threshold:
                no_triggered = True
                no_trigger_time = snapshot.timestamp
                break
        
        # If neither side triggered, hold both until resolution (break-even)
        if not yes_triggered and not no_triggered:
            # Get outcome prices to determine final value
            outcome_prices_raw = market.get("outcomePrices", {})
            if not outcome_prices_raw:
                market_info = enrich_market_from_api(market_id, self.market_fetcher)
                if market_info:
                    outcome_prices_raw = market_info.get("outcomePrices", {})
            
            if not outcome_prices_raw:
                return None
            
            # Both sides held until resolution
            # Final value = $X (can merge back to $X)
            # ROI = ($X - $X) / $X = 0%
            return {
                "market_id": market_id,
                "threshold": threshold,
                "margin": margin,
                "dollar_amount": dollar_amount,
                "triggered_sides": [],
                "yes_sold": False,
                "no_sold": False,
                "yes_sell_price": None,
                "no_sell_price": None,
                "yes_shares_sold": 0.0,
                "no_shares_sold": 0.0,
                "yes_cash_received": 0.0,
                "no_cash_received": 0.0,
                "held_side": "BOTH",
                "final_value": dollar_amount,  # Can merge both sides back to $X
                "roi": 0.0,
                "is_win": False,
            }
        
        # Determine which side to sell first (earliest trigger)
        sell_yes_first = False
        sell_no_first = False
        
        if yes_triggered and no_triggered:
            # Both triggered - sell the one that triggered first
            if yes_trigger_time <= no_trigger_time:
                sell_yes_first = True
            else:
                sell_no_first = True
        elif yes_triggered:
            sell_yes_first = True
        else:  # no_triggered
            sell_no_first = True
        
        # Sell orders
        yes_sell_price = None
        no_sell_price = None
        yes_shares_sold = 0.0
        no_shares_sold = 0.0
        yes_cash_received = 0.0
        no_cash_received = 0.0
        yes_fill_time = None
        no_fill_time = None
        
        # Calculate ask prices (sell prices)
        yes_ask_price = threshold - margin if yes_triggered else None
        no_ask_price = threshold - margin if no_triggered else None
        
        # Use fill window similar to previous backtesting
        if margin < 0.02:
            fill_window = timedelta(minutes=1)
        else:
            fill_window = timedelta(days=365)
        
        # Sell YES side if triggered
        if yes_triggered:
            yes_fill_deadline = yes_trigger_time + fill_window
            
            for snapshot in yes_snapshots:
                if snapshot.timestamp <= yes_trigger_time:
                    continue
                if snapshot.timestamp > yes_fill_deadline:
                    break
                
                # Check competitiveness (inverse of buy order logic):
                # 1. Our ask should be <= highest bid (we can sell to someone)
                # 2. Our ask should be <= lowest ask (we're competitive - no other asks below ours)
                highest_bid = get_highest_bid_from_orderbook(snapshot)
                lowest_ask = get_lowest_ask_from_orderbook(snapshot)
                
                if highest_bid is None:
                    continue
                
                # Condition 1: Our ask should NOT be greater than the highest bid
                # (i.e., threshold - margin <= highest_bid)
                if yes_ask_price > highest_bid:
                    continue  # Our ask is too high, skip
                
                # Condition 2: Our ask should be competitive (<= lowest ask)
                # If someone else has a lower ask, they'd get filled first
                if lowest_ask is not None and yes_ask_price > lowest_ask:
                    continue  # Another asker outcompetes us, skip
                
                # Walk down bid book to sell shares
                result = walk_orderbook_downward_from_ask(
                    snapshot,
                    yes_ask_price,
                    shares_per_side  # Sell all YES shares
                )
                
                if result[0] is not None and result[1] > 0:
                    yes_sell_price, yes_shares_sold, yes_cash_received = result
                    yes_fill_time = snapshot.timestamp
                    break
        
        # Sell NO side if triggered
        if no_triggered:
            no_fill_deadline = no_trigger_time + fill_window
            
            for snapshot in no_snapshots:
                if snapshot.timestamp <= no_trigger_time:
                    continue
                if snapshot.timestamp > no_fill_deadline:
                    break
                
                # Check competitiveness (inverse of buy order logic):
                # 1. Our ask should be <= highest bid (we can sell to someone)
                # 2. Our ask should be <= lowest ask (we're competitive - no other asks below ours)
                highest_bid = get_highest_bid_from_orderbook(snapshot)
                lowest_ask = get_lowest_ask_from_orderbook(snapshot)
                
                if highest_bid is None:
                    continue
                
                # Condition 1: Our ask should NOT be greater than the highest bid
                # (i.e., threshold - margin <= highest_bid)
                if no_ask_price > highest_bid:
                    continue  # Our ask is too high, skip
                
                # Condition 2: Our ask should be competitive (<= lowest ask)
                # If someone else has a lower ask, they'd get filled first
                if lowest_ask is not None and no_ask_price > lowest_ask:
                    continue  # Another asker outcompetes us, skip
                
                # Walk down bid book to sell shares
                result = walk_orderbook_downward_from_ask(
                    snapshot,
                    no_ask_price,
                    shares_per_side  # Sell all NO shares
                )
                
                if result[0] is not None and result[1] > 0:
                    no_sell_price, no_shares_sold, no_cash_received = result
                    no_fill_time = snapshot.timestamp
                    break
        
        # Determine held side and calculate final value
        # Account for partial fills - we hold remaining shares of sold side + all shares of unsold side
        held_side = None
        yes_shares_held = shares_per_side - yes_shares_sold if yes_shares_sold > 0 else shares_per_side
        no_shares_held = shares_per_side - no_shares_sold if no_shares_sold > 0 else shares_per_side
        
        if yes_shares_sold > 0 and no_shares_sold > 0:
            # Sold both sides (both triggered and both sold)
            # Hold remaining shares of both sides
            held_side = "BOTH_PARTIAL"
        elif yes_shares_sold > 0:
            # Sold YES (fully or partially), hold remaining YES + all NO
            held_side = "NO" if yes_shares_sold >= shares_per_side else "BOTH_PARTIAL"
        elif no_shares_sold > 0:
            # Sold NO (fully or partially), hold remaining NO + all YES
            held_side = "YES" if no_shares_sold >= shares_per_side else "BOTH_PARTIAL"
        else:
            # Neither sold (orders didn't fill)
            return None
        
        # Calculate final value of held side
        outcome_prices_raw = market.get("outcomePrices", {})
        if not outcome_prices_raw:
            market_info = enrich_market_from_api(market_id, self.market_fetcher)
            if market_info:
                outcome_prices_raw = market_info.get("outcomePrices", {})
        
        if not outcome_prices_raw:
            return None
        
        final_value = 0.0
        if held_side == "YES":
            # Hold YES only (NO fully sold)
            yes_outcome_price = parse_outcome_price(
                outcome_prices_raw,
                "YES",
                market_id=market_id,
                market_fetcher=self.market_fetcher
            )
            if yes_outcome_price is not None:
                final_value = yes_outcome_price * yes_shares_held
        elif held_side == "NO":
            # Hold NO only (YES fully sold)
            no_outcome_price = parse_outcome_price(
                outcome_prices_raw,
                "NO",
                market_id=market_id,
                market_fetcher=self.market_fetcher
            )
            if no_outcome_price is not None:
                final_value = no_outcome_price * no_shares_held
        elif held_side == "BOTH_PARTIAL":
            # Hold both sides (partial fills or both triggered)
            yes_outcome_price = parse_outcome_price(
                outcome_prices_raw,
                "YES",
                market_id=market_id,
                market_fetcher=self.market_fetcher
            )
            no_outcome_price = parse_outcome_price(
                outcome_prices_raw,
                "NO",
                market_id=market_id,
                market_fetcher=self.market_fetcher
            )
            if yes_outcome_price is not None and no_outcome_price is not None:
                # Final value = YES value + NO value
                final_value = (yes_outcome_price * yes_shares_held) + (no_outcome_price * no_shares_held)
        elif held_side == "BOTH":
            # Both sides held (threshold never reached)
            final_value = dollar_amount  # Can merge back to $X
        
        # Calculate fees for sales (if any shares were sold)
        from agents.backtesting.backtesting_utils import calculate_polymarket_fee
        
        yes_fee = 0.0
        no_fee = 0.0
        
        if yes_shares_sold > 0 and yes_sell_price is not None:
            # Fee is calculated based on sell price and cash received
            yes_fee = calculate_polymarket_fee(yes_sell_price, yes_cash_received)
        
        if no_shares_sold > 0 and no_sell_price is not None:
            # Fee is calculated based on sell price and cash received
            no_fee = calculate_polymarket_fee(no_sell_price, no_cash_received)
        
        # Calculate total cash received from sales (after fees)
        total_cash_received = (yes_cash_received - yes_fee) + (no_cash_received - no_fee)
        total_fees = yes_fee + no_fee
        
        # Calculate ROI
        # ROI = (cash from sales (after fees) + final value of held side - split cost) / split cost
        # Note: Split itself has no fee (it's minting, not trading)
        total_value = total_cash_received + final_value
        roi = (total_value - dollar_amount) / dollar_amount if dollar_amount > 0 else 0.0
        is_win = roi > 0
        
        return {
            "market_id": market_id,
            "threshold": threshold,
            "margin": margin,
            "dollar_amount": dollar_amount,
            "triggered_sides": (["YES"] if yes_triggered else []) + (["NO"] if no_triggered else []),
            "yes_sold": yes_shares_sold > 0,
            "no_sold": no_shares_sold > 0,
            "yes_sell_price": yes_sell_price,
            "no_sell_price": no_sell_price,
            "yes_shares_sold": yes_shares_sold,
            "no_shares_sold": no_shares_sold,
            "yes_cash_received": yes_cash_received,  # Gross cash received
            "no_cash_received": no_cash_received,  # Gross cash received
            "yes_fee": yes_fee,  # Fee paid on YES sale
            "no_fee": no_fee,  # Fee paid on NO sale
            "total_fees": total_fees,  # Total fees paid
            "held_side": held_side,
            "final_value": final_value,
            "total_cash_received": total_cash_received,  # Net cash received (after fees)
            "total_value": total_value,
            "roi": roi,
            "is_win": is_win,
        }
    
    def run_grid_search(
        self,
        threshold_min: float = 0.30,
        threshold_max: float = 0.50,
        threshold_step: float = 0.01,
        margin_min: float = 0.01,
        margin_step: float = 0.01,
        min_dollar_amount: float = 1.0,
        max_dollar_amount: float = 1000.0,
        dollar_amount_interval: float = 50.0,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_markets: Optional[int] = None
    ) -> Tuple[pd.DataFrame, Dict]:
        """
        Run grid search over threshold, margin, and dollar_amount parameters.
        
        Returns:
            Tuple of (results DataFrame, individual_trades dict)
        """
        
        # Get markets
        markets = self.get_markets_with_orderbooks(
            start_date=start_date,
            end_date=end_date,
            max_markets=max_markets
        )
        
        logger.info(f"Running grid search on {len(markets)} markets")
        
        # Generate parameter ranges
        threshold_values = np.arange(threshold_min, threshold_max + threshold_step/2, threshold_step)
        margin_values_base = np.arange(margin_min, 0.99 + margin_step/2, margin_step)
        dollar_amount_values = np.arange(min_dollar_amount, max_dollar_amount + dollar_amount_interval/2, dollar_amount_interval)
        
        # Pre-process markets (load snapshots)
        preprocessed_markets = []
        for market in markets:
            preprocessed = self._preprocess_market_snapshots(market)
            if preprocessed:
                preprocessed_markets.append(preprocessed)
        
        logger.info(f"Pre-processed {len(preprocessed_markets)} markets with valid snapshots")
        
        # Store results
        results = []
        individual_trades = {}  # {(threshold, margin, dollar_amount): [trade_results]}
        
        # Calculate total combinations for progress tracking
        total_combinations = 0
        for threshold in threshold_values:
            # Margin max is limited by threshold (threshold - margin >= 0)
            max_margin = threshold  # threshold - margin >= 0 means margin <= threshold
            margin_values = [m for m in margin_values_base if m <= max_margin]
            total_combinations += len(margin_values) * len(dollar_amount_values) * len(preprocessed_markets)
        
        print(f"Total parameter combinations: {total_combinations}", flush=True)
        
        combination_count = 0
        
        # Grid search
        for threshold in threshold_values:
            # Margin max is limited by threshold
            max_margin = threshold
            margin_values = [m for m in margin_values_base if m <= max_margin]
            
            for margin in margin_values:
                # Skip if threshold - margin < 0
                if threshold - margin < 0:
                    continue
                
                for dollar_amount in dollar_amount_values:
                    key = (threshold, margin, dollar_amount)
                    individual_trades[key] = []
                    
                    # Process each market
                    for market in preprocessed_markets:
                        trade_result = self.process_market_with_snapshots(
                            market,
                            threshold,
                            margin,
                            dollar_amount
                        )
                        
                        if trade_result:
                            individual_trades[key].append(trade_result)
                        
                        combination_count += 1
                        
                        # Progress logging
                        if combination_count % 10 == 0:
                            print(
                                f"Progress: {combination_count}/{total_combinations} "
                                f"({100*combination_count/total_combinations:.1f}%) - "
                                f"threshold={threshold:.3f}, margin={margin:.3f}, dollar_amount=${dollar_amount:.0f}",
                                flush=True
                            )
                    
                    # Calculate metrics for this parameter combination
                    trades = individual_trades[key]
                    if trades:
                        metrics = calculate_metrics(trades)
                        results.append({
                            "threshold": threshold,
                            "margin": margin,
                            "dollar_amount": dollar_amount,
                            **metrics
                        })
        
        # Create DataFrame
        df = pd.DataFrame(results)
        
        # Attach individual trades to DataFrame
        df.attrs['individual_trades'] = individual_trades
        
        return df, individual_trades
    
    def _preprocess_market_snapshots(self, market: Dict) -> Optional[Dict]:
        """Pre-process market to load snapshots."""
        market_id = market.get("id")
        
        # Determine which database to use
        orderbook_db = None
        if market.get("_market_type") == "15m" and self.orderbook_db_15m:
            orderbook_db = self.orderbook_db_15m
        elif market.get("_market_type") == "1h" and self.orderbook_db_1h:
            orderbook_db = self.orderbook_db_1h
        
        if not orderbook_db:
            return None
        
        # Load snapshots
        with orderbook_db.get_session() as session:
            from agents.polymarket.orderbook_db import BTC15MinOrderbookSnapshot, BTC1HourOrderbookSnapshot
            
            if market.get("_market_type") == "15m":
                snapshot_class = BTC15MinOrderbookSnapshot
            else:
                snapshot_class = BTC1HourOrderbookSnapshot
            
            snapshots = session.query(snapshot_class).filter(
                snapshot_class.market_id == market_id
            ).order_by(snapshot_class.timestamp).all()
        
        if not snapshots:
            return None
        
        # Enrich with outcome prices
        outcome_prices_raw = market.get("outcomePrices", {})
        if not outcome_prices_raw:
            market_info = enrich_market_from_api(market_id, self.market_fetcher)
            if market_info:
                outcome_prices_raw = market_info.get("outcomePrices", {})
        
        return {
            "market_id": market_id,
            "snapshots": snapshots,
            "outcomePrices": outcome_prices_raw,
            "_market_type": market.get("_market_type"),
        }

