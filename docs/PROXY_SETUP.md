# Proxy/VPN Setup Guide for Binance and Polymarket APIs

**SIMPLE SETUP**: Configure VPN/proxy in ONE place, and all components automatically use it!

This guide explains how to configure proxy/VPN support for the BTC backtesting framework to bypass IP-based restrictions from Binance and Polymarket.

## Why Use a Proxy?

- **Binance**: Geo-blocks US IP addresses (HTTP 451 error)
- **Polymarket**: May have IP-based rate limiting or restrictions
- **Session Stability**: Static IPs prevent "account sharing" flags and improve reliability

## Supported Proxy Types

1. **Oxylabs Static ISP Proxies** (Recommended for production)
   - Static Dutch IP addresses
   - Best for Binance/Polymarket compatibility
   - Format: `http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001`

2. **Standard HTTP/HTTPS Proxies**
   - Format: `http://user:pass@proxy.example.com:8080`

3. **SOCKS5 Proxies** (requires `httpx-socks`)
   - Format: `socks5://proxy.example.com:1080`

## Configuration Methods

### Method 1: Simple Configuration Script (Easiest!)

**This is the recommended way** - configure VPN/proxy once, and all components use it automatically:

```bash
# Configure Oxylabs proxy
python scripts/python/configure_vpn.py --oxylabs --username YOUR_USERNAME --password YOUR_PASSWORD --port 8001 --verify

# Or auto-detect from environment variables
python scripts/python/configure_vpn.py --auto --verify

# Or use direct proxy URL
python scripts/python/configure_vpn.py --proxy "http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001" --verify
```

That's it! Once configured, all API calls automatically use the proxy. No need to pass proxy to individual components.

### Method 2: Environment Variables (Recommended for Production)

Set these environment variables before running your scripts:

```bash
# Simple format (recommended):
export PROXY_USER="your_username"  # Will be prefixed with "user-" automatically
export PROXY_PASS="your_password"
export PROXY_PORT="8001"  # Optional, defaults to 8001 (8001 = first Dutch IP, 8002 = second, etc.)

# Or use direct proxy URL
export HTTPS_PROXY="http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001"

# Or legacy Oxylabs format (still supported):
export OXYLABS_USERNAME="your_username"
export OXYLABS_PASSWORD="your_password"
export OXYLABS_PORT="8001"
```

**Note**: `PROXY_USER` will automatically be prefixed with `user-` if not already present, so you can use either:
- `export PROXY_USER="myuser"` → becomes `user-myuser`
- `export PROXY_USER="user-myuser"` → stays `user-myuser`

### Method 3: Code-Level Configuration (One-Time Setup)

Configure proxy once at the start of your script:

```python
from agents.utils.proxy_config import configure_proxy, get_oxylabs_proxy_url
from agents.backtesting.btc_backtester import BTCBacktester

# Configure proxy ONCE at the start of your script
proxy_url = get_oxylabs_proxy_url(
    username="your_username",
    password="your_password",
    port=8001
)
configure_proxy(proxy_url)

# Now all components automatically use the proxy - no need to pass it!
backtester = BTCBacktester(model_name="chronos-bolt")  # Uses proxy automatically
btc_fetcher = BTCDataFetcher()  # Uses proxy automatically
market_fetcher = HistoricalMarketFetcher()  # Uses proxy automatically
```

### Method 4: Using Proxy Utility (Advanced)

```python
from agents.utils.proxy_config import get_oxylabs_proxy_url, verify_proxy_ip

# Create Oxylabs proxy URL
proxy_url = get_oxylabs_proxy_url(
    username="your_username",
    password="your_password",
    port=8001  # Dutch IP port
)

# Verify proxy is working
ip_info = verify_proxy_ip(proxy_url)
print(f"IP: {ip_info['ip']} | Location: {ip_info['city']}, {ip_info['country']}")
```

## Testing Proxy Configuration

Use the test script to verify your proxy setup:

