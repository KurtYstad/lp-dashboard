#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations
import os
import asyncio
import logging
import time
from decimal import Decimal
from threading import RLock
from typing import Callable, Optional, Dict
from web3 import Web3
from dotenv import load_dotenv
import httpx

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logger = logging.getLogger(__name__)

_web3_instances: Dict = {}
_token_maps: Dict = {}
_price_cache: Dict[tuple[str, str], tuple[float, Decimal]] = {}
_price_cache_lock = RLock()
_PRICE_CACHE_TTL_SECONDS = 300

CHAIN_IDS = {
    "eth": 1, "arbitrum": 42161, "polygon": 137, "bsc": 56,
    "base": 8453, "optimism": 10, "robinhood": 4663, "solana": "solana",
}
DEXSCREENER_CHAIN_IDS = {
    "eth": "ethereum",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
    "bsc": "bsc",
    "base": "base",
    "optimism": "optimism",
    "robinhood": "robinhood",
}

ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
_ALCHEMY_RPC_ENDPOINTS = {
    "eth": "https://eth-mainnet.g.alchemy.com/v2/",
    "arbitrum": "https://arb-mainnet.g.alchemy.com/v2/",
    "polygon": "https://polygon-mainnet.g.alchemy.com/v2/",
    "base": "https://base-mainnet.g.alchemy.com/v2/",
    "optimism": "https://opt-mainnet.g.alchemy.com/v2/",
    "robinhood": "https://robinhood-mainnet.g.alchemy.com/v2/",
    "bsc": "https://bnb-mainnet.g.alchemy.com/v2/",
}
RPC_URLS = (
    {
        chain: f"{endpoint}{ALCHEMY_API_KEY}"
        for chain, endpoint in _ALCHEMY_RPC_ENDPOINTS.items()
    }
    if ALCHEMY_API_KEY
    else {}
)

