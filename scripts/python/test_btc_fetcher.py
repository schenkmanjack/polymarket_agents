"""
Test script for BTC data fetcher.

Usage:
    python scripts/python/test_btc_fetcher.py
"""
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.connectors.btc_data import BTCDataFetcher
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_basic_fetch():
    """Test basic data fetching."""
    print("=" * 70)
    print("Test 1: Basic Data Fetching")
    print("=" * 70)
    
    fetcher = BTCDataFetcher()
    
    # Get last 24 hours of 1-minute data
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=1)
    
    print(f"\nFetching BTC data from {start_time} to {end_time}...")
    df = fetcher.get_prices(start_time, end_time, interval="1m")
    
    print(f"\n✓ Fetched {len(df)} rows")
    print(f"\nFirst few rows:")
    print(df.head())
    print(f"\nLast few rows:")
    print(df.tail())
    print(f"\nPrice range: ${df['close'].min():.2f} - ${df['close'].max():.2f}")
    print(f"Average price: ${df['close'].mean():.2f}")


def test_price_sequence():
    """Test getting price sequence for model input."""
    print("\n" + "=" * 70)
    print("Test 2: Price Sequence for Model Input")
    print("=" * 70)
    
    fetcher = BTCDataFetcher()
    
    # Get price sequence (last 200 minutes)
    timestamp = datetime.now(timezone.utc)
    print(f"\nGetting price sequence for {timestamp}...")
    print(f"Lookback: 200 minutes")
    
    sequence = fetcher.get_price_sequence(timestamp, lookback_minutes=200, interval="1m")
    
    print(f"\n✓ Sequence length: {len(sequence)}")
    print(f"First 10 prices: {[f'${p:.2f}' for p in sequence[:10]]}")
    print(f"Last 10 prices: {[f'${p:.2f}' for p in sequence[-10:]]}")
    print(f"\nThis sequence can be passed directly to Lag-Llama or Chronos-Bolt")


def test_price_at_time():
    """Test getting price at specific timestamp."""
    print("\n" + "=" * 70)
    print("Test 3: Price at Specific Timestamp")
    print("=" * 70)
    
    fetcher = BTCDataFetcher()
    
    # Get price 1 hour ago
    timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
    print(f"\nGetting BTC price at {timestamp}...")
    
    price = fetcher.get_price_at_time(timestamp, tolerance_seconds=60)
    
    if price:
        print(f"✓ Price: ${price:.2f}")
    else:
        print("✗ Price not found")


def test_cache():
    """Test caching functionality."""
    print("\n" + "=" * 70)
    print("Test 4: Cache Functionality")
    print("=" * 70)
    
    fetcher = BTCDataFetcher()
    
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=2)
    
    print(f"\nFirst fetch (will cache): {start_time} to {end_time}...")
    import time
    start = time.time()
    df1 = fetcher.get_prices(start_time, end_time, interval="5m", use_cache=True)
    time1 = time.time() - start
    print(f"✓ Fetched {len(df1)} rows in {time1:.2f}s")
    
    print(f"\nSecond fetch (from cache): {start_time} to {end_time}...")
    start = time.time()
    df2 = fetcher.get_prices(start_time, end_time, interval="5m", use_cache=True)
    time2 = time.time() - start
    print(f"✓ Loaded {len(df2)} rows from cache in {time2:.2f}s")
    
    print(f"\nCache speedup: {time1/time2:.1f}x faster")


def test_historical_data():
    """Test fetching historical data (for backtesting)."""
    print("\n" + "=" * 70)
    print("Test 5: Historical Data (for Backtesting)")
    print("=" * 70)
    
    fetcher = BTCDataFetcher()
    
    # Get data from 7 days ago
    end_time = datetime.now(timezone.utc) - timedelta(days=7)
    start_time = end_time - timedelta(days=1)
    
    print(f"\nFetching historical data: {start_time} to {end_time}...")
    df = fetcher.get_prices(start_time, end_time, interval="15m")
    
    print(f"\n✓ Fetched {len(df)} rows")
    print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"Price range: ${df['close'].min():.2f} - ${df['close'].max():.2f}")
    
    # Show how to get price sequence for a specific market start time
    market_start = df['timestamp'].iloc[0]
    print(f"\nExample: Price sequence for market starting at {market_start}")
    sequence = fetcher.get_price_sequence(market_start, lookback_minutes=200, interval="1m")
    print(f"Sequence length: {len(sequence)}")
    print(f"Last price in sequence (market start): ${sequence[-1]:.2f}")


if __name__ == "__main__":
    try:
        test_basic_fetch()
        test_price_sequence()
        test_price_at_time()
        test_cache()
        test_historical_data()
        
        print("\n" + "=" * 70)
        print("All tests completed!")
        print("=" * 70)
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)

