"""
Market maker module for BTC 1-hour markets.

Uses split position strategy: split USDC into YES + NO shares, then place sell orders
slightly above midpoint. Adjusts prices when one side fills.
"""
import asyncio
import logging
import os
import uuid
import httpx
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Set, Callable, List

from agents.trading.market_maker_config import MarketMakerConfig
from agents.trading.trade_db import TradeDatabase, RealMarketMakerPosition
from agents.trading.orderbook_helper import (
    fetch_orderbook,
    calculate_midpoint,
    get_highest_bid,
    get_lowest_ask,
    set_websocket_service,
)
from agents.trading.utils.order_status_helpers import (
    parse_order_status,
    is_order_filled,
    is_order_cancelled,
    is_order_partial_fill,
)
from agents.polymarket.polymarket import Polymarket
from agents.polymarket.btc_market_detector import (
    get_latest_btc_1h_market_proactive,
    is_market_active,
    get_market_by_slug,
)
from agents.polymarket.market_finder import get_token_ids_from_market
from agents.trading.utils.market_time_helpers import get_minutes_until_resolution

logger = logging.getLogger(__name__)


@dataclass
class MarketMakerPosition:
    """Tracks a single market maker position in memory."""
    market_slug: str
    market: Dict
    condition_id: str
    yes_token_id: str
    no_token_id: str
    
    # Split information
    split_amount: float
    yes_shares: float
    no_shares: float
    split_transaction_hash: Optional[str] = None
    
    # Orders
    yes_order_id: Optional[str] = None
    no_order_id: Optional[str] = None
    yes_order_price: Optional[float] = None
    no_order_price: Optional[float] = None
    
    # Status tracking
    yes_filled: bool = False
    no_filled: bool = False
    yes_fill_time: Optional[datetime] = None
    no_fill_time: Optional[datetime] = None
    
    # Adjustment tracking
    adjustment_count: int = 0
    max_adjustments: int = 10
    last_adjustment_time: Optional[datetime] = None  # When we last adjusted price
    
    # Neither-fills tracking
    neither_fills_iteration_count: int = 0  # How many times we've adjusted when neither fills
    orders_placed_time: Optional[datetime] = None  # When orders were first placed
    merged_waiting_resplit: bool = False  # True if we've merged and are waiting to re-split
    merged_at: Optional[datetime] = None  # When we merged (cancelled both orders)
    
    # Database record ID
    db_position_id: Optional[int] = None


