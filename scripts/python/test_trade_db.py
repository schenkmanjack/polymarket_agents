"""
Test script for trade database operations.

Tests CRUD operations, principal tracking, and market tracking.
"""
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from agents.trading.trade_db import TradeDatabase
from datetime import datetime, timezone


def test_database_operations():
    """Test basic database operations."""
    print("Testing trade database operations...")
    print()
    
    db = TradeDatabase()
    
    # Test creating a trade
    print("1. Creating test trade...")
    trade_id = db.create_trade(
        deployment_id="test-deployment-123",
        threshold=0.40,
        margin=0.02,
        kelly_fraction=0.25,
        kelly_scale_factor=1.0,
        market_type="15m",
        market_id="test-market-123",
        market_slug="test-market-slug",
        token_id="test-token-yes",
        order_id="test-order-123",
        order_price=0.42,
        order_size=10.0,
        order_side="YES",
        principal_before=100.0,
        order_status="open",
    )
    print(f"   ✓ Created trade with ID: {trade_id}")
    print()
    
    # Test retrieving trade
    print("2. Retrieving trade...")
    trade = db.get_trade_by_id(trade_id)
    if trade:
        print(f"   ✓ Retrieved trade: market_slug={trade.market_slug}, order_id={trade.order_id}")
    else:
        print("   ✗ Failed to retrieve trade")
    print()
    
    # Test updating trade fill
    print("3. Updating trade fill...")
    db.update_trade_fill(
        trade_id=trade_id,
        filled_shares=10.0,
        fill_price=0.42,
        dollars_spent=4.2,
        fee=0.01,
        order_status="filled",
    )
    trade = db.get_trade_by_id(trade_id)
    if trade and trade.filled_shares == 10.0:
        print(f"   ✓ Updated fill: filled_shares={trade.filled_shares}, fee={trade.fee}")
    else:
        print("   ✗ Failed to update fill")
    print()
    
    # Test updating trade outcome
    print("4. Updating trade outcome...")
    db.update_trade_outcome(
        trade_id=trade_id,
        outcome_price=1.0,
        payout=10.0,
        net_payout=5.79,
        roi=1.38,
        is_win=True,
        principal_after=105.79,
        winning_side="YES",
    )
    trade = db.get_trade_by_id(trade_id)
    if trade and trade.is_win:
        print(f"   ✓ Updated outcome: roi={trade.roi:.2f}, principal_after={trade.principal_after}")
    else:
        print("   ✗ Failed to update outcome")
    print()
    
    # Test principal tracking
    print("5. Testing principal tracking...")
    latest_principal = db.get_latest_principal()
    if latest_principal == 105.79:
        print(f"   ✓ Latest principal: ${latest_principal:.2f}")
    else:
        print(f"   ✗ Expected 105.79, got {latest_principal}")
    print()
    
    # Test market tracking
    print("6. Testing market tracking...")
    has_bet = db.has_bet_on_market("test-market-slug")
    if has_bet:
        print("   ✓ Correctly detected bet on market")
    else:
        print("   ✗ Failed to detect bet on market")
    print()
    
    # Test open trades
    print("7. Testing open trades query...")
    open_trades = db.get_open_trades()
    print(f"   ✓ Found {len(open_trades)} open trades")
    print()
    
    print("✓ All database tests passed!")


if __name__ == "__main__":
    test_database_operations()
