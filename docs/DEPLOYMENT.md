# Deployment Guide: Railway + Neon

This guide explains how to deploy the orderbook monitoring service to Railway with Neon PostgreSQL database.

## Why Railway + Neon?

- **Railway**: Easy Python deployment, automatic restarts, simple environment variable management
- **Neon**: Serverless PostgreSQL, perfect for time-series data, generous free tier
- **Alternative**: You could also use Render, Fly.io, or AWS/GCP

## Prerequisites

1. Railway account: https://railway.app
2. Neon account: https://neon.tech
3. GitHub repo (or Railway can deploy from local)

## Step 1: Set Up Neon Database

1. Go to https://neon.tech and create an account
2. Create a new project
3. Copy the connection string (looks like `postgres://user:pass@host/dbname`)
4. **Important**: Neon uses `postgres://` but SQLAlchemy needs `postgresql://` - the code handles this automatically

## Step 2: Deploy to Railway

### Option A: Deploy from GitHub

1. Push your code to GitHub
2. Go to Railway: https://railway.app
3. Click "New Project" → "Deploy from GitHub repo"
4. Select your repository
5. Railway will detect the Dockerfile and deploy

### Option B: Deploy from CLI

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login
railway login

# Initialize project
railway init

# Link to existing project (or create new)
railway link

# Deploy
railway up
```

## Step 3: Configure Environment Variables

In Railway dashboard, go to your service → Variables tab, add:

### Required Variables

```
DATABASE_URL=postgresql://user:pass@host.neon.tech/dbname?sslmode=require
POLYGON_WALLET_PRIVATE_KEY=your_private_key_here
```

### Optional Variables

```
# Monitoring configuration
CHECK_INTERVAL=60                    # Seconds between market checks
MONITOR_15MIN=true                   # Monitor 15-minute markets
MONITOR_1HOUR=true                   # Monitor 1-hour markets
MODE=websocket                       # websocket or poll

# Logging
LOG_LEVEL=INFO                       # DEBUG, INFO, WARNING, ERROR
```

### Adding Neon Database to Railway

**Option 1: Add as Railway Service**
1. In Railway project, click "+ New" → "Database" → "Add PostgreSQL"
2. Railway will create a PostgreSQL instance
3. Copy the `DATABASE_URL` from Railway (automatically set as env var)

**Option 2: Use External Neon**
1. Create Neon database separately
2. Add `DATABASE_URL` manually in Railway variables

## Step 4: Update Start Command

Railway will use the `railway.json` config, which sets:
```json
"startCommand": "python scripts/python/auto_monitor_markets.py"
```

Or you can set it manually in Railway dashboard:
- Service → Settings → Start Command: `python scripts/python/auto_monitor_markets.py`

## Step 5: Verify Deployment

1. Check Railway logs to see if service started
2. Look for messages like:
   ```
   Starting auto monitor:
     - 15-minute markets: True
     - 1-hour markets: True
     - Check interval: 60s
   ```
3. Check Neon dashboard to see if tables are created
4. Query the database to verify data is being logged

## Monitoring & Logs

### Railway Logs
- View in Railway dashboard → Service → Logs
- Real-time streaming logs
- Search/filter capabilities

### Database Monitoring
```python
# Query from anywhere
from agents.polymarket.orderbook_query import OrderbookQuery

query = OrderbookQuery(database_url="your_neon_url")
snapshots = query.get_snapshots(limit=10)
print(f"Found {len(snapshots)} snapshots")
```

## Cost Estimation

### Railway
- **Free tier**: $5 credit/month
- **Hobby**: $20/month (if you exceed free tier)
- **Pro**: $100/month

### Neon
- **Free tier**: 0.5 GB storage, shared CPU
- **Launch**: $19/month (10 GB, dedicated CPU)
- **Scale**: $69/month (50 GB)

**Estimated cost for this service**: ~$0-20/month (likely free tier)

## Troubleshooting

### Service Won't Start

1. **Check logs**: Railway → Service → Logs
2. **Verify DATABASE_URL**: Must be valid PostgreSQL connection string
3. **Check Python path**: Ensure `PYTHONPATH` is set if needed
4. **Verify dependencies**: All packages in `requirements.txt` installed

### Database Connection Issues

1. **SSL Mode**: Neon requires SSL, code handles this automatically
2. **Connection String**: Ensure it's `postgresql://` (code converts `postgres://`)
3. **Firewall**: Neon allows all IPs by default
4. **Test connection**:
   ```python
   from agents.polymarket.orderbook_db import OrderbookDatabase
   db = OrderbookDatabase()
   # Should create tables automatically
   ```

### WebSocket Connection Issues

1. **Check Railway logs** for WebSocket errors
2. **Fallback to polling**: Set `MODE=poll` in environment variables
3. **Network**: Railway should have stable outbound connections

## Updating the Service

### Via GitHub (Recommended)
1. Push changes to GitHub
2. Railway auto-deploys (if auto-deploy enabled)
3. Or manually trigger: Railway → Service → Deployments → Redeploy

### Via CLI
```bash
railway up
```

## Scaling Considerations

### Single Instance
- Current setup runs one instance
- Handles multiple markets concurrently
- WebSocket can handle many subscriptions

### Multiple Instances (Advanced)
- Use Redis for coordination
- Partition markets across instances
- Or use separate services for 15min vs 1hour markets

## Backup Strategy

### Neon Automatic Backups
- Neon provides automatic backups
- Point-in-time recovery available
- Can export to SQL dump

### Manual Backup
```bash
# Export from Neon dashboard or CLI
pg_dump $DATABASE_URL > backup.sql

# Or query and export via Python
from agents.polymarket.orderbook_query import OrderbookQuery
query = OrderbookQuery()
query.export_to_csv("backup.csv", token_id="...")
```

## Alternative Deployments

### Render
- Similar to Railway
- Free tier available
- Use `Procfile` for start command

### Fly.io
- Global edge deployment
- Good for low latency
- Free tier available

### AWS/GCP
- More control, more setup
- Use ECS/Fargate or Cloud Run
- RDS/Cloud SQL for database

## Environment Variable Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes* | `sqlite:///./orderbook.db` | PostgreSQL connection string |
| `POLYGON_WALLET_PRIVATE_KEY` | Yes | - | Wallet private key for API access |
| `CHECK_INTERVAL` | No | `60` | Seconds between market checks |
| `MONITOR_15MIN` | No | `true` | Monitor 15-minute markets |
| `MONITOR_1HOUR` | No | `true` | Monitor 1-hour markets |
| `MODE` | No | `websocket` | `websocket` or `poll` |
| `LOG_LEVEL` | No | `INFO` | Logging level |

*Required for production (Neon), optional for local dev (uses SQLite)

