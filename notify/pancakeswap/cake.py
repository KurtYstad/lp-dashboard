#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import json
import argparse
import datetime
import asyncio
import io
from decimal import Decimal, ROUND_HALF_UP, getcontext
from web3 import Web3
import dotenv
import mariadb
import telegram_send
import matplotlib.pyplot as plt
import numpy as np

# ============================================================
# CONFIG
# ============================================================

dotenv.load_dotenv()

parser = argparse.ArgumentParser(description="PancakeSwap fee monitor")
parser.add_argument(
    "position_id",
    nargs="?",
    type=int,
    help="Process only this position ID.",
)
args = parser.parse_args()

RPC_URL = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")

# Environment variables for chain config
CHAIN = os.getenv("CHAIN").lower()

# Database configuration
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT_STR = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
TABLE_SNAPSHOT = os.getenv("TABLE_SNAPSHOT")
TABLE_TOTAL = os.getenv("TABLE_TOTAL")
TELEGRAM_CONF = os.getenv("TELEGRAM_CONF", "./telegram.conf")
DB_PORT = int(DB_PORT_STR)

# USD stable coins on BSC
USD_STABLES_BSC = {
    "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
    "USDT": "0x55d398326f99059fF775485246999027B3197955",
    "BUSD": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
    "DAI": "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3",
}

# Common tokens on BSC
WBNB = "0xbb4CdB9CBd36B01bD1cbaEBF2De08d9173bc095c"
CAKE = "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82"

# Chainlink BNB/USD on BSC
BNB_USD_FEED = "0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE"

getcontext().prec = 80

Q128 = Decimal(2) ** 128

# ============================================================
# ABIS
# ============================================================

POSITION_ABI = [
    {
        "name": "positions",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"type": "uint256"}],
        "outputs": [
            {"type": "uint96"},
            {"type": "address"},
            {"type": "address"},
            {"type": "address"},
            {"type": "uint24"},
            {"type": "int24"},
            {"type": "int24"},
            {"type": "uint128"},
            {"type": "uint256"},
            {"type": "uint256"},
            {"type": "uint128"},
            {"type": "uint128"},
        ],
    },
    {
        "name": "collect",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {
                "components": [
                    {"name": "tokenId", "type": "uint256"},
                    {"name": "recipient", "type": "address"},
                    {"name": "amount0Max", "type": "uint128"},
                    {"name": "amount1Max", "type": "uint128"}
                ],
                "name": "params",
                "type": "tuple"
            }
        ],
        "outputs": [
            {"name": "amount0", "type": "uint256"},
            {"name": "amount1", "type": "uint256"}
        ],
    }
]

POOL_ABI = [
    {
        "name": "slot0",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [
            {"type": "uint160"},
            {"type": "int24"},
            {"type": "uint16"},
            {"type": "uint16"},
            {"type": "uint16"},
            {"type": "uint8"},
            {"type": "bool"},
        ],
    },
    {
        "name": "feeGrowthGlobal0X128",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"type": "uint256"}],
    },
    {
        "name": "feeGrowthGlobal1X128",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"type": "uint256"}],
    },
    {
        "name": "ticks",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"type": "int24"}],
        "outputs": [
            {"type": "uint128"},
            {"type": "int128"},
            {"type": "uint256"},
            {"type": "uint256"},
            {"type": "int56"},
            {"type": "uint160"},
            {"type": "uint32"},
            {"type": "bool"},
        ],
    },
]

MASTER_ABI = [
    {
        "name": "userPositionInfos",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"type": "uint256"}],
        "outputs": [
            {"type": "uint128"},
            {"type": "uint128"},
            {"type": "int24"},
            {"type": "int24"},
            {"type": "uint256"},
            {"type": "uint256"},
            {"type": "address"},
            {"type": "uint256"},
            {"type": "uint256"},
        ],
    },
    {
        "name": "collect",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "components": [
                    {"name": "tokenId", "type": "uint256"},
                    {"name": "recipient", "type": "address"},
                    {"name": "amount0Max", "type": "uint128"},
                    {"name": "amount1Max", "type": "uint128"}
                ],
                "name": "params",
                "type": "tuple"
            }
        ],
        "outputs": [
            {"name": "amount0", "type": "uint256"},
            {"name": "amount1", "type": "uint256"}
        ],
    },
    {
        "name": "pendingCake",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"type": "uint256"}],
        "outputs": [{"name": "reward", "type": "uint256"}],
    }
]