AGGREGATOR_V3_ABI = [
    {"inputs": [], "name": "decimals", "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "latestRoundData", "outputs": [{"internalType": "uint80", "name": "roundId", "type": "uint80"},{"internalType": "int256", "name": "answer", "type": "int256"},{"internalType": "uint256", "name": "startedAt", "type": "uint256"},{"internalType": "uint256", "name": "updatedAt", "type": "uint256"},{"internalType": "uint80", "name": "answeredInRound", "type": "uint80"}], "stateMutability": "view", "type": "function"}
]

FEED_BY_CHAIN_ASSET = {
    "eth": {"ETH": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419", "BTC": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88", "USDC": "0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6", "USDT": "0x3E7d1eAB13ad0104d2750B8863b489D65364e32D", "EURC": "0xb49f677943BC038e9857d61E7d053CaA2C1734C1", "EURe": "0xb49f677943BC038e9857d61E7d053CaA2C1734C1", "SOL": "0x4ffC43a60e009B551865A93d232E33Fce9f01507"},
    "arbitrum": {"ETH": "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612", "WBTC": "0xd0C7101eACbB49F3deCcCc166d238410D6D46d57", "USDC": "0x50834F3163758fcC1Df9973b6e91f0F0F0434aD3", "USDT": "0x3f3f5dF88dC9F13eac63DF89EC16ef6e7E25DdE7"},
    "polygon": {"ETH": "0xF9680D99D6C9589e2a93a78A04A279e509205945", "WBTC": "0xDE31F8bFBD8c84b5360CFACCa3539B938dd78ae6", "USDC": "0xfE4A8cc5b5B2366C1B58Bea3858e81843581b2F7", "USDT": "0x0A6513e40db6EB1b165753AD52E80663aeA50545", "POL": "0xAB594600376Ec9fD91F8e885dADF0CE036862dE0"},
    "bsc": {"BNB": "0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE", "WBNB": "0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE", "BTC": "0x264990fbd0A4796A3E3d8E37C4d5F87a3aCa5Ebf", "ETH": "0x9ef1B8c0E4F7dc8bF5719Ea496883DC6401d5b2e", "USDC": "0x51597f405303C4377E36123cBc172b13269EA163", "USDT": "0xB97AD0E74fa7d920791E90258A6E2085088b4320", "Cake": "0xB6064eD41d4f67e353768aA239cA86f4F73665a1"},
    "base": {"ETH": "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70", "WBTC": "0x64c911996D3c6aC71f9b455B1E8E7266BcbD848F", "cbBTC": "0x10509b4053385b49145Fab2D6B1c58e96Eac5b79", "USDC": "0x7e860098F58bBFC8648a4311b374B1D669a2bc6B"},
    "optimism": {"ETH": "0x13e3Ee699D1909E989722E753853AE30b17e08c5", "WBTC": "0x718A5788b89454aAE3A028AE9c111A29Be6c2a6F", "USDC": "0x16a9FA2FDa030272Ce99B29CF780dFA30361E0f3", "USDT": "0xECef79E109e997bCA29c1c0897ec9d7b03647F5E"},
    "robinhood": {"ETH": "0x78F3556b67E17Df817D51Ef5a990cDaF09E8d3A9", "USDG": "0x61B7e5650328764B076A108EFF5fa7282a1B9aD2"},
    "solana": {"SOL": "solana", "USDC": "0xeaa020c61cc479712813461ce153894a96a6c00b21ed0cfc2798d1f9a9e9c94a", "ETH": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace", "BTC": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43", "SPYx": "0x2817b78438c769357182c04346fddaad1178c82f4048828fe0997c3c64624e14"},
}

TOKEN_TO_ASSET = {
    "eth": {"0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2": "ETH", "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599": "BTC", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": "USDC", "0xdAC17F958D2ee523a2206206994597C13D831ec7": "USDT", "0x39b8B6385416f4cA36a20319F70D28621895279D": "EURe", "0x1aBaEA1f7C830bD89Acc67eC4af516284b1bC33c": "EURC", "0xD31a59c85aE9D8edEFeC411D448f90841571b89c": "SOL"},
    "arbitrum": {"0x82aF49447D8a07e3bd95BD0d56f35241523fBab1": "ETH", "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f": "WBTC", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831": "USDC", "0xFd086bC7CD5C481DCC9C85ebe478A1C0b69FCbb9": "USDT"},
    "polygon": {"0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619": "ETH", "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6": "WBTC", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174": "USDC", "0x3c499c542cef5e3811E1192ce70d8cC03d5c3359": "USDC", "0xc2132D05D31c914a87C6611C10748AEb04B58e8F": "USDT", "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270": "POL"},
    "bsc": {"0xBB4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c": "BNB", "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c": "BTC", "0x0555E30da8f98308EdB960aa94C0Db47230d2B9c": "BTC", "0x2170Ed0880ac9A755fd29B2688956BD959F933F8": "ETH", "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d": "USDC", "0x55d398326f99059fF775485246999027B3197955": "USDT", "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82": "Cake"},
    "base": {"0x4200000000000000000000000000000000000006": "ETH", "0x0555E30da8f98308EdB960aa94C0Db47230d2B9c": "WBTC", "0x1ceA84203673764244E05693e42E6Ace62bE9BA5": "WBTC", "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf": "cbBTC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913": "USDC", "0x940181a94A35A4569E4529A3CDfB74e38FD98631": "AERO"},
    "optimism": {"0x4200000000000000000000000000000000000006": "ETH", "0x68f180fcCe6836688e9084f035309E29Bf0A2095": "WBTC", "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85": "USDC", "0x7F5c764cBc14f9669B88837ca1490cCa17c31607": "USDC", "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58": "USDT"},
    "robinhood": {"0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73": "ETH", "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168": "USDG"},
    "solana": {"So11111111111111111111111111111111111111112": "SOL", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT", "2FPyTwcZLUg1MDrwsyoP4D6s1tM7hAkHYRjkNb5w6Pxk": "ETH", "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E": "BTC", "XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W": "SPYx"},
}

def normalize_chain(chain: str) -> str:
    key = chain.strip().lower()
    if "ethereum" in key: key = "eth"
    if key in {"robinhood chain", "robinhoodchain"}: key = "robinhood"
    if key not in CHAIN_IDS: raise ValueError(f"Unsupported chain: {chain}")
    return key

def get_web3_instance(chain_key: str, rpc_url: str) -> Web3:
    if chain_key not in _web3_instances: _web3_instances[chain_key] = Web3(Web3.HTTPProvider(rpc_url))
    return _web3_instances[chain_key]

def get_token_map(chain_key: str) -> Dict:
    if chain_key == "solana": raise ValueError("Solana handled separately")
    if chain_key not in _token_maps:
        _token_maps[chain_key] = {Web3.to_checksum_address(addr): asset for addr, asset in TOKEN_TO_ASSET.get(chain_key, {}).items()}
    return _token_maps[chain_key]

def _get_cached_price(
    key: tuple[str, str],
    fetch_price: Callable[[], Decimal],
) -> Decimal:
    now = time.monotonic()
    with _price_cache_lock:
        cached = _price_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]

    price = fetch_price()
    if price <= 0:
        raise RuntimeError(f"Invalid non-positive price for {key[0]}:{key[1]}")

    now = time.monotonic()
    with _price_cache_lock:
        if key not in _price_cache and len(_price_cache) >= 1024:
            expired_keys = [
                cache_key
                for cache_key, (expiry, _) in _price_cache.items()
                if expiry <= now
            ]
            for cache_key in expired_keys:
                _price_cache.pop(cache_key, None)
            if len(_price_cache) >= 1024:
                _price_cache.pop(next(iter(_price_cache)))
        _price_cache[key] = (now + _PRICE_CACHE_TTL_SECONDS, price)
    return price


def get_dexscreener_token_usd_price(
    token_address: str,
    asset: str,
    chain_id: str,
) -> Decimal:
    token_address_lower = token_address.lower()
    response = httpx.get(
        f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
        timeout=10.0,
    )
    response.raise_for_status()
    pairs = response.json().get("pairs") or []
    priced_pairs = [
        pair for pair in pairs
        if pair.get("chainId") == chain_id
        and token_address_lower in (
            pair.get("baseToken", {}).get("address", "").lower(),
            pair.get("quoteToken", {}).get("address", "").lower(),
        )
        and pair.get("pairAddress")
        and pair.get("priceUsd")
    ]
    if not priced_pairs:
        raise RuntimeError(f"No USD trading pair found for {asset} on {chain_id}")

    pair = max(
        priced_pairs,
        key=lambda item: Decimal(str(item.get("liquidity", {}).get("usd", 0))),
    )
    price = Decimal(str(pair["priceUsd"]))
    if pair.get("baseToken", {}).get("address", "").lower() != token_address_lower:
        price_native = Decimal(str(pair.get("priceNative", "0")))
        if price_native <= 0:
            raise RuntimeError(f"Invalid quote-token price for {asset} on {chain_id}")
        price /= price_native
    if price <= 0:
        raise RuntimeError(f"Invalid DexScreener price for {asset}: {price}")
    return price

def get_token_usd_price(token_address: str, chain: str, rpc_url: Optional[str] = None) -> Decimal:
    chain_key = normalize_chain(chain)
    normalized_token = (
        token_address
        if chain_key == "solana"
        else Web3.to_checksum_address(token_address)
    )
    cache_token = (
        normalized_token
        if chain_key == "solana"
        else normalized_token.lower()
    )
    cache_key = (chain_key, cache_token)
    return _get_cached_price(
        cache_key,
        lambda: _fetch_token_usd_price(normalized_token, chain_key, rpc_url),
    )


def _fetch_token_usd_price(
    token_address: str,
    chain_key: str,
    rpc_url: Optional[str],
) -> Decimal:
    if chain_key == "solana":
        asset = TOKEN_TO_ASSET.get(chain_key, {}).get(token_address)
    else:
        asset = get_token_map(chain_key).get(token_address)
    if chain_key == "solana":
        return get_token_usd_price_solana(token_address, chain_key, rpc_url)

    if chain_key == "base" and asset == "AERO":
        return get_dexscreener_token_usd_price(token_address, asset, chain_key)

    feed_address = FEED_BY_CHAIN_ASSET.get(chain_key, {}).get(asset or "")
    if not feed_address:
        return get_dexscreener_token_usd_price(
            token_address,
            asset or token_address,
            DEXSCREENER_CHAIN_IDS[chain_key],
        )

    rpc_url = rpc_url or RPC_URLS.get(chain_key)
    if not rpc_url:
        logger.warning(
            "No RPC URL configured for %s; using DexScreener for %s",
            chain_key,
            token_address,
        )
        return get_dexscreener_token_usd_price(
            token_address,
            asset or token_address,
            DEXSCREENER_CHAIN_IDS[chain_key],
        )
    w3 = get_web3_instance(chain_key, rpc_url)
    feed = w3.eth.contract(
        address=Web3.to_checksum_address(feed_address),
        abi=AGGREGATOR_V3_ABI,
    )
    decimals = feed.functions.decimals().call()
    _, answer, _, _, _ = feed.functions.latestRoundData().call()
    if answer <= 0:
        raise RuntimeError(f"Invalid Chainlink answer for {asset} on {chain_key}")
    return Decimal(answer) / (Decimal(10) ** decimals)

def get_token_usd_price_solana(token_address: str, chain_key: str, rpc_url: Optional[str] = None) -> Decimal:
    token_map = TOKEN_TO_ASSET.get(chain_key, {})
    asset = token_map.get(token_address, token_address)
    feed_address = FEED_BY_CHAIN_ASSET.get(chain_key, {}).get(asset)
    try:
        return asyncio.run(
            _fetch_solana_price_with_fallbacks_async(
                token_address, asset, feed_address
            )
        )
    except Exception as error:
        if asset == "SOL":
            wrapped_sol = "0xD31a59c85aE9D8edEFeC411D448f90841571b89c"
            try:
                return get_token_usd_price(wrapped_sol, "eth")
            except Exception as fallback_error:
                raise RuntimeError(
                    f"SOL price lookup failed ({error}); Ethereum fallback failed "
                    f"({fallback_error})"
                ) from fallback_error
        raise RuntimeError(f"Failed to get price for {asset} on {chain_key}: {error}") from error



async def _fetch_solana_price_with_fallbacks_async(token_address: str, asset: str, pyth_account: Optional[str]) -> Decimal:
    errors = []
    async with httpx.AsyncClient() as client:
        if pyth_account and pyth_account != "solana":
            try:
                return await _fetch_pyth_price_async(client, pyth_account, asset)
            except Exception as error:
                errors.append(f"Pyth: {error}")
        try:
            return await _fetch_jupiter_price_async(client, token_address, asset)
        except Exception as error:
            errors.append(f"Jupiter: {error}")
        try:
            return await _fetch_dexscreener_price_async(client, token_address, asset)
        except Exception as error:
            errors.append(f"DexScreener: {error}")
    raise RuntimeError("; ".join(errors))


async def _fetch_jupiter_price_async(client: httpx.AsyncClient, token_address: str, asset: str) -> Decimal:
    response = await client.get(
        "https://lite-api.jup.ag/price/v3",
        params={"ids": token_address},
        timeout=10.0,
    )
    response.raise_for_status()
    price_data = response.json().get(token_address)
    if not price_data or price_data.get("usdPrice") is None:
        raise RuntimeError(f"No Jupiter USD price found for {asset}")
    price = Decimal(str(price_data["usdPrice"]))
    if price <= 0:
        raise RuntimeError(f"Invalid Jupiter price for {asset}: {price}")
    return price


async def _fetch_dexscreener_price_async(
    client: httpx.AsyncClient,
    token_address: str,
    asset: str,
) -> Decimal:
    token_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
    response = await client.get(token_url, timeout=10.0)
    response.raise_for_status()
    pairs = response.json().get("pairs") or []
    priced_pairs = [
        pair for pair in pairs
        if pair.get("chainId") == "solana"
        and token_address in (
            pair.get("baseToken", {}).get("address"),
            pair.get("quoteToken", {}).get("address"),
        )
        and pair.get("pairAddress")
        and pair.get("priceUsd")
    ]
    if not priced_pairs:
        raise RuntimeError(f"No USD trading pair found for {asset}")

    selected_pair = max(
        priced_pairs,
        key=lambda item: Decimal(str(item.get("liquidity", {}).get("usd", 0))),
    )
    pool_url = (
        "https://api.dexscreener.com/latest/dex/pairs/solana/"
        f"{selected_pair['pairAddress']}"
    )
    response = await client.get(pool_url, timeout=10.0)
    response.raise_for_status()
    pool_pairs = response.json().get("pairs") or []
    pool = next(
        (
            pair for pair in pool_pairs
            if pair.get("pairAddress", "").lower()
            == selected_pair["pairAddress"].lower()
            and token_address in (
                pair.get("baseToken", {}).get("address"),
                pair.get("quoteToken", {}).get("address"),
            )
        ),
        None,
    )
    if not pool or not pool.get("priceUsd"):
        raise RuntimeError(f"No USD price returned for {asset} pool")

    price = Decimal(str(pool["priceUsd"]))
    if pool.get("baseToken", {}).get("address") != token_address:
        price_native = Decimal(str(pool.get("priceNative", "0")))
        if price_native <= 0:
            raise RuntimeError(f"Invalid quote-token price for {asset} pool")
        price /= price_native
    if price <= 0:
        raise RuntimeError(f"Invalid DexScreener price for {asset}: {price}")
    return price


async def _fetch_pyth_price_async(client: httpx.AsyncClient, pyth_account: str, asset: str) -> Decimal:
    pyth_feed_ids = {"ETH": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace", "BTC": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43", "USDC": "0xeaa020c61cc479712813461ce153894a96a6c00b21ed0cfc2798d1f9a9e9c94a"}
    feed_id = pyth_feed_ids.get(asset, pyth_account)
    hermes_url = "https://hermes.pyth.network/api/latest_price_feeds"
    response = await client.get(hermes_url, params={"ids[]": feed_id}, timeout=10.0)
    response.raise_for_status()
    result = response.json()
    if not result or "price" not in result[0]:
        raise RuntimeError(f"No Pyth price data returned for {asset}")
    price_info = result[0]["price"]
    price_value = int(price_info.get("price", 0))
    exponent = int(price_info.get("expo", 0))
    if price_value <= 0:
        raise RuntimeError(f"Invalid Pyth price for {asset}: {price_value}")
    return Decimal(price_value) * (Decimal(10) ** Decimal(exponent))


def get_eur_usd_rate() -> Decimal:
    def fetch_rate() -> Decimal:
        rpc_url = RPC_URLS.get("eth")
        if not rpc_url:
            raise ValueError("Missing Ethereum RPC URL; configure ALCHEMY_API_KEY")
        w3 = get_web3_instance("eth", rpc_url)
        feed = w3.eth.contract(address=Web3.to_checksum_address("0xb49f677943BC038e9857d61E7d053CaA2C1734C1"), abi=AGGREGATOR_V3_ABI)
        _, answer, _, _, _ = feed.functions.latestRoundData().call()
        if answer <= 0:
            raise RuntimeError("Invalid Chainlink EUR/USD answer")
        return Decimal(answer) / (Decimal(10) ** feed.functions.decimals().call())

    return _get_cached_price(("fx", "eur-usd"), fetch_rate)