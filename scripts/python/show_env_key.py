"""
Show what's stored in POLYGON_WALLET_PRIVATE_KEY (masked for security)
and explain what "derives" means.
"""
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from web3 import Web3

def show_env_key():
    """Show the private key value (masked) and what address it derives."""
    
    print("=" * 70)
    print("WHAT IS STORED IN POLYGON_WALLET_PRIVATE_KEY")
    print("=" * 70)
    print()
    
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    
    if not private_key:
        print("❌ POLYGON_WALLET_PRIVATE_KEY is not set in environment")
        print("   Check your .env file")
        return
    
    # Show masked version for security
    print("Private Key Value (MASKED for security):")
    if len(private_key) > 20:
        # Show first 10 and last 10 characters
        masked = private_key[:10] + "..." + "[HIDDEN]" + "..." + private_key[-10:]
    else:
        masked = "[TOO SHORT - INVALID]"
    
    print(f"  {masked}")
    print()
    print(f"  Full length: {len(private_key)} characters")
    print(f"  Starts with '0x': {private_key.startswith('0x')}")
    print(f"  Contains spaces: {' ' in private_key}")
    has_newlines = '\n' in private_key or '\r' in private_key
    print(f"  Contains newlines: {has_newlines}")
    print()
    
    # Clean it (same as Polymarket class)
    raw_key = private_key.strip()
    if "#" in raw_key:
        raw_key = raw_key.split("#")[0].strip()
    if raw_key.startswith("0x"):
        raw_key = raw_key[2:]
    raw_key = raw_key.replace(" ", "").replace("\n", "").replace("\r", "")
    
    print("After cleaning (removing comments, 0x, whitespace):")
    if len(raw_key) > 20:
        cleaned_masked = raw_key[:10] + "..." + "[HIDDEN]" + "..." + raw_key[-10:]
    else:
        cleaned_masked = raw_key
    print(f"  {cleaned_masked}")
    print(f"  Length: {len(raw_key)} characters")
    print()
    
    # Derive the address
    print("=" * 70)
    print("WHAT 'DERIVES' MEANS")
    print("=" * 70)
    print()
    print("'Derives' means: mathematically calculating the wallet address")
    print("from the private key. Every private key corresponds to exactly")
    print("one wallet address (on Ethereum/Polygon networks).")
    print()
    print("The process:")
    print("  1. Private key (64 hex characters)")
    print("  2. → Mathematical calculation")
    print("  3. → Wallet address (42 characters, starts with 0x)")
    print()
    print("This is deterministic - the same private key ALWAYS produces")
    print("the same wallet address.")
    print()
    
    # Actually derive it
    try:
        w3 = Web3()
        account = w3.eth.account.from_key(raw_key)
        derived_address = account.address
        
        print("=" * 70)
        print("DERIVED ADDRESS")
        print("=" * 70)
        print()
        print(f"The private key in your .env file derives this address:")
        print(f"  {derived_address}")
        print()
        print("This is the address the script will use for:")
        print("  - Checking USDC balance")
        print("  - Splitting positions (split_position function)")
        print("  - All on-chain transactions")
        print()
        print("=" * 70)
        print("VERIFICATION")
        print("=" * 70)
        print()
        print("Compare this address with what you see in MetaMask:")
        print(f"  Script address: {derived_address}")
        print("  MetaMask address: [What do you see?]")
        print()
        print("If they match:")
        print("  ✓ Your .env file has the correct private key")
        print("  ✓ The script will use the correct wallet")
        print()
        print("If they DON'T match:")
        print("  ⚠ The private key in .env is for a different wallet")
        print("  ⚠ You need to update POLYGON_WALLET_PRIVATE_KEY")
        print("  ⚠ Export the private key from the MetaMask wallet you see")
        print()
        print(f"Check on Polygonscan: https://polygonscan.com/address/{derived_address}")
        
    except Exception as e:
        print(f"❌ Error deriving address: {e}")
        print("   The private key format might be invalid")

if __name__ == "__main__":
    show_env_key()