FACTORY_ABI = [
    {
        "name": "getPool",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"type": "address"},
            {"type": "address"},
            {"type": "uint24"},
        ],
        "outputs": [{"type": "address"}],
    }
]

ERC20_ABI = [
    {
        "name": "decimals",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"type": "uint8"}],
    },
    {
        "name": "symbol",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"type": "string"}],
    }
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
# HELPERS
# ============================================================

def safe_dec(x):
    return Decimal(x) if x is not None else Decimal(0)


def clamp(x):
    return max(Decimal(0), x)


def get_token_decimals(w3, token_address):
    """Dynamically fetch token decimals from ERC20 contract"""
    try:
        token = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        decimals = token.functions.decimals().call()
        return decimals
    except Exception as e:
        return 18


def get_token_symbol(w3, token_address):
    """Dynamically fetch token symbol from ERC20 contract"""
    try:
        token = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        symbol = token.functions.symbol().call()
        return symbol
    except Exception as e:
        return "UNKNOWN"


def get_bnb_usd_price(w3):
    """Fetch BNB/USD price from Chainlink"""
    try:
        chainlink = w3.eth.contract(address=Web3.to_checksum_address(BNB_USD_FEED), abi=CHAINLINK_ABI)
        data = chainlink.functions.latestRoundData().call()
        # data[1] is the price with 8 decimals
        bnb_usd = Decimal(data[1]) / Decimal(10**8)
        return bnb_usd
    except Exception as e:
        print(f"Error fetching BNB/USD from Chainlink: {e}")
        return Decimal("600")  # Fallback


def get_cake_usd_price(w3, pool_addr_cake_bnb, cake_decimals=18):
    """Fetch CAKE/USD price via CAKE/BNB pool and BNB/USD"""
    try:
        # Get CAKE/BNB price from pool
        pool = w3.eth.contract(address=pool_addr_cake_bnb, abi=POOL_ABI)
        
        slot0_data = w3.eth.call({
            "to": pool_addr_cake_bnb,
            "data": "0x3850c7bd"
        })
        sqrtPriceX96 = int.from_bytes(slot0_data[0:32], 'big')
        
        # Calculate BNB per CAKE
        bnb_per_cake = sqrt_price_x96_to_token1_per_token0(sqrtPriceX96, cake_decimals, 18)
        
        # Get BNB/USD price
        bnb_usd = get_bnb_usd_price(w3)
        
        # CAKE/USD = BNB per CAKE * BNB/USD
        cake_usd = bnb_per_cake * bnb_usd
        return cake_usd
    except Exception as e:
        print(f"Error calculating CAKE/USD: {e}")
        return Decimal("0")


def is_usd_stable(token: str) -> bool:
    stablecoins = USD_STABLES_BSC.values()
    return token.lower() in {stable.lower() for stable in stablecoins}


def get_token_usd_prices(
    token0: str,
    token1: str,
    token1_per_token0: Decimal,
    bnb_usd: Decimal,
) -> tuple[Decimal, Decimal, str]:
    """
    Calculate USD prices for token0 and token1.
    Token pair has a known exchange rate: token1_per_token0
    We need to anchor this to USD somehow.
    """
    wbnb = Web3.to_checksum_address(WBNB)
    
    token0_lower = token0.lower()
    token1_lower = token1.lower()
    
    token0_is_bnb = token0_lower == wbnb.lower()
    token1_is_bnb = token1_lower == wbnb.lower()
    token0_is_stable = is_usd_stable(token0)
    token1_is_stable = is_usd_stable(token1)

    # Case 1: token0 is WBNB
    if token0_is_bnb:
        token0_usd = bnb_usd
        token1_usd = bnb_usd / token1_per_token0
        return token0_usd, token1_usd, "pool_bnb_chainlink_usd"

    # Case 2: token1 is WBNB
    if token1_is_bnb:
        token0_usd = token1_per_token0 * bnb_usd
        token1_usd = bnb_usd
        return token0_usd, token1_usd, "pool_bnb_chainlink_usd"

    # Case 3: token0 is USD stable
    if token0_is_stable:
        token0_usd = Decimal(1)
        token1_usd = Decimal(1) / token1_per_token0
        return token0_usd, token1_usd, "pool_usd_stable_anchor"

    # Case 4: token1 is USD stable
    if token1_is_stable:
        token0_usd = token1_per_token0
        token1_usd = Decimal(1)
        return token0_usd, token1_usd, "pool_usd_stable_anchor"

    # No anchor found
    return Decimal(0), Decimal(0), "unsupported_no_anchor"


