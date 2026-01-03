"""
Utilities for finding markets by duration (15 minutes, 1 hour, etc.)
"""
from datetime import datetime, timedelta
from typing import List, Optional, Set
import ast
from agents.polymarket.gamma import GammaMarketClient


def parse_duration_from_market(market) -> Optional[timedelta]:
    """
    Calculate market duration from startDate and endDate.
    
    Returns:
        timedelta if both dates exist, None otherwise
    """
    start_date = market.get("startDate") or market.get("startDateIso")
    end_date = market.get("endDate") or market.get("endDateIso")
    
    if not start_date or not end_date:
        return None
    
    try:
        # Parse ISO format dates
        if isinstance(start_date, str):
            start_str = start_date.replace("Z", "+00:00")
            # Fix microseconds if too long (e.g., 03753 -> 037530)
            if '.' in start_str:
                parts = start_str.split('.')
                if len(parts) > 1:
                    microsec_part = parts[1].split('+')[0].split('-')[0]
                    if len(microsec_part) > 6:
                        start_str = parts[0] + '.' + microsec_part[:6] + parts[1][len(microsec_part):]
            start = datetime.fromisoformat(start_str)
        else:
            start = start_date
        
        if isinstance(end_date, str):
            end_str = end_date.replace("Z", "+00:00")
            # Fix microseconds if too long
            if '.' in end_str:
                parts = end_str.split('.')
                if len(parts) > 1:
                    microsec_part = parts[1].split('+')[0].split('-')[0]
                    if len(microsec_part) > 6:
                        end_str = parts[0] + '.' + microsec_part[:6] + parts[1][len(microsec_part):]
            end = datetime.fromisoformat(end_str)
        else:
            end = end_date
        
        return end - start
    except Exception as e:
        # Debug: print error for troubleshooting
        # print(f"Error parsing dates: {e}, start={start_date}, end={end_date}")
        return None


def is_duration_market(market, target_duration: timedelta, tolerance: timedelta = timedelta(minutes=1)) -> bool:
    """
    Check if a market matches a target duration.
    
    Args:
        market: Market dict or object
        target_duration: Target duration (e.g., timedelta(minutes=15))
        tolerance: Allowed difference (default: 1 minute)
    
    Returns:
        True if market duration matches target
    """
    duration = parse_duration_from_market(market)
    if duration is None:
        return False
    
    diff = abs(duration - target_duration)
    return diff <= tolerance


def find_markets_by_duration(
    target_duration: timedelta,
    active_only: bool = True,
    limit: int = 1000,
    check_question_text: bool = True,
    include_closed: bool = False,  # Sometimes 15-min markets might show as closed but still active
) -> List[dict]:
    """
    Find markets that match a specific duration.
    
    Args:
        target_duration: Target duration (e.g., timedelta(minutes=15))
        active_only: Only return active markets
        limit: Maximum number of markets to check
        check_question_text: Also check question text for duration keywords
    
    Returns:
        List of market dicts matching the duration
    """
    gamma = GammaMarketClient()
    
    params = {
        "active": active_only,
        "closed": False if not include_closed else None,  # Allow closed if requested
        "archived": False,
        "limit": limit,
        "enableOrderBook": True,  # Only markets with orderbooks
    }
    # Remove None values
    params = {k: v for k, v in params.items() if v is not None}
    
    all_markets = gamma.get_markets(querystring_params=params, parse_pydantic=False)
    
    matching_markets = []
    for market in all_markets:
        # Check by duration calculation
        if is_duration_market(market, target_duration):
            matching_markets.append(market)
            continue
        
        # Also check question text and slug for keywords
        if check_question_text:
            question = (market.get("question") or "").lower()
            description = (market.get("description") or "").lower()
            slug = (market.get("slug") or "").lower()
            text = question + " " + description + " " + slug
            
            # Check for 15-minute patterns
            if target_duration == timedelta(minutes=15):
                patterns = [
                    "15m",  # Common pattern in slugs like "btc-updown-15m-..."
                    "15 min",
                    "15 minute",
                    "15 minutes",
                    "next 15",
                    "in 15 min",
                    # Time range patterns like "6:30-6:45PM" (15 min window)
                ]
                if any(pattern in text for pattern in patterns):
                    matching_markets.append(market)
                    continue
                
                # Check for time range patterns (e.g., "6:30-6:45PM")
                import re
                time_range_pattern = r'\d{1,2}:\d{2}[ap]?m?\s*-\s*\d{1,2}:\d{2}[ap]?m?'
                if re.search(time_range_pattern, question):
                    # Could be 15 minutes, verify if possible
                    matching_markets.append(market)
                    continue
            
            # Check for 1-hour patterns
            elif target_duration == timedelta(hours=1):
                patterns = [
                    "1h",  # Common pattern in slugs
                    "1 hour",
                    "1 hr",
                    "next hour",
                    "in 1 hour",
                    "60 min",
                    "60 minute",
                ]
                if any(pattern in text for pattern in patterns):
                    matching_markets.append(market)
                    continue
    
    return matching_markets


def get_token_ids_from_market(market: dict) -> List[str]:
    """
    Extract CLOB token IDs from a market dict.
    
    Args:
        market: Market dict from API
    
    Returns:
        List of token IDs
    """
    clob_token_ids = market.get("clobTokenIds")
    if not clob_token_ids:
        return []
    
    # Handle both string and list formats
    if isinstance(clob_token_ids, str):
        try:
            token_ids = ast.literal_eval(clob_token_ids)
            if isinstance(token_ids, list):
                return [str(tid) for tid in token_ids]
            else:
                return [str(token_ids)]
        except:
            return []
    elif isinstance(clob_token_ids, list):
        return [str(tid) for tid in clob_token_ids]
    
    return []


def find_15min_markets(active_only: bool = True, limit: int = 1000, check_question_text: bool = True, include_closed: bool = False) -> List[dict]:
    """Find 15-minute markets."""
    return find_markets_by_duration(timedelta(minutes=15), active_only, limit, check_question_text, include_closed)


def find_1hour_markets(active_only: bool = True, limit: int = 1000, check_question_text: bool = True) -> List[dict]:
    """Find 1-hour markets."""
    return find_markets_by_duration(timedelta(hours=1), active_only, limit, check_question_text)


def get_market_info_for_logging(market: dict) -> dict:
    """
    Extract market info needed for orderbook logging.
    
    Returns:
        Dict with market_id, market_question, and token_id -> outcome mapping
    """
    market_id = str(market.get("id", ""))
    question = market.get("question", "")
    outcomes = market.get("outcome", [])
    token_ids = get_token_ids_from_market(market)
    
    # Create mapping of token_id -> market info
    market_info = {}
    for i, token_id in enumerate(token_ids):
        outcome = outcomes[i] if i < len(outcomes) else f"Outcome {i+1}"
        market_info[token_id] = {
            "market_id": market_id,
            "market_question": question,
            "outcome": outcome,
        }
    
    return market_info

