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

