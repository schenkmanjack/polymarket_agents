"""
Run grid search for threshold-based trading strategy.

Varies threshold (60-100%) and margin (1% to max allowed) to find optimal parameters.
"""
import sys
import os
import argparse
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.backtesting.threshold_backtester import ThresholdBacktester
import pandas as pd

def main():
    parser = argparse.ArgumentParser(description="Run threshold strategy grid search")
    parser.add_argument("--threshold-min", type=float, default=0.60, help="Minimum threshold (default: 0.60)")
    parser.add_argument("--threshold-max", type=float, default=1.00, help="Maximum threshold (default: 1.00)")
    parser.add_argument("--threshold-step", type=float, default=0.01, help="Threshold step (default: 0.01)")
    parser.add_argument("--margin-min", type=float, default=0.01, help="Minimum margin (default: 0.01)")
    parser.add_argument("--margin-step", type=float, default=0.01, help="Margin step (default: 0.01)")
    parser.add_argument("--max-markets", type=int, default=None, help="Maximum number of markets to test")
    parser.add_argument("--output", type=str, default="threshold_grid_search_results.csv", help="Output CSV file")
    parser.add_argument("--start-date", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None, help="End date (YYYY-MM-DD)")
    
    args = parser.parse_args()
    
    # Parse dates
    start_date = None
    end_date = None
    if args.start_date:
        start_date = datetime.fromisoformat(args.start_date)
    if args.end_date:
        end_date = datetime.fromisoformat(args.end_date)
    
    print("=" * 80)
    print("THRESHOLD STRATEGY GRID SEARCH")
    print("=" * 80)
    print()
    print(f"Threshold range: {args.threshold_min:.2f} to {args.threshold_max:.2f} (step {args.threshold_step:.2f})")
    print(f"Margin range: {args.margin_min:.2f} to auto (step {args.margin_step:.2f})")
    print(f"Max markets: {args.max_markets or 'all'}")
    print(f"Output file: {args.output}")
    print()
    
    # Initialize backtester
    backtester = ThresholdBacktester()
    
    # Get markets
    print("Loading markets...")
    markets = backtester.get_markets_with_orderbooks(
        start_date=start_date,
        end_date=end_date,
        max_markets=args.max_markets
    )
    print(f"Found {len(markets)} markets")
    print()
    
    # Run grid search
    print("Running grid search...")
    results_df = backtester.run_grid_search(
        markets=markets,
        threshold_min=args.threshold_min,
        threshold_max=args.threshold_max,
        threshold_step=args.threshold_step,
        margin_min=args.margin_min,
        margin_step=args.margin_step
    )
    
    if results_df.empty:
        print("No results found!")
        return
    
    # Save results
    results_df.to_csv(args.output, index=False)
    print(f"\nResults saved to {args.output}")
    print()
    
    # Show top results
    print("=" * 80)
    print("TOP 20 RESULTS BY SHARPE RATIO")
    print("=" * 80)
    top_sharpe = results_df.nlargest(20, 'sharpe_ratio')
    print(top_sharpe.to_string(index=False))
    print()
    
    print("=" * 80)
    print("TOP 20 RESULTS BY WIN RATE")
    print("=" * 80)
    top_winrate = results_df.nlargest(20, 'win_rate')
    print(top_winrate.to_string(index=False))
    print()
    
    print("=" * 80)
    print("TOP 20 RESULTS BY AVG ROI")
    print("=" * 80)
    top_roi = results_df.nlargest(20, 'avg_roi')
    print(top_roi.to_string(index=False))
    print()
    
    # Summary statistics
    print("=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)
    print(f"Total parameter combinations tested: {len(results_df)}")
    print(f"Combinations with positive Sharpe ratio: {(results_df['sharpe_ratio'] > 0).sum()}")
    print(f"Combinations with win rate > 50%: {(results_df['win_rate'] > 0.5).sum()}")
    print(f"Combinations with positive avg ROI: {(results_df['avg_roi'] > 0).sum()}")
    print()
    print(f"Best Sharpe ratio: {results_df['sharpe_ratio'].max():.4f}")
    print(f"Best win rate: {results_df['win_rate'].max():.4f}")
    print(f"Best avg ROI: {results_df['avg_roi'].max():.4f}")

if __name__ == "__main__":
    main()

