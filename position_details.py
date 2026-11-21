#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from web3 import Web3

from database import (
    PENDING_FEE_TABLES,
    POSITION_PROTOCOLS,
    get_position_detail_snapshot,
    get_position_performance_data,
)
from pricing import (
    RPC_URLS,
    get_eur_usd_rate,
    get_token_usd_price,
    normalize_chain,
)

Q128 = 1 << 128
Q64 = 1 << 64
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
getcontext().prec = 80
logger = logging.getLogger(__name__)
ICON_CACHE_DIR = Path(__file__).resolve().parent / "icons"
ICON_CHAIN_DIRECTORIES = {
    "eth": "ethereum",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
    "bsc": "smartchain",
    "base": "base",
    "optimism": "optimism",
    "robinhood": "robinhoodchain",
    "solana": "solana",
}
ICON_PATH_OVERRIDES = {
    ("base", "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"): "bitcoin/info/logo.png",
}
TOKEN_ICON_URL_OVERRIDES = {
    "eure": "https://coin-images.coingecko.com/coins/images/54303/small/eure.jpg?1739167959",
}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_TOKEN_ICON_BYTES = 2 * 1024 * 1024

V3_MANAGERS = {
    "arbitrum": "0xc36442b4a4522e871399cd717abdd847ab11fe88",
    "base": "0x03a520b32c04bf3beef7beb72e919cf822ed34f1",
    "bsc": "0x7b8A01B39D58278b5DE7e48c8449c9f4F5170613",
    "ethereum": "0xc36442b4a4522e871399cd717abdd847ab11fe88",
    "optimism": "0xc36442b4a4522e871399cd717abdd847ab11fe88",
    "polygon": "0xc36442b4a4522e871399cd717abdd847ab11fe88",
    "robinhood": "0x73991a25c818bf1f1128deaab1492d45638de0d3",
}
PANCAKESWAP_MANAGER = "0x46a15b0b27311cedf172ab29e4f4766fbe7f4364"
AERO_FACTORY = "0x9cb220581b91b0c83223012fe69688b92ad07a76"
V4_CONFIG = {
    "arbitrum": (
        "0xd88f38f930b7952f2db2432cb002e7abbf3dd869",
        "0x76fd297e2d437cd7f76d50f01afe6160f86e9990",
    ),
    "ethereum": (
        "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e",
        "0x7ffe42c4a5deea5b0fec41c94c136cf115597227",
    ),
    "robinhood": (
        "0x58daec3116aae6d93017baaea7749052e8a04fa7",
        "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b",
    ),
}

POSITION_ABI = [
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "positions",
        "outputs": [
            {"type": "uint96"}, {"type": "address"}, {"type": "address"},
            {"type": "address"}, {"type": "uint24"}, {"type": "int24"},
            {"type": "int24"}, {"type": "uint128"}, {"type": "uint256"},
            {"type": "uint256"}, {"type": "uint128"}, {"type": "uint128"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "factory",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "ownerOf",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]
AERO_POSITION_ABI = [
    {
        **POSITION_ABI[0],
        "outputs": [
            {"type": "uint96"}, {"type": "address"}, {"type": "address"},
            {"type": "address"}, {"type": "int24"}, {"type": "int24"},
            {"type": "int24"}, {"type": "uint128"}, {"type": "uint256"},
            {"type": "uint256"}, {"type": "uint128"}, {"type": "uint128"},
        ],
    },
    *POSITION_ABI[1:],
]
FACTORY_ABI = [
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]
AERO_FACTORY_ABI = [
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "tickSpacing", "type": "int24"},
        ],
        "name": "getPool",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "positionManager",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]
POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"type": "uint160"}, {"type": "int24"}, {"type": "uint16"},
            {"type": "uint16"}, {"type": "uint16"}, {"type": "uint8"},
            {"type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "feeGrowthGlobal0X128",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "feeGrowthGlobal1X128",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "tick", "type": "int24"}],
        "name": "ticks",
        "outputs": [
            {"type": "uint128"}, {"type": "int128"}, {"type": "uint256"},
            {"type": "uint256"}, {"type": "int56"}, {"type": "uint160"},
            {"type": "uint32"}, {"type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]
AERO_POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"type": "uint160"}, {"type": "int24"}, {"type": "uint16"},
            {"type": "uint16"}, {"type": "uint16"}, {"type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    POOL_ABI[1],
    POOL_ABI[2],
    {
        **POOL_ABI[3],
        "outputs": [
            {"type": "uint128"}, {"type": "int128"}, {"type": "int128"},
            {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"},
            {"type": "int56"}, {"type": "uint160"}, {"type": "uint32"},
            {"type": "bool"},
        ],
    },
    {
        "inputs": [],
        "name": "gauge",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]
PANCAKESWAP_POOL_ABI = [
    {
        **POOL_ABI[0],
        "outputs": [
            {"type": "uint160"}, {"type": "int24"}, {"type": "uint16"},
            {"type": "uint16"}, {"type": "uint16"}, {"type": "uint32"},
            {"type": "bool"},
        ],
    },
    *POOL_ABI[1:],
]
TOKEN_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
]
V4_POSITION_MANAGER_ABI = [
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "getPoolAndPositionInfo",
        "outputs": [
            {
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
                "name": "poolKey",
                "type": "tuple",
            },
            {"name": "info", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "ownerOf",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]
V4_STATE_VIEW_ABI = [
    {
        "inputs": [{"name": "poolId", "type": "bytes32"}],
        "name": "getSlot0",
        "outputs": [
            {"type": "uint160"}, {"type": "int24"},
            {"type": "uint24"}, {"type": "uint24"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "poolId", "type": "bytes32"},
            {"name": "owner", "type": "address"},
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
            {"name": "salt", "type": "bytes32"},
        ],
        "name": "getPositionInfo",
        "outputs": [{"type": "uint128"}, {"type": "uint256"}, {"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "poolId", "type": "bytes32"},
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
        ],
        "name": "getFeeGrowthInside",
        "outputs": [{"type": "uint256"}, {"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

EXPLORERS = {
    "arbitrum": "https://arbiscan.io",
    "base": "https://basescan.org",
    "bsc": "https://bscscan.com",
    "eth": "https://etherscan.io",
    "ethereum": "https://etherscan.io",
    "optimism": "https://optimistic.etherscan.io",
    "polygon": "https://polygonscan.com",
    "robinhood": "https://robinhoodchain.blockscout.com",
}
SOLANA_PROGRAMS = {
    "orca": "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    "raydium": "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
}


class ClosedPositionError(Exception):
    pass


class UnsupportedPositionError(Exception):
    pass


def _v3_amounts(
    liquidity: int,
    sqrt_price_x96: int,
    tick_lower: int,
    tick_upper: int,
) -> tuple[Decimal, Decimal]:
    sqrt_price = Decimal(sqrt_price_x96) / Decimal(1 << 96)
    sqrt_lower = Decimal("1.0001") ** (Decimal(tick_lower) / 2)
    sqrt_upper = Decimal("1.0001") ** (Decimal(tick_upper) / 2)
    liquidity_value = Decimal(liquidity)

    if sqrt_price <= sqrt_lower:
        amount_0 = liquidity_value * (sqrt_upper - sqrt_lower) / (
            sqrt_lower * sqrt_upper
        )
        amount_1 = Decimal(0)
    elif sqrt_price < sqrt_upper:
        amount_0 = liquidity_value * (sqrt_upper - sqrt_price) / (
            sqrt_price * sqrt_upper
        )
        amount_1 = liquidity_value * (sqrt_price - sqrt_lower)
    else:
        amount_0 = Decimal(0)
        amount_1 = liquidity_value * (sqrt_upper - sqrt_lower)
    return amount_0, amount_1


def _fee_growth_inside(
    current_tick: int,
    tick_lower: int,
    tick_upper: int,
    fee_global: int,
    fee_outside_lower: int,
    fee_outside_upper: int,
) -> int:
    fee_below = (
        (fee_global - fee_outside_lower) % (1 << 256)
        if current_tick < tick_lower
        else fee_outside_lower
    )
    fee_above = (
        fee_outside_upper
        if current_tick < tick_upper
        else (fee_global - fee_outside_upper) % (1 << 256)
    )
    return (fee_global - fee_below - fee_above) % (1 << 256)


def _rpc_for_chain(chain: str) -> tuple[str, str]:
    chain_key = normalize_chain(chain)
    rpc_environment_names = {
        "eth": ("ETHEREUM_RPC_URL", "ETH_RPC_URL"),
        "arbitrum": ("ARBITRUM_RPC_URL",),
        "base": ("BASE_RPC_URL",),
        "bsc": ("BSC_RPC_URL",),
        "optimism": ("OPTIMISM_RPC_URL",),
        "polygon": ("POLYGON_RPC_URL",),
        "robinhood": ("ROBINHOOD_RPC_URL",),
    }
    rpc_url = next(
        (
            os.getenv(name)
            for name in rpc_environment_names.get(chain_key, ())
            if os.getenv(name)
        ),
        RPC_URLS.get(chain_key),
    )
    if not rpc_url:
        raise RuntimeError(f"Kein RPC-Endpunkt für {chain_key} konfiguriert.")
    return chain_key, rpc_url


def _token_metadata(w3: Web3, address: str) -> tuple[str, int]:
    token = w3.eth.contract(
        address=Web3.to_checksum_address(address),
        abi=TOKEN_ABI,
    )
    decimals = int(token.functions.decimals().call())
    symbol = str(token.functions.symbol().call())
    return symbol, decimals


def _web3_for_endpoint(rpc_url: str) -> Web3:
    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))


def _get_evm_details(
    table: str,
    position_id: str,
    snapshot: dict[str, Any],
    chain: str,
) -> dict[str, Any]:
    try:
        return _get_v3_details(table, position_id, snapshot, chain)
    except (ClosedPositionError, UnsupportedPositionError) as v3_error:
        v4_config = V4_CONFIG.get(chain)
        if POSITION_PROTOCOLS[table] != "Uniswap" or v4_config is None:
            raise
        try:
            return _get_v4_details(table, position_id, snapshot, chain, v4_config)
        except ClosedPositionError:
            if isinstance(v3_error, ClosedPositionError):
                raise v3_error
            raise


def _get_v3_details(
    table: str,
    position_id: str,
    snapshot: dict[str, Any],
    chain: str,
) -> dict[str, Any]:
    if not position_id.isdecimal():
        raise UnsupportedPositionError("Die EVM-Positions-ID ist nicht numerisch.")
    chain_key, rpc_url = _rpc_for_chain(chain)
    w3 = _web3_for_endpoint(rpc_url)
    is_aero = table == "aero"
    manager_address = (
        None
        if is_aero
        else PANCAKESWAP_MANAGER
        if table == "cake"
        else V3_MANAGERS.get(chain)
    )
    manager_abi = AERO_POSITION_ABI if is_aero else POSITION_ABI

    if is_aero:
        factory = w3.eth.contract(
            address=Web3.to_checksum_address(AERO_FACTORY),
            abi=AERO_FACTORY_ABI,
        )
        manager_address = factory.functions.positionManager().call()
    if not manager_address:
        raise UnsupportedPositionError(f"Kein Position Manager für {chain} konfiguriert.")

    manager = w3.eth.contract(
        address=Web3.to_checksum_address(manager_address),
        abi=manager_abi,
    )
    token_id = int(position_id)
    try:
        owner = manager.functions.ownerOf(token_id).call()
        position = manager.functions.positions(token_id).call()
    except Exception as error:
        message = str(error).lower()
        if any(term in message for term in ("execution reverted", "nonexistent", "invalid token id")):
            raise ClosedPositionError("Position inzwischen geschlossen") from error
        raise RuntimeError(f"On-Chain-Position konnte nicht geladen werden: {error}") from error

    token_0, token_1 = position[2], position[3]
    snapshot_tokens = (
        snapshot["token_0"].lower(),
        snapshot["token_1"].lower(),
    )
    if (token_0.lower(), token_1.lower()) != snapshot_tokens:
        raise UnsupportedPositionError(
            "Die Token des EVM-Position-Managers stimmen nicht mit dem aktuellen Datenbank-Snapshot überein."
        )
    fee_or_spacing = int(position[4])
    tick_lower, tick_upper, liquidity = int(position[5]), int(position[6]), int(position[7])
    if liquidity == 0:
        raise ClosedPositionError("Position inzwischen geschlossen")

    factory_address = (
        AERO_FACTORY
        if is_aero
        else manager.functions.factory().call()
    )
    factory = w3.eth.contract(
        address=Web3.to_checksum_address(factory_address),
        abi=AERO_FACTORY_ABI if is_aero else FACTORY_ABI,
    )
    pool_address = factory.functions.getPool(
        Web3.to_checksum_address(token_0),
        Web3.to_checksum_address(token_1),
        fee_or_spacing,
    ).call()
    if pool_address.lower() == ZERO_ADDRESS.lower():
        raise ClosedPositionError("Position inzwischen geschlossen")

    pool = w3.eth.contract(
        address=Web3.to_checksum_address(pool_address),
        abi=(
            AERO_POOL_ABI
            if is_aero
            else PANCAKESWAP_POOL_ABI
            if table == "cake"
            else POOL_ABI
        ),
    )
    slot0 = pool.functions.slot0().call()
    sqrt_price_x96, current_tick = int(slot0[0]), int(slot0[1])
    is_staked = (
        is_aero
        and pool.functions.gauge().call().lower() == owner.lower()
    )
    if is_aero:
        fee_outside_lower_index, fee_outside_upper_index = 3, 4
    else:
        fee_outside_lower_index, fee_outside_upper_index = 2, 3
    tick_data_lower, tick_data_upper = (
        pool.functions.ticks(tick_lower).call(),
        pool.functions.ticks(tick_upper).call(),
    )
    fee_growth_inside_0 = _fee_growth_inside(
        current_tick,
        tick_lower,
        tick_upper,
        int(pool.functions.feeGrowthGlobal0X128().call()),
        int(tick_data_lower[fee_outside_lower_index]),
        int(tick_data_upper[fee_outside_lower_index]),
    )
    fee_growth_inside_1 = _fee_growth_inside(
        current_tick,
        tick_lower,
        tick_upper,
        int(pool.functions.feeGrowthGlobal1X128().call()),
        int(tick_data_lower[fee_outside_upper_index]),
        int(tick_data_upper[fee_outside_upper_index]),
    )
    owed_0 = int(position[10]) + (
        liquidity * (fee_growth_inside_0 - int(position[8])) % (1 << 256)
    ) // Q128
    owed_1 = int(position[11]) + (
        liquidity * (fee_growth_inside_1 - int(position[9])) % (1 << 256)
    ) // Q128
    if is_staked:
        owed_0 = 0
        owed_1 = 0

    symbol_0, decimals_0 = _token_metadata(w3, token_0)
    symbol_1, decimals_1 = _token_metadata(w3, token_1)
    amount_0_raw, amount_1_raw = _v3_amounts(
        liquidity, sqrt_price_x96, tick_lower, tick_upper
    )
    amount_0 = amount_0_raw / Decimal(10**decimals_0)
    amount_1 = amount_1_raw / Decimal(10**decimals_1)
    fee_0 = Decimal(owed_0) / Decimal(10**decimals_0)
    fee_1 = Decimal(owed_1) / Decimal(10**decimals_1)

    token_values = [
        _token_detail(token_0, symbol_0, amount_0),
        _token_detail(token_1, symbol_1, amount_1),
    ]
    fee_values = [
        _token_detail(token_0, symbol_0, fee_0),
        _token_detail(token_1, symbol_1, fee_1),
    ]
    if snapshot["reward_token"] and snapshot["reward_amount"]:
        reward = _token_detail(
            snapshot["reward_token"],
            snapshot["reward_symbol"],
            snapshot["reward_amount"],
        )
        fee_values.append(reward)

    price = (
        (Decimal(sqrt_price_x96) / Decimal(1 << 96)) ** 2
        * Decimal(10) ** Decimal(decimals_0 - decimals_1)
    )
    price_lower = Decimal("1.0001") ** tick_lower * Decimal(10) ** Decimal(
        decimals_0 - decimals_1
    )
    price_upper = Decimal("1.0001") ** tick_upper * Decimal(10) ** Decimal(
        decimals_0 - decimals_1
    )
    return _format_details(
        table,
        position_id,
        chain,
        "Pancakeswap" if table == "cake" else "Aero Slipstream" if is_aero else "Uniswap v3",
        token_values,
        fee_values,
        price,
        price_lower,
        price_upper,
        tick_lower <= current_tick < tick_upper,
        snapshot,
        chain_key,
        manager_address,
    )


def _get_v4_details(
    table: str,
    position_id: str,
    snapshot: dict[str, Any],
    chain: str,
    v4_config: tuple[str, str],
) -> dict[str, Any]:
    if not position_id.isdecimal():
        raise UnsupportedPositionError("Die EVM-Positions-ID ist nicht numerisch.")
    chain_key, rpc_url = _rpc_for_chain(chain)
    w3 = _web3_for_endpoint(rpc_url)
    manager_address, state_view_address = v4_config
    manager = w3.eth.contract(
        address=Web3.to_checksum_address(manager_address),
        abi=V4_POSITION_MANAGER_ABI,
    )
    token_id = int(position_id)
    try:
        manager.functions.ownerOf(token_id).call()
        pool_key, position_info = manager.functions.getPoolAndPositionInfo(
            token_id
        ).call()
    except Exception as error:
        message = str(error).lower()
        if any(term in message for term in ("execution reverted", "nonexistent", "invalid token id")):
            raise ClosedPositionError("Position inzwischen geschlossen") from error
        raise RuntimeError(f"Uniswap-v4-Position konnte nicht geladen werden: {error}") from error

    token_0, token_1, fee, tick_spacing, hooks = pool_key
    if (token_0.lower(), token_1.lower()) != (
        snapshot["token_0"].lower(),
        snapshot["token_1"].lower(),
    ):
        raise UnsupportedPositionError(
            "Die Token des Uniswap-v4-Position-Managers stimmen nicht mit dem Datenbank-Snapshot überein."
        )
    tick_lower = _decode_int24((int(position_info) >> 8) & 0xFFFFFF)
    tick_upper = _decode_int24((int(position_info) >> 32) & 0xFFFFFF)
    pool_key_bytes = w3.codec.encode(
        ["address", "address", "uint24", "int24", "address"],
        [token_0, token_1, fee, tick_spacing, hooks],
    )
    pool_id = Web3.keccak(pool_key_bytes)
    state_view = w3.eth.contract(
        address=Web3.to_checksum_address(state_view_address),
        abi=V4_STATE_VIEW_ABI,
    )
    sqrt_price_x96, current_tick, _, _ = state_view.functions.getSlot0(
        pool_id
    ).call()
    liquidity, fee_growth_last_0, fee_growth_last_1 = (
        state_view.functions.getPositionInfo(
            pool_id,
            Web3.to_checksum_address(manager_address),
            tick_lower,
            tick_upper,
            token_id.to_bytes(32, "big"),
        ).call()
    )
    if liquidity == 0:
        raise ClosedPositionError("Position inzwischen geschlossen")
    fee_growth_inside_0, fee_growth_inside_1 = (
        state_view.functions.getFeeGrowthInside(
            pool_id,
            tick_lower,
            tick_upper,
        ).call()
    )
    fee_0 = Decimal(
        int(liquidity) * ((int(fee_growth_inside_0) - int(fee_growth_last_0)) % (1 << 256))
        // Q128
    )
    fee_1 = Decimal(
        int(liquidity) * ((int(fee_growth_inside_1) - int(fee_growth_last_1)) % (1 << 256))
        // Q128
    )
    symbol_0, decimals_0 = _token_metadata(w3, token_0)
    symbol_1, decimals_1 = _token_metadata(w3, token_1)
    amount_0_raw, amount_1_raw = _v3_amounts(
        int(liquidity), int(sqrt_price_x96), tick_lower, tick_upper
    )
    amount_0 = amount_0_raw / Decimal(10**decimals_0)
    amount_1 = amount_1_raw / Decimal(10**decimals_1)
    fee_0 /= Decimal(10**decimals_0)
    fee_1 /= Decimal(10**decimals_1)
    tokens = [
        _token_detail(token_0, symbol_0, amount_0),
        _token_detail(token_1, symbol_1, amount_1),
    ]
    fees = [
        _token_detail(token_0, symbol_0, fee_0),
        _token_detail(token_1, symbol_1, fee_1),
    ]
    price = (
        (Decimal(sqrt_price_x96) / Decimal(1 << 96)) ** 2
        * Decimal(10) ** Decimal(decimals_0 - decimals_1)
    )
    lower_price = Decimal("1.0001") ** tick_lower * Decimal(10) ** Decimal(
        decimals_0 - decimals_1
    )
    upper_price = Decimal("1.0001") ** tick_upper * Decimal(10) ** Decimal(
        decimals_0 - decimals_1
    )
    return _format_details(
        table,
        position_id,
        chain,
        "Uniswap v4",
        tokens,
        fees,
        price,
        lower_price,
        upper_price,
        tick_lower <= int(current_tick) < tick_upper,
        snapshot,
        chain_key,
        manager_address,
    )


def _decode_int24(value: int) -> int:
    value &= 0xFFFFFF
    return value - (1 << 24) if value & (1 << 23) else value


def _get_token_icon_url(address: str, chain_key: str) -> str | None:
    chain_directory = ICON_CHAIN_DIRECTORIES.get(chain_key)
    if not chain_directory:
        return None
    try:
        asset_address = (
            address
            if chain_key == "solana"
            else Web3.to_checksum_address(address)
        )
    except ValueError:
        logger.warning("Invalid token address for icon lookup: %s", address)
        return None
    if not asset_address.isalnum() or len(asset_address) > 64:
        logger.warning("Invalid token address for icon lookup: %s", address)
        return None

    icon_directory = ICON_CACHE_DIR / chain_directory / asset_address
    icon_path = icon_directory / "logo.png"
    if icon_path.is_file():
        try:
            with icon_path.open("rb") as cached_icon:
                if cached_icon.read(len(_PNG_SIGNATURE)) == _PNG_SIGNATURE:
                    return _local_token_icon_url(chain_directory, asset_address)
            icon_path.unlink()
        except OSError:
            logger.exception("Could not read cached token icon %s", icon_path)
            return None

    override_path = ICON_PATH_OVERRIDES.get(
        (chain_key, asset_address.lower())
    )
    if override_path:
        image_path = f"blockchains/{override_path}"
    else:
        image_path = (
            f"blockchains/{chain_directory}/assets/"
            f"{quote(asset_address, safe='')}/logo.png"
        )
    image_url = (
        "https://raw.githubusercontent.com/trustwallet/assets/master/"
        f"{image_path}"
    )
    try:
        with urllib.request.urlopen(image_url, timeout=3) as response:
            image_data = response.read(_MAX_TOKEN_ICON_BYTES + 1)
    except HTTPError as error:
        if error.code == 404:
            logger.debug("No Trust Wallet icon for %s on %s", address, chain_key)
        else:
            logger.warning(
                "Could not download Trust Wallet icon for %s on %s: HTTP %s",
                address,
                chain_key,
                error.code,
            )
        return None
    except (URLError, TimeoutError, OSError):
        logger.warning(
            "Could not download Trust Wallet icon for %s on %s",
            address,
            chain_key,
            exc_info=True,
        )
        return None

    if (
        len(image_data) > _MAX_TOKEN_ICON_BYTES
        or not image_data.startswith(_PNG_SIGNATURE)
    ):
        logger.warning("Trust Wallet returned an invalid PNG icon for %s", address)
        return None

    temporary_path: Path | None = None
    try:
        icon_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=icon_directory,
            prefix=".logo-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(image_data)
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, icon_path)
    except OSError:
        logger.exception("Could not cache Trust Wallet icon for %s", address)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.exception(
                    "Could not remove temporary token icon for %s",
                    address,
                )
        return None
    return _local_token_icon_url(chain_directory, asset_address)


def _local_token_icon_url(chain_directory: str, asset_address: str) -> str:
    return (
        f"/icons/{quote(chain_directory, safe='')}/"
        f"{quote(asset_address, safe='')}/logo.png"
    )


def _token_detail(
    address: str,
    symbol: str,
    amount: Decimal,
) -> dict[str, Any]:
    return {
        "address": address,
        "symbol": symbol,
        "amount": float(amount),
        "amount_exact": str(amount),
        "price_usd": None,
        "value_usd": None,
        "icon_url": None,
    }


def _format_details(
    table: str,
    position_id: str,
    chain: str,
    protocol: str,
    tokens: list[dict[str, Any]],
    fees: list[dict[str, Any]],
    price: Decimal,
    price_lower: Decimal,
    price_upper: Decimal,
    in_range: bool,
    snapshot: dict[str, Any],
    chain_key: str,
    manager_address: str,
) -> dict[str, Any]:
    currency = "USD"
    eur_usd_rate = Decimal(1)
    if snapshot.get("currency") == "EUR":
        eur_usd_rate = get_eur_usd_rate()
        currency = "EUR"

    performance_data = get_position_performance_data(
        table,
        position_id,
        chain,
        str(tokens[0]["address"]),
        str(tokens[1]["address"]),
    )
    collected_fee_tokens: list[dict[str, Any]] = []
    if performance_data is not None:
        for fee in performance_data["accrued_fees"]:
            collected_fee_tokens.append(
                _token_detail(
                    str(fee["token"]),
                    str(fee["symbol"]),
                    Decimal(str(fee["amount"])),
                )
            )

    prices_by_address: dict[str, Decimal | None] = {}
    for token in tokens + fees + collected_fee_tokens:
        address = str(token["address"])
        address_key = (
            address if chain_key == "solana" else address.lower()
        )
        if address_key in prices_by_address:
            continue
        try:
            prices_by_address[address_key] = get_token_usd_price(address, chain_key)
        except Exception:
            logger.exception(
                "Live token price unavailable for %s on %s",
                address,
                chain_key,
            )
            prices_by_address[address_key] = None

    icons_by_address: dict[str, str | None] = {}
    for token in tokens + fees + collected_fee_tokens:
        address = str(token["address"])
        address_key = address if chain_key == "solana" else address.lower()
        if address_key not in icons_by_address:
            icons_by_address[address_key] = _get_token_icon_url(address, chain_key)

    for token in tokens + fees + collected_fee_tokens:
        address = str(token["address"])
        address_key = address if chain_key == "solana" else address.lower()
        token["icon_url"] = TOKEN_ICON_URL_OVERRIDES.get(
            str(token["symbol"]).casefold(),
            icons_by_address[address_key],
        )
        live_price = prices_by_address[address_key]
        if live_price is not None:
            value_usd = Decimal(str(token["amount"])) * live_price
            token["price_usd"] = float(live_price)
            token["value_usd"] = float(value_usd)

    def convert(value: float | None) -> float | None:
        if value is None:
            return None
        return value / float(eur_usd_rate)

    for token in tokens + fees + collected_fee_tokens:
        token["value"] = convert(token["value_usd"])
        token["price"] = convert(token["price_usd"])
    position_value = (
        sum(token["value"] for token in tokens)
        if all(token["value"] is not None for token in tokens)
        else None
    )
    fee_value = (
        sum(token["value"] for token in fees)
        if all(token["value"] is not None for token in fees)
        else None
    )
    initial_value = None
    hodl_value = None
    performance: dict[str, float | None] = {
        "fees_rewards_return": None,
        "position_return": None,
        "total_roi": None,
        "impermanent_loss": None,
    }
    if performance_data is not None:
        if currency == "EUR":
            initial_value_raw = performance_data["entry_value_eur"]
        else:
            initial_value_raw = performance_data["entry_value_usd"]
        if initial_value_raw is not None:
            initial_value = float(initial_value_raw)

        initial_amounts = (
            performance_data["amount_0"],
            performance_data["amount_1"],
        )
        for index, token in enumerate(tokens):
            initial_amount = initial_amounts[index]
            current_amount = Decimal(str(token["amount"]))
            token["initial_amount"] = float(initial_amount)
            token["amount_change"] = float(current_amount - initial_amount)
            token["amount_change_positive"] = current_amount >= initial_amount

        if all(token["value"] is not None for token in collected_fee_tokens):
            fees_rewards_value = sum(
                token["value"] for token in collected_fee_tokens
            )
        else:
            fees_rewards_value = None

        if all(
            prices_by_address[
                str(token["address"])
                if chain_key == "solana"
                else str(token["address"]).lower()
            ]
            is not None
            for token in tokens
        ):
            hodl_value = sum(
                float(initial_amounts[index])
                * float(
                    prices_by_address[
                        str(token["address"])
                        if chain_key == "solana"
                        else str(token["address"]).lower()
                    ]
                )
                for index, token in enumerate(tokens)
            )
            if currency == "EUR":
                hodl_value /= float(eur_usd_rate)

        if initial_value is not None and initial_value > 0:
            if position_value is not None:
                performance["position_return"] = (
                    position_value / initial_value - 1
                ) * 100
            if fees_rewards_value is not None:
                performance["fees_rewards_return"] = (
                    fees_rewards_value / initial_value
                ) * 100
                performance["total_roi"] = (
                    (position_value + fees_rewards_value - initial_value)
                    / initial_value
                    * 100
                    if position_value is not None
                    else None
                )
            if position_value is not None and hodl_value and hodl_value > 0:
                performance["impermanent_loss"] = (
                    position_value / hodl_value
                    - 1
                ) * 100

    price_marker = (
        min(
            100.0,
            max(
                0.0,
                float((price - price_lower) / (price_upper - price_lower) * 100),
            ),
        )
        if price_upper != price_lower
        else 50.0
    )
    explorer = EXPLORERS.get(chain_key)
    if chain_key == "robinhood":
        explorer_url = (
            f"{explorer}/token/{manager_address}/instance/{position_id}"
        )
    else:
        explorer_url = f"{explorer}/nft/{manager_address}/{position_id}" if explorer else None

    return {
        "position_id": position_id,
        "table": table,
        "chain": chain,
        "protocol": protocol,
        "position_date": (
            performance_data["date"]
            if performance_data is not None
            else None
        ),
        "currency": currency,
        "symbol": "€" if currency == "EUR" else "$",
        "tokens": tokens,
        "position_value": position_value,
        "fees": fees,
        "fees_value": fee_value,
        "initial_value": initial_value,
        "position_value_change": (
            position_value - initial_value
            if position_value is not None and initial_value is not None
            else None
        ),
        "performance": performance,
        "price": float(price),
        "price_lower": float(price_lower),
        "price_upper": float(price_upper),
        "price_marker": price_marker,
        "in_range": in_range,
        "explorer_url": explorer_url,
        "snapshot_date": snapshot["date"].isoformat(),
    }


def _base58(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = int.from_bytes(data, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = alphabet[remainder] + encoded
    leading_zeros = len(data) - len(data.lstrip(b"\0"))
    return "1" * leading_zeros + (encoded or ("" if leading_zeros else "1"))


def _base58_decode(value: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for character in value:
        number = number * 58 + alphabet.index(character)
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big")
    leading_ones = len(value) - len(value.lstrip("1"))
    return b"\0" * leading_ones + decoded


def _is_ed25519_point(encoded: bytes) -> bool:
    prime = (1 << 255) - 19
    y = int.from_bytes(encoded, "little") & ((1 << 255) - 1)
    if y >= prime:
        return False
    curve_d = (-121665 * pow(121666, prime - 2, prime)) % prime
    y_squared = y * y % prime
    x_squared = (
        (y_squared - 1) * pow(curve_d * y_squared + 1, prime - 2, prime)
    ) % prime
    return x_squared == 0 or pow(x_squared, (prime - 1) // 2, prime) == 1


def _derive_program_address(seeds: list[bytes], program_id: str) -> str:
    marker = b"ProgramDerivedAddress"
    program_bytes = _base58_decode(program_id)
    for bump in range(255, -1, -1):
        digest = hashlib.sha256(
            b"".join([*seeds, bytes([bump]), program_bytes, marker])
        ).digest()
        if not _is_ed25519_point(digest):
            return _base58(digest)
    raise RuntimeError(f"Could not derive a PDA for program {program_id}")


def _solana_rpc(method: str, params: list[Any]) -> Any:
    rpc_url = os.getenv("SOLANA_RPC_URL")
    if not rpc_url:
        api_key = os.getenv("HELIUS_API_KEY")
        if not api_key:
            orca_env = Path(__file__).resolve().parents[1] / "orca" / ".env"
            load_dotenv(orca_env, override=False)
            api_key = os.getenv("HELIUS_API_KEY")
        if not api_key:
            raise RuntimeError(
                "SOLANA_RPC_URL oder HELIUS_API_KEY muss in der .env gesetzt sein."
            )
        rpc_url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    request_data = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode("utf-8")
    request = urllib.request.Request(
        rpc_url,
        data=request_data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read())
    if "error" in result:
        raise RuntimeError(f"Solana RPC {method} failed: {result['error']}")
    return result["result"]


def _solana_account(address: str) -> tuple[str, bytes] | None:
    result = _solana_rpc(
        "getAccountInfo",
        [address, {"encoding": "base64", "commitment": "confirmed"}],
    )
    value = result.get("value")
    if value is None:
        return None
    return value["owner"], base64.b64decode(value["data"][0])


def _get_solana_details(
    table: str,
    position_id: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    account = _solana_account(position_id)
    program = SOLANA_PROGRAMS[table]
    explorer_position_id = position_id
    if table == "raydium" and (account is None or account[0] != program):
        personal_position = _derive_program_address(
            [b"position", _base58_decode(position_id)],
            program,
        )
        derived_account = _solana_account(personal_position)
        if derived_account is not None and derived_account[0] == program:
            account = derived_account
            explorer_position_id = personal_position
    if account is None:
        raise ClosedPositionError("Position inzwischen geschlossen")
    owner, data = account
    if owner != program:
        raise UnsupportedPositionError(
            "Die Positionskonto-Adresse gehört nicht zum erwarteten Protokoll."
        )

    if table == "orca":
        if len(data) < 0x90:
            raise RuntimeError("Orca-Positionskonto hat ein ungültiges Format.")
        liquidity = int.from_bytes(data[0x48:0x58], "little")
        tick_lower = int.from_bytes(data[0x58:0x5C], "little", signed=True)
        tick_upper = int.from_bytes(data[0x5C:0x60], "little", signed=True)
        pool_address = _base58(data[0x08:0x28])
        pool_account = _solana_account(pool_address)
        if pool_account is None:
            raise ClosedPositionError("Position inzwischen geschlossen")
        pool_owner, pool = pool_account
        if pool_owner != program:
            raise RuntimeError("Das Orca-Whirlpool-Konto gehört nicht zum Orca-Programm.")
        if len(pool) < 0x105:
            raise RuntimeError("Orca-Whirlpool-Konto hat ein ungültiges Format.")
        offset = 0x08 + 32 + 1
        tick_spacing = int.from_bytes(pool[offset:offset + 2], "little")
        offset += 2 + 2 + 2 + 2 + 16
        sqrt_price_x64 = int.from_bytes(pool[offset:offset + 16], "little")
        offset += 16 + 4 + 8 + 8
        mint_0 = _base58(pool[offset:offset + 32])
        offset += 32 + 32 + 16
        mint_1 = _base58(pool[offset:offset + 32])
        decimals_0 = _solana_token_decimals(mint_0)
        decimals_1 = _solana_token_decimals(mint_1)
        current_tick = int.from_bytes(pool[81:85], "little", signed=True)
    else:
        if len(data) < 145 or data[:8] != _anchor_discriminator("PersonalPositionState"):
            raise RuntimeError("Raydium-Positionskonto hat ein ungültiges Format.")
        tick_lower = int.from_bytes(data[73:77], "little", signed=True)
        tick_upper = int.from_bytes(data[77:81], "little", signed=True)
        liquidity = int.from_bytes(data[81:97], "little")
        pool_address = _base58(data[41:73])
        pool_account = _solana_account(pool_address)
        if pool_account is None:
            raise ClosedPositionError("Position inzwischen geschlossen")
        pool_owner, pool = pool_account
        if pool_owner != program:
            raise RuntimeError("Das Raydium-Pool-Konto gehört nicht zum Raydium-Programm.")
        if len(pool) < 309 or pool[:8] != _anchor_discriminator("PoolState"):
            raise RuntimeError("Raydium-Pool-Konto hat ein ungültiges Format.")
        mint_0 = _base58(pool[73:105])
        mint_1 = _base58(pool[105:137])
        decimals_0, decimals_1 = pool[233], pool[234]
        sqrt_price_x64 = int.from_bytes(pool[253:269], "little")
        current_tick = int.from_bytes(pool[269:273], "little", signed=True)
        tick_spacing = int.from_bytes(pool[235:237], "little")

    if (mint_0, mint_1) != (snapshot["token_0"], snapshot["token_1"]):
        raise UnsupportedPositionError(
            "Die Token des Solana-Pools stimmen nicht mit dem aktuellen Datenbank-Snapshot überein."
        )
    if liquidity == 0:
        raise ClosedPositionError("Position inzwischen geschlossen")
    sqrt_price = Decimal(sqrt_price_x64) / Decimal(Q64)
    sqrt_lower = Decimal("1.0001") ** (Decimal(tick_lower) / 2)
    sqrt_upper = Decimal("1.0001") ** (Decimal(tick_upper) / 2)
    liquidity_value = Decimal(liquidity)
    if sqrt_price <= sqrt_lower:
        raw_0 = liquidity_value * (sqrt_upper - sqrt_lower) / (
            sqrt_lower * sqrt_upper
        )
        raw_1 = Decimal(0)
    elif sqrt_price < sqrt_upper:
        raw_0 = liquidity_value * (sqrt_upper - sqrt_price) / (
            sqrt_price * sqrt_upper
        )
        raw_1 = liquidity_value * (sqrt_price - sqrt_lower)
    else:
        raw_0 = Decimal(0)
        raw_1 = liquidity_value * (sqrt_upper - sqrt_lower)
    amount_0 = raw_0 / Decimal(10**decimals_0)
    amount_1 = raw_1 / Decimal(10**decimals_1)

    tokens = [
        _token_detail(
            mint_0,
            snapshot["symbol_0"],
            amount_0,
        ),
        _token_detail(
            mint_1,
            snapshot["symbol_1"],
            amount_1,
        ),
    ]
    fees = [
        _token_detail(
            mint_0,
            snapshot["symbol_0"],
            snapshot["fee_0"],
        ),
        _token_detail(
            mint_1,
            snapshot["symbol_1"],
            snapshot["fee_1"],
        ),
    ]
    current_price = sqrt_price**2 * Decimal(10) ** Decimal(
        decimals_0 - decimals_1
    )
    lower_price = Decimal("1.0001") ** tick_lower * Decimal(10) ** Decimal(
        decimals_0 - decimals_1
    )
    upper_price = Decimal("1.0001") ** tick_upper * Decimal(10) ** Decimal(
        decimals_0 - decimals_1
    )
    return _format_details(
        table,
        position_id,
        "solana",
        POSITION_PROTOCOLS[table],
        tokens,
        fees,
        current_price,
        lower_price,
        upper_price,
        tick_lower <= current_tick < tick_upper,
        snapshot,
        "solana",
        "",
    ) | {
        "explorer_url": f"https://solscan.io/account/{explorer_position_id}",
        "tick_spacing": tick_spacing,
    }


def _anchor_discriminator(account_name: str) -> bytes:
    return hashlib.sha256(f"account:{account_name}".encode("utf-8")).digest()[:8]


def _solana_token_decimals(mint: str) -> int:
    account = _solana_account(mint)
    if account is None:
        raise RuntimeError(f"Token-Mint {mint} konnte nicht geladen werden.")
    return account[1][44]


def get_position_details(
    table: str,
    position_id: str,
    currency: str,
) -> dict[str, Any]:
    if table not in PENDING_FEE_TABLES:
        raise ValueError(f"Unbekannte Positionstabelle: {table}")
    snapshot = get_position_detail_snapshot(table, position_id)
    if snapshot is None:
        raise ClosedPositionError("Position inzwischen geschlossen")
    snapshot["currency"] = currency
    return _get_evm_details(table, position_id, snapshot, PENDING_FEE_TABLES[table]) \
        if PENDING_FEE_TABLES[table] != "solana" \
        else _get_solana_details(table, position_id, snapshot)
