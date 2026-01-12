"""
Market time calculation utilities for threshold strategy.

Pure functions for calculating time remaining until market resolution.
"""
from typing import Optional, Dict
from datetime import datetime, timezone, timedelta


def get_minutes_until_resolution(market: Dict) -> Optional[float]:
    """
    Calculate minutes remaining until market resolution.
    
    Handles both 15-minute and 1-hour markets:
    - For 15-minute markets: extracts timestamp from slug, calculates end time
    - For 1-hour markets: uses endDate from market dict
    
    Args:
        market: Market dictionary
    
    Returns:
        Minutes remaining until resolution, or None if cannot determine
    """
    from agents.polymarket.btc_market_detector import extract_timestamp_from_slug, _parse_datetime_safe
    
    now_utc = datetime.now(timezone.utc)
    
    # For 15-minute markets, extract actual end time from slug timestamp
    event_slug = market.get("_event_slug", "")
    if event_slug and "btc-updown-15m-" in event_slug:
        # Extract timestamp from slug: btc-updown-15m-{timestamp}
        timestamp = extract_timestamp_from_slug(event_slug)
        if timestamp:
            # Timestamp is the START of the 15-minute window (in UTC)
            start_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            # End is 15 minutes later
            end_dt = start_dt.replace(second=0, microsecond=0) + timedelta(minutes=15)
            
            # Calculate minutes remaining
            time_remaining = end_dt - now_utc
            return time_remaining.total_seconds() / 60.0
    
    # For 1-hour markets, use endDate
    end_date = market.get("endDate") or market.get("endDateIso")
    if end_date:
        end_dt = _parse_datetime_safe(end_date)
        if end_dt:
            # Calculate minutes remaining
            time_remaining = end_dt - now_utc
            return time_remaining.total_seconds() / 60.0
    
    # Fallback: try startDate/endDate if available
    start_date = market.get("startDate") or market.get("startDateIso")
    if start_date and end_date:
        start_dt = _parse_datetime_safe(start_date)
        end_dt = _parse_datetime_safe(end_date)
        if start_dt and end_dt:
            # Calculate minutes remaining
            time_remaining = end_dt - now_utc
            return time_remaining.total_seconds() / 60.0
    
    return None