def fee_inside(global_fee, lower_tick_data, upper_tick_data, current_tick, tick_lower, tick_upper, token_index):
    """
    Calculate feeGrowthInside for a specific token.
    token_index: 0 for token0, 1 for token1
    """
    lower_fee = Decimal(lower_tick_data[2 + token_index])
    upper_fee = Decimal(upper_tick_data[2 + token_index])
    global_fee = Decimal(global_fee)

    if current_tick < tick_lower:
        return lower_fee
    elif current_tick >= tick_upper:
        return global_fee - upper_fee
    else:
        return global_fee - lower_fee - upper_fee


def sqrt_price_x96_to_token1_per_token0(
    sqrt_price_x96: int,
    decimals0: int,
    decimals1: int,
) -> Decimal:
    """Convert sqrt price X96 to token1 per token0"""
    raw_price = Decimal(sqrt_price_x96) ** 2 / Decimal(2**192)
    return raw_price * Decimal(10**decimals0) / Decimal(10**decimals1)


def tick_to_token1_per_token0(tick: int, decimals0: int, decimals1: int) -> Decimal:
    """Convert tick to token1 per token0 price"""
    raw_price = Decimal("1.0001") ** Decimal(tick)
    return raw_price * Decimal(10**decimals0) / Decimal(10**decimals1)


def format_usd(value):
    value = safe_dec(value)
    quantized = value.quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)
    return format(quantized, "f")


def format_token_amount(value, decimals=18):
    value = safe_dec(value)
    precision = Decimal("0." + "0" * decimals)
    quantized = value.quantize(precision, rounding=ROUND_HALF_UP)
    return format(quantized, "f")


# ============================================================
# DATABASE FUNCTIONS
# ============================================================

def read_position_ids(filename="positions") -> list[int]:
    """Read position IDs from file (one per line)"""
    position_ids = []
    try:
        with open(filename, "r", encoding="utf-8") as file:
            for line_no, raw_line in enumerate(file, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    position_ids.append(int(line))
                except ValueError:
                    print(f"✗ Skipping invalid position id in {filename}:{line_no}: {line}")
    except FileNotFoundError:
        print(f"✗ Error: positions file not found at {filename}")
        sys.exit(1)
    return position_ids


def db_connect():
    """Connect to MariaDB database"""
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
        if not entry:
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


def insert_snapshot(cursor, pos_id: int, timestamp: str, token0: str, token1: str,
                    symbol0: str, symbol1: str, fee0_display: Decimal, fee1_display: Decimal,
                    fee2_display: Decimal, token2: str, symbol2: str,
                    token0_usd: Decimal, token1_usd: Decimal, token2_usd: Decimal,
                    current_price: Decimal, in_range: int):
    """Insert position snapshot into cake table with token_2 data"""
    try:
        cursor.execute(
            f"INSERT INTO {TABLE_SNAPSHOT} "
            f"(id, date, token_0, token_1, symbol_0, symbol_1, fee_0, fee_1, "
            f"quote_0, quote_1, token_2, symbol_2, fee_2, quote_2, "
            f"current_price, in_range) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pos_id,
                timestamp,
                token0,
                token1,
                symbol0,
                symbol1,
                str(fee0_display),
                str(fee1_display),
                str(token0_usd),
                str(token1_usd),
                token2,
                symbol2,
                str(fee2_display),
                str(token2_usd),
                str(current_price),
                in_range,
            ),
        )
    except Exception as e:
        print(f"Error inserting snapshot: {e}")


