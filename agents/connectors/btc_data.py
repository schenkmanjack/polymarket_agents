"""
BTC Price Data Fetcher

Fetches historical Bitcoin price data from Binance API (public endpoint, no API key required).
Caches data locally for efficient backtesting.

API Key: NOT REQUIRED - Uses Binance public endpoints (no authentication needed)
Rate Limits: 1200 requests/minute (IP-based) - code includes rate limiting

Recommended Intervals for AI Models:
- For 15-minute predictions: Use '1m' interval (1-minute granularity)
  - Provides 200 data points = 200 minutes of history (~3.3 hours)
  - Good balance of granularity and sequence length
- Alternative: '5m' interval (200 points = 16.7 hours of history)
  - Less granular but covers longer time periods

Usage:
    from agents.connectors.btc_data import BTCDataFetcher
    
    fetcher = BTCDataFetcher()
    
    # Get prices for a time range
    prices = fetcher.get_prices(
        start_time=datetime(2024, 1, 1),
        end_time=datetime(2024, 1, 2),
        interval='1m'  # Recommended for 15-minute predictions
    )
    
    # Get price sequence for model input (Lag-Llama/Chronos-Bolt)
    sequence = fetcher.get_price_sequence(
        timestamp=datetime(2024, 1, 1, 12, 0),
        lookback_minutes=200,  # 200 data points
        interval='1m'  # 1-minute intervals
    )
    # Returns: [price_t-200, price_t-199, ..., price_t-1, price_t]
    # Ready to pass directly to AI model
"""
import os
import logging
import httpx
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple
from pathlib import Path
import json

logger = logging.getLogger(__name__)


