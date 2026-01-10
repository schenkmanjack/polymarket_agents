# Backtesting Performance Optimization Guide

## Current Bottlenecks

1. **Nested Loops**: 4-level nesting (threshold → margin → dollar_amount → markets)
2. **Sequential Processing**: Markets processed one at a time
3. **Repeated Orderbook Walking**: Same snapshots processed repeatedly
4. **Database Queries**: Individual queries per market
5. **No Early Termination**: Processes all snapshots even when market won't trigger

## Optimization Strategies

### 1. Parallelize Market Processing (Biggest Impact)

**Current**: Sequential processing
```python
for market in preprocessed_markets:
    trade_result = self.process_market_with_snapshots(...)
```

**Optimized**: Use multiprocessing
```python
from multiprocessing import Pool, cpu_count
from functools import partial

def process_market_wrapper(args):
    market, threshold, margin, dollar_amount = args
    return self.process_market_with_snapshots(market, threshold, margin, dollar_amount)

# Process markets in parallel
with Pool(processes=cpu_count()) as pool:
    args_list = [(m, threshold, margin, dollar_amount) 
                 for m in preprocessed_markets]
    results = pool.map(process_market_wrapper, args_list)
```

**Expected Speedup**: 4-8x (depending on CPU cores)

### 2. Pre-compute Orderbook Metrics

**Current**: Extract highest_bid/lowest_ask repeatedly for each snapshot
```python
highest_bid = get_highest_bid_from_orderbook(snapshot)
lowest_ask = get_lowest_ask_from_orderbook(snapshot)
```

**Optimized**: Pre-compute once during preprocessing
```python
def _preprocess_market_snapshots(self, market: Dict):
    # ... existing code ...
    
    # Pre-compute orderbook metrics
    for snapshot in snapshots:
        snapshot._highest_bid = get_highest_bid_from_orderbook(snapshot)
        snapshot._lowest_ask = get_lowest_ask_from_orderbook(snapshot)
    
    return {...}
```

**Expected Speedup**: 2-3x (reduces redundant JSON parsing)

### 3. Early Termination for Markets

**Current**: Processes all snapshots even if threshold never reached

**Optimized**: Check if market can trigger before processing
```python
def _can_market_trigger(self, market, threshold):
    """Quick check: does any snapshot have highest_bid < threshold?"""
    yes_snapshots, no_snapshots = group_snapshots_by_outcome(market['snapshots'])
    
    # Check YES side
    for snapshot in yes_snapshots:
        if snapshot._highest_bid and snapshot._highest_bid < threshold:
            return True
    
    # Check NO side
    for snapshot in no_snapshots:
        if snapshot._lowest_ask and snapshot._lowest_ask < threshold:
            return True
    
    return False

# In grid search:
if not self._can_market_trigger(market, threshold):
    continue  # Skip this market for this threshold
```

**Expected Speedup**: 1.5-2x (skips ~30-50% of markets that never trigger)

### 4. Batch Database Queries

**Current**: Individual query per market
```python
for market in markets:
    snapshots = session.query(...).filter(market_id == ...).all()
```

**Optimized**: Load all snapshots at once
```python
# Load all snapshots in one query
all_snapshots = session.query(snapshot_class).filter(
    snapshot_class.market_id.in_(all_market_ids)
).order_by(snapshot_class.market_id, snapshot_class.timestamp).all()

# Group by market_id
snapshots_by_market = {}
for snapshot in all_snapshots:
    if snapshot.market_id not in snapshots_by_market:
        snapshots_by_market[snapshot.market_id] = []
    snapshots_by_market[snapshot.market_id].append(snapshot)
```

**Expected Speedup**: 2-5x (reduces database round trips)

### 5. Vectorize Threshold Checks

**Current**: Loop through snapshots sequentially
```python
for snapshot in yes_snapshots:
    highest_bid = snapshot._highest_bid
    if highest_bid < threshold:
        # trigger
```

**Optimized**: Use numpy for bulk comparisons
```python
import numpy as np

# Extract all highest_bids at once
highest_bids = np.array([s._highest_bid for s in yes_snapshots 
                         if s._highest_bid is not None])

# Vectorized comparison
trigger_indices = np.where(highest_bids < threshold)[0]
if len(trigger_indices) > 0:
    first_trigger_idx = trigger_indices[0]
    trigger_time = yes_snapshots[first_trigger_idx].timestamp
```

**Expected Speedup**: 1.5-2x (for markets with many snapshots)

### 6. Cache Expensive Operations

**Current**: Parse outcome prices repeatedly

**Optimized**: Cache parsed prices
```python
# Cache outcome prices per market
outcome_price_cache = {}
for market in preprocessed_markets:
    market_id = market['market_id']
    if market_id not in outcome_price_cache:
        outcome_price_cache[market_id] = parse_outcome_price(...)
```

**Expected Speedup**: 1.2-1.5x

### 7. Reduce Grid Search Space

**Current**: Tests all combinations

**Optimized**: Use smarter parameter selection
- Use larger step sizes for initial exploration
- Focus on promising regions
- Use Bayesian optimization instead of grid search

**Expected Speedup**: 10-100x (fewer combinations to test)

## Implementation Priority

1. **High Impact, Easy**: Pre-compute orderbook metrics (#2)
2. **High Impact, Medium**: Parallelize market processing (#1)
3. **Medium Impact, Easy**: Early termination (#3)
4. **Medium Impact, Medium**: Batch database queries (#4)
5. **Low Impact, Easy**: Cache expensive operations (#6)
6. **High Impact, Hard**: Vectorize threshold checks (#5)
7. **Variable Impact**: Reduce grid search space (#7)

## Expected Overall Speedup

Combining optimizations #1, #2, #3, #4:
- **Current**: ~1-2 markets/second
- **Optimized**: ~20-40 markets/second
- **Speedup**: **10-20x faster**

## Quick Wins (Can implement immediately)

1. Pre-compute `_highest_bid` and `_lowest_ask` in `_preprocess_market_snapshots`
2. Add early termination check before processing each market
3. Use `multiprocessing.Pool` for parallel market processing

