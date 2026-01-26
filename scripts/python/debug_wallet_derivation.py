"""
Debug script to show exactly how the private key is being processed
and what address it derives.

This helps identify if there's a parsing issue.
"""
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from web3 import Web3

def debug_wallet_derivation():
    """Show detailed derivation process."""
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    
    if not private_key:
        print("❌ POLYGON_WALLET_PRIVATE_KEY not found in environment")
        return
    
    print("=" * 70)
    print("PRIVATE KEY PROCESSING DEBUG")
    print("=" * 70)
    print()
    
    # Show raw key (masked for security)
    raw_key = private_key
    if len(raw_key) > 20:
        masked_raw = raw_key[:10] + "..." + raw_key[-10:]
    else:
        masked_raw = "***"
    print(f"1. Raw private key from env: {masked_raw}")
    print(f"   Length: {len(raw_key)} characters")
    print()
    
    # Step by step cleaning (same as Polymarket class)
    step1 = raw_key.strip()
    print(f"2. After strip(): {step1[:10]}...{step1[-10:] if len(step1) > 20 else '***'}")
    
    step2 = step1
    if "#" in step2:
        step2 = step2.split("#")[0].strip()
        print(f"3. After removing comments: {step2[:10]}...{step2[-10:] if len(step2) > 20 else '***'}")
    else:
        print(f"3. No comments found")
    
    step3 = step2
    had_0x = False
    if step3.startswith("0x"):
        had_0x = True
        step3 = step3[2:]
        print(f"4. Removed 0x prefix: {step3[:10]}...{step3[-10:] if len(step3) > 20 else '***'}")
    else:
        print(f"4. No 0x prefix found")
    
    step4 = step3.replace(" ", "").replace("\n", "").replace("\r", "")
    print(f"5. After removing whitespace: {step4[:10]}...{step4[-10:] if len(step4) > 20 else '***'}")
    print(f"   Final length: {len(step4)} characters")
    print()
    
    # Validate length
    if len(step4) != 64:
        print(f"⚠️  WARNING: Private key should be 64 hex characters, got {len(step4)}")
        print(f"   This might cause issues!")
        print()
    
    # Derive address
    print("=" * 70)
    print("ADDRESS DERIVATION")
    print("=" * 70)
    print()
    
    try:
        w3 = Web3()
        
        # Try with the cleaned key (no 0x)
        account1 = w3.eth.account.from_key(step4)
        address1 = account1.address
        print(f"Derived address (from cleaned key): {address1}")
        print()
        
        # Try with 0x prefix added back
        account2 = w3.eth.account.from_key("0x" + step4)
        address2 = account2.address
        print(f"Derived address (with 0x prefix): {address2}")
        print()
        
        if address1 == address2:
            print("✓ Both methods produce the same address (expected)")
        else:
            print("⚠️  Different addresses! This shouldn't happen.")
        
        print()
        print("=" * 70)
        print("COMPARISON")
        print("=" * 70)
        print()
        print(f"Script derived address: {address1}")
        print()
        print("What address do you see in MetaMask?")
        print("(Make sure you're on Polygon network)")
        print()
        print("If they don't match:")
        print("  - The private key in your .env might be for a different wallet")
        print("  - Or there might be extra characters/whitespace in the .env file")
        print()
        print(f"Polygonscan: https://polygonscan.com/address/{address1}")
        
    except Exception as e:
        print(f"❌ Error deriving address: {e}")
        print(f"   This suggests the private key format is invalid")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_wallet_derivation()
