#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TypedDict

import mariadb
import pandas as pd
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

PENDING_FEE_TABLES = {
    "aero": "base",
    "arbitrum": "arbitrum",
    "base": "base",
    "bsc": "bsc",
    "cake": "bsc",
    "ethereum": "ethereum",
    "optimism": "optimism",
    "orca": "solana",
    "polygon": "polygon",
    "raydium": "solana",
    "robinhood": "robinhood",
}

POSITION_PROTOCOLS = {
    "aero": "Aero Slipstream",
    "arbitrum": "Uniswap",
    "base": "Uniswap",
    "bsc": "Uniswap",
    "cake": "Pancakeswap",
    "ethereum": "Uniswap",
    "optimism": "Uniswap",
    "orca": "Orca",
    "polygon": "Uniswap",
    "raydium": "Raydium",
    "robinhood": "Uniswap",
}


class CollectedFee(TypedDict):
    token: str
    symbol: str
    amount: Decimal


class PositionPerformanceData(TypedDict):
    date: object
    token_0: str
    amount_0: Decimal
    token_1: str
    amount_1: Decimal
    entry_value_usd: Decimal | None
    entry_value_eur: Decimal | None
    accrued_fees: list[CollectedFee]


def _connect() -> mariadb.Connection:
    db_password = os.getenv("DB_PASSWORD")
    if not db_password:
        raise ValueError("DB_PASSWORD in .env-Datei fehlt.")

    return mariadb.connect(
        user=os.getenv("DB_USER", "lpdashboard"),
        password=db_password,
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_NAME", "uniswap"),
    )


def get_dashboard_filters() -> tuple[list[str], list[str], list[str]]:
    """Lädt verfügbare Blockchains, Tokens und Positions-IDs."""
    connection = _connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT DISTINCT chain, symbol
                FROM total
                WHERE chain IS NOT NULL AND symbol IS NOT NULL
                ORDER BY chain, symbol
                """
            )
            rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT DISTINCT pos_id
                FROM total
                WHERE pos_id IS NOT NULL
                ORDER BY pos_id
                """
            )
            position_rows = cursor.fetchall()
        finally:
            cursor.close()
    finally:
        connection.close()

    return (
        sorted({str(chain) for chain, _ in rows}),
        sorted({str(symbol).upper() for _, symbol in rows}),
        [str(pos_id) for (pos_id,) in position_rows],
    )


def get_dashboard_dataframe() -> pd.DataFrame:
    """Lädt alle Erträge aus MariaDB."""
    query = """
        SELECT `timestamp`, chain, pos_id, token AS token_address,
               symbol AS token_symbol, fee AS amount
        FROM total
    """

    connection = _connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(query)
            rows = cursor.fetchall()
        finally:
            cursor.close()
    finally:
        connection.close()

    dataframe = pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "chain",
            "pos_id",
            "token_address",
            "token_symbol",
            "amount",
        ],
    )
    dataframe["timestamp"] = pd.to_datetime(dataframe["timestamp"])
    dataframe["amount"] = pd.to_numeric(dataframe["amount"], errors="raise")
    return dataframe


def get_recent_position_ids() -> set[str]:
    """Lädt Positionen mit Einträgen in den überwachten Tabellen der letzten Stunde."""
    return {position_id for _, position_id in get_recent_position_sources()}


def get_recent_position_sources() -> set[tuple[str, str]]:
    """Lädt Quelltabelle und Positions-ID für Positionen der letzten Stunde."""
    connection = _connect()
    position_sources = set()
    try:
        cursor = connection.cursor()
        try:
            for table in PENDING_FEE_TABLES:
                cursor.execute(
                    f"""
                    SELECT DISTINCT id
                    FROM `{table}`
                    WHERE id IS NOT NULL
                      AND `date` >= NOW() - INTERVAL 60 MINUTE
                    """
                )
                position_sources.update(
                    (table, str(row[0])) for row in cursor.fetchall()
                )
        finally:
            cursor.close()
    finally:
        connection.close()

    return position_sources


