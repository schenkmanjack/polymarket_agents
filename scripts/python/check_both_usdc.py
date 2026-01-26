"""
Check both USDC.e (bridged) and Native USDC balances on Polygon.
"""
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from web3 import Web3

def check_both_usdc():
    """Check both USDC.e and Native USDC balances."""
    
    # USDC contract addresses on Polygon
    USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # Bridged USDC (USDC.e)
    USDC_NATIVE_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # Native USDC
    
    POLYGON_RPC = "https://polygon-rpc.com"
    
    # Get private key
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    if not private_key:
        print("❌ POLYGON_WALLET_PRIVATE_KEY not set")
        return
    
    # Clean private key
    raw_key = private_key.strip()
    if "#" in raw_key:
        raw_key = raw_key.split("#")[0].strip()
    if raw_key.startswith("0x"):
        raw_key = raw_key[2:]
    raw_key = raw_key.replace(" ", "").replace("\n", "").replace("\r", "")
    
    # Connect to Polygon
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
    
    # Derive address
    account = w3.eth.account.from_key(raw_key)
    wallet_address = account.address
    
    print("=" * 70)
    print("CHECKING BOTH USDC TYPES ON POLYGON")
    print("=" * 70)
    print()
    print(f"Wallet Address: {wallet_address}")
    print(f"Network: Polygon (Chain ID: 137)")
    print()
    
    # USDC ABI (minimal - just balanceOf)
    usdc_abi = [{
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    }]
    
    results = {}
    
    # Check USDC.e (bridged)
    print("1. Checking USDC.e (Bridged USDC):")
    print(f"   Contract: {USDC_E_ADDRESS}")
    try:
        usdc_e_contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_E_ADDRESS), 
            abi=usdc_abi
        )
        raw_balance_e = usdc_e_contract.functions.balanceOf(
            Web3.to_checksum_address(wallet_address)
        ).call()
        balance_e = float(raw_balance_e) / 1e6
        results['USDC.e'] = balance_e
        print(f"   Balance: ${balance_e:,.2f}")
        if balance_e > 0:
            print(f"   ✓ Found USDC.e balance!")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        results['USDC.e'] = None
    
    print()
    
    # Check Native USDC
    print("2. Checking Native USDC:")
    print(f"   Contract: {USDC_NATIVE_ADDRESS}")
    try:
        usdc_native_contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_NATIVE_ADDRESS), 
            abi=usdc_abi
        )
        raw_balance_native = usdc_native_contract.functions.balanceOf(
            Web3.to_checksum_address(wallet_address)
        ).call()
        balance_native = float(raw_balance_native) / 1e6
        results['Native USDC'] = balance_native
        print(f"   Balance: ${balance_native:,.2f}")
        if balance_native > 0:
            print(f"   ✓ Found Native USDC balance!")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        results['Native USDC'] = None
    
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print()
    
    total = 0
    for token_type, balance in results.items():
        if balance is not None and balance > 0:
            print(f"✓ {token_type}: ${balance:,.2f}")
            total += balance
    
    if total == 0:
        print("⚠️  No USDC balance found in either token")
        print()
        print("The script currently uses USDC.e for split_position:")
        print(f"  {USDC_E_ADDRESS}")
        print()
        print("If you have Native USDC, you may need to:")
        print("  1. Swap Native USDC to USDC.e, OR")
        print("  2. Update the script to use Native USDC")
    else:
        print(f"\nTotal USDC: ${total:,.2f}")
        print()
        print("Note: The script currently checks/uses USDC.e:")
        print(f"  {USDC_E_ADDRESS}")
        if results.get('Native USDC', 0) > 0 and results.get('USDC.e', 0) == 0:
            print()
            print("⚠️  You have Native USDC but the script uses USDC.e")
            print("   You may need to swap or update the script")
    
    print()
    print("Polygonscan links:")
    print(f"  USDC.e: https://polygonscan.com/token/{USDC_E_ADDRESS}?a={wallet_address}")
    print(f"  Native: https://polygonscan.com/token/{USDC_NATIVE_ADDRESS}?a={wallet_address}")

if __name__ == "__main__":
    check_both_usdc()
