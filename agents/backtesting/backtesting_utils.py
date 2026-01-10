"""
General-purpose utilities for backtesting strategies.

This module provides reusable functions for:
- Market data fetching and enrichment
- Outcome price parsing
- Performance metrics calculation
- Orderbook data utilities
- Market date/time parsing
"""
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone
import numpy as np
import pandas as pd

from agents.backtesting.market_fetcher import HistoricalMarketFetcher
from agents.polymarket.orderbook_db import OrderbookDatabase
from agents.polymarket.orderbook_query import OrderbookQuery

logger = logging.getLogger(__name__)


def parse_market_dates(market: Dict) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Parse start and end dates from market dict.
    
    Args:
        market: Market dict with startDate/startDateIso and endDate/endDateIso
        
    Returns:
        Tuple of (start_date, end_date) or (None, None) if not found
    """
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
    
    return market_start, market_end


def enrich_market_from_api(market_id: str, market_fetcher: HistoricalMarketFetcher) -> Optional[Dict]:
    """
    Fetch and enrich market data from Polymarket API.
    
    Args:
        market_id: Market ID to fetch
        market_fetcher: HistoricalMarketFetcher instance
        
    Returns:
        Market dict with API data or None if fetch failed
    """
    try:
        import httpx
        from agents.utils.proxy_config import get_proxy_dict
        
        url = f"{market_fetcher.gamma_markets_endpoint}/{market_id}"
        proxies = get_proxy_dict()
        response = httpx.get(url, proxies=proxies, timeout=10.0)
        
        if response.status_code == 200:
            market_info = response.json()
            if isinstance(market_info, list) and len(market_info) > 0:
                market_info = market_info[0]
            return market_info
    except Exception as e:
        logger.debug(f"Could not fetch market {market_id} details: {e}")
    
    return None


def parse_outcome_price(
    outcome_prices_raw: any,
    trigger_side: str,
    market_id: Optional[str] = None,
    market_fetcher: Optional[HistoricalMarketFetcher] = None
) -> Optional[float]:
    """
    Parse outcome price for a given side (YES or NO).
    
    Args:
        outcome_prices_raw: Outcome prices in various formats (dict, list, JSON string, or None)
        trigger_side: "YES" or "NO"
        market_id: Optional market ID to fetch from API if outcome_prices_raw is None
        market_fetcher: Optional HistoricalMarketFetcher to fetch from API
        
    Returns:
        Outcome price (0.0 to 1.0) or None if not found
    """
    # If no outcome prices provided, try to fetch from API
    if not outcome_prices_raw and market_id and market_fetcher:
        market_info = enrich_market_from_api(market_id, market_fetcher)
        if market_info:
            outcome_prices_raw = market_info.get("outcomePrices", {})
    
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
    outcome_price = None
    
    if isinstance(outcome_prices_raw, list) and len(outcome_prices_raw) >= 2:
        # List format: [outcome1_price, outcome2_price]
        # Outcome 1 = YES, Outcome 2 = NO
        if trigger_side == "YES":
            try:
                outcome_price = float(outcome_prices_raw[0])
            except (ValueError, TypeError):
                outcome_price = None
        else:  # NO
            try:
                outcome_price = float(outcome_prices_raw[1])
            except (ValueError, TypeError):
                outcome_price = None
    elif isinstance(outcome_prices_raw, dict):
        # Dict format: {"Yes": 1, "No": 0}
        if trigger_side == "YES":
            outcome_price = outcome_prices_raw.get("Yes", None)
        else:
            outcome_price = outcome_prices_raw.get("No", None)
    
    if outcome_price is not None:
        try:
            return float(outcome_price)
        except (ValueError, TypeError):
            return None
    
    return None


def group_snapshots_by_outcome(snapshots: List) -> Tuple[List, List]:
    """
    Group orderbook snapshots by outcome (YES/NO or Outcome 1/2).
    
    Args:
        snapshots: List of orderbook snapshot objects with 'outcome' attribute
        
    Returns:
        Tuple of (yes_snapshots, no_snapshots)
    """
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
    
    # For BTC markets: Outcome 1 = YES (up), Outcome 2 = NO (down)
    yes_snapshots = outcome1_snapshots
    no_snapshots = outcome2_snapshots
    
    return yes_snapshots, no_snapshots


def get_highest_bid_from_orderbook(snapshot) -> Optional[float]:
    """
    Get the highest bid price from an orderbook snapshot's bids column.
    
    Args:
        snapshot: Orderbook snapshot object with 'bids' attribute
        
    Returns:
        Highest bid price or None if not found
    """
    bids = snapshot.bids if hasattr(snapshot, 'bids') and snapshot.bids else []
    if not isinstance(bids, list) or len(bids) == 0:
        return None
    
    highest_bid = None
    for bid in bids:
        if isinstance(bid, (list, tuple)) and len(bid) >= 1:
            try:
                bid_price = float(bid[0])
                if highest_bid is None or bid_price > highest_bid:
                    highest_bid = bid_price
            except (ValueError, TypeError):
                continue
    
    return highest_bid


def get_lowest_ask_from_orderbook(snapshot) -> Optional[float]:
    """
    Get the lowest ask price from an orderbook snapshot's asks column.
    
    Args:
        snapshot: Orderbook snapshot object with 'asks' attribute
        
    Returns:
        Lowest ask price or None if not found
    """
    asks = snapshot.asks if hasattr(snapshot, 'asks') and snapshot.asks else []
    if not isinstance(asks, list) or len(asks) == 0:
        return None
    
    lowest_ask = None
    for ask in asks:
        if isinstance(ask, (list, tuple)) and len(ask) >= 1:
            try:
                ask_price = float(ask[0])
                if lowest_ask is None or ask_price < lowest_ask:
                    lowest_ask = ask_price
            except (ValueError, TypeError):
                continue
    
    return lowest_ask


def walk_orderbook_upward_from_bid(
    snapshot, 
    bid_price: float, 
    dollar_amount: float
) -> Tuple[Optional[float], float, float]:
    """
    Walk the orderbook upward from bid_price to spend dollar_amount.
    Only considers asks at or above bid_price (conservative assumption).
    
    Args:
        snapshot: Orderbook snapshot object with 'asks' attribute
        bid_price: The bid price we're placing (start walking from here)
        dollar_amount: Dollar amount we want to spend
        
    Returns:
        Tuple of (weighted_average_fill_price, filled_shares, dollars_spent)
        - weighted_average_fill_price: None if no fill possible, otherwise the weighted avg price
        - filled_shares: Number of shares actually filled
        - dollars_spent: Actual dollars spent (may be less than dollar_amount if insufficient liquidity)
    """
    asks = snapshot.asks if hasattr(snapshot, 'asks') and snapshot.asks else []
    if not isinstance(asks, list) or len(asks) == 0:
        return None, 0.0, 0.0
    
    # Filter asks: only consider those >= bid_price (walk upward, ignore below)
    eligible_asks = []
    for ask in asks:
        if isinstance(ask, (list, tuple)) and len(ask) >= 2:
            try:
                ask_price = float(ask[0])
                ask_size = float(ask[1])
                if ask_price >= bid_price:  # Only consider asks at or above our bid
                    eligible_asks.append((ask_price, ask_size))
            except (ValueError, TypeError):
                continue
    
    if not eligible_asks:
        return None, 0.0, 0.0
    
    # Sort by price ascending (walk upward)
    eligible_asks.sort(key=lambda x: x[0])
    
    # Walk the book, consuming volume until we've spent dollar_amount
    total_cost = 0.0
    filled_shares = 0.0
    remaining_dollars = dollar_amount
    
    for ask_price, ask_size in eligible_asks:
        if remaining_dollars <= 0:
            break
        
        # How many shares can we buy with remaining dollars at this price?
        shares_affordable = remaining_dollars / ask_price
        
        # How many shares can we take from this level?
        shares_to_take = min(ask_size, shares_affordable)
        
        # Calculate cost for these shares
        cost_for_shares = ask_price * shares_to_take
        
        # Add to our fill
        total_cost += cost_for_shares
        filled_shares += shares_to_take
        remaining_dollars -= cost_for_shares
    
    if filled_shares == 0:
        return None, 0.0, 0.0
    
    # Calculate weighted average fill price
    weighted_avg_price = total_cost / filled_shares
    
    # Actual dollars spent
    dollars_spent = total_cost
    
    return weighted_avg_price, filled_shares, dollars_spent


def walk_orderbook_downward_from_ask(
    snapshot,
    ask_price: float,
    shares_to_sell: float
) -> Tuple[Optional[float], float, float]:
    """
    Walk the orderbook downward from ask_price to sell shares_to_sell.
    Starts at ask_price and walks down (accepts lower bid prices) if needed to fill all shares.
    
    Args:
        snapshot: Orderbook snapshot object with 'bids' attribute
        ask_price: The ask price we're placing (start walking from here)
        shares_to_sell: Number of shares we want to sell
        
    Returns:
        Tuple of (weighted_average_fill_price, filled_shares, dollars_received)
        - weighted_average_fill_price: None if no fill possible, otherwise the weighted avg price
        - filled_shares: Number of shares actually sold
        - dollars_received: Actual dollars received (may be less than shares_to_sell * ask_price if walking down)
    """
    bids = snapshot.bids if hasattr(snapshot, 'bids') and snapshot.bids else []
    if not isinstance(bids, list) or len(bids) == 0:
        return None, 0.0, 0.0
    
    # Filter bids: only consider those >= ask_price initially (conservative)
    # But if we need more volume, we'll walk down below ask_price
    eligible_bids = []
    for bid in bids:
        if isinstance(bid, (list, tuple)) and len(bid) >= 2:
            try:
                bid_price = float(bid[0])
                bid_size = float(bid[1])
                # Start with bids >= ask_price, but we'll expand if needed
                eligible_bids.append((bid_price, bid_size))
            except (ValueError, TypeError):
                continue
    
    if not eligible_bids:
        return None, 0.0, 0.0
    
    # Sort by price descending (highest bids first - we want best prices)
    eligible_bids.sort(key=lambda x: x[0], reverse=True)
    
    # Walk the book, selling shares until we've sold shares_to_sell
    total_revenue = 0.0
    filled_shares = 0.0
    remaining_shares = shares_to_sell
    
    # First pass: try to sell at ask_price or better (bids >= ask_price)
    for bid_price, bid_size in eligible_bids:
        if remaining_shares <= 0:
            break
        
        # Only accept bids at or above our ask_price initially
        if bid_price < ask_price:
            continue  # Skip bids below our ask price in first pass
        
        # How many shares can we sell to this bid level?
        shares_to_sell_here = min(bid_size, remaining_shares)
        
        # Calculate revenue for these shares
        revenue_for_shares = bid_price * shares_to_sell_here
        
        # Add to our fill
        total_revenue += revenue_for_shares
        filled_shares += shares_to_sell_here
        remaining_shares -= shares_to_sell_here
    
    # Second pass: if we still have shares to sell, walk down (accept lower prices)
    if remaining_shares > 0:
        for bid_price, bid_size in eligible_bids:
            if remaining_shares <= 0:
                break
            
            # Now accept bids below ask_price (walking down)
            # Skip bids we already used in first pass
            if bid_price >= ask_price:
                # Check if we already used this bid level
                # (In first pass, we might have partially used it)
                continue
            
            # How many shares can we sell to this bid level?
            shares_to_sell_here = min(bid_size, remaining_shares)
            
            # Calculate revenue for these shares
            revenue_for_shares = bid_price * shares_to_sell_here
            
            # Add to our fill
            total_revenue += revenue_for_shares
            filled_shares += shares_to_sell_here
            remaining_shares -= shares_to_sell_here
    
    if filled_shares == 0:
        return None, 0.0, 0.0
    
    # Calculate weighted average fill price
    weighted_avg_price = total_revenue / filled_shares
    
    # Actual dollars received
    dollars_received = total_revenue
    
    return weighted_avg_price, filled_shares, dollars_received


def calculate_polymarket_fee(price: float, trade_value: float) -> float:
    """
    Calculate Polymarket trading fee based on price and trade value.
    
    Fee formula: Fee = trade_value × feeRate × (p × (1-p))^exponent
    Where:
    - trade_value = Total dollar value of the trade
    - p = Price of the shares (0 to 1)
    - feeRate = 0.25
    - exponent = 2
    
    This results in maximum fees at p=0.50, decreasing toward extremes (0.01 and 0.99).
    
    Args:
        price: Share price (0.01 to 0.99)
        trade_value: Total dollar value of the trade
        
    Returns:
        Fee in USDC
    """
    if price <= 0 or price >= 1 or trade_value <= 0:
        return 0.0
    
    # Clamp price to valid range
    price = max(0.01, min(0.99, price))
    
    # Fee parameters
    fee_rate = 0.25
    exponent = 2
    
    # Calculate fee: trade_value × feeRate × (p × (1-p))^exponent
    p_times_one_minus_p = price * (1.0 - price)
    fee = trade_value * fee_rate * (p_times_one_minus_p ** exponent)
    
    # Minimum fee precision is 0.0001 USDC (very small trades may round to 0)
    return max(0.0, fee)


def calculate_metrics(trades: List[Dict]) -> Dict:
    """
    Calculate performance metrics from a list of trade results.
    
    Args:
        trades: List of trade dicts, each with at least 'roi' and optionally 'is_win'
        
    Returns:
        Dict with metrics: num_trades, wins, losses, win_rate, avg_roi, sharpe_ratio, total_roi
    """
    if not trades:
        return {
            "num_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_roi": 0.0,
            "sharpe_ratio": 0.0,
            "total_roi": 0.0,
        }
    
    rois = [t.get("roi", 0.0) for t in trades]
    wins = sum(1 for t in trades if t.get("is_win", t.get("roi", 0.0) > 0))
    losses = len(trades) - wins
    
    avg_roi = np.mean(rois)
    std_roi = np.std(rois)
    sharpe_ratio = avg_roi / std_roi if std_roi > 0 else 0.0
    win_rate = wins / len(trades) if trades else 0.0
    total_roi = sum(rois)
    
    return {
        "num_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_roi": avg_roi,
        "sharpe_ratio": sharpe_ratio,
        "total_roi": total_roi,
    }


def calculate_kelly_fraction(trades: List[Dict], bet_size: float = 4000.0) -> Optional[float]:
    """
    Calculate the Kelly fraction (optimal bet size as fraction of bankroll) from trade results.
    
    Uses the Generalized Kelly Criterion for multiple outcomes with different ROI values.
    Maximizes expected logarithmic growth: E[ln(1 + f * ROI)]
    
    The optimal f* is found by solving:
        Σ (ROI_i / (1 + f * ROI_i)) = 0
    
    where each trade i has ROI_i and equal probability (1/n).
    
    Args:
        trades: List of trade dicts, each with 'roi' and optionally 'is_win'
        bet_size: The bet size used for these trades (default $4000)
                  Only used to filter trades if needed
        
    Returns:
        Kelly fraction (0.0 to 1.0), or None if calculation is invalid
    """
    if not trades:
        return None
    
    # Filter to trades with the specified bet size (if dollar_amount field exists)
    relevant_trades = trades
    if any('dollar_amount' in t for t in trades):
        relevant_trades = [t for t in trades if abs(t.get('dollar_amount', bet_size) - bet_size) < 0.01]
    
    if not relevant_trades:
        return None
    
    # Extract ROI values
    rois = [t.get("roi", 0.0) for t in relevant_trades]
    rois = np.array(rois)
    
    if len(rois) == 0:
        return None
    
    # Check if we have any positive expected return
    if np.mean(rois) <= 0:
        return None  # No positive expected return
    
    # Find f that maximizes E[ln(1 + f * ROI)]
    # We need to solve: Σ (ROI_i / (1 + f * ROI_i)) = 0
    # Use numerical optimization (binary search or scipy)
    
    def kelly_objective(f: float) -> float:
        """Derivative of expected log growth. We want this to be 0."""
        # Handle cases where 1 + f * ROI <= 0 (would cause negative bankroll)
        valid_mask = (1 + f * rois) > 0
        if not np.any(valid_mask):
            return 1e10  # Penalize invalid f
        
        # Calculate derivative: Σ (ROI_i / (1 + f * ROI_i))
        derivative = np.sum(rois[valid_mask] / (1 + f * rois[valid_mask])) / len(rois)
        return abs(derivative)  # We want to minimize the absolute value
    
    def kelly_growth(f: float) -> float:
        """Expected log growth rate: E[ln(1 + f * ROI)]"""
        valid_mask = (1 + f * rois) > 0
        if not np.any(valid_mask):
            return -1e10  # Very negative for invalid f
        
        growth = np.mean(np.log(1 + f * rois[valid_mask]))
        return growth
    
    # Find the maximum f where 1 + f * min(ROI) > 0
    # This ensures we don't go bankrupt on the worst outcome
    min_roi = np.min(rois)
    if min_roi <= -1.0:
        # If we can lose more than 100%, limit f to prevent bankruptcy
        max_f = -0.99 / min_roi  # Ensure 1 + f * min_roi > 0.01
    else:
        max_f = 1.0
    
    max_f = min(max_f, 1.0)  # Never bet more than 100% of bankroll
    
    # Use binary search to find f where derivative is closest to 0
    # Or use scipy.optimize if available
    try:
        from scipy.optimize import minimize_scalar
        result = minimize_scalar(
            lambda f: -kelly_growth(f),  # Minimize negative growth (maximize growth)
            bounds=(0.0, max_f),
            method='bounded'
        )
        if result.success:
            kelly_fraction = result.x
        else:
            # Fallback to binary search
            kelly_fraction = _binary_search_kelly(rois, max_f)
    except ImportError:
        # Fallback to binary search if scipy not available
        kelly_fraction = _binary_search_kelly(rois, max_f)
    
    # Clamp to valid range [0, max_f]
    kelly_fraction = max(0.0, min(max_f, kelly_fraction))
    
    # Verify the result makes sense (positive expected growth)
    if kelly_fraction > 0:
        growth = kelly_growth(kelly_fraction)
        if growth <= 0:
            return None  # No positive growth possible
    
    return kelly_fraction


def _binary_search_kelly(rois: np.ndarray, max_f: float, tolerance: float = 1e-6) -> float:
    """
    Binary search to find Kelly fraction that maximizes expected log growth.
    
    Finds f that maximizes: E[ln(1 + f * ROI)]
    by finding f where derivative Σ (ROI_i / (1 + f * ROI_i)) ≈ 0.
    """
    def derivative(f: float) -> float:
        """Derivative of expected log growth."""
        valid_mask = (1 + f * rois) > 0
        if not np.any(valid_mask):
            return 1e10
        return np.sum(rois[valid_mask] / (1 + f * rois[valid_mask])) / len(rois)
    
    def growth(f: float) -> float:
        """Expected log growth rate."""
        valid_mask = (1 + f * rois) > 0
        if not np.any(valid_mask):
            return -1e10
        return np.mean(np.log(1 + f * rois[valid_mask]))
    
    # Binary search for f where derivative ≈ 0 (maximizes growth)
    left, right = 0.0, max_f
    best_f = 0.0
    best_growth = growth(0.0)
    
    for _ in range(100):  # Max iterations
        mid = (left + right) / 2.0
        deriv = derivative(mid)
        mid_growth = growth(mid)
        
        # Track best growth found
        if mid_growth > best_growth:
            best_growth = mid_growth
            best_f = mid
        
        if abs(deriv) < tolerance:
            break
        
        if deriv > 0:
            left = mid   # Derivative positive, can increase f
        else:
            right = mid  # Derivative negative, need to decrease f
    
    return best_f


def calculate_kelly_roi(trades: List[Dict], bet_size: float = 4000.0, bankroll: float = 100000.0) -> Optional[float]:
    """
    Calculate the expected ROI if betting at Kelly optimal sizing.
    
    Uses the Generalized Kelly Criterion growth rate formula:
        g(f) = (1/n) * Σ ln(1 + f * ROI_i)
    
    where f is the Kelly fraction and ROI_i is the ROI for each trade.
    
    Then converts growth rate to expected ROI per bet.
    
    Args:
        trades: List of trade dicts
        bet_size: The bet size used for these trades (default $4000)
        bankroll: Total bankroll for Kelly sizing calculation (default $100k)
        
    Returns:
        Expected ROI at Kelly optimal sizing, or None if calculation is invalid
    """
    kelly_fraction = calculate_kelly_fraction(trades, bet_size)
    if kelly_fraction is None or kelly_fraction <= 0:
        return None
    
    # Filter to trades with the specified bet size
    relevant_trades = trades
    if any('dollar_amount' in t for t in trades):
        relevant_trades = [t for t in trades if abs(t.get('dollar_amount', bet_size) - bet_size) < 0.01]
    
    if not relevant_trades:
        return None
    
    # Extract ROI values
    rois = np.array([t.get("roi", 0.0) for t in relevant_trades])
    
    if len(rois) == 0:
        return None
    
    # Calculate growth rate at Kelly fraction: g(f*) = (1/n) * Σ ln(1 + f* * ROI_i)
    # Only consider trades where 1 + f * ROI > 0 (no bankruptcy)
    valid_mask = (1 + kelly_fraction * rois) > 0
    if not np.any(valid_mask):
        return None
    
    growth_rate = np.mean(np.log(1 + kelly_fraction * rois[valid_mask]))
    
    # Convert growth rate to expected ROI per bet
    # The growth rate is in log space, so expected return = exp(growth_rate) - 1
    import math
    expected_return = math.exp(growth_rate) - 1
    
    return expected_return


def get_markets_with_orderbooks(
    use_15m_table: bool = True,
    use_1h_table: bool = True,
    orderbook_db_15m: Optional[OrderbookDatabase] = None,
    orderbook_db_1h: Optional[OrderbookDatabase] = None,
    market_fetcher: Optional[HistoricalMarketFetcher] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    max_markets: Optional[int] = None
) -> List[Dict]:
    """
    Get markets that have orderbook data recorded from btc_15_min_table and/or btc_1_hour_table.
    
    This is a general-purpose function that can be used by any backtesting strategy.
    
    Args:
        use_15m_table: If True, query btc_15_min_table
        use_1h_table: If True, query btc_1_hour_table
        orderbook_db_15m: OrderbookDatabase instance for 15m table (created if None)
        orderbook_db_1h: OrderbookDatabase instance for 1h table (created if None)
        market_fetcher: HistoricalMarketFetcher instance (created if None)
        start_date: Optional start date filter
        end_date: Optional end date filter
        max_markets: Optional maximum number of markets to return
        
    Returns:
        List of market dicts with orderbook data
    """
    from sqlalchemy import text
    
    if market_fetcher is None:
        market_fetcher = HistoricalMarketFetcher()
    
    markets_by_id = {}
    
    # Query 15-minute table if enabled
    if use_15m_table:
        if orderbook_db_15m is None:
            orderbook_db_15m = OrderbookDatabase(use_btc_15_min_table=True)
        
        with orderbook_db_15m.get_session() as session:
            query = """
                SELECT market_id,
                       COUNT(*) as snapshot_count,
                       MIN(timestamp) as first_snapshot,
                       MAX(timestamp) as last_snapshot
                FROM btc_15_min_table
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
                
                # Mark as 15-minute market
                markets_by_id[market_id] = {
                    "id": market_id,
                    "first_snapshot": first_snapshot,
                    "last_snapshot": last_snapshot,
                    "_snapshot_count": snapshot_count,
                    "_market_type": "15m",
                }
    
    # Query 1-hour table if enabled
    if use_1h_table:
        if orderbook_db_1h is None:
            orderbook_db_1h = OrderbookDatabase(use_btc_1_hour_table=True)
        
        with orderbook_db_1h.get_session() as session:
            query = """
                SELECT market_id,
                       COUNT(*) as snapshot_count,
                       MIN(timestamp) as first_snapshot,
                       MAX(timestamp) as last_snapshot
                FROM btc_1_hour_table
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
                
                # If market already exists (from 15m table), skip it (prefer 15m data)
                if market_id in markets_by_id:
                    logger.debug(f"Market {market_id} found in both tables, using 15m table data")
                    continue
                
                markets_by_id[market_id] = {
                    "id": market_id,
                    "first_snapshot": first_snapshot,
                    "last_snapshot": last_snapshot,
                    "_snapshot_count": snapshot_count,
                    "_market_type": "1h",
                }
    
    if not markets_by_id:
        logger.warning("No markets with orderbook data found in database")
        return []
    
    # Enrich with market data from API
    markets = []
    for market_id, market_data in markets_by_id.items():
        market_info = enrich_market_from_api(market_id, market_fetcher)
        if market_info:
            market_data.update(market_info)
        
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

