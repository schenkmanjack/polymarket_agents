"""
Test script for BTC backtesting framework.

Usage:
    python scripts/python/test_backtesting.py
"""
import sys
import os
from datetime import datetime, timedelta, timezone
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.backtesting.market_fetcher import HistoricalMarketFetcher
from agents.backtesting.btc_backtester import BTCBacktester, run_backtest
from agents.connectors.btc_data import BTCDataFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_market_fetcher():
    """Test historical market fetcher."""
    print("=" * 70)
    print("Testing Historical Market Fetcher")
    print("=" * 70)
    
    fetcher = HistoricalMarketFetcher()
    
    # Fetch a small number of markets for testing
    print("\nFetching closed BTC 15-minute markets...")
    markets = fetcher.get_closed_btc_15m_markets(max_markets=10)
    
    if not markets:
        print("✗ No markets found")
        return False
    
    print(f"✓ Found {len(markets)} markets")
    
    # Display sample market
    if markets:
        market = markets[0]
        print(f"\nSample market:")
        print(f"  ID: {market.get('id')}")
        print(f"  Slug: {market.get('_event_slug')}")
        print(f"  Start time: {market.get('_market_start_time')}")
        print(f"  Question: {market.get('question', '')[:80]}...")
    
    return True


def test_market_enrichment():
    """Test enriching markets with BTC data."""
    print("\n" + "=" * 70)
    print("Testing Market Enrichment with BTC Data")
    print("=" * 70)
    
    fetcher = HistoricalMarketFetcher()
    btc_fetcher = BTCDataFetcher()
    
    # Fetch a few markets
    markets = fetcher.get_closed_btc_15m_markets(max_markets=3)
    
    if not markets:
        print("✗ No markets found")
        return False
    
    print(f"\nEnriching {len(markets)} markets with BTC data...")
    
    for i, market in enumerate(markets):
        enriched = fetcher.enrich_market_with_btc_data(market, btc_fetcher)
        
        print(f"\nMarket {i+1}:")
        print(f"  Start time: {enriched.get('_market_start_time')}")
        print(f"  BTC start price: ${enriched.get('_btc_start_price', 'N/A')}")
        print(f"  BTC end price: ${enriched.get('_btc_end_price', 'N/A')}")
        print(f"  Actual direction: {enriched.get('_btc_actual_direction', 'N/A')}")
        print(f"  Price change: ${enriched.get('_btc_price_change', 'N/A'):.2f}" if enriched.get('_btc_price_change') else "  Price change: N/A")
    
    return True


def test_backtester():
    """Test backtesting framework."""
    print("\n" + "=" * 70)
    print("Testing Backtesting Framework")
    print("=" * 70)
    
    # Create backtester with baseline model
    backtester = BTCBacktester(model_name="baseline", lookback_minutes=200)
    
    # Run backtest on a small number of markets
    print("\nRunning backtest on recent markets...")
    
    # Get markets from last 7 days
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=7)
    
    results_df = backtester.run_backtest(
        start_date=start_date,
        end_date=end_date,
        max_markets=5,  # Small number for testing
        enrich_with_btc_data=True
    )
    
    if results_df.empty:
        print("✗ No results generated")
        return False
    
    print(f"\n✓ Generated {len(results_df)} results")
    print(f"\nResults summary:")
    print(results_df[["market_id", "predicted_direction", "actual_direction", "is_correct", "pnl"]].head())
    
    return True


def test_convenience_function():
    """Test the convenience function."""
    print("\n" + "=" * 70)
    print("Testing Convenience Function")
    print("=" * 70)
    
    # Get markets from last 3 days
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=3)
    
    print("\nRunning backtest via convenience function...")
    results_df = run_backtest(
        model_name="baseline",
        start_date=start_date,
        end_date=end_date,
        max_markets=3
    )
    
    if results_df.empty:
        print("✗ No results generated")
        return False
    
    print(f"\n✓ Generated {len(results_df)} results")
    return True


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("BTC Backtesting Framework Test")
    print("=" * 70)
    
    results = {
        "Market Fetcher": False,
        "Market Enrichment": False,
        "Backtester": False,
        "Convenience Function": False
    }
    
    # Test market fetcher
    try:
        results["Market Fetcher"] = test_market_fetcher()
    except Exception as e:
        logger.error(f"Market fetcher test failed: {e}", exc_info=True)
    
    # Test market enrichment
    try:
        results["Market Enrichment"] = test_market_enrichment()
    except Exception as e:
        logger.error(f"Market enrichment test failed: {e}", exc_info=True)
    
    # Test backtester
    try:
        results["Backtester"] = test_backtester()
    except Exception as e:
        logger.error(f"Backtester test failed: {e}", exc_info=True)
    
    # Test convenience function
    try:
        results["Convenience Function"] = test_convenience_function()
    except Exception as e:
        logger.error(f"Convenience function test failed: {e}", exc_info=True)
    
    # Summary
    print("\n" + "=" * 70)
    print("Test Results Summary")
    print("=" * 70)
    
    for test_name, success in results.items():
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    print("\n" + "=" * 70)
    
    if all(results.values()):
        print("\n✓ All tests passed!")
    else:
        print("\n⚠ Some tests failed. Check logs for details.")


if __name__ == "__main__":
    main()

