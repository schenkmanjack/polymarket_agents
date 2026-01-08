"""
Historical Market Fetcher for BTC 15-minute markets.

Fetches closed/resolved BTC 15-minute markets from Polymarket for backtesting.
"""
import httpx
import logging
import re
import os
from typing import List, Optional, Dict
from datetime import datetime, timezone, timedelta
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


class HistoricalMarketFetcher:
    """
    Fetches historical BTC 15-minute markets from Polymarket.
    
    Markets are identified by their event slug pattern: btc-updown-15m-{timestamp}
    
    Can optionally use authenticated API access via wallet private key for better market access.
    """
    
    def __init__(self, use_auth: bool = True, proxy: Optional[str] = None):
        """
        Initialize market fetcher.
        
        Args:
            use_auth: If True, try to use authenticated API access via wallet key (if available)
            proxy: Optional proxy URL for VPN/routing. Supports:
                   - HTTP/HTTPS: "http://user:pass@proxy.example.com:8080"
                   - Oxylabs: "http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001"
                   - Or set HTTPS_PROXY/OXYLABS_* environment variables
        """
        self.gamma_url = "https://gamma-api.polymarket.com"
        self.gamma_events_endpoint = f"{self.gamma_url}/events"
        self.gamma_markets_endpoint = f"{self.gamma_url}/markets"
        self.clob_url = "https://clob.polymarket.com"
        self.clob_markets_endpoint = f"{self.clob_url}/markets"
        
        # Proxy configuration - use provided proxy or global config
        if proxy is None:
            from agents.utils.proxy_config import get_proxy
            proxy = get_proxy()
        self.proxy = proxy
        
        # Try to get authenticated API credentials if wallet key is available
        self.api_headers = None
        if use_auth:
            self._init_auth()
    
    def _init_auth(self):
        """Initialize authenticated API access if wallet key is available."""
        try:
            private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
            if not private_key:
                logger.info("No wallet key found, using public API")
                return
            
            # Try to get API credentials via CLOB client directly
            try:
                from py_clob_client.client import ClobClient
                from py_clob_client.constants import POLYGON
                
                client = ClobClient(
                    host=self.clob_url,
                    key=private_key,
                    chain_id=POLYGON
                )
                creds = client.create_or_derive_api_creds()
                client.set_api_creds(creds)
                
                if creds and hasattr(creds, 'api_key') and hasattr(creds, 'api_secret'):
                    # Create auth headers for CLOB API
                    import base64
                    auth_str = f"{creds.api_key}:{creds.api_secret}"
                    auth_bytes = base64.b64encode(auth_str.encode()).decode()
                    self.api_headers = {
                        'Authorization': f'Basic {auth_bytes}'
                    }
                    logger.info("✓ Using authenticated API access (wallet key)")
                    return
            except ImportError:
                logger.debug("py_clob_client not available, trying Polymarket class")
            except Exception as e:
                logger.debug(f"Could not get credentials via CLOB client: {e}")
            
            # Fallback: Try Polymarket class
            try:
                from agents.polymarket.polymarket import Polymarket
                pm = Polymarket()
                if hasattr(pm, 'credentials') and pm.credentials:
                    # Create auth headers for CLOB API
                    import base64
                    creds = pm.credentials
                    if hasattr(creds, 'api_key') and hasattr(creds, 'api_secret'):
                        auth_str = f"{creds.api_key}:{creds.api_secret}"
                        auth_bytes = base64.b64encode(auth_str.encode()).decode()
                        self.api_headers = {
                            'Authorization': f'Basic {auth_bytes}'
                        }
                        logger.info("✓ Using authenticated API access (wallet key via Polymarket)")
                        return
            except Exception as e:
                logger.debug(f"Could not initialize auth via Polymarket class: {e}")
                
        except Exception as e:
            logger.debug(f"Could not initialize auth: {e}, using public API")
        
        logger.info("Using public API (no authentication)")
    
    def extract_timestamp_from_slug(self, slug: str) -> Optional[int]:
        """
        Extract timestamp from event slug like 'btc-updown-15m-1767393900'.
        
        Returns:
            Timestamp as int, or None if not found
        """
        match = re.search(r'btc-updown-15m-(\d+)', slug.lower())
        if match:
            return int(match.group(1))
        return None
    
    def is_btc_15m_market(self, market: Dict) -> bool:
        """
        Check if a market is a BTC 15-minute updown market.
        
        Args:
            market: Market dict from API
            
        Returns:
            True if it's a BTC 15-minute market
        """
        question = (market.get("question") or "").lower()
        slug = (market.get("slug") or market.get("_event_slug") or "").lower()
        description = (market.get("description") or "").lower()
        
        # Check for BTC updown pattern
        is_btc = "bitcoin" in question or "btc" in question or "btc" in slug
        is_updown = ("up" in question and "down" in question) or "updown" in slug
        is_15m = "15m" in slug or "15m" in question or "15 min" in question or "15-minute" in question
        
        # Also check for timestamp pattern in slug
        has_timestamp = self.extract_timestamp_from_slug(slug) is not None
        
        return is_btc and is_updown and (is_15m or has_timestamp)
    
    def _get_clob_client_markets(self) -> List[Dict]:
        """Try to get markets using CLOB client (requires wallet key)."""
        try:
            private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
            if not private_key:
                return []
            
            from py_clob_client.client import ClobClient
            from py_clob_client.constants import POLYGON
            
            client = ClobClient(
                host=self.clob_url,
                key=private_key,
                chain_id=POLYGON
            )
            client.set_api_creds(client.create_or_derive_api_creds())
            
            # Try different CLOB API methods
            markets = []
            
            # Try get_simplified_markets (may return more markets)
            try:
                simplified = client.get_simplified_markets()
                if simplified and "data" in simplified:
                    markets.extend(simplified["data"])
                    logger.debug(f"CLOB simplified_markets returned {len(simplified['data'])} markets")
            except Exception as e:
                logger.debug(f"CLOB simplified_markets error: {e}")
            
            # Try get_sampling_simplified_markets
            try:
                sampling = client.get_sampling_simplified_markets()
                if sampling and "data" in sampling:
                    # Avoid duplicates
                    existing_ids = {m.get("id") for m in markets}
                    new_markets = [m for m in sampling["data"] if m.get("id") not in existing_ids]
                    markets.extend(new_markets)
                    logger.debug(f"CLOB sampling_simplified_markets returned {len(new_markets)} additional markets")
            except Exception as e:
                logger.debug(f"CLOB sampling_simplified_markets error: {e}")
            
            # Try get_markets (if available)
            try:
                all_markets = client.get_markets()
                if all_markets and "data" in all_markets:
                    existing_ids = {m.get("id") for m in markets}
                    new_markets = [m for m in all_markets["data"] if m.get("id") not in existing_ids]
                    markets.extend(new_markets)
                    logger.debug(f"CLOB get_markets returned {len(new_markets)} additional markets")
            except Exception as e:
                logger.debug(f"CLOB get_markets error: {e}")
            
            return markets
            
        except Exception as e:
            logger.debug(f"Could not use CLOB client: {e}")
            return []
    
    def get_any_btc_markets(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 1000,
        max_markets: Optional[int] = None,
        require_15m: bool = False
    ) -> List[Dict]:
        """
        Fetch any BTC markets (not just 15-minute ones) for testing purposes.
        
        Args:
            start_date: Start date for filtering markets (UTC)
            end_date: End date for filtering markets (UTC)
            limit: Number of markets to fetch per API call
            max_markets: Maximum total markets to return (None = no limit)
            require_15m: If True, only return 15-minute markets
            
        Returns:
            List of market dicts with BTC markets
        """
        all_markets = []
        
        logger.info(f"Fetching BTC markets (require_15m={require_15m})...")
        
        # Search events API
        search_configs = [
            {"archived": True, "limit": limit},
            {"closed": True, "archived": False, "limit": limit},
            {"closed": True, "archived": True, "limit": limit},
            {"limit": limit},
        ]
        
        for config_idx, base_params in enumerate(search_configs):
            offset = 0
            while True:
                params = base_params.copy()
                params["offset"] = offset
                
                try:
                    from agents.utils.proxy_config import get_proxy_dict
                    proxies = get_proxy_dict() if self.proxy else None
                    response = httpx.get(self.gamma_events_endpoint, params=params, timeout=30.0, proxies=proxies)
                    if response.status_code != 200:
                        break
                    
                    events = response.json()
                    if not events:
                        break
                    
                    for event in events:
                        slug = (event.get("slug") or "").lower()
                        title = (event.get("title") or "").lower()
                        
                        # Check if it's a BTC market
                        is_btc = "btc" in slug or "bitcoin" in title or "btc" in title
                        if not is_btc:
                            continue
                        
                        # If require_15m, check for 15-minute pattern
                        if require_15m:
                            is_15m = (
                                "15m" in slug or "15m" in title or
                                "15-min" in slug or "15-min" in title or
                                "btc-updown-15m" in slug
                            )
                            if not is_15m:
                                continue
                        
                        markets = event.get("markets", [])
                        for market in markets:
                            market["_event_slug"] = event.get("slug")
                            market["_event_title"] = event.get("title")
                            market["_event_id"] = event.get("id")
                            
                            # Try to extract timestamp
                            timestamp = self.extract_timestamp_from_slug(slug)
                            if not timestamp:
                                # Try to get from endDate
                                end_date_str = market.get("endDate")
                                if end_date_str:
                                    try:
                                        end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                                        # Market start is typically 15 minutes before end for 15m markets
                                        if require_15m:
                                            timestamp = int((end_dt - timedelta(minutes=15)).timestamp())
                                        else:
                                            timestamp = int(end_dt.timestamp())
                                    except:
                                        pass
                            
                            if timestamp:
                                market["_market_start_timestamp"] = timestamp
                                market["_market_start_time"] = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                            
                            # Filter by date range
                            if start_date or end_date:
                                market_start = market.get("_market_start_time")
                                if market_start:
                                    if start_date and market_start < start_date:
                                        continue
                                    if end_date and market_start > end_date:
                                        continue
                            
                            # Avoid duplicates
                            market_id = market.get("id")
                            if market_id and not any(m.get("id") == market_id for m in all_markets):
                                all_markets.append(market)
                                if max_markets and len(all_markets) >= max_markets:
                                    return all_markets
                    
                    if len(events) < limit or offset > limit * 5:
                        break
                    offset += limit
                    
                except Exception as e:
                    logger.debug(f"Error: {e}")
                    break
        
        # Sort by timestamp if available
        all_markets.sort(key=lambda m: m.get("_market_start_timestamp", 0))
        
        logger.info(f"✓ Found {len(all_markets)} BTC markets")
        return all_markets
    
    def _fetch_market_by_slug(self, slug: str) -> Optional[Dict]:
        """Fetch a specific market by its event slug."""
        try:
            from agents.utils.proxy_config import get_proxy_dict
            proxies = get_proxy_dict() if self.proxy else None
            response = httpx.get(self.gamma_events_endpoint, params={"slug": slug}, timeout=30.0, proxies=proxies)
            if response.status_code == 200:
                events = response.json()
                if events and len(events) > 0:
                    event = events[0]
                    markets = event.get("markets", [])
                    if markets:
                        market = markets[0]
                        market["_event_slug"] = event.get("slug")
                        market["_event_title"] = event.get("title")
                        market["_event_id"] = event.get("id")
                        
                        # Extract timestamp
                        timestamp = self.extract_timestamp_from_slug(slug)
                        if timestamp:
                            market["_market_start_timestamp"] = timestamp
                            market["_market_start_time"] = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                        
                        return market
        except Exception as e:
            logger.debug(f"Error fetching market by slug {slug}: {e}")
        return None
    
    def get_closed_btc_15m_markets(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 1000,
        max_markets: Optional[int] = None
    ) -> List[Dict]:
        """
        Fetch closed/resolved BTC 15-minute markets from Polymarket.
        
        Args:
            start_date: Start date for filtering markets (UTC)
            end_date: End date for filtering markets (UTC)
            limit: Number of markets to fetch per API call
            max_markets: Maximum total markets to return (None = no limit)
            
        Returns:
            List of market dicts with BTC 15-minute markets
        """
        all_markets = []
        offset = 0
        
        logger.info(f"Fetching closed BTC 15-minute markets...")
        if start_date:
            logger.info(f"  Start date: {start_date}")
        if end_date:
            logger.info(f"  End date: {end_date}")
        
        # Try fetching from events API - try multiple approaches for old markets
        search_configs = [
            # Config 1: Closed but not archived (recently closed)
            {"closed": True, "archived": False, "limit": limit},
            # Config 2: Archived markets (older markets)
            {"archived": True, "limit": limit},
            # Config 3: All events (no filters)
            {"limit": limit},
            # Config 4: Closed and archived (both)
            {"closed": True, "archived": True, "limit": limit},
        ]
        
        for config_idx, base_params in enumerate(search_configs):
            logger.info(f"Trying search config {config_idx + 1}/{len(search_configs)}: {base_params}")
            offset = 0
            config_markets = []
            
            while True:
                params = base_params.copy()
                params["offset"] = offset
                
                try:
                    from agents.utils.proxy_config import get_proxy_dict
                    proxies = get_proxy_dict() if self.proxy else None
                    response = httpx.get(self.gamma_events_endpoint, params=params, timeout=30.0, proxies=proxies)
                    if response.status_code != 200:
                        logger.debug(f"Events API returned {response.status_code} for config {config_idx + 1}")
                        break
                    
                    events = response.json()
                    if not events:
                        break
                    
                    # Process events and extract markets
                    for event in events:
                        slug = (event.get("slug") or "").lower()
                        title = (event.get("title") or "").lower()
                        
                        # Check if it's a BTC 15-minute event
                        # Pattern: btc-updown-15m-{timestamp}
                        is_btc_15m = (
                            "btc-updown-15m" in slug or
                            (slug.startswith("btc-updown-15m-") and slug[-10:].isdigit())  # Ends with timestamp
                        )
                        
                        if is_btc_15m:
                            markets = event.get("markets", [])
                            for market in markets:
                                # Add event metadata
                                market["_event_slug"] = event.get("slug")
                                market["_event_title"] = event.get("title")
                                market["_event_id"] = event.get("id")
                                
                                # Extract timestamp from slug
                                timestamp = self.extract_timestamp_from_slug(slug)
                                if timestamp:
                                    market["_market_start_timestamp"] = timestamp
                                    market["_market_start_time"] = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                                
                                # Filter by date range if specified
                                if start_date or end_date:
                                    market_start = market.get("_market_start_time")
                                    if market_start:
                                        if start_date and market_start < start_date:
                                            continue
                                        if end_date and market_start > end_date:
                                            continue
                                
                                # Avoid duplicates
                                market_id = market.get("id")
                                if market_id and not any(m.get("id") == market_id for m in all_markets):
                                    all_markets.append(market)
                                    config_markets.append(market)
                                    
                                    if max_markets and len(all_markets) >= max_markets:
                                        logger.info(f"Reached max_markets limit: {max_markets}")
                                        return all_markets
                    
                    if len(events) < limit:
                        break
                    
                    offset += limit
                    logger.debug(f"Config {config_idx + 1}: Fetched {len(config_markets)} BTC 15m markets so far...")
                    
                    # Limit how far we search in each config
                    # For closed markets, search more pages since they might be further back
                    max_pages = 20 if base_params.get("closed") else 10
                    if offset > limit * max_pages:
                        logger.debug(f"Config {config_idx + 1}: Reached search limit ({max_pages} pages)")
                        break
                    
                except Exception as e:
                    logger.debug(f"Error fetching from events API (config {config_idx + 1}): {e}")
                    break
            
            logger.info(f"Config {config_idx + 1} found {len(config_markets)} markets")
            
            # If we found markets and have a max limit, we can stop
            if all_markets and max_markets and len(all_markets) >= max_markets:
                break
        
        # Also try CLOB API via client (might have better access with auth, especially for old markets)
        logger.info("Trying CLOB API via client...")
        clob_markets = self._get_clob_client_markets()
        logger.info(f"CLOB client returned {len(clob_markets)} markets")
        for market in clob_markets:
            if self.is_btc_15m_market(market):
                # Try to extract timestamp from slug
                slug = market.get("slug", "")
                timestamp = self.extract_timestamp_from_slug(slug)
                if timestamp:
                    market["_market_start_timestamp"] = timestamp
                    market["_market_start_time"] = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                
                # Filter by date range
                if start_date or end_date:
                    market_start = market.get("_market_start_time")
                    if market_start:
                        if start_date and market_start < start_date:
                            continue
                        if end_date and market_start > end_date:
                            continue
                
                # Avoid duplicates
                market_id = market.get("id")
                if market_id and not any(m.get("id") == market_id for m in all_markets):
                    all_markets.append(market)
                    
                    if max_markets and len(all_markets) >= max_markets:
                        return all_markets
        
        # Also try CLOB API via HTTP (might have better access with auth)
        if len(all_markets) == 0:
            logger.info("Trying CLOB API via HTTP...")
            try:
                params = {"limit": limit}
                headers = self.api_headers if self.api_headers else None
                from agents.utils.proxy_config import get_proxy_dict
                proxies = get_proxy_dict() if self.proxy else None
                response = httpx.get(self.clob_markets_endpoint, params=params, headers=headers, timeout=30.0, proxies=proxies)
                if response.status_code == 200:
                    data = response.json()
                    markets = data.get("data", [])
                    
                    for market in markets:
                        if self.is_btc_15m_market(market):
                            # Try to extract timestamp from slug
                            slug = market.get("slug", "")
                            timestamp = self.extract_timestamp_from_slug(slug)
                            if timestamp:
                                market["_market_start_timestamp"] = timestamp
                                market["_market_start_time"] = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                            
                            # Filter by date range
                            if start_date or end_date:
                                market_start = market.get("_market_start_time")
                                if market_start:
                                    if start_date and market_start < start_date:
                                        continue
                                    if end_date and market_start > end_date:
                                        continue
                            
                            all_markets.append(market)
                            
                            if max_markets and len(all_markets) >= max_markets:
                                return all_markets
            except Exception as e:
                logger.debug(f"Error fetching from CLOB API: {e}")
        
        # Also try markets API directly (as fallback)
        if len(all_markets) == 0:
            logger.info("Trying markets API directly...")
            offset = 0
            while True:
                params = {
                    "closed": True,
                    "archived": False,
                    "limit": limit,
                    "offset": offset,
                }
                
                try:
                    from agents.utils.proxy_config import get_proxy_dict
                    proxies = get_proxy_dict() if self.proxy else None
                    response = httpx.get(self.gamma_markets_endpoint, params=params, timeout=30.0, proxies=proxies)
                    if response.status_code != 200:
                        break
                    
                    markets = response.json()
                    if not markets:
                        break
                    
                    for market in markets:
                        if self.is_btc_15m_market(market):
                            # Try to extract timestamp from slug or question
                            slug = market.get("slug", "")
                            timestamp = self.extract_timestamp_from_slug(slug)
                            if timestamp:
                                market["_market_start_timestamp"] = timestamp
                                market["_market_start_time"] = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                            
                            # Filter by date range
                            if start_date or end_date:
                                market_start = market.get("_market_start_time")
                                if market_start:
                                    if start_date and market_start < start_date:
                                        continue
                                    if end_date and market_start > end_date:
                                        continue
                            
                            all_markets.append(market)
                            
                            if max_markets and len(all_markets) >= max_markets:
                                return all_markets
                    
                    if len(markets) < limit:
                        break
                    
                    offset += limit
                    
                except Exception as e:
                    logger.error(f"Error fetching from markets API: {e}")
                    break
        
        # If we still haven't found markets, try constructing slugs from timestamps
        # BTC 15-minute markets are created every 15 minutes, so we can generate slugs
        if len(all_markets) == 0 and (start_date or end_date):
            logger.info("Trying to fetch markets by constructing slugs from timestamps...")
            # Generate timestamps for 15-minute intervals
            if not end_date:
                end_date = datetime.now(timezone.utc)
            if not start_date:
                start_date = end_date - timedelta(days=7)  # Default to last 7 days
            
            # Round to 15-minute intervals
            current = start_date.replace(minute=(start_date.minute // 15) * 15, second=0, microsecond=0)
            end_ts = end_date.timestamp()
            
            # Limit how far back we search to avoid too many API calls
            max_days_back = 30  # Don't search more than 30 days back
            if start_date < end_date - timedelta(days=max_days_back):
                start_date = end_date - timedelta(days=max_days_back)
                current = start_date.replace(minute=(start_date.minute // 15) * 15, second=0, microsecond=0)
                logger.info(f"Limited search to last {max_days_back} days")
            
            # Calculate total intervals to check
            total_intervals = int((end_date - current).total_seconds() / 60 / 15)
            logger.info(f"Checking {total_intervals} potential 15-minute intervals...")
            
            fetched_count = 0
            checked_count = 0
            batch_size = 50  # Process in batches for progress reporting
            
            while current <= end_date:
                timestamp = int(current.timestamp())
                slug = f"btc-updown-15m-{timestamp}"
                
                try:
                    market = self._fetch_market_by_slug(slug)
                    checked_count += 1
                    
                    if market:
                        # Filter by date range
                        market_start = market.get("_market_start_time")
                        if market_start:
                            if start_date and market_start < start_date:
                                current += timedelta(minutes=15)
                                continue
                            if end_date and market_start > end_date:
                                break
                        
                        # Avoid duplicates
                        market_id = market.get("id")
                        if market_id and not any(m.get("id") == market_id for m in all_markets):
                            all_markets.append(market)
                            fetched_count += 1
                            
                            if max_markets and len(all_markets) >= max_markets:
                                logger.info(f"Reached max_markets limit: {max_markets}")
                                break
                    
                    # Progress reporting
                    if checked_count % batch_size == 0:
                        logger.info(f"Checked {checked_count}/{total_intervals} intervals, found {fetched_count} markets so far...")
                    
                    # Rate limiting: small delay to avoid overwhelming API
                    if checked_count % 100 == 0:
                        import time
                        time.sleep(0.1)  # Brief pause every 100 requests
                        
                except Exception as e:
                    logger.debug(f"Error fetching {slug}: {e}")
                
                current += timedelta(minutes=15)
                
                # Safety limit: don't check more than 2000 intervals at once
                if checked_count >= 2000:
                    logger.warning(f"Reached safety limit of 2000 interval checks")
                    break
            
            if fetched_count > 0:
                logger.info(f"Fetched {fetched_count} markets by constructing slugs (checked {checked_count} intervals)")
        
        # Sort markets by start time (oldest first for sequential processing)
        all_markets.sort(key=lambda m: m.get("_market_start_timestamp", 0) or 0)
        
        logger.info(f"✓ Fetched {len(all_markets)} closed BTC 15-minute markets")
        return all_markets
    
    def get_market_outcome(self, market: Dict) -> Optional[str]:
        """
        Get the resolved outcome of a market.
        
        Args:
            market: Market dict
            
        Returns:
            'up' or 'down' or None if not resolved
        """
        # Check if market is resolved
        if not market.get("closed") and not market.get("resolved"):
            return None
        
        # Try to get outcome from outcomePrices
        outcome_prices = market.get("outcomePrices")
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except:
                pass
        
        if isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
            # If first outcome (up) price is 1.0, market resolved to "up"
            # If second outcome (down) price is 1.0, market resolved to "down"
            if outcome_prices[0] == 1.0:
                return "up"
            elif outcome_prices[1] == 1.0:
                return "down"
        
        # Try to get from outcomes field
        outcomes = market.get("outcomes")
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except:
                pass
        
        if isinstance(outcomes, list) and len(outcomes) >= 2:
            # Check question to determine which outcome is "up"
            question = (market.get("question") or "").lower()
            if "up" in question[:50]:  # Check first part of question
                # First outcome is likely "up"
                if outcome_prices and outcome_prices[0] == 1.0:
                    return "up"
                elif outcome_prices and outcome_prices[1] == 1.0:
                    return "down"
        
        return None
    
    def get_market_start_price(self, market: Dict) -> Optional[float]:
        """
        Extract the starting BTC price from market metadata.
        
        For BTC 15-minute markets, the starting price is often in the question
        or can be fetched from BTC data at the market start time.
        
        Args:
            market: Market dict
            
        Returns:
            Starting BTC price or None
        """
        # Try to extract from question text
        question = market.get("question", "")
        
        # Look for price patterns like "$43,250" or "43250"
        price_match = re.search(r'\$?([\d,]+\.?\d*)', question)
        if price_match:
            try:
                price_str = price_match.group(1).replace(',', '')
                return float(price_str)
            except:
                pass
        
        return None
    
    def enrich_market_with_btc_data(self, market: Dict, btc_fetcher) -> Dict:
        """
        Enrich market dict with BTC price data at market start time.
        
        Args:
            market: Market dict
            btc_fetcher: BTCDataFetcher instance
            
        Returns:
            Enriched market dict
        """
        market_start_time = market.get("_market_start_time")
        if not market_start_time:
            return market
        
        try:
            # Get BTC price at market start time
            start_price = btc_fetcher.get_price_at_time(market_start_time)
            if start_price:
                market["_btc_start_price"] = start_price
            
            # Get BTC price 15 minutes later (market end)
            market_end_time = market_start_time + timedelta(minutes=15)
            end_price = btc_fetcher.get_price_at_time(market_end_time)
            if end_price:
                market["_btc_end_price"] = end_price
                market["_btc_actual_direction"] = "up" if end_price > start_price else "down"
                market["_btc_price_change"] = end_price - start_price
                market["_btc_price_change_pct"] = ((end_price - start_price) / start_price) * 100 if start_price > 0 else 0
            
        except Exception as e:
            logger.warning(f"Error enriching market with BTC data: {e}")
        
        return market

