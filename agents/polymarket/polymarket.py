# core polymarket api
# https://github.com/Polymarket/py-clob-client/tree/main/examples

import os
import pdb
import time
import ast
import requests
import logging
from typing import Optional, Dict, List

from dotenv import load_dotenv

from web3 import Web3
from web3.constants import MAX_INT
from web3.middleware import geth_poa_middleware

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from py_clob_client.constants import AMOY, POLYGON
from py_clob_client.exceptions import PolyApiException
from py_order_utils.builders import OrderBuilder
from py_order_utils.model import OrderData
from py_order_utils.signer import Signer
from py_clob_client.clob_types import (
    OrderArgs,
    MarketOrderArgs,
    OrderType,
    OrderBookSummary,
    TradeParams,
)
from py_clob_client.order_builder.constants import BUY, SELL
from dataclasses import dataclass
from typing import Dict, Any

# PostOrdersArgs doesn't exist in py-clob-client 0.17.5, so we define it ourselves
@dataclass
class PostOrdersArgs:
    """Dataclass for batch order placement (matches py-clob-client API)."""
    order: Dict[str, Any]
    orderType: OrderType

from agents.utils.objects import SimpleMarket, SimpleEvent

load_dotenv()

logger = logging.getLogger(__name__)


class Polymarket:
    def __init__(self) -> None:
        self.gamma_url = "https://gamma-api.polymarket.com"
        self.gamma_markets_endpoint = self.gamma_url + "/markets"
        self.gamma_events_endpoint = self.gamma_url + "/events"

        self.clob_url = "https://clob.polymarket.com"
        self.clob_auth_endpoint = self.clob_url + "/auth/api-key"

        self.chain_id = 137  # POLYGON
        # Clean private key: strip whitespace, remove comments, and remove 0x prefix if present
        raw_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
        if raw_key:
            raw_key = raw_key.strip()
            # Remove any comments (everything after #)
            if "#" in raw_key:
                raw_key = raw_key.split("#")[0].strip()
            # Remove 0x prefix if present
            if raw_key.startswith("0x"):
                raw_key = raw_key[2:]
            # Remove any remaining whitespace/newlines
            raw_key = raw_key.replace(" ", "").replace("\n", "").replace("\r", "")
            self.private_key = raw_key
        else:
            self.private_key = None
        self.proxy_wallet_address = os.getenv("POLYMARKET_PROXY_WALLET_ADDRESS")  # For gasless trading
        self.polygon_rpc = "https://polygon-rpc.com"
        self.w3 = Web3(Web3.HTTPProvider(self.polygon_rpc))

        self.exchange_address = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
        self.neg_risk_exchange_address = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

        self.erc20_approve = """[{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"owner","type":"address"},{"indexed":true,"internalType":"address","name":"spender","type":"address"},{"indexed":false,"internalType":"uint256","name":"value","type":"uint256"}],"name":"Approval","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"authorizer","type":"address"},{"indexed":true,"internalType":"bytes32","name":"nonce","type":"bytes32"}],"name":"AuthorizationCanceled","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"authorizer","type":"address"},{"indexed":true,"internalType":"bytes32","name":"nonce","type":"bytes32"}],"name":"AuthorizationUsed","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"account","type":"address"}],"name":"Blacklisted","type":"event"},{"anonymous":false,"inputs":[{"indexed":false,"internalType":"address","name":"userAddress","type":"address"},{"indexed":false,"internalType":"address payable","name":"relayerAddress","type":"address"},{"indexed":false,"internalType":"bytes","name":"functionSignature","type":"bytes"}],"name":"MetaTransactionExecuted","type":"event"},{"anonymous":false,"inputs":[],"name":"Pause","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"newRescuer","type":"address"}],"name":"RescuerChanged","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"bytes32","name":"role","type":"bytes32"},{"indexed":true,"internalType":"bytes32","name":"previousAdminRole","type":"bytes32"},{"indexed":true,"internalType":"bytes32","name":"newAdminRole","type":"bytes32"}],"name":"RoleAdminChanged","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"bytes32","name":"role","type":"bytes32"},{"indexed":true,"internalType":"address","name":"account","type":"address"},{"indexed":true,"internalType":"address","name":"sender","type":"address"}],"name":"RoleGranted","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"bytes32","name":"role","type":"bytes32"},{"indexed":true,"internalType":"address","name":"account","type":"address"},{"indexed":true,"internalType":"address","name":"sender","type":"address"}],"name":"RoleRevoked","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"from","type":"address"},{"indexed":true,"internalType":"address","name":"to","type":"address"},{"indexed":false,"internalType":"uint256","name":"value","type":"uint256"}],"name":"Transfer","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"account","type":"address"}],"name":"UnBlacklisted","type":"event"},{"anonymous":false,"inputs":[],"name":"Unpause","type":"event"},{"inputs":[],"name":"APPROVE_WITH_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"BLACKLISTER_ROLE","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"CANCEL_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"DECREASE_ALLOWANCE_WITH_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"DEFAULT_ADMIN_ROLE","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"DEPOSITOR_ROLE","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"DOMAIN_SEPARATOR","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"EIP712_VERSION","outputs":[{"internalType":"string","name":"","type":"string"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"INCREASE_ALLOWANCE_WITH_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"META_TRANSACTION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"PAUSER_ROLE","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"PERMIT_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"RESCUER_ROLE","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"TRANSFER_WITH_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"WITHDRAW_WITH_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"}],"name":"allowance","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"},{"internalType":"uint256","name":"validAfter","type":"uint256"},{"internalType":"uint256","name":"validBefore","type":"uint256"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"approveWithAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"authorizer","type":"address"},{"internalType":"bytes32","name":"nonce","type":"bytes32"}],"name":"authorizationState","outputs":[{"internalType":"enum GasAbstraction.AuthorizationState","name":"","type":"uint8"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"blacklist","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"blacklisters","outputs":[{"internalType":"address[]","name":"","type":"address[]"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"authorizer","type":"address"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"cancelAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"decimals","outputs":[{"internalType":"uint8","name":"","type":"uint8"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"subtractedValue","type":"uint256"}],"name":"decreaseAllowance","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"decrement","type":"uint256"},{"internalType":"uint256","name":"validAfter","type":"uint256"},{"internalType":"uint256","name":"validBefore","type":"uint256"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"decreaseAllowanceWithAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"user","type":"address"},{"internalType":"bytes","name":"depositData","type":"bytes"}],"name":"deposit","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"userAddress","type":"address"},{"internalType":"bytes","name":"functionSignature","type":"bytes"},{"internalType":"bytes32","name":"sigR","type":"bytes32"},{"internalType":"bytes32","name":"sigS","type":"bytes32"},{"internalType":"uint8","name":"sigV","type":"uint8"}],"name":"executeMetaTransaction","outputs":[{"internalType":"bytes","name":"","type":"bytes"}],"stateMutability":"payable","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"}],"name":"getRoleAdmin","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"},{"internalType":"uint256","name":"index","type":"uint256"}],"name":"getRoleMember","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"}],"name":"getRoleMemberCount","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"},{"internalType":"address","name":"account","type":"address"}],"name":"grantRole","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"},{"internalType":"address","name":"account","type":"address"}],"name":"hasRole","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"addedValue","type":"uint256"}],"name":"increaseAllowance","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"increment","type":"uint256"},{"internalType":"uint256","name":"validAfter","type":"uint256"},{"internalType":"uint256","name":"validBefore","type":"uint256"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"increaseAllowanceWithAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"string","name":"newName","type":"string"},{"internalType":"string","name":"newSymbol","type":"string"},{"internalType":"uint8","name":"newDecimals","type":"uint8"},{"internalType":"address","name":"childChainManager","type":"address"}],"name":"initialize","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"initialized","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"isBlacklisted","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"name","outputs":[{"internalType":"string","name":"","type":"string"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"}],"name":"nonces","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"pause","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"paused","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"pausers","outputs":[{"internalType":"address[]","name":"","type":"address[]"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"},{"internalType":"uint256","name":"deadline","type":"uint256"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"permit","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"},{"internalType":"address","name":"account","type":"address"}],"name":"renounceRole","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"contract IERC20","name":"tokenContract","type":"address"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"rescueERC20","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"rescuers","outputs":[{"internalType":"address[]","name":"","type":"address[]"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"},{"internalType":"address","name":"account","type":"address"}],"name":"revokeRole","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"symbol","outputs":[{"internalType":"string","name":"","type":"string"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"totalSupply","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"sender","type":"address"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"transferFrom","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"from","type":"address"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"},{"internalType":"uint256","name":"validAfter","type":"uint256"},{"internalType":"uint256","name":"validBefore","type":"uint256"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"transferWithAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"unBlacklist","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"unpause","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"string","name":"newName","type":"string"},{"internalType":"string","name":"newSymbol","type":"string"}],"name":"updateMetadata","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"},{"internalType":"uint256","name":"validAfter","type":"uint256"},{"internalType":"uint256","name":"validBefore","type":"uint256"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"withdrawWithAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"}]"""
        self.erc1155_set_approval = """[{"inputs": [{ "internalType": "address", "name": "operator", "type": "address" },{ "internalType": "bool", "name": "approved", "type": "bool" }],"name": "setApprovalForAll","outputs": [],"stateMutability": "nonpayable","type": "function"}]"""
        # ERC1155 ABI with balanceOf method for checking conditional token balances
        self.erc1155_abi = """[
            {"inputs": [{"internalType": "address", "name": "account", "type": "address"}, {"internalType": "uint256", "name": "id", "type": "uint256"}], "name": "balanceOf", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
            {"inputs": [{"internalType": "address", "name": "account", "type": "address"}, {"internalType": "address", "name": "operator", "type": "address"}], "name": "isApprovedForAll", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
            {"inputs": [{"internalType": "address", "name": "operator", "type": "address"}, {"internalType": "bool", "name": "approved", "type": "bool"}], "name": "setApprovalForAll", "outputs": [], "stateMutability": "nonpayable", "type": "function"}
        ]"""
        
        # CTF contract ABI with splitPosition and mergePositions functions
        self.ctf_abi = """[
            {"inputs": [{"internalType": "address", "name": "account", "type": "address"}, {"internalType": "uint256", "name": "id", "type": "uint256"}], "name": "balanceOf", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
            {"inputs": [{"internalType": "address", "name": "account", "type": "address"}, {"internalType": "address", "name": "operator", "type": "address"}], "name": "isApprovedForAll", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
            {"inputs": [{"internalType": "address", "name": "operator", "type": "address"}, {"internalType": "bool", "name": "approved", "type": "bool"}], "name": "setApprovalForAll", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
            {"inputs": [{"internalType": "address", "name": "collateralToken", "type": "address"}, {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"}, {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"}, {"internalType": "uint256[]", "name": "partition", "type": "uint256[]"}, {"internalType": "uint256", "name": "amount", "type": "uint256"}], "name": "splitPosition", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
            {"inputs": [{"internalType": "address", "name": "collateralToken", "type": "address"}, {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"}, {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"}, {"internalType": "uint256[]", "name": "partition", "type": "uint256[]"}, {"internalType": "uint256", "name": "amount", "type": "uint256"}], "name": "mergePositions", "outputs": [], "stateMutability": "nonpayable", "type": "function"}
        ]"""

        # USDC.e (bridged USDC) - required by Polymarket CTF contract
        # Note: CTF splitPosition function requires USDC.e, not Native USDC
        self.usdc_address = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        self.ctf_address = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

        self.web3 = Web3(Web3.HTTPProvider(self.polygon_rpc))
        self.web3.middleware_onion.inject(geth_poa_middleware, layer=0)

        self.usdc = self.web3.eth.contract(
            address=self.usdc_address, abi=self.erc20_approve
        )
        self.ctf = self.web3.eth.contract(
            address=self.ctf_address, abi=self.ctf_abi
        )

        self._init_api_keys()
        self._init_approvals(False)

    def _init_api_keys(self) -> None:
        # Initialize CLOB client with proxy wallet as funder (for gasless trading)
        # If proxy wallet address is set, use it as funder for gasless trading
        if self.proxy_wallet_address:
            # signature_type: 1 = Magic/Email, 2 = Browser/Gnosis Safe
            # Using 2 for Gnosis Safe (proxy wallet)
            self.client = ClobClient(
                host=self.clob_url,
                key=self.private_key,
                chain_id=self.chain_id,
                funder=self.proxy_wallet_address,
                signature_type=2
            )
            logger.info(f"✓ Using proxy wallet as funder: {self.proxy_wallet_address[:10]}...{self.proxy_wallet_address[-8:]}")
            logger.info("  (Gasless trading enabled)")
        else:
            # Standard initialization without proxy wallet
            self.client = ClobClient(
                host=self.clob_url,
                key=self.private_key,
                chain_id=self.chain_id
            )
        
        self.credentials = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(self.credentials)
        
        # Create a separate client for direct wallet orders (used when selling conditional tokens)
        # Conditional tokens from split are in direct wallet, so we need to use direct wallet for sell orders
        self.direct_wallet_client = None
        if self.private_key:
            try:
                self.direct_wallet_client = ClobClient(
                    host=self.clob_url,
                    key=self.private_key,
                    chain_id=self.chain_id
                    # No funder parameter = uses direct wallet (signature_type=0)
                )
                # Create separate API credentials for direct wallet (credentials are wallet-specific)
                direct_wallet_credentials = self.direct_wallet_client.create_or_derive_api_creds()
                self.direct_wallet_client.set_api_creds(direct_wallet_credentials)
                logger.info("✓ Direct wallet CLOB client initialized (for conditional token sell orders)")
                logger.info(f"  Direct wallet address: {self.get_address_for_private_key()}")
            except Exception as e:
                logger.warning(f"⚠️ Could not initialize direct wallet client: {e}")
        # print(self.credentials)

    def _init_approvals(self, run: bool = False) -> None:
        if not run:
            return

        priv_key = self.private_key
        pub_key = self.get_address_for_private_key()
        chain_id = self.chain_id
        web3 = self.web3
        nonce = web3.eth.get_transaction_count(pub_key)
        usdc = self.usdc
        ctf = self.ctf

        # CTF Exchange
        raw_usdc_approve_txn = usdc.functions.approve(
            "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E", int(MAX_INT, 0)
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_usdc_approve_tx = web3.eth.account.sign_transaction(
            raw_usdc_approve_txn, private_key=priv_key
        )
        send_usdc_approve_tx = web3.eth.send_raw_transaction(
            signed_usdc_approve_tx.raw_transaction
        )
        usdc_approve_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_usdc_approve_tx, 600
        )
        print(usdc_approve_tx_receipt)

        nonce = web3.eth.get_transaction_count(pub_key)

        raw_ctf_approval_txn = ctf.functions.setApprovalForAll(
            "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E", True
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_ctf_approval_tx = web3.eth.account.sign_transaction(
            raw_ctf_approval_txn, private_key=priv_key
        )
        send_ctf_approval_tx = web3.eth.send_raw_transaction(
            signed_ctf_approval_tx.raw_transaction
        )
        ctf_approval_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_ctf_approval_tx, 600
        )
        print(ctf_approval_tx_receipt)

        nonce = web3.eth.get_transaction_count(pub_key)

        # Neg Risk CTF Exchange
        raw_usdc_approve_txn = usdc.functions.approve(
            "0xC5d563A36AE78145C45a50134d48A1215220f80a", int(MAX_INT, 0)
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_usdc_approve_tx = web3.eth.account.sign_transaction(
            raw_usdc_approve_txn, private_key=priv_key
        )
        send_usdc_approve_tx = web3.eth.send_raw_transaction(
            signed_usdc_approve_tx.raw_transaction
        )
        usdc_approve_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_usdc_approve_tx, 600
        )
        print(usdc_approve_tx_receipt)

        nonce = web3.eth.get_transaction_count(pub_key)

        raw_ctf_approval_txn = ctf.functions.setApprovalForAll(
            "0xC5d563A36AE78145C45a50134d48A1215220f80a", True
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_ctf_approval_tx = web3.eth.account.sign_transaction(
            raw_ctf_approval_txn, private_key=priv_key
        )
        send_ctf_approval_tx = web3.eth.send_raw_transaction(
            signed_ctf_approval_tx.raw_transaction
        )
        ctf_approval_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_ctf_approval_tx, 600
        )
        print(ctf_approval_tx_receipt)

        nonce = web3.eth.get_transaction_count(pub_key)

        # Neg Risk Adapter
        raw_usdc_approve_txn = usdc.functions.approve(
            "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296", int(MAX_INT, 0)
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_usdc_approve_tx = web3.eth.account.sign_transaction(
            raw_usdc_approve_txn, private_key=priv_key
        )
        send_usdc_approve_tx = web3.eth.send_raw_transaction(
            signed_usdc_approve_tx.raw_transaction
        )
        usdc_approve_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_usdc_approve_tx, 600
        )
        print(usdc_approve_tx_receipt)

        nonce = web3.eth.get_transaction_count(pub_key)

        raw_ctf_approval_txn = ctf.functions.setApprovalForAll(
            "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296", True
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_ctf_approval_tx = web3.eth.account.sign_transaction(
            raw_ctf_approval_txn, private_key=priv_key
        )
        send_ctf_approval_tx = web3.eth.send_raw_transaction(
            signed_ctf_approval_tx.raw_transaction
        )
        ctf_approval_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_ctf_approval_tx, 600
        )
        print(ctf_approval_tx_receipt)

    def get_all_markets(self) -> "list[SimpleMarket]":
        markets = []
        res = httpx.get(self.gamma_markets_endpoint)
        if res.status_code == 200:
            for market in res.json():
                try:
                    market_data = self.map_api_to_market(market)
                    markets.append(SimpleMarket(**market_data))
                except Exception as e:
                    print(e)
                    pass
        return markets

    def filter_markets_for_trading(self, markets: "list[SimpleMarket]"):
        tradeable_markets = []
        for market in markets:
            if market.active:
                tradeable_markets.append(market)
        return tradeable_markets

    def get_market(self, token_id: str) -> SimpleMarket:
        params = {"clob_token_ids": token_id}
        res = httpx.get(self.gamma_markets_endpoint, params=params)
        if res.status_code == 200:
            data = res.json()
            market = data[0]
            return self.map_api_to_market(market, token_id)

    def map_api_to_market(self, market, token_id: str = "") -> SimpleMarket:
        market = {
            "id": int(market["id"]),
            "question": market["question"],
            "end": market["endDate"],
            "description": market["description"],
            "active": market["active"],
            # "deployed": market["deployed"],
            "funded": market["funded"],
            "rewardsMinSize": float(market["rewardsMinSize"]),
            "rewardsMaxSpread": float(market["rewardsMaxSpread"]),
            # "volume": float(market["volume"]),
            "spread": float(market["spread"]),
            "outcomes": str(market["outcomes"]),
            "outcome_prices": str(market["outcomePrices"]),
            "clob_token_ids": str(market["clobTokenIds"]),
        }
        if token_id:
            market["clob_token_ids"] = token_id
        return market

    def get_all_events(self) -> "list[SimpleEvent]":
        events = []
        res = httpx.get(self.gamma_events_endpoint)
        if res.status_code == 200:
            print(len(res.json()))
            for event in res.json():
                try:
                    print(1)
                    event_data = self.map_api_to_event(event)
                    events.append(SimpleEvent(**event_data))
                except Exception as e:
                    print(e)
                    pass
        return events

    def map_api_to_event(self, event) -> SimpleEvent:
        description = event["description"] if "description" in event.keys() else ""
        return {
            "id": int(event["id"]),
            "ticker": event["ticker"],
            "slug": event["slug"],
            "title": event["title"],
            "description": description,
            "active": event["active"],
            "closed": event["closed"],
            "archived": event["archived"],
            "new": event["new"],
            "featured": event["featured"],
            "restricted": event["restricted"],
            "end": event["endDate"],
            "markets": ",".join([x["id"] for x in event["markets"]]),
        }

    def filter_events_for_trading(
        self, events: "list[SimpleEvent]"
    ) -> "list[SimpleEvent]":
        tradeable_events = []
        for event in events:
            if (
                event.active
                and not event.restricted
                and not event.archived
                and not event.closed
            ):
                tradeable_events.append(event)
        return tradeable_events

    def get_all_tradeable_events(self) -> "list[SimpleEvent]":
        all_events = self.get_all_events()
        return self.filter_events_for_trading(all_events)

    def get_sampling_simplified_markets(self) -> "list[SimpleEvent]":
        markets = []
        raw_sampling_simplified_markets = self.client.get_sampling_simplified_markets()
        for raw_market in raw_sampling_simplified_markets["data"]:
            token_one_id = raw_market["tokens"][0]["token_id"]
            market = self.get_market(token_one_id)
            markets.append(market)
        return markets

    def get_orderbook(self, token_id: str) -> OrderBookSummary:
        return self.client.get_order_book(token_id)

    def get_orderbook_price(self, token_id: str) -> float:
        return float(self.client.get_price(token_id))

    def get_address_for_private_key(self):
        account = self.w3.eth.account.from_key(str(self.private_key))
        return account.address

    def build_order(
        self,
        market_token: str,
        amount: float,
        nonce: str = str(round(time.time())),  # for cancellations
        side: str = "BUY",
        expiration: str = "0",  # timestamp after which order expires
    ):
        signer = Signer(self.private_key)
        builder = OrderBuilder(self.exchange_address, self.chain_id, signer)

        buy = side == "BUY"
        side = 0 if buy else 1
        maker_amount = amount if buy else 0
        taker_amount = amount if not buy else 0
        order_data = OrderData(
            maker=self.get_address_for_private_key(),
            tokenId=market_token,
            makerAmount=maker_amount,
            takerAmount=taker_amount,
            feeRateBps="1",
            nonce=nonce,
            side=side,
            expiration=expiration,
        )
        order = builder.build_signed_order(order_data)
        return order

    def _get_client_for_order(self, side: str, token_id: str) -> Optional[ClobClient]:
        """
        Get the appropriate CLOB client for placing an order.
        
        For SELL orders of conditional tokens:
        - If shares are in direct wallet (from split), use direct wallet client
        - If shares are in proxy wallet (from CLOB buy), use proxy wallet client
        - By default, check both wallets and use the one with shares
        
        For other orders, use proxy wallet client if available (gasless trading).
        
        Args:
            side: "BUY" or "SELL"
            token_id: Token ID (conditional tokens are very long numeric strings)
        
        Returns:
            ClobClient instance to use for this order
        """
        # For SELL orders of conditional tokens, check where shares actually are
        if side == "SELL" and token_id and len(token_id) > 30:  # Conditional tokens have long numeric IDs
            # Check both wallets to see where shares are
            direct_wallet = self.get_address_for_private_key()
            direct_balance = None
            proxy_balance = None
            
            try:
                direct_balance = self.get_conditional_token_balance(token_id, wallet_address=direct_wallet)
            except Exception:
                pass
            
            if self.proxy_wallet_address:
                try:
                    proxy_balance = self.get_conditional_token_balance(token_id, wallet_address=self.proxy_wallet_address)
                except Exception:
                    pass
            
            # Use the wallet that has shares (prefer direct wallet if both have shares)
            if direct_balance and direct_balance > 0:
                if self.direct_wallet_client:
                    logger.debug(f"Using direct wallet client for SELL order (shares in direct wallet: {direct_balance:.6f})")
                    return self.direct_wallet_client
            elif proxy_balance and proxy_balance > 0:
                logger.debug(f"Using proxy wallet client for SELL order (shares in proxy wallet: {proxy_balance:.6f})")
                return self.client  # Proxy wallet client
            
            # If no shares found in either wallet, default to direct wallet client (for split-based strategies)
            if self.direct_wallet_client:
                logger.debug(f"Using direct wallet client for SELL order (default, no shares detected)")
                return self.direct_wallet_client
        
        # For all other orders, use the default client (proxy wallet if available)
        return self.client
    
    def execute_order(self, price, size, side, token_id, fee_rate_bps: Optional[int] = None, auto_detect_fee: bool = True, order_type: OrderType = OrderType.GTC) -> Dict:
        """
        Place a limit order.
        
        Args:
            price: Limit price (0.01 to 0.99)
            size: Order size (number of shares)
            side: "BUY" or "SELL" (or use BUY/SELL constants)
            token_id: CLOB token ID
            fee_rate_bps: Fee rate in basis points. If None and auto_detect_fee=True, 
                         will automatically detect from error message (default: None)
            auto_detect_fee: If True and fee_rate_bps is None, automatically detect 
                            fee rate from error message (default: True)
            order_type: OrderType (GTC for limit orders, FOK/IOC for market orders). Default: OrderType.GTC
            
        Returns:
            Order response dict with fields like 'orderID', 'status', 'success', etc.
        """
        # Use two-step process: create_order + post_order with explicit OrderType
        # This ensures the order is properly structured as a limit order
        # Reference: https://github.com/Polymarket/py-clob-client
        
        logger.info(
            f"🔵 execute_order() called: price={price}, size={size}, side={side}, "
            f"token_id={token_id[:20] if token_id else None}..., "
            f"fee_rate_bps={fee_rate_bps}, order_type={order_type}"
        )
        
        # Get appropriate client (direct wallet for conditional token SELL orders)
        client_to_use = self._get_client_for_order(side, token_id)
        if not client_to_use:
            raise ValueError("No CLOB client available for placing order")
        
        if client_to_use == self.direct_wallet_client:
            logger.info(f"  Using DIRECT WALLET client (shares are in direct wallet)")
        elif client_to_use == self.client and self.proxy_wallet_address:
            logger.info(f"  Using PROXY WALLET client (gasless trading)")
        else:
            logger.info(f"  Using standard client")
        
        # If fee_rate_bps not specified, try with default (0) and auto-detect from error
        if fee_rate_bps is None:
            if auto_detect_fee:
                # Try with fee_rate_bps=0 first, then parse error to get required fee
                try:
                    logger.debug(f"  Creating OrderArgs with fee_rate_bps=0 (will auto-detect if needed)")
                    order_args = OrderArgs(price=price, size=size, side=side, token_id=token_id, fee_rate_bps=0)
                    logger.debug(f"  OrderArgs created: {order_args}")
                    logger.debug(f"  Calling client.create_order()...")
                    signed_order = client_to_use.create_order(order_args)
                    logger.debug(f"  Signed order created, calling client.post_order() with order_type={order_type}...")
                    response = client_to_use.post_order(signed_order, order_type)
                    logger.info(f"  ✅ Order posted successfully, response: {response}")
                    return response
                except PolyApiException as e:
                    # Parse error message to extract required fee rate
                    error_str = str(e)
                    error_message = e.error_message if hasattr(e, 'error_message') else error_str
                    
                    logger.warning(
                        f"  ⚠️ PolyApiException during order creation: {error_str}. "
                        f"Error message attr: {error_message}"
                    )
                    
                    # Check if error is about fee rate
                    if "fee" in error_str.lower() or "fee" in str(error_message).lower():
                        logger.info(f"  🔍 Detected fee-related error, attempting to extract fee rate...")
                        # Extract fee rate from error message like "current market's taker fee: 1000"
                        import re
                        # Try multiple patterns
                        patterns = [
                            r"taker fee[:\s]+(\d+)",
                            r"fee[:\s]+(\d+)",
                            r"fee rate[:\s]+(\d+)",
                        ]
                        for pattern in patterns:
                            match = re.search(pattern, error_str, re.IGNORECASE)
                            if not match and isinstance(error_message, dict):
                                match = re.search(pattern, str(error_message), re.IGNORECASE)
                            if match:
                                detected_fee = int(match.group(1))
                                logger.info(f"  ✅ Auto-detected fee rate from error: {detected_fee} BPS")
                                # Retry with detected fee rate
                                logger.debug(f"  Retrying with fee_rate_bps={detected_fee}...")
                                order_args = OrderArgs(price=price, size=size, side=side, token_id=token_id, fee_rate_bps=detected_fee)
                                signed_order = client_to_use.create_order(order_args)
                                response = client_to_use.post_order(signed_order, order_type)
                                logger.info(f"  ✅ Order posted successfully after fee detection, response: {response}")
                                return response
                    # If we can't parse the error, re-raise it
                    logger.error(f"  ❌ Could not parse fee from error, re-raising exception")
                    raise
                except Exception as e:
                    # For any other exception, re-raise it
                    raise
            else:
                # Default to 1000 BPS if auto_detect is disabled
                fee_rate_bps = 1000
        
        # Use two-step process with explicit OrderType
        logger.debug(f"  Creating OrderArgs with fee_rate_bps={fee_rate_bps}")
        order_args = OrderArgs(price=price, size=size, side=side, token_id=token_id, fee_rate_bps=fee_rate_bps)
        logger.debug(f"  OrderArgs created: {order_args}")
        logger.debug(f"  Calling client.create_order()...")
        signed_order = client_to_use.create_order(order_args)
        logger.debug(f"  Signed order created, calling client.post_order() with order_type={order_type}...")
        response = client_to_use.post_order(signed_order, order_type)
        logger.info(f"  ✅ Order posted successfully, response: {response}")
        return response
    
    def extract_order_id(self, order_response) -> Optional[str]:
        """
        Extract order ID from order response.
        Handles both dict and string formats.
        
        Args:
            order_response: Response from execute_order() or execute_market_order()
            
        Returns:
            Order ID string, or None if not found
        """
        if isinstance(order_response, dict):
            # Try common field names
            return order_response.get("orderID") or order_response.get("order_id") or order_response.get("id")
        elif isinstance(order_response, str):
            # If it's a JSON string, try to parse it
            try:
                import json
                parsed = json.loads(order_response)
                return parsed.get("orderID") or parsed.get("order_id") or parsed.get("id")
            except (json.JSONDecodeError, AttributeError):
                # If it's just the order ID as a string, return it
                return order_response if order_response.startswith("0x") else None
        return None

    def execute_market_order(self, market, amount) -> str:
        token_id = ast.literal_eval(market[0].dict()["metadata"]["clob_token_ids"])[1]
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
        )
        signed_order = self.client.create_market_order(order_args)
        print("Execute market order... signed_order ", signed_order)
        resp = self.client.post_order(signed_order, orderType=OrderType.FOK)
        print(resp)
        print("Done!")
        return resp

    def get_order_status(self, order_id: str) -> Optional[Dict]:
        """
        Get the status of a specific order.
        
        Args:
            order_id: Order ID (from execute_order response)
            
        Returns:
            Order status dict with fields like 'status', 'orderID', 'takingAmount', 'makingAmount', etc.
            Returns None if order not found or error occurred
        """
        if not self.client:
            logger.error("CLOB client not initialized - cannot get order status")
            return None
        
        try:
            return self.client.get_order(order_id)
        except Exception as e:
            logger.error(f"Error getting order status for {order_id}: {e}")
            return None

    def get_open_orders(self) -> Optional[List[Dict]]:
        """
        Get all currently open/active orders for your account.
        
        Returns:
            List of open orders, or None if error occurred
        """
        if not self.client:
            logger.error("CLOB client not initialized - cannot get open orders")
            return None
        
        try:
            # Use get_orders with OpenOrderParams (correct method name per py-clob-client API)
            from py_clob_client.clob_types import OpenOrderParams
            return self.client.get_orders(OpenOrderParams())
        except Exception as e:
            logger.error(f"Error getting open orders: {e}")
            return None

    def get_trades(self, maker_address: Optional[str] = None, market: Optional[str] = None) -> Optional[List[Dict]]:
        """
        Get execution details (fills) for your orders.
        Returns trades once orders move from 'open' to 'matched'.
        
        Args:
            maker_address: Optional wallet address to filter trades. If not provided, uses current wallet.
            market: Optional market ID (condition_id) to filter trades by market.
        
        Returns:
            List of trade/fill records, or None if error occurred
        """
        if not self.client:
            logger.error("CLOB client not initialized - cannot get trades")
            return None
        
        try:
            # Use TradeParams to filter trades if parameters provided
            if maker_address or market:
                # Determine which address to use
                if not maker_address:
                    if self.proxy_wallet_address:
                        maker_address = self.proxy_wallet_address
                    else:
                        maker_address = self.get_address_for_private_key()
                
                trade_params = TradeParams(
                    maker_address=maker_address,
                    market=market if market else None,
                )
                logger.debug(
                    f"🔍 Getting trades with filters: maker_address={maker_address[:10]}..., "
                    f"market={market[:20] if market else 'None'}..."
                )
                return self.client.get_trades(trade_params)
            else:
                # No filters - get all trades
                return self.client.get_trades()
        except Exception as e:
            logger.error(f"Error getting trades: {e}", exc_info=True)
            return None

    def cancel_order(self, order_id: str) -> Optional[Dict]:
        """
        Cancel a specific order.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            Cancellation response dict, or None if error occurred
        """
        if not self.client:
            logger.error("CLOB client not initialized - cannot cancel order")
            return None
        
        try:
            # Check if client has cancel_order method
            if hasattr(self.client, 'cancel_order'):
                return self.client.cancel_order(order_id)
            elif hasattr(self.client, 'cancel'):
                return self.client.cancel(order_id)
            else:
                logger.warning("CLOB client does not have cancel_order or cancel method")
                return None
        except Exception as e:
            logger.error(f"Error canceling order {order_id}: {e}")
            return None
    
    def cancel_orders_batch(self, order_ids: List[str]) -> Optional[Dict]:
        """
        Cancel multiple orders in a single batch request.
        
        Args:
            order_ids: List of order IDs to cancel
            
        Returns:
            Batch cancellation response dict, or None if error occurred
        """
        if not self.client:
            logger.error("CLOB client not initialized - cannot cancel orders")
            return None
        
        if not order_ids:
            logger.warning("No order IDs provided for batch cancel")
            return None
        
        try:
            if hasattr(self.client, 'cancel_orders'):
                logger.info(f"Batch cancelling {len(order_ids)} orders...")
                result = self.client.cancel_orders(order_ids)
                if result is None:
                    logger.warning("⚠️ Batch cancel returned None - batch operation may have failed")
                return result
            else:
                logger.warning("⚠️ CLOB client does not have cancel_orders method, falling back to individual cancels")
                # Fallback to individual cancels
                results = []
                for order_id in order_ids:
                    result = self.cancel_order(order_id)
                    if result:
                        results.append(result)
                if results:
                    logger.info(f"✅ Fallback: Successfully cancelled {len(results)}/{len(order_ids)} orders individually")
                    return {"results": results}
                else:
                    logger.error(f"❌ Fallback: Failed to cancel any of {len(order_ids)} orders")
                    return None
        except Exception as e:
            logger.error(f"❌ Error batch canceling orders: {e}", exc_info=True)
            logger.warning("⚠️ Batch cancel failed with exception, returning None")
            return None
    
    def place_orders_batch(self, orders: List[Dict]) -> Optional[Dict]:
        """
        Place multiple orders in a single batch request using post_orders.
        
        Args:
            orders: List of order dicts, each with:
                - price: float
                - size: float
                - side: "BUY" or "SELL"
                - token_id: str
                - fee_rate_bps: Optional[int] (default: None, auto-detect)
                - order_type: OrderType (default: OrderType.GTC)
        
        Returns:
            Batch placement response dict, or None if error occurred
        """
        if not self.client:
            logger.error("CLOB client not initialized - cannot place orders")
            return None
        
        if not orders:
            logger.warning("No orders provided for batch placement")
            return None
        
        try:
            if hasattr(self.client, 'post_orders'):
                logger.info(f"Batch placing {len(orders)} orders...")
                
                # Convert order dicts to PostOrdersArgs (matching Gemini's pattern)
                post_orders_args = []
                for order_dict in orders:
                    price = order_dict['price']
                    size = order_dict['size']
                    side = order_dict['side']
                    token_id = order_dict['token_id']
                    fee_rate_bps = order_dict.get('fee_rate_bps')
                    order_type = order_dict.get('order_type', OrderType.GTC)
                    
                    # Create order using client.create_order
                    order_args = OrderArgs(
                        price=price,
                        size=size,
                        side=side,
                        token_id=token_id,
                        fee_rate_bps=fee_rate_bps,
                    )
                    
                    # Get appropriate client for this order (direct wallet for conditional token SELL orders)
                    client_to_use = self._get_client_for_order(side, token_id)
                    if not client_to_use:
                        logger.error(f"No CLOB client available for order")
                        continue
                    
                    signed_order = client_to_use.create_order(order_args)
                    
                    # Create PostOrdersArgs instance
                    post_orders_args.append(
                        PostOrdersArgs(
                            order=signed_order,
                            orderType=order_type,
                        )
                    )
                
                # Determine which client to use for batch placement
                # If all orders are SELL orders of conditional tokens, use direct wallet client
                all_conditional_sells = all(
                    order_dict.get('side') == 'SELL' and 
                    order_dict.get('token_id') and 
                    len(str(order_dict.get('token_id'))) > 30
                    for order_dict in orders
                )
                
                client_to_use = self.direct_wallet_client if (all_conditional_sells and self.direct_wallet_client) else self.client
                if client_to_use == self.direct_wallet_client:
                    logger.info(f"  Using DIRECT WALLET client for batch (conditional token SELL orders)")
                elif client_to_use == self.client and self.proxy_wallet_address:
                    logger.info(f"  Using PROXY WALLET client for batch (gasless trading)")
                
                # Place orders in batch
                result = client_to_use.post_orders(post_orders_args)
                if result is None:
                    logger.warning("⚠️ Batch place returned None - batch operation may have failed")
                return result
            else:
                logger.warning("⚠️ CLOB client does not have post_orders method, falling back to parallel individual placements")
                # Fallback: Use ThreadPoolExecutor to place orders in parallel (non-blocking)
                from concurrent.futures import ThreadPoolExecutor, as_completed
                
                def place_single_order(order_dict):
                    """Place a single order."""
                    try:
                        return self.execute_order(
                            price=order_dict['price'],
                            size=order_dict['size'],
                            side=order_dict['side'],
                            token_id=order_dict['token_id'],
                            fee_rate_bps=order_dict.get('fee_rate_bps'),
                            order_type=order_dict.get('order_type', OrderType.GTC),
                        )
                    except Exception as e:
                        logger.error(f"Error placing order in fallback: {e}")
                        return None
                
                # Place all orders in parallel using thread pool
                results = []
                with ThreadPoolExecutor(max_workers=len(orders)) as executor:
                    future_to_order = {
                        executor.submit(place_single_order, order_dict): order_dict
                        for order_dict in orders
                    }
                    
                    for future in as_completed(future_to_order):
                        result = future.result()
                        if result:
                            results.append(result)
                
                if results:
                    logger.info(f"✅ Fallback: Successfully placed {len(results)}/{len(orders)} orders in parallel")
                    return {"results": results}
                else:
                    logger.error(f"❌ Fallback: Failed to place any of {len(orders)} orders")
                    return None
        except Exception as e:
            logger.error(f"❌ Error batch placing orders: {e}", exc_info=True)
            logger.warning("⚠️ Batch place failed with exception, returning None")
            return None

    def get_usdc_balance(self) -> float:
        """Get USDC balance from your Polygon wallet (direct wallet)."""
        wallet_address = self.get_address_for_private_key()
        balance_res = self.usdc.functions.balanceOf(wallet_address).call()
        # USDC has 6 decimals, so divide by 1e6 (1,000,000)
        balance_float = float(balance_res) / 1e6
        logger.debug(f"USDC balance check: raw={balance_res}, wallet={wallet_address[:10]}...{wallet_address[-8:]}, balance=${balance_float:.2f}")
        return balance_float
    
    def get_polymarket_balance(self, proxy_wallet_address: Optional[str] = None) -> Optional[float]:
        """
        Get USDC balance from Polymarket (proxy wallet/trading balance).
        This is the balance available for trading on Polymarket.
        
        Args:
            proxy_wallet_address: Optional proxy wallet address. If not provided,
                                will try to get from POLYMARKET_PROXY_WALLET_ADDRESS env var.
        
        Returns:
            USDC balance as float, or None if unavailable
        """
        try:
            # First, try to query proxy wallet balance on-chain (most reliable)
            proxy_address = proxy_wallet_address or os.getenv("POLYMARKET_PROXY_WALLET_ADDRESS")
            if proxy_address:
                try:
                    # Query USDC balance of proxy wallet on-chain
                    balance_res = self.usdc.functions.balanceOf(proxy_address).call()
                    balance = float(balance_res / 10e5)
                    return balance
                except Exception as e:
                    # If on-chain query fails, continue to try API methods
                    pass
            
            # Fallback: Try API methods (may not work, but worth trying)
            if not self.client:
                return None
            
            # Try to get balance from CLOB API
            # Check if client has a balance method
            if hasattr(self.client, 'get_balance'):
                try:
                    balance = self.client.get_balance()
                    return float(balance) if balance else None
                except:
                    pass
            
            # Alternative: Try authenticated API endpoint
            import httpx
            try:
                # Get API credentials for authenticated request
                if hasattr(self.client, 'get_headers'):
                    headers = self.client.get_headers()
                else:
                    # Fallback: use credentials directly
                    headers = {}
                    if hasattr(self.client, 'api_creds') and self.client.api_creds:
                        creds = self.client.api_creds
                        if hasattr(creds, 'api_key') and hasattr(creds, 'api_secret'):
                            # Create auth header
                            import base64
                            auth_str = f"{creds.api_key}:{creds.api_secret}"
                            auth_bytes = base64.b64encode(auth_str.encode()).decode()
                            headers['Authorization'] = f'Basic {auth_bytes}'
                
                # Try balance endpoint
                response = httpx.get(
                    f"{self.clob_url}/balance",
                    headers=headers,
                    timeout=10.0
                )
                if response.status_code == 200:
                    data = response.json()
                    # Balance might be in different formats
                    if isinstance(data, dict):
                        balance = data.get('balance') or data.get('usdc_balance') or data.get('available') or data.get('usdc')
                        if balance:
                            return float(balance)
                    elif isinstance(data, (int, float, str)):
                        return float(data)
            except Exception as e:
                pass
            
            return None
        except Exception as e:
            return None
    
    def approve_usdc_for_ctf(self, amount_usdc: Optional[float] = None) -> Optional[Dict]:
        """
        Approve USDC for CTF contract to enable split_position.
        
        Args:
            amount_usdc: Amount to approve (None = approve max uint256)
            
        Returns:
            Transaction receipt dict, or None if error
        """
        try:
            wallet_address = self.get_address_for_private_key()
            
            if amount_usdc is None:
                # Approve max amount (2^256 - 1)
                from decimal import Decimal
                MAX_UINT256 = Decimal(2**256 - 1)
                amount_raw = int(MAX_UINT256)
                logger.info(f"Approving unlimited USDC for CTF contract...")
            else:
                amount_raw = int(amount_usdc * 1e6)
                logger.info(f"Approving ${amount_usdc:.2f} USDC for CTF contract...")
            
            # Build approval transaction
            nonce = self.web3.eth.get_transaction_count(wallet_address)
            
            approve_txn = self.usdc.functions.approve(
                self.ctf_address,
                amount_raw
            ).build_transaction({
                "chainId": self.chain_id,
                "from": wallet_address,
                "nonce": nonce,
                "gas": 100000,  # Standard gas limit for approve
                "gasPrice": self.web3.eth.gas_price
            })
            
            # Sign and send transaction
            signed_txn = self.web3.eth.account.sign_transaction(
                approve_txn, private_key=self.private_key
            )
            
            tx_hash = self.web3.eth.send_raw_transaction(signed_txn.raw_transaction)
            logger.info(f"⏳ Approval transaction sent: {tx_hash.hex()}")
            
            # Wait for transaction receipt
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            
            if receipt.status == 1:
                logger.info(f"✅ USDC approval successful! Transaction: {tx_hash.hex()}")
                return {
                    "transaction_hash": tx_hash.hex(),
                    "receipt": receipt
                }
            else:
                logger.error(f"❌ USDC approval transaction failed: {tx_hash.hex()}")
                return None
                
        except Exception as e:
            logger.error(f"Error approving USDC for CTF contract: {e}", exc_info=True)
            return None
    
    def get_conditional_token_balance(self, token_id: str, wallet_address: Optional[str] = None) -> Optional[float]:
        """
        Get balance of conditional tokens (ERC1155) for a specific token_id.
        
        Args:
            token_id: CLOB token ID (uint256)
            wallet_address: Optional wallet address. If not provided, uses proxy wallet or direct wallet.
        
        Returns:
            Balance as float (number of shares), or None if unavailable
        """
        try:
            # Determine which address to check
            if wallet_address:
                address_to_check = wallet_address
            elif self.proxy_wallet_address:
                address_to_check = self.proxy_wallet_address
            else:
                address_to_check = self.get_address_for_private_key()
            
            logger.debug(
                f"🔍 Checking conditional token balance: token_id={token_id[:20]}..., "
                f"wallet={address_to_check[:10]}...{address_to_check[-8:]}, "
                f"ctf_contract={self.ctf_address[:10]}...{self.ctf_address[-8:]}"
            )
            
            # Convert token_id to int (it's a uint256)
            token_id_int = int(token_id)
            
            # Call balanceOf(address, uint256) on ERC1155 contract
            balance_raw = self.ctf.functions.balanceOf(address_to_check, token_id_int).call()
            
            logger.debug(f"  Raw balance from contract: {balance_raw} (uint256)")
            
            # ERC1155 conditional tokens on Polymarket use 1e6 (6 decimals), not 1e18
            # Example: raw balance 1978900 = 1.9789 shares (with 6 decimals)
            # This is different from ERC20 tokens which typically use 1e18
            balance_float = float(balance_raw) / 1e6
            
            logger.info(
                f"  ✓ Conditional token balance: {balance_float:.6f} shares "
                f"(raw: {balance_raw}, token_id: {token_id[:20]}...)"
            )
            
            return balance_float
        except Exception as e:
            logger.error(
                f"❌ Error checking conditional token balance for token_id {token_id}: {e}",
                exc_info=True
            )
            return None
    
    def check_conditional_token_allowance(self, exchange_address: str, wallet_address: Optional[str] = None) -> Optional[bool]:
        """
        Check if conditional tokens (ERC1155) are approved for an exchange contract.
        
        Args:
            exchange_address: Exchange contract address to check approval for
            wallet_address: Optional wallet address. If not provided, uses proxy wallet or direct wallet.
        
        Returns:
            True if approved, False if not approved, None if error
        """
        try:
            # Determine which address to check
            if wallet_address:
                address_to_check = wallet_address
            elif self.proxy_wallet_address:
                address_to_check = self.proxy_wallet_address
            else:
                address_to_check = self.get_address_for_private_key()
            
            logger.debug(
                f"🔍 Checking conditional token allowance: "
                f"wallet={address_to_check[:10]}...{address_to_check[-8:]}, "
                f"exchange={exchange_address[:10]}...{exchange_address[-8:]}, "
                f"ctf_contract={self.ctf_address[:10]}...{self.ctf_address[-8:]}"
            )
            
            # Call isApprovedForAll(address, address) on ERC1155 contract
            is_approved = self.ctf.functions.isApprovedForAll(address_to_check, exchange_address).call()
            
            is_approved_bool = bool(is_approved)
            
            logger.info(
                f"  {'✓' if is_approved_bool else '✗'} Conditional token allowance for "
                f"{exchange_address[:10]}...{exchange_address[-8:]}: {is_approved_bool}"
            )
            
            return is_approved_bool
        except Exception as e:
            logger.error(
                f"❌ Error checking conditional token allowance for {exchange_address}: {e}",
                exc_info=True
            )
            return None
    
    def ensure_conditional_token_allowances(self, wallet_address: Optional[str] = None) -> bool:
        """
        Ensure conditional token allowances are set for all required exchange contracts.
        This is critical for selling shares - the exchange contracts need permission to transfer your conditional tokens.
        
        According to py-clob-client docs, these contracts need approval:
        - 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E (Main exchange)
        - 0xC5d563A36AE78145C45a50134d48A1215220f80a (Neg risk markets)
        - 0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296 (Neg risk adapter)
        
        Args:
            wallet_address: Optional wallet address to check. If None, uses direct wallet (where shares are).
                          IMPORTANT: Shares from split are in direct wallet, so we must check direct wallet allowances.
        
        Returns:
            True if all allowances are set (or if using proxy wallet where this may not be needed),
            False if allowances need to be set but couldn't be set
        """
        try:
            # IMPORTANT: Shares from split are in DIRECT wallet, so we must check direct wallet allowances
            # Even if using proxy wallet for trading, the shares are in direct wallet
            if wallet_address:
                address_to_check = wallet_address
            else:
                # Default to direct wallet (where shares are)
                address_to_check = self.get_address_for_private_key()
            
            exchange_addresses = [
                ("0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E", "Main exchange"),
                ("0xC5d563A36AE78145C45a50134d48A1215220f80a", "Neg risk markets"),
                ("0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296", "Neg risk adapter"),
            ]
            
            logger.info(
                f"🔍 Checking conditional token allowances for wallet "
                f"{address_to_check[:10]}...{address_to_check[-8:]} "
                f"(proxy_wallet={self.proxy_wallet_address is not None}, "
                f"signature_type={getattr(self.client, 'signature_type', 'unknown')})"
            )
            
            all_approved = True
            approval_results = []
            
            for exchange_addr, exchange_name in exchange_addresses:
                is_approved = self.check_conditional_token_allowance(exchange_addr, address_to_check)
                if is_approved is None:
                    logger.warning(f"  ⚠️ Could not check allowance for {exchange_name} ({exchange_addr[:10]}...)")
                    approval_results.append((exchange_name, exchange_addr, None))
                    continue
                if not is_approved:
                    logger.warning(
                        f"  ❌ Conditional token allowance NOT set for {exchange_name} "
                        f"({exchange_addr[:10]}...{exchange_addr[-8:]})"
                    )
                    all_approved = False
                    approval_results.append((exchange_name, exchange_addr, False))
                    # Note: We don't automatically set allowances here because:
                    # 1. It requires a transaction (gas fees)
                    # 2. For proxy wallets, allowances may be handled differently
                    # 3. The user should run _init_approvals(run=True) manually if needed
                else:
                    logger.info(f"  ✓ Conditional token allowance OK for {exchange_name}")
                    approval_results.append((exchange_name, exchange_addr, True))
            
            # Summary log
            if all_approved:
                logger.info("✅ All conditional token allowances are set - ready for selling")
            else:
                missing = [name for name, addr, approved in approval_results if approved is False]
                missing_addresses = [addr for name, addr, approved in approval_results if approved is False]
                
                # Try to set approvals automatically for the wallet where shares are (direct wallet)
                # Even if using proxy wallet for trading, shares are in direct wallet, so we need direct wallet allowances
                if missing_addresses:
                    # Check if we're checking direct wallet or proxy wallet
                    is_direct_wallet = (address_to_check == self.get_address_for_private_key())
                    if is_direct_wallet or not self.proxy_wallet_address:
                        logger.info(
                            f"🔧 Attempting to set conditional token allowances for: {', '.join(missing)} "
                            f"(wallet: {address_to_check[:10]}...{address_to_check[-8:]})"
                        )
                        all_set_successfully = True
                        import time
                        for idx, (exchange_addr, exchange_name) in enumerate(exchange_addresses):
                            if exchange_addr in missing_addresses:
                                # Small delay between approval transactions to allow previous one to propagate
                                if idx > 0:
                                    logger.info(f"⏳ Waiting 3 seconds before next approval transaction (to allow previous to propagate)...")
                                    time.sleep(3)
                                
                                logger.info(f"🔧 Setting approval for {exchange_name}...")
                                approval_result = self._set_conditional_token_approval(exchange_addr, exchange_name, wallet_address=address_to_check)
                                if approval_result:
                                    logger.info(f"✅ Successfully set approval for {exchange_name}")
                                    # Small delay after successful approval to allow blockchain state to update
                                    if idx < len([a for a in exchange_addresses if a[0] in missing_addresses]) - 1:
                                        logger.info(f"⏳ Waiting 2 seconds for blockchain state to update...")
                                        time.sleep(2)
                                else:
                                    logger.error(f"❌ Failed to set approval for {exchange_name}")
                                    logger.error(f"   This approval is required for placing sell orders. Please check the transaction above.")
                                    all_set_successfully = False
                                    # Continue trying other approvals even if one fails
                        
                        # Re-check approvals after setting them (with a small delay to allow blockchain to update)
                        if all_set_successfully:
                            logger.info("⏳ Waiting 3 seconds for approvals to propagate on blockchain...")
                            import time
                            time.sleep(3)
                            
                            logger.info("🔄 Re-checking conditional token allowances after setting them...")
                            all_approved = True
                            for exchange_addr, exchange_name in exchange_addresses:
                                is_approved = self.check_conditional_token_allowance(exchange_addr, address_to_check)
                                if is_approved is False:
                                    all_approved = False
                                    logger.warning(f"  ⚠️ Approval still not set for {exchange_name} - transaction may still be pending")
                                elif is_approved is True:
                                    logger.info(f"  ✓ Approval confirmed for {exchange_name}")
                    else:
                        logger.warning(
                            f"⚠️ Cannot auto-set allowances for proxy wallet. "
                            f"Shares are in direct wallet ({self.get_address_for_private_key()[:10]}...), "
                            f"but checking proxy wallet ({address_to_check[:10]}...). "
                            f"Please set allowances manually for direct wallet."
                        )
                
                if all_approved:
                    logger.info("✅ All conditional token allowances are set - ready for selling")
                else:
                    logger.warning(
                        f"⚠️ Conditional token allowances NOT set for: {', '.join(missing)}. "
                        f"This may cause 'not enough balance / allowance' errors when selling. "
                        f"Consider running _init_approvals(run=True) to set them."
                    )
            
            return all_approved
        except Exception as e:
            logger.error(f"❌ Error ensuring conditional token allowances: {e}", exc_info=True)
            return False
    
    def _set_conditional_token_approval(self, exchange_address: str, exchange_name: str, wallet_address: Optional[str] = None) -> bool:
        """
        Set conditional token approval for a specific exchange contract.
        
        Args:
            exchange_address: Exchange contract address to approve
            exchange_name: Human-readable name for logging
            wallet_address: Optional wallet address to set approval for. If None, uses direct wallet.
        
        Returns:
            True if approval was set successfully, False otherwise
        """
        try:
            if not self.private_key:
                logger.error("Cannot set approval: no private key available")
                return False
            
            # Use provided wallet address or default to direct wallet
            if wallet_address is None:
                wallet_address = self.get_address_for_private_key()
            
            # We can only sign transactions with the direct wallet's private key
            signer_address = self.get_address_for_private_key()
            if wallet_address != signer_address:
                logger.warning(
                    f"⚠️ Cannot set approval for {wallet_address[:10]}... using different private key. "
                    f"Only can set approval for direct wallet {signer_address[:10]}..."
                )
                return False
            
            logger.info(
                f"🔧 Setting conditional token approval for {exchange_name} "
                f"({exchange_address[:10]}...{exchange_address[-8:]}) "
                f"for wallet {wallet_address[:10]}...{wallet_address[-8:]}"
            )
            
            # Build transaction
            # Get nonce with 'pending' to include pending transactions
            nonce = self.web3.eth.get_transaction_count(wallet_address, 'pending')
            logger.debug(f"   Using nonce: {nonce} for approval transaction")
            
            approval_txn = self.ctf.functions.setApprovalForAll(
                exchange_address, True
            ).build_transaction({
                "chainId": self.chain_id,
                "from": wallet_address,
                "nonce": nonce,
                "gas": 100000,  # Reasonable gas limit for setApprovalForAll
                "gasPrice": self.web3.eth.gas_price
            })
            
            # Sign transaction
            signed_txn = self.web3.eth.account.sign_transaction(
                approval_txn, private_key=self.private_key
            )
            
            # Send transaction
            raw_tx = getattr(signed_txn, 'raw_transaction', None) or getattr(signed_txn, 'rawTransaction', None)
            if not raw_tx:
                raise ValueError("Could not extract raw transaction from signed transaction")
            tx_hash = self.web3.eth.send_raw_transaction(raw_tx)
            logger.info(f"Transaction sent: {tx_hash.hex()}")
            logger.info(f"Polygonscan: https://polygonscan.com/tx/{tx_hash.hex()}")
            
            # Wait for receipt
            logger.info(f"⏳ Waiting for transaction receipt (timeout: 300s)...")
            try:
                receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
                logger.info(f"✅ Transaction receipt received! Status: {receipt.status}")
            except Exception as e:
                logger.warning(f"⚠️ Timeout waiting for receipt, trying direct fetch: {e}")
                try:
                    receipt = self.web3.eth.get_transaction_receipt(tx_hash)
                    logger.info(f"✅ Retrieved receipt directly! Status: {receipt.status}")
                except Exception as get_error:
                    logger.error(
                        f"❌ Could not retrieve transaction receipt: {get_error}. "
                        f"Transaction may still be pending. Check Polygonscan: https://polygonscan.com/tx/{tx_hash.hex()}"
                    )
                    return False
            
            if receipt.status == 1:
                logger.info(
                    f"✅✅✅ Successfully set conditional token approval for {exchange_name}! ✅✅✅"
                )
                logger.info(f"   Transaction: {tx_hash.hex()}")
                logger.info(f"   Block: {receipt.blockNumber}, Gas used: {receipt.gasUsed}")
                return True
            else:
                logger.error(
                    f"❌ Failed to set conditional token approval for {exchange_name}. "
                    f"Transaction status: {receipt.status} (0 = failed, 1 = success)"
                )
                logger.error(f"   Transaction: {tx_hash.hex()}")
                logger.error(f"   Polygonscan: https://polygonscan.com/tx/{tx_hash.hex()}")
                return False
                
        except Exception as e:
            logger.error(
                f"❌ Error setting conditional token approval for {exchange_name}: {e}",
                exc_info=True
            )
            logger.error(
                f"❌ Error setting conditional token approval for {exchange_name}: {e}",
                exc_info=True
            )
            return False
    
    def split_position(
        self,
        condition_id: str,
        amount_usdc: float,
        check_approval: bool = True
    ) -> Optional[Dict]:
        """
        Split USDC into YES + NO shares using CTF contract's splitPosition function.
        
        This atomically converts $X USDC into X YES + X NO shares in one transaction.
        
        Args:
            condition_id: Condition ID (bytes32) from market data (market.conditionId)
            amount_usdc: Amount of USDC to split (will be converted to 6 decimals)
            check_approval: If True, check and log USDC approval status (default: True)
            
        Returns:
            Transaction receipt dict, or None if error
        """
        try:
            wallet_address = self.get_address_for_private_key()
            logger.info(
                f"🔧 Splitting position: wallet={wallet_address[:10]}...{wallet_address[-8:]}, "
                f"amount=${amount_usdc:.2f}, condition_id={condition_id[:20]}..."
            )
            
            # Convert condition_id to bytes32
            # Condition ID from API can be:
            # - Hex string (with or without 0x prefix)
            # - Integer string
            # - Integer
            if isinstance(condition_id, str):
                condition_id_str = condition_id.strip()
                if condition_id_str.startswith('0x'):
                    # Hex string with 0x prefix
                    hex_str = condition_id_str[2:]
                    # Pad to 64 hex chars (32 bytes) if needed
                    hex_str = hex_str.zfill(64)
                    condition_id_bytes32 = bytes.fromhex(hex_str)
                elif all(c in '0123456789abcdefABCDEF' for c in condition_id_str):
                    # Hex string without 0x prefix
                    hex_str = condition_id_str.zfill(64)
                    condition_id_bytes32 = bytes.fromhex(hex_str)
                else:
                    # Integer string - convert to bytes32
                    condition_id_int = int(condition_id_str)
                    condition_id_bytes32 = condition_id_int.to_bytes(32, byteorder='big')
            else:
                # Assume it's already an integer
                condition_id_int = int(condition_id)
                condition_id_bytes32 = condition_id_int.to_bytes(32, byteorder='big')
            
            # Ensure it's exactly 32 bytes
            if len(condition_id_bytes32) < 32:
                condition_id_bytes32 = condition_id_bytes32.rjust(32, b'\x00')
            elif len(condition_id_bytes32) > 32:
                condition_id_bytes32 = condition_id_bytes32[-32:]
            
            # Convert amount to 6 decimals (USDC uses 6 decimals)
            amount_raw = int(amount_usdc * 1e6)
            
            # Check USDC balance
            logger.info(f"Checking USDC balance for wallet: {wallet_address}")
            logger.info(f"  USDC contract: {self.usdc_address}")
            logger.info(f"  CTF contract: {self.ctf_address}")
            
            usdc_balance = self.usdc.functions.balanceOf(wallet_address).call()
            usdc_balance_float = float(usdc_balance) / 1e6
            logger.info(f"USDC balance: ${usdc_balance_float:.2f} (raw: {usdc_balance}, need ${amount_usdc:.2f})")
            
            if usdc_balance < amount_raw:
                logger.error(
                    f"Insufficient USDC balance: have ${usdc_balance_float:.2f}, "
                    f"need ${amount_usdc:.2f}"
                )
                logger.error(
                    f"💡 Make sure you sent USDC to this wallet address on Polygon network: {wallet_address}"
                )
                logger.error(
                    f"💡 Check your balance on Polygonscan: https://polygonscan.com/address/{wallet_address}"
                )
                return None
            
            # Check USDC approval for CTF contract if requested
            if check_approval:
                allowance = self.usdc.functions.allowance(wallet_address, self.ctf_address).call()
                allowance_float = float(allowance) / 1e6
                logger.info(
                    f"USDC allowance for CTF contract: ${allowance_float:.2f} "
                    f"(need ${amount_usdc:.2f})"
                )
                if allowance < amount_raw:
                    logger.warning(
                        f"USDC allowance insufficient: ${allowance_float:.2f} < ${amount_usdc:.2f}. "
                        f"Attempting to approve USDC for CTF contract..."
                    )
                    # Try to approve automatically
                    approve_result = self.approve_usdc_for_ctf(amount_usdc=None)  # Approve unlimited
                    if approve_result:
                        logger.info("✅ USDC approval successful, proceeding with split...")
                        # Re-check allowance after approval
                        allowance = self.usdc.functions.allowance(wallet_address, self.ctf_address).call()
                        allowance_float = float(allowance) / 1e6
                        logger.info(f"New USDC allowance: ${allowance_float:.2f}")
                    else:
                        logger.error(
                            f"Failed to approve USDC. Transaction will likely fail. "
                            f"Please approve USDC manually for CTF contract: {self.ctf_address}"
                        )
                        return None
            
            # Build splitPosition transaction
            # Parameters:
            # - collateralToken: USDC address
            # - parentCollectionId: bytes32(0) for Polymarket
            # - conditionId: bytes32 condition ID
            # - partition: [1, 2] for binary YES/NO markets
            # - amount: amount in USDC (6 decimals)
            
            parent_collection_id = b'\x00' * 32  # bytes32(0) for Polymarket
            partition = [1, 2]  # Binary market: YES (1) and NO (2)
            
            logger.info(
                f"Splitting ${amount_usdc:.2f} USDC into YES + NO shares "
                f"(condition_id: {condition_id[:20]}...)"
            )
            
            # Build transaction
            # Use 'pending' nonce to include pending transactions and avoid nonce conflicts
            nonce = self.web3.eth.get_transaction_count(wallet_address, 'pending')
            logger.debug(f"Using nonce: {nonce} (includes pending transactions)")
            
            # Use current gas price (Polygon is usually fast enough)
            # Note: Polygon blocks are ~2 seconds, so transactions confirm quickly
            gas_price = self.web3.eth.gas_price
            logger.debug(f"Gas price: {gas_price / 1e9:.2f} gwei")
            
            split_txn = self.ctf.functions.splitPosition(
                self.usdc_address,
                parent_collection_id,
                condition_id_bytes32,
                partition,
                amount_raw
            ).build_transaction({
                "chainId": self.chain_id,
                "from": wallet_address,
                "nonce": nonce,
                "gas": 500000,  # Reasonable gas limit for splitPosition
                "gasPrice": gas_price
            })
            
            # Sign transaction
            signed_txn = self.web3.eth.account.sign_transaction(
                split_txn, private_key=self.private_key
            )
            
            # Send transaction
            logger.info("Sending splitPosition transaction...")
            # Handle both old (rawTransaction) and new (raw_transaction) web3.py versions
            raw_tx = getattr(signed_txn, 'raw_transaction', None) or getattr(signed_txn, 'rawTransaction', None)
            if not raw_tx:
                raise ValueError("Could not extract raw transaction from signed transaction")
            
            try:
                tx_hash = self.web3.eth.send_raw_transaction(raw_tx)
                logger.info(f"Transaction sent: {tx_hash.hex()}")
            except Exception as send_error:
                logger.error(f"❌ Error sending transaction: {send_error}")
                logger.error(f"   This usually means the transaction was rejected before being broadcast")
                logger.error(f"   Common causes: nonce too high, gas price too low, or network issue")
                # Check current nonce
                try:
                    current_nonce = self.web3.eth.get_transaction_count(wallet_address, 'pending')
                    logger.info(f"   Current pending nonce: {current_nonce}, used nonce: {nonce}")
                    if nonce > current_nonce:
                        logger.warning(f"   ⚠️ Nonce {nonce} is higher than pending nonce {current_nonce} - transaction may be rejected")
                except Exception:
                    pass
                raise
            
            # Verify transaction was actually broadcast (check mempool)
            logger.info(f"Verifying transaction was broadcast to network...")
            try:
                # Wait a moment for transaction to propagate
                time.sleep(2)
                tx_check = self.web3.eth.get_transaction(tx_hash)
                if tx_check:
                    logger.info(f"✅ Transaction confirmed in mempool/blockchain")
                    logger.info(f"   From: {tx_check.get('from', 'unknown')}")
                    logger.info(f"   To: {tx_check.get('to', 'unknown')}")
                    logger.info(f"   Nonce: {tx_check.get('nonce', 'unknown')}")
                    logger.info(f"   Gas price: {tx_check.get('gasPrice', 'unknown')}")
                    block_num = tx_check.get('blockNumber')
                    if block_num:
                        logger.info(f"   Block: {block_num} (already mined!)")
                    else:
                        logger.info(f"   Status: Pending in mempool")
                else:
                    logger.warning(f"⚠️ Transaction hash returned but transaction not found in mempool")
                    logger.warning(f"   This may indicate a network connectivity issue")
            except Exception as verify_error:
                logger.warning(f"⚠️ Could not verify transaction broadcast: {verify_error}")
                logger.warning(f"   Transaction hash: {tx_hash.hex()}")
                logger.warning(f"   Will proceed to wait for receipt anyway...")
            
            # Wait for receipt
            logger.info(f"Waiting for transaction receipt (timeout: 300s)...")
            logger.info(f"Transaction hash: {tx_hash.hex()}")
            logger.info(f"Polygonscan: https://polygonscan.com/tx/{tx_hash.hex()}")
            
            receipt = None
            max_wait_time = 300  # seconds
            # Use faster polling: 1s for first 30s, then 2s after that
            start_time = time.time()
            
            try:
                logger.info(f"⏳ Polling for transaction receipt (checking every 1-2s, max {max_wait_time}s)...")
                # Poll manually with periodic status updates
                while (time.time() - start_time) < max_wait_time:
                    elapsed = time.time() - start_time
                    
                    # Use faster polling interval initially (1s), then slower (2s) after 30s
                    poll_interval = 1.0 if elapsed < 30 else 2.0
                    
                    try:
                        receipt = self.web3.eth.get_transaction_receipt(tx_hash)
                        if receipt is not None:
                            logger.info(f"✅ Transaction receipt received! Status: {receipt.status} (after {elapsed:.1f}s)")
                            break
                    except Exception:
                        # Receipt not available yet, continue polling
                        pass
                    
                    # Log progress more frequently initially
                    if elapsed < 30:
                        if int(elapsed) % 5 == 0:  # Every 5 seconds for first 30s
                            logger.info(f"⏳ Still waiting for receipt... ({int(elapsed)}s elapsed)")
                    elif int(elapsed) % 15 == 0:  # Every 15 seconds after 30s
                        logger.info(f"⏳ Still waiting for receipt... ({int(elapsed)}s elapsed)")
                    
                    time.sleep(poll_interval)
                
                # If we still don't have a receipt, try wait_for_transaction_receipt as fallback
                if receipt is None:
                    logger.warning(f"⚠️ Manual polling didn't find receipt, trying wait_for_transaction_receipt...")
                    try:
                        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                        logger.info(f"✅ Transaction receipt received via wait_for_transaction_receipt! Status: {receipt.status}")
                    except Exception as wait_error:
                        logger.warning(f"⚠️ wait_for_transaction_receipt also failed: {wait_error}")
                        
            except Exception as receipt_error:
                logger.warning(f"⚠️ Error waiting for transaction receipt: {receipt_error}")
                logger.info(f"⚠️ Attempting to check transaction status directly...")
            
            # Final attempt to get receipt directly
            if receipt is None:
                try:
                    receipt = self.web3.eth.get_transaction_receipt(tx_hash)
                    logger.info(f"✅ Retrieved receipt directly! Status: {receipt.status}")
                except Exception as get_error:
                    logger.error(f"❌ Could not retrieve receipt: {get_error}")
                    # Check if transaction is pending
                    try:
                        tx = self.web3.eth.get_transaction(tx_hash)
                        if tx is None:
                            logger.error(f"❌ Transaction not found in mempool or blockchain")
                        else:
                            block_num = tx.get('blockNumber')
                            if block_num:
                                logger.warning(f"⚠️ Transaction found in block {block_num} but receipt not available yet")
                            else:
                                logger.warning(f"⚠️ Transaction found but still pending in mempool")
                    except Exception as tx_error:
                        logger.error(f"❌ Could not check transaction status: {tx_error}")
                    logger.error(f"❌ Split transaction may have failed or is still pending")
                    logger.info(f"💡 Check transaction status manually: https://polygonscan.com/tx/{tx_hash.hex()}")
                    return None
            
            if receipt is None:
                logger.error(f"❌ No receipt available for transaction {tx_hash.hex()}")
                return None
            
            if receipt.status == 1:
                logger.info(f"✅✅✅ Split position successful! ✅✅✅")
                logger.info(f"   Transaction: {tx_hash.hex()}")
                logger.info(f"   Split ${amount_usdc:.2f} USDC → {amount_usdc:.2f} YES + {amount_usdc:.2f} NO shares")
                logger.info(f"   Block: {receipt.blockNumber}, Gas used: {receipt.gasUsed}")
                return {
                    "transaction_hash": tx_hash.hex(),
                    "receipt": receipt,
                    "status": "success",
                    "amount_usdc": amount_usdc,
                    "shares_per_side": amount_usdc
                }
            else:
                logger.error(f"❌❌❌ Split position failed! ❌❌❌")
                logger.error(f"   Transaction: {tx_hash.hex()}")
                logger.error(f"   Receipt status: {receipt.status} (0 = failed, 1 = success)")
                logger.error(f"   Check transaction: https://polygonscan.com/tx/{tx_hash.hex()}")
                return None
                
        except Exception as e:
            logger.error(f"❌❌❌ Error splitting position: {e}", exc_info=True)
            logger.error(f"   Full exception details logged above")
            return None
    
    def merge_positions(
        self,
        condition_id: str,
        amount_usdc: float
    ) -> Optional[Dict]:
        """
        Merge equal amounts of YES + NO shares back into USDC using CTF contract's mergePositions function.
        
        This atomically converts X YES + X NO shares back into $X USDC in one transaction.
        Requires equal amounts of YES and NO shares.
        
        Args:
            condition_id: Condition ID (bytes32) from market data (market.conditionId)
            amount_usdc: Amount of shares to merge (will convert X YES + X NO → $X USDC)
            
        Returns:
            Transaction receipt dict, or None if error
        """
        try:
            wallet_address = self.get_address_for_private_key()
            logger.info(
                f"🔧 Merging positions: wallet={wallet_address[:10]}...{wallet_address[-8:]}, "
                f"amount=${amount_usdc:.2f} (will merge {amount_usdc:.2f} YES + {amount_usdc:.2f} NO → ${amount_usdc:.2f} USDC), "
                f"condition_id={condition_id[:20]}..."
            )
            
            # Convert condition_id to bytes32 (same logic as split_position)
            if isinstance(condition_id, str):
                condition_id_str = condition_id.strip()
                if condition_id_str.startswith('0x'):
                    hex_str = condition_id_str[2:]
                    hex_str = hex_str.zfill(64)
                    condition_id_bytes32 = bytes.fromhex(hex_str)
                elif all(c in '0123456789abcdefABCDEF' for c in condition_id_str):
                    hex_str = condition_id_str.zfill(64)
                    condition_id_bytes32 = bytes.fromhex(hex_str)
                else:
                    condition_id_int = int(condition_id_str)
                    condition_id_bytes32 = condition_id_int.to_bytes(32, byteorder='big')
            else:
                condition_id_int = int(condition_id)
                condition_id_bytes32 = condition_id_int.to_bytes(32, byteorder='big')
            
            # Ensure it's exactly 32 bytes
            if len(condition_id_bytes32) < 32:
                condition_id_bytes32 = condition_id_bytes32.rjust(32, b'\x00')
            elif len(condition_id_bytes32) > 32:
                condition_id_bytes32 = condition_id_bytes32[-32:]
            
            # Convert amount to 6 decimals (USDC uses 6 decimals)
            amount_raw = int(amount_usdc * 1e6)
            
            # Build mergePositions transaction
            # Parameters same as splitPosition:
            # - collateralToken: USDC address
            # - parentCollectionId: bytes32(0) for Polymarket
            # - conditionId: bytes32 condition ID
            # - partition: [1, 2] for binary YES/NO markets
            # - amount: amount in USDC (6 decimals)
            
            parent_collection_id = b'\x00' * 32  # bytes32(0) for Polymarket
            partition = [1, 2]  # Binary market: YES (1) and NO (2)
            
            logger.info(
                f"Merging {amount_usdc:.2f} YES + {amount_usdc:.2f} NO shares back to ${amount_usdc:.2f} USDC "
                f"(condition_id: {condition_id[:20]}...)"
            )
            
            # Build transaction
            nonce = self.web3.eth.get_transaction_count(wallet_address)
            
            merge_txn = self.ctf.functions.mergePositions(
                self.usdc_address,
                parent_collection_id,
                condition_id_bytes32,
                partition,
                amount_raw
            ).build_transaction({
                "chainId": self.chain_id,
                "from": wallet_address,
                "nonce": nonce,
                "gas": 500000,  # Reasonable gas limit for mergePositions
                "gasPrice": self.web3.eth.gas_price
            })
            
            # Sign transaction
            signed_txn = self.web3.eth.account.sign_transaction(
                merge_txn, private_key=self.private_key
            )
            
            # Send transaction
            logger.info("Sending mergePositions transaction...")
            raw_tx = getattr(signed_txn, 'raw_transaction', None) or getattr(signed_txn, 'rawTransaction', None)
            if not raw_tx:
                raise ValueError("Could not extract raw transaction from signed transaction")
            tx_hash = self.web3.eth.send_raw_transaction(raw_tx)
            logger.info(f"Transaction sent: {tx_hash.hex()}")
            
            # Wait for receipt
            logger.info(f"Waiting for transaction receipt (timeout: 300s)...")
            logger.info(f"Transaction hash: {tx_hash.hex()}")
            logger.info(f"Polygonscan: https://polygonscan.com/tx/{tx_hash.hex()}")
            
            try:
                receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            except Exception as e:
                logger.warning(f"Timeout waiting for receipt, trying direct fetch: {e}")
                receipt = self.web3.eth.get_transaction_receipt(tx_hash)
            
            if receipt.status == 1:
                logger.info(f"✅✅✅ Merge positions successful! ✅✅✅")
                logger.info(f"   Transaction: {tx_hash.hex()}")
                logger.info(f"   Merged {amount_usdc:.2f} YES + {amount_usdc:.2f} NO → ${amount_usdc:.2f} USDC")
                logger.info(f"   Block: {receipt.blockNumber}, Gas used: {receipt.gasUsed}")
                return {
                    "transaction_hash": tx_hash.hex(),
                    "receipt": receipt,
                    "status": "success",
                    "amount_usdc": amount_usdc
                }
            else:
                logger.error(f"❌❌❌ Merge positions failed! ❌❌❌")
                logger.error(f"   Transaction: {tx_hash.hex()}")
                logger.error(f"   Receipt status: {receipt.status} (0 = failed, 1 = success)")
                logger.error(f"   Check transaction: https://polygonscan.com/tx/{tx_hash.hex()}")
                return None
                
        except Exception as e:
            logger.error(f"❌❌❌ Error merging positions: {e}", exc_info=True)
            logger.error(f"   Full exception details logged above")
            return None
    
    def get_notifications(self) -> Optional[List[Dict]]:
        """
        Get notifications from Polymarket API.
        Notifications include order fills, cancellations, and other events.
        
        Returns:
            List of notification dictionaries, or None if error
        """
        try:
            notifications = self.client.get_notifications()
            return notifications
        except Exception as e:
            logger.error(f"Error getting notifications: {e}", exc_info=True)
            return None