def insert_into_total_table(cursor, pos_id: int, timestamp: str, 
                            token: str, symbol: str, fee: Decimal, chain: str = "bsc"):
    """Insert fee delta into total table"""
    try:
        cursor.execute(
            f"INSERT INTO {TABLE_TOTAL} (pos_id, timestamp, token, symbol, fee, chain) "
            f"VALUES (?, ?, ?, ?, ?, ?)",
            (pos_id, timestamp, token, symbol, str(fee), chain),
        )
    except Exception as e:
        print(f"Error inserting into total table: {e}")


def get_last_snapshot(cursor, pos_id: int, current_timestamp: str):
    """Retrieve the most recent snapshot before the current timestamp"""
    try:
        cursor.execute(
            f"SELECT fee_0, fee_1, fee_2 FROM {TABLE_SNAPSHOT} "
            f"WHERE id = ? AND date < ? ORDER BY date DESC LIMIT 1",
            (pos_id, current_timestamp),
        )
        result = cursor.fetchone()
        if result:
            return {
                "fee_0": Decimal(result[0]) if result[0] else Decimal(0),
                "fee_1": Decimal(result[1]) if result[1] else Decimal(0),
                "fee_2": Decimal(result[2]) if result[2] else Decimal(0),
            }
    except Exception as e:
        print(f"Warning: Could not fetch last snapshot: {e}")
    
    return None


def fetch_snapshot_history(cursor, pos_id: int):
    """Fetch all snapshots from the last 24 hours for a position"""
    try:
        cursor.execute(
            f"""
            SELECT date, token_0, token_1, symbol_0, symbol_1,
                   fee_0, fee_1, quote_0, quote_1,
                   token_2, symbol_2, fee_2, quote_2, current_price
            FROM {TABLE_SNAPSHOT}
            WHERE id = ? AND date >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            ORDER BY date ASC
            """,
            (pos_id,),
        )
        return list(cursor.fetchall())
    except Exception as e:
        print(f"Warning: Could not fetch snapshot history: {e}")
        return []


def calculate_fee_summary(cursor, pos_id: int, symbol0: str, symbol1: str, symbol2: str,
                         decimals0: int, decimals1: int, decimals2: int,
                         price_lower: Decimal, price_upper: Decimal,
                         total_unclaimed_usd: Decimal):
    """Calculate fee summary from snapshot history for the last 24 hours"""
    rows = fetch_snapshot_history(cursor, pos_id)
    
    if len(rows) < 2:
        return None
    
    dates = [row[0] for row in rows]
    prices = [Decimal(str(row[13])) if row[13] else Decimal(0) for row in rows]  # current_price
    fees_0 = [Decimal(str(row[5])) if row[5] else Decimal(0) for row in rows]  # fee_0
    fees_1 = [Decimal(str(row[6])) if row[6] else Decimal(0) for row in rows]  # fee_1
    fees_2 = [Decimal(str(row[11])) if row[11] else Decimal(0) for row in rows]  # fee_2
    
    # USD prices per token (use last snapshot for current rates)
    quote_0 = Decimal(str(rows[-1][7])) if rows[-1][7] else Decimal(0)  # quote_0
    quote_1 = Decimal(str(rows[-1][8])) if rows[-1][8] else Decimal(0)  # quote_1
    quote_2 = Decimal(str(rows[-1][12])) if rows[-1][12] else Decimal(0)  # quote_2
    
    # Calculate hourly fee values (deltas in USD)
    hourly_fee_values = []
    for i, (previous, current) in enumerate(zip(rows, rows[1:])):
        delta_0 = Decimal(str(current[5])) - Decimal(str(previous[5])) if previous[5] and current[5] else Decimal(0)
        delta_1 = Decimal(str(current[6])) - Decimal(str(previous[6])) if previous[6] and current[6] else Decimal(0)
        delta_2 = Decimal(str(current[11])) - Decimal(str(previous[11])) if previous[11] and current[11] else Decimal(0)
        
        quote_0_current = Decimal(str(current[7])) if current[7] else Decimal(0)
        quote_1_current = Decimal(str(current[8])) if current[8] else Decimal(0)
        quote_2_current = Decimal(str(current[12])) if current[12] else Decimal(0)
        
        hourly_value = delta_0 * quote_0_current + delta_1 * quote_1_current + delta_2 * quote_2_current
        hourly_fee_values.append(hourly_value)
    
    # Raw deltas from last to previous snapshot (for individual tokens last hour)
    raw_delta_0 = fees_0[-1] - fees_0[-2] if len(fees_0) >= 2 else Decimal(0)
    raw_delta_1 = fees_1[-1] - fees_1[-2] if len(fees_1) >= 2 else Decimal(0)
    raw_delta_2 = fees_2[-1] - fees_2[-2] if len(fees_2) >= 2 else Decimal(0)
    
    # Fee values
    fee_last_hour = hourly_fee_values[-1] if hourly_fee_values else Decimal(0)
    fee_last_day = (fees_0[-1] - fees_0[0]) * quote_0 + (fees_1[-1] - fees_1[0]) * quote_1 + (fees_2[-1] - fees_2[0]) * quote_2
    
    return {
        "pos_id": pos_id,
        "dates": dates,
        "prices": prices,
        "hourly_fee_values": hourly_fee_values,
        "symbol_0": symbol0,
        "symbol_1": symbol1,
        "symbol_2": symbol2,
        "decimals_0": decimals0,
        "decimals_1": decimals1,
        "decimals_2": decimals2,
        "raw_delta_0": raw_delta_0,
        "raw_delta_1": raw_delta_1,
        "raw_delta_2": raw_delta_2,
        "fee_last_hour": fee_last_hour,
        "fee_last_day": fee_last_day,
        "quote_0": quote_0,
        "quote_1": quote_1,
        "quote_2": quote_2,
        "price_lower": price_lower,
        "price_upper": price_upper,
        "total_unclaimed_usd": total_unclaimed_usd,
    }