class MarketMaker:
    """Market maker for BTC 1-hour markets using split position strategy."""
    
    def __init__(self, config_path: str, proxy_url: Optional[str] = None):
        """Initialize market maker with config."""
        self.config = MarketMakerConfig(config_path)
        self.db = TradeDatabase()
        self.pm = Polymarket()
        
        # Generate deployment ID
        self.deployment_id = str(uuid.uuid4())
        logger.info(f"Deployment ID: {self.deployment_id}")
        
        # Track active positions
        self.active_positions: Dict[str, MarketMakerPosition] = {}  # market_slug -> position
        
        # Track markets we're monitoring
        self.monitored_markets: Set[str] = set()  # market_slugs
        
        # Track orderbook prices near resolution for determining winner
        # Format: {market_slug: {"yes_highest_bid": float, "no_highest_bid": float, "timestamp": datetime}}
        self.last_orderbook_prices: Dict[str, Dict] = {}
        
        # Initialize WebSocket orderbook service if enabled
        self.websocket_service = None
        if self.config.use_websocket_orderbook:
            try:
                from agents.trading.websocket_orderbook_service import WebSocketOrderbookService
                self.websocket_service = WebSocketOrderbookService(
                    proxy_url=proxy_url,
                    health_check_timeout=self.config.websocket_health_check_timeout,
                    reconnect_delay=self.config.websocket_reconnect_delay,
                )
                logger.info("✓ WebSocket orderbook service initialized")
            except ImportError as e:
                logger.warning(f"⚠️ WebSocket orderbook service not available: {e}. Falling back to HTTP polling.")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize WebSocket orderbook service: {e}. Falling back to HTTP polling.")
        
        # Set WebSocket service in orderbook_helper for fallback logic
        if self.websocket_service:
            set_websocket_service(self.websocket_service)
        
        # Initialize WebSocket order status service if enabled
        self.websocket_order_status_service = None
        if self.config.use_websocket_order_status:
            try:
                from agents.trading.websocket_order_status_service import WebSocketOrderStatusService
                
                # Get API credentials from Polymarket client
                if not hasattr(self.pm, 'credentials') or not self.pm.credentials:
                    logger.warning("⚠️ No API credentials available for WebSocket order status. Falling back to HTTP polling.")
                else:
                    self.websocket_order_status_service = WebSocketOrderStatusService(
                        api_key=self.pm.credentials.api_key,
                        api_secret=self.pm.credentials.api_secret,
                        api_passphrase=self.pm.credentials.api_passphrase,
                        proxy_url=proxy_url,
                        health_check_timeout=self.config.websocket_order_status_health_check_timeout,
                        reconnect_delay=self.config.websocket_order_status_reconnect_delay,
                        on_order_update=self._handle_websocket_order_update,
                        on_trade_update=self._handle_websocket_trade_update,
                    )
                    logger.info("✓ WebSocket order status service initialized")
            except ImportError as e:
                logger.warning(f"⚠️ WebSocket order status service not available: {e}. Falling back to HTTP polling.")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize WebSocket order status service: {e}. Falling back to HTTP polling.")
        
        # Track WebSocket fallback state
        self._websocket_fallback_logged = False
        
        self.running = False
    
    async def scan_wallet_and_redeem(self, limit: int = 10):
        """
        Scan Polygon wallet directly for conditional tokens from database-tracked positions.
        Checks wallet balances for positions in the database and redeems winning shares.
        
        Args:
            limit: Maximum number of recent positions to check (default: 10)
        """
        try:
            logger.info("=" * 80)
            logger.info(f"SCANNING POLYGON WALLET FOR REDEEMABLE CONDITIONAL TOKENS (past {limit} positions)")
            logger.info("=" * 80)
            
            direct_wallet = self.pm.get_address_for_private_key()
            logger.info(f"Wallet address: {direct_wallet}")
            logger.info("")
            
            # Get recent positions from database (ordered by ID, most recent first)
            # Note: Using ID as proxy for creation time since it auto-increments
            session = self.db.SessionLocal()
            try:
                all_positions = session.query(RealMarketMakerPosition).order_by(
                    RealMarketMakerPosition.id.desc()
                ).limit(limit).all()
                logger.info(f"Found {len(all_positions)} recent positions in database to check")
                
                if len(all_positions) == 0:
                    logger.info("No positions found in database - nothing to redeem")
                else:
                    logger.info("Checking each position for redeemable shares...")
                logger.info("")
                
                redeemable_count = 0
                skipped_no_shares = 0
                skipped_not_resolved = 0
                skipped_no_winning_side = 0
                
                for db_pos in all_positions:
                    try:
                        # Check wallet balances
                        yes_balance = self.pm.get_conditional_token_balance(
                            db_pos.yes_token_id,
                            wallet_address=direct_wallet
                        )
                        no_balance = self.pm.get_conditional_token_balance(
                            db_pos.no_token_id,
                            wallet_address=direct_wallet
                        )
                        
                        if yes_balance is None or no_balance is None:
                            logger.warning(f"Could not check balances for {db_pos.market_slug}")
                            continue
                        
                        logger.info(f"📊 {db_pos.market_slug}:")
                        logger.info(f"   YES shares: {yes_balance:.2f}, NO shares: {no_balance:.2f}")
                        logger.info(f"   Status: {db_pos.position_status}, Winning side: {db_pos.winning_side}")
                        
                        if yes_balance == 0 and no_balance == 0:
                            logger.info(f"   ✓ No shares remaining (already redeemed)")
                            skipped_no_shares += 1
                            logger.info("")
                            continue
                        
                        # Create position object for redemption/merging
                        position = MarketMakerPosition(
                            market_slug=db_pos.market_slug,
                            market={},  # Not needed for redemption
                            condition_id=db_pos.condition_id,
                            yes_token_id=db_pos.yes_token_id,
                            no_token_id=db_pos.no_token_id,
                            split_amount=db_pos.split_amount,
                            yes_shares=yes_balance,
                            no_shares=no_balance,
                            yes_filled=(yes_balance == 0),
                            no_filled=(no_balance == 0),
                        )
                        
                        # Process all positions - merge/redeem based on status
                        # IMPORTANT: Only merge/resolve if position is marked as resolved
                        # We don't merge active positions because the purpose is to sell each side separately
                        if db_pos.position_status == "resolved":
                            # Case 1: Both YES and NO shares exist - merge equal amounts back to USDC
                            if yes_balance > 0 and no_balance > 0:
                                merge_amount = min(yes_balance, no_balance)
                                if abs(yes_balance - no_balance) < 0.01:  # Essentially equal
                                    logger.info(
                                        f"   💰 Both sides exist in equal amounts ({merge_amount:.2f}). "
                                        f"Merging back to ${merge_amount:.2f} USDC..."
                                    )
                                    loop = asyncio.get_event_loop()
                                    merge_result = await loop.run_in_executor(
                                        None,
                                        self.pm.merge_positions,
                                        db_pos.condition_id,
                                        merge_amount
                                    )
                                    if merge_result:
                                        logger.info(f"   ✅ Successfully merged {merge_amount:.2f} YES + NO → ${merge_amount:.2f} USDC")
                                        redeemable_count += 1
                                        # Update remaining balances
                                        yes_balance -= merge_amount
                                        no_balance -= merge_amount
                                    else:
                                        logger.error(f"   ❌ Failed to merge positions")
                                else:
                                    logger.info(
                                        f"   💰 Unequal amounts (YES: {yes_balance:.2f}, NO: {no_balance:.2f}). "
                                        f"Merging {merge_amount:.2f} equal amounts..."
                                    )
                                    loop = asyncio.get_event_loop()
                                    merge_result = await loop.run_in_executor(
                                        None,
                                        self.pm.merge_positions,
                                        db_pos.condition_id,
                                        merge_amount
                                    )
                                    if merge_result:
                                        logger.info(f"   ✅ Successfully merged {merge_amount:.2f} YES + NO → ${merge_amount:.2f} USDC")
                                        redeemable_count += 1
                                        yes_balance -= merge_amount
                                        no_balance -= merge_amount
                            
                            # Case 2: Position is resolved and we know winning side - redeem winning shares
                            if db_pos.winning_side:
                                # Update position with remaining balances after merge
                                position.yes_shares = yes_balance
                                position.no_shares = no_balance
                                position.yes_filled = (yes_balance == 0)
                                position.no_filled = (no_balance == 0)
                                
                                logger.info(f"   Market is RESOLVED - redeeming winning shares...")
                                await self._redeem_winning_shares(position, db_pos.winning_side)
                                redeemable_count += 1
                            else:
                                logger.info(f"   Market resolved but winning_side not recorded - skipping redemption")
                                skipped_no_winning_side += 1
                        else:
                            # Position not resolved - do NOT merge (market making is still active)
                            # The purpose of splitting is to sell each side separately
                            logger.info(f"   Market status: '{db_pos.position_status}' - position is still active")
                            logger.info(f"   (YES: {yes_balance:.2f}, NO: {no_balance:.2f}) - NOT merging (market making in progress)")
                            logger.info(f"   These shares are being used for market making - will be redeemed after market resolves")
                            skipped_not_resolved += 1
                        
                        logger.info("")
                        
                    except Exception as e:
                        logger.error(f"Error processing {db_pos.market_slug}: {e}", exc_info=True)
                        continue
                
            finally:
                session.close()
            
            logger.info("=" * 80)
            logger.info(f"SCAN COMPLETE:")
            logger.info(f"  - Positions checked: {len(all_positions)}")
            logger.info(f"  - Positions with redeemable shares: {redeemable_count}")
            logger.info(f"  - Skipped (no shares): {skipped_no_shares}")
            logger.info(f"  - Skipped (not resolved): {skipped_not_resolved}")
            logger.info(f"  - Skipped (no winning_side): {skipped_no_winning_side}")
            logger.info("=" * 80)
            
        except Exception as e:
            logger.error(f"Error scanning wallet: {e}", exc_info=True)
    
    async def _merge_resolved_positions_for_capital(self, limit: int = 20):
        """
        Merge positions with equal YES/NO shares to free up USDC capital.
        Called once on startup/deployment to merge the most recent positions.
        
        Merges ALL positions that have equal YES/NO shares, regardless of status.
        
        Args:
            limit: Maximum number of recent positions to check (default: 20)
        """
        try:
            logger.info("=" * 80)
            logger.info("MERGING RECENT POSITIONS TO FREE UP USDC CAPITAL (STARTUP)")
            logger.info("=" * 80)
            
            direct_wallet = self.pm.get_address_for_private_key()
            
            # Get ALL recent positions from database (regardless of status)
            session = self.db.SessionLocal()
            try:
                all_positions = session.query(RealMarketMakerPosition).order_by(
                    RealMarketMakerPosition.id.desc()
                ).limit(limit).all()
                
                logger.info(f"Found {len(all_positions)} recent positions to check for merging")
                
                merged_count = 0
                total_usdc_freed = 0.0
                
                for db_pos in all_positions:
                    try:
                        # Check wallet balances
                        yes_balance = self.pm.get_conditional_token_balance(
                            db_pos.yes_token_id,
                            wallet_address=direct_wallet
                        )
                        no_balance = self.pm.get_conditional_token_balance(
                            db_pos.no_token_id,
                            wallet_address=direct_wallet
                        )
                        
                        if yes_balance is None or no_balance is None:
                            continue
                        
                        if yes_balance == 0 and no_balance == 0:
                            continue
                        
                        # Merge if position has both YES and NO shares (regardless of status)
                        if yes_balance > 0 and no_balance > 0:
                            merge_amount = min(yes_balance, no_balance)
                            logger.info(
                                f"💰 Merging {db_pos.market_slug} "
                                f"(status: {db_pos.position_status}): "
                                f"{merge_amount:.2f} YES + {merge_amount:.2f} NO → ${merge_amount:.2f} USDC"
                            )
                            
                            loop = asyncio.get_event_loop()
                            merge_result = await loop.run_in_executor(
                                None,
                                self.pm.merge_positions,
                                db_pos.condition_id,
                                merge_amount
                            )
                            
                            if merge_result:
                                logger.info(f"   ✅ Successfully merged → ${merge_amount:.2f} USDC freed")
                                merged_count += 1
                                total_usdc_freed += merge_amount
                            else:
                                logger.warning(f"   ❌ Failed to merge")
                        else:
                            logger.info(
                                f"   ⏭️ Skipping {db_pos.market_slug}: "
                                f"YES shares={yes_balance:.2f}, NO shares={no_balance:.2f} "
                                f"(no equal shares to merge)"
                            )
                        
                    except Exception as e:
                        logger.error(f"Error merging {db_pos.market_slug}: {e}", exc_info=True)
                        continue
                
                logger.info("=" * 80)
                logger.info(f"MERGE COMPLETE: Merged {merged_count} positions, freed ${total_usdc_freed:.2f} USDC")
                logger.info("=" * 80)
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error merging resolved positions for capital: {e}", exc_info=True)
    
    async def _check_polygonscan_for_merge_opportunities(self):
        """
        Query Polygonscan API for splitPosition transactions not logged to database.
        Check wallet balances for those condition IDs and merge if equal YES/NO shares exist.
        """
        try:
            logger.info("=" * 80)
            logger.info("CHECKING POLYGONSCAN FOR TRANSACTIONS NOT LOGGED TO DATABASE")
            logger.info("=" * 80)
            
            direct_wallet = self.pm.get_address_for_private_key()
            ctf_address = self.pm.ctf_address
            
            logger.info(f"Wallet address: {direct_wallet}")
            logger.info(f"CTF contract: {ctf_address}")
            logger.info("")
            
            # Query Polygonscan API for transactions to CTF contract
            # API: https://api.polygonscan.com/api?module=account&action=txlist&address=...
            polygonscan_api_key = os.getenv("POLYGONSCAN_API_KEY", "")
            if not polygonscan_api_key:
                logger.warning("⚠️ POLYGONSCAN_API_KEY not set - skipping Polygonscan check")
                logger.warning("   Set POLYGONSCAN_API_KEY environment variable to enable this feature")
                return
            
            url = "https://api.polygonscan.com/api"
            params = {
                "module": "account",
                "action": "txlist",
                "address": direct_wallet,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": 100,  # Check last 100 transactions
                "sort": "desc",
                "apikey": polygonscan_api_key
            }
            
            logger.info(f"Querying Polygonscan API for transactions...")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            
            if data.get("status") != "1" or not data.get("result"):
                logger.info(f"No transactions found or API error: {data.get('message', 'Unknown')}")
                return
            
            transactions = data["result"]
            logger.info(f"Found {len(transactions)} transactions from Polygonscan")
            
            # Get all condition IDs from database to filter out already-logged transactions
            session = self.db.SessionLocal()
            try:
                db_positions = session.query(RealMarketMakerPosition).all()
                logged_tx_hashes = {pos.split_transaction_hash for pos in db_positions if pos.split_transaction_hash}
                logged_condition_ids = {pos.condition_id for pos in db_positions if pos.condition_id}
                logger.info(f"Found {len(logged_tx_hashes)} logged transaction hashes in database")
                logger.info(f"Found {len(logged_condition_ids)} logged condition IDs in database")
            finally:
                session.close()
            
            # Parse transactions to find splitPosition calls
            split_position_method_id = "0x3a51cfa2"  # splitPosition function selector (first 4 bytes of keccak256 hash)
            found_condition_ids = set()
            
            for tx in transactions:
                tx_hash = tx.get("hash", "")
                to_address = tx.get("to", "").lower()
                input_data = tx.get("input", "")
                
                # Check if transaction is to CTF contract and is a splitPosition call
                if to_address == ctf_address.lower() and input_data.startswith(split_position_method_id):
                    if tx_hash in logged_tx_hashes:
                        logger.debug(f"  ⏭️ Skipping {tx_hash[:10]}... (already logged to database)")
                        continue
                    
                    # Decode transaction input to extract condition ID
                    try:
                        # Remove method ID (first 10 chars = 0x + 8 hex chars)
                        encoded_params = input_data[10:]
                        
                        # splitPosition signature: (address, bytes32, bytes32, uint256[], uint256)
                        # We need the 3rd parameter (conditionId, bytes32 = 64 hex chars)
                        # Parameters are 32 bytes (64 hex chars) each
                        # offset 0: address (padded to 32 bytes)
                        # offset 64: bytes32 parentCollectionId
                        # offset 128: bytes32 conditionId <- this is what we need
                        condition_id_hex = "0x" + encoded_params[128:192]  # Extract bytes32 at offset 128
                        condition_id = condition_id_hex.lower()
                        
                        if condition_id not in logged_condition_ids:
                            found_condition_ids.add(condition_id)
                            logger.info(f"  ✓ Found unlogged splitPosition: {tx_hash[:16]}... → condition_id: {condition_id}")
                        else:
                            logger.debug(f"  ⏭️ Skipping {tx_hash[:10]}... (condition_id already logged)")
                    except Exception as decode_error:
                        logger.warning(f"  ⚠️ Could not decode transaction {tx_hash[:16]}...: {decode_error}")
                        continue
            
            logger.info(f"")
            logger.info(f"Found {len(found_condition_ids)} condition IDs from Polygonscan not in database")
            
            if not found_condition_ids:
                logger.info("No unlogged transactions found - database is up to date")
                return
            
            # Check wallet balances for these condition IDs and merge if equal shares exist
            merged_count = 0
            total_usdc_freed = 0.0
            
            for condition_id in found_condition_ids:
                try:
                    # Get token IDs for this condition
                    # Use the existing web3 instance from Polymarket
                    w3 = self.pm.web3
                    
                    # Convert condition_id hex string to bytes32
                    condition_id_bytes = bytes.fromhex(condition_id[2:].zfill(64))
                    
                    # YES token ID (outcome 0): keccak256(abi.encodePacked(conditionId, 0))
                    # NO token ID (outcome 1): keccak256(abi.encodePacked(conditionId, 1))
                    # Use solidityKeccak which does abi.encodePacked
                    yes_token_id_raw = w3.solidityKeccak(['bytes32', 'uint256'], [condition_id_bytes, 0])
                    yes_token_id = str(int.from_bytes(yes_token_id_raw, 'big'))
                    
                    no_token_id_raw = w3.solidityKeccak(['bytes32', 'uint256'], [condition_id_bytes, 1])
                    no_token_id = str(int.from_bytes(no_token_id_raw, 'big'))
                    
                    # Check wallet balances
                    yes_balance = self.pm.get_conditional_token_balance(
                        yes_token_id,
                        wallet_address=direct_wallet
                    )
                    no_balance = self.pm.get_conditional_token_balance(
                        no_token_id,
                        wallet_address=direct_wallet
                    )
                    
                    if yes_balance is None or no_balance is None:
                        continue
                    
                    if yes_balance > 0 and no_balance > 0:
                        merge_amount = min(yes_balance, no_balance)
                        logger.info(
                            f"💰 Found merge opportunity (from Polygonscan): "
                            f"condition_id={condition_id[:20]}..., "
                            f"YES={yes_balance:.2f}, NO={no_balance:.2f}, "
                            f"merging {merge_amount:.2f} shares"
                        )
                        
                        loop = asyncio.get_event_loop()
                        merge_result = await loop.run_in_executor(
                            None,
                            self.pm.merge_positions,
                            condition_id,
                            merge_amount
                        )
                        
                        if merge_result:
                            logger.info(f"   ✅ Successfully merged → ${merge_amount:.2f} USDC freed")
                            merged_count += 1
                            total_usdc_freed += merge_amount
                        else:
                            logger.warning(f"   ❌ Failed to merge")
                    
                except Exception as e:
                    logger.error(f"Error processing condition_id {condition_id[:20]}...: {e}", exc_info=True)
                    continue
            
            logger.info("=" * 80)
            logger.info(f"POLYGONSCAN CHECK COMPLETE: Merged {merged_count} positions, freed ${total_usdc_freed:.2f} USDC")
            logger.info("=" * 80)
            
        except Exception as e:
            logger.error(f"Error checking Polygonscan for merge opportunities: {e}", exc_info=True)
    
    async def _merge_from_transaction_hashes(self, tx_hashes: List[str]):
        """
        Merge positions from a list of transaction hashes.
        Fetches transaction details from Polygonscan, extracts condition IDs, and merges if equal YES/NO shares exist.
        
        Args:
            tx_hashes: List of transaction hashes (with or without 0x prefix)
        """
        try:
            if not tx_hashes:
                return
            
            logger.info("=" * 80)
            logger.info(f"MERGING FROM PROVIDED TRANSACTION HASHES ({len(tx_hashes)} transactions)")
            logger.info("=" * 80)
            
            direct_wallet = self.pm.get_address_for_private_key()
            ctf_address = self.pm.ctf_address
            
            # Normalize transaction hashes (ensure 0x prefix)
            normalized_hashes = []
            for tx_hash in tx_hashes:
                tx_hash = tx_hash.strip()
                if not tx_hash.startswith("0x"):
                    tx_hash = "0x" + tx_hash
                normalized_hashes.append(tx_hash.lower())
            
            logger.info(f"Processing {len(normalized_hashes)} transaction hashes...")
            
            # Fetch transaction details from blockchain RPC (no API key needed)
            found_condition_ids = set()
            split_position_method_id = "0x3a51cfa2"  # splitPosition function selector
            
            # Use web3 to fetch transactions directly from blockchain
            w3 = self.pm.web3
            
            for tx_hash in normalized_hashes:
                try:
                    # Fetch transaction details from blockchain RPC
                    loop = asyncio.get_event_loop()
                    tx_data = await loop.run_in_executor(
                        None,
                        w3.eth.get_transaction,
                        tx_hash
                    )
                    
                    if not tx_data:
                        logger.warning(f"  ⚠️ Transaction {tx_hash[:16]}... not found on blockchain")
                        continue
                    
                    to_address = tx_data.get("to", "")
                    if to_address:
                        to_address = to_address.lower()
                    input_data = tx_data.get("input", "")
                    
                    # Check if transaction is to CTF contract and is a splitPosition call
                    if to_address != ctf_address.lower():
                        logger.debug(f"  ⏭️ Skipping {tx_hash[:16]}... (not a CTF contract transaction)")
                        continue
                    
                    if not input_data.startswith(split_position_method_id):
                        logger.debug(f"  ⏭️ Skipping {tx_hash[:16]}... (not a splitPosition call)")
                        continue
                    
                    # Decode transaction input to extract condition ID
                    try:
                        # Remove method ID (first 10 chars = 0x + 8 hex chars)
                        encoded_params = input_data[10:]
                        
                        # splitPosition signature: (address, bytes32, bytes32, uint256[], uint256)
                        # offset 128: bytes32 conditionId
                        condition_id_hex = "0x" + encoded_params[128:192]
                        condition_id = condition_id_hex.lower()
                        
                        found_condition_ids.add(condition_id)
                        logger.info(f"  ✓ Extracted condition_id from {tx_hash[:16]}...: {condition_id}")
                    except Exception as decode_error:
                        logger.warning(f"  ⚠️ Could not decode transaction {tx_hash[:16]}...: {decode_error}")
                        continue
                    
                except Exception as fetch_error:
                    logger.error(f"  ❌ Error fetching transaction {tx_hash[:16]}...: {fetch_error}")
                    continue
            
            logger.info(f"")
            logger.info(f"Found {len(found_condition_ids)} condition IDs from provided transaction hashes")
            
            if not found_condition_ids:
                logger.info("No valid splitPosition transactions found in provided hashes")
                return
            
            # Check wallet balances and merge if equal shares exist
            merged_count = 0
            total_usdc_freed = 0.0
            
            for condition_id in found_condition_ids:
                try:
                    # Calculate token IDs
                    w3 = self.pm.web3
                    condition_id_bytes = bytes.fromhex(condition_id[2:].zfill(64))
                    
                    yes_token_id_raw = w3.solidityKeccak(['bytes32', 'uint256'], [condition_id_bytes, 0])
                    yes_token_id = str(int.from_bytes(yes_token_id_raw, 'big'))
                    
                    no_token_id_raw = w3.solidityKeccak(['bytes32', 'uint256'], [condition_id_bytes, 1])
                    no_token_id = str(int.from_bytes(no_token_id_raw, 'big'))
                    
                    # Check wallet balances
                    yes_balance = self.pm.get_conditional_token_balance(
                        yes_token_id,
                        wallet_address=direct_wallet
                    )
                    no_balance = self.pm.get_conditional_token_balance(
                        no_token_id,
                        wallet_address=direct_wallet
                    )
                    
                    if yes_balance is None or no_balance is None:
                        continue
                    
                    if yes_balance > 0 and no_balance > 0:
                        merge_amount = min(yes_balance, no_balance)
                        logger.info(
                            f"💰 Found merge opportunity (from provided tx hash): "
                            f"condition_id={condition_id[:20]}..., "
                            f"YES={yes_balance:.2f}, NO={no_balance:.2f}, "
                            f"merging {merge_amount:.2f} shares"
                        )
                        
                        loop = asyncio.get_event_loop()
                        merge_result = await loop.run_in_executor(
                            None,
                            self.pm.merge_positions,
                            condition_id,
                            merge_amount
                        )
                        
                        if merge_result:
                            logger.info(f"   ✅ Successfully merged → ${merge_amount:.2f} USDC freed")
                            merged_count += 1
                            total_usdc_freed += merge_amount
                        else:
                            logger.warning(f"   ❌ Failed to merge")
                    else:
                        logger.debug(
                            f"  ⏭️ No equal shares to merge for condition_id {condition_id[:20]}... "
                            f"(YES: {yes_balance:.2f}, NO: {no_balance:.2f})"
                        )
                    
                except Exception as e:
                    logger.error(f"Error processing condition_id {condition_id[:20]}...: {e}", exc_info=True)
                    continue
            
            logger.info("=" * 80)
            logger.info(f"MERGE FROM TX HASHES COMPLETE: Merged {merged_count} positions, freed ${total_usdc_freed:.2f} USDC")
            logger.info("=" * 80)
            
        except Exception as e:
            logger.error(f"Error merging from transaction hashes: {e}", exc_info=True)
    
    async def redeem_past_positions(self, condition_ids: Optional[List[str]] = None):
        """
        Scan wallet and redeem winning shares from past/resolved markets.
        
        Args:
            condition_ids: Optional list of condition IDs to check. If None, checks all positions in database.
        """
        try:
            logger.info("=" * 80)
            logger.info("SCANNING WALLET FOR PAST POSITIONS TO REDEEM")
            logger.info("=" * 80)
            
            # Get positions from database
            session = self.db.SessionLocal()
            try:
                query = session.query(RealMarketMakerPosition).filter(
                    RealMarketMakerPosition.position_status == "resolved"
                )
                
                if condition_ids:
                    query = query.filter(RealMarketMakerPosition.condition_id.in_(condition_ids))
                
                resolved_positions = query.all()
                logger.info(f"Found {len(resolved_positions)} resolved positions in database")
                
                for db_pos in resolved_positions:
                    try:
                        # Check if shares still exist in wallet
                        direct_wallet = self.pm.get_address_for_private_key()
                        yes_balance = self.pm.get_conditional_token_balance(
                            db_pos.yes_token_id, 
                            wallet_address=direct_wallet
                        )
                        no_balance = self.pm.get_conditional_token_balance(
                            db_pos.no_token_id,
                            wallet_address=direct_wallet
                        )
                        
                        if yes_balance is None or no_balance is None:
                            logger.warning(f"Could not check balances for {db_pos.market_slug}")
                            continue
                        
                        if yes_balance == 0 and no_balance == 0:
                            logger.info(f"✓ {db_pos.market_slug}: No shares remaining (already redeemed)")
                            continue
                        
                        logger.info(
                            f"📊 {db_pos.market_slug}: YES={yes_balance:.2f}, NO={no_balance:.2f}, "
                            f"winning_side={db_pos.winning_side}"
                        )
                        
                        # Create position object for redemption
                        position = MarketMakerPosition(
                            market_slug=db_pos.market_slug,
                            market={},  # Not needed for redemption
                            condition_id=db_pos.condition_id,
                            yes_token_id=db_pos.yes_token_id,
                            no_token_id=db_pos.no_token_id,
                            split_amount=db_pos.split_amount,
                            yes_shares=yes_balance,
                            no_shares=no_balance,
                            yes_filled=(yes_balance == 0),
                            no_filled=(no_balance == 0),
                        )
                        
                        # Redeem shares
                        if db_pos.winning_side:
                            await self._redeem_winning_shares(position, db_pos.winning_side)
                        else:
                            logger.warning(
                                f"⚠️ {db_pos.market_slug}: No winning_side recorded. "
                                f"Cannot determine which shares to redeem."
                            )
                            
                    except Exception as e:
                        logger.error(f"Error processing {db_pos.market_slug}: {e}", exc_info=True)
                        continue
                
            finally:
                session.close()
                
            logger.info("=" * 80)
            logger.info("FINISHED SCANNING PAST POSITIONS")
            logger.info("=" * 80)
            
        except Exception as e:
            logger.error(f"Error scanning past positions: {e}", exc_info=True)
    
    async def start(self):
        """Start the market maker loop."""
        logger.info("=" * 80)
        logger.info("STARTING MARKET MAKER")
        logger.info("=" * 80)
        logger.info(f"Split amount: ${self.config.split_amount:.2f}")
        logger.info(f"Offset above midpoint: {self.config.offset_above_midpoint:.4f}")
        logger.info(f"Price step: {self.config.price_step:.4f}")
        logger.info(f"Wait after fill: {self.config.wait_after_fill:.1f} seconds")
        logger.info(f"Poll interval: {self.config.poll_interval:.1f} seconds")
        logger.info("=" * 80)
        
        # Check wallet balances
        # Note: split_position uses direct wallet balance (on-chain), not proxy wallet balance
        try:
            # Get wallet address for verification
            wallet_address = self.pm.get_address_for_private_key()
            logger.info(f"Wallet address: {wallet_address}")
            logger.info(f"  (Make sure USDC is sent to this address on Polygon network)")
            
            # Check direct Polygon wallet balance (used for splitting)
            direct_balance = self.pm.get_usdc_balance()
            logger.info(f"Direct Polygon wallet USDC balance: ${direct_balance:.2f}")
            
            if direct_balance < self.config.split_amount:
                logger.warning(
                    f"⚠ INSUFFICIENT DIRECT WALLET BALANCE: "
                    f"${direct_balance:.2f} < ${self.config.split_amount:.2f} (required for split)"
                )
                logger.info(
                    f"💡 Note: Split position requires USDC in your direct Polygon wallet, "
                    f"not the Polymarket proxy wallet. Transfer USDC to your wallet address."
                )
            else:
                logger.info(f"✓ Direct wallet balance sufficient for split (${self.config.split_amount:.2f})")
            
            # Also check proxy wallet balance (for reference)
            proxy_balance = self.pm.get_polymarket_balance()
            if proxy_balance is not None:
                logger.info(f"Polymarket proxy wallet balance: ${proxy_balance:.2f} (for trading, not splitting)")
        except Exception as e:
            logger.warning(f"Could not check wallet balance: {e}")
        
        logger.info("=" * 80)
        
        # Start WebSocket services if enabled
        # Start orderbook WebSocket service (before resuming positions so we can subscribe)
        if self.websocket_service:
            try:
                await self.websocket_service.start()
                logger.info("✓ WebSocket orderbook service started")
            except Exception as e:
                logger.error(f"Failed to start WebSocket orderbook service: {e}", exc_info=True)
                logger.warning("Continuing with HTTP polling fallback")
        
        # Start order status WebSocket service
        if self.websocket_order_status_service:
            try:
                await self.websocket_order_status_service.start()
                logger.info("✓ WebSocket order status service started")
            except Exception as e:
                logger.error(f"Failed to start WebSocket order status service: {e}", exc_info=True)
                logger.warning("Continuing with HTTP polling fallback")
        
        # Resume monitoring existing positions
        await self._resume_positions()
        
        # Subscribe resumed positions to WebSocket if service is available
        if self.websocket_service:
            for market_slug, position in self.active_positions.items():
                token_ids = [position.yes_token_id, position.no_token_id]
                self.websocket_service.subscribe_tokens(token_ids, market_slug=market_slug)
        
        # On new deployment: scan and redeem past 10 positions
        logger.info("")
        logger.info("Scanning wallet for past positions to redeem (past 10)...")
        try:
            await self.scan_wallet_and_redeem(limit=10)
        except Exception as e:
            logger.error(f"Error scanning wallet for past positions: {e}", exc_info=True)
        logger.info("")
        
        # Check Polygonscan for transactions not logged to database (one-time on startup)
        logger.info("Checking Polygonscan for transactions not logged to database...")
        try:
            await self._check_polygonscan_for_merge_opportunities()
        except Exception as e:
            logger.error(f"Error checking Polygonscan: {e}", exc_info=True)
        logger.info("")
        
        # Merge from transaction hashes in config (if any)
        merge_tx_hashes = self.config.merge_transaction_hashes
        if merge_tx_hashes:
            logger.info(f"Merging from {len(merge_tx_hashes)} transaction hash(es) in config...")
            try:
                await self._merge_from_transaction_hashes(merge_tx_hashes)
            except Exception as e:
                logger.error(f"Error merging from config transaction hashes: {e}", exc_info=True)
            logger.info("")
        
        # Merge most recent positions to free up capital (one-time on startup)
        logger.info("Merging recent positions to free up USDC capital (past 20)...")
        try:
            await self._merge_resolved_positions_for_capital(limit=20)
        except Exception as e:
            logger.error(f"Error merging positions for capital: {e}", exc_info=True)
        logger.info("")
        
        self.running = True
        
        # Start background tasks
        task_coros = {
            "market_detection": self._market_detection_loop,
            "market_maker": self._market_maker_loop,
        }
        
        tasks = {}
        for name, coro in task_coros.items():
            task = asyncio.create_task(coro())
            task.set_name(name)
            tasks[name] = task
            logger.info(f"Started background task: {name}")
        
        try:
            # Monitor tasks and restart if they crash
            while self.running:
                await asyncio.sleep(5.0)
                
                for name, task in list(tasks.items()):
                    if task.done():
                        try:
                            exception = task.exception()
                            if exception:
                                logger.error(f"⚠️ Background task '{name}' crashed - restarting...", exc_info=exception)
                            else:
                                logger.warning(f"⚠️ Background task '{name}' completed - restarting...")
                        except Exception as e:
                            logger.error(f"Error checking task '{name}' status: {e}", exc_info=True)
                        
                        if self.running and name in task_coros:
                            logger.info(f"🔄 Restarting background task: {name}")
                            new_task = asyncio.create_task(task_coros[name]())
                            new_task.set_name(name)
                            tasks[name] = new_task
                            
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error("CRITICAL ERROR in market maker loop", exc_info=True)
            raise
        finally:
            self.running = False
            logger.info("Market maker stopped - cancelling all background tasks...")
            
            for name, task in tasks.items():
                if not task.done():
                    logger.info(f"Cancelling task: {name}")
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            # Stop WebSocket services
            if self.websocket_service:
                try:
                    await self.websocket_service.stop()
                    logger.info("✓ WebSocket orderbook service stopped")
                except Exception as e:
                    logger.error(f"Error stopping WebSocket orderbook service: {e}")
            
            if self.websocket_order_status_service:
                try:
                    await self.websocket_order_status_service.stop()
                    logger.info("✓ WebSocket order status service stopped")
                except Exception as e:
                    logger.error(f"Error stopping WebSocket order status service: {e}")
    
    async def _handle_websocket_order_update(self, order_data: Dict):
        """
        Handle order status update from WebSocket.
        
        Called when WebSocket receives an order update (placement, cancellation, fill).
        
        Args:
            order_data: Order update data from WebSocket
        """
        try:
            order_id = order_data.get("id") or order_data.get("order_id") or order_data.get("orderID")
            order_status = order_data.get("status", "unknown")
            
            if not order_id:
                logger.debug(f"⚠️ WebSocket order update missing order_id: {order_data}")
                return
            
            # Find position with this order_id
            position = None
            side = None
            for market_slug, pos in self.active_positions.items():
                if pos.yes_order_id == order_id:
                    position = pos
                    side = "YES"
                    break
                elif pos.no_order_id == order_id:
                    position = pos
                    side = "NO"
                    break
            
            if not position:
                # Not one of our orders
                return
            
            logger.info(
                f"📋 WebSocket order update | Market: {position.market_slug} | Side: {side} | "
                f"Order: {order_id[:20]}... | Status: {order_status}"
            )
            
            # Handle filled orders
            if order_status in ["filled", "FILLED", "complete", "COMPLETE"]:
                filled_shares = order_data.get("size") or order_data.get("filled_amount")
                if filled_shares:
                    filled_shares = float(filled_shares)
                else:
                    filled_shares = position.yes_shares if side == "YES" else position.no_shares
                
                # Extract fill price from WebSocket data
                fill_price = order_data.get("price") or order_data.get("fillPrice")
                limit_price = position.yes_order_price if side == "YES" else position.no_order_price
                
                if fill_price:
                    fill_price = float(fill_price)
                    # Validate for sell orders (should never fill below limit price)
                    if fill_price < limit_price:
                        logger.warning(
                            f"⚠️ WebSocket fill price (${fill_price:.4f}) is BELOW limit price (${limit_price:.4f}) "
                            f"for {side} sell order! Using limit price instead."
                        )
                        fill_price = limit_price
                else:
                    fill_price = limit_price
                
                fill_time = datetime.now(timezone.utc)
                
                logger.info(
                    f"✅ WebSocket: {side} order FILLED for {position.market_slug}: "
                    f"{filled_shares:.2f} shares @ ${fill_price:.4f}"
                )
                
                # Update position state
                if side == "YES":
                    position.yes_filled = True
                    position.yes_fill_time = fill_time
                else:
                    position.no_filled = True
                    position.no_fill_time = fill_time
                
                # Initialize last_adjustment_time if this is the first side to fill
                if position.last_adjustment_time is None:
                    position.last_adjustment_time = fill_time
                
                # Update database
                self._update_fill_details_in_db(position, side, filled_shares, fill_price)
                
                # Track fill timing
                if side == "YES":
                    if not position.no_filled:
                        # YES filled first, NO hasn't filled yet
                        logger.info(f"📊 Fill timing: YES filled first (NO still pending)")
                        self._update_fill_timing_in_db(position, "YES", None, None)
                    else:
                        # NO already filled - calculate time difference
                        time_diff = (position.yes_fill_time - position.no_fill_time).total_seconds()
                        logger.info(
                            f"📊 Fill timing: NO filled first, YES filled {time_diff:.2f}s later"
                        )
                        self._update_fill_timing_in_db(position, "NO", "YES", time_diff)
                else:  # NO
                    if not position.yes_filled:
                        # NO filled first, YES hasn't filled yet
                        logger.info(f"📊 Fill timing: NO filled first (YES still pending)")
                        self._update_fill_timing_in_db(position, "NO", None, None)
                    else:
                        # YES already filled - calculate time difference
                        time_diff = (position.no_fill_time - position.yes_fill_time).total_seconds()
                        logger.info(
                            f"📊 Fill timing: YES filled first, NO filled {time_diff:.2f}s later"
                        )
                        self._update_fill_timing_in_db(position, "YES", "NO", time_diff)
            
            # Handle cancelled orders
            elif order_status in ["cancelled", "CANCELLED", "canceled"]:
                logger.info(f"❌ WebSocket: {side} order CANCELLED for {position.market_slug}")
                # Order cancellation is handled in _adjust_unfilled_side, so we just log it
        
        except Exception as e:
            logger.error(f"Error handling WebSocket order update: {e}", exc_info=True)
    
    async def _handle_websocket_trade_update(self, trade_data: Dict):
        """
        Handle trade/fill update from WebSocket.
        
        Called when WebSocket receives a trade event (order was matched/filled).
        This is similar to order update but provides additional trade details.
        
        Args:
            trade_data: Trade update data from WebSocket
        """
        try:
            order_id = trade_data.get("order_id") or trade_data.get("orderID")
            
            if not order_id:
                logger.debug(f"⚠️ WebSocket trade update missing order_id: {trade_data}")
                return
            
            # Find position with this order_id
            position = None
            side = None
            for market_slug, pos in self.active_positions.items():
                if pos.yes_order_id == order_id:
                    position = pos
                    side = "YES"
                    break
                elif pos.no_order_id == order_id:
                    position = pos
                    side = "NO"
                    break
            
            if not position:
                # Not one of our orders
                return
            
            # Trade updates are handled similarly to order updates
            # The order update callback will handle the fill, so we just log it
            logger.debug(
                f"📊 WebSocket trade update | Market: {position.market_slug} | Side: {side} | "
                f"Order: {order_id[:20]}... | Price: {trade_data.get('price', 'N/A')}"
            )
        
        except Exception as e:
            logger.error(f"Error handling WebSocket trade update: {e}", exc_info=True)
    
    async def _resume_positions(self):
        """Resume monitoring existing positions from database."""
        # Query for active positions from this deployment
        session = self.db.SessionLocal()
        try:
            positions = session.query(RealMarketMakerPosition).filter(
                RealMarketMakerPosition.deployment_id == self.deployment_id,
                RealMarketMakerPosition.position_status == 'active'
            ).all()
            
            logger.info(f"Found {len(positions)} active positions to resume")
            
            for db_pos in positions:
                market_slug = db_pos.market_slug
                market = get_market_by_slug(market_slug)
                
                if not market:
                    logger.warning(f"Could not find market for slug: {market_slug}")
                    continue
                
                # Recreate position object
                position = MarketMakerPosition(
                    market_slug=market_slug,
                    market=market,
                    condition_id=db_pos.condition_id,
                    yes_token_id=db_pos.yes_token_id,
                    no_token_id=db_pos.no_token_id,
                    split_amount=db_pos.split_amount,
                    yes_shares=db_pos.yes_shares,
                    no_shares=db_pos.no_shares,
                    split_transaction_hash=db_pos.split_transaction_hash,
                    yes_order_id=db_pos.yes_order_id,
                    no_order_id=db_pos.no_order_id,
                    yes_order_price=db_pos.yes_order_price,
                    no_order_price=db_pos.no_order_price,
                    yes_filled=db_pos.yes_order_status == 'filled',
                    no_filled=db_pos.no_order_status == 'filled',
                    adjustment_count=db_pos.adjustment_count,
                    db_position_id=db_pos.id,
                )
                
                self.active_positions[market_slug] = position
                self.monitored_markets.add(market_slug)
                logger.info(f"Resumed position for market: {market_slug}")
                
        finally:
            session.close()
    
    async def _market_detection_loop(self):
        """Continuously detect new BTC 1-hour markets."""
        check_interval = 60.0  # Check every 60 seconds
        
        while self.running:
            try:
                await self._check_for_new_markets()
            except Exception as e:
                logger.error(f"Error in market detection: {e}", exc_info=True)
            
            await asyncio.sleep(check_interval)
    
    async def _check_for_new_markets(self):
        """Check for new BTC 1-hour markets."""
        try:
            market = get_latest_btc_1h_market_proactive()
            
            if not market:
                return
            
            market_slug = market.get("slug") or market.get("_event_slug")
            if not market_slug:
                logger.warning("Market found but no slug available")
                return
            
            # Skip if already monitoring
            if market_slug in self.monitored_markets:
                return
            
            # Skip if market is not active
            if not is_market_active(market):
                return
            
            # Check if we're too close to resolution (don't create new positions)
            minutes_remaining = get_minutes_until_resolution(market)
            if minutes_remaining is None:
                logger.warning(f"Could not determine time remaining for market {market_slug}")
                return
            
            # Check max_minutes_before_resolution (upper limit - don't start too early)
            if self.config.max_minutes_before_resolution is not None:
                if minutes_remaining > self.config.max_minutes_before_resolution:
                    logger.info(
                        f"Market {market_slug} found but {minutes_remaining:.1f} minutes remaining "
                        f"exceeds max_minutes_before_resolution ({self.config.max_minutes_before_resolution:.1f})"
                    )
                    return
            
            # Check min_minutes_before_resolution (lower limit - don't start too late)
            if self.config.min_minutes_before_resolution is not None:
                if minutes_remaining < self.config.min_minutes_before_resolution:
                    logger.info(
                        f"Market {market_slug} found but {minutes_remaining:.1f} minutes remaining "
                        f"is less than min_minutes_before_resolution ({self.config.min_minutes_before_resolution:.1f}). "
                        f"Skipping new position."
                    )
                    return
            
            # Get token IDs
            token_ids = get_token_ids_from_market(market)
            if not token_ids or len(token_ids) < 2:
                logger.warning(f"Could not get token IDs for market {market_slug}")
                return
            
            yes_token_id = token_ids[0]
            no_token_id = token_ids[1]
            
            # Get condition ID
            condition_id = market.get("conditionId")
            if not condition_id:
                logger.warning(f"Could not get conditionId for market {market_slug}")
                return
            
            logger.info(f"Found new BTC 1h market: {market_slug}")
            logger.info(f"  Question: {market.get('question', 'N/A')}")
            logger.info(f"  Condition ID: {condition_id}")
            
            # Subscribe to tokens for WebSocket orderbook updates
            if self.websocket_service:
                token_ids = [yes_token_id, no_token_id]
                self.websocket_service.subscribe_tokens(token_ids, market_slug=market_slug)
            
            # Start market making for this market
            success = await self._start_market_making(market, market_slug, condition_id, yes_token_id, no_token_id)
            
            if not success:
                logger.warning(f"Failed to start market making for {market_slug}, will retry on next check")
            
        except Exception as e:
            logger.error(f"Error checking for new markets: {e}", exc_info=True)
    
    async def _start_market_making(
        self,
        market: Dict,
        market_slug: str,
        condition_id: str,
        yes_token_id: str,
        no_token_id: str
    ) -> bool:
        """Start market making for a new market. Returns True if successful, False otherwise."""
        try:
            # Check if we already have an active position for this market (prevent duplicate splits)
            if market_slug in self.active_positions:
                logger.debug(f"Already have active position for {market_slug}, skipping")
                return True
            
            # Check database for recent active/pending positions for this market
            session = self.db.SessionLocal()
            try:
                existing = session.query(RealMarketMakerPosition).filter(
                    RealMarketMakerPosition.market_slug == market_slug,
                    RealMarketMakerPosition.position_status.in_(['active', 'pending'])
                ).order_by(RealMarketMakerPosition.id.desc()).first()
                
                if existing:
                    logger.info(
                        f"Found existing {existing.position_status} position for {market_slug} "
                        f"(ID: {existing.id}). Skipping duplicate split."
                    )
                    return True
            finally:
                session.close()
            
            # Create position object (before split, so we can track it in DB)
            position = MarketMakerPosition(
                market_slug=market_slug,
                market=market,
                condition_id=condition_id,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                split_amount=self.config.split_amount,
                yes_shares=self.config.split_amount,
                no_shares=self.config.split_amount,
                split_transaction_hash=None,  # Will be updated after split
            )
            
            # Save to database BEFORE split (so we have a record even if split hangs/fails)
            db_position = self._save_position_to_db(position)
            if db_position:
                position.db_position_id = db_position.id
                logger.info(f"📝 Created pending position record in database: ID={db_position.id}")
            
            # Split position (this may take time waiting for receipt)
            split_result = await self._split_position(condition_id)
            
            if not split_result:
                logger.error(f"Failed to split position for market {market_slug}")
                # Update database record to mark as failed
                if position.db_position_id:
                    self._update_split_status_in_db(position.db_position_id, None, "failed")
                return False
            
            # Update position with transaction hash
            position.split_transaction_hash = split_result.get("transaction_hash")
            
            # Update database with transaction hash
            if position.db_position_id:
                self._update_split_status_in_db(position.db_position_id, position.split_transaction_hash, "active")
            
            # Place sell orders
            logger.info(f"📤 Placing sell orders for {market_slug}...")
            await self._place_sell_orders(position)
            
            # Verify orders were placed
            if not position.yes_order_id and not position.no_order_id:
                logger.error(f"❌ Failed to place sell orders for {market_slug} - no order IDs")
                return False
            
            # Track position
            self.active_positions[market_slug] = position
            self.monitored_markets.add(market_slug)
            
            logger.info(f"✅✅✅ Started market making for {market_slug} ✅✅✅")
            logger.info(f"   YES order: {position.yes_order_id[:20] if position.yes_order_id else 'None'}... @ ${position.yes_order_price:.4f}")
            logger.info(f"   NO order: {position.no_order_id[:20] if position.no_order_id else 'None'}... @ ${position.no_order_price:.4f}")
            return True
            
        except Exception as e:
            logger.error(f"Error starting market making for {market_slug}: {e}", exc_info=True)
            return False
    
    async def _split_position(self, condition_id: str) -> Optional[Dict]:
        """Split USDC into YES + NO shares."""
        logger.info(f"Splitting ${self.config.split_amount:.2f} USDC into YES + NO shares...")
        
        # Pre-check balance before attempting split
        try:
            current_balance = self.pm.get_usdc_balance()
            if current_balance < self.config.split_amount:
                logger.error(
                    f"❌ Insufficient USDC balance for split: "
                    f"have ${current_balance:.2f}, need ${self.config.split_amount:.2f}. "
                    f"Skipping split for this market."
                )
                logger.info(
                    f"💡 Transfer USDC to wallet: {self.pm.get_address_for_private_key()}"
                )
                return None
            logger.info(f"✓ Balance check passed: ${current_balance:.2f} >= ${self.config.split_amount:.2f}")
        except Exception as e:
            logger.warning(f"Could not check balance before split: {e}. Proceeding anyway...")
        
        try:
            # Call split_position synchronously (it's a blocking web3 call)
            # Run in executor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            split_result = await loop.run_in_executor(
                None,
                self.pm.split_position,
                condition_id,
                self.config.split_amount
            )
            
            if not split_result:
                logger.error(
                    f"❌ Split position failed for condition_id {condition_id}. "
                    f"Possible reasons: insufficient USDC balance, insufficient USDC approval, "
                    f"or transaction failed. Check logs above for details."
                )
                return None
            
            # Log successful split
            tx_hash = split_result.get("transaction_hash")
            logger.info(f"✅✅✅ Split completed successfully! Transaction: {tx_hash}")
            logger.info(f"   Proceeding to place sell orders...")
            
            return split_result
            
        except Exception as e:
            logger.error(f"❌ Exception during split position: {e}", exc_info=True)
            logger.error(
                f"Split failed for condition_id {condition_id}. "
                f"Check: 1) USDC balance >= ${self.config.split_amount:.2f}, "
                f"2) USDC approved for CTF contract, 3) Network connectivity"
            )
            return None
    
    def _save_position_to_db(self, position: MarketMakerPosition) -> Optional[RealMarketMakerPosition]:
        """Save position to database."""
        try:
            session = self.db.SessionLocal()
            try:
                db_position = RealMarketMakerPosition(
                    deployment_id=self.deployment_id,
                    split_amount=position.split_amount,
                    offset_above_midpoint=self.config.offset_above_midpoint,
                    price_step=self.config.price_step,
                    wait_after_fill=self.config.wait_after_fill,
                    poll_interval=self.config.poll_interval,
                    market_type=self.config.market_type,
                    market_id=str(position.market.get("id", "")),
                    market_slug=position.market_slug,
                    condition_id=position.condition_id,
                    yes_token_id=position.yes_token_id,
                    no_token_id=position.no_token_id,
                    split_transaction_hash=position.split_transaction_hash,
                    yes_shares=position.yes_shares,
                    no_shares=position.no_shares,
                    position_status='pending',  # Will be updated to 'active' after split completes
                )
                
                session.add(db_position)
                session.commit()
                session.refresh(db_position)
                
                logger.info(f"Saved position to database: ID={db_position.id}")
                return db_position
                
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error saving position to database: {e}", exc_info=True)
            return None
    
    async def _place_sell_orders(self, position: MarketMakerPosition):
        """Place sell orders for YES and NO shares."""
        try:
            # Wait a few seconds after split for shares to settle on-chain
            logger.info("⏳ Waiting 5 seconds for shares to settle after split...")
            await asyncio.sleep(5.0)
            
            # Verify shares are available on-chain
            # IMPORTANT: Split always goes to direct wallet (on-chain transaction), not proxy wallet
            # So we must check the direct wallet address, not proxy wallet
            direct_wallet_address = self.pm.get_address_for_private_key()
            logger.info(
                f"🔍 Verifying YES and NO shares are available on-chain "
                f"(checking direct wallet: {direct_wallet_address[:10]}...{direct_wallet_address[-8:]})..."
            )
            yes_balance = self.pm.get_conditional_token_balance(position.yes_token_id, wallet_address=direct_wallet_address)
            no_balance = self.pm.get_conditional_token_balance(position.no_token_id, wallet_address=direct_wallet_address)
            
            if yes_balance is None or no_balance is None:
                logger.error("❌ Could not check conditional token balances - aborting order placement")
                return
            
            logger.info(f"  YES balance: {yes_balance:.2f} shares (expected: {position.yes_shares:.2f})")
            logger.info(f"  NO balance: {no_balance:.2f} shares (expected: {position.no_shares:.2f})")
            
            if yes_balance < position.yes_shares or no_balance < position.no_shares:
                logger.warning(
                    f"⚠️ Insufficient shares on-chain! "
                    f"YES: {yes_balance:.2f} < {position.yes_shares:.2f}, "
                    f"NO: {no_balance:.2f} < {position.no_shares:.2f}. "
                    f"Shares may still be settling. Waiting 5 more seconds..."
                )
                await asyncio.sleep(5.0)
                # Re-check after wait (still checking direct wallet)
                yes_balance = self.pm.get_conditional_token_balance(position.yes_token_id, wallet_address=direct_wallet_address)
                no_balance = self.pm.get_conditional_token_balance(position.no_token_id, wallet_address=direct_wallet_address)
                if yes_balance is None or no_balance is None:
                    logger.error("❌ Could not re-check conditional token balances - aborting order placement")
                    return
                logger.info(f"  After wait - YES: {yes_balance:.2f}, NO: {no_balance:.2f}")
                if yes_balance < position.yes_shares or no_balance < position.no_shares:
                    logger.error(
                        f"❌ Still insufficient shares after wait! "
                        f"YES: {yes_balance:.2f} < {position.yes_shares:.2f}, "
                        f"NO: {no_balance:.2f} < {position.no_shares:.2f}. "
                        f"Aborting order placement."
                    )
                    return
            
            # Ensure conditional token allowances are set (required for selling)
            # IMPORTANT: Check allowances for DIRECT wallet (where shares are), not proxy wallet
            logger.info("🔍 Checking conditional token allowances for exchange contracts...")
            logger.info(f"   Checking allowances for DIRECT wallet: {direct_wallet_address[:10]}...{direct_wallet_address[-8:]}")
            logger.info(f"   (Shares are in direct wallet, so allowances must be set for direct wallet)")
            allowances_ok = self.pm.ensure_conditional_token_allowances(wallet_address=direct_wallet_address)
            if not allowances_ok:
                logger.error(
                    "❌ Conditional token allowances NOT set. Cannot place sell orders. "
                    "Please check the approval transactions above and ensure they completed successfully."
                )
                logger.error(
                    "   Orders will fail with 'not enough balance / allowance' error. "
                    "Aborting order placement until allowances are set."
                )
                return
            
            # Get orderbook for YES side (NO should be symmetric)
            yes_orderbook = fetch_orderbook(position.yes_token_id)
            
            if not yes_orderbook:
                logger.error(f"Could not fetch orderbook for YES token {position.yes_token_id}")
                return
            
            # Get market prices for comparison
            best_bid = get_highest_bid(yes_orderbook)
            best_ask = get_lowest_ask(yes_orderbook)
            
            # Calculate midpoint (weighted or simple)
            midpoint = calculate_midpoint(
                yes_orderbook,
                weighted=self.config.use_weighted_midpoint,
                depth_levels=self.config.midpoint_depth_levels
            )
            
            if midpoint is None:
                logger.error("Could not calculate midpoint from orderbook")
                return
            
            # Calculate sell prices
            # Strategy: Sell at midpoint + offset, but ensure we're competitive
            base_sell_price = midpoint + self.config.offset_above_midpoint
            
            # If best bid exists and our price is above it, consider matching best bid for immediate fill
            # But for market making, we want to sell above market, so we'll use the calculated price
            sell_price = base_sell_price
            
            # Cap at 0.99 (max sell price)
            sell_price = min(sell_price, 0.99)
            
            # Warn if price is uncompetitive
            if best_bid and sell_price > best_bid + 0.01:  # More than 1% above best bid
                logger.warning(
                    f"   ⚠️ WARNING: Sell price (${sell_price:.4f}) is ${sell_price - best_bid:.4f} above best bid (${best_bid:.4f}). "
                    f"Orders may not fill unless market moves up or buyers pay premium."
                )
            
            midpoint_type = "weighted" if self.config.use_weighted_midpoint else "simple"
            logger.info("=" * 80)
            logger.info(f"📊 MARKET ANALYSIS for {position.market_slug}:")
            logger.info(f"   Best BID (highest buyer): ${best_bid:.4f}" if best_bid else "   Best BID: None")
            logger.info(f"   Best ASK (lowest seller): ${best_ask:.4f}" if best_ask else "   Best ASK: None")
            logger.info(f"   {midpoint_type.upper()} Midpoint: ${midpoint:.4f}")
            logger.info(f"   Our SELL price: ${sell_price:.4f} (midpoint + {self.config.offset_above_midpoint:.4f})")
            
            # Check if our price is competitive
            if best_ask is not None:
                if sell_price > best_ask:
                    logger.warning(
                        f"   ⚠️ Our sell price (${sell_price:.4f}) is HIGHER than best ask (${best_ask:.4f}) - "
                        f"orders may not fill quickly (we're ${sell_price - best_ask:.4f} above market)"
                    )
                elif sell_price < best_ask:
                    logger.info(
                        f"   ✓ Our sell price (${sell_price:.4f}) is BELOW best ask (${best_ask:.4f}) - "
                        f"should fill immediately (${best_ask - sell_price:.4f} better than market)"
                    )
                else:
                    logger.info(f"   ✓ Our sell price matches best ask (${sell_price:.4f})")
            
            if best_bid is not None:
                if sell_price <= best_bid:
                    logger.info(
                        f"   ✅ Our sell price (${sell_price:.4f}) is AT or BELOW best bid (${best_bid:.4f}) - "
                        f"should fill immediately!"
                    )
                else:
                    logger.info(
                        f"   📈 Our sell price (${sell_price:.4f}) is ${sell_price - best_bid:.4f} above best bid (${best_bid:.4f})"
                    )
            
            logger.info("=" * 80)
            
            # Place YES and NO sell orders in batch to reduce latency
            loop = asyncio.get_event_loop()
            
            orders_to_place = [
                {
                    "price": sell_price,
                    "size": position.yes_shares,
                    "side": "SELL",
                    "token_id": position.yes_token_id,
                },
                {
                    "price": sell_price,
                    "size": position.no_shares,
                    "side": "SELL",
                    "token_id": position.no_token_id,
                }
            ]
            
            # Execute batch order placement
            batch_result = await loop.run_in_executor(
                None,
                self.pm.place_orders_batch,
                orders_to_place
            )
            
            # Process batch result
            if batch_result:
                # Extract order IDs from batch response
                if isinstance(batch_result, dict):
                    results = batch_result.get("results", batch_result.get("data", []))
                    if isinstance(results, list) and len(results) >= 2:
                        yes_order_id = self.pm.extract_order_id(results[0])
                        no_order_id = self.pm.extract_order_id(results[1])
                        
                        if yes_order_id:
                            position.yes_order_id = yes_order_id
                            position.yes_order_price = sell_price
                            logger.info(f"✅ Placed YES sell order: {yes_order_id}")
                            
                            # Verify order status immediately
                            await asyncio.sleep(1.0)  # Brief wait for order to propagate
                            yes_status = self.pm.get_order_status(yes_order_id)
                            if yes_status:
                                logger.info(f"   📋 YES order status: {yes_status.get('status', 'unknown')}")
                            else:
                                logger.warning(f"   ⚠️ Could not get YES order status immediately after placement")
                        else:
                            logger.warning(f"⚠️ Could not extract YES order ID from batch response: {results[0]}")
                        
                        if no_order_id:
                            position.no_order_id = no_order_id
                            position.no_order_price = sell_price
                            logger.info(f"✅ Placed NO sell order: {no_order_id}")
                            
                            # Verify order status immediately
                            await asyncio.sleep(1.0)  # Brief wait for order to propagate
                            no_status = self.pm.get_order_status(no_order_id)
                            if no_status:
                                logger.info(f"   📋 NO order status: {no_status.get('status', 'unknown')}")
                            else:
                                logger.warning(f"   ⚠️ Could not get NO order status immediately after placement")
                        else:
                            logger.warning(f"⚠️ Could not extract NO order ID from batch response: {results[1]}")
                    else:
                        logger.warning(f"⚠️ Unexpected batch place response format - expected list with 2+ items, got: {batch_result}")
                        logger.warning(f"   Response type: {type(results) if 'results' in locals() else 'unknown'}, length: {len(results) if isinstance(results, list) else 'N/A'}")
                else:
                    logger.warning(f"⚠️ Unexpected batch place response type - expected dict, got: {type(batch_result)}")
                    logger.warning(f"   Response: {batch_result}")
            else:
                logger.error(f"❌ Batch order placement failed for {position.market_slug} - batch_result is None or empty")
                logger.warning(f"⚠️ Initial sell orders were not placed - position may be incomplete")
            
            # Verify orders were placed successfully and check their status
            if position.yes_order_id or position.no_order_id:
                position.orders_placed_time = datetime.now(timezone.utc)
                self._update_position_in_db(position)
                
                # Wait a moment for orders to appear on orderbook
                await asyncio.sleep(2.0)
                
                # Verify orders are on the orderbook
                logger.info("🔍 Verifying orders appear on orderbook...")
                yes_orderbook_after = fetch_orderbook(position.yes_token_id)
                no_orderbook_after = fetch_orderbook(position.no_token_id)
                
                if yes_orderbook_after and position.yes_order_id:
                    asks = yes_orderbook_after.get("asks", [])
                    our_price_found = any(
                        abs(float(ask[0]) - position.yes_order_price) < 0.0001 
                        for ask in asks[:10]  # Check top 10 asks
                    )
                    if our_price_found:
                        logger.info(f"   ✅ YES order found on orderbook at ${position.yes_order_price:.4f}")
                    else:
                        logger.warning(
                            f"   ⚠️ YES order NOT found on orderbook at ${position.yes_order_price:.4f}. "
                            f"Top asks: {[f'${ask[0]:.4f}' for ask in asks[:5]]}"
                        )
                
                if no_orderbook_after and position.no_order_id:
                    asks = no_orderbook_after.get("asks", [])
                    our_price_found = any(
                        abs(float(ask[0]) - position.no_order_price) < 0.0001 
                        for ask in asks[:10]  # Check top 10 asks
                    )
                    if our_price_found:
                        logger.info(f"   ✅ NO order found on orderbook at ${position.no_order_price:.4f}")
                    else:
                        logger.warning(
                            f"   ⚠️ NO order NOT found on orderbook at ${position.no_order_price:.4f}. "
                            f"Top asks: {[f'${ask[0]:.4f}' for ask in asks[:5]]}"
                        )
                    
        except Exception as e:
            logger.error(f"Error placing sell orders: {e}", exc_info=True)
    
    def _update_split_status_in_db(self, db_position_id: int, transaction_hash: Optional[str], status: str):
        """Update split transaction hash and status in database."""
        try:
            session = self.db.SessionLocal()
            try:
                db_position = session.query(RealMarketMakerPosition).filter(
                    RealMarketMakerPosition.id == db_position_id
                ).first()
                
                if db_position:
                    if transaction_hash:
                        db_position.split_transaction_hash = transaction_hash
                    db_position.position_status = status
                    db_position.updated_at = datetime.now(timezone.utc)
                    session.commit()
                    logger.info(f"Updated position {db_position_id} in database: status={status}, tx_hash={transaction_hash}")
                    
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error updating split status in database: {e}", exc_info=True)
    
    def _update_position_in_db(self, position: MarketMakerPosition):
        """Update position in database."""
        if not position.db_position_id:
            return
        
        try:
            session = self.db.SessionLocal()
            try:
                db_position = session.query(RealMarketMakerPosition).filter(
                    RealMarketMakerPosition.id == position.db_position_id
                ).first()
                
                if db_position:
                    db_position.yes_order_id = position.yes_order_id
                    db_position.no_order_id = position.no_order_id
                    db_position.yes_order_price = position.yes_order_price
                    db_position.no_order_price = position.no_order_price
                    db_position.yes_order_size = position.yes_shares
                    db_position.no_order_size = position.no_shares
                    db_position.adjustment_count = position.adjustment_count
                    db_position.updated_at = datetime.now(timezone.utc)
                    
                    session.commit()
                    
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error updating position in database: {e}", exc_info=True)
    
    def _update_order_status_in_db(
        self,
        position: MarketMakerPosition,
        side: str,
        status: str,
        filled_amount: float,
        total_amount: float,
        order_status_dict: Optional[Dict] = None
    ):
        """Update order status in database."""
        if not position.db_position_id:
            return
        
        try:
            session = self.db.SessionLocal()
            try:
                db_position = session.query(RealMarketMakerPosition).filter(
                    RealMarketMakerPosition.id == position.db_position_id
                ).first()
                
                if db_position:
                    if side == "YES":
                        db_position.yes_order_status = status
                        db_position.yes_filled_shares = filled_amount if filled_amount > 0 else None
                    else:
                        db_position.no_order_status = status
                        db_position.no_filled_shares = filled_amount if filled_amount > 0 else None
                    
                    db_position.updated_at = datetime.now(timezone.utc)
                    session.commit()
                    
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error updating order status in database: {e}", exc_info=True)
    
    def _update_fill_details_in_db(
        self,
        position: MarketMakerPosition,
        side: str,
        filled_shares: float,
        fill_price: float
    ):
        """Update fill details in database."""
        if not position.db_position_id:
            return
        
        try:
            session = self.db.SessionLocal()
            try:
                db_position = session.query(RealMarketMakerPosition).filter(
                    RealMarketMakerPosition.id == position.db_position_id
                ).first()
                
                if db_position:
                    if side == "YES":
                        db_position.yes_order_status = "filled"
                        db_position.yes_filled_shares = filled_shares
                        db_position.yes_fill_price = fill_price
                        db_position.yes_filled_at = position.yes_fill_time
                    else:
                        db_position.no_order_status = "filled"
                        db_position.no_filled_shares = filled_shares
                        db_position.no_fill_price = fill_price
                        db_position.no_filled_at = position.no_fill_time
                    
                    db_position.updated_at = datetime.now(timezone.utc)
                    session.commit()
                    
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error updating fill details in database: {e}", exc_info=True)
    
    def _extract_fill_price(self, order_status: Dict, fallback_price: Optional[float], is_sell_order: bool = True) -> float:
        """
        Extract fill price from order status, or use fallback.
        
        Args:
            order_status: Order status dictionary from API
            fallback_price: Original limit order price (used if API doesn't provide fill price)
            is_sell_order: True if this is a sell order (for validation)
        
        Returns:
            Fill price (validated for sell orders to ensure it's >= limit price)
        """
        # Try to get fill price from order status
        fill_price = (
            order_status.get("price") or
            order_status.get("fillPrice") or
            order_status.get("fill_price") or
            order_status.get("averageFillPrice") or
            order_status.get("average_fill_price") or
            order_status.get("avgFillPrice") or
            order_status.get("avg_fill_price") or
            None
        )
        
        extracted_price = None
        if fill_price:
            try:
                extracted_price = float(fill_price)
            except (ValueError, TypeError):
                pass
        
        # Use fallback price (original order price) if API didn't provide fill price
        if extracted_price is None:
            if fallback_price:
                logger.debug(f"Using fallback price (limit order price): ${fallback_price:.4f}")
                return fallback_price
            else:
                logger.warning("No fill price found in order status and no fallback price - using default 0.50")
                return 0.50
        
        # Validate fill price for sell orders (should never be below limit price)
        if is_sell_order and fallback_price:
            if extracted_price < fallback_price:
                logger.warning(
                    f"⚠️ Fill price (${extracted_price:.4f}) is BELOW limit price (${fallback_price:.4f}) "
                    f"for sell order! This shouldn't happen. Using limit price instead. "
                    f"Order status fields: {list(order_status.keys())}"
                )
                logger.debug(f"Full order status: {order_status}")
                # Use limit price instead of the suspicious lower price
                return fallback_price
        
        return extracted_price
    
    def _update_fill_timing_in_db(
        self,
        position: MarketMakerPosition,
        first_fill_side: str,
        second_fill_side: Optional[str],
        time_between_fills: Optional[float]
    ):
        """Update fill timing information in database."""
        if not position.db_position_id:
            return
        
        try:
            session = self.db.SessionLocal()
            try:
                db_position = session.query(RealMarketMakerPosition).filter(
                    RealMarketMakerPosition.id == position.db_position_id
                ).first()
                
                if db_position:
                    db_position.first_fill_side = first_fill_side
                    db_position.second_fill_side = second_fill_side
                    db_position.time_between_fills_seconds = time_between_fills
                    db_position.updated_at = datetime.now(timezone.utc)
                    
                    session.commit()
                    
                    if time_between_fills is not None:
                        logger.info(
                            f"📊 Stored fill timing: {first_fill_side} → {second_fill_side} "
                            f"({time_between_fills:.2f}s)"
                        )
                    
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error updating fill timing in database: {e}", exc_info=True)
    
    async def _market_maker_loop(self):
        """Main market maker loop - monitors and adjusts orders."""
        while self.running:
            try:
                # Track orderbook prices for markets near resolution
                await self._track_orderbook_prices_near_resolution()
                
                # Process each position
                for market_slug, position in list(self.active_positions.items()):
                    await self._process_position(market_slug, position)
                    
                    # Check for market resolution
                    await self._check_market_resolution(market_slug, position)
            except Exception as e:
                logger.error(f"Error in market maker loop: {e}", exc_info=True)
            
            await asyncio.sleep(self.config.poll_interval)
    
    async def _process_position(self, market_slug: str, position: MarketMakerPosition):
        """Process a single position - check order status and handle fills."""
        try:
            # Check if market is still active
            if not is_market_active(position.market):
                logger.info(f"Market {market_slug} is no longer active, closing position")
                await self._close_position(position, reason="market_closed")
                return
            
            # Check order status for both sides
            # WebSocket provides real-time updates via callbacks, but we still do periodic HTTP checks as backup
            yes_status = None
            no_status = None
            
            # Check if WebSocket is working
            using_websocket = (
                self.websocket_order_status_service and 
                self.websocket_order_status_service.is_connected()
            )
            
            if using_websocket:
                if self._websocket_fallback_logged:
                    logger.info("✓ WebSocket order status service is working again - using real-time updates")
                    self._websocket_fallback_logged = False
            else:
                # Fallback to HTTP polling
                if not self._websocket_fallback_logged:
                    logger.warning("⚠️ Falling back to HTTP polling for order status (WebSocket unavailable)")
                    self._websocket_fallback_logged = True
            
            # Always do HTTP check as backup (even if WebSocket is working)
            # WebSocket callbacks handle real-time updates, but HTTP ensures we don't miss anything
            if position.yes_order_id and not position.yes_filled:
                yes_status = self.pm.get_order_status(position.yes_order_id)
            
            if position.no_order_id and not position.no_filled:
                no_status = self.pm.get_order_status(position.no_order_id)
            
            # Parse order statuses and detect fills
            if yes_status:
                yes_status_str, yes_filled_amount, yes_total_amount = parse_order_status(yes_status)
                
                # Update order status in database
                self._update_order_status_in_db(position, "YES", yes_status_str, yes_filled_amount, yes_total_amount, yes_status)
                
                if is_order_filled(yes_status_str, yes_filled_amount, yes_total_amount):
                    if not position.yes_filled:
                        position.yes_filled = True
                        position.yes_fill_time = datetime.now(timezone.utc)
                        
                        # Extract fill price if available (YES is a sell order)
                        yes_fill_price = self._extract_fill_price(yes_status, position.yes_order_price, is_sell_order=True)
                        
                        logger.info(
                            f"✅ YES order filled for {market_slug}: "
                            f"{yes_filled_amount:.2f}/{yes_total_amount:.2f} shares @ ${yes_fill_price:.4f}"
                        )
                        
                        # Track which side filled first (if this is the first fill)
                        if not position.no_filled:
                            # YES filled first, NO hasn't filled yet
                            logger.info(f"📊 Fill timing: YES filled first (NO still pending)")
                            self._update_fill_timing_in_db(position, "YES", None, None)
                        else:
                            # NO already filled - calculate time difference
                            time_diff = (position.yes_fill_time - position.no_fill_time).total_seconds()
                            logger.info(
                                f"📊 Fill timing: NO filled first, YES filled {time_diff:.2f}s later"
                            )
                            self._update_fill_timing_in_db(position, "NO", "YES", time_diff)
                        
                        # Initialize last_adjustment_time if this is the first side to fill
                        if position.last_adjustment_time is None:
                            position.last_adjustment_time = position.yes_fill_time
                        
                        # Update database with fill details
                        self._update_fill_details_in_db(position, "YES", yes_filled_amount, yes_fill_price)
            
            if no_status:
                no_status_str, no_filled_amount, no_total_amount = parse_order_status(no_status)
                
                # Update order status in database
                self._update_order_status_in_db(position, "NO", no_status_str, no_filled_amount, no_total_amount, no_status)
                
                if is_order_filled(no_status_str, no_filled_amount, no_total_amount):
                    if not position.no_filled:
                        position.no_filled = True
                        position.no_fill_time = datetime.now(timezone.utc)
                        
                        # Extract fill price if available (NO is a sell order)
                        no_fill_price = self._extract_fill_price(no_status, position.no_order_price, is_sell_order=True)
                        
                        logger.info(
                            f"✅ NO order filled for {market_slug}: "
                            f"{no_filled_amount:.2f}/{no_total_amount:.2f} shares @ ${no_fill_price:.4f}"
                        )
                        
                        # Track which side filled first (if this is the first fill)
                        if not position.yes_filled:
                            # NO filled first, YES hasn't filled yet
                            logger.info(f"📊 Fill timing: NO filled first (YES still pending)")
                            self._update_fill_timing_in_db(position, "NO", None, None)
                        else:
                            # YES already filled - calculate time difference
                            time_diff = (position.no_fill_time - position.yes_fill_time).total_seconds()
                            logger.info(
                                f"📊 Fill timing: YES filled first, NO filled {time_diff:.2f}s later"
                            )
                            self._update_fill_timing_in_db(position, "YES", "NO", time_diff)
                        
                        # Initialize last_adjustment_time if this is the first side to fill
                        if position.last_adjustment_time is None:
                            position.last_adjustment_time = position.no_fill_time
                        
                        # Update database with fill details
                        self._update_fill_details_in_db(position, "NO", no_filled_amount, no_fill_price)
            
            # Check if both sides filled
            if position.yes_filled and position.no_filled:
                logger.info(f"Both sides filled for {market_slug}, ready to split again")
                await self._handle_both_filled(position)
                return
            
            # Handle merged state: waiting to re-split
            if position.merged_waiting_resplit:
                await self._check_resplit_ready(position)
                return
            
            # Handle imbalanced fill: one side filled, other hasn't
            # Check if wait_after_fill time has passed since last adjustment (or first fill)
            if (position.yes_filled and not position.no_filled) or (position.no_filled and not position.yes_filled):
                await self._check_and_adjust_if_needed(position)
                return
            
            # Handle neither side filled: check if we should adjust both prices or merge
            if not position.yes_filled and not position.no_filled:
                await self._check_neither_fills_and_adjust(position)
                
        except Exception as e:
            logger.error(f"Error processing position {market_slug}: {e}", exc_info=True)
    
    async def _check_and_adjust_if_needed(self, position: MarketMakerPosition):
        """Check if wait_after_fill time has passed and adjust if needed."""
        try:
            # Determine which side filled and which didn't
            if position.yes_filled and not position.no_filled:
                filled_side = "YES"
                unfilled_side = "NO"
                fill_time = position.yes_fill_time
            elif position.no_filled and not position.yes_filled:
                filled_side = "NO"
                unfilled_side = "YES"
                fill_time = position.no_fill_time
            else:
                # Both filled or neither filled - nothing to do
                return
            
            # Use last_adjustment_time if we've already adjusted, otherwise use fill_time
            reference_time = position.last_adjustment_time if position.last_adjustment_time else fill_time
            
            if reference_time is None:
                return
            
            # Calculate time since last adjustment (or first fill)
            time_since_reference = (datetime.now(timezone.utc) - reference_time).total_seconds()
            
            # Check if wait_after_fill time has passed
            if time_since_reference >= self.config.wait_after_fill:
                # Check if other side filled (double-check before adjusting)
                other_order_id = position.no_order_id if unfilled_side == "NO" else position.yes_order_id
                
                if other_order_id:
                    other_status = self.pm.get_order_status(other_order_id)
                    if other_status:
                        other_status_str, other_filled_amount, other_total_amount = parse_order_status(other_status)
                        if is_order_filled(other_status_str, other_filled_amount, other_total_amount):
                            # Other side filled! Update status
                            if unfilled_side == "YES":
                                position.yes_filled = True
                                position.yes_fill_time = datetime.now(timezone.utc)
                                
                                # Calculate time between fills
                                if position.no_fill_time:
                                    time_diff = (position.yes_fill_time - position.no_fill_time).total_seconds()
                                    logger.info(
                                        f"📊 Fill timing: NO filled first, YES filled {time_diff:.2f}s later"
                                    )
                                    self._update_fill_timing_in_db(position, "NO", "YES", time_diff)
                            else:
                                position.no_filled = True
                                position.no_fill_time = datetime.now(timezone.utc)
                                
                                # Calculate time between fills
                                if position.yes_fill_time:
                                    time_diff = (position.no_fill_time - position.yes_fill_time).total_seconds()
                                    logger.info(
                                        f"📊 Fill timing: YES filled first, NO filled {time_diff:.2f}s later"
                                    )
                                    self._update_fill_timing_in_db(position, "YES", "NO", time_diff)
                            
                            logger.info(f"✅ Other side ({unfilled_side}) filled!")
                            await self._handle_both_filled(position)
                            return
                
                # Other side still didn't fill - adjust price
                logger.info(
                    f"{unfilled_side} side did not fill after {time_since_reference:.1f}s "
                    f"(wait_after_fill={self.config.wait_after_fill:.1f}s), "
                    f"adjusting price by -{self.config.price_step:.4f}"
                )
                
                await self._adjust_unfilled_side(position, unfilled_side)
            
        except Exception as e:
            logger.error(f"Error checking and adjusting: {e}", exc_info=True)
    
    async def _check_neither_fills_and_adjust(self, position: MarketMakerPosition):
        """Check if neither side has filled and adjust both prices or merge."""
        try:
            # Need orders_placed_time to know when to check
            if not position.orders_placed_time:
                return
            
            # Check if we've exceeded max iterations
            if position.neither_fills_iteration_count >= self.config.max_iterations_neither_fills:
                logger.warning(
                    f"Max iterations ({self.config.max_iterations_neither_fills}) reached for {position.market_slug} "
                    f"with neither side filling. Stopping adjustments."
                )
                return
            
            # Calculate time since orders were placed (or last adjustment)
            reference_time = position.last_adjustment_time if position.last_adjustment_time else position.orders_placed_time
            time_since_reference = (datetime.now(timezone.utc) - reference_time).total_seconds()
            
            # Check if wait_if_neither_fills time has passed
            if time_since_reference >= self.config.wait_if_neither_fills:
                # Check current prices
                yes_price = position.yes_order_price or 0.0
                no_price = position.no_order_price or 0.0
                price_sum = yes_price + no_price
                
                logger.info(
                    f"🔍 Neither fills check for {position.market_slug}: "
                    f"YES price={yes_price:.4f}, NO price={no_price:.4f}, "
                    f"sum={price_sum:.4f}, threshold={self.config.merge_threshold:.4f}"
                )
                
                # Check if we should merge (price sum <= threshold)
                if price_sum <= self.config.merge_threshold:
                    logger.info(
                        f"Price sum ({price_sum:.4f}) <= merge_threshold ({self.config.merge_threshold:.4f}) "
                        f"for {position.market_slug}. Merging orders and will re-split."
                    )
                    await self._merge_and_wait_resplit(position)
                else:
                    # Adjust both prices down by price_step
                    logger.info(
                        f"Neither side filled after {time_since_reference:.1f}s "
                        f"(wait_if_neither_fills={self.config.wait_if_neither_fills:.1f}s). "
                        f"Adjusting both prices by -{self.config.price_step:.4f}"
                    )
                    await self._adjust_both_prices(position)
            
        except Exception as e:
            logger.error(f"Error checking neither fills: {e}", exc_info=True)
    
    async def _check_resplit_ready(self, position: MarketMakerPosition):
        """Check if it's time to re-split after merging."""
        try:
            if not position.merged_at:
                # Reset merged state if merged_at is missing
                position.merged_waiting_resplit = False
                return
            
            time_since_merge = (datetime.now(timezone.utc) - position.merged_at).total_seconds()
            
            if time_since_merge >= self.config.wait_before_resplit:
                logger.info(
                    f"Wait time ({self.config.wait_before_resplit:.1f}s) elapsed since merge. "
                    f"Re-splitting for {position.market_slug}..."
                )
                
                # Reset merged state
                position.merged_waiting_resplit = False
                position.merged_at = None
                position.neither_fills_iteration_count = 0
                position.last_adjustment_time = None
                
                # Re-split
                split_result = await self._split_position(position.condition_id)
                if split_result:
                    # Update shares (should be same as split_amount)
                    position.yes_shares = self.config.split_amount
                    position.no_shares = self.config.split_amount
                    position.split_transaction_hash = split_result.get("transaction_hash")
                    
                    # Place new sell orders
                    await self._place_sell_orders(position)
                    
                    # Update database
                    self._update_position_in_db(position)
                else:
                    logger.error(f"Failed to re-split for {position.market_slug}")
                    position.merged_waiting_resplit = True  # Keep in merged state to retry
            
        except Exception as e:
            logger.error(f"Error checking resplit ready: {e}", exc_info=True)
    
    async def _adjust_both_prices(self, position: MarketMakerPosition):
        """Cancel both orders and place new ones at lower prices (both reduced by price_step) using batch operations."""
        try:
            if not position.yes_order_id or not position.no_order_id:
                logger.warning(f"Cannot adjust both prices: missing order IDs for {position.market_slug}")
                return
            
            # Calculate new prices (both reduced by price_step)
            new_yes_price = max(0.01, (position.yes_order_price or 0.0) - self.config.price_step)
            new_no_price = max(0.01, (position.no_order_price or 0.0) - self.config.price_step)
            
            logger.info(
                f"Adjusting both prices for {position.market_slug}: "
                f"YES {position.yes_order_price:.4f} → {new_yes_price:.4f}, "
                f"NO {position.no_order_price:.4f} → {new_no_price:.4f}"
            )
            
            # Batch cancel both orders
            loop = asyncio.get_event_loop()
            cancel_result = await loop.run_in_executor(
                None,
                self.pm.cancel_orders_batch,
                [position.yes_order_id, position.no_order_id]
            )
            
            if cancel_result:
                logger.info(f"✅ Batch cancelled both orders for {position.market_slug}")
            else:
                logger.error(f"❌ Batch cancel failed for {position.market_slug} - cancel_result is None or empty")
                logger.warning(f"⚠️ Cannot proceed with price adjustment - orders may still be active")
                return
            
            # Batch place new orders at adjusted prices
            orders_to_place = [
                {
                    "price": new_yes_price,
                    "size": position.yes_shares,
                    "side": "SELL",
                    "token_id": position.yes_token_id,
                },
                {
                    "price": new_no_price,
                    "size": position.no_shares,
                    "side": "SELL",
                    "token_id": position.no_token_id,
                }
            ]
            
            place_result = await loop.run_in_executor(
                None,
                self.pm.place_orders_batch,
                orders_to_place
            )
            
            if place_result:
                # Extract order IDs from batch response
                # The response structure may vary, so we need to handle it
                if isinstance(place_result, dict):
                    # Try to extract order IDs from response
                    # Response might be a list or dict with results
                    results = place_result.get("results", place_result.get("data", []))
                    if isinstance(results, list) and len(results) >= 2:
                        yes_order_id = self.pm.extract_order_id(results[0])
                        no_order_id = self.pm.extract_order_id(results[1])
                        
                        if yes_order_id:
                            position.yes_order_id = yes_order_id
                            position.yes_order_price = new_yes_price
                            logger.info(f"✅ Placed adjusted YES sell order: {yes_order_id} @ ${new_yes_price:.4f}")
                        else:
                            logger.warning(f"⚠️ Could not extract YES order ID from batch response: {results[0]}")
                        
                        if no_order_id:
                            position.no_order_id = no_order_id
                            position.no_order_price = new_no_price
                            logger.info(f"✅ Placed adjusted NO sell order: {no_order_id} @ ${new_no_price:.4f}")
                        else:
                            logger.warning(f"⚠️ Could not extract NO order ID from batch response: {results[1]}")
                    else:
                        logger.warning(f"⚠️ Unexpected batch place response format - expected list with 2+ items, got: {place_result}")
                        logger.warning(f"   Response type: {type(results) if 'results' in locals() else 'unknown'}, length: {len(results) if isinstance(results, list) else 'N/A'}")
                else:
                    logger.warning(f"⚠️ Unexpected batch place response type - expected dict, got: {type(place_result)}")
                    logger.warning(f"   Response: {place_result}")
            else:
                logger.error(f"❌ Batch order placement failed for {position.market_slug} - place_result is None or empty")
                logger.warning(f"⚠️ Orders were cancelled but new orders were not placed - position may be in inconsistent state")
            
            # Update tracking
            position.neither_fills_iteration_count += 1
            position.last_adjustment_time = datetime.now(timezone.utc)
            position.orders_placed_time = datetime.now(timezone.utc)  # Reset timer
            
            # Update database
            self._update_position_in_db(position)
            
        except Exception as e:
            logger.error(f"Error adjusting both prices: {e}", exc_info=True)
    
    async def _merge_and_wait_resplit(self, position: MarketMakerPosition):
        """Cancel both orders and set merged state to wait for re-split using batch cancel."""
        try:
            if not position.yes_order_id or not position.no_order_id:
                logger.warning(f"Cannot merge: missing order IDs for {position.market_slug}")
                return
            
            logger.info(f"Merging orders for {position.market_slug} (batch cancelling both orders)")
            
            # Batch cancel both orders
            loop = asyncio.get_event_loop()
            cancel_result = await loop.run_in_executor(
                None,
                self.pm.cancel_orders_batch,
                [position.yes_order_id, position.no_order_id]
            )
            
            if cancel_result:
                logger.info(f"✅ Batch cancelled both orders for {position.market_slug}")
            else:
                logger.error(f"❌ Batch cancel failed during merge for {position.market_slug} - cancel_result is None or empty")
                logger.warning(f"⚠️ Orders may still be active - merge state may be inconsistent")
            
            # Clear order IDs and prices
            position.yes_order_id = None
            position.no_order_id = None
            position.yes_order_price = None
            position.no_order_price = None
            
            # Set merged state
            position.merged_waiting_resplit = True
            position.merged_at = datetime.now(timezone.utc)
            
            logger.info(
                f"✅ Merged orders for {position.market_slug}. "
                f"Will re-split after {self.config.wait_before_resplit:.1f} seconds."
            )
            
            # Update database
            self._update_position_in_db(position)
            
        except Exception as e:
            logger.error(f"Error merging orders: {e}", exc_info=True)
    
    async def _adjust_unfilled_side(self, position: MarketMakerPosition, side: str):
        """Cancel unfilled order and place new order at lower price."""
        try:
            if position.adjustment_count >= position.max_adjustments:
                logger.warning(
                    f"Max adjustments ({position.max_adjustments}) reached for {position.market_slug}, "
                    f"stopping adjustments"
                )
                return
            
            # Get order ID and current price
            if side == "YES":
                order_id = position.yes_order_id
                current_price = position.yes_order_price
                token_id = position.yes_token_id
                shares = position.yes_shares
            else:
                order_id = position.no_order_id
                current_price = position.no_order_price
                token_id = position.no_token_id
                shares = position.no_shares
            
            if not order_id or current_price is None:
                logger.error(f"Cannot adjust {side} side - missing order info")
                return
            
            # Check order status before cancelling
            current_status = self.pm.get_order_status(order_id)
            if current_status:
                status_str, filled_amount, total_amount = parse_order_status(current_status)
                
                # If already filled, update position and return
                if is_order_filled(status_str, filled_amount, total_amount):
                    logger.info(f"{side} order {order_id} already filled, no need to cancel")
                    if side == "YES":
                        position.yes_filled = True
                        position.yes_fill_time = datetime.now(timezone.utc)
                        fill_price = self._extract_fill_price(current_status, position.yes_order_price, is_sell_order=True)
                        self._update_fill_details_in_db(position, "YES", filled_amount, fill_price)
                    else:
                        position.no_filled = True
                        position.no_fill_time = datetime.now(timezone.utc)
                        fill_price = self._extract_fill_price(current_status, position.no_order_price, is_sell_order=True)
                        self._update_fill_details_in_db(position, "NO", filled_amount, fill_price)
                    return
                
                # If already cancelled, just update status and continue
                if is_order_cancelled(status_str):
                    logger.info(f"{side} order {order_id} already cancelled")
                    self._update_order_status_in_db(position, side, "cancelled", filled_amount, total_amount, current_status)
                    # Continue to place new order
            
            # Cancel existing order
            logger.info(f"Cancelling {side} order {order_id}")
            try:
                cancel_result = self.pm.cancel_order(order_id)
                
                if cancel_result:
                    logger.info(f"✅ Cancelled {side} order")
                    # Update status - use order size as total_amount
                    total_shares = position.yes_shares if side == "YES" else position.no_shares
                    self._update_order_status_in_db(position, side, "cancelled", 0, total_shares, None)
                else:
                    logger.warning(
                        f"⚠️ Cancel order returned None/False for {side} order {order_id}. "
                        f"Order may already be filled/cancelled. Will attempt to place new order anyway."
                    )
                    # Continue anyway - order might already be cancelled/filled
            except Exception as e:
                logger.warning(
                    f"⚠️ Exception cancelling {side} order {order_id}: {e}. "
                    f"Will attempt to place new order anyway (order may already be cancelled/filled)."
                )
                # Continue anyway - might be a transient error
            
            # Calculate new price
            new_price = current_price - self.config.price_step
            
            # Ensure price is valid (between 0.01 and 0.99)
            new_price = max(0.01, min(new_price, 0.99))
            
            # Check actual wallet balance before placing new order
            # Use direct wallet address (where shares are)
            direct_wallet_address = self.pm.get_address_for_private_key()
            logger.info(f"🔍 Verifying {side} shares are still available before placing new order...")
            actual_balance = self.pm.get_conditional_token_balance(
                token_id,
                wallet_address=direct_wallet_address
            )
            
            if actual_balance is None:
                logger.error(f"❌ Could not check {side} balance - aborting order placement")
                return
            
            if actual_balance == 0:
                logger.warning(
                    f"⚠️ No {side} shares remaining in wallet (balance: 0). "
                    f"Order may have already filled or shares were moved. Aborting order placement."
                )
                # Mark as filled if balance is 0
                if side == "YES":
                    position.yes_filled = True
                    position.yes_fill_time = datetime.now(timezone.utc)
                else:
                    position.no_filled = True
                    position.no_fill_time = datetime.now(timezone.utc)
                return
            
            # Use actual balance (remaining shares), not original split amount
            remaining_shares = actual_balance
            if remaining_shares < shares:
                logger.info(
                    f"📊 Using remaining balance: {remaining_shares:.2f} shares "
                    f"(original was {shares:.2f}, {shares - remaining_shares:.2f} already sold)"
                )
                shares = remaining_shares
            
            # Verify allowances are still set
            logger.info(f"🔍 Verifying conditional token allowances before placing new order...")
            allowances_ok = self.pm.ensure_conditional_token_allowances(wallet_address=direct_wallet_address)
            if not allowances_ok:
                logger.error(
                    f"❌ Conditional token allowances NOT set for direct wallet. "
                    f"Cannot place new order. Please check allowances."
                )
                return
            
            logger.info(f"Placing new {side} sell order at {new_price:.4f} (was {current_price:.4f})")
            logger.info(f"  Order size: {shares:.2f} shares (remaining balance: {actual_balance:.2f})")
            
            # Place new order
            order_response = self.pm.execute_order(
                price=new_price,
                size=shares,
                side="SELL",
                token_id=token_id,
            )
            
            if order_response:
                new_order_id = self.pm.extract_order_id(order_response)
                if new_order_id:
                    if side == "YES":
                        position.yes_order_id = new_order_id
                        position.yes_order_price = new_price
                    else:
                        position.no_order_id = new_order_id
                        position.no_order_price = new_price
                    
                    position.adjustment_count += 1
                    position.last_adjustment_time = datetime.now(timezone.utc)  # Update adjustment time
                    logger.info(f"✅ Placed new {side} order: {new_order_id} (adjustment #{position.adjustment_count})")
                    self._update_position_in_db(position)
            
        except Exception as e:
            logger.error(f"Error adjusting unfilled side: {e}", exc_info=True)
    
    async def _handle_both_filled(self, position: MarketMakerPosition):
        """Handle when both sides are filled - ready to split again."""
        try:
            logger.info(f"Both sides filled for {position.market_slug}, closing position")
            
            # Save market info before closing (we'll need it to potentially start a new position)
            market = position.market
            market_slug = position.market_slug
            condition_id = position.condition_id
            yes_token_id = position.yes_token_id
            no_token_id = position.no_token_id
            
            # Close position (mark as both_filled)
            await self._close_position(position, reason="both_filled")
            
            # Immediately check if we can start a new position for the same market
            # (Don't wait for the detection loop - if market is still good, split again now)
            if is_market_active(market):
                minutes_remaining = get_minutes_until_resolution(market)
                
                if minutes_remaining is not None:
                    # Check if market still meets criteria for new positions
                    can_start = True
                    
                    # Check max_minutes_before_resolution (upper limit)
                    if self.config.max_minutes_before_resolution is not None:
                        if minutes_remaining > self.config.max_minutes_before_resolution:
                            can_start = False
                            logger.info(
                                f"Market {market_slug} has {minutes_remaining:.1f} minutes remaining "
                                f"(exceeds max_minutes_before_resolution={self.config.max_minutes_before_resolution:.1f}), "
                                f"skipping immediate re-split"
                            )
                    
                    # Check min_minutes_before_resolution (lower limit)
                    if can_start and self.config.min_minutes_before_resolution is not None:
                        if minutes_remaining < self.config.min_minutes_before_resolution:
                            can_start = False
                            logger.info(
                                f"Market {market_slug} has {minutes_remaining:.1f} minutes remaining "
                                f"(less than min_minutes_before_resolution={self.config.min_minutes_before_resolution:.1f}), "
                                f"skipping immediate re-split"
                            )
                    
                    if can_start:
                        logger.info(
                            f"Market {market_slug} still active with {minutes_remaining:.1f} minutes remaining - "
                            f"starting new position immediately"
                        )
                        success = await self._start_market_making(
                            market, market_slug, condition_id, yes_token_id, no_token_id
                        )
                        if success:
                            logger.info(f"✅ Immediately started new position for {market_slug} after both orders filled")
                        else:
                            logger.warning(f"Failed to immediately start new position for {market_slug}, will retry via detection loop")
                    else:
                        logger.info(f"Market {market_slug} no longer meets criteria for new positions")
                else:
                    logger.warning(f"Could not determine time remaining for {market_slug}, skipping immediate re-split")
            else:
                logger.info(f"Market {market_slug} is no longer active, skipping immediate re-split")
            
        except Exception as e:
            logger.error(f"Error handling both filled: {e}", exc_info=True)
    
    async def _track_orderbook_prices_near_resolution(self):
        """Track orderbook prices for markets approaching resolution."""
        # Only track if min_minutes_before_resolution is configured
        if self.config.min_minutes_before_resolution is None:
            return
        
        for market_slug, position in list(self.active_positions.items()):
            minutes_remaining = get_minutes_until_resolution(position.market)
            
            # Track prices if market is within min_minutes_before_resolution
            if minutes_remaining is not None and minutes_remaining <= self.config.min_minutes_before_resolution:
                try:
                    yes_orderbook = fetch_orderbook(position.yes_token_id)
                    no_orderbook = fetch_orderbook(position.no_token_id)
                    
                    if yes_orderbook and no_orderbook:
                        yes_highest_bid = get_highest_bid(yes_orderbook)
                        no_highest_bid = get_highest_bid(no_orderbook)
                        
                        # Store prices if we got valid values
                        if yes_highest_bid is not None and no_highest_bid is not None:
                            self.last_orderbook_prices[market_slug] = {
                                "yes_highest_bid": yes_highest_bid,
                                "no_highest_bid": no_highest_bid,
                                "timestamp": datetime.now(timezone.utc),
                            }
                            logger.debug(
                                f"Tracked orderbook prices for {market_slug}: "
                                f"YES bid={yes_highest_bid:.4f}, NO bid={no_highest_bid:.4f}"
                            )
                except Exception as e:
                    logger.debug(f"Error tracking orderbook prices for {market_slug}: {e}")
            else:
                # Remove from tracking if market is no longer near resolution
                self.last_orderbook_prices.pop(market_slug, None)
    
    async def _check_market_resolution(self, market_slug: str, position: MarketMakerPosition):
        """Check if market has resolved and handle resolution."""
        try:
            # Check if market is still active
            if is_market_active(position.market):
                return  # Market still active, no resolution yet
            
            # Market has resolved - process resolution
            logger.info(f"Market {market_slug} has resolved, processing resolution...")
            
            # Get last orderbook prices before resolution
            last_prices = self.last_orderbook_prices.get(market_slug)
            
            if last_prices:
                yes_highest_bid = last_prices.get("yes_highest_bid")
                no_highest_bid = last_prices.get("no_highest_bid")
                
                logger.info(
                    f"Using last orderbook prices before resolution: "
                    f"YES highest_bid={yes_highest_bid:.4f}, NO highest_bid={no_highest_bid:.4f}"
                )
            else:
                # Fallback: fetch current orderbook (may be closed, but worth trying)
                logger.warning(f"No tracked orderbook prices for {market_slug}, fetching current orderbook...")
                yes_orderbook = fetch_orderbook(position.yes_token_id)
                no_orderbook = fetch_orderbook(position.no_token_id)
                
                if yes_orderbook and no_orderbook:
                    yes_highest_bid = get_highest_bid(yes_orderbook)
                    no_highest_bid = get_highest_bid(no_orderbook)
                else:
                    yes_highest_bid = None
                    no_highest_bid = None
            
            # Determine winning side from highest bids
            # Rule: highest_bid >= 0.98 means that side won
            winning_side = None
            if yes_highest_bid is not None and no_highest_bid is not None:
                if yes_highest_bid >= 0.98:
                    winning_side = "YES"
                    logger.info(f"✅ YES won (YES highest_bid={yes_highest_bid:.4f} ≥ 0.98)")
                elif no_highest_bid >= 0.98:
                    winning_side = "NO"
                    logger.info(f"✅ NO won (NO highest_bid={no_highest_bid:.4f} ≥ 0.98)")
                else:
                    # Inconclusive - use higher bid as tiebreaker
                    if yes_highest_bid > no_highest_bid:
                        winning_side = "YES"
                        logger.info(f"✅ YES won (YES bid {yes_highest_bid:.4f} > NO bid {no_highest_bid:.4f})")
                    else:
                        winning_side = "NO"
                        logger.info(f"✅ NO won (NO bid {no_highest_bid:.4f} > YES bid {yes_highest_bid:.4f})")
            else:
                logger.warning(f"Could not determine winning side from orderbook prices")
                # Default to YES if we can't determine (conservative)
                winning_side = "YES"
            
            # Calculate payout for remaining shares
            await self._calculate_resolution_payout(position, winning_side, yes_highest_bid, no_highest_bid)
            
            # Automatically redeem winning shares
            await self._redeem_winning_shares(position, winning_side)
            
            # Close position
            await self._close_position(position, reason="resolved")
            
        except Exception as e:
            logger.error(f"Error checking market resolution for {market_slug}: {e}", exc_info=True)
    
    async def _calculate_resolution_payout(
        self,
        position: MarketMakerPosition,
        winning_side: str,
        yes_highest_bid: Optional[float],
        no_highest_bid: Optional[float]
    ):
        """Calculate payout for remaining shares after market resolution."""
        try:
            # Calculate remaining shares (shares that weren't sold)
            yes_remaining = position.yes_shares if not position.yes_filled else 0.0
            no_remaining = position.no_shares if not position.no_filled else 0.0
            
            # Value remaining shares:
            # - Winning side: $1 per share
            # - Losing side: $0 per share
            if winning_side == "YES":
                yes_payout = yes_remaining * 1.0  # $1 per share
                no_payout = no_remaining * 0.0    # $0 per share
            else:  # NO won
                yes_payout = yes_remaining * 0.0  # $0 per share
                no_payout = no_remaining * 1.0     # $1 per share
            
            total_payout = yes_payout + no_payout
            
            # Calculate net payout (payout - split_amount - fees)
            # Note: We don't track fees separately, so net_payout = total_payout - split_amount
            net_payout = total_payout - position.split_amount
            
            # Calculate ROI
            roi = (net_payout / position.split_amount) if position.split_amount > 0 else 0.0
            
            logger.info(
                f"Resolution payout for {position.market_slug}: "
                f"winning_side={winning_side}, "
                f"yes_remaining={yes_remaining:.2f}, no_remaining={no_remaining:.2f}, "
                f"yes_payout=${yes_payout:.2f}, no_payout=${no_payout:.2f}, "
                f"total_payout=${total_payout:.2f}, net_payout=${net_payout:.2f}, "
                f"ROI={roi*100:.2f}%"
            )
            
            # Update database with resolution info
            if position.db_position_id:
                session = self.db.SessionLocal()
                try:
                    db_position = session.query(RealMarketMakerPosition).filter(
                        RealMarketMakerPosition.id == position.db_position_id
                    ).first()
                    
                    if db_position:
                        db_position.winning_side = winning_side
                        db_position.outcome_price_yes = 1.0 if winning_side == "YES" else 0.0
                        db_position.outcome_price_no = 1.0 if winning_side == "NO" else 0.0
                        db_position.total_payout = total_payout
                        db_position.net_payout = net_payout
                        db_position.roi = roi
                        db_position.market_resolved_at = datetime.now(timezone.utc)
                        db_position.position_status = "resolved"
                        db_position.updated_at = datetime.now(timezone.utc)
                        
                        session.commit()
                        logger.info(f"Updated database with resolution info for position {position.db_position_id}")
                        
                finally:
                    session.close()
                    
        except Exception as e:
            logger.error(f"Error calculating resolution payout: {e}", exc_info=True)
    
    async def _redeem_winning_shares(self, position: MarketMakerPosition, winning_side: str):
        """
        Automatically redeem winning shares after market resolution.
        
        Strategy (only after market resolution):
        1. If both YES and NO shares remain in equal amounts: merge them back to USDC
        2. If only winning side shares remain: sell them at $0.99 (should fill immediately after resolution)
        3. If only losing side shares remain: do nothing (worthless)
        
        Note: This is ONLY called after market resolution. We don't merge during active market making
        because the purpose of splitting is to sell each side separately.
        """
        try:
            # Check ACTUAL wallet balances (more reliable than position object)
            direct_wallet = self.pm.get_address_for_private_key()
            yes_balance = self.pm.get_conditional_token_balance(
                position.yes_token_id,
                wallet_address=direct_wallet
            )
            no_balance = self.pm.get_conditional_token_balance(
                position.no_token_id,
                wallet_address=direct_wallet
            )
            
            if yes_balance is None or no_balance is None:
                logger.warning(f"Could not check wallet balances for {position.market_slug}, using position object values")
                # Fallback to position object values
                yes_remaining = position.yes_shares if not position.yes_filled else 0.0
                no_remaining = position.no_shares if not position.no_filled else 0.0
            else:
                # Use actual wallet balances
                yes_remaining = yes_balance
                no_remaining = no_balance
            
            logger.info(
                f"🔄 Attempting to redeem shares for {position.market_slug} (AFTER RESOLUTION): "
                f"winning_side={winning_side}, yes_remaining={yes_remaining:.2f}, no_remaining={no_remaining:.2f}"
            )
            
            # Case 1: Both sides remain unsold - merge equal amounts back to USDC
            # This only happens if neither side sold during market making
            if yes_remaining > 0 and no_remaining > 0:
                merge_amount = min(yes_remaining, no_remaining)
                if abs(yes_remaining - no_remaining) < 0.01:  # Essentially equal
                    logger.info(
                        f"💰 Both sides remain unsold in equal amounts ({merge_amount:.2f}). "
                        f"Merging back to ${merge_amount:.2f} USDC..."
                    )
                    loop = asyncio.get_event_loop()
                    merge_result = await loop.run_in_executor(
                        None,
                        self.pm.merge_positions,
                        position.condition_id,
                        merge_amount
                    )
                    if merge_result:
                        logger.info(f"✅ Successfully merged {merge_amount:.2f} YES + NO → ${merge_amount:.2f} USDC")
                        # Update remaining shares
                        yes_remaining -= merge_amount
                        no_remaining -= merge_amount
                    else:
                        logger.error(f"❌ Failed to merge positions")
                else:
                    logger.info(
                        f"⚠️ Unequal amounts (YES: {yes_remaining:.2f}, NO: {no_remaining:.2f}). "
                        f"Will merge {merge_amount:.2f} and handle remainder separately."
                    )
                    loop = asyncio.get_event_loop()
                    merge_result = await loop.run_in_executor(
                        None,
                        self.pm.merge_positions,
                        position.condition_id,
                        merge_amount
                    )
                    if merge_result:
                        logger.info(f"✅ Successfully merged {merge_amount:.2f} YES + NO → ${merge_amount:.2f} USDC")
                        yes_remaining -= merge_amount
                        no_remaining -= merge_amount
            
            # Case 2: Only winning side remains unsold - sell at $0.99 (should fill immediately)
            if winning_side == "YES" and yes_remaining > 0:
                logger.info(
                    f"💰 YES won - selling {yes_remaining:.2f} YES shares at $0.99 "
                    f"(should fill immediately after resolution)..."
                )
                try:
                    # Place sell order at $0.99 (just below $1.00 to ensure fill)
                    loop = asyncio.get_event_loop()
                    order_result = await loop.run_in_executor(
                        None,
                        self.pm.execute_order,
                        price=0.99,
                        size=yes_remaining,
                        side="SELL",
                        token_id=position.yes_token_id
                    )
                    if order_result and order_result.get("order_id"):
                        logger.info(f"✅ Placed sell order for {yes_remaining:.2f} YES shares: {order_result.get('order_id')}")
                    else:
                        logger.warning(f"⚠️ Failed to place sell order for YES shares")
                except Exception as e:
                    logger.error(f"❌ Error selling YES shares: {e}", exc_info=True)
            
            elif winning_side == "NO" and no_remaining > 0:
                logger.info(
                    f"💰 NO won - selling {no_remaining:.2f} NO shares at $0.99 "
                    f"(should fill immediately after resolution)..."
                )
                try:
                    # Place sell order at $0.99 (just below $1.00 to ensure fill)
                    loop = asyncio.get_event_loop()
                    order_result = await loop.run_in_executor(
                        None,
                        self.pm.execute_order,
                        price=0.99,
                        size=no_remaining,
                        side="SELL",
                        token_id=position.no_token_id
                    )
                    if order_result and order_result.get("order_id"):
                        logger.info(f"✅ Placed sell order for {no_remaining:.2f} NO shares: {order_result.get('order_id')}")
                    else:
                        logger.warning(f"⚠️ Failed to place sell order for NO shares")
                except Exception as e:
                    logger.error(f"❌ Error selling NO shares: {e}", exc_info=True)
            
            # Case 3: Only losing side remains - do nothing (worthless)
            if (winning_side == "YES" and no_remaining > 0) or (winning_side == "NO" and yes_remaining > 0):
                losing_shares = no_remaining if winning_side == "YES" else yes_remaining
                logger.info(
                    f"💔 Losing side shares remain ({losing_shares:.2f} {'NO' if winning_side == 'YES' else 'YES'}), "
                    f"but they're worthless. No action needed."
                )
            
            if yes_remaining == 0 and no_remaining == 0:
                logger.info(f"✅ All shares redeemed for {position.market_slug}")
                
        except Exception as e:
            logger.error(f"Error redeeming winning shares: {e}", exc_info=True)
    
    async def _close_position(self, position: MarketMakerPosition, reason: str):
        """Close a position."""
        try:
            # Cancel any remaining open orders
            if not position.yes_filled and position.yes_order_id:
                try:
                    logger.info(f"Cancelling remaining YES order {position.yes_order_id}")
                    self.pm.cancel_order(position.yes_order_id)
                except Exception as e:
                    logger.warning(f"Error cancelling YES order: {e}")
            
            if not position.no_filled and position.no_order_id:
                try:
                    logger.info(f"Cancelling remaining NO order {position.no_order_id}")
                    self.pm.cancel_order(position.no_order_id)
                except Exception as e:
                    logger.warning(f"Error cancelling NO order: {e}")
            
            # Update database
            if position.db_position_id:
                session = self.db.SessionLocal()
                try:
                    db_position = session.query(RealMarketMakerPosition).filter(
                        RealMarketMakerPosition.id == position.db_position_id
                    ).first()
                    
                    if db_position:
                        db_position.position_status = reason
                        db_position.updated_at = datetime.now(timezone.utc)
                        session.commit()
                        
                finally:
                    session.close()
            
            # Remove from active positions
            if position.market_slug in self.active_positions:
                del self.active_positions[position.market_slug]
            
            if position.market_slug in self.monitored_markets:
                self.monitored_markets.remove(position.market_slug)
            
            # Remove from orderbook price tracking
            self.last_orderbook_prices.pop(position.market_slug, None)
            
            logger.info(f"Closed position for {position.market_slug}: {reason}")
            
        except Exception as e:
            logger.error(f"Error closing position: {e}", exc_info=True)
