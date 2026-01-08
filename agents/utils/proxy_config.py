"""
Centralized Proxy Configuration for VPN/Proxy support.

This module provides a single point of configuration for all proxy/VPN settings.
Once configured, all API calls (Binance, Polymarket, etc.) automatically use the proxy.

Supports:
- Oxylabs Static ISP proxies (Dutch IP for Binance/Polymarket)
- Standard HTTP/HTTPS proxies
- SOCKS5 proxies
- Environment variable configuration (including .env file)

Note: .env file is automatically loaded by other modules (market_fetcher.py, etc.)
      so PROXY_USER, PROXY_PASS, PROXY_PORT from .env will work automatically.
"""
import os
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Global proxy configuration - set once, used everywhere
_global_proxy_url: Optional[str] = None
_global_proxy_dict: Optional[Dict[str, str]] = None


def get_oxylabs_proxy_url(
    username: str,
    password: str,
    port: int = 8001,
    endpoint: str = "isp.oxylabs.io"
) -> str:
    """
    Create Oxylabs Static ISP proxy URL.
    
    Args:
        username: Oxylabs username (use 'user-' prefix for Static ISP)
        password: Oxylabs password
        port: Port number (8001 = first Dutch IP, 8002 = second, etc.)
        endpoint: Proxy endpoint (default: isp.oxylabs.io for Static ISP)
    
    Returns:
        Proxy URL string
    """
    # Ensure username has 'user-' prefix for Static ISP proxies
    if not username.startswith("user-"):
        username = f"user-{username}"
    
    proxy_url = f"http://{username}:{password}@{endpoint}:{port}"
    return proxy_url


def get_proxy_from_env() -> Optional[str]:
    """
    Get proxy URL from environment variables.
    
    Checks in order:
    1. HTTPS_PROXY or HTTP_PROXY (direct proxy URL)
    2. PROXY_USER + PROXY_PASS + PROXY_PORT (Oxylabs format)
    3. OXYLABS_USERNAME + OXYLABS_PASSWORD + OXYLABS_PORT (legacy format)
    
    Returns:
        Proxy URL or None
    """
    # Check standard environment variables first
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    
    # Check for simplified PROXY_USER/PROXY_PASS format
    if not proxy:
        proxy_user = os.environ.get("PROXY_USER")
        proxy_pass = os.environ.get("PROXY_PASS")
        proxy_port = os.environ.get("PROXY_PORT", "8001")
        
        if proxy_user and proxy_pass:
            # Ensure username has 'user-' prefix if not already present
            if not proxy_user.startswith("user-"):
                proxy_user = f"user-{proxy_user}"
            
            proxy = get_oxylabs_proxy_url(proxy_user, proxy_pass, int(proxy_port))
            logger.info(f"Using proxy from PROXY_USER/PROXY_PASS environment variables (port {proxy_port})")
    
    # Check for legacy OXYLABS_* format (backward compatibility)
    if not proxy:
        oxy_user = os.environ.get("OXYLABS_USERNAME")
        oxy_pass = os.environ.get("OXYLABS_PASSWORD")
        oxy_port = os.environ.get("OXYLABS_PORT", "8001")
        
        if oxy_user and oxy_pass:
            proxy = get_oxylabs_proxy_url(oxy_user, oxy_pass, int(oxy_port))
            logger.info(f"Using Oxylabs proxy from environment (port {oxy_port})")
    
    return proxy


def configure_proxy(proxy_url: Optional[str] = None, auto_detect: bool = True):
    """
    Configure proxy globally for all API calls.
    
    This is the SINGLE place to configure VPN/proxy. Once set here,
    all components (BTC fetcher, market fetcher, backtester) will use it automatically.
    
    Args:
        proxy_url: Proxy URL (e.g., "http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001")
                  If None and auto_detect=True, will check environment variables
        auto_detect: If True and proxy_url is None, automatically detect from environment
    
    Usage:
        # Option 1: Set directly
        configure_proxy("http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001")
        
        # Option 2: Auto-detect from environment variables
        configure_proxy()  # Will check OXYLABS_* or HTTPS_PROXY
        
        # Option 3: Disable proxy
        configure_proxy(None, auto_detect=False)
    """
    global _global_proxy_url, _global_proxy_dict
    
    if proxy_url is None and auto_detect:
        proxy_url = get_proxy_from_env()
    
    _global_proxy_url = proxy_url
    
    if proxy_url:
        _global_proxy_dict = {"http://": proxy_url, "https://": proxy_url}
        logger.info(f"✓ Proxy configured globally: {proxy_url.split('@')[1] if '@' in proxy_url else 'configured'}")
    else:
        _global_proxy_dict = None
        logger.info("✓ Proxy disabled (using direct connection)")


def get_proxy() -> Optional[str]:
    """
    Get the currently configured global proxy URL.
    
    Returns:
        Proxy URL or None
    """
    return _global_proxy_url


def get_proxy_dict() -> Optional[Dict[str, str]]:
    """
    Get proxy dictionary for httpx/requests.
    
    Returns:
        Dictionary with 'http://' and 'https://' keys, or None
    """
    global _global_proxy_dict
    
    # Auto-initialize from environment if not yet configured
    if _global_proxy_dict is None and _global_proxy_url is None:
        proxy_url = get_proxy_from_env()
        if proxy_url:
            configure_proxy(proxy_url, auto_detect=False)
    
    return _global_proxy_dict




def verify_proxy_ip(proxy_url: Optional[str] = None) -> Optional[Dict]:
    """
    Verify proxy is working and get IP location.
    
    Args:
        proxy_url: Proxy URL (if None, uses globally configured proxy)
    
    Returns:
        Dict with IP info or None if failed
    """
    """
    Verify proxy is working and get IP location.
    
    Args:
        proxy_url: Proxy URL (if None, checks environment variables)
    
    Returns:
        Dict with IP info or None if failed
    """
    import httpx
    
    # Use provided proxy_url or fall back to global config
    if proxy_url is None:
        proxies = get_proxy_dict()
    else:
        proxies = {"http://": proxy_url, "https://": proxy_url}
    
    if not proxies:
        logger.warning("No proxy configured")
        return None
    
    try:
        # Use Oxylabs location service if using Oxylabs proxy
        if proxy_url and "oxylabs" in proxy_url.lower():
            response = httpx.get(
                "https://ip.oxylabs.io/location",
                proxies=proxies,
                timeout=10
            )
            data = response.json()
            logger.info(f"Proxy IP: {data.get('ip')} | Location: {data.get('city')}, {data.get('country')}")
            return data
        else:
            # Generic IP check
            response = httpx.get(
                "https://api.ipify.org?format=json",
                proxies=proxies,
                timeout=10
            )
            ip_data = response.json()
            ip = ip_data.get("ip")
            
            # Get location
            location_response = httpx.get(
                f"https://ipapi.co/{ip}/json/",
                proxies=proxies,
                timeout=10
            )
            location_data = location_response.json()
            
            logger.info(f"Proxy IP: {ip} | Location: {location_data.get('city')}, {location_data.get('country_name')}")
            return location_data
            
    except Exception as e:
        logger.error(f"Failed to verify proxy: {e}")
        return None

