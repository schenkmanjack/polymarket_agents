"""
Trading utilities for threshold strategy.

This package contains pure utility functions for:
- Order calculations (size, fees, Kelly)
- Market resolution calculations (ROI, payout)
- Order status parsing
- Trade validation
"""

from agents.trading.utils.order_calculations import (
    calculate_fee_multiplier,
    calculate_order_size_with_fees,
    calculate_kelly_amount,
)
from agents.trading.utils.market_resolution_helpers import (
    calculate_roi,
    calculate_payout_for_filled_sell,
    calculate_payout_for_unfilled_sell,
    determine_bet_outcome,
)
from agents.trading.utils.order_status_helpers import (
    parse_order_status,
    is_order_filled,
    is_order_cancelled,
    is_order_partial_fill,
)
from agents.trading.utils.trade_validation import (
    validate_trade_for_resolution,
    check_order_belongs_to_market,
)
from agents.trading.utils.market_time_helpers import (
    get_minutes_until_resolution,
)

__all__ = [
    # Order calculations
    "calculate_fee_multiplier",
    "calculate_order_size_with_fees",
    "calculate_kelly_amount",
    # Market resolution
    "calculate_roi",
    "calculate_payout_for_filled_sell",
    "calculate_payout_for_unfilled_sell",
    "determine_bet_outcome",
    # Order status
    "parse_order_status",
    "is_order_filled",
    "is_order_cancelled",
    "is_order_partial_fill",
    # Validation
    "validate_trade_for_resolution",
    "check_order_belongs_to_market",
    # Market time
    "get_minutes_until_resolution",
]