def test():
    host = "https://clob.polymarket.com"
    key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    print(key)
    chain_id = POLYGON

    # Create CLOB client and get/set API credentials
    client = ClobClient(host, key=key, chain_id=chain_id)
    client.set_api_creds(client.create_or_derive_api_creds())

    creds = ApiCreds(
        api_key=os.getenv("CLOB_API_KEY"),
        api_secret=os.getenv("CLOB_SECRET"),
        api_passphrase=os.getenv("CLOB_PASS_PHRASE"),
    )
    chain_id = AMOY
    client = ClobClient(host, key=key, chain_id=chain_id, creds=creds)

    print(client.get_markets())
    print(client.get_simplified_markets())
    print(client.get_sampling_markets())
    print(client.get_sampling_simplified_markets())
    print(client.get_market("condition_id"))

    print("Done!")


def gamma():
    url = "https://gamma-com"
    markets_url = url + "/markets"
    res = httpx.get(markets_url)
    code = res.status_code
    if code == 200:
        markets: list[SimpleMarket] = []
        data = res.json()
        for market in data:
            try:
                market_data = {
                    "id": int(market["id"]),
                    "question": market["question"],
                    # "start": market['startDate'],
                    "end": market["endDate"],
                    "description": market["description"],
                    "active": market["active"],
                    "deployed": market["deployed"],
                    "funded": market["funded"],
                    # "orderMinSize": float(market['orderMinSize']) if market['orderMinSize'] else 0,
                    # "orderPriceMinTickSize": float(market['orderPriceMinTickSize']),
                    "rewardsMinSize": float(market["rewardsMinSize"]),
                    "rewardsMaxSpread": float(market["rewardsMaxSpread"]),
                    "volume": float(market["volume"]),
                    "spread": float(market["spread"]),
                    "outcome_a": str(market["outcomes"][0]),
                    "outcome_b": str(market["outcomes"][1]),
                    "outcome_a_price": str(market["outcomePrices"][0]),
                    "outcome_b_price": str(market["outcomePrices"][1]),
                }
                markets.append(SimpleMarket(**market_data))
            except Exception as err:
                print(f"error {err} for market {id}")
        pdb.set_trace()
    else:
        raise Exception()