def get_position_collected_fees(
    position_id: str,
    chain: str,
) -> list[CollectedFee]:
    chain_aliases = {
        "eth": ("eth", "ethereum"),
        "ethereum": ("eth", "ethereum"),
        "robinhood": ("robinhood", "robinhood chain", "robinhoodchain"),
        "solana": ("solana",),
    }
    chain_values = chain_aliases.get(chain.strip().lower(), (chain.strip().lower(),))
    placeholders = ", ".join("?" for _ in chain_values)

    connection = _connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT `token`, `symbol`, SUM(`fee`)
                FROM `total`
                WHERE `pos_id` = ?
                  AND LOWER(`chain`) IN ({placeholders})
                  AND `token` IS NOT NULL
                  AND `fee` IS NOT NULL
                GROUP BY `token`, `symbol`
                """,
                (position_id, *chain_values),
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()
    finally:
        connection.close()

    return [
        CollectedFee(
            token=str(token),
            symbol=str(symbol or ""),
            amount=Decimal(str(amount or 0)),
        )
        for token, symbol, amount in rows
    ]


def get_position_detail_snapshot(table: str, position_id: str) -> dict[str, object] | None:
    """Lädt den letzten Snapshot, sofern die Position noch innerhalb der Stunde aktiv ist."""
    if table not in PENDING_FEE_TABLES:
        raise ValueError(f"Unbekannte Positionstabelle: {table}")

    reward_columns = {
        "aero": "reward_token, reward_symbol, reward_amount, reward_quote",
        "cake": "token_2, symbol_2, fee_2, quote_2",
    }.get(table, "NULL, NULL, NULL, NULL")
    connection = _connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT id, `date`, token_0, symbol_0, fee_0, quote_0,
                       token_1, symbol_1, fee_1, quote_1,
                       current_price, in_range, {reward_columns}
                FROM `{table}`
                WHERE id = ?
                  AND `date` >= NOW() - INTERVAL 60 MINUTE
                ORDER BY `date` DESC
                LIMIT 1
                """,
                (position_id,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
    finally:
        connection.close()

    if row is None:
        return None

    return {
        "id": str(row[0]),
        "date": row[1],
        "token_0": str(row[2] or ""),
        "symbol_0": str(row[3] or ""),
        "fee_0": Decimal(str(row[4] or 0)),
        "quote_0": Decimal(str(row[5] or 0)),
        "token_1": str(row[6] or ""),
        "symbol_1": str(row[7] or ""),
        "fee_1": Decimal(str(row[8] or 0)),
        "quote_1": Decimal(str(row[9] or 0)),
        "current_price": Decimal(str(row[10] or 0)),
        "in_range": bool(row[11]),
        "reward_token": str(row[12] or ""),
        "reward_symbol": str(row[13] or ""),
        "reward_amount": Decimal(str(row[14] or 0)),
        "reward_quote": Decimal(str(row[15] or 0)),
    }


def get_position_snapshot_count(table: str, position_id: str) -> int:
    if table not in PENDING_FEE_TABLES:
        raise ValueError(f"Unbekannte Positionstabelle: {table}")

    connection = _connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"SELECT COUNT(*) FROM `{table}` WHERE id = ?",
                (position_id,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
    finally:
        connection.close()

    if row is None:
        raise RuntimeError("Die Datenbank hat keine Snapshot-Anzahl zurückgegeben.")
    return int(row[0])


def get_position_price_history(
    table: str,
    position_id: str,
) -> list[tuple[object, Decimal]]:
    """Lädt die Kurssnapshots einer Position aus den letzten 24 Stunden."""
    if table not in PENDING_FEE_TABLES:
        raise ValueError(f"Unbekannte Positionstabelle: {table}")

    connection = _connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT `date`, `current_price`
                FROM `{table}`
                WHERE `id` = ?
                  AND `date` >= NOW() - INTERVAL 24 HOUR
                  AND `current_price` IS NOT NULL
                  AND `current_price` > 0
                ORDER BY `date` ASC
                """,
                (position_id,),
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()
    finally:
        connection.close()

    return [(date, Decimal(str(price))) for date, price in rows]


def get_position_hourly_fees(
    table: str,
    position_id: str,
    chain: str,
) -> tuple[list[tuple[str, str, str, Decimal]], str, int]:
    """Bewertet Positions-Erträge mit dem zeitnahen Snapshot-Quote in USD."""
    if table not in PENDING_FEE_TABLES:
        raise ValueError(f"Unbekannte Positionstabelle: {table}")

    chain_aliases = {
        "eth": ("eth", "ethereum"),
        "ethereum": ("eth", "ethereum"),
        "robinhood": ("robinhood", "robinhood chain", "robinhoodchain"),
        "solana": ("solana",),
    }
    chain_key = chain.strip().lower()
    chain_values = chain_aliases.get(chain_key, (chain_key,))
    placeholders = ", ".join("?" for _ in chain_values)
    reward_columns = {
        "aero": "`reward_token`, `reward_quote`",
        "cake": "`token_2`, `quote_2`",
    }.get(table, "NULL, NULL")

    connection = _connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT `timestamp`, `token`,
                       COALESCE(NULLIF(`symbol`, ''), 'Unbekannt'), `fee`
                FROM `total`
                WHERE `pos_id` = ?
                  AND LOWER(`chain`) IN ({placeholders})
                  AND `timestamp` >= NOW() - INTERVAL 24 HOUR
                  AND `fee` IS NOT NULL
                  AND `token` IS NOT NULL
                ORDER BY `timestamp` ASC
                """,
                (position_id, *chain_values),
            )
            fee_rows = cursor.fetchall()
            cursor.execute(
                f"""
                SELECT `date`, `token_0`, `quote_0`, `token_1`, `quote_1`,
                       {reward_columns}
                FROM `{table}`
                WHERE `id` = ?
                  AND `date` >= NOW() - INTERVAL 24 HOUR
                ORDER BY `date` ASC
                """,
                (position_id,),
            )
            snapshots = cursor.fetchall()
            cursor.execute(
                f"""
                SELECT `date`, `token_0`, `quote_0`, `token_1`, `quote_1`,
                       {reward_columns}
                FROM `{table}`
                WHERE `id` = ?
                  AND `date` < NOW() - INTERVAL 24 HOUR
                ORDER BY `date` DESC
                LIMIT 1
                """,
                (position_id,),
            )
            previous_snapshot = cursor.fetchone()
            if previous_snapshot is not None:
                snapshots.insert(0, previous_snapshot)
            cursor.execute("SELECT DATE_FORMAT(NOW(), '%Y-%m-%d %H:00:00')")
            current_hour_row = cursor.fetchone()
        finally:
            cursor.close()
    finally:
        connection.close()

    if current_hour_row is None:
        raise RuntimeError("Die aktuelle Datenbankzeit konnte nicht ermittelt werden.")

    def token_key(token: object) -> str:
        address = str(token or "")
        return address if chain_key == "solana" else address.lower()

    snapshot_quotes: list[tuple[datetime, dict[str, Decimal]]] = []
    for snapshot in snapshots:
        quotes = {}
        for token, quote in (
            (snapshot[1], snapshot[2]),
            (snapshot[3], snapshot[4]),
            (snapshot[5], snapshot[6]),
        ):
            if token and quote is not None:
                quotes[token_key(token)] = Decimal(str(quote))
        snapshot_quotes.append((snapshot[0], quotes))

    hourly_token_values: dict[tuple[str, str, str], Decimal] = {}
    unpriced_count = 0
    snapshot_index = 0
    for timestamp, token, symbol, amount in fee_rows:
        while (
            snapshot_index + 1 < len(snapshot_quotes)
            and snapshot_quotes[snapshot_index + 1][0] <= timestamp
        ):
            snapshot_index += 1

        quote = None
        if (
            snapshot_quotes
            and snapshot_quotes[snapshot_index][0] <= timestamp
            and timestamp - snapshot_quotes[snapshot_index][0] <= timedelta(hours=1)
        ):
            quote = snapshot_quotes[snapshot_index][1].get(token_key(token))
        if quote is None or quote <= 0:
            unpriced_count += 1
            continue

        hour = timestamp.strftime("%Y-%m-%d %H:00:00")
        symbol = str(symbol or "Unbekannt")
        key = (hour, token_key(token), symbol)
        hourly_token_values[key] = (
            hourly_token_values.get(key, Decimal(0))
            + Decimal(str(amount)) * quote
        )

    return (
        [
            (hour, token, symbol, amount)
            for (hour, token, symbol), amount in sorted(hourly_token_values.items())
        ],
        str(current_hour_row[0]),
        unpriced_count,
    )


def get_position_performance_data(
    table: str,
    position_id: str,
    chain: str,
    token_0: str,
    token_1: str,
) -> PositionPerformanceData | None:
    if table not in PENDING_FEE_TABLES:
        raise ValueError(f"Unbekannte Positionstabelle: {table}")

    is_solana = chain.strip().lower() == "solana"
    token_comparison = (
        "`token_0` = ? AND `token_1` = ?"
        if is_solana
        else "LOWER(`token_0`) = LOWER(?) AND LOWER(`token_1`) = LOWER(?)"
    )

    connection = _connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT `date`, token_0, amount_0, token_1, amount_1,
                       entry_value_usd, entry_value_eur
                FROM `positions`
                WHERE `source_table` = ? AND `id` = ? AND `status` = 'complete'
                  AND {token_comparison}
                ORDER BY `uid` DESC
                LIMIT 1
                """,
                (table, position_id, token_0, token_1),
            )
            row = cursor.fetchone()
            if row is None:
                return None
        finally:
            cursor.close()
    finally:
        connection.close()

    accrued_fees = get_position_collected_fees(position_id, chain)
    return {
        "date": row[0],
        "token_0": str(row[1]),
        "amount_0": Decimal(str(row[2])),
        "token_1": str(row[3]),
        "amount_1": Decimal(str(row[4])),
        "entry_value_usd": Decimal(str(row[5])) if row[5] is not None else None,
        "entry_value_eur": Decimal(str(row[6])) if row[6] is not None else None,
        "accrued_fees": accrued_fees,
    }


def get_active_pending_fees() -> list[dict[str, str | Decimal]]:
    """Lädt die jüngsten Pending-Fees der Positionen mit Updates in der letzten Stunde."""
    connection = _connect()
    pending_fees = []
    try:
        cursor = connection.cursor()
        try:
            for table, chain in PENDING_FEE_TABLES.items():
                if table == "cake":
                    reward_columns = "token_2, symbol_2, fee_2"
                elif table == "aero":
                    reward_columns = "reward_token, reward_symbol, reward_amount"
                else:
                    reward_columns = "NULL, NULL, NULL"

                cursor.execute(
                    f"""
                    SELECT id, `date`, token_0, symbol_0, fee_0,
                           token_1, symbol_1, fee_1,
                           {reward_columns}
                    FROM `{table}`
                    WHERE id IS NOT NULL
                      AND `date` >= NOW() - INTERVAL 60 MINUTE
                    ORDER BY `date` DESC
                    """
                )
                rows = cursor.fetchall()
                seen_position_ids = set()
                for row in rows:
                    position_id = row[0]
                    if position_id in seen_position_ids:
                        continue
                    seen_position_ids.add(position_id)

                    for token_address, symbol, amount in (
                        (row[2], row[3], row[4]),
                        (row[5], row[6], row[7]),
                        (row[8], row[9], row[10]),
                    ):
                        if amount is not None:
                            pending_fees.append(
                                {
                                    "chain": chain,
                                    "token_address": str(token_address or ""),
                                    "symbol": str(symbol or ""),
                                    "amount": Decimal(str(amount)),
                                }
                            )
        finally:
            cursor.close()
    finally:
        connection.close()

    return pending_fees
