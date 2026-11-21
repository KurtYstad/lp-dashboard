#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import httpx
from dotenv import load_dotenv

from position_details import TOKEN_ICON_URL_OVERRIDES, _get_token_icon_url
from pricing import TOKEN_TO_ASSET

load_dotenv(Path(__file__).resolve().with_name(".env"))

logger = logging.getLogger(__name__)

COINTRACKING_API_URL = "https://cointracking.info/api/v1/"
COINTRACKING_CACHE_SECONDS = 3600
TRUST_WALLET_TOKENLIST_URL = (
    "https://raw.githubusercontent.com/trustwallet/assets/master/"
    "blockchains/ethereum/tokenlist.json"
)
TRUST_WALLET_ASSETS_URL = (
    "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains"
)
COINTRACKING_ICON_DIR = Path(__file__).resolve().parent / "icons" / "cointracking"
COINTRACKING_ICON_URL = "/icons/cointracking"
ETHEREUM_TOKEN_INDEX_PATH = COINTRACKING_ICON_DIR / "ethereum-token-index.json"
ETHEREUM_TOKEN_INDEX_TTL_SECONDS = 86400
TOKEN_SYMBOL_CHAIN_PRIORITY = (
    "eth",
    "arbitrum",
    "polygon",
    "bsc",
    "base",
    "optimism",
    "robinhood",
    "solana",
)
TOKEN_ICON_SYMBOL_ALIASES = {
    "eur": "EURC",
    "sol2": "SOL",
    "atom2": "ATOM",
    "btcb": "BTC",
    "usdg3": "USDG",
    "pol3": "POL",
}
_BALANCE_CACHE: tuple[float, list["CoinTrackingBalance"]] | None = None
_BALANCE_LOCK = threading.RLock()
_LAST_NONCE = 0
_ETHEREUM_TOKEN_INDEX: dict[str, dict[str, str]] | None = None
_ETHEREUM_TOKEN_INDEX_LOCK = threading.Lock()
_ICON_LOCKS: dict[str, threading.Lock] = {}
_ICON_LOCKS_GUARD = threading.Lock()
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CoinTrackingBalance(TypedDict):
    symbol: str
    amount: Decimal
    value_eur: Decimal
    icon_url: str | None


