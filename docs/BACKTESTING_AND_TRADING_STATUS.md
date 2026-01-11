# Backtesting and Threshold Trading Strategy - Status Documentation

## Overview

This document describes the current status and capabilities of the backtesting framework and live threshold trading strategy implementation.

---

## Backtesting Framework

### Status: ✅ **Fully Functional**

The backtesting framework provides comprehensive tools for testing trading strategies on historical Polymarket data.

### Available Backtesters

#### 1. **Threshold Strategy Backtester** (`agents/backtesting/threshold_backtester.py`)
- **Status**: ✅ Fully operational
- **Purpose**: Tests the threshold-based buy strategy on historical markets
- **Strategy Logic**:
  - Monitors YES and NO highest bid prices from orderbook snapshots
  - When one side reaches a threshold (e.g., 40%), places a limit buy order at `threshold + margin`
  - Simulates order fills based on orderbook depth
  - Calculates ROI based on market outcome prices
- **Features**:
  - Grid search over threshold (60-100%) and margin (1-99%)
  - Supports both individual market testing and batch processing
  - Uses historical orderbook snapshots from database
  - Accounts for Polymarket fees in ROI calculations
- **Usage**:
  ```python
  from agents.backtesting.threshold_backtester import ThresholdBacktester
  
  backtester = ThresholdBacktester()
  result = backtester.process_market_with_snapshots(
      market_data=market_dict,
      threshold=0.40,
      margin=0.02,
      dollar_amount=100.0
  )
  ```

#### 2. **Split Strategy Backtester** (`agents/backtesting/split_strategy_backtester.py`)
- **Status**: ✅ Fully operational
- **Purpose**: Tests a split strategy where you buy both YES and NO, then sell one side when threshold is reached
- **Strategy Logic**:
  1. Split $X USDC → X YES + X NO shares (cost = $X)
  2. Monitor both sides - when highest bid < threshold for one side, sell that side
  3. Sell at threshold - margin (walk down bid book if needed)
  4. Hold the other side until market resolution
  5. ROI = (cash from sale + final value of held side - split cost) / split cost
- **Features**:
  - Grid search over threshold, margin, and dollar amount
  - Handles orderbook walking for partial fills
  - Accounts for fees on both buy and sell sides

#### 3. **Orderbook Backtester** (`agents/backtesting/orderbook_backtester.py`)
- **Status**: ✅ Fully operational
- **Purpose**: Generic backtesting framework for custom strategies
- **Features**:
  - Works with any strategy function
  - Processes orderbook snapshots sequentially
  - Calculates P&L, return percentage, and win rate

#### 4. **BTC Prediction Backtester** (`agents/backtesting/btc_backtester.py`)
- **Status**: ✅ Fully operational
- **Purpose**: End-to-end backtesting of BTC price prediction models
- **Models Supported**:
  - `chronos-bolt`: Amazon's Chronos T5-based forecasting model ✅ **Working**
  - `lag-llama`: Probabilistic forecasting model ⚠️ Partial support
  - `baseline`: Simple momentum-based predictor (fallback)
- **Features**:
  - Processes historical BTC 15-minute markets sequentially
  - Uses cached BTC OHLCV data from Binance
  - Calculates prediction accuracy and trading performance
  - Supports proxy configuration for API access

### Backtesting Data Sources

- **Orderbook Snapshots**: Stored in SQLite database (`orderbook.db`)
  - Full orderbook ladders (bid/ask depth)
  - Precise timestamps
  - Best bid/ask prices pre-calculated
  - Spread metrics
- **Historical Markets**: Fetched from Polymarket API via `HistoricalMarketFetcher`
  - Closed/resolved markets
  - Outcome prices
  - Market metadata

### Backtesting Utilities (`agents/backtesting/backtesting_utils.py`)

- **Market Parsing**: Parse market dates, enrich market data from API
- **Outcome Parsing**: Extract outcome prices for YES/NO sides
- **Orderbook Helpers**: Get highest bid, lowest ask, walk orderbook
- **Fee Calculation**: Calculate Polymarket fees based on price and trade value
  - Formula: `fee = trade_value × 0.25 × (price × (1-price))²`
  - Maximum fees at p=0.50, decreasing toward extremes

