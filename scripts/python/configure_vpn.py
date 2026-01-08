#!/usr/bin/env python3
"""
Simple VPN/Proxy Configuration Script

This is the SINGLE place to configure VPN/proxy for all API calls.
Once configured here, all components automatically use it.

Usage:
    # Configure using Oxylabs credentials
    python scripts/python/configure_vpn.py --oxylabs --username YOUR_USERNAME --password YOUR_PASSWORD --port 8001
    
    # Configure using proxy URL directly
    python scripts/python/configure_vpn.py --proxy "http://user-USERNAME:PASSWORD@isp.oxylabs.io:8001"
    
    # Auto-detect from environment variables
    python scripts/python/configure_vpn.py --auto
    
    # Disable proxy
    python scripts/python/configure_vpn.py --disable
"""
import argparse
import sys
from agents.utils.proxy_config import (
    configure_proxy,
    get_oxylabs_proxy_url,
    verify_proxy_ip,
    get_proxy
)


def main():
    parser = argparse.ArgumentParser(
        description="Configure VPN/Proxy for all API calls (Binance, Polymarket, etc.)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Configure Oxylabs proxy
  python configure_vpn.py --oxylabs --username myuser --password mypass --port 8001
  
  # Configure from environment variables
  export OXYLABS_USERNAME=myuser
  export OXYLABS_PASSWORD=mypass
  export OXYLABS_PORT=8001
  python configure_vpn.py --auto
  
  # Configure direct proxy URL
  python configure_vpn.py --proxy "http://user-user:pass@isp.oxylabs.io:8001"
  
  # Disable proxy
  python configure_vpn.py --disable
        """
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--oxylabs", action="store_true", help="Configure Oxylabs proxy")
    group.add_argument("--proxy", type=str, help="Proxy URL (e.g., http://user:pass@proxy.com:8080)")
    group.add_argument("--auto", action="store_true", help="Auto-detect from environment variables")
    group.add_argument("--disable", action="store_true", help="Disable proxy (use direct connection)")
    
    parser.add_argument("--username", type=str, help="Oxylabs username (required with --oxylabs)")
    parser.add_argument("--password", type=str, help="Oxylabs password (required with --oxylabs)")
    parser.add_argument("--port", type=int, default=8001, help="Oxylabs port (default: 8001)")
    parser.add_argument("--verify", action="store_true", help="Verify proxy connection after configuration")
    
    args = parser.parse_args()
    
    # Determine proxy URL
    proxy_url = None
    
    if args.oxylabs:
        if not args.username or not args.password:
            parser.error("--username and --password are required with --oxylabs")
        proxy_url = get_oxylabs_proxy_url(args.username, args.password, args.port)
        print(f"✓ Configured Oxylabs proxy (port {args.port})")
        
    elif args.proxy:
        proxy_url = args.proxy
        print(f"✓ Configured proxy URL")
        
    elif args.auto:
        configure_proxy(None, auto_detect=True)
        proxy_url = get_proxy()
        if proxy_url:
            print(f"✓ Auto-detected proxy from environment")
        else:
            print("⚠ No proxy found in environment variables")
            print("\nSet one of:")
            print("  # Simple format (recommended):")
            print("  export PROXY_USER=your_username")
            print("  export PROXY_PASS=your_password")
            print("  export PROXY_PORT=8001  # optional, defaults to 8001")
            print("\n  # Or direct proxy URL:")
            print("  export HTTPS_PROXY=http://user:pass@proxy.com:8080")
            print("\n  # Or legacy Oxylabs format:")
            print("  export OXYLABS_USERNAME=your_username")
            print("  export OXYLABS_PASSWORD=your_password")
            print("  export OXYLABS_PORT=8001")
            return 1
        
    elif args.disable:
        configure_proxy(None, auto_detect=False)
        print("✓ Proxy disabled - using direct connection")
        return 0
    
    # Configure proxy globally
    if proxy_url:
        configure_proxy(proxy_url, auto_detect=False)
    
    # Verify if requested
    if args.verify and proxy_url:
        print("\nVerifying proxy connection...")
        ip_info = verify_proxy_ip(proxy_url)
        
        if ip_info:
            print("✓ Proxy is working!")
            print(f"  IP: {ip_info.get('ip', 'N/A')}")
            print(f"  Location: {ip_info.get('city', 'N/A')}, {ip_info.get('country', 'N/A')}")
        else:
            print("❌ Proxy verification failed")
            return 1
    
    print("\n" + "="*60)
    print("✓ VPN/Proxy configured successfully!")
    print("="*60)
    print("\nAll API calls (Binance, Polymarket, etc.) will now use this proxy.")
    print("No need to configure proxy in individual components.\n")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

