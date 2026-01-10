"""
Test split strategy backtesting with grid search.

Runs grid search separately for 15-minute and 1-hour markets,
displaying results in threshold vs margin grids and saving heatmap plots.
"""
import sys
import os
import argparse
import json
from datetime import datetime, timezone
from typing import Optional, Union, Tuple
import pandas as pd
import numpy as np

# Try to import plotting libraries, but make them optional
try:
    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend
    import matplotlib.pyplot as plt
    import seaborn as sns
    PLOTTING_AVAILABLE = True
except ImportError:
    PLOTTING_AVAILABLE = False
    print("Warning: matplotlib/seaborn not available. Plots will not be generated.")

# Try to import plotly for interactive plots
try:
    import plotly.graph_objects as go
    import plotly.express as px
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    make_subplots = None
    print("Warning: plotly not available. Interactive plots will not be generated.")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

# Import visualization functions from threshold grid script
# We need to import the module and access the functions
import importlib.util
threshold_grid_path = os.path.join(os.path.dirname(__file__), "test_threshold_grid.py")
spec = importlib.util.spec_from_file_location("test_threshold_grid", threshold_grid_path)
test_threshold_grid = importlib.util.module_from_spec(spec)
spec.loader.exec_module(test_threshold_grid)

# Access the visualization functions
create_heatmap = test_threshold_grid.create_heatmap
create_interactive_heatmap = test_threshold_grid.create_interactive_heatmap
create_kelly_roi_heatmap = test_threshold_grid.create_kelly_roi_heatmap
display_grid = test_threshold_grid.display_grid
display_summary_stats = test_threshold_grid.display_summary_stats

from agents.backtesting.split_strategy_backtester import SplitStrategyBacktester
from dotenv import load_dotenv

load_dotenv()


def run_grid_search_for_market_type(
    market_type: str,
    threshold_min: float = 0.30,
    threshold_max: float = 0.50,
    threshold_step: float = 0.01,
    margin_min: float = 0.01,
    margin_step: float = 0.01,
    min_dollar_amount: float = 1.0,
    max_dollar_amount: float = 1000.0,
    dollar_amount_interval: float = 50.0,
    max_markets: int = None,
    start_date: datetime = None,
    end_date: datetime = None,
    output_dir: str = None
):
    """Run grid search for a specific market type (15m or 1h)."""
    print(f"\n{'='*80}")
    print(f"RUNNING GRID SEARCH FOR {market_type.upper()} MARKETS - SPLIT STRATEGY")
    print(f"{'='*80}")
    
    # Initialize backtester for specific market type
    if market_type == "15m":
        backtester = SplitStrategyBacktester(use_15m_table=True, use_1h_table=False)
    elif market_type == "1h":
        backtester = SplitStrategyBacktester(use_15m_table=False, use_1h_table=True)
    else:
        raise ValueError(f"Invalid market_type: {market_type}. Must be '15m' or '1h'")
    
    # Get markets
    print(f"\nLoading {market_type} markets...")
    markets = backtester.get_markets_with_orderbooks(
        start_date=start_date,
        end_date=end_date,
        max_markets=max_markets
    )
    num_markets = len(markets)
    print(f"Found {num_markets} {market_type} markets")
    
    if num_markets == 0:
        print(f"No {market_type} markets found. Skipping grid search.")
        return pd.DataFrame(), []
    
    # Run grid search
    print(f"\nRunning grid search on {num_markets} {market_type} markets...")
    print(f"Threshold: {threshold_min:.2f} to {threshold_max:.2f} (step {threshold_step:.2f})")
    print(f"Margin: {margin_min:.2f} to auto (step {margin_step:.2f})")
    print(f"Dollar amount: ${min_dollar_amount:.0f} to ${max_dollar_amount:.0f} (step ${dollar_amount_interval:.0f})")
    
    results_df, individual_trades = backtester.run_grid_search(
        threshold_min=threshold_min,
        threshold_max=threshold_max,
        threshold_step=threshold_step,
        margin_min=margin_min,
        margin_step=margin_step,
        min_dollar_amount=min_dollar_amount,
        max_dollar_amount=max_dollar_amount,
        dollar_amount_interval=dollar_amount_interval,
        start_date=start_date,
        end_date=end_date,
        max_markets=max_markets
    )
    
    print(f"\nGrid search completed. Found {len(results_df)} parameter combinations with trades.")
    
    # Display summary statistics
    display_summary_stats(results_df, market_type, num_markets=num_markets)
    
    # Display grids
    display_grid(results_df, "avg_roi", f"{market_type.upper()} Markets - Split Strategy")
    display_grid(results_df, "win_rate", f"{market_type.upper()} Markets - Split Strategy")
    
    # Create and save heatmaps if plotting is available
    plot_files = []
    html_files = []
    
    if PLOTTING_AVAILABLE or PLOTLY_AVAILABLE:
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        
        for metric in ["avg_roi", "win_rate"]:
            result = create_heatmap(
                results_df, 
                metric, 
                f"{market_type}_split_{metric}", 
                output_dir=output_dir
            )
            if result:
                if isinstance(result, tuple):
                    plot_files.append(result[0])
                    html_files.append(result[1])
                else:
                    plot_files.append(result)
        
        # Create interactive heatmaps with dollar amount slider
        # Note: individual_trades is attached to df.attrs by the backtester
        if PLOTLY_AVAILABLE:
            metrics_to_visualize = ["avg_roi", "win_rate"]
            
            # Add Kelly ROI if available in results (now automatically calculated)
            if "kelly_roi" in results_df.columns:
                metrics_to_visualize.append("kelly_roi")
            
            for metric in metrics_to_visualize:
                html_path = create_interactive_heatmap(
                    results_df,
                    metric,
                    f"{market_type}_split_{metric}",
                    output_dir=output_dir
                )
                if html_path:
                    html_files.append(html_path)
    
    return results_df, (plot_files, html_files)


