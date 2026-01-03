#!/usr/bin/env python
"""Quick test script to run monitor and see output"""
import asyncio
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
load_dotenv()

from agents.polymarket.auto_monitor import run_auto_monitor

async def main():
    print("Starting auto monitor (will run for 30 seconds)...")
    print(f"DATABASE_URL: {os.getenv('DATABASE_URL', 'NOT SET')[:50]}...")
    print()
    
    try:
        # Run for 30 seconds then stop
        await asyncio.wait_for(
            run_auto_monitor(
                check_interval=30.0,  # Check every 30 seconds for testing
                monitor_15min=True,
                monitor_1hour=True,
                mode="websocket",
            ),
            timeout=30.0
        )
    except asyncio.TimeoutError:
        print("\n30 seconds elapsed - stopping test")
    except KeyboardInterrupt:
        print("\nStopped by user")

if __name__ == "__main__":
    asyncio.run(main())

