"""
Helper script to verify what address a private key derives.

You can use this to test if a private key matches a specific address.

Usage:
    python scripts/python/verify_private_key.py
    (Then paste your private key when prompted)
"""
import sys
import os
from web3 import Web3

def clean_private_key(raw_key: str) -> str:
    """Clean private key (same logic as Polymarket class)."""
    raw_key = raw_key.strip()
    if "#" in raw_key:
        raw_key = raw_key.split("#")[0].strip()
    if raw_key.startswith("0x"):
        raw_key = raw_key[2:]
    raw_key = raw_key.replace(" ", "").replace("\n", "").replace("\r", "")
    return raw_key

def derive_address_from_key(private_key: str) -> str:
    """Derive address from private key."""
    cleaned = clean_private_key(private_key)
    
    if len(cleaned) != 64:
        raise ValueError(f"Private key must be 64 hex characters, got {len(cleaned)}")
    
    try:
        int(cleaned, 16)  # Validate hex
    except ValueError:
        raise ValueError("Private key contains invalid hex characters")
    
    w3 = Web3()
    account = w3.eth.account.from_key(cleaned)
    return account.address

def main():
    print("=" * 70)
    print("PRIVATE KEY TO ADDRESS VERIFIER")
    print("=" * 70)
    print()
    print("This script will help you verify what address a private key derives.")
    print("⚠️  SECURITY: Your private key will be processed but NOT saved.")
    print()
    
    # First, show what's currently in .env
    from dotenv import load_dotenv
    load_dotenv()
    current_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    
    if current_key:
        try:
            current_addr = derive_address_from_key(current_key)
            print(f"Current .env file derives: {current_addr}")
            print()
        except Exception as e:
            print(f"Error deriving from current .env: {e}")
            print()
    
    print("Enter the private key from MetaMask:")
    print("(The wallet address you see in MetaMask)")
    print()
    print("You can:")
    print("  1. Paste the private key here (it will be hidden)")
    print("  2. Or type 'skip' to exit")
    print()
    
    import getpass
    user_key = getpass.getpass("Private key: ").strip()
    
    if user_key.lower() == 'skip' or not user_key:
        print("Skipped.")
        return
    
    try:
        derived_addr = derive_address_from_key(user_key)
        print()
        print("=" * 70)
        print("RESULT")
        print("=" * 70)
        print()
        print(f"Derived Address: {derived_addr}")
        print()
        print("Compare this with the address you see in MetaMask.")
        print("If they match, use this private key in your .env file.")
        print("If they don't match, double-check that you copied the")
        print("private key correctly from MetaMask.")
        print()
        print(f"Polygonscan: https://polygonscan.com/address/{derived_addr}")
        
    except Exception as e:
        print()
        print(f"❌ Error: {e}")
        print()
        print("Make sure:")
        print("  - The private key is 64 hex characters")
        print("  - No extra spaces or newlines")
        print("  - Can include or exclude '0x' prefix")

if __name__ == "__main__":
    main()