def insert_fee_deltas(cursor, pos_id: int, timestamp: str,
                      token0: str, symbol0: str, fee0_current: Decimal,
                      token1: str, symbol1: str, fee1_current: Decimal,
                      token2: str, symbol2: str, fee2_current: Decimal):
    """DEPRECATED: This function is no longer used. Deltas are calculated inline in main()."""
    pass


def create_image_buffer(fig):
    """Create PNG buffer from matplotlib figure"""
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    buffer.seek(0)
    plt.close(fig)
    return buffer


def should_send_report(summary_data) -> bool:
    """Only send a report when fees actually increased in the tracked window."""
    return bool(
        summary_data
        and (
            summary_data["fee_last_hour"] > 0
            or summary_data["fee_last_day"] > 0
            or summary_data["raw_delta_0"] > 0
            or summary_data["raw_delta_1"] > 0
            or summary_data["raw_delta_2"] > 0
        )
    )


def create_price_chart(summary_data, pos_id: int):
    """Create line chart showing price evolution over 24 hours"""
    fig, ax = plt.subplots()
    dates = summary_data["dates"]
    prices = [float(p) for p in summary_data["prices"]]
    
    ax.plot(dates, prices, marker="o", linewidth=1)
    from matplotlib.dates import DateFormatter
    ax.xaxis.set_major_formatter(DateFormatter("%H"))
    plt.axhline(y=float(summary_data["price_lower"]), color="r", linestyle="--")
    plt.axhline(y=float(summary_data["price_upper"]), color="g", linestyle="--")
    ax.set_title(f"#{pos_id} - Price Evolution")
    plt.tight_layout()
    return fig


def create_fee_chart(summary_data, pos_id: int):
    """Create bar chart showing hourly fee values over 24 hours"""
    fig, ax = plt.subplots()
    hourly_values = [float(v) for v in summary_data["hourly_fee_values"]]
    
    ax.bar(
        np.arange(1, len(hourly_values) + 1),
        hourly_values,
    )
    ax.set_title("Fees earned last 24 hours")
    ax.axes.xaxis.set_ticks([])
    plt.tight_layout()
    return fig


async def send_telegram_text(message: str):
    """Send text message via Telegram"""
    try:
        await telegram_send.send(
            conf=TELEGRAM_CONF,
            parse_mode="html",
            messages=[message]
        )
    except Exception as e:
        print(f"[TELEGRAM] ✗ Error sending message: {e}")


