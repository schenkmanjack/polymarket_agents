"""
Detailed debugging script to identify why the derived address doesn't match
what the user sees in MetaMask.
"""
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from web3 import Web3
import binascii

def debug_private_key_issue():
    """Debug the private key to address derivation."""
    private_key_env = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    
    if not private_key_env:
        print("❌ POLYGON_WALLET_PRIVATE_KEY not found")
        return
    
    print("=" * 70)
    print("DETAILED PRIVATE KEY DEBUGGING")
    print("=" * 70)
    print()
    
    # Show raw value
    print("1. RAW VALUE FROM ENV:")
    print(f"   Length: {len(private_key_env)} characters")
    print(f"   First 20 chars: {private_key_env[:20]}")
    print(f"   Last 20 chars: {private_key_env[-20:]}")
    has_newlines = '\n' in private_key_env or '\r' in private_key_env
    print(f"   Contains newlines: {has_newlines}")
    print(f"   Contains spaces: {' ' in private_key_env}")
    print(f"   Contains 0x: {private_key_env.startswith('0x')}")
    print()
    
    # Step-by-step processing (exactly as Polymarket class does)
    print("2. PROCESSING STEPS (as in Polymarket class):")
    
    step1 = private_key_env.strip()
    print(f"   Step 1 (strip): length={len(step1)}")
    
    step2 = step1
    if "#" in step2:
        step2 = step2.split("#")[0].strip()
        print(f"   Step 2 (remove comments): length={len(step2)}")
    else:
        print(f"   Step 2 (no comments found)")
    
    step3 = step2
    if step3.startswith("0x"):
        step3 = step3[2:]
        print(f"   Step 3 (remove 0x): length={len(step3)}")
    else:
        print(f"   Step 3 (no 0x prefix)")
    
    step4 = step3.replace(" ", "").replace("\n", "").replace("\r", "")
    print(f"   Step 4 (remove whitespace): length={len(step4)}")
    print(f"   Final cleaned key (first 10): {step4[:10]}...")
    print(f"   Final cleaned key (last 10): ...{step4[-10:]}")
    print()
    
    # Validate it's hex
    try:
        int(step4, 16)
        print("   ✓ Valid hex string")
    except ValueError:
        print("   ❌ NOT a valid hex string!")
        print(f"   Contains invalid characters")
        return
    
    # Check length
    if len(step4) != 64:
        print(f"   ⚠️  WARNING: Expected 64 hex chars, got {len(step4)}")
        if len(step4) < 64:
            print(f"   Too short - might be missing characters")
        else:
            print(f"   Too long - might have extra characters")
    else:
        print("   ✓ Correct length (64 hex characters)")
    print()
    
    # Try different derivation methods
    print("3. ADDRESS DERIVATION ATTEMPTS:")
    print()
    
    w3 = Web3()
    
    # Method 1: Direct from cleaned key
    try:
        account1 = w3.eth.account.from_key(step4)
        addr1 = account1.address
        print(f"   Method 1 (cleaned key, no 0x): {addr1}")
    except Exception as e:
        print(f"   Method 1 FAILED: {e}")
        addr1 = None
    
    # Method 2: With 0x prefix
    try:
        account2 = w3.eth.account.from_key("0x" + step4)
        addr2 = account2.address
        print(f"   Method 2 (with 0x prefix): {addr2}")
    except Exception as e:
        print(f"   Method 2 FAILED: {e}")
        addr2 = None
    
    # Method 3: Try using Account.create() to see if we can reverse-engineer
    # Actually, let's try to convert to bytes and back
    try:
        # Convert hex string to bytes
        key_bytes = bytes.fromhex(step4)
        if len(key_bytes) == 32:
            account3 = w3.eth.account.from_key(key_bytes)
            addr3 = account3.address
            print(f"   Method 3 (from bytes): {addr3}")
        else:
            print(f"   Method 3 SKIPPED: key_bytes length is {len(key_bytes)}, expected 32")
            addr3 = None
    except Exception as e:
        print(f"   Method 3 FAILED: {e}")
        addr3 = None
    
    print()
    print("=" * 70)
    print("COMPARISON")
    print("=" * 70)
    print()
    
    if addr1:
        print(f"Script derived address: {addr1}")
        print()
        print("What address do you see in MetaMask?")
        print("(Make sure you're on Polygon network)")
        print()
        print("If they don't match, possible causes:")
        print("  1. The private key in .env is for a different wallet")
        print("  2. There are hidden characters in the .env file")
        print("  3. The private key format is incorrect")
        print()
        print(f"Check on Polygonscan: https://polygonscan.com/address/{addr1}")
        print()
        print("To fix:")
        print("  1. Export private key from MetaMask (the wallet you see)")
        print("  2. Copy it EXACTLY (no extra spaces, newlines, or comments)")
        print("  3. Update POLYGON_WALLET_PRIVATE_KEY in .env file")
        print("  4. Make sure it's just the hex string (64 chars, with or without 0x)")

if __name__ == "__main__":
    debug_private_key_issue()