### Running Backtests

#### Threshold Strategy Grid Search
```bash
python scripts/python/run_threshold_grid_search.py
```

#### Orderbook Backtest
```bash
python scripts/python/run_orderbook_backtest.py
```

#### BTC Prediction Backtest
```python
from agents.backtesting.btc_backtester import BTCBacktester

backtester = BTCBacktester(model_name='chronos-bolt', lookback_minutes=200)
results = backtester.run_backtest(start_date=..., end_date=...)
```

---

## Threshold Trading Strategy (Live Execution)

### Status: ✅ **Production Ready**

The live threshold trading strategy (`scripts/python/trade_threshold_strategy.py`) is a fully functional automated trading system.

### Strategy Overview

**Buy Logic**:
1. Monitors BTC 15-minute or 1-hour markets
2. Tracks YES and NO highest bid prices from real-time orderbooks
3. When one side reaches the configured threshold (e.g., 40%), places a limit buy order
4. Order placed at `threshold + margin` (e.g., 42% if threshold=40%, margin=2%)
5. Uses Kelly Criterion for position sizing, capped by `dollar_bet_limit`

**Sell Logic**:
1. **Initial Sell Order**: Immediately after buy order fills, places a limit sell order at $0.99
2. **Early Sell (Stop-Loss)**: If price drops below `threshold_sell` (e.g., 30%), cancels $0.99 order and places new sell at `threshold_sell - margin_sell`
3. **Market Resolution**: If neither sell order triggers, calculates ROI based on outcome:
   - If won: Assumes claim at $1/share (accounts for sell fees)
   - If lost: Shares worthless, ROI = -100%

### Key Features

#### ✅ **Order Management**
- Limit orders (GTC - Good-Til-Cancelled) for both buy and sell
- Automatic retry logic for failed orders
- Order status monitoring and fill detection
- Handles partial fills

#### ✅ **Fee Accounting**
- **Buy Orders**: Adjusts order size to account for fees (ensures desired shares after fees)
- **Sell Orders**: Accounts for fees in ROI calculations
- **ROI Formula**: `ROI = net_payout / (dollars_spent + buy_fee)`
  - `net_payout = sell_proceeds - sell_fee - dollars_spent - buy_fee`

#### ✅ **Balance Management**
- Checks conditional token balance before placing sell orders
- Automatically adjusts sell size to match available balance (fees may reduce shares)
- Rounds down to whole shares (Polymarket requirement)
- Handles insufficient balance errors gracefully

