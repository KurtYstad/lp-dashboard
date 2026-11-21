#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Backfill initial liquidity-position data and historical entry prices."""

from __future__ import annotations

import argparse
import asyncio
import base64
import html
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from functools import lru_cache
from threading import Lock
from typing import Any
from urllib.parse import quote

import httpx
from web3 import Web3

from database import PENDING_FEE_TABLES, POSITION_PROTOCOLS, _connect
from position_details import (
    AERO_FACTORY,
    AERO_FACTORY_ABI,
    AERO_POSITION_ABI,
    PANCAKESWAP_MANAGER,
    POSITION_ABI,
    SOLANA_PROGRAMS,
    V3_MANAGERS,
    V4_CONFIG,
    V4_POSITION_MANAGER_ABI,
    _base58,
    _base58_decode,
    _derive_program_address,
    _anchor_discriminator,
    _rpc_for_chain,
    _v3_amounts,
    V4_STATE_VIEW_ABI,
)
from pricing import (
    AGGREGATOR_V3_ABI,
    FEED_BY_CHAIN_ASSET,
    TOKEN_TO_ASSET,
    normalize_chain,
)

logger = logging.getLogger("positions")

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
SOURCE_TABLE_PROTOCOLS = {
    "aero": "aero",
    "arbitrum": "uniswap",
    "base": "uniswap",
    "bsc": "uniswap",
    "cake": "pancakeswap",
    "ethereum": "uniswap",
    "optimism": "uniswap",
    "orca": "orca",
    "polygon": "uniswap",
    "raydium": "raydium",
    "robinhood": "uniswap",
}
DEFILLAMA_CHAINS = {
    "eth": "ethereum",
    "arbitrum": "arbitrum",
    "base": "base",
    "bsc": "bsc",
    "optimism": "optimism",
    "polygon": "polygon",
    "robinhood": "robinhood",
    "solana": "solana",
}
GECKOTERMINAL_NETWORKS = {
    "eth": "eth",
    "arbitrum": "arbitrum",
    "base": "base",
    "bsc": "bsc",
    "optimism": "optimism",
    "polygon": "polygon_pos",
    "solana": "solana",
}
ERC20_DECIMALS_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    }
]
LIQUIDITY_EVENT_TOPIC = "0x" + Web3.keccak(
    text="IncreaseLiquidity(uint256,uint128,uint256,uint256)"
).hex().removeprefix("0x").lower()
MODIFY_LIQUIDITY_EVENT_TOPIC = "0x" + Web3.keccak(
    text="ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)"
).hex().removeprefix("0x").lower()
ERC20_TRANSFER_EVENT_TOPIC = "0x" + Web3.keccak(
    text="Transfer(address,address,uint256)"
).hex().removeprefix("0x").lower()
POOL_MINT_EVENT_TOPIC = "0x" + Web3.keccak(
    text="Mint(address,address,int24,int24,uint128,uint256,uint256)"
).hex().removeprefix("0x").lower()
V3_POOL_TOKEN_ABI = [
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]
PYTH_FEED_BY_ASSET = {
    "SOL": "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
}

CREATE_POSITIONS_SQL = """
CREATE TABLE IF NOT EXISTS `positions` (
    `uid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `id` VARCHAR(128) NOT NULL,
    `date` DATETIME(6) NULL,
    `protocol` VARCHAR(32) NOT NULL,
    `blockchain` VARCHAR(24) NOT NULL,
    `token_0` VARCHAR(128) NOT NULL,
    `symbol_0` VARCHAR(64) NOT NULL,
    `amount_0` DECIMAL(65, 30) NULL,
    `quote_usd_0` DECIMAL(38, 18) NULL,
    `quote_eur_0` DECIMAL(38, 18) NULL,
    `token_1` VARCHAR(128) NOT NULL,
    `symbol_1` VARCHAR(64) NOT NULL,
    `amount_1` DECIMAL(65, 30) NULL,
    `quote_usd_1` DECIMAL(38, 18) NULL,
    `quote_eur_1` DECIMAL(38, 18) NULL,
    `source` VARCHAR(20) NOT NULL DEFAULT 'internal',
    `source_table` VARCHAR(32) NOT NULL,
    `position_contract` VARCHAR(66) NOT NULL DEFAULT 'unknown',
    `position_account` VARCHAR(128) NULL,
    `position_version` VARCHAR(16) NULL,
    `mint_tx` VARCHAR(128) NULL,
    `mint_block` BIGINT UNSIGNED NULL,
    `mint_slot` BIGINT UNSIGNED NULL,
    `deposit_tx` VARCHAR(128) NULL,
    `deposit_date` DATETIME(6) NULL,
    `amount_source` VARCHAR(40) NULL,
    `quote_timestamp` DATETIME(6) NULL,
    `fx_source` VARCHAR(24) NULL,
    `fx_date` DATE NULL,
    `entry_value_usd` DECIMAL(65, 30) NULL,
    `entry_value_eur` DECIMAL(65, 30) NULL,
    `status` VARCHAR(16) NOT NULL DEFAULT 'pending',
    `last_error` TEXT NULL,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`uid`),
    UNIQUE KEY `uq_positions_source_id_contract`
        (
            `source_table`, `protocol`, `blockchain`, `id`,
            `position_contract`, `token_0`, `token_1`
        )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

POSITION_COLUMNS = (
    "id",
    "date",
    "protocol",
    "blockchain",
    "token_0",
    "symbol_0",
    "amount_0",
    "quote_usd_0",
    "quote_eur_0",
    "token_1",
    "symbol_1",
    "amount_1",
    "quote_usd_1",
    "quote_eur_1",
    "source",
    "source_table",
    "position_contract",
    "position_account",
    "position_version",
    "mint_tx",
    "mint_block",
    "mint_slot",
    "deposit_tx",
    "deposit_date",
    "amount_source",
    "quote_timestamp",
    "fx_source",
    "fx_date",
    "entry_value_usd",
    "entry_value_eur",
    "status",
    "last_error",
)


@dataclass(frozen=True)
class SourcePosition:
    source_table: str
    position_id: str
    blockchain: str
    protocol: str
    snapshot_date: datetime
    token_0: str
    symbol_0: str
    quote_0: Decimal | None
    token_1: str
    symbol_1: str
    quote_1: Decimal | None


@dataclass(frozen=True)
class MintData:
    date: datetime
    mint_tx: str
    mint_block: int | None
    mint_slot: int | None
    position_contract: str
    position_account: str | None
    position_version: str
    deposit_tx: str
    deposit_date: datetime
    amount_0: Decimal
    amount_1: Decimal
    amount_source: str


class RateLimiter:
    def __init__(self) -> None:
        self._lock = Lock()
        self._last_request: dict[str, float] = {}

    def wait(self, key: str, interval: float) -> None:
        with self._lock:
            now = time.monotonic()
            delay = interval - (now - self._last_request.get(key, 0.0))
            if delay > 0:
                time.sleep(delay)
            self._last_request[key] = time.monotonic()


class ProviderHTTPError(RuntimeError):
    def __init__(self, status_code: int, host: str) -> None:
        super().__init__(f"HTTP {status_code} returned by {host}")
        self.status_code = status_code


_rate_limiter = RateLimiter()
_CHAINLINK_DECIMALS_CACHE: dict[tuple[str, str], int] = {}
_SOLANA_RPC_URL: str | None = None


def _http_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    interval: float = 0.5,
) -> dict[str, Any]:
    rate_key = url.split("/", 3)[2]
    for attempt in range(5):
        _rate_limiter.wait(rate_key, interval)
        try:
            response = httpx.post(url, json=payload, params=params, timeout=30.0) \
                if payload is not None \
                else httpx.get(
                    url,
                    params=params,
                    timeout=30.0,
                    follow_redirects=True,
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 4:
                    raise ProviderHTTPError(response.status_code, rate_key)
                retry_after = response.headers.get("Retry-After")
                wait_seconds = (
                    float(retry_after)
                    if retry_after and retry_after.replace(".", "", 1).isdigit()
                    else min(2**attempt, 30)
                )
                time.sleep(wait_seconds)
                continue
            if response.status_code >= 400:
                raise ProviderHTTPError(response.status_code, rate_key)
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected JSON response from {rate_key}")
            error = data.get("error")
            if (
                payload is not None
                and isinstance(error, dict)
                and attempt < 4
                and (
                    error.get("code") in {-32005, -32016, 429}
                    or "rate limit" in str(error.get("message", "")).lower()
                )
            ):
                time.sleep(min(2**attempt, 30))
                continue
            return data
        except httpx.RequestError as error:
            if attempt == 4:
                raise RuntimeError(
                    f"Network request failed for {rate_key}: "
                    f"{type(error).__name__}"
                ) from None
            time.sleep(min(2**attempt, 30))
    raise RuntimeError(f"Request retries exhausted for {rate_key}")


def _evm_rpc(chain: str, method: str, params: list[Any]) -> Any:
    chain_key, rpc_url = _rpc_for_chain(chain)
    response = _http_json(
        rpc_url,
        payload={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        },
        interval=0.15,
    )
    if "error" in response:
        error = response["error"]
        raise RuntimeError(f"Alchemy {method} failed on {chain_key}: {error}")
    if "result" not in response:
        raise RuntimeError(f"Alchemy {method} returned no result on {chain_key}")
    return response["result"]


def _solana_rpc_result(method: str, params: list[Any]) -> Any:
    global _SOLANA_RPC_URL
    if _SOLANA_RPC_URL is None:
        _SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")
        if not _SOLANA_RPC_URL:
            api_key = os.getenv("HELIUS_API_KEY")
            if not api_key:
                raise RuntimeError("SOLANA_RPC_URL or HELIUS_API_KEY is required")
            _SOLANA_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={api_key}"

    response = _http_json(
        _SOLANA_RPC_URL,
        payload={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        },
        interval=0.2,
    )
    if "error" in response:
        raise RuntimeError(f"Helius {method} failed: {response['error']}")
    if "result" not in response:
        raise RuntimeError(f"Helius {method} returned no result")
    return response["result"]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(
        timezone.utc
    )


def _db_datetime(value: datetime | None) -> datetime | None:
    return _as_utc(value).replace(tzinfo=None) if value is not None else None


def _timestamp(value: datetime) -> int:
    return int(_as_utc(value).timestamp())


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    result = Decimal(str(value))
    return result if result.is_finite() else None


def _source_positions(connection: Any, only_table: str | None = None) -> list[SourcePosition]:
    tables = [only_table] if only_table else list(PENDING_FEE_TABLES)
    positions: list[SourcePosition] = []
    for table in tables:
        if table not in PENDING_FEE_TABLES:
            raise ValueError(f"Unknown source table: {table}")
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT id, `date`, token_0, symbol_0, quote_0,
                       token_1, symbol_1, quote_1
                FROM `{table}`
                WHERE id IS NOT NULL
                  AND `date` IS NOT NULL
                  AND token_0 IS NOT NULL
                  AND token_1 IS NOT NULL
                ORDER BY id, `date`, uid
                """
            )
            first_by_id: dict[tuple[str, str, str], tuple[Any, ...]] = {}
            for row in cursor.fetchall():
                key = (str(row[0]), str(row[2]), str(row[5]))
                first_by_id.setdefault(key, row)
        finally:
            cursor.close()

        for key, row in first_by_id.items():
            position_id = key[0]
            snapshot_date = row[1]
            if isinstance(snapshot_date, str):
                snapshot_date = datetime.fromisoformat(snapshot_date)
            positions.append(
                SourcePosition(
                    source_table=table,
                    position_id=position_id,
                    blockchain=PENDING_FEE_TABLES[table],
                    protocol=SOURCE_TABLE_PROTOCOLS.get(
                        table, POSITION_PROTOCOLS[table].lower()
                    ),
                    snapshot_date=snapshot_date,
                    token_0=str(row[2]),
                    symbol_0=str(row[3] or ""),
                    quote_0=_decimal(row[4]),
                    token_1=str(row[5]),
                    symbol_1=str(row[6] or ""),
                    quote_1=_decimal(row[7]),
                )
            )
    return positions


