#!/usr/bin/env python
# -*- coding: utf-8 -*-

import io
import os
import sys
import datetime
import argparse
import telegram_send
import mariadb
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
import numpy as np
import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, getcontext
import dotenv
from typing import Any
from eth_abi import encode
from eth_utils import keccak, to_checksum_address
from web3 import AsyncHTTPProvider, AsyncWeb3


# ============================================================
# CONFIG
# ============================================================

# Load environment variables from .env
dotenv.load_dotenv()

parser = argparse.ArgumentParser(description="Uniswap V3/V4 fee monitor")
parser.add_argument(
    "--chain",
    choices=["arbitrum", "ethereum", "polygon", "optimism", "base"],
    help="Blockchain to use. Overrides the CHAIN environment variable.",
)
parser.add_argument(
    "position_id",
    nargs="?",
    type=int,
    help="Process only this position ID.",
)
parser.add_argument(
    "version",
    nargs="?",
    choices=["v3", "v4"],
    help="Uniswap protocol version for the position ID.",
)
args = parser.parse_args()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT_STR = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
TABLE_SNAPSHOT = os.getenv("TABLE_SNAPSHOT")
TABLE_TOTAL = os.getenv("TABLE_TOTAL")
TELEGRAM_CONF = os.getenv("TELEGRAM_CONF")
CHAIN = args.chain or os.getenv("CHAIN")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
POSITION_READ_CONCURRENCY = int(os.getenv("POSITION_READ_CONCURRENCY", "4"))

REQUIRED_ENV = [
    "DB_USER",
    "DB_PASSWORD",
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "TABLE_SNAPSHOT",
    "TABLE_TOTAL",
    "TELEGRAM_CONF",
    "ALCHEMY_API_KEY",
]
if not CHAIN:
    print("Missing chain selection: pass --chain <arbitrum|ethereum|polygon|optimism|base> or set CHAIN in the environment.")
    sys.exit(1)

if CHAIN not in {"arbitrum", "ethereum", "polygon", "optimism", "base"}:
    print(f"Unsupported chain '{CHAIN}'. Supported chains: arbitrum, ethereum, polygon, optimism, base")
    sys.exit(1)

if not all(os.getenv(name) for name in REQUIRED_ENV):
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    print(f"Missing required environment variables: {', '.join(missing)}")
    sys.exit(1)

DB_PORT = int(DB_PORT_STR)