def main():
    # auth()
    # test()
    # gamma()
    print(Polymarket().get_all_events())


if __name__ == "__main__":
    load_dotenv()

    p = Polymarket()

    # k = p.get_api_key()
    # m = p.get_sampling_simplified_markets()

    # print(m)
    # m = p.get_market('11015470973684177829729219287262166995141465048508201953575582100565462316088')

    # t = m[0]['token_id']
    # o = p.get_orderbook(t)
    # pdb.set_trace()

    """
    
    (Pdb) pprint(o)
            OrderBookSummary(
                market='0x26ee82bee2493a302d21283cb578f7e2fff2dd15743854f53034d12420863b55', 
                asset_id='11015470973684177829729219287262166995141465048508201953575582100565462316088', 
                bids=[OrderSummary(price='0.01', size='600005'), OrderSummary(price='0.02', size='200000'), ...
                asks=[OrderSummary(price='0.99', size='100000'), OrderSummary(price='0.98', size='200000'), ...
            )
    
    """

    # https://polygon-rpc.com

    test_market_token_id = (
        "101669189743438912873361127612589311253202068943959811456820079057046819967115"
    )
    test_market_data = p.get_market(test_market_token_id)

    # test_size = 0.0001
    test_size = 1
    test_side = BUY
    test_price = float(ast.literal_eval(test_market_data["outcome_prices"])[0])

    # order = p.execute_order(
    #    test_price,
    #    test_size,
    #    test_side,
    #    test_market_token_id,
    # )

    # order = p.execute_market_order(test_price, test_market_token_id)

    balance = p.get_usdc_balance()