```bash
# Using environment variables
export OXYLABS_USERNAME="your_username"
export OXYLABS_PASSWORD="your_password"
export OXYLABS_PORT="8001"
python scripts/python/test_proxy.py

# Or pass proxy URL directly
python scripts/python/test_proxy.py --proxy "http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001"

# Or use command-line arguments
python scripts/python/test_proxy.py --username "your_username" --password "your_password" --port 8001
```

The test script will:
1. Verify proxy connection and IP location
2. Test Binance API access
3. Test Polymarket API access

## Example: Running Backtests with Proxy

```python
from agents.backtesting.btc_backtester import BTCBacktester
from datetime import datetime, timedelta, timezone
import os

# Configure proxy via environment variable
os.environ["OXYLABS_USERNAME"] = "your_username"
os.environ["OXYLABS_PASSWORD"] = "your_password"
os.environ["OXYLABS_PORT"] = "8001"

# Initialize backtester (will automatically use proxy from env)
backtester = BTCBacktester(model_name="chronos-bolt")

# Run backtest
results = backtester.run_backtest(
    start_date=datetime.now(timezone.utc) - timedelta(days=7),
    end_date=datetime.now(timezone.utc),
    max_markets=50
)
```

## Oxylabs Static ISP Proxy Setup

### Getting Started with Oxylabs

1. **Sign up** for Oxylabs Static ISP proxy service
2. **Choose Dutch IP** (recommended for Binance/Polymarket)
3. **Get credentials**:
   - Username (will be prefixed with `user-` automatically)
   - Password
   - Port number (8001, 8002, etc. - each port = one static IP)

### Port Mapping

- Port `8001` = First Dutch Static IP
- Port `8002` = Second Dutch Static IP
- Port `8003` = Third Dutch Static IP
- etc.

### Proxy URL Format

```
http://user-USERNAME:PASSWORD@isp.oxylabs.io:PORT
```

Example:
```
http://user-myaccount:MySecurePass123@isp.oxylabs.io:8001
```

## Troubleshooting

### Issue: Proxy not working

**Check:**
1. Verify proxy URL format is correct
2. Check credentials are valid
3. Ensure port number matches your Oxylabs dashboard
4. Test with `test_proxy.py` script

**Solution:**
```bash
python scripts/python/test_proxy.py --proxy "YOUR_PROXY_URL"
```

### Issue: Binance still returns 451 error

**Possible causes:**
1. Proxy IP is still detected as US-based
2. Proxy not being used (check logs for "Using proxy" message)
3. IP rotation (use Static ISP, not rotating proxies)

**Solution:**
- Use Oxylabs Static ISP proxies (not rotating)
- Verify IP location with `verify_proxy_ip()`
- Check that proxy is actually being used in requests

### Issue: SOCKS proxy not working

**Solution:**
Install `httpx-socks`:
```bash
pip install httpx-socks
```

### Issue: Polymarket API errors

**Check:**
1. Proxy is configured correctly
2. API credentials are valid (if using authenticated access)
3. Rate limits are not exceeded

## Security Best Practices

1. **Never commit credentials** to git
2. **Use environment variables** or `.env` file (add to `.gitignore`)
3. **Rotate credentials** periodically
4. **Use separate proxy IPs** for different services if possible
5. **Monitor proxy usage** in Oxylabs dashboard

## Example .env File

Create a `.env` file in project root:

```bash
# Proxy Configuration (Simple format - recommended)
PROXY_USER=your_username
PROXY_PASS=your_password
PROXY_PORT=8001

# Or use direct proxy URL
# HTTPS_PROXY=http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001

# Or legacy Oxylabs format (still supported)
# OXYLABS_USERNAME=your_username
# OXYLABS_PASSWORD=your_password
# OXYLABS_PORT=8001

# Polymarket API (if using authenticated access)
POLYGON_WALLET_PRIVATE_KEY=your_private_key
```

Load with `python-dotenv`:
```python
from dotenv import load_dotenv
load_dotenv()  # Loads .env file automatically
```

## Additional Resources

- [Oxylabs Documentation](https://oxylabs.io/docs)
- [Binance API Documentation](https://binance-docs.github.io/apidocs/spot/en/)
- [Polymarket API Documentation](https://docs.polymarket.com/)