def _next_nonce() -> int:
    global _LAST_NONCE
    with _BALANCE_LOCK:
        _LAST_NONCE = max(time.time_ns() // 100_000_000, _LAST_NONCE + 1)
        return _LAST_NONCE


def _parse_balance_details(payload: object) -> list[CoinTrackingBalance]:
    if not isinstance(payload, dict):
        raise ValueError("CoinTracking lieferte eine ungültige Antwort.")

    result = payload
    nested_result = payload.get("result")
    if "details" not in payload and isinstance(nested_result, dict):
        result = nested_result

    success = result.get("success", payload.get("success"))
    if success not in (True, 1, "1"):
        error = result.get("error_msg") or result.get("error")
        raise RuntimeError(f"CoinTracking API-Fehler: {error or 'unbekannter Fehler'}")

    details = result.get("details")
    if not isinstance(details, dict):
        raise ValueError("CoinTracking-Antwort enthält keine Kontostände.")

    balances: list[CoinTrackingBalance] = []
    for symbol, detail in details.items():
        symbol = str(symbol).strip()
        if not symbol or symbol.casefold().startswith("lp-"):
            continue
        if not isinstance(detail, dict):
            raise ValueError(f"Ungültiger Kontostand für {symbol}.")
        try:
            amount = Decimal(str(detail["amount"]))
            value_eur = Decimal(str(detail["value_fiat"]))
        except (KeyError, InvalidOperation) as error:
            raise ValueError(
                f"CoinTracking-Kontostand für {symbol} ist unvollständig."
            ) from error
        if not amount.is_finite() or not value_eur.is_finite():
            raise ValueError(f"CoinTracking-Kontostand für {symbol} ist ungültig.")
        balances.append(
            {
                "symbol": symbol,
                "amount": amount,
                "value_eur": value_eur,
                "icon_url": None,
            }
        )
    return balances


def get_cointracking_balances() -> list[CoinTrackingBalance]:
    global _BALANCE_CACHE
    with _BALANCE_LOCK:
        now = time.monotonic()
        if _BALANCE_CACHE and _BALANCE_CACHE[0] > now:
            return [dict(balance) for balance in _BALANCE_CACHE[1]]

        api_key = os.getenv("ct_key")
        api_secret = os.getenv("ct_secret")
        if not api_key or not api_secret:
            raise ValueError("ct_key oder ct_secret fehlt in der .env-Datei.")

        fields = {"method": "getBalance", "nonce": str(_next_nonce())}
        message = "&".join(f"{key}={value}" for key, value in fields.items())
        signature = hmac.new(
            api_secret.encode("utf-8"),
            message.encode("utf-8"),
            digestmod=hashlib.sha512,
        ).hexdigest()
        response = httpx.post(
            COINTRACKING_API_URL,
            files={key: (None, value) for key, value in fields.items()},
            headers={"Key": api_key, "Sign": signature},
            timeout=15.0,
        )
        response.raise_for_status()
        balances = _parse_balance_details(response.json())
        _BALANCE_CACHE = (time.monotonic() + COINTRACKING_CACHE_SECONDS, balances)
        return [dict(balance) for balance in balances]


def _load_ethereum_token_index() -> dict[str, dict[str, str]]:
    global _ETHEREUM_TOKEN_INDEX
    with _ETHEREUM_TOKEN_INDEX_LOCK:
        if _ETHEREUM_TOKEN_INDEX is not None:
            return _ETHEREUM_TOKEN_INDEX

        try:
            cache_is_fresh = (
                time.time() - ETHEREUM_TOKEN_INDEX_PATH.stat().st_mtime
                < ETHEREUM_TOKEN_INDEX_TTL_SECONDS
            )
            if cache_is_fresh:
                cached_index = json.loads(
                    ETHEREUM_TOKEN_INDEX_PATH.read_text(encoding="utf-8")
                )
                if isinstance(cached_index, dict) and all(
                    isinstance(symbol, str)
                    and isinstance(token, dict)
                    and isinstance(token.get("symbol"), str)
                    and isinstance(token.get("address"), str)
                    for symbol, token in cached_index.items()
                ):
                    _ETHEREUM_TOKEN_INDEX = cached_index
                    return cached_index
                logger.warning("Cached Trust Wallet token symbols are invalid")
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            logger.warning(
                "Could not read cached Trust Wallet Ethereum token symbols",
                exc_info=True,
            )

        response = httpx.get(TRUST_WALLET_TOKENLIST_URL, timeout=5.0)
        response.raise_for_status()
        token_list = response.json()
        tokens = token_list.get("tokens") if isinstance(token_list, dict) else None
        if not isinstance(tokens, list):
            raise ValueError("Trust Wallet Ethereum-Tokenliste ist ungültig.")

        index: dict[str, dict[str, str]] = {}
        for token in tokens:
            if not isinstance(token, dict):
                continue
            symbol = token.get("symbol")
            address = token.get("address")
            if isinstance(symbol, str) and isinstance(address, str):
                index.setdefault(
                    symbol.casefold(),
                    {"symbol": symbol, "address": address},
                )
        _ETHEREUM_TOKEN_INDEX = index
        try:
            COINTRACKING_ICON_DIR.mkdir(parents=True, exist_ok=True)
            temporary_path = ETHEREUM_TOKEN_INDEX_PATH.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(index, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary_path.replace(ETHEREUM_TOKEN_INDEX_PATH)
        except OSError:
            logger.exception("Could not cache Trust Wallet token symbols")
        return index


def _safe_icon_filename(symbol: str) -> str:
    normalized_symbol = re.sub(r"[^a-zA-Z0-9._-]", "_", symbol.strip())
    digest = hashlib.sha256(symbol.casefold().encode("utf-8")).hexdigest()[:12]
    return f"{normalized_symbol[:40] or 'token'}-{digest}.png"


def _save_trust_wallet_icon(
    symbol: str,
    chain: str,
    address: str,
    *,
    native: bool = False,
) -> str | None:
    filename = _safe_icon_filename(symbol)
    icon_path = COINTRACKING_ICON_DIR / filename
    with _ICON_LOCKS_GUARD:
        icon_lock = _ICON_LOCKS.setdefault(filename, threading.Lock())
    with icon_lock:
        if icon_path.is_file():
            return f"{COINTRACKING_ICON_URL}/{filename}"

        chain_directory = {
            "eth": "ethereum",
            "arbitrum": "arbitrum",
            "polygon": "polygon",
            "bsc": "smartchain",
            "base": "base",
            "optimism": "optimism",
            "robinhood": "robinhoodchain",
            "solana": "solana",
        }.get(chain)
        if native:
            chain_directory = {
                "btc": "bitcoin",
                "eth": "ethereum",
                "sol": "solana",
                "bnb": "smartchain",
                "atom": "cosmos",
                "kava": "kava",
                "tezos": "tezos",
                "osmosis": "osmosis",
                "polkadot": "polkadot",
                "kusama": "kusama",
            }.get(chain)
            if not chain_directory:
                return None
            image_path = f"{chain_directory}/info/logo.png"
        elif not chain_directory:
            return None

        if not native:
            if chain == "solana":
                if not address.isalnum() or len(address) > 64:
                    return None
                asset_address = address
            else:
                if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
                    return None
                asset_address = address
            image_path = (
                f"{chain_directory}/assets/{asset_address}/logo.png"
            )

        source_url = f"{TRUST_WALLET_ASSETS_URL}/{image_path}"
        try:
            with urlopen(source_url, timeout=3) as response:
                image_data = response.read(2_000_001)
        except HTTPError as error:
            if error.code != 404:
                logger.warning(
                    "Could not load Trust Wallet icon for %s: HTTP %s",
                    symbol,
                    error.code,
                )
            return None
        except (URLError, TimeoutError, OSError):
            logger.warning(
                "Could not load Trust Wallet icon for %s",
                symbol,
                exc_info=True,
            )
            return None

        if len(image_data) > 2_000_000 or not image_data.startswith(_PNG_SIGNATURE):
            logger.warning("Trust Wallet returned an invalid icon for %s", symbol)
            return None

        try:
            COINTRACKING_ICON_DIR.mkdir(parents=True, exist_ok=True)
            temporary_path = icon_path.with_suffix(".tmp")
            temporary_path.write_bytes(image_data)
            temporary_path.replace(icon_path)
        except OSError:
            logger.exception("Could not cache Trust Wallet icon for %s", symbol)
            return None
        return f"{COINTRACKING_ICON_URL}/{filename}"


def _resolve_icon(
    symbol: str,
    ethereum_index: dict[str, dict[str, str]] | None,
) -> tuple[str, str | None]:
    symbol_key = symbol.casefold()
    icon_url = TOKEN_ICON_URL_OVERRIDES.get(symbol_key)
    if icon_url:
        return symbol, icon_url

    if ethereum_index:
        ethereum_token = ethereum_index.get(symbol_key)
        if ethereum_token:
            return (
                ethereum_token["symbol"],
                _save_trust_wallet_icon(
                    ethereum_token["symbol"],
                    "eth",
                    ethereum_token["address"],
                ),
            )

    native_chain = {
        "btc": "btc",
        "eth": "eth",
        "sol": "sol",
        "bnb": "bnb",
        "atom": "atom",
        "kava": "kava",
        "xtz": "tezos",
        "osmo": "osmosis",
        "dot2": "polkadot",
        "ksm": "kusama",
    }.get(symbol_key)
    if native_chain:
        return (
            symbol.upper(),
            _save_trust_wallet_icon(symbol.upper(), native_chain, "", native=True),
        )

    for chain in TOKEN_SYMBOL_CHAIN_PRIORITY:
        for address, known_symbol in TOKEN_TO_ASSET.get(chain, {}).items():
            if known_symbol.casefold() == symbol_key:
                return known_symbol, _get_token_icon_url(address, chain)
    return symbol, None


def add_trust_wallet_icons(
    balances: list[CoinTrackingBalance],
) -> list[CoinTrackingBalance]:
    ethereum_index: dict[str, dict[str, str]] | None = None
    if balances:
        try:
            ethereum_index = _load_ethereum_token_index()
        except (httpx.HTTPError, ValueError):
            logger.warning(
                "Could not load Trust Wallet Ethereum token symbols",
                exc_info=True,
            )

    def resolve(balance: CoinTrackingBalance) -> CoinTrackingBalance:
        original_symbol = balance["symbol"]
        icon_symbol = TOKEN_ICON_SYMBOL_ALIASES.get(
            original_symbol.casefold(),
            original_symbol,
        )
        symbol, icon_url = _resolve_icon(icon_symbol, ethereum_index)
        return {
            **balance,
            "symbol": (
                original_symbol if icon_symbol != original_symbol else symbol
            ),
            "icon_url": icon_url,
        }

    with ThreadPoolExecutor(max_workers=8) as executor:
        return list(executor.map(resolve, balances))