async def send_telegram_image(image_buffer):
    """Send image via Telegram"""
    try:
        await telegram_send.send(
            conf=TELEGRAM_CONF,
            images=[image_buffer]
        )
    except Exception as e:
        print(f"[TELEGRAM] ✗ Error sending image: {e}")


async def send_telegram_report(summary_data):
    """Send Telegram report with text and two charts (price + fees)"""
    pos_id = summary_data["pos_id"]
    fee_last_hour_text = format_usd(summary_data["fee_last_hour"])
    fee_last_day_text = format_usd(summary_data["fee_last_day"])
    fee_0_last_hour_text = format_token_amount(summary_data["raw_delta_0"], summary_data["decimals_0"])
    fee_1_last_hour_text = format_token_amount(summary_data["raw_delta_1"], summary_data["decimals_1"])
    fee_2_last_hour_text = format_token_amount(summary_data["raw_delta_2"], summary_data["decimals_2"])
    unclaimed_text = format_usd(summary_data["total_unclaimed_usd"])
    
    title = f"#{pos_id} - {summary_data['symbol_0']}/{summary_data['symbol_1']}"
    
    message = (
        f"<b>{title}</b>\n\n"
        f"<b>Fees last hour:</b>\n"
        f"{fee_0_last_hour_text} {summary_data['symbol_0']}\n"
        f"{fee_1_last_hour_text} {summary_data['symbol_1']}\n"
        f"{fee_2_last_hour_text} {summary_data['symbol_2']}\n"
        f"--------------------\n"
        f"{fee_last_hour_text} USD\n\n"
        f"<b>Fees last 24 hours:</b>\n"
        f"{fee_last_day_text} USD\n\n"
        f"<b>Total value of unclaimed fees:</b>\n"
        f"{unclaimed_text} USD"
    )
    
    # Send text message
    await send_telegram_text(message)
    
    # Send price chart
    price_fig = create_price_chart(summary_data, pos_id)
    price_buffer = create_image_buffer(price_fig)
    await send_telegram_image(price_buffer)
    price_buffer.close()
    
    # Send fee chart
    fee_fig = create_fee_chart(summary_data, pos_id)
    fee_buffer = create_image_buffer(fee_fig)
    await send_telegram_image(fee_buffer)
    fee_buffer.close()


def send_telegram_async(summary_data=None):
    """Wrapper to send Telegram report from sync context"""
    try:
        if summary_data:
            asyncio.run(send_telegram_report(summary_data))
        else:
            print("[TELEGRAM] ⚠ No summary data available for report")
    except Exception as e:
        print(f"[TELEGRAM] ✗ Failed to send report: {e}")


# ============================================================
# MAIN
# ============================================================

