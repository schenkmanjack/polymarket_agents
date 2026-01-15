"""
Script to verify which wallet address corresponds to your private key.

This helps you confirm that the private key in your .env file matches
the wallet address you're looking at in MetaMask.

Usage:
    python scripts/python/verify_wallet_address.py
"""
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from web3 import Web3

def verify_wallet_address():
    """Derive and display the wallet address from the private key."""
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    
    if not private_key:
        print("❌ POLYGON_WALLET_PRIVATE_KEY not found in environment")
        print("   Check your .env file or environment variables")
        return
    
    # Clean the private key (same logic as Polymarket class)
    raw_key = private_key.strip()
    if "#" in raw_key:
        raw_key = raw_key.split("#")[0].strip()
    if raw_key.startswith("0x"):
        raw_key = raw_key[2:]
    raw_key = raw_key.replace(" ", "").replace("\n", "").replace("\r", "")
    
    # Derive the address
    w3 = Web3()
    account = w3.eth.account.from_key(raw_key)
    wallet_address = account.address
    
    print("=" * 70)
    print("WALLET ADDRESS VERIFICATION")
    print("=" * 70)
    print()
    print(f"Derived Wallet Address: {wallet_address}")
    print()
    print("=" * 70)
    print("VERIFICATION STEPS")
    print("=" * 70)
    print()
    print("1. Open MetaMask")
    print("2. Make sure you're on Polygon network")
    print("3. Check the wallet address shown at the top of MetaMask")
    print(f"4. Compare it with: {wallet_address}")
    print()
    if wallet_address.lower() == wallet_address:
        print("   (Addresses are case-insensitive, so 0xABC... = 0xabc...)")
    print()
    print("If they match:")
    print("  ✓ Your .env file has the correct private key")
    print("  ✓ Check that you're looking at USDC balance (not MATIC)")
    print("  ✓ Make sure you're on Polygon network in MetaMask")
    print()
    print("If they DON'T match:")
    print("  ⚠ The private key in your .env file is for a different wallet")
    print("  ⚠ You need to update POLYGON_WALLET_PRIVATE_KEY in your .env file")
    print("  ⚠ Get the private key from the wallet you're looking at in MetaMask")
    print()
    print("=" * 70)
    print(f"Polygonscan Link: https://polygonscan.com/address/{wallet_address}")
    print("=" * 70)

if __name__ == "__main__":
    verify_wallet_address()