CHAIN_CONFIG = {
    "arbitrum": {
        "rpc": f"https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
        "chain_id": 42161,
        "v3_position_manager": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "v4_position_manager": "0xd88F38F930b7952f2DB2432Cb002E7abbF3dD869",
        "v4_state_view": "0x76Fd297e2D437cd7f76d50F01AfE6160f86e9990",
        "eth_usd_feed": "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612",
        "weth": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "usd_stables": {
            "USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
            "USDC.e": "0xFF970A61A04b1CA14834A43f5dE4533eBDDB5CC8",
            "USDT": "0xFd086bC7CD5C481DCC9C85ebe478A1C0b69FCbb9",
            "DAI": "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
        },
    },
    "ethereum": {
        "rpc": f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
        "chain_id": 1,
        "v3_position_manager": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "v4_position_manager": "0x0000000000000000000000000000000000000000",
        "v4_state_view": "0x0000000000000000000000000000000000000000",
        "eth_usd_feed": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
        "weth": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "usd_stables": {
            "USDC": "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        },
    },
    "polygon": {
        "rpc": f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
        "chain_id": 137,
        "v3_position_manager": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "v4_position_manager": "0x0000000000000000000000000000000000000000",
        "v4_state_view": "0x0000000000000000000000000000000000000000",
        "eth_usd_feed": "0xF9680D99d6C9589e2a93a78A04a279e509205945",
        "weth": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        "usd_stables": {
            "USDC": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            "USDT": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
            "DAI": "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
        },
    },
    "optimism": {
        "rpc": f"https://opt-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
        "chain_id": 10,
        "v3_position_manager": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "v4_position_manager": "0x0000000000000000000000000000000000000000",
        "v4_state_view": "0x0000000000000000000000000000000000000000",
        "eth_usd_feed": "0x13e3Ee699D1909e989722E753853AE30b17e08c5",
        "weth": "0x4200000000000000000000000000000000000006",
        "usd_stables": {
            "USDC": "0x7F5c764cBc14f9669B88837ca1490cCa17c31607",
            "USDT": "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
            "DAI": "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
        },
    },
    "base": {
        "rpc": f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
        "chain_id": 8453,
        "v3_position_manager": "0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1",
        "v4_position_manager": "0x0000000000000000000000000000000000000000",
        "v4_state_view": "0x0000000000000000000000000000000000000000",
        "eth_usd_feed": "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",
        "weth": "0x4200000000000000000000000000000000000006",
        "usd_stables": {
            "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "USDT": "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
            "DAI": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
        },
    },
}


# ============================================================
# ABIS
# ============================================================

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def create_abi(contract: str) -> list[dict[str, Any]]:
    if not contract or contract.lower() == ZERO_ADDRESS:
        return []

    fn = "uniswap_" + contract + ".abi"

    with open(fn, "r", encoding="utf-8") as file:
        return json.load(file)


STATE_VIEW_ABI = create_abi(CHAIN_CONFIG[CHAIN]["v4_state_view"]) if CHAIN_CONFIG[CHAIN].get("v4_state_view") and CHAIN_CONFIG[CHAIN]["v4_state_view"].lower() != ZERO_ADDRESS else []
POSITION_MANAGER_ABI = create_abi(CHAIN_CONFIG[CHAIN]["v4_position_manager"]) if CHAIN_CONFIG[CHAIN].get("v4_position_manager") and CHAIN_CONFIG[CHAIN]["v4_position_manager"].lower() != ZERO_ADDRESS else []

V3_POSITION_MANAGER_ABI = [
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "positions",
        "outputs": [
            {"internalType": "uint96", "name": "nonce", "type": "uint96"},
            {"internalType": "address", "name": "operator", "type": "address"},
            {"internalType": "address", "name": "token0", "type": "address"},
            {"internalType": "address", "name": "token1", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
            {"internalType": "int24", "name": "tickLower", "type": "int24"},
            {"internalType": "int24", "name": "tickUpper", "type": "int24"},
            {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
            {"internalType": "uint256", "name": "feeGrowthInside0LastX128", "type": "uint256"},
            {"internalType": "uint256", "name": "feeGrowthInside1LastX128", "type": "uint256"},
            {"internalType": "uint128", "name": "tokensOwed0", "type": "uint128"},
            {"internalType": "uint128", "name": "tokensOwed1", "type": "uint128"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "factory",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

V3_POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "feeGrowthGlobal0X128",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "feeGrowthGlobal1X128",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "int24", "name": "tick", "type": "int24"}],
        "name": "ticks",
        "outputs": [
            {"internalType": "uint128", "name": "liquidityGross", "type": "uint128"},
            {"internalType": "int128", "name": "liquidityNet", "type": "int128"},
            {"internalType": "uint256", "name": "feeGrowthOutside0X128", "type": "uint256"},
            {"internalType": "uint256", "name": "feeGrowthOutside1X128", "type": "uint256"},
            {"internalType": "int56", "name": "tickCumulativeOutside", "type": "int56"},
            {"internalType": "uint160", "name": "secondsPerLiquidityOutsideX128", "type": "uint160"},
            {"internalType": "uint32", "name": "secondsOutside", "type": "uint32"},
            {"internalType": "bool", "name": "initialized", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

V3_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

ERC20_ABI = [
    {
        "name": "symbol",
        "outputs": [{"type": "string"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
]

CHAINLINK_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"type": "uint80"},
            {"type": "int256"},
            {"type": "uint256"},
            {"type": "uint256"},
            {"type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]


# ============================================================
# ADDRESS NORMALIZATION
# ============================================================

def normalize_address(address: str) -> str:
    if not isinstance(address, str):
        raise TypeError(f"Address must be a string, got {type(address).__name__}")

    candidate = address.strip()
    if not candidate:
        raise ValueError("Address is empty")

    if not candidate.startswith("0x"):
        candidate = "0x" + candidate

    if len(candidate) != 42:
        raise ValueError(f"Invalid EVM address length: {candidate}")

    hex_part = candidate[2:]
    if len(hex_part) != 40 or any(ch not in "0123456789abcdefABCDEF" for ch in hex_part):
        raise ValueError(f"Invalid EVM address: {candidate}")

    return AsyncWeb3.to_checksum_address(candidate.lower())


# ============================================================
# RPC CLIENT WITH RETRY/BACKOFF
# ============================================================

class RetryAsyncHTTPProvider(AsyncHTTPProvider):
    async def make_request(self, method, params):
        retries = 8
        delay = 1.0

        for _ in range(retries):
            try:
                return await super().make_request(method, params)
            except Exception as e:
                error_str = str(e).lower()

                if (
                    "429" in error_str
                    or "rate limit" in error_str
                    or "too many requests" in error_str
                ):
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue

                raise

        raise Exception("Max retry attempts exceeded")


w3 = AsyncWeb3(RetryAsyncHTTPProvider(CHAIN_CONFIG[CHAIN]["rpc"]))
V3_POSITION_MANAGER = w3.eth.contract(
    address=normalize_address(CHAIN_CONFIG[CHAIN]["v3_position_manager"]),
    abi=V3_POSITION_MANAGER_ABI,
)
POSITION_MANAGER = w3.eth.contract(
    address=normalize_address(CHAIN_CONFIG[CHAIN]["v4_position_manager"]),
    abi=POSITION_MANAGER_ABI,
) if CHAIN_CONFIG[CHAIN].get("v4_position_manager") and CHAIN_CONFIG[CHAIN]["v4_position_manager"] != "0x0000000000000000000000000000000000000000" else None
STATE_VIEW = w3.eth.contract(
    address=normalize_address(CHAIN_CONFIG[CHAIN]["v4_state_view"]),
    abi=STATE_VIEW_ABI,
) if CHAIN_CONFIG[CHAIN].get("v4_state_view") and CHAIN_CONFIG[CHAIN]["v4_state_view"] != "0x0000000000000000000000000000000000000000" else None
ETH_USD_FEED = w3.eth.contract(
    address=normalize_address(CHAIN_CONFIG[CHAIN]["eth_usd_feed"]),
    abi=CHAINLINK_ABI,
)
TOKEN_SYMBOL_CACHE: dict[str, str] = {}
TOKEN_DECIMALS_CACHE: dict[str, int] = {}


# ============================================================
# HELPERS
# ============================================================

getcontext().prec = 80

Q128 = 2**128
Q256 = 2**256
Q96 = 2**96


@dataclass(frozen=True)
class PositionSnapshot:
    pos_id: int
    token_0: str
    token_1: str
    symbol_0: str
    symbol_1: str
    fee_0: Decimal
    fee_1: Decimal
    quote_0: Decimal
    quote_1: Decimal
    price_lower: Decimal
    price_upper: Decimal
    current_price: Decimal
    fee: int
    liquidity: int
    total_unclaimed_usd_value: Decimal
    in_range: int
    valuation_source: str


@dataclass(frozen=True)
class FeeSummary:
    dates: list[Any]
    prices: list[Decimal]
    hourly_fee_values: list[Decimal]
    fee_last_hour: Decimal
    fee_last_day: Decimal
    raw_delta_0: Decimal
    raw_delta_1: Decimal
    token_0: str
    token_1: str
    symbol_0: str
    symbol_1: str


def sub_in_256(x: int, y: int) -> int:
    return (x - y) % Q256


def compute_unclaimed_fees(
    liquidity: int,
    fee_growth_inside_current: int,
    fee_growth_inside_last: int,
) -> int:
    delta = sub_in_256(fee_growth_inside_current, fee_growth_inside_last)

    return liquidity * delta // Q128


def decode_int24(value: int) -> int:
    return value if value < 0x800000 else value - 0x1000000


def tick_to_token1_per_token0(tick: int, decimals0: int, decimals1: int) -> Decimal:
    raw_price = Decimal("1.0001") ** Decimal(tick)

    return raw_price * Decimal(10**decimals0) / Decimal(10**decimals1)


def sqrt_price_x96_to_token1_per_token0(
    sqrt_price_x96: int,
    decimals0: int,
    decimals1: int,
) -> Decimal:
    raw_price = Decimal(sqrt_price_x96) ** 2 / Decimal(2**192)

    return raw_price * Decimal(10**decimals0) / Decimal(10**decimals1)

def is_usd_stable(token: str) -> bool:
    stablecoins = CHAIN_CONFIG[CHAIN]["usd_stables"].values()

    return token.lower() in {stable.lower() for stable in stablecoins}


def get_token_usd_prices(
    token0: str,
    token1: str,
    token1_per_token0: Decimal,
    eth_usd: Decimal,
) -> tuple[Decimal, Decimal, str]:
    weth = normalize_address(CHAIN_CONFIG[CHAIN]["weth"])

    token0_is_weth = token0.lower() == weth.lower()
    token1_is_weth = token1.lower() == weth.lower()
    token0_is_stable = is_usd_stable(token0)
    token1_is_stable = is_usd_stable(token1)

    if token0_is_weth:
        return eth_usd, eth_usd / token1_per_token0, "pool_ratio_weth_chainlink_eth_usd"

    if token1_is_weth:
        return token1_per_token0 * eth_usd, eth_usd, "pool_ratio_weth_chainlink_eth_usd"

    if token0_is_stable:
        return Decimal(1), Decimal(1) / token1_per_token0, "pool_ratio_usd_stable"

    if token1_is_stable:
        return token1_per_token0, Decimal(1), "pool_ratio_usd_stable"

    return Decimal(0), Decimal(0), "unsupported_no_weth_or_usd_stable_anchor"


async def get_eth_usd() -> Decimal:
    feed_address = CHAIN_CONFIG[CHAIN]["eth_usd_feed"]
    code = await w3.eth.get_code(normalize_address(feed_address))
    if not code:
        raise RuntimeError(
            f"Chainlink ETH/USD feed {feed_address} has no contract code on {CHAIN}. "
            "Check the feed address and RPC network."
        )

    try:
        data = await ETH_USD_FEED.functions.latestRoundData().call()
    except Exception as exc:
        raise RuntimeError(
            f"Could not read Chainlink ETH/USD feed {feed_address} on {CHAIN}. "
            "Check the feed address and RPC network."
        ) from exc

    return Decimal(data[1]) / Decimal(10**8)


async def get_symbol(token: str) -> str:
    token = normalize_address(token)
    if token in TOKEN_SYMBOL_CACHE:
        return TOKEN_SYMBOL_CACHE[token]

    contract = w3.eth.contract(address=normalize_address(token), abi=ERC20_ABI)
    symbol = await contract.functions.symbol().call()
    if CHAIN == "polygon" and symbol.upper() == "USDT0":
        symbol = "USDT"
    TOKEN_SYMBOL_CACHE[token] = symbol

    return symbol


async def get_decimals(token: str) -> int:
    token = normalize_address(token)
    if token in TOKEN_DECIMALS_CACHE:
        return TOKEN_DECIMALS_CACHE[token]

    contract = w3.eth.contract(address=normalize_address(token), abi=ERC20_ABI)
    decimals = await contract.functions.decimals().call()
    TOKEN_DECIMALS_CACHE[token] = decimals

    return decimals


def db_connect():
    try:
        connection = mariadb.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
        )
    except mariadb.Error as exc:
        raise RuntimeError(f"Error connecting to MariaDB: {exc}") from exc

    return connection, connection.cursor()


def fetch_snapshot_history(cursor, pos_id):
    cursor.execute(
        f"""
        SELECT date, token_0, token_1, symbol_0, symbol_1,
               fee_0, fee_1, current_price
        FROM {TABLE_SNAPSHOT}
        WHERE id = ? AND date >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        ORDER BY date ASC
        """,
        (pos_id,),
    )
    return list(cursor)


def safe_decimal(value):
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def format_usd(value):
    value = safe_decimal(value)
    quantized = value.quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)
    return format(quantized, "f")


def format_token_amount(value, symbol):
    value = safe_decimal(value)
    symbol = symbol.upper() if symbol else ""
    if symbol in {"USDT", "USDC"}:
        precision = Decimal("0.000000")
    else:
        precision = Decimal("0.000000000000000000")
    quantized = value.quantize(precision, rounding=ROUND_HALF_UP)
    return format(quantized, "f")


def format_fee_percent(fee: int) -> str:
    return format(Decimal(fee) / Decimal("10000"), "f")


def read_position_ids(filename="positions") -> list[tuple[str | None, int]]:
    position_ids: list[tuple[str | None, int]] = []
    with open(filename, "r", encoding="utf-8") as file:
        for line_no, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue

            version, separator, raw_token_id = line.partition(":")
            if separator:
                version = version.strip().lower()
                if version not in {"v3", "v4"}:
                    print(
                        f"Skipping invalid position version in "
                        f"{filename}:{line_no}: {version}"
                    )
                    continue
                line = raw_token_id.strip()
            else:
                version = None

            try:
                position_ids.append((version, int(line)))
            except ValueError:
                print(f"Skipping invalid position id in {filename}:{line_no}: {line}")

    return position_ids


def remove_position_id(
    position_id: int,
    version: str | None,
    filename: str = "positions",
) -> bool:
    try:
        with open(filename, "r", encoding="utf-8", newline="") as file:
            lines = file.readlines()
    except FileNotFoundError:
        return False

    remaining = []
    removed = False
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            remaining.append(line)
            continue

        entry_version, separator, entry_id = entry.partition(":")
        if separator:
            entry_version = entry_version.strip().lower()
            entry_id = entry_id.strip()
        else:
            entry_version = None
            entry_id = entry

        try:
            matches_id = int(entry_id) == position_id
        except ValueError:
            matches_id = False

        if matches_id and entry_version == version:
            removed = True
        else:
            remaining.append(line)

    if removed:
        with open(filename, "w", encoding="utf-8", newline="") as file:
            file.writelines(remaining)
    return removed


def insert_snapshot(cursor, snapshot: PositionSnapshot, timestamp: str):
    cursor.execute(
        f"INSERT INTO {TABLE_SNAPSHOT} (id, date, token_0, token_1, "
        f"symbol_0, symbol_1, fee_0, fee_1, quote_0, quote_1, "
        f"current_price, in_range) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot.pos_id,
            timestamp,
            snapshot.token_0,
            snapshot.token_1,
            snapshot.symbol_0,
            snapshot.symbol_1,
            snapshot.fee_0,
            snapshot.fee_1,
            snapshot.quote_0,
            snapshot.quote_1,
            snapshot.current_price,
            snapshot.in_range,
        ),
    )


def create_image_buffer(fig):
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    buffer.seek(0)
    plt.close(fig)
    return buffer


async def send_telegram_text(message):
    await telegram_send.send(
        conf=(TELEGRAM_CONF), parse_mode="html", messages=[message]
    )


async def send_telegram_image(image_buffer):
    await telegram_send.send(conf=(TELEGRAM_CONF), images=[image_buffer])


def calculate_fee_summary(cursor, snapshot: PositionSnapshot) -> FeeSummary | None:
    rows = fetch_snapshot_history(cursor, snapshot.pos_id)
    if len(rows) < 2:
        return None

    dates = [row[0] for row in rows]
    prices = [safe_decimal(row[7]) for row in rows]
    fees_0 = [safe_decimal(row[5]) for row in rows]
    fees_1 = [safe_decimal(row[6]) for row in rows]
    token_0 = rows[-1][1]
    token_1 = rows[-1][2]
    symbol_0 = rows[-1][3]
    symbol_1 = rows[-1][4]

    hourly_fee_values = []
    for previous, current in zip(rows, rows[1:]):
        delta_0 = safe_decimal(current[5]) - safe_decimal(previous[5])
        delta_1 = safe_decimal(current[6]) - safe_decimal(previous[6])
        hourly_fee_values.append(delta_0 * snapshot.quote_0 + delta_1 * snapshot.quote_1)

    fee_last_hour = hourly_fee_values[-1]
    fee_last_day = (
        (fees_0[-1] - fees_0[0]) * snapshot.quote_0
        + (fees_1[-1] - fees_1[0]) * snapshot.quote_1
    )

    raw_delta_0 = fees_0[-1] - fees_0[-2]
    raw_delta_1 = fees_1[-1] - fees_1[-2]
    unsupported_valuation = snapshot.valuation_source.startswith("unsupported")

    if fee_last_day <= 0 and not unsupported_valuation:
        return None

    if unsupported_valuation and raw_delta_0 <= 0 and raw_delta_1 <= 0:
        return None

    return FeeSummary(
        dates=dates,
        prices=prices,
        hourly_fee_values=hourly_fee_values,
        fee_last_hour=fee_last_hour,
        fee_last_day=fee_last_day,
        raw_delta_0=raw_delta_0,
        raw_delta_1=raw_delta_1,
        token_0=token_0,
        token_1=token_1,
        symbol_0=symbol_0,
        symbol_1=symbol_1,
    )


def insert_fee_deltas(cursor, summary: FeeSummary, pos_id: int, timestamp: str):
    if summary.raw_delta_0 > 0:
        cursor.execute(
            f"INSERT INTO {TABLE_TOTAL} (pos_id, timestamp, token, symbol, "
            f"fee, chain) VALUES (?, ?, ?, ?, ?, ?)",
            (
                pos_id,
                timestamp,
                summary.token_0,
                summary.symbol_0,
                summary.raw_delta_0,
                CHAIN,
            ),
        )

    if summary.raw_delta_1 > 0:
        cursor.execute(
            f"INSERT INTO {TABLE_TOTAL} (pos_id, timestamp, token, symbol, "
            f"fee, chain) VALUES (?, ?, ?, ?, ?, ?)",
            (
                pos_id,
                timestamp,
                summary.token_1,
                summary.symbol_1,
                summary.raw_delta_1,
                CHAIN,
            ),
        )


async def send_telegram_report(
    snapshot: PositionSnapshot,
    summary: FeeSummary,
    title: str,
):
    fee_last_hour_text = format_usd(summary.fee_last_hour)
    fee_last_day_text = format_usd(summary.fee_last_day)
    fee_0_last_hour_text = format_token_amount(summary.raw_delta_0, summary.symbol_0)
    fee_1_last_hour_text = format_token_amount(summary.raw_delta_1, summary.symbol_1)
    unclaimed_text = format_usd(snapshot.total_unclaimed_usd_value)
    valuation_warning = ""
    if snapshot.valuation_source.startswith("unsupported"):
        valuation_warning = "\n\n<b>Warning:</b>\nUSD valuation unavailable for this pool."

    message = (
        f"<b>{title}</b>\n\n"
        f"<b>Fees last hour:</b>\n"
        f"{fee_0_last_hour_text} {summary.symbol_0}\n"
        f"{fee_1_last_hour_text} {summary.symbol_1}\n"
        f"--------------------\n"
        f"{fee_last_hour_text} USD\n\n"
        f"<b>Fees last 24 hours:</b>\n"
        f"{fee_last_day_text} USD\n\n"
        f"<b>Total value of unclaimed fees:</b>\n"
        f"{unclaimed_text} USD"
        f"{valuation_warning}"
    )

    await send_telegram_text(message)

    fig, ax = plt.subplots()
    ax.plot(
        summary.dates,
        [float(price) for price in summary.prices],
        marker="o",
        linewidth=1,
    )
    ax.xaxis.set_major_formatter(DateFormatter("%H"))
    plt.axhline(y=float(snapshot.price_lower), color="r", linestyle="--")
    plt.axhline(y=float(snapshot.price_upper), color="g", linestyle="--")
    plt.title(title)
    price_image = create_image_buffer(fig)
    await send_telegram_image(price_image)
    price_image.close()

    fig, ax = plt.subplots()
    ax.bar(
        np.arange(1, len(summary.hourly_fee_values) + 1),
        [float(value) for value in summary.hourly_fee_values],
    )
    plt.title("Fees earned last 24 hours")
    ax.axes.xaxis.set_ticks([])
    fee_image = create_image_buffer(fig)
    await send_telegram_image(fee_image)
    fee_image.close()


# ============================================================
# MAIN POSITION READER
# ============================================================

async def detect_position_version(
    token_id: int,
    version_hint: str | None = None,
) -> str:
    if version_hint not in {None, "v3", "v4"}:
        raise ValueError(f"Unsupported position version for {token_id}: {version_hint}")

    try:
        if version_hint in {None, "v4"} and POSITION_MANAGER is not None:
            await POSITION_MANAGER.functions.getPoolAndPositionInfo(token_id).call()
            return "v4"
    except Exception:
        pass

    try:
        if version_hint in {None, "v3"}:
            await V3_POSITION_MANAGER.functions.positions(token_id).call()
            return "v3"
    except Exception:
        pass

    raise ValueError(f"Could not determine whether position {token_id} is V3 or V4")


def compute_v3_fee_growth_inside(
    current_tick: int,
    tick_lower: int,
    tick_upper: int,
    fee_growth_global: int,
    lower_fee_outside: int,
    upper_fee_outside: int,
) -> int:
    if current_tick >= tick_lower:
        fee_growth_below = lower_fee_outside
    else:
        fee_growth_below = fee_growth_global - lower_fee_outside

    if current_tick < tick_upper:
        fee_growth_above = upper_fee_outside
    else:
        fee_growth_above = fee_growth_global - upper_fee_outside

    return fee_growth_global - fee_growth_below - fee_growth_above


async def read_v3_position(token_id: int, eth_usd: Decimal) -> PositionSnapshot:
    position_info = await V3_POSITION_MANAGER.functions.positions(token_id).call()
    _, _, token0, token1, fee, tick_lower, tick_upper, liquidity, fee_growth_inside0_last_x128, fee_growth_inside1_last_x128, _, _ = position_info

    factory_address = await V3_POSITION_MANAGER.functions.factory().call()
    v3_factory = w3.eth.contract(
        address=normalize_address(factory_address),
        abi=V3_FACTORY_ABI,
    )
    pool_address = await v3_factory.functions.getPool(token0, token1, fee).call()
    pool_contract = w3.eth.contract(address=normalize_address(pool_address), abi=V3_POOL_ABI)

    sqrt_price_x96, current_tick, _, _, _, _, _ = await pool_contract.functions.slot0().call()
    fee_growth_global0_x128 = await pool_contract.functions.feeGrowthGlobal0X128().call()
    fee_growth_global1_x128 = await pool_contract.functions.feeGrowthGlobal1X128().call()

    lower_tick_info = await pool_contract.functions.ticks(tick_lower).call()
    upper_tick_info = await pool_contract.functions.ticks(tick_upper).call()

    lower_fee_outside_0 = lower_tick_info[2]
    upper_fee_outside_0 = upper_tick_info[2]
    lower_fee_outside_1 = lower_tick_info[3]
    upper_fee_outside_1 = upper_tick_info[3]

    fee_growth_inside0_current = compute_v3_fee_growth_inside(
        current_tick=current_tick,
        tick_lower=tick_lower,
        tick_upper=tick_upper,
        fee_growth_global=fee_growth_global0_x128,
        lower_fee_outside=lower_fee_outside_0,
        upper_fee_outside=upper_fee_outside_0,
    )
    fee_growth_inside1_current = compute_v3_fee_growth_inside(
        current_tick=current_tick,
        tick_lower=tick_lower,
        tick_upper=tick_upper,
        fee_growth_global=fee_growth_global1_x128,
        lower_fee_outside=lower_fee_outside_1,
        upper_fee_outside=upper_fee_outside_1,
    )

    unclaimed_fee0 = compute_unclaimed_fees(
        liquidity=liquidity,
        fee_growth_inside_current=fee_growth_inside0_current,
        fee_growth_inside_last=fee_growth_inside0_last_x128,
    )
    unclaimed_fee1 = compute_unclaimed_fees(
        liquidity=liquidity,
        fee_growth_inside_current=fee_growth_inside1_current,
        fee_growth_inside_last=fee_growth_inside1_last_x128,
    )

    symbol0_task = asyncio.create_task(get_symbol(token0))
    symbol1_task = asyncio.create_task(get_symbol(token1))
    decimals0_task = asyncio.create_task(get_decimals(token0))
    decimals1_task = asyncio.create_task(get_decimals(token1))

    symbol0 = await symbol0_task
    symbol1 = await symbol1_task
    decimals0 = await decimals0_task
    decimals1 = await decimals1_task

    token1_per_token0 = sqrt_price_x96_to_token1_per_token0(
        sqrt_price_x96,
        decimals0,
        decimals1,
    )
    price_lower = tick_to_token1_per_token0(tick_lower, decimals0, decimals1)
    price_upper = tick_to_token1_per_token0(tick_upper, decimals0, decimals1)
    in_range = tick_lower <= current_tick <= tick_upper

    fee0_adj = Decimal(unclaimed_fee0) / Decimal(10**decimals0)
    fee1_adj = Decimal(unclaimed_fee1) / Decimal(10**decimals1)

    token0_usd, token1_usd, valuation_source = get_token_usd_prices(
        token0=token0,
        token1=token1,
        token1_per_token0=token1_per_token0,
        eth_usd=eth_usd,
    )

    unclaimed_fee0_usd = fee0_adj * token0_usd
    unclaimed_fee1_usd = fee1_adj * token1_usd
    total_unclaimed_usd_value = unclaimed_fee0_usd + unclaimed_fee1_usd

    if valuation_source.startswith("unsupported"):
        print(
            f"Position {token_id}: unsupported USD valuation for "
            f"{symbol0}/{symbol1}"
        )

    return PositionSnapshot(
        pos_id=token_id,
        token_0=token0,
        token_1=token1,
        symbol_0=symbol0,
        symbol_1=symbol1,
        fee_0=fee0_adj,
        fee_1=fee1_adj,
        quote_0=token0_usd,
        quote_1=token1_usd,
        price_lower=price_lower,
        price_upper=price_upper,
        current_price=token1_per_token0,
        fee=fee,
        liquidity=liquidity,
        total_unclaimed_usd_value=total_unclaimed_usd_value,
        in_range=int(in_range),
        valuation_source=valuation_source,
    )


async def read_v4_position(token_id: int, eth_usd: Decimal) -> PositionSnapshot:
    pool_key, position_info = (
        await POSITION_MANAGER.functions.getPoolAndPositionInfo(token_id).call()
    )

    token0, token1, fee, tick_spacing, hook_address = pool_key

    tick_lower = decode_int24((position_info >> 8) & 0xFFFFFF)
    tick_upper = decode_int24((position_info >> 32) & 0xFFFFFF)

    encoded_key = encode(
        ["address", "address", "uint24", "int24", "address"],
        [token0, token1, fee, tick_spacing, hook_address],
    )
    pool_id_bytes32 = keccak(encoded_key)

    owner_address = normalize_address(CHAIN_CONFIG[CHAIN]["v4_position_manager"])
    salt = int(token_id).to_bytes(32, "big")

    sqrt_price_x96, current_tick, protocol_fee, lp_fee = (
        await STATE_VIEW.functions.getSlot0(pool_id_bytes32).call()
    )

    liquidity, fee_growth_inside0_last_x128, fee_growth_inside1_last_x128 = (
        await STATE_VIEW.functions.getPositionInfo(
            pool_id_bytes32,
            owner_address,
            tick_lower,
            tick_upper,
            salt,
        ).call()
    )

    fee_growth_inside0_current, fee_growth_inside1_current = (
        await STATE_VIEW.functions.getFeeGrowthInside(
            pool_id_bytes32,
            tick_lower,
            tick_upper,
        ).call()
    )

    unclaimed_fee0 = compute_unclaimed_fees(
        liquidity=liquidity,
        fee_growth_inside_current=fee_growth_inside0_current,
        fee_growth_inside_last=fee_growth_inside0_last_x128,
    )
    unclaimed_fee1 = compute_unclaimed_fees(
        liquidity=liquidity,
        fee_growth_inside_current=fee_growth_inside1_current,
        fee_growth_inside_last=fee_growth_inside1_last_x128,
    )

    symbol0_task = asyncio.create_task(get_symbol(token0))
    symbol1_task = asyncio.create_task(get_symbol(token1))
    decimals0_task = asyncio.create_task(get_decimals(token0))
    decimals1_task = asyncio.create_task(get_decimals(token1))

    symbol0 = await symbol0_task
    symbol1 = await symbol1_task
    decimals0 = await decimals0_task
    decimals1 = await decimals1_task

    token1_per_token0 = sqrt_price_x96_to_token1_per_token0(
        sqrt_price_x96,
        decimals0,
        decimals1,
    )
    price_lower = tick_to_token1_per_token0(tick_lower, decimals0, decimals1)
    price_upper = tick_to_token1_per_token0(tick_upper, decimals0, decimals1)
    in_range = tick_lower <= current_tick <= tick_upper

    fee0_adj = Decimal(unclaimed_fee0) / Decimal(10**decimals0)
    fee1_adj = Decimal(unclaimed_fee1) / Decimal(10**decimals1)

    token0_usd, token1_usd, valuation_source = get_token_usd_prices(
        token0=token0,
        token1=token1,
        token1_per_token0=token1_per_token0,
        eth_usd=eth_usd,
    )

    unclaimed_fee0_usd = fee0_adj * token0_usd
    unclaimed_fee1_usd = fee1_adj * token1_usd
    total_unclaimed_usd_value = unclaimed_fee0_usd + unclaimed_fee1_usd

    if valuation_source.startswith("unsupported"):
        print(
            f"Position {token_id}: unsupported USD valuation for "
            f"{symbol0}/{symbol1}"
        )

    return PositionSnapshot(
        pos_id=token_id,
        token_0=token0,
        token_1=token1,
        symbol_0=symbol0,
        symbol_1=symbol1,
        fee_0=fee0_adj,
        fee_1=fee1_adj,
        quote_0=token0_usd,
        quote_1=token1_usd,
        price_lower=price_lower,
        price_upper=price_upper,
        current_price=token1_per_token0,
        fee=fee,
        liquidity=liquidity,
        total_unclaimed_usd_value=total_unclaimed_usd_value,
        in_range=int(in_range),
        valuation_source=valuation_source,
    )


async def read_position(
    token_id: int,
    eth_usd: Decimal,
    version_hint: str | None = None,
) -> PositionSnapshot:
    version = await detect_position_version(token_id, version_hint)
    if version == "v4":
        return await read_v4_position(token_id, eth_usd)
    if version == "v3":
        return await read_v3_position(token_id, eth_usd)

    raise ValueError(f"Unsupported position version for {token_id}: {version}")


async def read_position_limited(
    token_id: int,
    eth_usd: Decimal,
    semaphore: asyncio.Semaphore,
    version_hint: str | None = None,
) -> PositionSnapshot:
    async with semaphore:
        return await read_position(token_id, eth_usd, version_hint)


async def main():
    if args.position_id is None:
        position_ids = read_position_ids()
    else:
        version = args.version
        if version is None:
            try:
                configured_positions = read_position_ids()
            except FileNotFoundError:
                configured_positions = []
            configured_versions = {
                file_version
                for file_version, pos_id in configured_positions
                if pos_id == args.position_id
            }
            if len(configured_versions) == 1:
                version = configured_versions.pop()
        position_ids = [(version, args.position_id)]
    if not position_ids:
        print("No positions found.")
        return

    eth_usd = await get_eth_usd()
    semaphore = asyncio.Semaphore(POSITION_READ_CONCURRENCY)
    position_tasks = [
        read_position_limited(pos_id, eth_usd, semaphore, version)
        for version, pos_id in position_ids
    ]
    position_results = await asyncio.gather(
        *position_tasks,
        return_exceptions=True,
    )

    connection, cursor = db_connect()
    try:
        for (version, pos_id), result in zip(position_ids, position_results):
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            position_label = f"{version}:" if version else ""

            if isinstance(result, Exception):
                print(f"##### {timestamp} #####")
                print(f"Position {position_label}{pos_id}: {result}")
                continue

            snapshot = result
            title = (
                f"#{snapshot.pos_id} - {snapshot.symbol_0} - "
                f"{snapshot.symbol_1} {format_fee_percent(snapshot.fee)} %"
            )

            try:
                insert_snapshot(cursor, snapshot, timestamp)
                summary = calculate_fee_summary(cursor, snapshot)
                if summary is not None:
                    insert_fee_deltas(
                        cursor,
                        summary,
                        snapshot.pos_id,
                        timestamp,
                    )
                connection.commit()
            except Exception as error:
                connection.rollback()
                print(f"##### {timestamp} #####")
                print(f"Position {snapshot.pos_id}: {error}")
                continue

            if snapshot.liquidity == 0 and remove_position_id(pos_id, version):
                print(
                    f"Removed {position_label}{pos_id} from positions "
                    "(liquidity is zero)."
                )

            if summary is None:
                continue

            try:
                await send_telegram_report(snapshot, summary, title)
            except Exception as error:
                print(f"##### {timestamp} #####")
                print(f"Position {snapshot.pos_id}: {error}")
    finally:
        connection.close()


if __name__ == "__main__":
    asyncio.run(main())