class BTCDataFetcher:
    """
    Fetches and caches historical BTC price data from CoinGecko API.
    
    Features:
    - Fetches OHLCV data from CoinGecko
    - Caches data locally (CSV/Parquet) for fast access
    - Supports multiple intervals (1m, 5m, 15m, 1h, etc.)
    - Efficient data retrieval with automatic caching
    """
    
    def __init__(self, cache_dir: Optional[str] = None, proxy: Optional[str] = None):
        """
        Initialize BTC data fetcher.
        
        Uses Binance public API - NO API KEY REQUIRED.
        Rate limits: 1200 requests/minute (IP-based)
        
        Args:
            cache_dir: Directory to cache data files. Defaults to ./data/btc_cache/
            proxy: Optional proxy URL. Supports:
                   - HTTP/HTTPS: "http://user:pass@proxy.example.com:8080"
                   - Oxylabs: "http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001"
                   - Or set HTTPS_PROXY/OXYLABS_* environment variables
        """
        if cache_dir is None:
            cache_dir = os.path.join(os.getcwd(), "data", "btc_cache")
        
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.base_url = "https://api.binance.com/api/v3"
        self.coingecko_url = "https://api.coingecko.com/api/v3"
        self.timeout = 30.0
        
        # Proxy configuration - use provided proxy or global config
        if proxy is None:
            from agents.utils.proxy_config import get_proxy
            proxy = get_proxy()
        self.proxy = proxy
        
        # Headers for API requests
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        # Rate limiting: Binance allows 1200 requests/minute (IP-based)
        # Using 1.2s between requests = ~50 requests/minute (well under limit)
        self._last_request_time = None
        self._min_request_interval = 1.2  # seconds between requests
        
        logger.info(f"BTC Data Fetcher initialized. Cache directory: {self.cache_dir}")
        if self.proxy:
            logger.info(f"Using proxy: {self.proxy.split('@')[1] if '@' in self.proxy else 'configured'}")
        logger.info("Using Binance public API - no API key required")
    
    def _get_cache_path(self, start_date: datetime, end_date: datetime, interval: str) -> Path:
        """Get cache file path for a date range."""
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        filename = f"btc_{interval}_{start_str}_{end_str}.parquet"
        return self.cache_dir / filename
    
    def _rate_limit(self):
        """Simple rate limiting to avoid hitting API limits."""
        import time
        if self._last_request_time:
            elapsed = time.time() - self._last_request_time
            if elapsed < self._min_request_interval:
                time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()
    
    def _fetch_from_api(
        self, 
        start_time: datetime, 
        end_time: datetime, 
        interval: str = "1m"
    ) -> pd.DataFrame:
        """
        Fetch BTC OHLCV data from Binance API (public endpoint, no auth required).
        
        Note: This method is kept for compatibility but directly calls Binance.
        Binance provides better granularity and reliability than CoinGecko.
        
        Args:
            start_time: Start datetime (UTC)
            end_time: End datetime (UTC)
            interval: Data interval ('1m', '5m', '15m', '1h', '1d')
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        # Directly use Binance API (better for our use case)
        return self._fetch_from_binance(start_time, end_time, interval)
    
    def _fetch_from_binance(
        self,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1m"
    ) -> pd.DataFrame:
        """
        Fetch BTC OHLCV data from Binance API (better for granular data).
        
        Args:
            start_time: Start datetime (UTC)
            end_time: End datetime (UTC)
            interval: Data interval ('1m', '5m', '15m', '1h', etc.)
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        self._rate_limit()
        
        # Binance API endpoint
        url = "https://api.binance.com/api/v3/klines"
        
        # Binance interval mapping
        interval_map = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d"
        }
        
        if interval not in interval_map:
            raise ValueError(f"Unsupported interval: {interval}. Supported: {list(interval_map.keys())}")
        
        binance_interval = interval_map[interval]
        symbol = "BTCUSDT"
        
        # Binance expects timestamps in milliseconds
        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)
        
        all_data = []
        current_start = start_ms
        limit = 1000  # Binance max per request
        
        logger.info(f"Fetching BTC data from Binance: {start_time} to {end_time} ({interval})")
        
        while current_start < end_ms:
            params = {
                "symbol": symbol,
                "interval": binance_interval,
                "startTime": current_start,
                "endTime": min(current_start + (limit * self._interval_to_ms(binance_interval)), end_ms),
                "limit": limit
            }
            
            try:
                # Use proxy if configured (always check global config)
                from agents.utils.proxy_config import get_proxy_dict
                proxies = get_proxy_dict()  # Always use global proxy if configured
                
                response = httpx.get(url, params=params, timeout=self.timeout, proxies=proxies)
                response.raise_for_status()
                data = response.json()
                
                if not data:
                    break
                
                all_data.extend(data)
                
                # Update start time for next batch
                # Last timestamp + interval
                last_timestamp = data[-1][0]
                current_start = last_timestamp + self._interval_to_ms(binance_interval)
                
                logger.debug(f"Fetched {len(data)} candles, total: {len(all_data)}")
                
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error fetching from Binance: {e}")
                raise
            except Exception as e:
                logger.error(f"Error fetching from Binance: {e}")
                raise
        
        if not all_data:
            logger.warning("No data returned from Binance")
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        
        # Parse Binance response
        # Format: [timestamp, open, high, low, close, volume, ...]
        df = pd.DataFrame(all_data, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"
        ])
        
        # Convert to proper types
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        
        # Select and rename columns
        df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        
        # Filter to exact time range
        df = df[(df["timestamp"] >= start_time) & (df["timestamp"] <= end_time)]
        
        logger.info(f"✓ Fetched {len(df)} candles from Binance")
        return df
    
    def _interval_to_ms(self, interval: str) -> int:
        """Convert interval string to milliseconds."""
        unit = interval[-1]
        value = int(interval[:-1])
        
        multipliers = {
            "m": 60 * 1000,      # minutes
            "h": 3600 * 1000,     # hours
            "d": 86400 * 1000     # days
        }
        
        return value * multipliers.get(unit, 0)
    
    def _load_from_cache(
        self, 
        start_time: datetime, 
        end_time: datetime, 
        interval: str
    ) -> Optional[pd.DataFrame]:
        """Load data from cache if available."""
        cache_path = self._get_cache_path(start_time, end_time, interval)
        
        if not cache_path.exists():
            return None
        
        try:
            df = pd.read_parquet(cache_path)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            
            # Check if cached data covers the requested range
            cached_start = df["timestamp"].min()
            cached_end = df["timestamp"].max()
            
            if cached_start <= start_time and cached_end >= end_time:
                # Filter to requested range
                df = df[(df["timestamp"] >= start_time) & (df["timestamp"] <= end_time)]
                logger.debug(f"Loaded {len(df)} rows from cache")
                return df
            else:
                logger.debug(f"Cache doesn't cover full range: {cached_start} to {cached_end}, need {start_time} to {end_time}")
                return None
                
        except Exception as e:
            logger.warning(f"Error loading cache: {e}")
            return None
    
    def _save_to_cache(
        self, 
        df: pd.DataFrame, 
        start_time: datetime, 
        end_time: datetime, 
        interval: str
    ):
        """Save data to cache."""
        cache_path = self._get_cache_path(start_time, end_time, interval)
        
        try:
            df.to_parquet(cache_path, index=False)
            logger.debug(f"Saved {len(df)} rows to cache: {cache_path}")
        except Exception as e:
            logger.warning(f"Error saving cache: {e}")
    
    def get_prices(
        self,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1m",
        use_cache: bool = True,
        force_refresh: bool = False
    ) -> pd.DataFrame:
        """
        Get BTC prices for a time range.
        
        Args:
            start_time: Start datetime (UTC, timezone-aware)
            end_time: End datetime (UTC, timezone-aware)
            interval: Data interval ('1m', '5m', '15m', '1h', etc.)
            use_cache: Whether to use cached data
            force_refresh: Force refresh even if cache exists
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        # Ensure timezone-aware
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        
        # Try cache first
        if use_cache and not force_refresh:
            cached_data = self._load_from_cache(start_time, end_time, interval)
            if cached_data is not None and len(cached_data) > 0:
                return cached_data
        
        # Fetch from API
        logger.info(f"Fetching BTC data: {start_time} to {end_time} ({interval})")
        df = self._fetch_from_binance(start_time, end_time, interval)
        
        # Save to cache
        if use_cache and len(df) > 0:
            self._save_to_cache(df, start_time, end_time, interval)
        
        return df
    
    def get_price_sequence(
        self,
        timestamp: datetime,
        lookback_minutes: int = 200,
        interval: str = "1m"
    ) -> List[float]:
        """
        Get price sequence for model input (just close prices).
        
        Recommended for Lag-Llama/Chronos-Bolt:
        - interval='1m' (1-minute intervals) for 15-minute predictions
        - lookback_minutes=200 (200 data points = ~3.3 hours of history)
        
        Args:
            timestamp: Target timestamp (UTC, timezone-aware)
            lookback_minutes: How many minutes of history to get (default: 200)
            interval: Data interval - use '1m' for best granularity (default: '1m')
        
        Returns:
            List of close prices (oldest to newest)
            Example: [43000.0, 43050.0, ..., 43250.5]  # 200 prices
        
        Note:
            This returns raw price values. Models like Lag-Llama/Chronos-Bolt
            can handle these directly - they learn patterns from the sequence.
        """
        start_time = timestamp - timedelta(minutes=lookback_minutes)
        end_time = timestamp
        
        df = self.get_prices(start_time, end_time, interval=interval)
        
        if df.empty:
            logger.warning(f"No data found for sequence at {timestamp}")
            return []
        
        # Return close prices as list (oldest to newest)
        return df["close"].tolist()
    
    def get_price_at_time(
        self,
        timestamp: datetime,
        tolerance_seconds: int = 60
    ) -> Optional[float]:
        """
        Get BTC price at a specific timestamp.
        
        Args:
            timestamp: Target timestamp (UTC, timezone-aware)
            tolerance_seconds: Maximum seconds away from timestamp to accept
        
        Returns:
            Close price or None if not found
        """
        start_time = timestamp - timedelta(seconds=tolerance_seconds)
        end_time = timestamp + timedelta(seconds=tolerance_seconds)
        
        df = self.get_prices(start_time, end_time, interval="1m")
        
        if df.empty:
            return None
        
        # Find closest timestamp
        df["time_diff"] = abs((df["timestamp"] - timestamp).dt.total_seconds())
        closest = df.loc[df["time_diff"].idxmin()]
        
        if closest["time_diff"] > tolerance_seconds:
            return None
        
        return float(closest["close"])
    
    def get_ohlcv(
        self,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1m"
    ) -> pd.DataFrame:
        """
        Get full OHLCV data (alias for get_prices).
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        return self.get_prices(start_time, end_time, interval)


if __name__ == "__main__":
    # Example usage
    logging.basicConfig(level=logging.INFO)
    
    fetcher = BTCDataFetcher()
    
    # Test: Get last 24 hours of 1-minute data
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=1)
    
    print(f"Fetching BTC data from {start_time} to {end_time}...")
    df = fetcher.get_prices(start_time, end_time, interval="1m")
    
    print(f"\nFetched {len(df)} rows")
    print(df.head())
    print(f"\nPrice range: ${df['close'].min():.2f} - ${df['close'].max():.2f}")
    
    # Test: Get price sequence
    print(f"\nGetting price sequence for {end_time}...")
    sequence = fetcher.get_price_sequence(end_time, lookback_minutes=100)
    print(f"Sequence length: {len(sequence)}")
    print(f"First 10 prices: {sequence[:10]}")
    print(f"Last 10 prices: {sequence[-10:]}")

