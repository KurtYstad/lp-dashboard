#!/usr/bin/env python
# -*- coding: utf-8 -*-

import io
import os
import argparse
import sys
import datetime
import re
import telegram_send
import mariadb
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
import numpy as np
import asyncio
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, getcontext
import dotenv
from typing import Any
from web3 import AsyncHTTPProvider, AsyncWeb3


# ============================================================
# CONFIG
# ============================================================

# Load environment variables from .env
dotenv.load_dotenv()

parser = argparse.ArgumentParser(description="Aerodrome fee monitor")
parser.add_argument(
    "position_id",
    nargs="?",
    type=int,
    help="Process only this position ID.",
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
CHAIN = os.getenv("CHAIN")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
POSITION_READ_CONCURRENCY = int(os.getenv("POSITION_READ_CONCURRENCY", "4"))

REQUIRED_ENV = [
    "CHAIN",
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
if not all(os.getenv(name) for name in REQUIRED_ENV):
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    print(f"Missing required environment variables: {', '.join(missing)}")
    sys.exit(1)

if CHAIN != "base":
    print("Unsupported chain for Aerodrome positions: set CHAIN=base in the environment.")
    sys.exit(1)

DB_PORT = int(DB_PORT_STR)

BASE_CONFIG = {
    "rpc": f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
    "aero_factory": "0x9cb220581b91b0c83223012fe69688b92ad07a76",
    "eth_usd_feed": "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",
    "weth": "0x4200000000000000000000000000000000000006",
    "usd_stables": {
        "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "USDT": "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
        "DAI": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    },
}
AERO_STAKER_ADDRESS = os.getenv("AERO_STAKER_ADDRESS")
AERO_REFERENCE_POOL = "0x2A430bEbD2082d8e05D56d64DEDE8944279bbC24"
AERO_REFERENCE_POOL_KEY = (
    "0x4200000000000000000000000000000000000006",
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
    100,
)
AERO_TOKEN = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
AERO_USD_POOL = "0x6cDcb1C4A4D1C3C6d054b27AC5B77e89eAFb971d"


# ============================================================
# ABIS
# ============================================================

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

AERO_POSITION_MANAGER_ABI = [
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "positions",
        "outputs": [
            {"internalType": "uint96", "name": "nonce", "type": "uint96"},
            {"internalType": "address", "name": "operator", "type": "address"},
            {"internalType": "address", "name": "token0", "type": "address"},
            {"internalType": "address", "name": "token1", "type": "address"},
            {"internalType": "int24", "name": "tickSpacing", "type": "int24"},
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
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "ownerOf",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

AERO_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "int24", "name": "tickSpacing", "type": "int24"},
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "positionManager",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

AERO_GAUGE_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "account", "type": "address"},
            {"internalType": "uint256[]", "name": "tokenIds", "type": "uint256[]"},
        ],
        "name": "earned",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

AERO_V2_PAIR_ABI = [
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "reserve1", "type": "uint112"},
            {"internalType": "uint32", "name": "blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

AERO_FEE_GROWTH_ABI = [
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
            {"internalType": "int128", "name": "stakedLiquidityNet", "type": "int128"},
            {"internalType": "uint256", "name": "feeGrowthOutside0X128", "type": "uint256"},
            {"internalType": "uint256", "name": "feeGrowthOutside1X128", "type": "uint256"},
            {"internalType": "uint256", "name": "secondsPerLiquidityOutsideX128", "type": "uint256"},
            {"internalType": "int56", "name": "tickCumulativeOutside", "type": "int56"},
            {"internalType": "uint160", "name": "secondsPerLiquidityOutsideX128", "type": "uint160"},
            {"internalType": "uint32", "name": "secondsOutside", "type": "uint32"},
            {"internalType": "bool", "name": "initialized", "type": "bool"},
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
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    *AERO_FEE_GROWTH_ABI,
    {
        "inputs": [],
        "name": "fee",
        "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "gauge",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "tickSpacing",
        "outputs": [{"internalType": "int24", "name": "", "type": "int24"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "nft",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
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


w3 = AsyncWeb3(RetryAsyncHTTPProvider(BASE_CONFIG["rpc"]))
AERO_FACTORY = w3.eth.contract(
    address=normalize_address(BASE_CONFIG["aero_factory"]),
    abi=AERO_FACTORY_ABI,
)
AERO_POSITION_MANAGER = None
ETH_USD_FEED = w3.eth.contract(
    address=normalize_address(BASE_CONFIG["eth_usd_feed"]),
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
    reward_token: str | None = None
    reward_symbol: str | None = None
    reward_amount: Decimal = Decimal("0")
    reward_quote: Decimal = Decimal("0")
    reward_warning: str | None = None


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
    reward_token: str | None
    reward_last_hour: Decimal
    reward_hour_usd: Decimal
    reward_symbol: str | None
    reward_warning: str | None


def sub_in_256(x: int, y: int) -> int:
    return (x - y) % Q256


def compute_unclaimed_fees(
    liquidity: int,
    fee_growth_inside_current: int,
    fee_growth_inside_last: int,
) -> int:
    delta = sub_in_256(fee_growth_inside_current, fee_growth_inside_last)

    return liquidity * delta // Q128


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
    stablecoins = BASE_CONFIG["usd_stables"].values()

    return token.lower() in {stable.lower() for stable in stablecoins}


def get_token_usd_prices(
    token0: str,
    token1: str,
    token1_per_token0: Decimal,
    eth_usd: Decimal,
) -> tuple[Decimal, Decimal, str]:
    weth = normalize_address(BASE_CONFIG["weth"])

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


async def get_reward_usd_price(
    reward_token: str,
    token0: str,
    token1: str,
    token1_per_token0: Decimal,
    eth_usd: Decimal,
) -> tuple[Decimal, str]:
    reward_token = normalize_address(reward_token)
    if reward_token.lower() == token0.lower():
        quote0, _, source = get_token_usd_prices(
            token0,
            token1,
            token1_per_token0,
            eth_usd,
        )
        return quote0, source
    if reward_token.lower() == token1.lower():
        _, quote1, source = get_token_usd_prices(
            token0,
            token1,
            token1_per_token0,
            eth_usd,
        )
        return quote1, source

    if reward_token.lower() == AERO_TOKEN.lower():
        pair = w3.eth.contract(
            address=normalize_address(AERO_USD_POOL),
            abi=AERO_V2_PAIR_ABI,
        )
        pair_token0, pair_token1, reserves = await asyncio.gather(
            pair.functions.token0().call(),
            pair.functions.token1().call(),
            pair.functions.getReserves().call(),
        )
        usdc = normalize_address(BASE_CONFIG["usd_stables"]["USDC"])
        if {
            pair_token0.lower(),
            pair_token1.lower(),
        } != {reward_token.lower(), usdc.lower()}:
            raise RuntimeError(
                f"Aerodrome reward price pool {AERO_USD_POOL} is not an AERO/USDC pair"
            )

        decimals0, decimals1 = await asyncio.gather(
            get_decimals(pair_token0),
            get_decimals(pair_token1),
        )
        reserve0 = Decimal(reserves[0]) / Decimal(10**decimals0)
        reserve1 = Decimal(reserves[1]) / Decimal(10**decimals1)
        if reserve0 <= 0 or reserve1 <= 0:
            raise RuntimeError(f"Aerodrome reward price pool {AERO_USD_POOL} is empty")

        if pair_token0.lower() == reward_token.lower():
            return reserve1 / reserve0, "aerodrome_aero_usdc_pool"
        return reserve0 / reserve1, "aerodrome_aero_usdc_pool"

    return Decimal(0), "unsupported_reward_token_usd_price"


async def get_eth_usd() -> Decimal:
    feed_address = BASE_CONFIG["eth_usd_feed"]
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


def quote_table_name(table_name: str) -> str:
    parts = table_name.split(".")
    if any(not re.fullmatch(r"[A-Za-z0-9_]+", part) for part in parts):
        raise ValueError(f"Invalid database table name: {table_name}")
    return ".".join(f"`{part}`" for part in parts)


def ensure_snapshot_reward_columns(cursor):
    table = quote_table_name(TABLE_SNAPSHOT)
    cursor.execute(f"SHOW COLUMNS FROM {table}")
    existing_columns = {row[0] for row in cursor}
    reward_columns = {
        "reward_token": "VARCHAR(42) NULL",
        "reward_symbol": "VARCHAR(32) NULL",
        "reward_amount": "DECIMAL(65, 30) NOT NULL DEFAULT 0",
        "reward_quote": "DECIMAL(65, 30) NOT NULL DEFAULT 0",
    }
    for column, definition in reward_columns.items():
        if column not in existing_columns:
            cursor.execute(
                f"ALTER TABLE {table} ADD COLUMN `{column}` {definition}"
            )


def fetch_snapshot_history(cursor, pos_id):
    cursor.execute(
        f"""
        SELECT date, token_0, token_1, symbol_0, symbol_1,
               fee_0, fee_1, current_price,
               reward_token, reward_symbol, reward_amount, reward_quote
        FROM {TABLE_SNAPSHOT}
        WHERE id = ? AND date >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        ORDER BY date ASC, id ASC
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


def read_position_ids(filename="positions") -> list[int]:
    position_ids = []
    with open(filename, "r", encoding="utf-8") as file:
        for line_no, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                position_ids.append(int(line))
            except ValueError:
                print(f"Skipping invalid position id in {filename}:{line_no}: {line}")

    return position_ids


def remove_position_id(position_id: int, filename: str = "positions") -> bool:
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

        try:
            matches_id = int(entry) == position_id
        except ValueError:
            matches_id = False

        if matches_id:
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
        f"current_price, in_range, reward_token, reward_symbol, "
        f"reward_amount, reward_quote) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            snapshot.reward_token,
            snapshot.reward_symbol,
            snapshot.reward_amount,
            snapshot.reward_quote,
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
    reward_deltas = []
    reward_hour_values = []
    for previous, current in zip(rows, rows[1:]):
        delta_0 = safe_decimal(current[5]) - safe_decimal(previous[5])
        delta_1 = safe_decimal(current[6]) - safe_decimal(previous[6])
        reward_delta = max(
            Decimal(0),
            safe_decimal(current[10]) - safe_decimal(previous[10]),
        )
        reward_usd = reward_delta * safe_decimal(current[11])
        reward_deltas.append(reward_delta)
        reward_hour_values.append(reward_usd)
        hourly_fee_values.append(
            delta_0 * snapshot.quote_0
            + delta_1 * snapshot.quote_1
            + reward_usd
        )

    fee_last_hour = hourly_fee_values[-1]
    fee_last_day = (
        (fees_0[-1] - fees_0[0]) * snapshot.quote_0
        + (fees_1[-1] - fees_1[0]) * snapshot.quote_1
        + sum(reward_hour_values, Decimal(0))
    )

    raw_delta_0 = fees_0[-1] - fees_0[-2]
    raw_delta_1 = fees_1[-1] - fees_1[-2]
    reward_last_hour = reward_deltas[-1] if reward_deltas else Decimal(0)
    reward_hour_usd = reward_hour_values[-1] if reward_hour_values else Decimal(0)
    unsupported_valuation = snapshot.valuation_source.startswith("unsupported")

    if fee_last_day <= 0 and not unsupported_valuation:
        return None

    if (
        unsupported_valuation
        and raw_delta_0 <= 0
        and raw_delta_1 <= 0
        and fee_last_day <= 0
    ):
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
        reward_token=snapshot.reward_token,
        reward_last_hour=reward_last_hour,
        reward_hour_usd=reward_hour_usd,
        reward_symbol=snapshot.reward_symbol,
        reward_warning=snapshot.reward_warning,
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

    if (
        summary.reward_last_hour > 0
        and summary.reward_token is not None
        and summary.reward_symbol is not None
    ):
        cursor.execute(
            f"INSERT INTO {TABLE_TOTAL} (pos_id, timestamp, token, symbol, "
            f"fee, chain) VALUES (?, ?, ?, ?, ?, ?)",
            (
                pos_id,
                timestamp,
                summary.reward_token,
                summary.reward_symbol,
                summary.reward_last_hour,
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
    if summary.reward_symbol is not None:
        reward_hour_text = format_token_amount(
            summary.reward_last_hour,
            summary.reward_symbol,
        )
        reward_hour_usd_text = format_usd(summary.reward_hour_usd)
        reward_report = (
            f"{reward_hour_text} {summary.reward_symbol} "
            f"({reward_hour_usd_text} USD)"
        )
    else:
        reward_report = ""
    if summary.reward_warning:
        valuation_warning += f"\n\n<b>Reward tracking warning:</b>\n{summary.reward_warning}"

    message = (
        f"<b>{title}</b>\n\n"
        f"<b>LP fees and rewards last hour:</b>\n"
        f"{fee_0_last_hour_text} {summary.symbol_0}\n"
        f"{fee_1_last_hour_text} {summary.symbol_1}\n"
        f"{reward_report}\n"
        f"--------------------\n"
        f"{fee_last_hour_text} USD\n\n"
        f"<b>LP fees and rewards last 24 hours:</b>\n"
        f"{fee_last_day_text} USD\n\n"
        f"<b>Total value of unclaimed fees and rewards:</b>\n"
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
    plt.title("LP fees and rewards last 24 hours")
    ax.axes.xaxis.set_ticks([])
    fee_image = create_image_buffer(fig)
    await send_telegram_image(fee_image)
    fee_image.close()


# ============================================================
# MAIN POSITION READER
# ============================================================

async def get_aero_position_manager():
    global AERO_POSITION_MANAGER
    manager_address = await AERO_FACTORY.functions.positionManager().call()
    if manager_address.lower() == ZERO_ADDRESS.lower():
        raise RuntimeError("Aerodrome factory returned a zero position manager")

    if (
        AERO_POSITION_MANAGER is None
        or AERO_POSITION_MANAGER.address.lower() != manager_address.lower()
    ):
        AERO_POSITION_MANAGER = w3.eth.contract(
            address=normalize_address(manager_address),
            abi=AERO_POSITION_MANAGER_ABI,
        )

    return AERO_POSITION_MANAGER


def compute_fee_growth_inside(
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


async def read_aero_position(token_id: int, eth_usd: Decimal) -> PositionSnapshot:
    position_manager = await get_aero_position_manager()
    position_info = await position_manager.functions.positions(token_id).call()
    (
        _,
        _,
        token0,
        token1,
        tick_spacing,
        tick_lower,
        tick_upper,
        liquidity,
        fee_growth_inside0_last_x128,
        fee_growth_inside1_last_x128,
        tokens_owed0,
        tokens_owed1,
    ) = position_info

    pool_key = (token0.lower(), token1.lower(), tick_spacing)
    if pool_key == AERO_REFERENCE_POOL_KEY:
        pool_address = AERO_REFERENCE_POOL
    else:
        factory_address = await position_manager.functions.factory().call()
        aero_factory = w3.eth.contract(
            address=normalize_address(factory_address),
            abi=AERO_FACTORY_ABI,
        )
        pool_address = await aero_factory.functions.getPool(
            token0,
            token1,
            tick_spacing,
        ).call()
        if pool_address.lower() == ZERO_ADDRESS.lower():
            raise ValueError(
                f"Could not find Aerodrome pool for position {token_id}"
            )

    pool_contract = w3.eth.contract(
        address=normalize_address(pool_address),
        abi=AERO_POOL_ABI,
    )
    pool_token0, pool_token1, pool_tick_spacing = await asyncio.gather(
        pool_contract.functions.token0().call(),
        pool_contract.functions.token1().call(),
        pool_contract.functions.tickSpacing().call(),
    )
    pool_position_manager = await pool_contract.functions.nft().call()
    if (
        pool_token0.lower() != token0.lower()
        or pool_token1.lower() != token1.lower()
        or pool_tick_spacing != tick_spacing
        or pool_position_manager.lower() != position_manager.address.lower()
    ):
        raise ValueError(
            f"Pool metadata does not match Aerodrome position {token_id}"
        )

    (
        (sqrt_price_x96, current_tick, _, _, _, _),
        fee,
        gauge_address,
        owner_address,
    ) = await asyncio.gather(
        pool_contract.functions.slot0().call(),
        pool_contract.functions.fee().call(),
        pool_contract.functions.gauge().call(),
        position_manager.functions.ownerOf(token_id).call(),
    )

    is_staked = gauge_address.lower() != ZERO_ADDRESS.lower() and (
        owner_address.lower() == gauge_address.lower()
    )
    symbol0_task = asyncio.create_task(get_symbol(token0))
    symbol1_task = asyncio.create_task(get_symbol(token1))
    decimals0_task = asyncio.create_task(get_decimals(token0))
    decimals1_task = asyncio.create_task(get_decimals(token1))

    reward_token = None
    reward_symbol = None
    reward_amount = Decimal(0)
    reward_warnings = []

    if gauge_address.lower() != ZERO_ADDRESS.lower():
        gauge_contract = w3.eth.contract(
            address=normalize_address(gauge_address),
            abi=AERO_GAUGE_ABI,
        )
        reward_token = normalize_address(AERO_TOKEN)
        reward_symbol, reward_decimals = await asyncio.gather(
            get_symbol(reward_token),
            get_decimals(reward_token),
        )
        if is_staked:
            if AERO_STAKER_ADDRESS:
                staker = normalize_address(AERO_STAKER_ADDRESS)
                raw_reward = await gauge_contract.functions.earned(
                    staker,
                    [token_id],
                ).call()
                reward_amount = Decimal(raw_reward) / Decimal(
                    10**reward_decimals
                )
            else:
                reward_warnings.append(
                    f"Position {token_id} is staked. Set "
                    "AERO_STAKER_ADDRESS to read its claimable rewards."
                )

    symbol0 = await symbol0_task
    symbol1 = await symbol1_task
    decimals0 = await decimals0_task
    decimals1 = await decimals1_task

    if is_staked:
        unclaimed_fee0 = 0
        unclaimed_fee1 = 0
    else:
        (
            fee_growth_global0_x128,
            fee_growth_global1_x128,
            lower_tick_info,
            upper_tick_info,
        ) = await asyncio.gather(
            pool_contract.functions.feeGrowthGlobal0X128().call(),
            pool_contract.functions.feeGrowthGlobal1X128().call(),
            pool_contract.functions.ticks(tick_lower).call(),
            pool_contract.functions.ticks(tick_upper).call(),
        )
        fee_growth_inside0_current = compute_fee_growth_inside(
            current_tick=current_tick,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            fee_growth_global=fee_growth_global0_x128,
            lower_fee_outside=lower_tick_info[3],
            upper_fee_outside=upper_tick_info[3],
        )
        fee_growth_inside1_current = compute_fee_growth_inside(
            current_tick=current_tick,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            fee_growth_global=fee_growth_global1_x128,
            lower_fee_outside=lower_tick_info[4],
            upper_fee_outside=upper_tick_info[4],
        )
        unclaimed_fee0 = tokens_owed0 + compute_unclaimed_fees(
            liquidity,
            fee_growth_inside0_current,
            fee_growth_inside0_last_x128,
        )
        unclaimed_fee1 = tokens_owed1 + compute_unclaimed_fees(
            liquidity,
            fee_growth_inside1_current,
            fee_growth_inside1_last_x128,
        )

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
    reward_quote = Decimal(0)
    if reward_token is not None:
        reward_quote, reward_valuation_source = await get_reward_usd_price(
            reward_token=reward_token,
            token0=token0,
            token1=token1,
            token1_per_token0=token1_per_token0,
            eth_usd=eth_usd,
        )
        if reward_valuation_source.startswith("unsupported"):
            reward_warnings.append(
                f"USD valuation unavailable for reward token {reward_symbol}."
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
        total_unclaimed_usd_value=(
            fee0_adj * token0_usd
            + fee1_adj * token1_usd
            + reward_amount * reward_quote
        ),
        in_range=int(in_range),
        valuation_source=valuation_source,
        reward_token=reward_token,
        reward_symbol=reward_symbol,
        reward_amount=reward_amount,
        reward_quote=reward_quote,
        reward_warning=" ".join(reward_warnings) or None,
    )


async def read_position_limited(
    token_id: int,
    eth_usd: Decimal,
    semaphore: asyncio.Semaphore,
) -> PositionSnapshot:
    async with semaphore:
        return await read_aero_position(token_id, eth_usd)


async def main():
    position_ids = (
        [args.position_id]
        if args.position_id is not None
        else read_position_ids()
    )
    if not position_ids:
        print("No positions found.")
        return

    eth_usd = await get_eth_usd()
    semaphore = asyncio.Semaphore(POSITION_READ_CONCURRENCY)
    position_tasks = [
        read_position_limited(pos_id, eth_usd, semaphore)
        for pos_id in position_ids
    ]
    position_results = await asyncio.gather(
        *position_tasks,
        return_exceptions=True,
    )

    connection, cursor = db_connect()
    try:
        ensure_snapshot_reward_columns(cursor)
        connection.commit()

        for pos_id, result in zip(position_ids, position_results):
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if isinstance(result, Exception):
                print(f"##### {timestamp} #####")
                print(f"Position {pos_id}: {result}")
                continue

            snapshot = result
            if snapshot.reward_warning:
                print(snapshot.reward_warning)

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

            if snapshot.liquidity == 0 and remove_position_id(pos_id):
                print(f"Removed {pos_id} from positions (liquidity is zero).")

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
