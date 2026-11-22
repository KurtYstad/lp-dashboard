#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Orca Liquidity Pool Fee Tracker for Solana Whirlpools
Uses Helius as RPC provider (best for Solana free tier)
Tracks unclaimed fees and sends Telegram notifications
"""

import io
import os
import argparse
import datetime
import telegram_send
import mariadb
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
import numpy as np
import asyncio
import hashlib
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, getcontext
import dotenv
from typing import Any, Optional
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
import base64


def parse_position_id(value: str) -> str:
    if len(value) != 44:
        raise argparse.ArgumentTypeError("Orca position IDs must be 44 characters long.")
    try:
        Pubkey.from_string(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Invalid Orca position ID: {error}") from error
    return value


# ============================================================
# CONFIG
# ============================================================

dotenv.load_dotenv()

parser = argparse.ArgumentParser(description="Orca fee monitor")
parser.add_argument(
    "position_id",
    nargs="?",
    type=parse_position_id,
    help="Process only this position ID.",
)
args = parser.parse_args()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT_STR = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
TABLE_SNAPSHOT = os.getenv("TABLE_SNAPSHOT", "orca_snapshot")
TABLE_TOTAL = os.getenv("TABLE_TOTAL", "total")
TELEGRAM_CONF = os.getenv("TELEGRAM_CONF")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
POSITION_READ_CONCURRENCY = int(os.getenv("POSITION_READ_CONCURRENCY", "4"))

DB_PORT = int(DB_PORT_STR)

# Solana Mainnet with Helius
SOLANA_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

# Orca Programs
ORCA_WHIRLPOOL_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"

# Well-known mints for valuation
NATIVE_MINT = "So11111111111111111111111111111111111111112"  # SOL
USD_STABLES = {
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}

# Well-known token mints on Solana
WELL_KNOWN_MINTS = {
    "So11111111111111111111111111111111111111112": ("SOL", 9),
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", 6),
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": ("mSOL", 9),
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R": ("RAY", 6),
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE": ("ORCA", 6),
}


# ============================================================
# DATA STRUCTURES
# ============================================================

getcontext().prec = 80

Q128 = 2**128
Q64 = 2**64
TICK_ARRAY_SIZE = 88
TICK_SIZE = 113
FIXED_TICK_ARRAY_DISCRIMINATOR = bytes.fromhex("4561bdbe6e0742bb")
WHIRLPOOL_PROGRAM_PUBKEY = Pubkey.from_string(ORCA_WHIRLPOOL_PROGRAM)


@dataclass(frozen=True)
class TokenInfo:
    mint: str
    symbol: str
    decimals: int


@dataclass(frozen=True)
class PositionSnapshot:
    pos_id: str
    whirlpool_id: str
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
    fee_percent: Decimal
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


@dataclass(frozen=True)
class WhirlpoolState:
    tick_spacing: int
    fee_rate: int
    liquidity: int
    sqrt_price: int
    tick_current_index: int
    token_mint_a: str
    token_mint_b: str
    fee_growth_global_a: int
    fee_growth_global_b: int


@dataclass(frozen=True)
class TickState:
    initialized: bool
    liquidity_net: int
    liquidity_gross: int
    fee_growth_outside_a: int
    fee_growth_outside_b: int


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def sub_in_u128(x: int, y: int) -> int:
    return (x - y) % Q128


def compute_unclaimed_fees(
    liquidity: int,
    fee_growth_inside_current: int,
    fee_growth_inside_last: int,
) -> int:
    delta = sub_in_u128(fee_growth_inside_current, fee_growth_inside_last)
    return liquidity * delta // Q64


def anchor_discriminator(account_name: str) -> bytes:
    return hashlib.sha256(f"account:{account_name}".encode("utf-8")).digest()[:8]


DYNAMIC_TICK_ARRAY_DISCRIMINATOR = anchor_discriminator("DynamicTickArray")


def pubkey_from_bytes(data: bytes) -> str:
    return str(Pubkey(data))


def tick_to_price(tick_index: int, decimals_a: int, decimals_b: int) -> Decimal:
    price = Decimal("1.0001") ** Decimal(tick_index)
    return price * (Decimal(10) ** Decimal(decimals_a - decimals_b))


def sqrt_price_x64_to_price(sqrt_price_x64: int, decimals_a: int, decimals_b: int) -> Decimal:
    sqrt_price = Decimal(sqrt_price_x64) / Decimal(Q64)
    return (sqrt_price * sqrt_price) * (Decimal(10) ** Decimal(decimals_a - decimals_b))


def token_amount(raw_amount: int, decimals: int) -> Decimal:
    return Decimal(raw_amount) / (Decimal(10) ** Decimal(decimals))


def derive_tick_array_start_tick(tick_index: int, tick_spacing: int) -> int:
    ticks_in_array = TICK_ARRAY_SIZE * tick_spacing
    return (tick_index // ticks_in_array) * ticks_in_array


def derive_tick_array_address(whirlpool: str, start_tick_index: int) -> str:
    whirlpool_pubkey = Pubkey.from_string(whirlpool)
    address, _bump = Pubkey.find_program_address(
        [
            b"tick_array",
            bytes(whirlpool_pubkey),
            str(start_tick_index).encode("utf-8"),
        ],
        WHIRLPOOL_PROGRAM_PUBKEY,
    )
    return str(address)


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


def read_position_ids(filename="positions") -> list[str]:
    """Read Orca position public keys from file (44-char base58 strings)"""
    position_ids = []
    with open(filename, "r", encoding="utf-8") as file:
        for line_no, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                if len(line) != 44:
                    print(f"❌ Skipping invalid position id in {filename}:{line_no}")
                    print(f"   Expected 44 chars, got {len(line)}: {line}")
                    print(f"   ⚠️ Make sure you're using 'Position Address' (not 'Position NFT')")
                    continue
                
                # Validate it's valid base58
                try:
                    Pubkey.from_string(line)
                    position_ids.append(line)
                except Exception as e:
                    print(f"❌ Skipping invalid base58 key in {filename}:{line_no}")
                    print(f"   Error: {e}")
                    print(f"   Key: {line}")
                    
            except Exception as e:
                print(f"❌ Skipping position id in {filename}:{line_no}: {e}")

    return position_ids


# ============================================================
# DATABASE FUNCTIONS
# ============================================================

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


def remove_position_id(position_id: str, filename: str = "positions") -> bool:
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

        if entry == position_id:
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


def insert_fee_deltas(cursor, summary: FeeSummary, pos_id: str, timestamp: str):

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
                "solana",
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
                "solana",
            ),
        )


def calculate_fee_summary(cursor, snapshot: PositionSnapshot) -> Optional[FeeSummary]:
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

    fee_last_hour = hourly_fee_values[-1] if hourly_fee_values else Decimal(0)
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


# ============================================================
# TELEGRAM FUNCTIONS
# ============================================================

async def send_telegram_text(message):
    await telegram_send.send(
        conf=(TELEGRAM_CONF), parse_mode="html", messages=[message]
    )


async def send_telegram_image(image_buffer):
    await telegram_send.send(conf=(TELEGRAM_CONF), images=[image_buffer])


def create_image_buffer(fig):
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    buffer.seek(0)
    plt.close(fig)
    return buffer


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
# SOLANA CLIENT
# ============================================================

class SolanaClient:
    def __init__(self, rpc_url: str):
        self.client = AsyncClient(rpc_url)
        self.token_cache: dict[str, TokenInfo] = {}

    async def close(self):
        await self.client.close()

    async def get_account_info(self, pubkey_str: str) -> Optional[dict]:
        """Fetch account data from Solana and return full info"""
        try:
            # Validate pubkey format first
            try:
                pubkey = Pubkey.from_string(pubkey_str)
            except Exception as e:
                print(f"❌ Invalid Solana address format: {pubkey_str}")
                print(f"   Error: {e}")
                print(f"   ⚠️ Make sure you copied the 'Position Address' correctly")
                return None
            
            response = await self.client.get_account_info(pubkey)
            
            if response.value is not None:
                raw_data = response.value.data
                
                # Try to decode from base64 if it's a string, otherwise use as-is
                if isinstance(raw_data, str):
                    try:
                        decoded = base64.b64decode(raw_data)
                    except Exception as decode_err:
                        print(f"⚠️ Base64 decode error for {pubkey_str[:8]}...")
                        print(f"   Error: {decode_err}")
                        print(f"   Using raw data as-is")
                        decoded = raw_data.encode()
                else:
                    # Already bytes
                    decoded = raw_data
                
                return {
                    "data": decoded,
                    "lamports": response.value.lamports,
                    "owner": str(response.value.owner),
                    "executable": response.value.executable,
                    "rent_epoch": response.value.rent_epoch,
                }
            return None
        except Exception as e:
            error_str = str(e).lower()
            if "padding" in error_str:
                print(f"❌ Invalid address format for {pubkey_str[:8]}...")
                print(f"   Error: {e}")
                print(f"   ⚠️ This might be a Position NFT instead of Position Address")
                print(f"   ⚠️ In Orca UI, make sure you're copying 'Position Address'")
            else:
                print(f"Error fetching account {pubkey_str[:8]}...: {e}")
            return None

    async def get_token_info(self, mint: str) -> TokenInfo:
        """Get token symbol and decimals from mint"""
        if mint in self.token_cache:
            return self.token_cache[mint]

        # Check well-known mints first
        if mint in WELL_KNOWN_MINTS:
            symbol, decimals = WELL_KNOWN_MINTS[mint]
            info = TokenInfo(mint, symbol, decimals)
            self.token_cache[mint] = info
            return info

        account_info = await self.get_account_info(mint)
        decimals = 6
        if account_info and len(account_info["data"]) > 44:
            decimals = account_info["data"][44]

        # For unknown tokens, use on-chain decimals and a short mint alias.
        info = TokenInfo(mint, f"Token_{mint[:4]}", decimals)
        self.token_cache[mint] = info
        return info


# ============================================================
# POSITION DATA PARSING
# ============================================================

def parse_position_data(data: bytes) -> Optional[dict]:
    """
    Parse Orca Whirlpool Position account data (v2)
    
    Official struct from Orca Whirlpools program:
    https://github.com/orca-so/whirlpools/blob/main/programs/whirlpool/src/state/position.rs
    
    Position account layout (CORRECT offsets):
    0x00-0x07: discriminator (8 bytes, u64)
    0x08-0x27: whirlpool (32 bytes, Pubkey)
    0x28-0x47: position_mint (32 bytes, Pubkey)
    0x48-0x57: liquidity (16 bytes, u128) ← THE KEY FIELD!
    0x58-0x5b: tick_lower_index (4 bytes, i32)
    0x5c-0x5f: tick_upper_index (4 bytes, i32)
    0x60-0x6f: fee_growth_checkpoint_a (16 bytes, u128)
    0x70-0x77: fee_owed_a (8 bytes, u64) ← unclaimed fees token A
    0x78-0x87: fee_growth_checkpoint_b (16 bytes, u128)
    0x88-0x8f: fee_owed_b (8 bytes, u64) ← unclaimed fees token B
    0x90+: reward_infos (72 bytes = 3 x 24 bytes per PositionRewardInfo)
    
    Total: 216 bytes (8 + 32 + 32 + 16 + 4 + 4 + 16 + 8 + 16 + 8 + 72 = 216)
    """
    if len(data) < 0x90:  # Need at least 144 bytes (up to end of fee_owed_b)
        print(f"   ⚠️ Account data too small: {len(data)} bytes (need 144+ for core fields)")
        return None

    try:
        # Parse position fields with CORRECT offsets from official Orca struct
        discriminator = int.from_bytes(data[0x00:0x08], byteorder="little", signed=False)
        whirlpool = str(Pubkey(data[0x08:0x28]))
        position_mint = str(Pubkey(data[0x28:0x48]))
        
        # These are the critical fields that were read as 0 before.
        liquidity = int.from_bytes(data[0x48:0x58], byteorder="little", signed=False)
        tick_lower = int.from_bytes(data[0x58:0x5c], byteorder="little", signed=True)
        tick_upper = int.from_bytes(data[0x5c:0x60], byteorder="little", signed=True)
        
        # Fee growth and owed values
        fee_growth_a = int.from_bytes(data[0x60:0x70], byteorder="little", signed=False)
        fee_owed_a = int.from_bytes(data[0x70:0x78], byteorder="little", signed=False)
        fee_growth_b = int.from_bytes(data[0x78:0x88], byteorder="little", signed=False)
        fee_owed_b = int.from_bytes(data[0x88:0x90], byteorder="little", signed=False)
        
        return {
            "discriminator": discriminator,
            "whirlpool": whirlpool,
            "position_mint": position_mint,
            "liquidity": liquidity,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "fee_growth_a": fee_growth_a,
            "fee_owed_a": fee_owed_a,
            "fee_growth_b": fee_growth_b,
            "fee_owed_b": fee_owed_b,
        }
    except Exception as e:
        print(f"   ❌ Error parsing position data: {e}")
        import traceback
        traceback.print_exc()
        return None


def parse_whirlpool_data(data: bytes) -> Optional[WhirlpoolState]:
    """
    Parse Orca Whirlpool account data.

    Layout from Orca Whirlpool::LEN:
    discriminator + config/bump/spacing/fees/liquidity/sqrt_price/current_tick,
    token A/B mints and global fee growth accumulators.
    """
    if len(data) < 0x105:
        print(f"   ⚠️ Whirlpool account too small: {len(data)} bytes (need 261+)")
        return None

    try:
        offset = 0x08
        offset += 32  # whirlpools_config
        offset += 1   # whirlpool_bump
        tick_spacing = int.from_bytes(data[offset:offset + 2], "little")
        offset += 2
        offset += 2   # fee_tier_index_seed
        fee_rate = int.from_bytes(data[offset:offset + 2], "little")
        offset += 2
        offset += 2   # protocol_fee_rate
        liquidity = int.from_bytes(data[offset:offset + 16], "little")
        offset += 16
        sqrt_price = int.from_bytes(data[offset:offset + 16], "little")
        offset += 16
        tick_current_index = int.from_bytes(data[offset:offset + 4], "little", signed=True)
        offset += 4
        offset += 8   # protocol_fee_owed_a
        offset += 8   # protocol_fee_owed_b
        token_mint_a = pubkey_from_bytes(data[offset:offset + 32])
        offset += 32
        offset += 32  # token_vault_a
        fee_growth_global_a = int.from_bytes(data[offset:offset + 16], "little")
        offset += 16
        token_mint_b = pubkey_from_bytes(data[offset:offset + 32])
        offset += 32
        offset += 32  # token_vault_b
        fee_growth_global_b = int.from_bytes(data[offset:offset + 16], "little")

        return WhirlpoolState(
            tick_spacing=tick_spacing,
            fee_rate=fee_rate,
            liquidity=liquidity,
            sqrt_price=sqrt_price,
            tick_current_index=tick_current_index,
            token_mint_a=token_mint_a,
            token_mint_b=token_mint_b,
            fee_growth_global_a=fee_growth_global_a,
            fee_growth_global_b=fee_growth_global_b,
        )
    except Exception as e:
        print(f"   ❌ Error parsing whirlpool data: {e}")
        return None


def parse_fixed_tick(data: bytes, tick_index: int, tick_spacing: int) -> Optional[TickState]:
    if len(data) < 8 + 4 + 32:
        return None

    start_tick_index = int.from_bytes(data[0x08:0x0c], "little", signed=True)
    offset_index = (tick_index - start_tick_index) // tick_spacing
    if offset_index < 0 or offset_index >= TICK_ARRAY_SIZE:
        print(f"   ⚠️ Tick {tick_index} not inside fixed tick array {start_tick_index}")
        return None

    offset = 0x0c + (offset_index * TICK_SIZE)
    if len(data) < offset + TICK_SIZE:
        print(f"   ⚠️ Fixed tick array truncated at tick offset {offset_index}")
        return None

    return TickState(
        initialized=data[offset] != 0,
        liquidity_net=int.from_bytes(data[offset + 1:offset + 17], "little", signed=True),
        liquidity_gross=int.from_bytes(data[offset + 17:offset + 33], "little"),
        fee_growth_outside_a=int.from_bytes(data[offset + 33:offset + 49], "little"),
        fee_growth_outside_b=int.from_bytes(data[offset + 49:offset + 65], "little"),
    )


def parse_dynamic_tick(data: bytes, tick_index: int, tick_spacing: int) -> Optional[TickState]:
    if len(data) < 8 + 4 + 32 + 16:
        return None

    start_tick_index = int.from_bytes(data[0x08:0x0c], "little", signed=True)
    offset_index = (tick_index - start_tick_index) // tick_spacing
    if offset_index < 0 or offset_index >= TICK_ARRAY_SIZE:
        print(f"   ⚠️ Tick {tick_index} not inside dynamic tick array {start_tick_index}")
        return None

    bitmap = int.from_bytes(data[0x2c:0x3c], "little")
    tick_offset = 0x3c
    for index in range(offset_index):
        tick_offset += 113 if ((bitmap >> index) & 1) else 1

    initialized = ((bitmap >> offset_index) & 1) == 1
    if not initialized:
        return TickState(False, 0, 0, 0, 0)

    if len(data) < tick_offset + 113:
        print(f"   ⚠️ Dynamic tick array truncated at tick offset {offset_index}")
        return None

    variant = data[tick_offset]
    if variant != 1:
        return TickState(False, 0, 0, 0, 0)

    offset = tick_offset + 1
    return TickState(
        initialized=True,
        liquidity_net=int.from_bytes(data[offset:offset + 16], "little", signed=True),
        liquidity_gross=int.from_bytes(data[offset + 16:offset + 32], "little"),
        fee_growth_outside_a=int.from_bytes(data[offset + 32:offset + 48], "little"),
        fee_growth_outside_b=int.from_bytes(data[offset + 48:offset + 64], "little"),
    )


def parse_tick_array_data(data: bytes, tick_index: int, tick_spacing: int) -> Optional[TickState]:
    discriminator = data[:8]
    if discriminator == FIXED_TICK_ARRAY_DISCRIMINATOR:
        return parse_fixed_tick(data, tick_index, tick_spacing)
    if discriminator == DYNAMIC_TICK_ARRAY_DISCRIMINATOR:
        return parse_dynamic_tick(data, tick_index, tick_spacing)

    print(f"   ⚠️ Unknown tick array discriminator: {discriminator.hex()}")
    return None


def compute_fee_growth_inside(
    whirlpool: WhirlpoolState,
    tick_lower: TickState,
    tick_upper: TickState,
    lower_index: int,
    upper_index: int,
) -> tuple[int, int]:
    if whirlpool.tick_current_index < lower_index:
        fee_growth_below_a = sub_in_u128(
            whirlpool.fee_growth_global_a,
            tick_lower.fee_growth_outside_a,
        )
        fee_growth_below_b = sub_in_u128(
            whirlpool.fee_growth_global_b,
            tick_lower.fee_growth_outside_b,
        )
    else:
        fee_growth_below_a = tick_lower.fee_growth_outside_a
        fee_growth_below_b = tick_lower.fee_growth_outside_b

    if whirlpool.tick_current_index < upper_index:
        fee_growth_above_a = tick_upper.fee_growth_outside_a
        fee_growth_above_b = tick_upper.fee_growth_outside_b
    else:
        fee_growth_above_a = sub_in_u128(
            whirlpool.fee_growth_global_a,
            tick_upper.fee_growth_outside_a,
        )
        fee_growth_above_b = sub_in_u128(
            whirlpool.fee_growth_global_b,
            tick_upper.fee_growth_outside_b,
        )

    fee_growth_inside_a = sub_in_u128(
        sub_in_u128(whirlpool.fee_growth_global_a, fee_growth_below_a),
        fee_growth_above_a,
    )
    fee_growth_inside_b = sub_in_u128(
        sub_in_u128(whirlpool.fee_growth_global_b, fee_growth_below_b),
        fee_growth_above_b,
    )
    return fee_growth_inside_a, fee_growth_inside_b


async def read_position(
    solana_client: SolanaClient,
    position_pubkey: str,
) -> Optional[PositionSnapshot]:
    """
    Read Orca Whirlpool position and compute unclaimed fees
    
    NOTE: Full production implementation requires:
    1. Fetching whirlpool state for current tick and token mints
    2. Parsing all related accounts
    3. Computing prices from ticks
    
    For production use, integrate with Orca SDK:
    pip install orca-whirlpool-sdk
    """
    try:
        # Get position account data
        account_info = await solana_client.get_account_info(position_pubkey)
        
        if not account_info:
            print(f"Position {position_pubkey[:8]}... not found or could not be fetched")
            return None

        position_data = account_info["data"]
        
        # Parse position data
        parsed = parse_position_data(position_data)
        if not parsed:
            print(f"⚠️ Could not parse position data (size: {len(position_data)} bytes)")
            print(f"   Position might be closed or not yet initialized")
            print(f"   Expected minimum 176 bytes, got {len(position_data)}")
            print(f"\n   ⚠️ IMPORTANT: Account structure might differ from expected layout")
            print(f"   Detected values at key offsets:")
            
            # Try to detect ticks (should be i32 values around -887220 to 443380)
            try:
                tick_lower_candidate = int.from_bytes(position_data[0x4c:0x50], byteorder="little", signed=True)
                tick_upper_candidate = int.from_bytes(position_data[0x50:0x54], byteorder="little", signed=True)
                print(f"   At 0x4c-0x53: {tick_lower_candidate} and {tick_upper_candidate} (might be ticks)")
            except:
                pass
            
            # Try to find non-zero values
            print(f"   First non-zero bytes:")
            for i in range(0, min(len(position_data), 0xd0), 8):
                chunk = position_data[i:i+8]
                if any(b != 0 for b in chunk):
                    hex_str = chunk.hex()
                    print(f"   0x{i:02x}: {hex_str}")
            
            return None

        
        whirlpool_info = await solana_client.get_account_info(parsed["whirlpool"])
        if not whirlpool_info:
            print(f"⚠️ Could not fetch whirlpool {parsed['whirlpool'][:8]}...")
            return None

        whirlpool = parse_whirlpool_data(whirlpool_info["data"])
        if not whirlpool:
            return None



        lower_start_tick = derive_tick_array_start_tick(
            parsed["tick_lower"],
            whirlpool.tick_spacing,
        )
        upper_start_tick = derive_tick_array_start_tick(
            parsed["tick_upper"],
            whirlpool.tick_spacing,
        )
        lower_tick_array = derive_tick_array_address(parsed["whirlpool"], lower_start_tick)
        upper_tick_array = derive_tick_array_address(parsed["whirlpool"], upper_start_tick)

        lower_tick_array_info, upper_tick_array_info = await asyncio.gather(
            solana_client.get_account_info(lower_tick_array),
            solana_client.get_account_info(upper_tick_array),
        )
        if not lower_tick_array_info or not upper_tick_array_info:
            print("⚠️ Could not fetch lower/upper tick array accounts")
            return None

        lower_tick = parse_tick_array_data(
            lower_tick_array_info["data"],
            parsed["tick_lower"],
            whirlpool.tick_spacing,
        )
        upper_tick = parse_tick_array_data(
            upper_tick_array_info["data"],
            parsed["tick_upper"],
            whirlpool.tick_spacing,
        )
        if not lower_tick or not upper_tick:
            return None

        if not lower_tick.initialized or not upper_tick.initialized:
            print("⚠️ Lower or upper tick is not initialized; fee growth cannot be computed")
            return None

        fee_growth_inside_a, fee_growth_inside_b = compute_fee_growth_inside(
            whirlpool,
            lower_tick,
            upper_tick,
            parsed["tick_lower"],
            parsed["tick_upper"],
        )

        unclaimed_a_raw = parsed["fee_owed_a"] + compute_unclaimed_fees(
            parsed["liquidity"],
            fee_growth_inside_a,
            parsed["fee_growth_a"],
        )
        unclaimed_b_raw = parsed["fee_owed_b"] + compute_unclaimed_fees(
            parsed["liquidity"],
            fee_growth_inside_b,
            parsed["fee_growth_b"],
        )

        token_a, token_b = await asyncio.gather(
            solana_client.get_token_info(whirlpool.token_mint_a),
            solana_client.get_token_info(whirlpool.token_mint_b),
        )

        fee_a = token_amount(unclaimed_a_raw, token_a.decimals)
        fee_b = token_amount(unclaimed_b_raw, token_b.decimals)
        current_price = sqrt_price_x64_to_price(
            whirlpool.sqrt_price,
            token_a.decimals,
            token_b.decimals,
        )
        price_lower = tick_to_price(parsed["tick_lower"], token_a.decimals, token_b.decimals)
        price_upper = tick_to_price(parsed["tick_upper"], token_a.decimals, token_b.decimals)

        quote_a = Decimal("0")
        quote_b = Decimal("0")
        valuation_source = "unsupported: no stable side"
        stable_mints = set(USD_STABLES.values())
        if whirlpool.token_mint_a in stable_mints:
            quote_a = Decimal("1")
            quote_b = Decimal("1") / current_price if current_price else Decimal("0")
            valuation_source = "derived_from_token_a_stable"
        elif whirlpool.token_mint_b in stable_mints:
            quote_b = Decimal("1")
            quote_a = current_price
            valuation_source = "derived_from_token_b_stable"

        total_unclaimed_usd_value = (fee_a * quote_a) + (fee_b * quote_b)
        in_range = int(parsed["tick_lower"] <= whirlpool.tick_current_index < parsed["tick_upper"])



        return PositionSnapshot(
            pos_id=position_pubkey,
            whirlpool_id=parsed["whirlpool"],
            token_0=whirlpool.token_mint_a,
            token_1=whirlpool.token_mint_b,
            symbol_0=token_a.symbol,
            symbol_1=token_b.symbol,
            fee_0=fee_a,
            fee_1=fee_b,
            quote_0=quote_a,
            quote_1=quote_b,
            price_lower=price_lower,
            price_upper=price_upper,
            current_price=current_price,
            fee_percent=Decimal(whirlpool.fee_rate) / Decimal("10000"),
            liquidity=parsed["liquidity"],
            total_unclaimed_usd_value=total_unclaimed_usd_value,
            in_range=in_range,
            valuation_source=valuation_source,
        )
        
    except Exception as e:
        print(f"Error reading position {position_pubkey[:8]}...: {e}")
        return None


async def read_position_limited(
    solana_client: SolanaClient,
    position_pubkey: str,
    semaphore: asyncio.Semaphore,
) -> Optional[PositionSnapshot]:
    async with semaphore:
        return await read_position(solana_client, position_pubkey)


# ============================================================
# MAIN
# ============================================================

async def main():
    position_ids = (
        [args.position_id]
        if args.position_id is not None
        else read_position_ids()
    )
    if not position_ids:
        return

    solana_client = SolanaClient(SOLANA_RPC)
    
    try:
        semaphore = asyncio.Semaphore(POSITION_READ_CONCURRENCY)
        position_tasks = [
            read_position_limited(solana_client, pos_id, semaphore)
            for pos_id in position_ids
        ]
        position_results = await asyncio.gather(
            *position_tasks,
            return_exceptions=True,
        )

        connection, cursor = db_connect()
        try:
            for pos_id, result in zip(position_ids, position_results):
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if isinstance(result, Exception):
                    print(f"##### {timestamp} #####")
                    print(f"Position {pos_id[:8]}...: {result}")
                    continue

                if result is None:
                    continue

                snapshot = result
                title = (
                    f"#{snapshot.pos_id[:8]}... - {snapshot.symbol_0} - "
                    f"{snapshot.symbol_1}"
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
                    print(f"Position {snapshot.pos_id[:8]}...: {error}")
                    continue

                if snapshot.liquidity == 0 and remove_position_id(pos_id):
                    print(
                        f"Removed {pos_id} from positions "
                        "(liquidity is zero)."
                    )

                if summary is None:
                    continue

                try:
                    await send_telegram_report(snapshot, summary, title)
                except Exception as error:
                    print(f"##### {timestamp} #####")
                    print(f"Position {snapshot.pos_id[:8]}...: {error}")
        finally:
            connection.close()
    finally:
        await solana_client.close()


if __name__ == "__main__":
    asyncio.run(main())
