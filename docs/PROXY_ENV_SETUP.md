# Proxy Configuration via .env File

## Quick Setup

**Yes, you can put proxy settings in `.env` file!** And **no, you don't need a Binance API key** - Binance public API doesn't require authentication.

## 1. Create/Update `.env` File

Add these lines to your `.env` file in the project root:

```bash
# Proxy/VPN Configuration (for Binance and Polymarket)
PROXY_USER=your_username
PROXY_PASS=your_password
PROXY_PORT=8001

# Optional: Polymarket wallet (only needed for authenticated API access)
POLYGON_WALLET_PRIVATE_KEY=your_private_key_here
```

**Note**: `PROXY_USER` will automatically be prefixed with `user-` if needed, so you can use:
- `PROXY_USER=myuser` → becomes `user-myuser`
- `PROXY_USER=user-myuser` → stays `user-myuser`

## 2. How It Works

The codebase automatically loads `.env` file via `python-dotenv`:

- `market_fetcher.py` calls `load_dotenv()` at import time
- `proxy_config.py` reads environment variables (which includes `.env` values)
- All components automatically detect and use the proxy

**No code changes needed** - just set the variables in `.env`!

## 3. Binance API Key

**You do NOT need a Binance API key!**

- Binance public endpoints (used for OHLCV data) don't require authentication
- The code uses: `https://api.binance.com/api/v3/klines` (public endpoint)
- No API key needed, no authentication required
- Only requires a non-US IP (which is why we use the proxy)

## 4. Example .env File

```bash
# Proxy Configuration (Required for Binance if you're in US)
PROXY_USER=my_oxylabs_username
PROXY_PASS=my_oxylabs_password
PROXY_PORT=8001

# Polymarket Wallet (Optional - only for authenticated API access)
POLYGON_WALLET_PRIVATE_KEY=0xYourPrivateKeyHere

# Other optional variables
OPENAI_API_KEY=your_openai_key_if_needed
```

## 5. Testing

After setting up `.env`, test it:

```bash
# Test proxy configuration
python scripts/python/test_proxy.py --auto

# Test Binance access (no API key needed!)
python scripts/python/test_btc_fetcher.py

# Run backtest (uses proxy automatically)
python scripts/python/test_backtesting.py
```

## Summary

✅ **`.env` file works** - variables are automatically loaded  
✅ **No Binance API key needed** - uses public endpoints  
✅ **Proxy configured once** - all components use it automatically  
✅ **Simple setup** - just add PROXY_USER, PROXY_PASS, PROXY_PORT to `.env`