#### ✅ **Principal Tracking**
- Tracks principal across deployments
- Updates principal when:
  - Sell orders fill (early sell or $0.99 limit)
  - Market resolves (if lost or if won but sell didn't fill)
- Principal persists in database

#### ✅ **Risk Management**
- Kelly Criterion for position sizing
- `dollar_bet_limit` caps maximum bet size
- Minimum order value enforcement ($1.00 Polymarket requirement)
- Prevents duplicate bets on same market (checks database)

#### ✅ **Error Handling**
- Comprehensive error logging
- Retry mechanisms for API failures
- Graceful handling of balance/allowance errors
- Database logging of all errors

#### ✅ **Database Integration**
- Stores all trades in PostgreSQL/Neon database
- Tracks order status, fills, fees, ROI
- Deployment ID tracking (supports multiple deployments)
- Queryable trade history

### Configuration (`config/trading_config.json`)

```json
{
  "threshold": 0.40,           // Buy trigger threshold (0-1)
  "upper_threshold": 0.97,     // Upper bound (don't buy above this)
  "margin": 0.02,              // Buy order margin above threshold
  "threshold_sell": 0.3,       // Early sell trigger (stop-loss)
  "margin_sell": 0.02,         // Early sell margin below threshold
  "kelly_fraction": 0.25,      // Kelly Criterion fraction
  "kelly_scale_factor": 0.9,   // Scale down Kelly (conservative)
  "market_type": "15m",        // "15m" or "1h" markets
  "initial_principal": 100.0,  // Starting capital
  "dollar_bet_limit": 100.0    // Maximum bet size
}
```

### Running Live Trading

```bash
python scripts/python/trade_threshold_strategy.py --config config/trading_config.json
```

### Required Environment Variables

- `PK`: Private key for wallet (hex format, no 0x prefix)
- `CLOB_API_KEY`: Polymarket CLOB API key
- `CLOB_SECRET`: Polymarket CLOB API secret
- `CLOB_PASS_PHRASE`: Polymarket CLOB API passphrase
- `DATABASE_URL`: PostgreSQL connection string (for Neon or other)
- `CLOB_API_URL`: Polymarket CLOB API URL (default: https://clob.polymarket.com)

### Current Implementation Details

#### Order Placement Flow
1. **Buy Order**:
   - Calculates Kelly-based position size
   - Adjusts for fees (orders slightly more to get desired shares)
   - Places limit order at `threshold + margin`
   - Waits for fill confirmation

2. **Sell Order Placement**:
   - After buy fills, waits 5 seconds for shares to settle
   - Checks conditional token balance
   - Verifies exchange contract allowances
   - Places limit sell at $0.99
   - Retries up to 5 times with increasing delays if balance/allowance errors occur

3. **Early Sell**:
   - Monitors price every orderbook update
   - If price drops below `threshold_sell`, cancels $0.99 order
   - Places new sell order at `threshold_sell - margin_sell`

4. **Market Resolution**:
   - Checks market status every 30 seconds
   - When resolved, calculates final ROI and updates principal
   - Handles cases where sell orders didn't fill

#### Database Schema

Trades are stored in `RealTradeThreshold` table with fields:
- Order details (order_id, order_price, order_size, order_status)
- Fill information (filled_shares, fill_price, dollars_spent, fee)
- Sell order details (sell_order_id, sell_order_price, sell_order_size, sell_order_status, sell_dollars_received, sell_fee)
- Outcome information (outcome_price, payout, net_payout, roi, is_win, winning_side)
- Principal tracking (principal_before, principal_after)
- Timestamps (order_placed_at, order_filled_at, market_resolved_at)

### Known Limitations

1. **Fractional Shares**: Polymarket requires whole shares, so orders are rounded down
2. **Fee Estimation**: Fee adjustment uses estimated fee formula; actual fees may vary slightly
3. **Orderbook Latency**: Real-time orderbook updates depend on WebSocket/API polling frequency
4. **Balance Settlement**: Shares may take a few seconds to appear in wallet after buy fills

### Monitoring and Logging

- Comprehensive logging at INFO level
- Error details logged with full stack traces
- Database tracks all order attempts and outcomes
- Principal updates logged for audit trail

---

## Comparison: Backtesting vs Live Trading

| Feature | Backtesting | Live Trading |
|---------|-------------|--------------|
| **Data Source** | Historical orderbook snapshots | Real-time orderbook API |
| **Order Execution** | Simulated (orderbook walking) | Actual API calls to Polymarket |
| **Fees** | Calculated using formula | Actual fees from exchange |
| **Balance** | Assumed infinite | Real wallet balance checked |
| **Principal** | Tracked in memory | Persisted in database |
| **Error Handling** | Minimal (assumes perfect execution) | Comprehensive (retries, fallbacks) |
| **Deployment** | One-time script execution | Long-running service |

---

## Future Enhancements

### Backtesting
- [ ] Add more strategy variants
- [ ] Support for multi-market portfolio backtesting
- [ ] Visualization tools for backtest results
- [ ] Statistical analysis and confidence intervals

### Live Trading
- [ ] Support for multiple strategies simultaneously
- [ ] Dynamic threshold adjustment based on market conditions
- [ ] Portfolio-level risk management
- [ ] Telegram/Discord notifications
- [ ] Web dashboard for monitoring

---

## Related Documentation

- [Orderbook Backtesting Guide](ORDERBOOK_BACKTESTING.md)
- [BTC Prediction Status](BTC_PREDICTION_STATUS.md)
- [Deployment Guide](DEPLOYMENT.md)
- [Troubleshooting](TROUBLESHOOTING.md)
- [Fee Rate Explanation](FEE_RATE_EXPLANATION.md)

---

## Support

For issues or questions:
1. Check [Troubleshooting Guide](TROUBLESHOOTING.md)
2. Review logs for error details
3. Check database for trade history
4. Open an issue on GitHub

---

**Last Updated**: January 2025
