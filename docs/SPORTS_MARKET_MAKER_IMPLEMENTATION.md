# Sports Market Maker Implementation

## Overview

The Sports Market Maker extends the existing BTC market maker to trade live sports events on Polymarket. It uses the same split position strategy (split USDC into YES + NO shares, then place limit sell orders) but is adapted for sports markets.

## Key Features

### 1. Game Start Detection
- **Checks `startDate`/`startDateIso`** to verify games have already begun
- **Fallback**: Estimates start time from `endDate` minus typical game duration:
  - NFL: 3 hours
  - NBA: 2.5 hours
  - NHL: 2.5 hours
  - Soccer: 2 hours
- **Buffer**: Requires game to have started at least 5 minutes ago (configurable via `game_start_buffer_minutes`)

### 2. Market Prioritization
Markets are prioritized by:
1. **Liquidity** (higher is better)
2. **Time until resolution** (shorter is better - more urgency)

### 3. Concurrent Position Limits
- **Default: 1 concurrent position** (configurable via `max_concurrent_positions`)
- Only starts new positions when under the limit
- Automatically selects the best available market based on prioritization

### 4. Exit Before Resolution
- **Default: Exit 5 minutes before `endDate`** (configurable via `exit_minutes_before_resolution`)
- This avoids holding positions through uncertain resolution periods
- **Overtime handling**: Exits at original `endDate` minus buffer, regardless of whether game goes to OT
  - Rationale: Sports markets resolve based on game outcome, but Polymarket's `endDate` doesn't change for OT
  - Safer to exit before resolution than risk holding through delayed/uncertain resolution

### 5. Minimum Liquidity Threshold
- **Default: $10,000** (configurable via `min_liquidity`)
- Rationale: Ensures sufficient orderbook depth for reliable trading
- Can be adjusted based on your risk tolerance and capital

## Configuration

See `config/sports_market_maker_config.json` for all configurable parameters:

```json
{
  "split_amount": 6.0,                    // Amount to split per position
  "offset_above_midpoint": 0.01,          // Initial sell price offset
  "price_step": 0.01,                     // Price adjustment step
  "min_liquidity": 10000.0,               // Minimum liquidity required
  "max_concurrent_positions": 1,          // Max positions at once
  "exit_minutes_before_resolution": 5.0,   // Exit buffer before endDate
  "game_start_buffer_minutes": 5.0,       // Buffer after game start
  "topics": ["nfl", "nba", "nhl", "soccer"] // Sports to monitor
}
```

## Usage

### Running the Sports Market Maker

```python
from agents.trading.sports_market_maker import SportsMarketMaker
import asyncio

async def main():
    maker = SportsMarketMaker("config/sports_market_maker_config.json")
    await maker.start()

if __name__ == "__main__":
    asyncio.run(main())
```

### Market Detection

The detector runs every 60 seconds (configurable) and:
1. Queries Polymarket API for active markets in configured topics
2. Filters for markets where games have already started
3. Checks liquidity and token availability
4. Adds qualifying markets to monitoring pool
5. Prioritizes markets for position entry

## Architecture

### Components

1. **SportsMarketDetector** (`agents/trading/sports_market_detector.py`)
   - Detects live sports markets
   - Filters by game start time
   - Prioritizes markets

2. **SportsMarketMaker** (`agents/trading/sports_market_maker.py`)
   - Extends `MarketMaker` class
   - Manages position limits
   - Handles exit before resolution
   - Integrates with WebSocket orderbook/order status

3. **SportsMarketMakerConfig** (`agents/trading/sports_market_maker.py`)
   - Extends `MarketMakerConfig`
   - Adds sports-specific configuration

## Differences from BTC Market Maker

| Feature | BTC Market Maker | Sports Market Maker |
|---------|------------------|---------------------|
| Market Detection | Pattern-based (slug contains timestamp) | Topic-based + startDate filtering |
| Market Selection | Single market per type | Prioritized from pool |
| Concurrent Positions | Unlimited | Limited (default: 1) |
| Exit Timing | At resolution | Before resolution (configurable) |
| Market Duration | Fixed (15m or 1h) | Variable (game-dependent) |

## Recommendations

### Minimum Liquidity
- **$10,000 default** is reasonable for most use cases
- Lower ($5,000): More markets available, but thinner orderbooks
- Higher ($50,000+): Fewer markets, but better execution

### Exit Timing
- **5 minutes default** balances safety vs. opportunity
- Shorter (2-3 min): More time in market, but riskier
- Longer (10-15 min): Safer, but less time for fills

### Overtime Handling
The current approach (exit at original `endDate` minus buffer) is conservative but safe:
- ✅ Avoids holding through uncertain resolution periods
- ✅ Prevents exposure to delayed resolution
- ⚠️ May exit before game actually ends if it goes to OT

Alternative approaches (not implemented):
- Monitor for resolution status changes
- Extend exit time if market not resolved at `endDate`
- Use external game data to detect OT

## Testing

Before running live:
1. Test with small `split_amount` ($1-2)
2. Monitor detection logic with logging
3. Verify start date filtering works correctly
4. Test exit before resolution timing
5. Verify WebSocket orderbook/order status integration

## Questions & Answers

### Q: What happens if a game goes to overtime?
**A:** The market maker exits at the original `endDate` minus the exit buffer (default 5 minutes), regardless of OT. This is safer than holding through uncertain resolution periods.

### Q: Why exit before resolution?
**A:** Sports markets can have delayed or uncertain resolutions. Exiting before `endDate` reduces risk of holding positions through resolution uncertainty.

### Q: Can I trade multiple games simultaneously?
**A:** Yes, increase `max_concurrent_positions` in config. Default is 1 for conservative risk management.

### Q: How often are markets checked?
**A:** Every 60 seconds (configurable via `detection_interval_seconds`). This balances responsiveness with API rate limits.

### Q: What if `startDate` is missing?
**A:** The detector estimates start time from `endDate` minus typical game duration for that sport. If estimation fails, the market is skipped.