def process_position(pos_id: int, w3, position_mgr, factory, master, bnb_usd, position_mgr_address):
    """Process a single position"""
    try:
        # ===== POSITION DATA =====
        pos = position_mgr.functions.positions(pos_id).call()

        token0 = pos[2]
        token1 = pos[3]
        fee_tier = pos[4]
        tick_lower = pos[5]
        tick_upper = pos[6]
        liquidity = pos[7]

        fee_growth_inside0_last = Decimal(pos[8])
        fee_growth_inside1_last = Decimal(pos[9])

        owed0 = pos[10]
        owed1 = pos[11]

        # ===== POOL DATA =====
        pool_addr = factory.functions.getPool(token0, token1, fee_tier).call()
        pool = w3.eth.contract(address=pool_addr, abi=POOL_ABI)

        slot0_data = w3.eth.call({
            "to": pool_addr,
            "data": "0x3850c7bd"
        })

        sqrtPriceX96 = int.from_bytes(slot0_data[0:32], 'big')
        current_tick = int.from_bytes(slot0_data[32:64], 'big', signed=True)
        if current_tick > 2**23:
            current_tick = current_tick - 2**24

        # ===== FEE DATA =====
        fee_global0 = pool.functions.feeGrowthGlobal0X128().call()
        fee_global1 = pool.functions.feeGrowthGlobal1X128().call()

        lower = pool.functions.ticks(tick_lower).call()
        upper = pool.functions.ticks(tick_upper).call()

        fee_inside0_current = fee_inside(fee_global0, lower, upper, current_tick, tick_lower, tick_upper, token_index=0)
        fee_inside1_current = fee_inside(fee_global1, lower, upper, current_tick, tick_lower, tick_upper, token_index=1)

        in_range = tick_lower <= current_tick < tick_upper

        # Try direct collect call
        fee0 = Decimal(0)
        fee1 = Decimal(0)
        collect_success = False
        try:
            collect_params = {
                "tokenId": pos_id,
                "recipient": position_mgr_address,
                "amount0Max": 2**128 - 1,
                "amount1Max": 2**128 - 1,
            }
            amount0, amount1 = position_mgr.functions.collect(collect_params).call()
            fee0 = Decimal(amount0)
            fee1 = Decimal(amount1)
            collect_success = True
        except Exception as e:
            fee0_unrealized = (Decimal(liquidity) * (fee_inside0_current - fee_growth_inside0_last)) / Q128
            fee1_unrealized = (Decimal(liquidity) * (fee_inside1_current - fee_growth_inside1_last)) / Q128
            fee0 = clamp(fee0_unrealized) + Decimal(owed0)
            fee1 = clamp(fee1_unrealized) + Decimal(owed1)

        if fee0 == 0 and fee1 == 0:
            try:
                collect_params = {
                    "tokenId": pos_id,
                    "recipient": position_mgr_address,
                    "amount0Max": 2**128 - 1,
                    "amount1Max": 2**128 - 1,
                }
                amount0, amount1 = position_mgr.functions.collect(collect_params).call()
                fee0 = Decimal(amount0)
                fee1 = Decimal(amount1)
            except Exception as e:
                pass

        # ===== TOKEN INFO =====
        decimals0 = get_token_decimals(w3, token0)
        decimals1 = get_token_decimals(w3, token1)
        symbol0 = get_token_symbol(w3, token0)
        symbol1 = get_token_symbol(w3, token1)

        fee0_display = fee0 / Decimal(10) ** decimals0
        fee1_display = fee1 / Decimal(10) ** decimals1

        # ===== CAKE REWARDS =====
        cake_reward_display = Decimal(0)
        try:
            pending_cake = master.functions.pendingCake(pos_id).call()
            cake_reward_display = Decimal(pending_cake) / Decimal(1e18)
        except Exception as e:
            try:
                info = master.functions.userPositionInfos(pos_id).call()
                pending_reward = Decimal(info[5])
                if pending_reward > 0:
                    cake_reward_display = pending_reward / Decimal(1e18)
            except Exception as e2:
                pass

        # ===== PRICE CALCULATIONS =====
        token1_per_token0 = sqrt_price_x96_to_token1_per_token0(sqrtPriceX96, decimals0, decimals1)

        token0_usd, token1_usd, valuation_source = get_token_usd_prices(
            token0=token0,
            token1=token1,
            token1_per_token0=token1_per_token0,
            bnb_usd=bnb_usd,
        )

        fee0_usd = fee0_display * token0_usd
        fee1_usd = fee1_display * token1_usd
        
        # Get CAKE price from CAKE/BNB pool
        cake_usd = Decimal(0)
        try:
            cake_bnb_pool = factory.functions.getPool(
                Web3.to_checksum_address(CAKE),
                Web3.to_checksum_address(WBNB),
                100  # 0.01% fee
            ).call()
            if cake_bnb_pool != "0x" + "0" * 40:  # Pool exists
                cake_usd = get_cake_usd_price(w3, cake_bnb_pool, 18)
        except Exception as e:
            pass
        
        if cake_usd == 0:
            cake_usd = Decimal("1")  # Fallback to $1
        
        cake_usd_display = cake_reward_display * cake_usd
        
        total_unclaimed_usd = fee0_usd + fee1_usd + cake_usd_display

        # ===== TIMESTAMP =====
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ===== PRICE BOUNDARIES =====
        price_lower = tick_to_token1_per_token0(tick_lower, decimals0, decimals1)
        price_upper = tick_to_token1_per_token0(tick_upper, decimals0, decimals1)

        # ===== DATABASE WRITES =====
        connection, cursor = db_connect()
        
        # Get last snapshot BEFORE the current timestamp
        last_snapshot = get_last_snapshot(cursor, pos_id, timestamp)
        
        # Insert snapshot into cake table
        insert_snapshot(
            cursor=cursor,
            pos_id=pos_id,
            timestamp=timestamp,
            token0=token0,
            token1=token1,
            symbol0=symbol0,
            symbol1=symbol1,
            fee0_display=fee0_display,
            fee1_display=fee1_display,
            fee2_display=cake_reward_display,
            token2=Web3.to_checksum_address(CAKE),
            symbol2=get_token_symbol(w3, Web3.to_checksum_address(CAKE)),
            token0_usd=token0_usd,
            token1_usd=token1_usd,
            token2_usd=cake_usd,
            current_price=token1_per_token0,
            in_range=1 if in_range else 0,
        )
        
        # Calculate and insert fee deltas into total table (only positive changes)
        if last_snapshot:
            delta_0 = fee0_display - last_snapshot["fee_0"]
            delta_1 = fee1_display - last_snapshot["fee_1"]
            delta_2 = cake_reward_display - last_snapshot["fee_2"]
            
            if delta_0 > 0:
                insert_into_total_table(cursor, pos_id, timestamp, token0, symbol0, delta_0, "bsc")
            
            if delta_1 > 0:
                insert_into_total_table(cursor, pos_id, timestamp, token1, symbol1, delta_1, "bsc")
            
            if delta_2 > 0:
                insert_into_total_table(cursor, pos_id, timestamp, Web3.to_checksum_address(CAKE), get_token_symbol(w3, Web3.to_checksum_address(CAKE)), delta_2, "bsc")
        else:
            # No previous snapshot, insert all current fees if > 0 (first run)
            if fee0_display > 0:
                insert_into_total_table(cursor, pos_id, timestamp, token0, symbol0, fee0_display, "bsc")
            if fee1_display > 0:
                insert_into_total_table(cursor, pos_id, timestamp, token1, symbol1, fee1_display, "bsc")
            if cake_reward_display > 0:
                insert_into_total_table(cursor, pos_id, timestamp, Web3.to_checksum_address(CAKE), get_token_symbol(w3, Web3.to_checksum_address(CAKE)), cake_reward_display, "bsc")
        
        connection.commit()

        if liquidity == 0 and remove_position_id(pos_id):
            print(f"Removed {pos_id} from positions (liquidity is zero).")
        
        # Calculate fee summary from 24h history and send Telegram report
        cake_decimals = get_token_decimals(w3, Web3.to_checksum_address(CAKE))
        summary_data = calculate_fee_summary(cursor, pos_id, symbol0, symbol1, get_token_symbol(w3, Web3.to_checksum_address(CAKE)), 
                                            decimals0, decimals1, cake_decimals,
                                            price_lower, price_upper,
                                            total_unclaimed_usd)
        cursor.close()
        connection.close()
        
        if should_send_report(summary_data):
            send_telegram_async(summary_data)
        
        return True
        
    except Exception as e:
        print(f"✗ Error processing position {pos_id}: {e}\n")
        return False


def main():
    """Main loop: process all positions from file"""
    w3 = Web3(Web3.HTTPProvider(RPC_URL))

    # Contract addresses
    POSITION_MANAGER = Web3.to_checksum_address("0x46A15B0b27311cedF172AB29E4f4766fbE7F4364")
    MASTER_CHEF = Web3.to_checksum_address("0x556B9306565093C855AEA9AE92A594704c2Cd59e")
    FACTORY = Web3.to_checksum_address("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865")

    position_mgr = w3.eth.contract(address=POSITION_MANAGER, abi=POSITION_ABI)
    factory = w3.eth.contract(address=FACTORY, abi=FACTORY_ABI)
    master = w3.eth.contract(address=MASTER_CHEF, abi=MASTER_ABI)

    # Get BNB/USD price from Chainlink
    bnb_usd = get_bnb_usd_price(w3)

    # Read all position IDs from file
    position_ids = (
        [args.position_id]
        if args.position_id is not None
        else read_position_ids()
    )
    
    if not position_ids:
        print("✗ No positions found in positions file")
        return

    # Process each position
    for pos_id in position_ids:
        process_position(pos_id, w3, position_mgr, factory, master, bnb_usd, POSITION_MANAGER)


if __name__ == "__main__":
    main()
