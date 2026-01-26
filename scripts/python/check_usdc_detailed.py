"""
Detailed USDC balance check with network verification.
"""
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from web3 import Web3

def check_usdc_detailed():
    """Check USDC balance with detailed information."""
    
    # Polygon USDC contract address
    USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    
    # Polygon RPC
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
    print("DETAILED USDC BALANCE CHECK")
    print("=" * 70)
    print()
    print(f"Wallet Address: {wallet_address}")
    print(f"Network: Polygon (Chain ID: 137)")
    print(f"USDC Contract: {USDC_ADDRESS}")
    print()
    
    # Check network connection
    try:
        chain_id = w3.eth.chain_id
        print(f"✓ Connected to network with Chain ID: {chain_id}")
        if chain_id != 137:
            print(f"  ⚠️  WARNING: Expected Chain ID 137 (Polygon), got {chain_id}")
        else:
            print(f"  ✓ Correct network (Polygon)")
    except Exception as e:
        print(f"❌ Error checking network: {e}")
        return
    
    print()
    
    # USDC ABI (minimal - just balanceOf)
    usdc_abi = [{
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    }]
    
    # Get USDC contract
    usdc_contract = w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=usdc_abi)
    
    # Check balance
    try:
        raw_balance = usdc_contract.functions.balanceOf(Web3.to_checksum_address(wallet_address)).call()
        balance_usd = float(raw_balance) / 1e6  # USDC has 6 decimals
        
        print("=" * 70)
        print("USDC BALANCE RESULT")
        print("=" * 70)
        print()
        print(f"Raw balance (wei): {raw_balance}")
        print(f"USDC Balance: ${balance_usd:,.2f}")
        print()
        
        if balance_usd == 0:
            print("⚠️  Balance is $0.00")
            print()
            print("Possible reasons:")
            print("  1. USDC is on Ethereum network, not Polygon")
            print("  2. USDC is in a different wallet")
            print("  3. USDC hasn't been transferred yet")
            print()
            print("To verify in MetaMask:")
            print("  1. Make sure you're on Polygon network")
            print("  2. Check the token contract address matches:")
            print(f"     {USDC_ADDRESS}")
            print("  3. Make sure you're looking at the correct wallet:")
            print(f"     {wallet_address}")
        else:
            print(f"✓ USDC balance found: ${balance_usd:,.2f}")
        
        print()
        print(f"View on Polygonscan:")
        print(f"  https://polygonscan.com/address/{wallet_address}")
        print(f"  https://polygonscan.com/token/{USDC_ADDRESS}?a={wallet_address}")
        
    except Exception as e:
        print(f"❌ Error checking USDC balance: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_usdc_detailed()
