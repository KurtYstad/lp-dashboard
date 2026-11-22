#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Raydium CLMM position fee tracker for Solana."""

import asyncio
import base64
import datetime
import argparse
import hashlib
import io
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Any, Optional

import dotenv
import mariadb
import matplotlib.pyplot as plt
import numpy as np
import telegram_send
from matplotlib.dates import DateFormatter
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey


def parse_position_id(value: str) -> str:
    try:
        Pubkey.from_string(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Invalid Raydium position ID: {error}") from error
    return value


dotenv.load_dotenv()

parser = argparse.ArgumentParser(description="Raydium fee monitor")
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
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME")
TABLE_SNAPSHOT = os.getenv("TABLE_SNAPSHOT", "raydium_snapshot")
TABLE_TOTAL = os.getenv("TABLE_TOTAL", "total")
TELEGRAM_CONF = os.getenv("TELEGRAM_CONF")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
POSITION_READ_CONCURRENCY = int(os.getenv("POSITION_READ_CONCURRENCY", "4"))
SOLANA_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
RAYDIUM_CLMM_PROGRAM = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
RAYDIUM_CLMM_PROGRAM_PUBKEY = Pubkey.from_string(RAYDIUM_CLMM_PROGRAM)
USD_STABLES = {
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
WELL_KNOWN_MINTS = {
    "So11111111111111111111111111111111111111112": ("SOL", 9),
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", 6),
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": ("mSOL", 9),
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R": ("RAY", 6),
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE": ("ORCA", 6),
    "XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W": ("SPYx", 8),
    "Xs3oZwbHvqis4NYcf4YKWmEia2eC84wSiVrcYcTqpH8": ("SPCXx", 8),
    "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh": ("NVDAx", 8),
    "METAewgxyPbgwsseH8T16a39CQ5VyVxZi9zXiDPY18m": ("MPLX", 8),
    "XsueG8BtpquVJX9LVLLEGuViXUungE6WmK5YZ3p3bd1": ("CRCLx", 8),
    "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB": ("TSLAx", 8),
    "XsbEhLAtcf6HdfpFZ5xEMdqW8nfAvcsP5bdudRLJzJp": ("AAPLx", 8),
    "XsEH7wWfJJu2ZT3UCFeVfALnVA6CP5ur7Ee11KmzVpL": ("NFLXx", 8),
    "XsP7xzNPvEHS1m6qfanPUGjNmdnmsLKEoNAnHjdxxyZ": ("MSTRx", 8),
    "XsvNBAYkrDRNhA7wPHQfX3ZUXZyZLdnCQDfHZ56bzpg": ("HOODx", 8),
    "Xs8S1uUs1zvS2p7iwtsG3b6fkhpvmwz4GYU3gWAmWHZ": ("QQQx", 8),
    "XsCPL9dNWBMvFtTmwcCA5v3xWPSMEBCszbQdiLLq6aN": ("GOOGLx", 8),
    "Xs78JED6PFZxWc2wCEPspZW9kL3Se5J7L5TChKgsidH": ("STRCx", 8),
    "Xsa62P5mvPszXL1krVUnU5ar38bBSVcWAB6fmPCo5Zu": ("METAx", 8),
    "XspzcW1PRtgf6Wj92HCiZdjzKCyFekVD8P5Ueh3dRMX": ("MSFTx", 8),
    "XshPgPdXFRWB8tP1j82rebb2Q9rPgGX37RuqzohmArM": ("INTCx", 8),
    "Xsv9hRk1z5ystj9MhnA7Lq4vjSsLwzL2nxrwmwtD3re": ("GLDx", 8),
    "Xs7ZdzSHLU9ftNJsii5fCeJhoRWSC32SQGzGQtePxNu": ("COINx", 8),
    "XsqE9cRRpzxcGKDXj1BJ7Xmg4GRhZoyY1KpmGSxAWT2": ("MCDx", 8),
}

getcontext().prec = 80
Q128 = 2**128
Q64 = 2**64
TICK_ARRAY_SIZE = 60
TICK_SIZE = 168


@dataclass(frozen=True)
class TokenInfo:
    mint: str
    symbol: str
    decimals: int


@dataclass(frozen=True)
class PositionSnapshot:
    pos_id: str
    pool_id: str
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
class RaydiumPoolState:
    mint_decimals_0: int
    mint_decimals_1: int
    tick_spacing: int
    sqrt_price_x64: int
    tick_current: int
    token_mint_0: str
    token_mint_1: str
    fee_growth_global_0_x64: int
    fee_growth_global_1_x64: int


@dataclass(frozen=True)
class TickState:
    initialized: bool
    liquidity_net: int
    liquidity_gross: int
    fee_growth_outside_0_x64: int
    fee_growth_outside_1_x64: int


def anchor_discriminator(account_name: str) -> bytes:
    return hashlib.sha256(f"account:{account_name}".encode("utf-8")).digest()[:8]


def sub_in_u128(value: int, subtrahend: int) -> int:
    return (value - subtrahend) % Q128


def compute_unclaimed_fees(liquidity: int, growth_current: int, growth_last: int) -> int:
    return liquidity * sub_in_u128(growth_current, growth_last) // Q64


def pubkey_from_bytes(data: bytes) -> str:
    return str(Pubkey(data))


def tick_to_price(tick_index: int, decimals_0: int, decimals_1: int) -> Decimal:
    return Decimal("1.0001") ** Decimal(tick_index) * Decimal(10) ** Decimal(decimals_0 - decimals_1)


def sqrt_price_x64_to_price(sqrt_price_x64: int, decimals_0: int, decimals_1: int) -> Decimal:
    sqrt_price = Decimal(sqrt_price_x64) / Decimal(Q64)
    return sqrt_price**2 * Decimal(10) ** Decimal(decimals_0 - decimals_1)


def token_amount(raw_amount: int, decimals: int) -> Decimal:
    return Decimal(raw_amount) / Decimal(10**decimals)


def derive_tick_array_start_tick(tick_index: int, tick_spacing: int) -> int:
    return (tick_index // (TICK_ARRAY_SIZE * tick_spacing)) * TICK_ARRAY_SIZE * tick_spacing


def derive_tick_array_address(pool_id: str, start_tick_index: int) -> str:
    pool_pubkey = Pubkey.from_string(pool_id)
    address, _bump = Pubkey.find_program_address(
        [b"tick_array", bytes(pool_pubkey), start_tick_index.to_bytes(4, "big", signed=True)],
        RAYDIUM_CLMM_PROGRAM_PUBKEY,
    )
    return str(address)


def derive_personal_position_address(nft_mint: str) -> str:
    mint_pubkey = Pubkey.from_string(nft_mint)
    address, _bump = Pubkey.find_program_address(
        [b"position", bytes(mint_pubkey)],
        RAYDIUM_CLMM_PROGRAM_PUBKEY,
    )
    return str(address)


def safe_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def format_usd(value: Any) -> str:
    rounded = safe_decimal(value).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)
    return format(rounded, "f")


def format_token_amount(value: Any, symbol: str) -> str:
    precision = Decimal("0.000000") if (symbol or "").upper() in {"USDT", "USDC"} else Decimal("0.000000000000000000")
    return format(safe_decimal(value).quantize(precision, rounding=ROUND_HALF_UP), "f")


def read_position_ids(filename: str = "positions") -> list[str]:
    """Read Raydium personal-position account addresses from a file."""
    position_ids = []
    with open(filename, "r", encoding="utf-8") as file:
        for line_no, raw_line in enumerate(file, start=1):
            address = raw_line.strip()
            if not address or address.startswith("#"):
                continue
            try:
                Pubkey.from_string(address)
                position_ids.append(address)
            except Exception as error:
                print(f"Skipping invalid Solana address in {filename}:{line_no}: {error}")
    return position_ids


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


def db_connect():
    try:
        connection = mariadb.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
        )
    except mariadb.Error as error:
        raise RuntimeError(f"Error connecting to MariaDB: {error}") from error
    return connection, connection.cursor()


def insert_snapshot(cursor, snapshot: PositionSnapshot, timestamp: str) -> None:
    cursor.execute(
        f"INSERT INTO {TABLE_SNAPSHOT} (id, date, token_0, token_1, "
        "symbol_0, symbol_1, fee_0, fee_1, quote_0, quote_1, current_price, in_range) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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


def fetch_snapshot_history(cursor, pos_id: str):
    cursor.execute(
        f"""SELECT date, token_0, token_1, symbol_0, symbol_1,
                   fee_0, fee_1, current_price
            FROM {TABLE_SNAPSHOT}
            WHERE id = ? AND date >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            ORDER BY date ASC""",
        (pos_id,),
    )
    return list(cursor)


def insert_fee_deltas(cursor, summary: FeeSummary, pos_id: str, timestamp: str) -> None:
    for delta, token, symbol in (
        (summary.raw_delta_0, summary.token_0, summary.symbol_0),
        (summary.raw_delta_1, summary.token_1, summary.symbol_1),
    ):
        if delta > 0:
            cursor.execute(
                f"INSERT INTO {TABLE_TOTAL} (pos_id, timestamp, token, symbol, fee, chain) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (pos_id, timestamp, token, symbol, delta, "solana"),
            )


def calculate_fee_summary(cursor, snapshot: PositionSnapshot) -> Optional[FeeSummary]:
    rows = fetch_snapshot_history(cursor, snapshot.pos_id)
    if len(rows) < 2:
        return None

    dates = [row[0] for row in rows]
    prices = [safe_decimal(row[7]) for row in rows]
    fees_0 = [safe_decimal(row[5]) for row in rows]
    fees_1 = [safe_decimal(row[6]) for row in rows]
    hourly_fee_values = [
        (safe_decimal(current[5]) - safe_decimal(previous[5])) * snapshot.quote_0
        + (safe_decimal(current[6]) - safe_decimal(previous[6])) * snapshot.quote_1
        for previous, current in zip(rows, rows[1:])
    ]
    fee_last_hour = hourly_fee_values[-1] if hourly_fee_values else Decimal(0)
    fee_last_day = (fees_0[-1] - fees_0[0]) * snapshot.quote_0 + (fees_1[-1] - fees_1[0]) * snapshot.quote_1
    raw_delta_0 = fees_0[-1] - fees_0[-2]
    raw_delta_1 = fees_1[-1] - fees_1[-2]
    unsupported_valuation = snapshot.valuation_source.startswith("unsupported")
    if (fee_last_day <= 0 and not unsupported_valuation) or (
        unsupported_valuation and raw_delta_0 <= 0 and raw_delta_1 <= 0
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
        token_0=rows[-1][1],
        token_1=rows[-1][2],
        symbol_0=rows[-1][3],
        symbol_1=rows[-1][4],
    )


async def send_telegram_text(message: str) -> None:
    await telegram_send.send(conf=TELEGRAM_CONF, parse_mode="html", messages=[message])


async def send_telegram_image(image_buffer: io.BytesIO) -> None:
    await telegram_send.send(conf=TELEGRAM_CONF, images=[image_buffer])


def create_image_buffer(fig) -> io.BytesIO:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    buffer.seek(0)
    plt.close(fig)
    return buffer


async def send_telegram_report(snapshot: PositionSnapshot, summary: FeeSummary, title: str) -> None:
    valuation_warning = ""
    if snapshot.valuation_source.startswith("unsupported"):
        valuation_warning = "\n\n<b>Warning:</b>\nUSD valuation unavailable for this pool."
    message = (
        f"<b>{title}</b>\n\n"
        "<b>Fees last hour:</b>\n"
        f"{format_token_amount(summary.raw_delta_0, summary.symbol_0)} {summary.symbol_0}\n"
        f"{format_token_amount(summary.raw_delta_1, summary.symbol_1)} {summary.symbol_1}\n"
        "--------------------\n"
        f"{format_usd(summary.fee_last_hour)} USD\n\n"
        "<b>Fees last 24 hours:</b>\n"
        f"{format_usd(summary.fee_last_day)} USD\n\n"
        "<b>Total value of unclaimed fees:</b>\n"
        f"{format_usd(snapshot.total_unclaimed_usd_value)} USD{valuation_warning}"
    )
    await send_telegram_text(message)

    fig, ax = plt.subplots()
    ax.plot(summary.dates, [float(price) for price in summary.prices], marker="o", linewidth=1)
    ax.xaxis.set_major_formatter(DateFormatter("%H"))
    ax.axhline(y=float(snapshot.price_lower), color="r", linestyle="--")
    ax.axhline(y=float(snapshot.price_upper), color="g", linestyle="--")
    ax.set_title(title)
    price_image = create_image_buffer(fig)
    await send_telegram_image(price_image)
    price_image.close()

    fig, ax = plt.subplots()
    ax.bar(np.arange(1, len(summary.hourly_fee_values) + 1), [float(value) for value in summary.hourly_fee_values])
    ax.set_title("Fees earned last 24 hours")
    ax.axes.xaxis.set_ticks([])
    fee_image = create_image_buffer(fig)
    await send_telegram_image(fee_image)
    fee_image.close()


class SolanaClient:
    def __init__(self, rpc_url: str):
        self.client = AsyncClient(rpc_url)
        self.token_cache: dict[str, TokenInfo] = {}

    async def close(self) -> None:
        await self.client.close()

    async def get_account_info(self, pubkey_str: str) -> Optional[dict[str, Any]]:
        try:
            pubkey = Pubkey.from_string(pubkey_str)
            response = await self.client.get_account_info(pubkey)
            if response.value is None:
                return None
            raw_data = response.value.data
            if isinstance(raw_data, str):
                raw_data = base64.b64decode(raw_data)
            return {
                "data": raw_data,
                "lamports": response.value.lamports,
                "owner": str(response.value.owner),
            }
        except Exception as error:
            print(f"Error fetching account {pubkey_str[:8]}...: {error}")
            return None

    async def get_token_info(self, mint: str) -> TokenInfo:
        if mint in self.token_cache:
            return self.token_cache[mint]
        if mint in WELL_KNOWN_MINTS:
            symbol, decimals = WELL_KNOWN_MINTS[mint]
            info = TokenInfo(mint, symbol, decimals)
        else:
            account_info = await self.get_account_info(mint)
            decimals = account_info["data"][44] if account_info and len(account_info["data"]) > 44 else 6
            info = TokenInfo(mint, f"Token_{mint[:4]}", decimals)
        self.token_cache[mint] = info
        return info


def parse_position_data(data: bytes) -> Optional[dict[str, Any]]:
    """Parse the Raydium PersonalPositionState account fields used by the tracker."""
    if len(data) < 145 or data[:8] != anchor_discriminator("PersonalPositionState"):
        return None
    try:
        return {
            "nft_mint": str(Pubkey(data[9:41])),
            "pool_id": str(Pubkey(data[41:73])),
            "tick_lower": int.from_bytes(data[73:77], "little", signed=True),
            "tick_upper": int.from_bytes(data[77:81], "little", signed=True),
            "liquidity": int.from_bytes(data[81:97], "little"),
            "fee_growth_inside_0": int.from_bytes(data[97:113], "little"),
            "fee_growth_inside_1": int.from_bytes(data[113:129], "little"),
            "fee_owed_0": int.from_bytes(data[129:137], "little"),
            "fee_owed_1": int.from_bytes(data[137:145], "little"),
        }
    except Exception as error:
        print(f"Error parsing Raydium position data: {error}")
        return None


def parse_pool_data(data: bytes) -> Optional[RaydiumPoolState]:
    """Parse Raydium PoolState fields using its Anchor account layout."""
    if len(data) < 309 or data[:8] != anchor_discriminator("PoolState"):
        return None
    try:
        return RaydiumPoolState(
            mint_decimals_0=data[233],
            mint_decimals_1=data[234],
            tick_spacing=int.from_bytes(data[235:237], "little"),
            sqrt_price_x64=int.from_bytes(data[253:269], "little"),
            tick_current=int.from_bytes(data[269:273], "little", signed=True),
            token_mint_0=pubkey_from_bytes(data[73:105]),
            token_mint_1=pubkey_from_bytes(data[105:137]),
            fee_growth_global_0_x64=int.from_bytes(data[277:293], "little"),
            fee_growth_global_1_x64=int.from_bytes(data[293:309], "little"),
        )
    except Exception as error:
        print(f"Error parsing Raydium pool data: {error}")
        return None


def parse_tick_array_data(data: bytes, tick_index: int, tick_spacing: int) -> Optional[TickState]:
    """Read one tick from Raydium's fixed-size TickArrayState account."""
    header_size = 44
    if (
        tick_spacing <= 0
        or len(data) < header_size + TICK_ARRAY_SIZE * TICK_SIZE
        or data[:8] != anchor_discriminator("TickArrayState")
    ):
        return None
    start_tick_index = int.from_bytes(data[40:44], "little", signed=True)
    offset_index = (tick_index - start_tick_index) // tick_spacing
    if offset_index < 0 or offset_index >= TICK_ARRAY_SIZE:
        return None
    offset = header_size + offset_index * TICK_SIZE
    if int.from_bytes(data[offset:offset + 4], "little", signed=True) != tick_index:
        return None

    liquidity_gross = int.from_bytes(data[offset + 20:offset + 36], "little")
    return TickState(
        initialized=liquidity_gross > 0,
        liquidity_net=int.from_bytes(data[offset + 4:offset + 20], "little", signed=True),
        liquidity_gross=liquidity_gross,
        fee_growth_outside_0_x64=int.from_bytes(data[offset + 36:offset + 52], "little"),
        fee_growth_outside_1_x64=int.from_bytes(data[offset + 52:offset + 68], "little"),
    )


def compute_fee_growth_inside(
    pool: RaydiumPoolState,
    tick_lower: TickState,
    tick_upper: TickState,
    lower_index: int,
    upper_index: int,
) -> tuple[int, int]:
    if pool.tick_current >= lower_index:
        growth_below_0 = tick_lower.fee_growth_outside_0_x64
        growth_below_1 = tick_lower.fee_growth_outside_1_x64
    else:
        growth_below_0 = sub_in_u128(pool.fee_growth_global_0_x64, tick_lower.fee_growth_outside_0_x64)
        growth_below_1 = sub_in_u128(pool.fee_growth_global_1_x64, tick_lower.fee_growth_outside_1_x64)
    if pool.tick_current < upper_index:
        growth_above_0 = tick_upper.fee_growth_outside_0_x64
        growth_above_1 = tick_upper.fee_growth_outside_1_x64
    else:
        growth_above_0 = sub_in_u128(pool.fee_growth_global_0_x64, tick_upper.fee_growth_outside_0_x64)
        growth_above_1 = sub_in_u128(pool.fee_growth_global_1_x64, tick_upper.fee_growth_outside_1_x64)
    return (
        sub_in_u128(sub_in_u128(pool.fee_growth_global_0_x64, growth_below_0), growth_above_0),
        sub_in_u128(sub_in_u128(pool.fee_growth_global_1_x64, growth_below_1), growth_above_1),
    )


async def read_position(solana_client: SolanaClient, position_pubkey: str) -> Optional[PositionSnapshot]:
    try:
        position_info = await solana_client.get_account_info(position_pubkey)
        if not position_info:
            print(f"Position {position_pubkey[:8]}... not found or could not be fetched")
            return None
        if position_info["owner"] != RAYDIUM_CLMM_PROGRAM:
            nft_mint = position_pubkey
            position_pubkey = derive_personal_position_address(nft_mint)
            position_info = await solana_client.get_account_info(position_pubkey)
            if not position_info or position_info["owner"] != RAYDIUM_CLMM_PROGRAM:
                print(
                    f"Could not find a Raydium position state for NFT mint {nft_mint[:8]}... "
                    f"(mint owner: {position_info['owner'] if position_info else 'no derived account'})"
                )
                return None

        position = parse_position_data(position_info["data"])
        if not position:
            print(f"Could not parse Raydium position account ({len(position_info['data'])} bytes)")
            return None
        pool_info = await solana_client.get_account_info(position["pool_id"])
        if not pool_info or pool_info["owner"] != RAYDIUM_CLMM_PROGRAM:
            print(f"Could not fetch a valid Raydium pool for position {position_pubkey[:8]}...")
            return None
        pool = parse_pool_data(pool_info["data"])
        if not pool or pool.tick_spacing == 0:
            return None

        lower_start = derive_tick_array_start_tick(position["tick_lower"], pool.tick_spacing)
        upper_start = derive_tick_array_start_tick(position["tick_upper"], pool.tick_spacing)
        lower_address = derive_tick_array_address(position["pool_id"], lower_start)
        upper_address = derive_tick_array_address(position["pool_id"], upper_start)
        lower_info, upper_info = await asyncio.gather(
            solana_client.get_account_info(lower_address),
            solana_client.get_account_info(upper_address),
        )
        if not lower_info or not upper_info:
            print("Could not fetch Raydium lower/upper tick arrays")
            return None
        if any(
            info["owner"] != RAYDIUM_CLMM_PROGRAM
            or str(Pubkey(info["data"][8:40])) != position["pool_id"]
            for info in (lower_info, upper_info)
        ):
            print("Raydium tick array owner or pool id does not match the position")
            return None

        tick_lower = parse_tick_array_data(lower_info["data"], position["tick_lower"], pool.tick_spacing)
        tick_upper = parse_tick_array_data(upper_info["data"], position["tick_upper"], pool.tick_spacing)
        if not tick_lower or not tick_upper or not tick_lower.initialized or not tick_upper.initialized:
            print("Lower or upper Raydium tick is not initialized")
            return None

        growth_inside_0, growth_inside_1 = compute_fee_growth_inside(
            pool, tick_lower, tick_upper, position["tick_lower"], position["tick_upper"]
        )
        unclaimed_0_raw = position["fee_owed_0"] + compute_unclaimed_fees(
            position["liquidity"], growth_inside_0, position["fee_growth_inside_0"]
        )
        unclaimed_1_raw = position["fee_owed_1"] + compute_unclaimed_fees(
            position["liquidity"], growth_inside_1, position["fee_growth_inside_1"]
        )

        info_0, info_1 = await asyncio.gather(
            solana_client.get_token_info(pool.token_mint_0),
            solana_client.get_token_info(pool.token_mint_1),
        )
        token_0 = TokenInfo(info_0.mint, info_0.symbol, pool.mint_decimals_0)
        token_1 = TokenInfo(info_1.mint, info_1.symbol, pool.mint_decimals_1)
        fee_0 = token_amount(unclaimed_0_raw, token_0.decimals)
        fee_1 = token_amount(unclaimed_1_raw, token_1.decimals)
        current_price = sqrt_price_x64_to_price(pool.sqrt_price_x64, token_0.decimals, token_1.decimals)
        price_lower = tick_to_price(position["tick_lower"], token_0.decimals, token_1.decimals)
        price_upper = tick_to_price(position["tick_upper"], token_0.decimals, token_1.decimals)

        quote_0 = Decimal("0")
        quote_1 = Decimal("0")
        valuation_source = "unsupported: no stable side"
        stable_mints = set(USD_STABLES.values())
        if pool.token_mint_0 in stable_mints:
            quote_0 = Decimal("1")
            quote_1 = Decimal("1") / current_price if current_price else Decimal("0")
            valuation_source = "derived_from_token_0_stable"
        elif pool.token_mint_1 in stable_mints:
            quote_1 = Decimal("1")
            quote_0 = current_price
            valuation_source = "derived_from_token_1_stable"

        return PositionSnapshot(
            pos_id=position_pubkey,
            pool_id=position["pool_id"],
            token_0=pool.token_mint_0,
            token_1=pool.token_mint_1,
            symbol_0=token_0.symbol,
            symbol_1=token_1.symbol,
            fee_0=fee_0,
            fee_1=fee_1,
            quote_0=quote_0,
            quote_1=quote_1,
            price_lower=price_lower,
            price_upper=price_upper,
            current_price=current_price,
            fee_percent=Decimal("0"),
            liquidity=position["liquidity"],
            total_unclaimed_usd_value=fee_0 * quote_0 + fee_1 * quote_1,
            in_range=int(position["tick_lower"] <= pool.tick_current < position["tick_upper"]),
            valuation_source=valuation_source,
        )
    except Exception as error:
        print(f"Error reading position {position_pubkey[:8]}...: {error}")
        return None


async def read_position_limited(
    solana_client: SolanaClient,
    position_pubkey: str,
    semaphore: asyncio.Semaphore,
) -> Optional[PositionSnapshot]:
    async with semaphore:
        return await read_position(solana_client, position_pubkey)


async def main() -> None:
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
        results = await asyncio.gather(
            *(read_position_limited(solana_client, pos_id, semaphore) for pos_id in position_ids),
            return_exceptions=True,
        )
        connection, cursor = db_connect()
        try:
            for pos_id, result in zip(position_ids, results):
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if isinstance(result, Exception):
                    print(f"##### {timestamp} #####\nPosition {pos_id[:8]}...: {result}")
                    continue
                if result is None:
                    continue

                snapshot = result
                title = f"#{snapshot.pos_id[:8]}... - {snapshot.symbol_0} - {snapshot.symbol_1}"
                try:
                    insert_snapshot(cursor, snapshot, timestamp)
                    summary = calculate_fee_summary(cursor, snapshot)
                    if summary is not None:
                        insert_fee_deltas(cursor, summary, snapshot.pos_id, timestamp)
                    connection.commit()
                except Exception as error:
                    connection.rollback()
                    print(f"##### {timestamp} #####\nPosition {snapshot.pos_id[:8]}...: {error}")
                    continue

                if snapshot.liquidity == 0 and remove_position_id(pos_id):
                    print(
                        f"Removed {pos_id} from positions "
                        "(liquidity is zero)."
                    )

                if summary is not None:
                    try:
                        await send_telegram_report(snapshot, summary, title)
                    except Exception as error:
                        print(f"##### {timestamp} #####\nPosition {snapshot.pos_id[:8]}...: {error}")
        finally:
            connection.close()
    finally:
        await solana_client.close()


if __name__ == "__main__":
    asyncio.run(main())
