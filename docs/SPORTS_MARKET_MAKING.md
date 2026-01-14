# Sports Market Making - Codebase Adaptation Guide

## Current Status

✅ **Successfully connected to Polymarket API** - Can fetch live sports markets  
✅ **Topic filtering works** - API supports `topic` parameter (e.g., "sports", "nfl", "nba", "nhl", "soccer")  
✅ **Active markets found** - Found active NFL markets with good liquidity ($1M+)

## Key Differences: BTC Markets vs Sports Markets

### BTC Markets (Current Implementation)
- **Fixed duration**: 15-minute or 1-hour windows
- **Predictable timing**: New markets created at fixed intervals
- **Resolution**: Based on BTC price at end of window
- **Market detection**: Pattern-based (slug contains timestamp)
- **Orderbook dynamics**: Continuous, price-based

### Sports Markets
- **Variable duration**: Game times vary (2-3 hours for most sports)
- **Event-based timing**: Markets created around game schedules
- **Resolution**: Based on game outcome (score, winner, etc.)
- **Market detection**: Need to query by topic/category and filter by game time
- **Orderbook dynamics**: Can spike during games, quieter pre-game

## API Access Confirmed

The Polymarket Gamma API supports:
- **Topic filtering**: `topic=sports`, `topic=nfl`, `topic=nba`, etc.
- **Active markets**: `active=true`, `closed=false`
- **Orderbook data**: `enableOrderBook=true`
- **Pagination**: `limit` and `offset` parameters

Example API call:
```
GET https://gamma-api.polymarket.com/markets?topic=nfl&active=true&limit=50&enableOrderBook=true
```

## Adaptation Strategy

### 1. Market Detection Module (`MarketDetector`)

**Current**: Detects BTC markets by slug pattern (`btc-updown-15m-{timestamp}`)

**For Sports**: Need to:
- Query Gamma API with topic filters (e.g., `topic=nfl`)
- Filter by `endDate` to find markets ending soon (live games)
- Track markets by `market_id` instead of slug
- Handle multiple concurrent games

**New Module**: `agents/trading/sports_market_detector.py`
```python
class SportsMarketDetector:
    def __init__(self, topics: List[str] = ["nfl", "nba", "nhl"]):
        self.topics = topics
        self.gamma = GammaMarketClient()
    
    async def get_live_sports_markets(self) -> List[Dict]:
        """Get markets for games happening now or soon."""
        # Query by topic, filter by endDate near current time
        # Return markets with orderbooks and good liquidity
        pass
```

### 2. Orderbook Monitoring (`OrderbookMonitor`)

**Current**: Monitors orderbooks for threshold triggers

**For Sports**: 
- ✅ Can reuse most logic
- ⚠️ May need different thresholds (sports markets can be more volatile)
- ⚠️ Need to handle multiple markets simultaneously (many games at once)
- ⚠️ Resolution timing is less predictable (games can go to overtime)

### 3. Order Management (`OrderManager`)

**Current**: Handles buy/sell orders, fill detection, status checking

**For Sports**:
- ✅ Can reuse most logic
- ⚠️ May need different confirmation times (sports markets move faster during games)
- ⚠️ Need to handle partial fills differently (sports markets can have large spreads)

### 4. Market Resolution (`ThresholdTrader._process_market_resolution`)

**Current**: Uses orderbook prices or `outcomePrices` to determine winner

**For Sports**:
- ✅ Can reuse orderbook price logic (YES/NO highest bid ≥ 0.98)
- ⚠️ Sports markets resolve based on game outcome, not price
- ⚠️ Need to wait for official resolution (may take time after game ends)
- ⚠️ Some markets resolve immediately, others may have delays

### 5. Configuration

**New config file**: `config/sports_trading_config.json`
```json
{
  "topics": ["nfl", "nba", "nhl"],
  "min_liquidity": 10000.0,
  "max_markets_concurrent": 10,
  "threshold": 0.68,
  "margin": 0.08,
  "threshold_confirmation_seconds": 5,
  "orderbook_poll_interval": 0.5
}
```

## Implementation Plan

### Phase 1: Market Detection
1. Create `SportsMarketDetector` class
2. Query Gamma API for active sports markets
3. Filter by liquidity, endDate, and orderbook availability
4. Track markets by market_id

### Phase 2: Integration
1. Create `SportsThresholdTrader` that uses `SportsMarketDetector`
2. Reuse `OrderbookMonitor` and `OrderManager` (with config adjustments)
3. Adapt resolution logic for sports market outcomes

### Phase 3: Testing
1. Test with low-stakes markets first
2. Monitor orderbook dynamics during live games
3. Verify resolution detection works correctly

## Example: Live NFL Markets Found

From the exploration script, we found active NFL markets like:
- "Will the Buffalo Bills win Super Bowl 2026?" - $1.7M liquidity
- "Will the Chicago Bears win Super Bowl 2026?" - $1.5M liquidity
- "Will the Bears win the NFC Championship?" - $58K liquidity

These markets have:
- ✅ Active orderbooks
- ✅ Good liquidity
- ✅ Token IDs available for trading
- ✅ Clear YES/NO outcomes

## Next Steps

1. **Create `SportsMarketDetector`** - Adapt market detection for sports
2. **Create `SportsThresholdTrader`** - Main trader class for sports
3. **Test with one topic** - Start with NFL markets
4. **Monitor performance** - Compare to BTC market making
5. **Scale up** - Add more sports topics

## Code Reusability

**High Reusability** (can use as-is):
- `OrderManager` - Order placement, status checking, fill detection
- `OrderbookMonitor` - Orderbook polling, threshold checking
- `TradingConfig` - Configuration loading and validation
- `TradeDatabase` - Database operations
- Orderbook helper functions

**Needs Adaptation**:
- `MarketDetector` - Different detection logic for sports
- `ThresholdTrader` - Different resolution timing and logic
- Market resolution helpers - May need sports-specific outcome parsing

**New Components Needed**:
- Sports market detection logic
- Game schedule integration (optional - for pre-game markets)
- Sports-specific resolution handlers

## Questions to Consider

1. **Which sports to focus on?** NFL, NBA, NHL, Soccer?
2. **What types of markets?** Game outcomes, player props, futures?
3. **Trading strategy?** Same threshold strategy or different approach?
4. **Risk management?** Different position sizing for sports vs crypto?
5. **Resolution timing?** How to handle games that go to overtime?
