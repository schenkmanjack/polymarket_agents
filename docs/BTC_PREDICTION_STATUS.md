# BTC Price Prediction & Backtesting - Status

## Overview

This document tracks the status of integrating AI models (Lag-Llama/Chronos-Bolt) to predict Bitcoin prices 15 minutes ahead for Polymarket BTC 15-minute markets.

## Current Status: 🟡 In Progress

### ✅ Completed

1. **BTC Data Fetcher** (`agents/connectors/btc_data.py`)
   - ✓ Fetches historical BTC OHLCV data from Binance API (public endpoint, no API key required)
   - ✓ Supports multiple intervals: 1m, 5m, 15m, 1h, 4h, 1d
   - ✓ Automatic caching to Parquet files (`./data/btc_cache/`)
   - ✓ Rate limiting (1.2s between requests, well under Binance's 1200 req/min limit)
   - ✓ Methods:
     - `get_prices()` - Get OHLCV data for time range
     - `get_price_sequence()` - Get price sequence for model input (200 data points)
     - `get_price_at_time()` - Get price at specific timestamp
   - ✓ Tested and working

2. **Test Scripts**
   - ✓ `scripts/python/test_btc_fetcher.py` - Tests BTC data fetching
   - ✓ `scripts/python/test_model_integration.py` - Tests model integration

3. **Simple Baseline Predictions**
   - ✓ Last price baseline
   - ✓ Moving average predictions
   - ✓ Linear trend projections

### 🟡 In Progress

1. **Model Integration**
   - ⚠️ Lag-Llama: Cannot test (PyTorch not installed)
   - ⚠️ Chronos-Bolt: Cannot test (PyTorch not installed)
   - ⚠️ Need to install dependencies and retest

2. **Historical Market Fetcher**
   - ⚠️ Not yet implemented
   - Need to fetch closed/resolved BTC 15-minute markets from Polymarket

3. **Backtesting Framework**
   - ⚠️ Not yet implemented
   - Need to process markets sequentially, run predictions, calculate metrics

### ❌ Blocked / Missing

1. **Dependencies**
   - ❌ PyTorch - Required for Lag-Llama/Chronos models
   - ❌ PyArrow - Required for Parquet caching (already in requirements.txt but not installed)
   - ❌ Transformers (HuggingFace) - Required for model loading

2. **Model Integration**
   - ❌ Full Lag-Llama integration (probabilistic forecasting)
   - ❌ Full Chronos-Bolt integration (time series forecasting)
   - ❌ Proper data formatting for models
   - ❌ Inference pipeline setup

## Architecture

### Data Flow

```
Polymarket Market (15-min window)
    ↓
Extract market start timestamp
    ↓
BTC Data Fetcher
    ↓
Get 200 minutes of BTC price history (1-minute intervals)
    ↓
Format for AI Model
    ↓
Model Prediction (15 minutes ahead)
    ↓
Compare to Actual Outcome
    ↓
Calculate Performance Metrics
```

### Sequential Processing Strategy

Markets are processed chronologically to maximize cache efficiency:

- **Market 1** (2:00 PM): Fetch 200 minutes of BTC data (12:00 PM - 2:00 PM)
- **Market 2** (2:15 PM): Reuse 185 minutes from cache (12:15 PM - 2:00 PM), fetch only 15 new minutes
- **Market 3** (2:30 PM): Reuse 185 minutes from cache, fetch only 15 new minutes
- **Cache hit rate**: ~92.5% (185/200 minutes reused)

## Usage

### BTC Data Fetcher

```python
from agents.connectors.btc_data import BTCDataFetcher
from datetime import datetime, timedelta, timezone

# Initialize fetcher
fetcher = BTCDataFetcher()

# Get price sequence for model input (recommended: 200 minutes, 1-minute intervals)
timestamp = datetime.now(timezone.utc)
sequence = fetcher.get_price_sequence(
    timestamp=timestamp,
    lookback_minutes=200,  # 200 data points
    interval='1m'          # 1-minute intervals
)
# Returns: [price_t-200, price_t-199, ..., price_t-1, price_t]

# Get OHLCV data for a time range
start = datetime(2024, 1, 1, tzinfo=timezone.utc)
end = datetime(2024, 1, 2, tzinfo=timezone.utc)
df = fetcher.get_prices(start, end, interval='1m')
# Returns DataFrame: timestamp, open, high, low, close, volume
```

### Testing

```bash
# Test BTC data fetcher
python scripts/python/test_btc_fetcher.py

# Test model integration (requires PyTorch)
python scripts/python/test_model_integration.py
```

## Dependencies

### Required (for basic functionality)
- `httpx` - API requests
- `pandas` - Data manipulation
- `numpy` - Numerical operations

### Required (for caching)
- `pyarrow` - Parquet file support
  ```bash
  pip install pyarrow
  ```

### Required (for AI models)
- `torch` - PyTorch (for model inference)
  ```bash
  pip install torch
  ```
- `transformers` - HuggingFace transformers (for model loading)
  ```bash
  pip install transformers
  ```

## Model Details

### Lag-Llama (Probabilistic Forecasting)
- **Model**: `time-series-foundation-models/Lag-Llama`
- **Output**: Distribution (Student's t-distribution) - perfect for uncertainty quantification
- **Use case**: When you need confidence intervals (e.g., "90% CI suggests price > threshold")
- **Status**: ⚠️ Not tested (PyTorch missing)

### Chronos-Bolt (High-Frequency Forecasting)
- **Model**: `amazon/chronos-t5-tiny` (or other Chronos variants)
- **Output**: Point prediction (fast inference)
- **Use case**: When you need sub-second predictions or many predictions
- **Status**: ⚠️ Not tested (PyTorch missing)

### Recommended Settings
- **Interval**: `1m` (1-minute intervals)
- **Lookback**: 200 minutes (~3.3 hours of history)
- **Prediction horizon**: 15 minutes ahead

## Performance Metrics (Planned)

Once backtesting is implemented, we'll track:

1. **Primary Metrics**
   - Win rate (% correct predictions)
   - Expected Value (EV)
   - Sharpe ratio
   - Profit factor

2. **Secondary Metrics**
   - Calibration (Brier score)
   - Information coefficient
   - Maximum drawdown
   - Win/loss ratio

3. **Model-Specific Metrics**
   - Lag-Llama: Confidence interval accuracy
   - Chronos-Bolt: Prediction latency vs accuracy tradeoff

## Next Steps

1. **Install Dependencies**
   ```bash
   pip install pyarrow torch transformers
   ```

2. **Test Model Integration**
   - Run `test_model_integration.py` again
   - Verify Lag-Llama and Chronos can load
   - Test with actual BTC price sequences

3. **Build Historical Market Fetcher**
   - Query Polymarket API for closed BTC 15-minute markets
   - Extract: market start time, starting prices, final outcomes
   - Match with BTC price data

4. **Build Backtesting Framework**
   - Process markets sequentially
   - For each market:
     - Get BTC data up to market start
     - Run model prediction
     - Compare to actual outcome
     - Calculate P&L
   - Aggregate metrics across all markets

5. **Model Integration**
   - Implement proper data formatting for models
   - Set up inference pipelines
   - Handle model outputs (point predictions vs distributions)

## File Structure

```
agents/
  connectors/
    btc_data.py          # BTC data fetcher (✓ Working)
  backtesting/           # (To be created)
    btc_backtester.py    # Backtesting framework
    market_fetcher.py    # Historical market fetcher

scripts/python/
  test_btc_fetcher.py           # Test BTC fetcher (✓ Working)
  test_model_integration.py     # Test model integration (⚠️ Needs PyTorch)

data/
  btc_cache/            # Cached BTC data (Parquet files)
```

## Notes

- **No API Key Required**: Binance public endpoint works without authentication
- **Rate Limits**: Binance allows 1200 requests/minute (IP-based) - code includes rate limiting
- **Caching**: Data cached locally in Parquet format for fast access
- **Sequential Processing**: Recommended for cache efficiency (92.5% hit rate)

## Issues / Known Problems

1. **PyTorch Installation Issue**
   - Error: "Failed to load PyTorch C extensions"
   - May need to reinstall PyTorch or use different installation method
   - See: https://pytorch.org/get-started/locally/

2. **PyArrow Missing**
   - Caching works but can't save to Parquet
   - Install: `pip install pyarrow`

## References

- [Binance API Documentation](https://binance-docs.github.io/apidocs/spot/en/#kline-candlestick-data)
- [Lag-Llama Paper](https://arxiv.org/abs/2310.08278)
- [Chronos Paper](https://arxiv.org/abs/2403.07815)
- [Polymarket API](https://docs.polymarket.com/)

## Last Updated

2026-01-05

