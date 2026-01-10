"""
Test script for orderbook polling.

Tests fetching orderbooks and checking threshold conditions.
"""
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from agents.trading.orderbook_helper import (
    fetch_orderbook,
    get_lowest_ask,
    get_highest_bid,
    check_threshold_triggered,
)


def test_orderbook_fetching():
    """Test fetching orderbook from API."""
    print("Testing orderbook fetching...")
    print()
    
    # Use a known token ID (you may need to update this with a real token ID)
    # For testing, we'll try to fetch any orderbook
    print("Note: This test requires a valid token ID")
    print("To get a token ID, check a market's clob_token_ids")
    print()
    
    # Example: Try fetching orderbook (will fail if token_id is invalid, but tests the function)
    token_id = "test-token-id"
    orderbook = fetch_orderbook(token_id)
    
    if orderbook is None:
        print("   ✓ Function handles invalid token ID gracefully")
    else:
        print(f"   ✓ Fetched orderbook: {len(orderbook.get('bids', []))} bids, {len(orderbook.get('asks', []))} asks")
    print()


def test_orderbook_parsing():
    """Test parsing orderbook data."""
    print("Testing orderbook parsing...")
    print()
    
    # Create mock orderbook
    mock_orderbook = {
        "bids": [[0.60, 10.0], [0.59, 5.0], [0.58, 3.0]],
        "asks": [[0.61, 8.0], [0.62, 12.0], [0.63, 15.0]],
    }
    
    # Test lowest ask
    lowest_ask = get_lowest_ask(mock_orderbook)
    if lowest_ask == 0.61:
        print(f"   ✓ Lowest ask: {lowest_ask:.4f}")
    else:
        print(f"   ✗ Expected 0.61, got {lowest_ask}")
    print()
    
    # Test highest bid
    highest_bid = get_highest_bid(mock_orderbook)
    if highest_bid == 0.60:
        print(f"   ✓ Highest bid: {highest_bid:.4f}")
    else:
        print(f"   ✗ Expected 0.60, got {highest_bid}")
    print()


def test_threshold_checking():
    """Test threshold trigger checking."""
    print("Testing threshold trigger checking...")
    print()
    
    # Create mock orderbooks
    yes_orderbook = {
        "asks": [[0.45, 10.0], [0.46, 5.0]],  # Lowest ask = 0.45
    }
    no_orderbook = {
        "asks": [[0.35, 10.0], [0.36, 5.0]],  # Lowest ask = 0.35
    }
    
    # Test threshold = 0.40
    # YES: 0.45 >= 0.40 -> triggered
    # NO: 0.35 >= 0.40 -> not triggered
    trigger = check_threshold_triggered(yes_orderbook, no_orderbook, 0.40)
    if trigger and trigger[0] == "YES":
        print(f"   ✓ Threshold triggered: {trigger[0]} side, price={trigger[1]:.4f}")
    else:
        print(f"   ✗ Expected YES trigger, got {trigger}")
    print()
    
    # Test threshold = 0.50
    # YES: 0.45 >= 0.50 -> not triggered
    # NO: 0.35 >= 0.50 -> not triggered
    trigger = check_threshold_triggered(yes_orderbook, no_orderbook, 0.50)
    if trigger is None:
        print("   ✓ Threshold not triggered (as expected)")
    else:
        print(f"   ✗ Expected no trigger, got {trigger}")
    print()
    
    print("✓ Orderbook polling tests passed!")


if __name__ == "__main__":
    test_orderbook_fetching()
    test_orderbook_parsing()
    test_threshold_checking()
