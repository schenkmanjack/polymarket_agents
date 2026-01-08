#!/usr/bin/env python3
"""
Run orderbook-based backtest for BTC 15-minute markets.

For each market with recorded orderbook data:
1. One minute before market start, predict BTC price
2. Use prediction to decide which side to bet on
3. Buy N shares at orderbook price
4. Calculate P&L, win rate, average return, Sharpe ratio

Usage:
    python scripts/python/run_orderbook_backtest.py --model chronos-bolt --shares 1.0
    python scripts/python/run_orderbook_backtest.py --model baseline --shares 1.0 --max-markets 10
"""
import argparse
import sys
import os
from datetime import datetime, timezone
import logging

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.backtesting.orderbook_backtester import OrderbookBacktester, run_orderbook_backtest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Run orderbook-based backtest for BTC 15-minute markets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run backtest with Chronos-Bolt model, 1 share per trade:
  python scripts/python/run_orderbook_backtest.py --model chronos-bolt --shares 1.0
  
  # Run with baseline model, limit to 10 markets:
  python scripts/python/run_orderbook_backtest.py --model baseline --shares 1.0 --max-markets 10
  
  # Run with custom date range:
  python scripts/python/run_orderbook_backtest.py --model chronos-bolt --shares 1.0 --start-date "2026-01-01" --end-date "2026-01-05"
        """
    )
    
    parser.add_argument(
        "--model",
        default="baseline",
        choices=["baseline", "chronos-bolt", "lag-llama"],
        help="Model to use for predictions (default: baseline)"
    )
    
    parser.add_argument(
        "--shares",
        type=float,
        default=1.0,
        help="Number of shares to buy per trade (default: 1.0)"
    )
    
    parser.add_argument(
        "--start-date",
        help="Start date for markets (YYYY-MM-DD format, UTC)"
    )
    
    parser.add_argument(
        "--end-date",
        help="End date for markets (YYYY-MM-DD format, UTC)"
    )
    
    parser.add_argument(
        "--max-markets",
        type=int,
        help="Maximum number of markets to process"
    )
    
    parser.add_argument(
        "--output",
        help="Path to save results CSV file"
    )
    
    args = parser.parse_args()
    
    # Parse dates
    start_date = None
    end_date = None
    
    if args.start_date:
        try:
            start_date = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            logger.error(f"Invalid start date format: {args.start_date}. Use YYYY-MM-DD")
            return 1
    
    if args.end_date:
        try:
            end_date = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            logger.error(f"Invalid end date format: {args.end_date}. Use YYYY-MM-DD")
            return 1
    
    # Run backtest
    logger.info("Starting orderbook-based backtest...")
    logger.info(f"Model: {args.model}")
    logger.info(f"Shares per trade: {args.shares}")
    
    try:
        results_df = run_orderbook_backtest(
            model_name=args.model,
            shares_per_trade=args.shares,
            start_date=start_date,
            end_date=end_date,
            max_markets=args.max_markets,
            output_file=args.output
        )
        
        if results_df.empty:
            logger.warning("No results generated. Check if you have orderbook data in your database.")
            return 1
        
        logger.info(f"\n✓ Backtest completed successfully!")
        logger.info(f"Total trades: {len(results_df)}")
        
        if args.output:
            logger.info(f"Results saved to: {args.output}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Error running backtest: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

