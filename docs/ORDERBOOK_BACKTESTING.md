# Orderbook Data Structure for Backtesting

This document shows what the orderbook data looks like when logged to the database, and how to use it for backtesting trading strategies.

## Database Schema

Each orderbook snapshot stored in the database contains:

```python
OrderbookSnapshot(
    id=1,                                    # Auto-increment ID
    token_id="11015470973684177829729219287262166995141465048508201953575582100565462316088",
    market_id="12345",                       # Polymarket market ID (optional)
    timestamp=datetime(2024, 1, 15, 14, 30, 25),  # UTC timestamp
    
    # Best bid/ask (quick access)
    best_bid_price=0.45,
    best_bid_size=1000.0,
    best_ask_price=0.46,
    best_ask_size=500.0,
    
    # Spread metrics
    spread=0.01,                             # ask - bid
    spread_bps=217.39,                       # spread in basis points
    
    # Full orderbook ladders (stored as JSON)
    bids=[
        [0.45, 1000.0],   # [price, size]
        [0.44, 2000.0],
        [0.43, 1500.0],
        # ... more levels
    ],
    asks=[
        [0.46, 500.0],    # [price, size]
        [0.47, 800.0],
        [0.48, 1200.0],
        # ... more levels
    ],
    
    # Metadata
    market_question="Will X happen?",
    outcome="Yes",
    metadata={
        "source": "rtds",  # or "polling"
        "raw_data": {...}  # Original WebSocket/polling data
    }
)
```

## Example: What Gets Logged

### From WebSocket (RTDS)

When a WebSocket update arrives, it gets parsed and stored. The `bids` and `asks` arrays contain the **full orderbook ladder**, sorted by price:

**Bids** (buy orders, sorted descending by price - best bid first):
```json
[
    [0.45, 1000.0],  // Best bid: $0.45 for 1000 shares
    [0.44, 2000.0],  // Next level: $0.44 for 2000 shares
    [0.43, 1500.0],
    [0.42, 3000.0],
    // ... more levels
]
```

**Asks** (sell orders, sorted ascending by price - best ask first):
```json
[
    [0.46, 500.0],   // Best ask: $0.46 for 500 shares
    [0.47, 800.0],   // Next level: $0.47 for 800 shares
    [0.48, 1200.0],
    [0.49, 2000.0],
    // ... more levels
]
```

## Using for Backtesting

### 1. Load Historical Orderbook Data

```python
from agents.polymarket.orderbook_query import OrderbookQuery
from datetime import datetime, timedelta

query = OrderbookQuery()

# Get snapshots for a time period
snapshots = query.get_snapshots(
    token_id="YOUR_TOKEN_ID",
    start_time=datetime(2024, 1, 15),
    end_time=datetime(2024, 1, 16),
    limit=10000
)

# Or as DataFrame
df = query.get_snapshots_dataframe(
    token_id="YOUR_TOKEN_ID",
    start_time=datetime(2024, 1, 15),
    end_time=datetime(2024, 1, 16),
)
```

### 2. Reconstruct Orderbook State at Any Time

```python
# Get orderbook at a specific time
snapshot = query.get_orderbook_at_time(
    token_id="YOUR_TOKEN_ID",
    target_time=datetime(2024, 1, 15, 14, 30, 0),
    tolerance_seconds=60
)

if snapshot:
    # Access full orderbook
    bids = snapshot.bids  # [[price, size], ...]
    asks = snapshot.asks  # [[price, size], ...]
    
    # Or use pre-calculated best bid/ask
    best_bid = snapshot.best_bid_price
    best_ask = snapshot.best_ask_price
    mid_price = (best_bid + best_ask) / 2
```

### 3. Simulate Order Execution

For backtesting, you can simulate order execution against the historical orderbook:

```python
def simulate_market_buy(orderbook_snapshot, size):
    """
    Simulate a market buy order against the orderbook.
    Returns: (total_cost, avg_price, remaining_size)
    """
    asks = orderbook_snapshot.asks
    total_cost = 0.0
    remaining_size = size
    
    for price, available_size in asks:
        if remaining_size <= 0:
            break
        
        executed_size = min(remaining_size, available_size)
        total_cost += executed_size * price
        remaining_size -= executed_size
    
    if remaining_size > 0:
        # Not enough liquidity - partial fill
        pass
    
    avg_price = total_cost / (size - remaining_size) if (size - remaining_size) > 0 else None
    return total_cost, avg_price, remaining_size


def simulate_limit_buy(orderbook_snapshot, limit_price, size):
    """
    Simulate a limit buy order.
    Returns: (executed_size, avg_price)
    """
    asks = orderbook_snapshot.asks
    executed_size = 0.0
    total_cost = 0.0
    
    for price, available_size in asks:
        if price > limit_price:  # Can't buy above limit
            break
        
        executed = min(size - executed_size, available_size)
        executed_size += executed
        total_cost += executed * price
        
        if executed_size >= size:
            break
    
    avg_price = total_cost / executed_size if executed_size > 0 else None
    return executed_size, avg_price
```