def _existing_position_status(
    connection: Any,
    position: SourcePosition,
) -> tuple[bool, bool, bool]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT `status`, `position_contract`
            FROM `positions`
            WHERE `source_table` = ? AND `id` = ?
              AND `token_0` = ? AND `token_1` = ?
            """,
            (
                position.source_table,
                position.position_id,
                position.token_0,
                position.token_1,
            ),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
    complete = any(str(row[0]) == "complete" for row in rows)
    has_stub = any(str(row[1]) == "unknown" for row in rows)
    return complete, has_stub, bool(rows)


def _save_position(
    connection: Any,
    position: SourcePosition,
    data: dict[str, Any],
    *,
    replace_stub: bool = False,
) -> None:
    contract = str(data.get("position_contract") or "unknown")
    cursor = connection.cursor()
    try:
        if replace_stub and contract != "unknown":
            cursor.execute(
                """
                UPDATE `positions`
                SET `position_contract` = ?
                WHERE `source_table` = ? AND `id` = ?
                  AND `protocol` = ? AND `blockchain` = ?
                  AND `token_0` = ? AND `token_1` = ?
                  AND `position_contract` = 'unknown'
                """,
                (
                    contract,
                    position.source_table,
                    position.position_id,
                    position.protocol,
                    position.blockchain,
                    position.token_0,
                    position.token_1,
                ),
            )

        values = {
            "id": position.position_id,
            "date": None,
            "protocol": position.protocol,
            "blockchain": position.blockchain,
            "token_0": position.token_0,
            "symbol_0": position.symbol_0,
            "amount_0": None,
            "quote_usd_0": None,
            "quote_eur_0": None,
            "token_1": position.token_1,
            "symbol_1": position.symbol_1,
            "amount_1": None,
            "quote_usd_1": None,
            "quote_eur_1": None,
            "source": "internal",
            "source_table": position.source_table,
            "position_contract": contract,
            "position_account": None,
            "position_version": None,
            "mint_tx": None,
            "mint_block": None,
            "mint_slot": None,
            "deposit_tx": None,
            "deposit_date": None,
            "amount_source": None,
            "quote_timestamp": None,
            "fx_source": None,
            "fx_date": None,
            "entry_value_usd": None,
            "entry_value_eur": None,
            "status": "pending",
            "last_error": None,
        }
        values.update(data)
        placeholders = ", ".join("?" for _ in POSITION_COLUMNS)
        columns = ", ".join(f"`{column}`" for column in POSITION_COLUMNS)
        updates = ", ".join(
            f"`{column}` = VALUES(`{column}`)"
            for column in POSITION_COLUMNS
            if column not in {"id", "source_table", "protocol", "blockchain"}
        )
        cursor.execute(
            f"""
            INSERT INTO `positions` ({columns})
            VALUES ({placeholders})
            ON DUPLICATE KEY UPDATE {updates}
            """,
            tuple(values[column] for column in POSITION_COLUMNS),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _load_stub(connection: Any, position: SourcePosition) -> None:
    complete, has_stub, _ = _existing_position_status(connection, position)
    if complete or has_stub:
        return
    _save_position(
        connection,
        position,
        {
            "position_contract": "unknown",
            "status": "pending",
        },
    )


def _contract_call(
    chain: str,
    contract_address: str,
    abi: list[dict[str, Any]],
    function_name: str,
    arguments: list[Any],
    *,
    block_number: int | None = None,
) -> tuple[Any, ...]:
    web3 = Web3()
    contract = web3.eth.contract(
        address=Web3.to_checksum_address(contract_address),
        abi=abi,
    )
    call_data = contract.encode_abi(function_name, args=arguments)
    block_tag = hex(block_number) if block_number is not None else "latest"
    result = _evm_rpc(
        chain,
        "eth_call",
        [{"to": Web3.to_checksum_address(contract_address), "data": call_data}, block_tag],
    )
    output_types = next(
        entry["outputs"]
        for entry in abi
        if entry.get("name") == function_name and entry.get("type") == "function"
    )
    decoded = web3.codec.decode(
        [
            component["type"]
            for output in output_types
            for component in (
                output.get("components", [])
                if output["type"] == "tuple"
                else [output]
            )
        ],
        bytes.fromhex(str(result).removeprefix("0x")),
    )
    if function_name == "getPoolAndPositionInfo":
        return (
            tuple(decoded[:5]),
            decoded[5],
        )
    return decoded


def _evm_position_managers(position: SourcePosition) -> list[tuple[str, str, list[dict[str, Any]]]]:
    chain_key = normalize_chain(position.blockchain)
    v4_config = V4_CONFIG.get(position.blockchain) or V4_CONFIG.get(chain_key)
    candidates: list[tuple[str, str, list[dict[str, Any]]]] = []
    if position.source_table == "aero":
        manager = _contract_call(
            position.blockchain,
            AERO_FACTORY,
            AERO_FACTORY_ABI,
            "positionManager",
            [],
        )[0]
        candidates.append((str(manager), "aero_slipstream", AERO_POSITION_ABI))
    elif position.source_table == "cake":
        candidates.append(
            (PANCAKESWAP_MANAGER, "pancakeswap_v3", POSITION_ABI)
        )
    else:
        v3_manager = V3_MANAGERS.get(position.blockchain)
        if v3_manager:
            candidates.append((v3_manager, "uniswap_v3", POSITION_ABI))
        if position.protocol == "uniswap" and v4_config:
            candidates.append(
                (v4_config[0], "uniswap_v4", V4_POSITION_MANAGER_ABI)
            )
    return candidates


def _manager_token_pair(
    position: SourcePosition,
    manager: str,
    version: str,
    abi: list[dict[str, Any]],
    *,
    block_number: int | None = None,
) -> tuple[str, str]:
    token_id = int(position.position_id)
    method = "getPoolAndPositionInfo" if version == "uniswap_v4" else "positions"
    result = _contract_call(
        position.blockchain,
        manager,
        abi,
        method,
        [token_id],
        block_number=block_number,
    )
    if version == "uniswap_v4":
        return str(result[0][0]), str(result[0][1])
    return str(result[2]), str(result[3])


def _resolve_evm_manager(
    position: SourcePosition,
) -> tuple[str, str, list[dict[str, Any]]]:
    errors: list[str] = []
    matching: list[tuple[str, str, list[dict[str, Any]]]] = []
    for manager, version, abi in _evm_position_managers(position):
        needs_historical_lookup = False
        try:
            token_0, token_1 = _manager_token_pair(
                position, manager, version, abi
            )
        except Exception as error:
            errors.append(f"{version} {manager}: {error}")
            needs_historical_lookup = True
        else:
            if (
                token_0.lower() == position.token_0.lower()
                and token_1.lower() == position.token_1.lower()
            ):
                matching.append((manager, version, abi))
                continue
            needs_historical_lookup = (
                token_0.lower() == ZERO_ADDRESS
                and token_1.lower() == ZERO_ADDRESS
            )
        if not needs_historical_lookup:
            continue
        try:
            transfer = _find_alchemy_mint_transfer(position, manager)
            mint_block = int(transfer["blockNum"], 16)
            token_0, token_1 = _manager_token_pair(
                position,
                manager,
                version,
                abi,
                block_number=mint_block,
            )
        except Exception as error:
            errors.append(f"{version} {manager} historical lookup: {error}")
            continue
        if (
            token_0.lower() == position.token_0.lower()
            and token_1.lower() == position.token_1.lower()
        ):
            matching.append((manager, version, abi))
        else:
            errors.append(
                f"{version} {manager} historical pair "
                f"{token_0}/{token_1} does not match the source tokens"
            )

    if len(matching) > 1:
        contracts = ", ".join(item[0] for item in matching)
        raise RuntimeError(
            "Ambiguous V3/V4 position ID; both managers match the source tokens: "
            f"{contracts}"
        )
    if not matching:
        details = "; ".join(errors) if errors else "no manager matched token_0/token_1"
        raise RuntimeError(
            f"Could not identify EVM position manager for {position.position_id}: {details}"
        )
    return matching[0]


@lru_cache(maxsize=256)
def _block_timestamp(chain: str, block_number: int) -> int:
    block = _evm_rpc(chain, "eth_getBlockByNumber", [hex(block_number), False])
    if block is None:
        raise RuntimeError(f"Alchemy returned no block {block_number} on {chain}")
    return int(block["timestamp"], 16)


def _block_at_or_before(chain: str, target: datetime) -> int:
    target_timestamp = _timestamp(target)
    latest = _evm_rpc(chain, "eth_blockNumber", [])
    high = int(latest, 16)
    low = 0
    while low <= high:
        middle = (low + high) // 2
        if _block_timestamp(chain, middle) <= target_timestamp:
            low = middle + 1
        else:
            high = middle - 1
    if high < 0:
        raise RuntimeError(f"No EVM block exists before {target.isoformat()}")
    return high


def _parse_token_id(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value)
    try:
        return int(text, 16) if text.startswith("0x") else int(text)
    except ValueError:
        return None


def _find_alchemy_mint_transfer(
    position: SourcePosition,
    manager: str,
) -> dict[str, Any]:
    upper = _block_at_or_before(position.blockchain, position.snapshot_date)
    expected_id = int(position.position_id)
    _, rpc_url = _rpc_for_chain(position.blockchain)

    def transfer_page(params: dict[str, Any]) -> dict[str, Any]:
        response = _http_json(
            rpc_url,
            payload={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "alchemy_getAssetTransfers",
                "params": [params],
            },
            interval=0.5,
        )
        if "error" in response:
            raise RuntimeError(
                f"Alchemy NFT transfer history failed for {manager}: "
                f"{response['error']}"
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(
                f"Alchemy returned an invalid transfer response for {manager}"
            )
        return result

    base_params: dict[str, Any] = {
        "fromBlock": "0x0",
        "toBlock": hex(upper),
        "fromAddress": ZERO_ADDRESS,
        "contractAddresses": [manager],
        "category": ["erc721"],
        "excludeZeroValue": False,
        "maxCount": "0x3e8",
    }

    try:
        latest_result = transfer_page(
            {**base_params, "order": "desc", "maxCount": "0x1"}
        )
        latest_transfers = latest_result.get("transfers", [])
        latest_id = max(
            (
                token_id
                for token_id in (
                    _parse_token_id(
                        transfer.get("tokenId") or transfer.get("erc721TokenId")
                    )
                    for transfer in latest_transfers
                )
                if token_id is not None
            ),
            default=0,
        )
        if latest_id < expected_id:
            raise RuntimeError(
                f"Manager {manager} has no mint for token ID {expected_id} "
                f"before block {upper}"
            )

        low, high = 0, upper
        while low < high:
            middle = (low + high) // 2
            result = transfer_page(
                {
                    **base_params,
                    "toBlock": hex(middle),
                    "order": "desc",
                    "maxCount": "0x1",
                }
            )
            token_ids = [
                _parse_token_id(
                    transfer.get("tokenId") or transfer.get("erc721TokenId")
                )
                for transfer in result.get("transfers", [])
            ]
            newest_id = max(
                (token_id for token_id in token_ids if token_id is not None),
                default=0,
            )
            if newest_id >= expected_id:
                high = middle
            else:
                low = middle + 1

        page_key: str | None = None
        for _ in range(10):
            params = {
                **base_params,
                "fromBlock": hex(max(0, low - 1)),
                "toBlock": hex(min(upper, low + 1)),
                "order": "asc",
            }
            if page_key:
                params["pageKey"] = page_key
            result = transfer_page(params)
            for transfer in result.get("transfers", []):
                if _parse_token_id(
                    transfer.get("tokenId") or transfer.get("erc721TokenId")
                ) == expected_id:
                    return transfer
            page_key = result.get("pageKey") or None
            if not page_key:
                break
    except ProviderHTTPError as error:
        logger.info(
            "Alchemy block search unavailable for %s; trying paginated history: %s",
            position.position_id,
            error,
        )

    width = 10_000
    pages_read = 0
    while upper >= 0:
        lower = max(0, upper - width + 1)
        page_key: str | None = None
        while True:
            transfer_params: dict[str, Any] = {
                "fromBlock": hex(lower),
                "toBlock": hex(upper),
                "fromAddress": ZERO_ADDRESS,
                "contractAddresses": [manager],
                "category": ["erc721"],
                "excludeZeroValue": False,
                "order": "desc",
                "maxCount": "0x3e8",
            }
            if page_key:
                transfer_params["pageKey"] = page_key
            result = transfer_page(transfer_params)
            for transfer in result.get("transfers", []):
                if _parse_token_id(
                    transfer.get("tokenId") or transfer.get("erc721TokenId")
                ) == expected_id:
                    return transfer
            page_key = result.get("pageKey") or None
            if not page_key:
                break
            pages_read += 1
            if pages_read >= 100:
                raise RuntimeError(
                    "Alchemy returned more than 100 NFT-history pages without "
                    f"finding token ID {position.position_id}"
                )
        if lower == 0:
            break
        upper = lower - 1
        width *= 2
    raise RuntimeError(
        f"Alchemy did not find the mint transfer for token ID {position.position_id}"
    )


def _evm_token_decimals(chain: str, token_address: str, block_number: int) -> int:
    if token_address.lower() == ZERO_ADDRESS:
        return 18
    result = _contract_call(
        chain,
        token_address,
        ERC20_DECIMALS_ABI,
        "decimals",
        [],
        block_number=block_number,
    )
    decimals = int(result[0])
    if decimals < 0 or decimals > 255:
        raise RuntimeError(f"Invalid decimals for token {token_address}: {decimals}")
    return decimals


def _v4_initial_amounts(
    position: SourcePosition,
    manager: str,
    receipt: dict[str, Any],
    block_number: int,
) -> tuple[int, int]:
    config = V4_CONFIG.get(position.blockchain) or V4_CONFIG.get(
        normalize_chain(position.blockchain)
    )
    if config is None:
        raise RuntimeError(
            f"No Uniswap v4 state view configured for {position.blockchain}"
        )

    expected_id = int(position.position_id)
    liquidity_events: list[tuple[str, str, int, int, int]] = []
    for event in receipt.get("logs", []):
        topics = event.get("topics") or []
        if (
            len(topics) < 3
            or str(topics[0]).lower() != MODIFY_LIQUIDITY_EVENT_TOPIC
            or ("0x" + str(topics[2])[-40:]).lower() != manager.lower()
        ):
            continue
        data = bytes.fromhex(str(event.get("data", "0x")).removeprefix("0x"))
        if len(data) < 128 or int.from_bytes(data[96:128], "big") != expected_id:
            continue
        liquidity_events.append(
            (
                str(event.get("address", "")),
                str(topics[1]),
                int.from_bytes(data[:32], "big", signed=True),
                int.from_bytes(data[32:64], "big", signed=True),
                int.from_bytes(data[64:96], "big", signed=True),
            )
        )
    if not liquidity_events:
        raise RuntimeError(
            f"Uniswap v4 mint has no ModifyLiquidity event for token ID {expected_id}"
        )

    event_keys = {
        (pool_manager.lower(), pool_id.lower(), tick_lower, tick_upper)
        for pool_manager, pool_id, tick_lower, tick_upper, _ in liquidity_events
    }
    if len(event_keys) != 1:
        raise RuntimeError(
            f"Uniswap v4 mint has ambiguous liquidity events for token ID {expected_id}"
        )
    pool_manager, pool_id, tick_lower, tick_upper = next(iter(event_keys))
    liquidity = sum(event[4] for event in liquidity_events)
    if liquidity <= 0:
        raise RuntimeError(
            f"Uniswap v4 mint has no positive liquidity for token ID {expected_id}"
        )

    sqrt_price_x96 = int(
        _contract_call(
            position.blockchain,
            config[1],
            V4_STATE_VIEW_ABI,
            "getSlot0",
            [pool_id],
            block_number=block_number,
        )[0]
    )
    calculated_amounts = _v3_amounts(
        liquidity, sqrt_price_x96, tick_lower, tick_upper
    )
    amounts = [
        int(amount.to_integral_value(rounding=ROUND_CEILING))
        for amount in calculated_amounts
    ]

    token_addresses = (position.token_0.lower(), position.token_1.lower())
    actual_net_transfers = {token: 0 for token in token_addresses}
    transfer_tokens: set[str] = set()
    for event in receipt.get("logs", []):
        token = str(event.get("address", "")).lower()
        topics = event.get("topics") or []
        if (
            token not in actual_net_transfers
            or len(topics) != 3
            or str(topics[0]).lower() != ERC20_TRANSFER_EVENT_TOPIC
        ):
            continue
        sender = "0x" + str(topics[1])[-40:].lower()
        recipient = "0x" + str(topics[2])[-40:].lower()
        amount = int(str(event.get("data", "0x")), 16)
        if recipient == pool_manager:
            actual_net_transfers[token] += amount
            transfer_tokens.add(token)
        if sender == pool_manager:
            actual_net_transfers[token] -= amount
            transfer_tokens.add(token)

    for index, token in enumerate(token_addresses):
        if token == ZERO_ADDRESS.lower():
            continue
        actual_amount = actual_net_transfers[token]
        if amounts[index] > 0 and token not in transfer_tokens:
            raise RuntimeError(
                f"Uniswap v4 mint has no verifiable token transfer for {token}"
            )
        if abs(actual_amount - amounts[index]) > 1:
            raise RuntimeError(
                "Uniswap v4 liquidity-derived amount does not match the mint "
                f"transaction transfer for {token}"
            )
        amounts[index] = actual_amount
    return amounts[0], amounts[1]


def _aero_initial_amounts(
    position: SourcePosition,
    manager: str,
    receipt: dict[str, Any],
    block_number: int,
) -> tuple[int, int]:
    state = _contract_call(
        position.blockchain,
        manager,
        AERO_POSITION_ABI,
        "positions",
        [int(position.position_id)],
        block_number=block_number,
    )
    if (
        str(state[2]).lower() != position.token_0.lower()
        or str(state[3]).lower() != position.token_1.lower()
    ):
        raise RuntimeError(
            f"Aero position token pair does not match ID {position.position_id}"
        )
    tick_lower, tick_upper = int(state[5]), int(state[6])
    matching_amounts: list[tuple[int, int]] = []
    for event in receipt.get("logs", []):
        topics = event.get("topics") or []
        if (
            len(topics) < 4
            or str(topics[0]).lower() != POOL_MINT_EVENT_TOPIC
            or ("0x" + str(topics[1])[-40:]).lower() != manager.lower()
            or int.from_bytes(
                bytes.fromhex(str(topics[2]).removeprefix("0x")), "big", signed=True
            )
            != tick_lower
            or int.from_bytes(
                bytes.fromhex(str(topics[3]).removeprefix("0x")), "big", signed=True
            )
            != tick_upper
        ):
            continue
        pool_tokens = tuple(
            str(
                _contract_call(
                    position.blockchain,
                    str(event.get("address", "")),
                    V3_POOL_TOKEN_ABI,
                    function_name,
                    [],
                    block_number=block_number,
                )[0]
            ).lower()
            for function_name in ("token0", "token1")
        )
        if pool_tokens != (position.token_0.lower(), position.token_1.lower()):
            continue
        data = bytes.fromhex(str(event.get("data", "0x")).removeprefix("0x"))
        if len(data) < 128:
            raise RuntimeError(
                f"Malformed Aero pool Mint event for ID {position.position_id}"
            )
        matching_amounts.append(
            (
                int.from_bytes(data[64:96], "big"),
                int.from_bytes(data[96:128], "big"),
            )
        )
    if len(matching_amounts) != 1:
        raise RuntimeError(
            "Could not uniquely identify the Aero pool Mint event for "
            f"position {position.position_id}"
        )
    return matching_amounts[0]


def _evm_mint_data(position: SourcePosition) -> MintData:
    manager, version, _ = _resolve_evm_manager(position)
    transfer = _find_alchemy_mint_transfer(position, manager)
    transaction_hash = str(transfer["hash"])
    receipt = _evm_rpc(
        position.blockchain,
        "eth_getTransactionReceipt",
        [transaction_hash],
    )
    if receipt is None:
        raise RuntimeError(f"Alchemy has no receipt for mint transaction {transaction_hash}")

    block_number = int(receipt["blockNumber"], 16)
    amount_0_raw = 0
    amount_1_raw = 0
    matching_events = 0
    amount_source = "increase_liquidity_event"
    expected_id = int(position.position_id)
    for event in receipt.get("logs", []):
        if str(event.get("address", "")).lower() != manager.lower():
            continue
        topics = event.get("topics") or []
        if (
            len(topics) < 2
            or str(topics[0]).lower() != LIQUIDITY_EVENT_TOPIC
            or int(str(topics[1]), 16) != expected_id
        ):
            continue
        data = bytes.fromhex(str(event.get("data", "0x")).removeprefix("0x"))
        if len(data) < 96:
            raise RuntimeError(
                f"Malformed IncreaseLiquidity event for token ID {expected_id}"
            )
        amount_0_raw += int.from_bytes(data[32:64], "big")
        amount_1_raw += int.from_bytes(data[64:96], "big")
        matching_events += 1
    if matching_events == 0:
        if version == "uniswap_v4":
            amount_0_raw, amount_1_raw = _v4_initial_amounts(
                position, manager, receipt, block_number
            )
            amount_source = "modify_liquidity_event"
        elif version == "aero_slipstream":
            amount_0_raw, amount_1_raw = _aero_initial_amounts(
                position, manager, receipt, block_number
            )
            amount_source = "pool_mint_event"
        else:
            raise RuntimeError(
                "Mint transaction has no IncreaseLiquidity event for token ID "
                f"{expected_id}; exact initial amounts were not recoverable."
            )

    block = _evm_rpc(
        position.blockchain,
        "eth_getBlockByNumber",
        [hex(block_number), False],
    )
    if block is None:
        raise RuntimeError(f"Alchemy returned no mint block {block_number}")
    mint_date = datetime.fromtimestamp(
        int(block["timestamp"], 16), tz=timezone.utc
    )
    decimals_0 = _evm_token_decimals(
        position.blockchain, position.token_0, block_number
    )
    decimals_1 = _evm_token_decimals(
        position.blockchain, position.token_1, block_number
    )
    amount_0 = Decimal(amount_0_raw) / (Decimal(10) ** decimals_0)
    amount_1 = Decimal(amount_1_raw) / (Decimal(10) ** decimals_1)
    if amount_0 == 0 and amount_1 == 0:
        raise RuntimeError("Mint transaction recorded zero initial liquidity amounts")

    return MintData(
        date=mint_date,
        mint_tx=transaction_hash,
        mint_block=block_number,
        mint_slot=None,
        position_contract=Web3.to_checksum_address(manager),
        position_account=None,
        position_version=version,
        deposit_tx=transaction_hash,
        deposit_date=mint_date,
        amount_0=amount_0,
        amount_1=amount_1,
        amount_source=amount_source,
    )


def _solana_pool_details(
    position: SourcePosition,
) -> tuple[str, str, str, str, str]:
    program = SOLANA_PROGRAMS[position.source_table]
    position_account = position.position_id
    account = _solana_account_limited(position_account)
    if position.source_table == "raydium" and (
        account is None or account[0] != program
    ):
        position_account = _derive_program_address(
            [b"position", _base58_decode(position.position_id)],
            program,
        )
        account = _solana_account_limited(position_account)
    pool_address: str | None = None
    if account is not None and account[0] == program:
        position_data = account[1]
        if position.source_table == "orca":
            if len(position_data) < 0x90:
                raise RuntimeError("Invalid Orca position account data")
            pool_address = _base58(position_data[0x08:0x28])
        elif (
            len(position_data) >= 145
            and position_data[:8] == _anchor_discriminator("PersonalPositionState")
        ):
            pool_address = _base58(position_data[41:73])

    pool = (
        _solana_account_limited(pool_address)
        if pool_address is not None
        else None
    )
    if pool is not None and pool[0] == program:
        pool_data = pool[1]
        token_0, token_1, vault_0, vault_1 = _solana_pool_token_data(
            position.source_table, pool_data
        )
    else:
        pool_address, token_0, token_1, vault_0, vault_1 = (
            _solana_pool_from_history(position, position_account, program)
        )
    if (token_0, token_1) != (position.token_0, position.token_1):
        raise RuntimeError(
            "On-chain Solana pool mints do not match token_0/token_1 in "
            f"source table for position {position.position_id}"
        )
    return program, position_account, pool_address, vault_0, vault_1


def _solana_pool_token_data(
    table: str,
    pool_data: bytes,
) -> tuple[str, str, str, str]:
    if table == "orca":
        if len(pool_data) < 0x105:
            raise RuntimeError("Invalid Orca Whirlpool account data")
        offset = 0x08 + 32 + 1 + 2 + 2 + 2 + 2 + 16
        offset += 16 + 4 + 8 + 8
        token_0 = _base58(pool_data[offset : offset + 32])
        vault_0 = _base58(pool_data[offset + 32 : offset + 64])
        offset += 32 + 32 + 16
        token_1 = _base58(pool_data[offset : offset + 32])
        vault_1 = _base58(pool_data[offset + 32 : offset + 64])
        return token_0, token_1, vault_0, vault_1

    if (
        len(pool_data) < 201
        or pool_data[:8] != _anchor_discriminator("PoolState")
    ):
        raise RuntimeError("Invalid Raydium PoolState account data")
    return (
        _base58(pool_data[73:105]),
        _base58(pool_data[105:137]),
        _base58(pool_data[137:169]),
        _base58(pool_data[169:201]),
    )


def _solana_pool_from_history(
    position: SourcePosition,
    position_account: str,
    program: str,
) -> tuple[str, str, str, str, str]:
    history_address = (
        position.position_id
        if position.source_table == "raydium"
        else position_account
    )
    transactions, _ = _solana_transaction_page(history_address, None)
    if not transactions:
        raise RuntimeError(
            f"Helius found no history for closed position {history_address}"
        )
    expected_discriminator = _anchor_discriminator(
        "Whirlpool" if position.source_table == "orca" else "PoolState"
    )
    matches: list[tuple[str, str, str, str, str]] = []
    for transaction in transactions:
        message = (transaction.get("transaction") or {}).get("message") or {}
        keys = message.get("accountKeys") or []
        account_keys = [
            str(key.get("pubkey")) if isinstance(key, dict) else str(key)
            for key in keys
        ]
        if history_address not in account_keys:
            continue
        for start in range(0, len(account_keys), 100):
            batch = account_keys[start : start + 100]
            result = _solana_rpc_result(
                "getMultipleAccounts",
                [batch, {"encoding": "base64", "commitment": "finalized"}],
            )
            for address, account in zip(batch, result.get("value", [])):
                if account is None or account.get("owner") != program:
                    continue
                data = base64.b64decode(account["data"][0])
                if data[:8] != expected_discriminator:
                    continue
                try:
                    token_0, token_1, vault_0, vault_1 = _solana_pool_token_data(
                        position.source_table, data
                    )
                except RuntimeError:
                    continue
                if (token_0, token_1) == (position.token_0, position.token_1):
                    matches.append(
                        (address, token_0, token_1, vault_0, vault_1)
                    )
        if matches:
            break
    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) != 1:
        raise RuntimeError(
            "Could not uniquely identify the historical Solana pool from "
            f"position {history_address}"
        )
    return unique_matches[0]


@lru_cache(maxsize=256)
def _solana_account_limited(address: str) -> tuple[str, bytes] | None:
    result = _solana_rpc_result(
        "getAccountInfo",
        [address, {"encoding": "base64", "commitment": "finalized"}],
    )
    value = result.get("value")
    if value is None:
        return None
    return value["owner"], base64.b64decode(value["data"][0])


def _solana_balance_delta(
    transaction: dict[str, Any],
    vault: str,
    expected_mint: str,
) -> tuple[Decimal, int] | None:
    transaction_data = transaction.get("transaction") or {}
    message = transaction_data.get("message") or {}
    account_keys = message.get("accountKeys") or []
    keys: list[str] = []
    for key in account_keys:
        keys.append(str(key.get("pubkey")) if isinstance(key, dict) else str(key))
    meta = transaction.get("meta") or {}
    if meta.get("err") is not None:
        return None

    def balances(name: str) -> dict[int, tuple[int, int]]:
        result: dict[int, tuple[int, int]] = {}
        for entry in meta.get(name, []) or []:
            if entry.get("mint") != expected_mint:
                continue
            index = int(entry["accountIndex"])
            if index >= len(keys) or keys[index] != vault:
                continue
            token_amount = entry.get("uiTokenAmount") or {}
            result[index] = (
                int(token_amount.get("amount", "0")),
                int(token_amount.get("decimals", 0)),
            )
        return result

    before = balances("preTokenBalances")
    after = balances("postTokenBalances")
    indices = set(before) | set(after)
    if not indices:
        return None
    raw_delta = sum(
        after.get(index, (0, before.get(index, (0, 0))[1]))[0]
        - before.get(index, (0, after.get(index, (0, 0))[1]))[0]
        for index in indices
    )
    decimals = next(
        (value[1] for value in after.values()),
        next((value[1] for value in before.values()), 0),
    )
    return Decimal(raw_delta) / (Decimal(10) ** decimals), decimals


def _solana_transaction_page(
    position_account: str,
    pagination_token: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    config: dict[str, Any] = {
        "transactionDetails": "full",
        "sortOrder": "asc",
        "commitment": "finalized",
        "limit": 1000,
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 1,
        "filters": {"status": "succeeded"},
    }
    if pagination_token:
        config["paginationToken"] = pagination_token
    result = _solana_rpc_result(
        "getTransactionsForAddress",
        [position_account, config],
    )
    if isinstance(result, list):
        return result, None
    if not isinstance(result, dict):
        raise RuntimeError("Helius returned an invalid transaction-history response")
    transactions = result.get("data")
    if not isinstance(transactions, list):
        raise RuntimeError("Helius response has no transaction data")
    return transactions, result.get("paginationToken") or None


def _solana_mint_data(position: SourcePosition) -> MintData:
    program, position_account, _, vault_0, vault_1 = _solana_pool_details(position)
    history_address = (
        position.position_id
        if position.source_table == "raydium"
        else position_account
    )
    pagination_token: str | None = None
    mint_transaction: dict[str, Any] | None = None
    deposit_transaction: dict[str, Any] | None = None
    amount_0 = Decimal(0)
    amount_1 = Decimal(0)
    page_count = 0

    while page_count < 10:
        transactions, next_token = _solana_transaction_page(
            history_address, pagination_token
        )
        for transaction in transactions:
            tx_data = transaction.get("transaction") or {}
            message = tx_data.get("message") or {}
            keys = message.get("accountKeys") or []
            key_strings = [
                str(key.get("pubkey")) if isinstance(key, dict) else str(key)
                for key in keys
            ]
            if history_address not in key_strings:
                continue
            meta = transaction.get("meta") or {}
            if meta.get("err") is not None:
                continue
            if mint_transaction is None:
                mint_transaction = transaction

            delta_0 = _solana_balance_delta(
                transaction, vault_0, position.token_0
            )
            delta_1 = _solana_balance_delta(
                transaction, vault_1, position.token_1
            )
            logs = " ".join(meta.get("logMessages") or []).lower()
            has_initial_liquidity_instruction = any(
                marker in logs
                for marker in (
                    "open_position",
                    "openposition",
                    "increase_liquidity",
                    "increaseliquidity",
                    "increase liquidity",
                )
            )
            if (
                has_initial_liquidity_instruction
                and delta_0 is not None
                and delta_1 is not None
                and (delta_0[0] > 0 or delta_1[0] > 0)
            ):
                deposit_transaction = transaction
                amount_0, amount_1 = delta_0[0], delta_1[0]
                break
        if deposit_transaction is not None or not next_token:
            break
        pagination_token = next_token
        page_count += 1

    if mint_transaction is None:
        raise RuntimeError(
            f"Helius found no successful transaction for position {history_address}"
        )
    if deposit_transaction is None:
        raise RuntimeError(
            "Helius history contains no initial IncreaseLiquidity transaction "
            f"with verifiable pool-vault balances for {position.position_id}"
        )

    mint_time = mint_transaction.get("blockTime")
    if mint_time is None:
        mint_time = (mint_transaction.get("transaction") or {}).get("blockTime")
    deposit_time = deposit_transaction.get("blockTime")
    if deposit_time is None:
        deposit_time = (deposit_transaction.get("transaction") or {}).get("blockTime")
    if mint_time is None or deposit_time is None:
        raise RuntimeError(
            f"Helius returned no block time for Solana position {position.position_id}"
        )
    mint_date = datetime.fromtimestamp(int(mint_time), tz=timezone.utc)
    deposit_date = datetime.fromtimestamp(int(deposit_time), tz=timezone.utc)
    mint_signature = (
        mint_transaction.get("signature")
        or (mint_transaction.get("transaction") or {}).get("signatures", [None])[0]
    )
    deposit_signature = (
        deposit_transaction.get("signature")
        or (deposit_transaction.get("transaction") or {}).get("signatures", [None])[0]
    )
    if not mint_signature or not deposit_signature:
        raise RuntimeError(
            f"Helius returned no transaction signature for {position.position_id}"
        )
    mint_slot = mint_transaction.get("slot")

    return MintData(
        date=mint_date,
        mint_tx=str(mint_signature),
        mint_block=None,
        mint_slot=int(mint_slot) if mint_slot is not None else None,
        position_contract=program,
        position_account=position_account,
        position_version="solana",
        deposit_tx=str(deposit_signature),
        deposit_date=deposit_date,
        amount_0=amount_0,
        amount_1=amount_1,
        amount_source="helius_pool_vault_balances",
    )


def _token_asset(chain: str, token_address: str) -> str | None:
    chain_key = normalize_chain(chain)
    tokens = TOKEN_TO_ASSET.get(chain_key, {})
    if chain_key == "solana":
        return tokens.get(token_address)
    token_lower = token_address.lower()
    return next(
        (
            asset
            for address, asset in tokens.items()
            if address.lower() == token_lower
        ),
        None,
    )


@lru_cache(maxsize=4096)
def _chainlink_usd_price(
    chain: str,
    token_address: str,
    block_number: int,
) -> Decimal:
    chain_key = normalize_chain(chain)
    asset = _token_asset(chain_key, token_address)
    feed_address = FEED_BY_CHAIN_ASSET.get(chain_key, {}).get(asset or "")
    if not feed_address:
        raise RuntimeError(f"No Chainlink feed mapped for {token_address} on {chain}")
    feed_contract = Web3().eth.contract(
        address=Web3.to_checksum_address(feed_address),
        abi=AGGREGATOR_V3_ABI,
    )
    call_data = feed_contract.encode_abi("latestRoundData")
    raw = _evm_rpc(
        chain,
        "eth_call",
        [
            {"to": Web3.to_checksum_address(feed_address), "data": call_data},
            hex(block_number),
        ],
    )
    values = Web3().codec.decode(
        ["uint80", "int256", "uint256", "uint256", "uint80"],
        bytes.fromhex(str(raw).removeprefix("0x")),
    )
    answer, updated_at = int(values[1]), int(values[3])
    if answer <= 0 or updated_at <= 0:
        raise RuntimeError(f"Invalid historical Chainlink answer for {asset}")

    cache_key = (chain_key, feed_address.lower())
    decimals = _CHAINLINK_DECIMALS_CACHE.get(cache_key)
    if decimals is None:
        decimals_call = feed_contract.encode_abi("decimals")
        raw_decimals = _evm_rpc(
            chain,
            "eth_call",
            [{"to": Web3.to_checksum_address(feed_address), "data": decimals_call}, hex(block_number)],
        )
        decimals = int(
            Web3().codec.decode(
                ["uint8"],
                bytes.fromhex(str(raw_decimals).removeprefix("0x")),
            )[0]
        )
        _CHAINLINK_DECIMALS_CACHE[cache_key] = decimals
    return Decimal(answer) / (Decimal(10) ** decimals)


@lru_cache(maxsize=2048)
def _pyth_usd_price(chain: str, token_address: str, timestamp: int) -> Decimal:
    if normalize_chain(chain) != "solana":
        raise RuntimeError("Pyth historical pricing is only configured for Solana")
    asset = _token_asset(chain, token_address)
    feed_id = FEED_BY_CHAIN_ASSET.get("solana", {}).get(asset or "")
    if not feed_id or feed_id == "solana":
        feed_id = PYTH_FEED_BY_ASSET.get(asset or "")
    if not feed_id:
        raise RuntimeError(f"No historical Pyth feed mapped for {token_address}")
    url = f"https://hermes.pyth.network/v2/updates/price/{timestamp}"
    result = _http_json(
        url,
        params={"ids[]": feed_id, "parsed": "true"},
        interval=1.0,
    )
    entries = result.get("parsed") or []
    if not entries:
        raise RuntimeError(f"Pyth returned no historical price for {asset}")
    price_data = entries[0].get("price") or {}
    price_value = int(price_data.get("price", 0))
    exponent = int(price_data.get("expo", 0))
    publish_time = int(price_data.get("publish_time", 0))
    if price_value <= 0 or publish_time <= 0 or publish_time > timestamp:
        raise RuntimeError(f"Pyth returned an invalid historical price for {asset}")
    return Decimal(price_value) * (Decimal(10) ** exponent)


@lru_cache(maxsize=2048)
def _defillama_prices(
    chain: str,
    token_0: str,
    token_1: str,
    timestamp: int,
) -> tuple[Decimal, Decimal]:
    chain_key = normalize_chain(chain)
    chain_name = DEFILLAMA_CHAINS.get(chain_key)
    if not chain_name:
        raise RuntimeError(f"DefiLlama does not support chain {chain}")
    coin_0 = f"{chain_name}:{token_0}"
    coin_1 = f"{chain_name}:{token_1}"
    coin_path = quote(f"{coin_0},{coin_1}", safe=":,")
    result = _http_json(
        f"https://coins.llama.fi/prices/historical/{timestamp}/{coin_path}",
        interval=1.0,
    )
    coins = result.get("coins") or {}

    def find_price(coin: str) -> Decimal:
        value = coins.get(coin)
        if value is None:
            value = next(
                (
                    item
                    for key, item in coins.items()
                    if str(key).lower() == coin.lower()
                ),
                None,
            )
        if not isinstance(value, dict) or value.get("price") is None:
            raise RuntimeError(f"DefiLlama has no historical price for {coin}")
        price = Decimal(str(value["price"]))
        if price <= 0:
            raise RuntimeError(f"DefiLlama returned a non-positive price for {coin}")
        return price

    return find_price(coin_0), find_price(coin_1)


@lru_cache(maxsize=2048)
def _geckoterminal_usd_price(
    chain: str,
    token_address: str,
    timestamp: int,
) -> Decimal:
    chain_key = normalize_chain(chain)
    network = GECKOTERMINAL_NETWORKS.get(chain_key)
    if not network:
        raise RuntimeError(f"GeckoTerminal does not support chain {chain}")
    token_url = (
        f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/"
        f"{quote(token_address, safe='')}/pools"
    )
    pools_response = _http_json(token_url, interval=2.1)
    pools = pools_response.get("data") or []
    if not pools:
        raise RuntimeError(f"GeckoTerminal found no pool for {token_address}")

    def liquidity(pool: dict[str, Any]) -> Decimal:
        value = (pool.get("attributes") or {}).get("reserve_in_usd") or 0
        return Decimal(str(value))

    pool = max(pools, key=liquidity)
    attributes = pool.get("attributes") or {}
    pool_address = attributes.get("address")
    relationships = pool.get("relationships") or {}
    base_data = (
        (relationships.get("base_token") or {}).get("data") or {}
    )
    quote_data = (
        (relationships.get("quote_token") or {}).get("data") or {}
    )
    base_address = str(base_data.get("id", "")).rsplit("_", 1)[-1]
    quote_address = str(quote_data.get("id", "")).rsplit("_", 1)[-1]
    if not pool_address:
        raise RuntimeError("GeckoTerminal pool response has no address")
    if token_address.lower() == base_address.lower():
        token_side = "base"
    elif token_address.lower() == quote_address.lower():
        token_side = "quote"
    else:
        raise RuntimeError("GeckoTerminal pool token addresses do not match")
    ohlcv_url = (
        f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/"
        f"{quote(str(pool_address), safe='')}/ohlcv/hour"
    )
    result = _http_json(
        ohlcv_url,
        params={
            "aggregate": 1,
            "before_timestamp": timestamp,
            "limit": 1,
            "currency": "usd",
            "token": token_side,
        },
        interval=2.1,
    )
    candles = (
        ((result.get("data") or {}).get("attributes") or {}).get("ohlcv_list")
        or []
    )
    candle = next(
        (item for item in candles if int(item[0]) <= timestamp),
        None,
    )
    if candle is None or len(candle) < 5:
        raise RuntimeError(f"GeckoTerminal has no historical candle for {token_address}")
    price = Decimal(str(candle[4]))
    if price <= 0:
        raise RuntimeError(f"GeckoTerminal returned a non-positive price for {token_address}")
    return price


def _internal_prices(position: SourcePosition) -> tuple[Decimal, Decimal]:
    if position.quote_0 is None or position.quote_1 is None:
        raise RuntimeError("Oldest internal snapshot has no quote for both tokens")
    if position.quote_0 <= 0 or position.quote_1 <= 0:
        raise RuntimeError("Oldest internal snapshot has a non-positive token quote")
    return position.quote_0, position.quote_1


def _historical_usd_prices(
    position: SourcePosition,
    mint: MintData,
) -> tuple[Decimal, Decimal, str, datetime]:
    timestamp = _timestamp(mint.date)
    chain_key = normalize_chain(position.blockchain)
    if mint.mint_block is not None and chain_key != "solana":
        try:
            return (
                _chainlink_usd_price(
                    position.blockchain, position.token_0, mint.mint_block
                ),
                _chainlink_usd_price(
                    position.blockchain, position.token_1, mint.mint_block
                ),
                "chainlink",
                mint.date,
            )
        except Exception:
            logger.info(
                "Historical Chainlink prices unavailable for %s:%s; trying fallbacks",
                position.blockchain,
                position.position_id,
                exc_info=True,
            )
    if chain_key == "solana":
        try:
            return (
                _pyth_usd_price(position.blockchain, position.token_0, timestamp),
                _pyth_usd_price(position.blockchain, position.token_1, timestamp),
                "pyth",
                mint.date,
            )
        except Exception:
            logger.info(
                "Historical Pyth prices unavailable for %s; trying fallbacks",
                position.position_id,
                exc_info=True,
            )
    try:
        quote_0, quote_1 = _defillama_prices(
            position.blockchain,
            position.token_0,
            position.token_1,
            timestamp,
        )
        return quote_0, quote_1, "defillama", mint.date
    except Exception:
        logger.info(
            "DefiLlama prices unavailable for %s; trying GeckoTerminal",
            position.position_id,
            exc_info=True,
        )
    try:
        return (
            _geckoterminal_usd_price(
                position.blockchain, position.token_0, timestamp
            ),
            _geckoterminal_usd_price(
                position.blockchain, position.token_1, timestamp
            ),
            "geckoterminal",
            mint.date,
        )
    except Exception:
        logger.info(
            "GeckoTerminal prices unavailable for %s; using oldest internal quotes",
            position.position_id,
            exc_info=True,
        )
    quote_0, quote_1 = _internal_prices(position)
    return quote_0, quote_1, "internal", position.snapshot_date


@lru_cache(maxsize=64)
def _historical_usd_to_eur(rate_date: date) -> tuple[Decimal, date]:
    for days_back in range(8):
        requested_date = rate_date - timedelta(days=days_back)
        try:
            result = _http_json(
                f"https://api.frankfurter.app/{requested_date.isoformat()}",
                params={"from": "USD", "to": "EUR"},
                interval=1.0,
            )
        except ProviderHTTPError as error:
            if error.status_code == 404:
                continue
            raise
        rates = result.get("rates") or {}
        if rates.get("EUR") is None:
            continue
        rate = Decimal(str(rates["EUR"]))
        if rate <= 0:
            raise RuntimeError("Frankfurter returned a non-positive USD/EUR rate")
        actual_date = date.fromisoformat(str(result.get("date", requested_date.isoformat())))
        return rate, actual_date
    raise RuntimeError(f"Frankfurter has no USD/EUR rate near {rate_date.isoformat()}")


def _make_position_data(position: SourcePosition, mint: MintData) -> dict[str, Any]:
    quote_0, quote_1, source, quote_timestamp = _historical_usd_prices(
        position, mint
    )
    usd_to_eur, fx_date = _historical_usd_to_eur(_as_utc(mint.date).date())
    quote_eur_0 = quote_0 * usd_to_eur
    quote_eur_1 = quote_1 * usd_to_eur
    entry_value_usd = mint.amount_0 * quote_0 + mint.amount_1 * quote_1
    entry_value_eur = mint.amount_0 * quote_eur_0 + mint.amount_1 * quote_eur_1

    return {
        "date": _db_datetime(mint.date),
        "token_0": position.token_0,
        "symbol_0": position.symbol_0,
        "amount_0": mint.amount_0,
        "quote_usd_0": quote_0,
        "quote_eur_0": quote_eur_0,
        "token_1": position.token_1,
        "symbol_1": position.symbol_1,
        "amount_1": mint.amount_1,
        "quote_usd_1": quote_1,
        "quote_eur_1": quote_eur_1,
        "source": source,
        "position_contract": mint.position_contract,
        "position_account": mint.position_account,
        "position_version": mint.position_version,
        "mint_tx": mint.mint_tx,
        "mint_block": mint.mint_block,
        "mint_slot": mint.mint_slot,
        "deposit_tx": mint.deposit_tx,
        "deposit_date": _db_datetime(mint.deposit_date),
        "amount_source": mint.amount_source,
        "quote_timestamp": _db_datetime(quote_timestamp),
        "fx_source": "frankfurter",
        "fx_date": fx_date,
        "entry_value_usd": entry_value_usd,
        "entry_value_eur": entry_value_eur,
        "status": "complete",
        "last_error": None,
    }


def _send_telegram_notification(message: str) -> None:
    config_path = os.getenv(
        "TELEGRAM_CONF",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram.conf"),
    )
    try:
        import telegram_send

        asyncio.run(
            telegram_send.send(
                conf=config_path,
                parse_mode="html",
                messages=[message],
            )
        )
    except Exception:
        logger.exception("Could not send Telegram notification using %s", config_path)


def _position_notification(
    position: SourcePosition,
    data: dict[str, Any],
    *,
    updated: bool,
) -> str:
    title = "Position aktualisiert" if updated else "Neue Position importiert"
    values = (
        f"<b>{title}</b>",
        f"Chain: {html.escape(position.blockchain)}",
        f"Protokoll: {html.escape(position.protocol)}",
        f"ID: <code>{html.escape(position.position_id)}</code>",
        (
            f"{html.escape(position.symbol_0)}: "
            f"{html.escape(str(data['amount_0']))} "
            f"@ ${html.escape(str(data['quote_usd_0']))}"
        ),
        (
            f"{html.escape(position.symbol_1)}: "
            f"{html.escape(str(data['amount_1']))} "
            f"@ ${html.escape(str(data['quote_usd_1']))}"
        ),
        f"Einstand: ${html.escape(str(data['entry_value_usd']))}",
        f"Preisquelle: {html.escape(str(data['source']))}",
    )
    return "\n".join(values)


def _position_error_notification(position: SourcePosition, error: Exception) -> str:
    details = html.escape(str(error)[:2500])
    return "\n".join(
        (
            "<b>Fehler beim Positionsimport</b>",
            f"Chain: {html.escape(position.blockchain)}",
            f"Protokoll: {html.escape(position.protocol)}",
            f"ID: <code>{html.escape(position.position_id)}</code>",
            f"Quelle: {html.escape(position.source_table)}",
            f"Fehler: <code>{details}</code>",
        )
    )


def _process_position(
    connection: Any,
    position: SourcePosition,
    *,
    dry_run: bool,
    updated: bool = False,
) -> bool:
    try:
        if position.blockchain == "solana":
            mint = _solana_mint_data(position)
        else:
            mint = _evm_mint_data(position)
        data = _make_position_data(position, mint)
        if not dry_run:
            _save_position(connection, position, data, replace_stub=True)
        logger.info(
            "%s:%s (%s) -> %s; price source=%s; initial value USD=%s",
            position.blockchain,
            position.position_id,
            position.protocol,
            "dry-run" if dry_run else "saved",
            data["source"],
            data["entry_value_usd"],
        )
    except Exception as error:
        logger.exception(
            "Could not backfill %s:%s from %s",
            position.blockchain,
            position.position_id,
            position.source_table,
        )
        if not dry_run:
            _save_position(
                connection,
                position,
                {
                    "position_contract": "unknown",
                    "status": "incomplete",
                    "last_error": str(error)[:60000],
                },
            )
            _send_telegram_notification(
                _position_error_notification(position, error)
            )
        return False
    if not dry_run:
        _send_telegram_notification(
            _position_notification(position, data, updated=updated)
        )
    return True


def run(
    *,
    only_table: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    connection = _connect()
    try:
        if not dry_run:
            cursor = connection.cursor()
            try:
                cursor.execute(CREATE_POSITIONS_SQL)
                connection.commit()
            finally:
                cursor.close()

        positions = _source_positions(connection, only_table)
        if limit is not None:
            positions = positions[:limit]
        logger.info("Found %s unique positions to inspect", len(positions))

        existing_positions: set[tuple[str, str, str, str]] = set()
        if not dry_run:
            for position in positions:
                _, _, exists = _existing_position_status(connection, position)
                if exists:
                    existing_positions.add(
                        (
                            position.source_table,
                            position.position_id,
                            position.token_0,
                            position.token_1,
                        )
                    )
                _load_stub(connection, position)

        complete = failed = skipped = 0
        for position in positions:
            if not dry_run:
                already_complete, _, _ = _existing_position_status(
                    connection, position
                )
                if already_complete:
                    skipped += 1
                    continue
            position_key = (
                position.source_table,
                position.position_id,
                position.token_0,
                position.token_1,
            )
            if _process_position(
                connection,
                position,
                dry_run=dry_run,
                updated=position_key in existing_positions,
            ):
                complete += 1
            else:
                failed += 1
        logger.info(
            "Backfill finished: complete=%d, failed=%d, skipped=%d",
            complete,
            failed,
            skipped,
        )
        return complete, failed, skipped
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill exact LP mint data and historical prices."
    )
    parser.add_argument(
        "--table",
        choices=sorted(PENDING_FEE_TABLES),
        help="Process only one position source table.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process at most this many source positions (for a small trial run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read source data and query APIs without creating or changing positions.",
    )
    parser.add_argument(
        "--print-sql",
        action="store_true",
        help="Print the CREATE TABLE statement and exit.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if args.print_sql:
        print(CREATE_POSITIONS_SQL.strip())
        return 0
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be greater than zero")

    _, failed, _ = run(
        only_table=args.table,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
