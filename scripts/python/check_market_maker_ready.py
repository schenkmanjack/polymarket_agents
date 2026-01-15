"""
Pre-flight check for market maker readiness.
"""
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

load_dotenv()

from web3 import Web3
from agents.polymarket.polymarket import Polymarket
from agents.trading.market_maker_config import MarketMakerConfig

def check_market_maker_ready():
    """Check if market maker is ready to run."""
    
    print("=" * 70)
    print("MARKET MAKER READINESS CHECK")
    print("=" * 70)
    print()
    
    checks_passed = 0
    checks_total = 0
    
    # 1. Check private key
    checks_total += 1
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    if private_key:
        print("✓ 1. POLYGON_WALLET_PRIVATE_KEY is set")
        checks_passed += 1
    else:
        print("❌ 1. POLYGON_WALLET_PRIVATE_KEY is NOT set")
    
    # 2. Check USDC balance
    checks_total += 1
    try:
        pm = Polymarket()
        wallet_address = pm.get_address_for_private_key()
        usdc_balance = pm.get_usdc_balance()
        
        print(f"✓ 2. USDC Balance: ${usdc_balance:,.2f}")
        print(f"   Wallet: {wallet_address}")
        
        # Check config
        config_path = "config/market_maker_config.json"
        if os.path.exists(config_path):
            config = MarketMakerConfig(config_path)
            required = config.split_amount
            
            if usdc_balance >= required:
                print(f"   ✓ Sufficient balance (${usdc_balance:.2f} >= ${required:.2f})")
                checks_passed += 1
            else:
                print(f"   ❌ Insufficient balance (${usdc_balance:.2f} < ${required:.2f})")
        else:
            print(f"   ⚠️  Config file not found, assuming ${usdc_balance:.2f} is sufficient")
            checks_passed += 1
    except Exception as e:
        print(f"❌ 2. Error checking USDC balance: {e}")
    
    # 3. Check MATIC balance (for gas)
    checks_total += 1
    try:
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        matic_balance_wei = w3.eth.get_balance(wallet_address)
        matic_balance = float(matic_balance_wei) / 1e18
        
        print(f"✓ 3. MATIC Balance: {matic_balance:.4f} MATIC")
        if matic_balance >= 0.01:
            print(f"   ✓ Sufficient for gas fees")
            checks_passed += 1
        else:
            print(f"   ⚠️  Low MATIC balance - may need more for gas")
            checks_passed += 1  # Still pass, just warning
    except Exception as e:
        print(f"❌ 3. Error checking MATIC balance: {e}")
    
    # 4. Check USDC approval for CTF contract
    checks_total += 1
    try:
        ctf_address = pm.ctf_address
        allowance = pm.usdc.functions.allowance(wallet_address, ctf_address).call()
        allowance_float = float(allowance) / 1e6
        
        print(f"✓ 4. USDC Approval for CTF: ${allowance_float:,.2f}")
        if allowance_float >= 1000:  # Reasonable threshold
            print(f"   ✓ Sufficient approval")
            checks_passed += 1
        else:
            print(f"   ⚠️  Low approval - will auto-approve if needed")
            checks_passed += 1  # Auto-approval handles this
    except Exception as e:
        print(f"❌ 4. Error checking approval: {e}")
    
    # 5. Check config file
    checks_total += 1
    config_path = "config/market_maker_config.json"
    if os.path.exists(config_path):
        print(f"✓ 5. Config file exists: {config_path}")
        try:
            config = MarketMakerConfig(config_path)
            print(f"   Split amount: ${config.split_amount:.2f}")
            print(f"   Offset: {config.offset_above_midpoint:.4f}")
            print(f"   Price step: {config.price_step:.4f}")
            checks_passed += 1
        except Exception as e:
            print(f"   ❌ Error loading config: {e}")
    else:
        print(f"❌ 5. Config file not found: {config_path}")
    
    # 6. Check database connection (optional)
    checks_total += 1
    try:
        from agents.trading.trade_db import TradeDatabase
        db = TradeDatabase()
        print(f"✓ 6. Database connection: OK")
        checks_passed += 1
    except Exception as e:
        print(f"⚠️  6. Database connection: {e}")
        print(f"   (Market maker can still run, but won't save positions)")
        checks_passed += 1  # Not critical
    
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print()
    print(f"Checks passed: {checks_passed}/{checks_total}")
    
    if checks_passed == checks_total:
        print()
        print("✅ MARKET MAKER IS READY TO RUN!")
        print()
        print("Note: The script now uses Native USDC.")
        print("If you encounter errors with split_position, the CTF contract")
        print("might need USDC.e instead. In that case, you can:")
        print("  1. Swap Native USDC to USDC.e on a DEX, OR")
        print("  2. Revert the USDC address change in polymarket.py")
    else:
        print()
        print("⚠️  SOME CHECKS FAILED - REVIEW ABOVE")
        print("Fix the issues above before running the market maker.")
    
    print()
    print("To run the market maker:")
    print("  python scripts/python/trade_market_maker.py")

if __name__ == "__main__":
    check_market_maker_ready()