def main():
    parser = argparse.ArgumentParser(description="Test split strategy grid search for 15m and 1h markets")
    parser.add_argument("--threshold-min", type=float, default=0.30, help="Minimum threshold (default: 0.30)")
    parser.add_argument("--threshold-max", type=float, default=0.50, help="Maximum threshold (default: 0.50)")
    parser.add_argument("--threshold-step", type=float, default=0.01, help="Threshold step (default: 0.01)")
    parser.add_argument("--margin-min", type=float, default=0.01, help="Minimum margin (default: 0.01)")
    parser.add_argument("--margin-step", type=float, default=0.01, help="Margin step (default: 0.01)")
    parser.add_argument("--min-dollar-amount", type=float, default=1.0, help="Minimum dollar amount (default: 1)")
    parser.add_argument("--max-dollar-amount", type=float, default=1000.0, help="Maximum dollar amount (default: 1000)")
    parser.add_argument("--dollar-amount-interval", type=float, default=50.0, help="Dollar amount interval (default: 50)")
    parser.add_argument("--max-markets", type=int, default=None, help="Maximum number of markets to test per type")
    parser.add_argument("--start-date", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--15m-only", action="store_true", dest="only_15m", help="Only test 15-minute markets")
    parser.add_argument("--1h-only", action="store_true", dest="only_1h", help="Only test 1-hour markets")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to save CSV results and plots")
    
    args = parser.parse_args()
    
    # Parse dates
    start_date = None
    end_date = None
    if args.start_date:
        start_date = datetime.fromisoformat(args.start_date).replace(tzinfo=timezone.utc)
    if args.end_date:
        end_date = datetime.fromisoformat(args.end_date).replace(tzinfo=timezone.utc)
    
    print("=" * 80)
    print("SPLIT STRATEGY GRID SEARCH - 15M AND 1H MARKETS")
    print("=" * 80)
    print()
    print(f"Threshold range: {args.threshold_min:.2f} to {args.threshold_max:.2f} (step {args.threshold_step:.2f})")
    print(f"Margin range: {args.margin_min:.2f} to auto (step {args.margin_step:.2f})")
    print(f"Dollar amount range: ${args.min_dollar_amount:.0f} to ${args.max_dollar_amount:.0f} (step ${args.dollar_amount_interval:.0f})")
    print(f"Max markets per type: {args.max_markets or 'all'}")
    print()
    
    results_15m = None
    results_1h = None
    plot_files_15m = []
    plot_files_1h = []
    html_files_15m = []
    html_files_1h = []
    
    # Run grid search for 15-minute markets
    if not args.only_1h:
        results_15m, (plot_files_15m, html_files_15m) = run_grid_search_for_market_type(
            "15m",
            threshold_min=args.threshold_min,
            threshold_max=args.threshold_max,
            threshold_step=args.threshold_step,
            margin_min=args.margin_min,
            margin_step=args.margin_step,
            min_dollar_amount=args.min_dollar_amount,
            max_dollar_amount=args.max_dollar_amount,
            dollar_amount_interval=args.dollar_amount_interval,
            max_markets=args.max_markets,
            start_date=start_date,
            end_date=end_date,
            output_dir=args.output_dir
        )
    
    # Run grid search for 1-hour markets
    if not args.only_15m:
        results_1h, (plot_files_1h, html_files_1h) = run_grid_search_for_market_type(
            "1h",
            threshold_min=args.threshold_min,
            threshold_max=args.threshold_max,
            threshold_step=args.threshold_step,
            margin_min=args.margin_min,
            margin_step=args.margin_step,
            min_dollar_amount=args.min_dollar_amount,
            max_dollar_amount=args.max_dollar_amount,
            dollar_amount_interval=args.dollar_amount_interval,
            max_markets=args.max_markets,
            start_date=start_date,
            end_date=end_date,
            output_dir=args.output_dir
        )
    
    # Save results to CSV if output directory specified
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        if results_15m is not None and not results_15m.empty:
            csv_path_15m = os.path.join(args.output_dir, "split_strategy_15m_results.csv")
            results_15m.to_csv(csv_path_15m, index=False)
            print(f"\nSaved 15m results to {csv_path_15m}")
        
        if results_1h is not None and not results_1h.empty:
            csv_path_1h = os.path.join(args.output_dir, "split_strategy_1h_results.csv")
            results_1h.to_csv(csv_path_1h, index=False)
            print(f"Saved 1h results to {csv_path_1h}")
    
    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    if results_15m is not None and not results_15m.empty:
        print(f"\n15-minute markets: {len(results_15m)} parameter combinations tested")
        if len(plot_files_15m) > 0:
            print(f"  PNG plots: {', '.join(plot_files_15m)}")
        if len(html_files_15m) > 0:
            print(f"  HTML plots: {', '.join(html_files_15m)}")
    
    if results_1h is not None and not results_1h.empty:
        print(f"\n1-hour markets: {len(results_1h)} parameter combinations tested")
        if len(plot_files_1h) > 0:
            print(f"  PNG plots: {', '.join(plot_files_1h)}")
        if len(html_files_1h) > 0:
            print(f"  HTML plots: {', '.join(html_files_1h)}")
    print()


if __name__ == "__main__":
    main()

