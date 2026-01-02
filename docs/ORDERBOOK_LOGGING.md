# Orderbook Logging Guide

This guide explains how to log real-time Polymarket orderbook data to a database and query historical data.

## Overview

The orderbook logging system provides two methods for capturing orderbook data:

1. **WebSocket Streaming** (Most Real-Time): Uses Polymarket's Real-Time Data Socket (RTDS) for live orderbook updates
2. **Polling** (Fallback): Polls the REST API at regular intervals

Both methods store data in a SQLite database (or PostgreSQL) for historical analysis.

## Quick Start

### 1. Get Token IDs

First, you need the CLOB token IDs for the markets you want to monitor. You can get these from a market:

```python
from agents.polymarket.polymarket import Polymarket

polymarket = Polymarket()
market = polymarket.get_market("MARKET_ID")
import ast
token_ids = ast.literal_eval(market.clob_token_ids)
```

### 2. Start Logging (WebSocket - Most Real-Time)

```bash
python scripts/python/orderbook_logger.py --mode websocket --tokens TOKEN_ID1 TOKEN_ID2
```

Or monitor all tokens from a market:

```bash
python scripts/python/orderbook_logger.py --mode websocket --market MARKET_ID
```

### 3. Start Logging (Polling - Fallback)

If WebSocket is unavailable or unreliable:

```bash
python scripts/python/orderbook_logger.py --mode poll --tokens TOKEN_ID1 TOKEN_ID2 --interval 2.0
```

The `--interval` parameter controls seconds between polls (default: 1.0).

### 4. Query Historical Data

```bash
# View recent snapshots
python scripts/python/query_orderbook.py --token TOKEN_ID --limit 100

# Get spread history
python scripts/python/query_orderbook.py --token TOKEN_ID --spread-history

# Get statistics
python scripts/python/query_orderbook.py --token TOKEN_ID --stats

# Export to CSV
python scripts/python/query_orderbook.py --token TOKEN_ID --export output.csv
```

## Database Schema

The orderbook snapshots are stored with the following structure:

- `id`: Auto-increment primary key
- `token_id`: CLOB token ID (indexed)
- `market_id`: Polymarket market ID (indexed)
- `timestamp`: When the snapshot was taken (indexed)
- `best_bid_price`, `best_bid_size`: Best bid price and size
- `best_ask_price`, `best_ask_size`: Best ask price and size
- `spread`: Spread (ask - bid)
- `spread_bps`: Spread in basis points
- `bids`: Full bid ladder (JSON array of [price, size] tuples)
- `asks`: Full ask ladder (JSON array of [price, size] tuples)
- `market_question`: Market question text
- `outcome`: Outcome name
- `metadata`: Additional metadata (JSON)

## Programmatic Usage

### WebSocket Streaming

```python
import asyncio
from agents.polymarket.orderbook_stream import OrderbookLogger
from agents.polymarket.orderbook_db import OrderbookDatabase

async def main():
    db = OrderbookDatabase()
    token_ids = ["TOKEN_ID1", "TOKEN_ID2"]
    
    logger = OrderbookLogger(db, token_ids)
    await logger.start()

asyncio.run(main())
```

### Polling

```python
import asyncio
from agents.polymarket.orderbook_poller import OrderbookPoller
from agents.polymarket.orderbook_db import OrderbookDatabase

async def main():
    db = OrderbookDatabase()
    token_ids = ["TOKEN_ID1", "TOKEN_ID2"]
    
    poller = OrderbookPoller(db, token_ids, poll_interval=1.0)
    await poller.poll_loop()

asyncio.run(main())
```

### Querying Historical Data

```python
from agents.polymarket.orderbook_query import OrderbookQuery
from datetime import datetime, timedelta

query = OrderbookQuery()

# Get snapshots
snapshots = query.get_snapshots(
    token_id="TOKEN_ID",
    start_time=datetime.now() - timedelta(hours=24),
    limit=1000
)

# Get as DataFrame
df = query.get_snapshots_dataframe(token_id="TOKEN_ID")

# Get spread history
spread_df = query.get_spread_history(token_id="TOKEN_ID")

# Get statistics
stats = query.get_statistics(token_id="TOKEN_ID")
```

## Real-Time vs Historical Data

### Real-Time Data (WebSocket)

- **Latency**: Sub-second updates
- **Method**: WebSocket connection to `wss://ws-live-data.polymarket.com`
- **Best for**: Live monitoring, real-time analysis
- **Limitations**: Requires stable connection, may have rate limits

### Historical Data

- **Source**: Your database (from logging)
- **Third-party options**:
  - [PredictionData.dev](https://predictiondata.dev): Comprehensive historical data with tick-level updates
  - [DeltaBase](https://www.deltabase.tech): Historical trading data via CSV or BigQuery

## Database Configuration

By default, data is stored in `./orderbook.db` (SQLite). You can customize this:

```python
# Custom SQLite path
db = OrderbookDatabase(database_url="sqlite:///custom/path/orderbook.db")

# PostgreSQL
db = OrderbookDatabase(database_url="postgresql://user:pass@localhost/dbname")
```

Or set the `ORDERBOOK_DB_PATH` environment variable:

```bash
export ORDERBOOK_DB_PATH="./custom/orderbook.db"
```

## Performance Considerations

- **WebSocket**: Most efficient for real-time updates, minimal API calls
- **Polling**: More API calls, but more reliable if WebSocket has issues
- **Database**: SQLite is fine for moderate volumes. For high-frequency logging (many tokens), consider PostgreSQL
- **Storage**: Each snapshot stores full orderbook. Consider archiving old data periodically

## Troubleshooting

### WebSocket Connection Issues

If WebSocket fails to connect:
1. Check your internet connection
2. Try polling mode as fallback
3. Check Polymarket RTDS status

### Database Locked Errors

If using SQLite with multiple processes:
- Use separate database files per process, or
- Switch to PostgreSQL for concurrent access

### Missing Data

- Ensure the logger is running continuously
- Check logs for errors
- Verify token IDs are correct
- For WebSocket: check if subscription messages were acknowledged

## Example: Complete Workflow

```python
# 1. Get token IDs for a market
from agents.polymarket.polymarket import Polymarket
polymarket = Polymarket()
market = polymarket.get_market("MARKET_ID")
import ast
token_ids = ast.literal_eval(market.clob_token_ids)

# 2. Start logging (in a separate process/script)
# python scripts/python/orderbook_logger.py --mode websocket --tokens {token_ids}

# 3. Query data later
from agents.polymarket.orderbook_query import OrderbookQuery
query = OrderbookQuery()
df = query.get_snapshots_dataframe(token_id=token_ids[0])
print(df.head())

# 4. Analyze spread
spread_df = query.get_spread_history(token_id=token_ids[0])
print(spread_df.describe())
```

## API Reference

See the docstrings in:
- `agents/polymarket/orderbook_db.py`: Database models and operations
- `agents/polymarket/orderbook_stream.py`: WebSocket streaming
- `agents/polymarket/orderbook_poller.py`: Polling-based logging
- `agents/polymarket/orderbook_query.py`: Historical data queries

