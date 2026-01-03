# Quick Deploy Guide

## Step 1: Deploy to Railway

1. Go to https://railway.app
2. Click "New Project" → "Deploy from GitHub repo"
3. Select your `polymarket_agents` repository
4. Railway will auto-detect the Dockerfile and start deploying

## Step 2: Add Environment Variables

In Railway dashboard → Your service → Variables tab, add:

```
DATABASE_URL=your_neon_connection_string_here
POLYGON_WALLET_PRIVATE_KEY=your_private_key_here
```

## Step 3: Update Start Command

In Railway → Service → Settings → Start Command, change to:

```
python scripts/python/monitor_specific_market.py --event-slug btc-updown-15m-1767393900
```

This will monitor the specific market you shared.

## Step 4: Deploy

Railway will automatically redeploy with the new start command.

## Alternative: Monitor Multiple Markets

To monitor multiple 15-minute markets as they appear, use:

```
python scripts/python/auto_monitor_markets.py
```

But since the API might filter them, the specific market monitor is better for now.

## Check Logs

In Railway → Service → Logs, you should see:
- "Found market: ID=..."
- "Starting to monitor X tokens"
- Orderbook updates being logged

## Verify Data

Query your Neon database to see if data is being logged:

```python
from agents.polymarket.orderbook_query import OrderbookQuery
query = OrderbookQuery()
snapshots = query.get_snapshots(limit=10)
print(f"Found {len(snapshots)} snapshots")
```

