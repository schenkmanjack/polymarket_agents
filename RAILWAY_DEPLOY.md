# Railway Deployment Steps

## Quick Deploy Guide

### Step 1: Create Railway Project
1. Go to https://railway.app
2. Sign up/Login
3. Click **"New Project"**
4. Select **"Deploy from GitHub repo"**
5. Authorize Railway to access GitHub if needed
6. Select your repository: **`schenkmanjack/polymarket_agents`**

### Step 2: Add Environment Variables
1. In Railway dashboard, click on your service
2. Go to **"Variables"** tab (top menu)
3. Click **"+ New Variable"** and add:

   **Variable 1:**
   - Name: `DATABASE_URL`
   - Value: `postgresql://neondb_owner:npg_enBG0KZD3kvt@ep-super-frost-ahv6x9a1-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`
   - (Or your Neon connection string)

   **Variable 2:**
   - Name: `POLYGON_WALLET_PRIVATE_KEY`
   - Value: `0x86e06e52fdf3d0b7b600c68cf228300b570affabecdb7fe6de7ecbde88a4a031`
   - (Your private key from MetaMask)

### Step 3: Update Start Command
1. Go to **"Settings"** tab
2. Scroll to **"Deploy"** section
3. Find **"Custom Start Command"**
4. Change it to:
   ```
   python scripts/python/monitor_specific_market.py --event-slug btc-updown-15m-1767393900
   ```
5. Click **"Update"**

### Step 4: Deploy
- Railway will automatically detect the change and redeploy
- Or go to **"Deployments"** tab and click **"Redeploy"**

### Step 5: Check Logs
1. Go to **"Logs"** tab
2. You should see:
   - "Found market: ID=..."
   - "✓ Wallet key found - Using WebSocket mode (lower latency)"
   - "Starting to monitor X tokens"
   - Orderbook updates being logged

## What Happens Next

Once deployed:
- ✅ Service runs 24/7
- ✅ Automatically monitors the specified market
- ✅ Logs orderbook snapshots to Neon database
- ✅ Uses WebSocket for low-latency updates
- ✅ Auto-restarts on failure

## Monitoring

### Check if it's running:
- Railway dashboard → Service → Logs tab
- Look for orderbook update messages

### Query your database:
```python
from agents.polymarket.orderbook_query import OrderbookQuery
query = OrderbookQuery()
snapshots = query.get_snapshots(limit=10)
print(f"Found {len(snapshots)} snapshots")
```

## Troubleshooting

**Service won't start:**
- Check logs for errors
- Verify DATABASE_URL is correct
- Verify POLYGON_WALLET_PRIVATE_KEY starts with `0x`

**No data being logged:**
- Check logs for "Found market" message
- Verify the event slug is correct
- Check Neon database connection

**WebSocket connection issues:**
- Check logs for WebSocket errors
- Falls back to polling automatically if WebSocket fails

## Cost

- **Railway**: Free tier ($5 credit/month) should be enough
- **Neon**: Free tier (0.5 GB) should be enough for orderbook data

## Next Steps After Deployment

1. Monitor logs for first few minutes
2. Check database for snapshots
3. Query data to verify it's working
4. Consider monitoring multiple markets by updating start command