### 4. Calculate Depth Metrics

```python
def calculate_depth(orderbook_snapshot, depth_bps=100):
    """
    Calculate orderbook depth within N basis points of mid price.
    """
    best_bid = orderbook_snapshot.best_bid_price
    best_ask = orderbook_snapshot.best_ask_price
    mid_price = (best_bid + best_ask) / 2
    
    depth_range = mid_price * (depth_bps / 10000)
    
    bid_depth = sum(size for price, size in orderbook_snapshot.bids 
                    if price >= (mid_price - depth_range))
    ask_depth = sum(size for price, size in orderbook_snapshot.asks 
                    if price <= (mid_price + depth_range))
    
    return {
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "total_depth": bid_depth + ask_depth,
        "imbalance": (bid_depth - ask_depth) / (bid_depth + ask_depth) if (bid_depth + ask_depth) > 0 else 0
    }
```

### 5. Example Backtest Loop

```python
import pandas as pd

def backtest_strategy(token_id, start_time, end_time, strategy_func):
    """
    Simple backtest framework.
    
    strategy_func: Function that takes (snapshot, portfolio_state) 
                   and returns trade decision
    """
    query = OrderbookQuery()
    snapshots = query.get_snapshots(
        token_id=token_id,
        start_time=start_time,
        end_time=end_time,
        limit=100000
    )
    
    # Sort by timestamp (oldest first)
    snapshots = sorted(snapshots, key=lambda s: s.timestamp)
    
    portfolio = {
        "cash": 10000.0,
        "shares": 0.0,
        "trades": []
    }
    
    for snapshot in snapshots:
        # Get current portfolio value
        if snapshot.best_bid_price:
            portfolio_value = portfolio["cash"] + portfolio["shares"] * snapshot.best_bid_price
        else:
            portfolio_value = portfolio["cash"]
        
        # Strategy decides what to do
        decision = strategy_func(snapshot, portfolio)
        
        if decision["action"] == "buy" and decision["size"] > 0:
            cost, avg_price, remaining = simulate_market_buy(snapshot, decision["size"])
            if remaining == 0:  # Full fill
                portfolio["cash"] -= cost
                portfolio["shares"] += decision["size"]
                portfolio["trades"].append({
                    "timestamp": snapshot.timestamp,
                    "action": "buy",
                    "size": decision["size"],
                    "price": avg_price,
                    "cost": cost
                })
        
        elif decision["action"] == "sell" and portfolio["shares"] > 0:
            # Similar for sell...
            pass
    
    return portfolio
```

## Data Completeness for Backtesting

### What You Get:
✅ **Full orderbook ladders** - Complete bid/ask depth at each snapshot  
✅ **Precise timestamps** - Know exactly when each snapshot was taken  
✅ **Best bid/ask** - Quick access without parsing JSON  
✅ **Spread metrics** - Pre-calculated for analysis  
✅ **Market metadata** - Question, outcome, etc.

### What You DON'T Get (would need separate data):
❌ **Trade history** - Actual executed trades (separate from orderbook)  
❌ **Order updates** - Individual order adds/cancels (only snapshots)  
❌ **Fill events** - When orders actually executed

### For Complete Backtesting:

You might want to combine with:
1. **Trade data** - From Polymarket's trade history API
2. **Your own orders** - If you're tracking your own order placement/fills
3. **Market events** - News, resolution updates, etc.

## Querying Efficiently

For large backtests, use time-based queries:

```python
# Get data in chunks
chunk_size = timedelta(hours=1)
current_time = start_time

while current_time < end_time:
    chunk_end = min(current_time + chunk_size, end_time)
    snapshots = query.get_snapshots(
        token_id=token_id,
        start_time=current_time,
        end_time=chunk_end,
        limit=10000
    )
    # Process chunk
    process_snapshots(snapshots)
    current_time = chunk_end
```

## Example: Full Backtest Script

See `scripts/python/backtest_example.py` (if created) for a complete example.

