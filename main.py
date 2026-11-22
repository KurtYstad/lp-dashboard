#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal, TypedDict
from urllib.parse import urlsplit

import pandas as pd
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cointracking import add_trust_wallet_icons, get_cointracking_balances
from database import (
    PENDING_FEE_TABLES,
    POSITION_PROTOCOLS,
    get_active_pending_fees,
    get_dashboard_dataframe,
    get_dashboard_filters,
    get_position_collected_fees,
    get_position_detail_snapshot,
    get_position_hourly_fees,
    get_position_price_history,
    get_position_snapshot_count,
    get_recent_position_sources,
)
from number_formatting import format_de_number
from pricing import get_eur_usd_rate, get_token_usd_price, normalize_chain
from position_details import (
    ClosedPositionError,
    UnsupportedPositionError,
    _base58_decode,
    get_position_details,
)

logger = logging.getLogger(__name__)

APP_DIRECTORY = Path(__file__).resolve().parent
POSITION_NOTIFY_CONFIGS = {
    "uniswap_ethereum": (
        "Uniswap (Ethereum)",
        "ethereum",
        "notify/uniswap/ethereum/uniswap.py",
    ),
    "uniswap_arbitrum": (
        "Uniswap (Arbitrum)",
        "arbitrum",
        "notify/uniswap/arbitrum/uniswap.py",
    ),
    "uniswap_polygon": (
        "Uniswap (Polygon)",
        "polygon",
        "notify/uniswap/polygon/uniswap.py",
    ),
    "uniswap_base": (
        "Uniswap (Base)",
        "base",
        "notify/uniswap/base/uniswap.py",
    ),
    "uniswap_robinhood": (
        "Uniswap (Robinhood)",
        "robinhood",
        "notify/uniswap/robinhood/uniswap.py",
    ),
    "aero": ("Aerodrome (Base)", "aero", "notify/aero/aero.py"),
    "cake": ("PancakeSwap (BNB Chain)", "cake", "notify/pancakeswap/cake.py"),
    "orca": ("Orca (Solana)", "orca", "notify/orca/orca.py"),
    "raydium": ("Raydium (Solana)", "raydium", "notify/raydium/raydium.py"),
}
POSITION_NOTIFY_TIMEOUT_SECONDS = 300
app = FastAPI()
app.mount(
    "/icons",
    StaticFiles(directory=str(APP_DIRECTORY / "icons"), check_dir=False),
    name="icons",
)
templates = Jinja2Templates(directory=str(APP_DIRECTORY / "templates"))
templates.env.filters["de_number"] = format_de_number


@app.get("/favicon.ico", include_in_schema=False)
def favicon(request: Request):
    return RedirectResponse(url=request.url_for("icons", path="favicon.svg"))


PERIOD_SETTINGS = {
    "D": "D",
    "W": "W",
    "M": "ME",
    "Y": "YE",
}

PERIOD_LABEL_FORMATS = {
    "D": "%d.%m.%Y",
    "W": "%d.%m.%Y",
    "M": "%m.%Y",
    "Y": "%Y",
}

OPEN_POSITIONS_FILTER = "__open_positions__"
USDT_SYMBOLS = {"usd₮0", "usdt", "usdt0"}
USDT_GROUP_LABEL = "USD₮0 / USDT / USDT0"


class _TokenHoldingRow(TypedDict):
    chain: str
    chain_label: str
    amount: Decimal
    symbols: list[str]
    value: Decimal
    complete: bool
    icons: list[str]


class _TokenHoldingGroup(TypedDict):
    symbol: str
    chains: dict[str, _TokenHoldingRow]
    icons: list[str]


class _TokenHoldingDisplayGroup(TypedDict):
    symbol: str
    icons: list[str]
    rows: list[_TokenHoldingRow]
    value: Decimal | None


def _position_source_map(
    position_sources: set[tuple[str, str]],
) -> dict[tuple[str, str], list[dict[str, str]]]:
    sources_by_position: dict[tuple[str, str], list[dict[str, str]]] = {}
    for table, position_id in sorted(position_sources):
        chain_key = normalize_chain(PENDING_FEE_TABLES[table])
        sources_by_position.setdefault((chain_key, position_id), []).append(
            {
                "table": table,
                "protocol": POSITION_PROTOCOLS[table],
            }
        )
    return sources_by_position


def _position_id_text(value: object) -> str | None:
    if pd.isna(value):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, Decimal)):
        numeric_value = Decimal(str(value))
        if (
            numeric_value.is_finite()
            and numeric_value == numeric_value.to_integral_value()
        ):
            return str(int(numeric_value))
    return str(value)


def _position_join_key(chain: object, position_id: object) -> tuple[str, str] | None:
    if pd.isna(chain) or position_id is None:
        return None
    try:
        return normalize_chain(str(chain)), str(position_id)
    except ValueError:
        return None


def _price_key(chain: str, token_address: str) -> tuple[str, str]:
    chain_key = normalize_chain(chain)
    address_key = token_address if chain_key == "solana" else token_address.lower()
    return chain_key, address_key


def _get_pending_total(currency: str) -> tuple[float | None, list[str]]:
    pending_fees = get_active_pending_fees()
    total_usd = Decimal(0)
    price_errors = set()

    for fee in pending_fees:
        amount = Decimal(str(fee["amount"]))
        if amount == 0:
            continue

        chain = str(fee["chain"])
        token_address = str(fee["token_address"])
        price_key = _price_key(chain, token_address)
        try:
            token_price = get_token_usd_price(token_address, price_key[0])
            total_usd += amount * Decimal(str(token_price))
        except Exception:
            logger.exception(
                "Could not load pending fee price for token %s on %s",
                token_address,
                price_key[0],
            )
            price_errors.add(f"{price_key[0]}: {token_address}")

    if price_errors:
        return None, sorted(price_errors)

    if currency == "EUR":
        try:
            total_usd /= Decimal(str(get_eur_usd_rate()))
        except Exception:
            logger.exception("Could not load EUR/USD rate for pending fees")
            return None, ["EUR/USD"]

    return float(total_usd), []


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    try:
        blockchains, tokens, position_ids = get_dashboard_filters()
        filter_error = None
    except Exception:
        logger.exception("Could not load dashboard filter options")
        blockchains, tokens, position_ids = [], [], []
        filter_error = "Filteroptionen konnten nicht aus der Datenbank geladen werden."

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "blockchains": blockchains,
            "tokens": tokens,
            "position_ids": position_ids,
            "filter_error": filter_error,
            "position_notifiers": [
                {
                    "key": key,
                    "label": config[0],
                    "is_uniswap": key.startswith("uniswap_"),
                }
                for key, config in POSITION_NOTIFY_CONFIGS.items()
            ],
        },
    )


def _position_id_error(protocol: str, position_id: str) -> str | None:
    if protocol.startswith("uniswap_") or protocol in {"aero", "cake"}:
        if not position_id.isascii() or not position_id.isdigit():
            return "Die Positions-ID muss eine positive Zahl sein."
        if len(position_id) > 78:
            return "Die Positions-ID liegt außerhalb des gültigen Bereichs."
        if not 0 < int(position_id) < 2**256:
            return "Die Positions-ID liegt außerhalb des gültigen Bereichs."
        return None

    if protocol == "orca" and len(position_id) != 44:
        return "Orca-Positionsadressen müssen 44 Zeichen lang sein."
    if protocol == "raydium" and len(position_id) not in {43, 44}:
        return "Raydium-Positionsadressen müssen 43 oder 44 Zeichen lang sein."
    try:
        decoded_position_id = _base58_decode(position_id)
    except ValueError:
        return "Die Positions-ID ist keine gültige Base58-Adresse."
    if len(decoded_position_id) != 32:
        return "Die Positions-ID muss eine gültige 32-Byte-Adresse sein."
    return None


def _request_has_same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return False

    origin_parts = urlsplit(origin)
    expected_host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    expected_scheme = (
        request.headers.get("x-forwarded-proto") or request.url.scheme
    ).split(",")[0].strip()
    return (
        origin_parts.scheme == expected_scheme
        and origin_parts.netloc.casefold() == expected_host.casefold()
    )


def _append_position_entry(
    positions_file: Path,
    position_id: str,
    version: str | None,
    is_uniswap: bool,
) -> bool:
    entry_to_add = f"{version}:{position_id}" if is_uniswap and version else position_id
    with positions_file.open("a+", encoding="utf-8", newline="") as file:
        file.seek(0)
        lines = file.readlines()
        unversioned_matches = []
        has_same_version = False

        for index, line in enumerate(lines):
            existing_entry = line.strip()
            if not existing_entry or existing_entry.startswith("#"):
                continue
            existing_version, separator, existing_id = existing_entry.partition(":")
            if not separator:
                existing_version = None
                existing_id = existing_entry
            else:
                existing_version = existing_version.strip().lower()
                existing_id = existing_id.strip()

            if existing_id != position_id:
                continue
            if not is_uniswap:
                return False
            if existing_version is None:
                unversioned_matches.append(index)
            elif existing_version == version:
                has_same_version = True
            elif version is None:
                return False

        if unversioned_matches and version is not None:
            for index in unversioned_matches:
                line_ending = (
                    "\r\n"
                    if lines[index].endswith("\r\n")
                    else "\n"
                    if lines[index].endswith("\n")
                    else "\r"
                    if lines[index].endswith("\r")
                    else ""
                )
                lines[index] = f"{version}:{position_id}{line_ending}"
            file.seek(0)
            file.truncate()
            file.writelines(lines)
            file.flush()
            return True
        if unversioned_matches or has_same_version:
            return False

        file.seek(0, 2)
        if lines and not lines[-1].endswith(("\n", "\r")):
            file.write("\n")
        file.write(f"{entry_to_add}\n")
        file.flush()
    return True


async def _run_position_notifier(
    script: Path,
    position_id: str,
    version: str | None,
) -> tuple[int | None, bool]:
    command = [sys.executable, str(script), position_id]
    if version is not None:
        command.append(version)

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(script.parent),
    )
    try:
        return_code = await asyncio.wait_for(
            process.wait(),
            timeout=POSITION_NOTIFY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        return None, True
    except asyncio.CancelledError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    return return_code, False


@app.post("/add-position", name="add_position")
async def add_position(
    request: Request,
    protocol: str = Form(...),
    position_id: str = Form(...),
    version: str = Form(""),
):
    if not _request_has_same_origin(request):
        return JSONResponse(
            {"detail": "Ungültige Anfragequelle."},
            status_code=403,
        )

    config = POSITION_NOTIFY_CONFIGS.get(protocol)
    if config is None:
        return JSONResponse(
            {"detail": "Dieses Protokoll wird nicht unterstützt."},
            status_code=400,
        )

    position_id = position_id.strip()
    version = version.strip().lower() or None
    is_uniswap = protocol.startswith("uniswap_")
    if version is not None and (not is_uniswap or version not in {"v3", "v4"}):
        return JSONResponse(
            {"detail": "Die Protokollversion muss v3 oder v4 sein."},
            status_code=400,
        )

    id_error = _position_id_error(protocol, position_id)
    if id_error:
        return JSONResponse({"detail": id_error}, status_code=400)

    try:
        existing_snapshot_count = await asyncio.to_thread(
            get_position_snapshot_count,
            config[1],
            position_id,
        )
    except Exception:
        logger.exception(
            "Could not verify the position snapshot table for %s",
            protocol,
        )
        return JSONResponse(
            {"detail": "Die Datenbank konnte nicht geprüft werden."},
            status_code=503,
        )

    script = APP_DIRECTORY / config[2]
    positions_file = script.parent / "positions"
    try:
        was_added = _append_position_entry(
            positions_file,
            position_id,
            version,
            is_uniswap,
        )
    except OSError:
        logger.exception("Could not update positions file %s", positions_file)
        return JSONResponse(
            {"detail": "Die Datei positions konnte nicht aktualisiert werden."},
            status_code=500,
        )

    if was_added:
        logger.info("Added %s position %s to %s", protocol, position_id, positions_file)
    else:
        logger.info(
            "Position %s is already listed in %s; rerunning its notifier",
            position_id,
            positions_file,
        )
    try:
        return_code, timed_out = await _run_position_notifier(
            script,
            position_id,
            version if is_uniswap else None,
        )
    except OSError:
        logger.exception("Could not start position notifier %s", script)
        return JSONResponse(
            {
                "detail": (
                    "Der Eintrag in positions bleibt bestehen, aber das "
                    "Notify-Skript konnte nicht gestartet werden."
                )
            },
            status_code=500,
        )

    if timed_out:
        logger.error("Position notifier timed out: %s", script)
        return JSONResponse(
            {
                "detail": (
                    "Der Eintrag in positions bleibt bestehen, aber die "
                    "erste Abfrage hat das Zeitlimit überschritten."
                )
            },
            status_code=504,
        )
    if return_code != 0:
        logger.error(
            "Position notifier failed for %s/%s with exit code %s",
            protocol,
            position_id,
            return_code,
        )
        return JSONResponse(
            {
                "detail": (
                    "Der Eintrag in positions bleibt bestehen, aber die "
                    f"Abfrage ist mit Exit-Code {return_code} fehlgeschlagen."
                )
            },
            status_code=502,
        )

    try:
        new_snapshot_count = await asyncio.to_thread(
            get_position_snapshot_count,
            config[1],
            position_id,
        )
    except Exception:
        logger.exception(
            "Could not verify the initial snapshot for %s/%s",
            protocol,
            position_id,
        )
        return JSONResponse(
            {
                "detail": (
                    "Das Notify-Skript wurde ausgeführt, der Datenbankeintrag "
                    "konnte aber nicht verifiziert werden."
                )
            },
            status_code=503,
        )
    if new_snapshot_count <= existing_snapshot_count:
        logger.error(
            "Position notifier produced no snapshot for %s/%s",
            protocol,
            position_id,
        )
        return JSONResponse(
            {
                "detail": (
                    "Der Eintrag in positions bleibt bestehen, aber es wurde "
                    "kein neuer Datenbankeintrag angelegt."
                )
            },
            status_code=502,
        )

    return JSONResponse(
        {
            "message": (
                "Position eingetragen und erste Abfrage abgeschlossen."
                if was_added
                else "Position war bereits eingetragen; Abfrage erneut abgeschlossen."
            )
        }
    )


@app.post("/update-dashboard", response_class=HTMLResponse)
def update_dashboard(
    request: Request,
    blockchain: str = Form("all"),
    token: str = Form("all"),
    position_id: str = Form(""),
    view: Literal["positions", "token_totals"] = Form("positions"),
    period: Literal["D", "W", "M", "Y"] = Form("D"),
    currency: Literal["USD", "EUR"] = Form("USD"),
    missing_price_only: bool = Form(False),
    start_date: date | None = Form(None),
    end_date: date | None = Form(None),
):
    try:
        if start_date and end_date and start_date > end_date:
            return HTMLResponse(
                "<p class='text-red-400 p-4' role='alert'>"
                "Das Startdatum muss vor oder am Enddatum liegen.</p>",
                status_code=400,
            )

        resample_rule = PERIOD_SETTINGS[period]
        pending_total, pending_price_errors = _get_pending_total(currency)
        all_data = get_dashboard_dataframe()
        all_data["pos_id"] = all_data["pos_id"].map(_position_id_text)
        all_data["_position_key"] = [
            _position_join_key(chain, position_id)
            for chain, position_id in all_data[
                ["chain", "pos_id"]
            ].itertuples(index=False, name=None)
        ]
        recent_position_sources = get_recent_position_sources()
        position_sources_by_key = _position_source_map(recent_position_sources)
        open_position_keys = set(position_sources_by_key)
        all_data["token_symbol"] = all_data["token_symbol"].map(
            lambda symbol: symbol.upper() if isinstance(symbol, str) else symbol
        )
        all_data["token_address"] = [
            address
            if str(chain).strip().lower() == "solana"
            or not isinstance(address, str)
            else address.lower()
            for chain, address in all_data[
                ["chain", "token_address"]
            ].itertuples(index=False, name=None)
        ]
        if start_date:
            all_data = all_data[
                all_data["timestamp"].dt.date >= start_date
            ]
        if end_date:
            all_data = all_data[
                all_data["timestamp"].dt.date <= end_date
            ]
        position_ids = sorted(
            {
                _position_id_text(pos_id)
                for pos_id in all_data.loc[
                    all_data["pos_id"].notna(),
                    "pos_id",
                ]
                if _position_id_text(pos_id) is not None
            }
        )
        position_id = position_id.strip()
        if (
            position_id != OPEN_POSITIONS_FILTER
            and position_id not in position_ids
        ):
            position_id = ""
        chart_dataframe = all_data.copy()

        if blockchain != "all":
            chart_dataframe = chart_dataframe[
                chart_dataframe["chain"] == blockchain
            ]
        if position_id == OPEN_POSITIONS_FILTER:
            chart_dataframe = chart_dataframe[
                chart_dataframe["_position_key"].isin(open_position_keys)
            ]
        elif position_id:
            chart_dataframe = chart_dataframe[
                chart_dataframe["pos_id"].astype("string") == position_id
            ]
        if token != "all":
            chart_dataframe = chart_dataframe[
                chart_dataframe["token_symbol"] == token
            ]

        dataframe = all_data.copy()
        if token != "all":
            dataframe = dataframe[dataframe["token_symbol"] == token]
        if view == "positions":
            if blockchain != "all":
                dataframe = dataframe[dataframe["chain"] == blockchain]
            if position_id == OPEN_POSITIONS_FILTER:
                dataframe = dataframe[
                    dataframe["_position_key"].isin(open_position_keys)
                ]
            elif position_id:
                dataframe = dataframe[
                    dataframe["pos_id"].astype("string") == position_id
                ]

        if dataframe.empty and chart_dataframe.empty:
            return templates.TemplateResponse(
                request,
                "table.html",
                {
                    "request": request,
                    "records": [],
                    "symbol": "$" if currency == "USD" else "€",
                    "currency": currency,
                    "view": view,
                    "chart_data": None,
                    "pending_total": pending_total,
                    "pending_price_errors": pending_price_errors,
                    "price_errors": [],
                    "chart_price_errors": [],
                    "has_unpriced_rows": False,
                    "missing_price_only": missing_price_only,
                    "missing_amount_count": 0,
                    "position_ids": position_ids,
                    "selected_position_id": position_id,
                },
            )

        price_dataframe = pd.concat(
            [dataframe, chart_dataframe], ignore_index=True
        ).drop_duplicates()
        prices_usd: dict[tuple[str, str], float | None] = {}
        for chain, address in price_dataframe[
            ["chain", "token_address"]
        ].drop_duplicates().itertuples(index=False, name=None):
            token_address = str(address)
            price_key = _price_key(str(chain), token_address)
            chain_key = price_key[0]
            if price_key in prices_usd:
                continue
            try:
                prices_usd[price_key] = float(
                    get_token_usd_price(token_address, chain_key)
                )
            except Exception:
                logger.exception(
                    "Could not load price for token %s on %s",
                    token_address,
                    chain_key,
                )
                prices_usd[price_key] = None

        eur_usd_rate = float(get_eur_usd_rate()) if currency == "EUR" else None
        for current_dataframe in (dataframe, chart_dataframe):
            current_dataframe["_price_key"] = [
                _price_key(str(chain), str(address))
                for chain, address in current_dataframe[
                    ["chain", "token_address"]
                ].itertuples(index=False, name=None)
            ]
            current_dataframe["price_usd"] = pd.to_numeric(
                current_dataframe["_price_key"].map(prices_usd), errors="coerce"
            )
            current_dataframe["value_usd"] = (
                current_dataframe["amount"] * current_dataframe["price_usd"]
            )
            if eur_usd_rate is not None:
                current_dataframe["value_converted"] = (
                    current_dataframe["value_usd"] / eur_usd_rate
                )
            else:
                current_dataframe["value_converted"] = current_dataframe["value_usd"]

        price_errors = set()
        for chain, address in dataframe[
            ["chain", "token_address"]
        ].drop_duplicates().itertuples(index=False, name=None):
            price_key = _price_key(str(chain), str(address))
            if prices_usd.get(price_key) is None:
                price_errors.add(f"{price_key[0]}: {address}")

        chart_price_errors = set()
        for chain, address in chart_dataframe[
            ["chain", "token_address"]
        ].drop_duplicates().itertuples(index=False, name=None):
            price_key = _price_key(str(chain), str(address))
            if prices_usd.get(price_key) is None:
                chart_price_errors.add(f"{price_key[0]}: {address}")
        chart_price_errors -= price_errors
        has_unpriced_rows = bool(price_errors)
        if missing_price_only:
            dataframe = dataframe[dataframe["price_usd"].isna()]

        missing_amount_count = int(dataframe["amount"].isna().sum())
        if missing_amount_count:
            logger.warning(
                "Dashboard contains %d rows with missing fee amounts",
                missing_amount_count,
            )

        group_columns = (
            ["token_symbol"]
            if view == "token_totals"
            else ["chain", "pos_id", "token_address", "token_symbol"]
        )
        if dataframe.empty:
            grouped = pd.DataFrame(
                columns=group_columns + ["timestamp", "amount", "value_converted"]
            )
        else:
            grouped = (
                dataframe.set_index("timestamp")
                .groupby(group_columns, dropna=False)[
                    ["amount", "value_converted"]
                ]
                .resample(resample_rule)
                .sum(min_count=1)
                .reset_index()
                .sort_values("timestamp", ascending=False)
            )
        grouped = grouped.astype(object).where(pd.notna(grouped), None)

        chart_data = None
        if not chart_dataframe.empty:
            chart_source = chart_dataframe.assign(
                token_symbol=chart_dataframe["token_symbol"].fillna("Unbekannt")
            )
            chart_grouped = (
                chart_source.set_index("timestamp")
                .groupby("token_symbol")["value_converted"]
                .resample(resample_rule)
                .sum(min_count=1)
                .reset_index()
            )
            chart_pivot = (
                chart_grouped.pivot(
                    index="timestamp",
                    columns="token_symbol",
                    values="value_converted",
                )
                .fillna(0)
                .sort_index()
            )
            chart_tokens = sorted(chart_pivot.columns, key=str)
            chart_data = {
                "currency": currency,
                "labels": [
                    timestamp.strftime(PERIOD_LABEL_FORMATS[period])
                    for timestamp in chart_pivot.index
                ],
                "datasets": [
                    {
                        "label": str(token_symbol),
                        "data": [
                            round(float(value), 2)
                            for value in chart_pivot[token_symbol].tolist()
                        ],
                        "backgroundColor": (
                            f"hsl({(index * 137.508) % 360:.1f}, 65%, 55%)"
                        ),
                        "borderWidth": 0,
                    }
                    for index, token_symbol in enumerate(chart_tokens)
                ],
            }

        records = grouped.to_dict(orient="records")
        if view == "positions":
            for row in records:
                position_id_text = _position_id_text(row["pos_id"])
                if position_id_text is None:
                    row["details_sources"] = []
                    continue
                row["pos_id"] = position_id_text
                try:
                    chain_key = normalize_chain(str(row["chain"]))
                except ValueError:
                    row["details_sources"] = []
                    continue
                row["details_sources"] = position_sources_by_key.get(
                    (chain_key, str(row["pos_id"])),
                    [],
                )

        return templates.TemplateResponse(
            request,
            "table.html",
            {
                "request": request,
                "records": records,
                "symbol": "$" if currency == "USD" else "€",
                "currency": currency,
                "view": view,
                "chart_data": chart_data,
                "pending_total": pending_total,
                "pending_price_errors": pending_price_errors,
                "price_errors": sorted(price_errors),
                "chart_price_errors": sorted(chart_price_errors),
                "has_unpriced_rows": has_unpriced_rows,
                "missing_price_only": missing_price_only,
                "missing_amount_count": missing_amount_count,
                "position_ids": position_ids,
                "selected_position_id": position_id,
            },
        )
    except Exception:
        logger.exception("Could not update the yield dashboard")
        return HTMLResponse(
            "<p class='text-red-400 p-4' role='alert'>"
            "Die Ertragsdaten konnten nicht geladen werden. "
            "Details stehen im Server-Log.</p>",
            status_code=500,
        )


@app.get(
    "/position-details/{table}/{position_id}",
    response_class=HTMLResponse,
    name="position_details",
)
def position_details(
    request: Request,
    table: str,
    position_id: str,
    currency: Literal["USD", "EUR"] = "USD",
):
    try:
        details = get_position_details(table, position_id, currency)
        price_history = get_position_price_history(table, position_id)
        (
            hourly_fees,
            current_database_hour,
            unpriced_fee_count,
        ) = get_position_hourly_fees(
            table,
            position_id,
            PENDING_FEE_TABLES[table],
        )
    except ClosedPositionError:
        return templates.TemplateResponse(
            request,
            "_position_closed.html",
            {"request": request},
            status_code=410,
        )
    except (ValueError, UnsupportedPositionError) as error:
        logger.warning(
            "Position details are unavailable for %s/%s: %s",
            table,
            position_id,
            error,
        )
        return templates.TemplateResponse(
            request,
            "_position_detail_error.html",
            {"request": request, "message": str(error)},
            status_code=422,
        )
    except Exception:
        logger.exception(
            "Could not load live position details for %s/%s",
            table,
            position_id,
        )
        return templates.TemplateResponse(
            request,
            "_position_detail_error.html",
            {
                "request": request,
                "message": "Live-Daten konnten nicht geladen werden. Details stehen im Server-Log.",
            },
            status_code=502,
        )

    price_points = [
        (timestamp, float(price))
        for timestamp, price in price_history
    ]
    price_points_with_crossings = []
    lower_limit = details["price_lower"]
    upper_limit = details["price_upper"]
    for index, (timestamp, price) in enumerate(price_points):
        if index:
            previous_timestamp, previous_price = price_points[index - 1]
            if isinstance(timestamp, datetime) and isinstance(
                previous_timestamp, datetime
            ):
                crossed_limits = [
                    limit
                    for limit in {lower_limit, upper_limit}
                    if min(previous_price, price) < limit < max(previous_price, price)
                ]
                crossed_limits.sort(reverse=price < previous_price)
                for limit in crossed_limits:
                    ratio = (limit - previous_price) / (price - previous_price)
                    crossing_time = previous_timestamp + (
                        timestamp - previous_timestamp
                    ) * ratio
                    price_points_with_crossings.append(
                        {
                            "timestamp": crossing_time,
                            "price": limit,
                        }
                    )
        price_points_with_crossings.append(
            {"timestamp": timestamp, "price": price}
        )

    price_labels = [
        point["timestamp"].strftime("%H:%M")
        if isinstance(point["timestamp"], datetime)
        else str(point["timestamp"])
        for point in price_points_with_crossings
    ]
    last_hour = datetime.strptime(current_database_hour, "%Y-%m-%d %H:%M:%S")
    earnings_hours = [
        (last_hour - timedelta(hours=offset)).strftime("%Y-%m-%d %H:%M:%S")
        for offset in range(24, -1, -1)
    ]
    earnings_by_token = {}
    symbols_by_token = {}
    for hour, token, symbol, amount in hourly_fees:
        earnings_by_token.setdefault(token, {})[hour] = float(amount)
        symbols_by_token[token] = symbol
    symbol_counts = {}
    for symbol in symbols_by_token.values():
        symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
    chart_data = {
        "prices": {
            "labels": price_labels,
            "values": [point["price"] for point in price_points_with_crossings],
            "lower": lower_limit,
            "upper": upper_limit,
        },
        "earnings": {
            "labels": [hour[11:16] for hour in earnings_hours],
            "datasets": [
                {
                    "label": (
                        symbols_by_token[token]
                        if symbol_counts[symbols_by_token[token]] == 1
                        else f"{symbols_by_token[token]} ({token[:6]}...)"
                    ),
                    "data": [
                        earnings_by_token[token].get(hour, 0)
                        for hour in earnings_hours
                    ],
                }
                for token in sorted(earnings_by_token)
            ],
            "unpriced_count": unpriced_fee_count,
        },
    }

    return templates.TemplateResponse(
        request,
        "_position_details.html",
        {"request": request, "details": details, "chart_data": chart_data},
    )


@app.get(
    "/portfolio-total-summary",
    response_class=HTMLResponse,
    name="portfolio_total_summary",
)
def portfolio_total_summary(
    request: Request,
    currency: Literal["USD", "EUR"] = "USD",
):
    try:
        eur_usd_rate = (
            Decimal(str(get_eur_usd_rate()))
            if currency == "USD"
            else Decimal(1)
        )
        total_lp_value = Decimal(0)
        incomplete_lp_positions = 0
        for table, position_id in sorted(get_recent_position_sources()):
            chain = PENDING_FEE_TABLES[table]
            try:
                details = get_position_details(table, position_id, currency)
                position_value = details.get("position_value")
                if position_value is None:
                    raise ValueError("Aktueller LP-Wert fehlt")
                total_lp_value += Decimal(str(position_value))
            except ClosedPositionError:
                continue
            except Exception:
                incomplete_lp_positions += 1
                logger.exception(
                    "Could not load current LP value for %s/%s",
                    chain,
                    position_id,
                )

        balances = [
            balance
            for balance in get_cointracking_balances()
            if balance["value_eur"] != 0
        ]
        balances = add_trust_wallet_icons(balances)
        display_balances: list[dict[str, object]] = []
        total_cointracking_value = Decimal(0)
        for balance in balances:
            value = balance["value_eur"] * eur_usd_rate
            display_balances.append(
                {
                    **balance,
                    "value": value,
                }
            )
            total_cointracking_value += value
        display_balances.sort(
            key=lambda row: (
                row["value"],
                str(row["symbol"]).casefold(),
            ),
            reverse=True,
        )

        lp_total = total_lp_value if incomplete_lp_positions == 0 else None
        total_portfolio_value = (
            total_lp_value + total_cointracking_value
            if incomplete_lp_positions == 0
            else None
        )
        return templates.TemplateResponse(
            request,
            "_portfolio_total_summary.html",
            {
                "request": request,
                "currency": currency,
                "symbol": "$" if currency == "USD" else "€",
                "lp_total": lp_total,
                "incomplete_lp_positions": incomplete_lp_positions,
                "balances": display_balances,
                "cointracking_total": total_cointracking_value,
                "portfolio_total": total_portfolio_value,
            },
        )
    except Exception:
        logger.exception("Could not load portfolio total summary")
        return templates.TemplateResponse(
            request,
            "_position_detail_error.html",
            {
                "request": request,
                "message": "Portfolio-Gesamtwert konnte nicht geladen werden.",
            },
            status_code=502,
        )


@app.get(
    "/position-performance-summary",
    response_class=HTMLResponse,
    name="position_performance_summary",
)
def position_performance_summary(
    request: Request,
    currency: Literal["USD", "EUR"] = "USD",
):
    try:
        sources = sorted(get_recent_position_sources())
        eur_usd_rate = (
            Decimal(str(get_eur_usd_rate()))
            if currency == "EUR"
            else Decimal(1)
        )
        current_time = datetime.now(timezone.utc)
        rows: list[dict[str, object]] = []
        total_current_value = Decimal(0)
        current_values_complete = True
        total_position_change = Decimal(0)
        total_collected_fees = Decimal(0)
        total_combined = Decimal(0)
        incomplete_count = 0

        for table, position_id in sources:
            chain = PENDING_FEE_TABLES[table]
            row: dict[str, object] = {
                "table": table,
                "chain": chain,
                "chain_label": chain[:1].upper() + chain[1:],
                "protocol": POSITION_PROTOCOLS[table],
                "position_id": position_id,
                "tokens": [],
                "position_date": None,
                "age_days": None,
                "in_range": None,
                "current_value": None,
                "position_change": None,
                "collected_fees": None,
                "combined": None,
                "error": None,
            }
            try:
                snapshot = get_position_detail_snapshot(table, position_id)
                if snapshot is not None:
                    row["in_range"] = snapshot["in_range"]
                    row["tokens"] = [
                        {"symbol": snapshot["symbol_0"], "icon_url": None},
                        {"symbol": snapshot["symbol_1"], "icon_url": None},
                    ]
            except Exception:
                logger.exception(
                    "Could not load range status for %s/%s",
                    table,
                    position_id,
                )
            try:
                details = get_position_details(table, position_id, currency)
                row["tokens"] = details["tokens"]
                position_date = details.get("position_date")
                if isinstance(position_date, str):
                    position_date = datetime.fromisoformat(position_date)
                if isinstance(position_date, datetime):
                    if position_date.tzinfo is None:
                        position_date = position_date.replace(tzinfo=timezone.utc)
                    else:
                        position_date = position_date.astimezone(timezone.utc)
                    row["position_date"] = position_date
                    row["age_days"] = max(
                        (current_time - position_date).days,
                        0,
                    )
                elif isinstance(position_date, date):
                    position_date_utc = datetime.combine(
                        position_date,
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    )
                    row["position_date"] = position_date_utc
                    row["age_days"] = max(
                        (current_time - position_date_utc).days,
                        0,
                    )
                initial_value = details.get("initial_value")
                current_value = details.get("position_value")
                if current_value is None:
                    raise ValueError("Aktueller LP-Wert fehlt")
                current_value = Decimal(str(current_value))
                row["current_value"] = float(current_value)
                total_current_value += current_value
                if initial_value is None:
                    raise ValueError("Initialer LP-Wert fehlt")
                position_change = Decimal(str(current_value)) - Decimal(
                    str(initial_value)
                )

                chain_key = normalize_chain(chain)
                collected_fees_usd = Decimal(0)
                for fee in get_position_collected_fees(
                    position_id,
                    chain_key,
                ):
                    try:
                        fee_price = Decimal(
                            str(
                                get_token_usd_price(
                                    str(fee["token"]),
                                    chain_key,
                                )
                            )
                        )
                    except Exception:
                        logger.exception(
                            "Could not price collected fee token %s for %s/%s",
                            fee["token"],
                            chain,
                            position_id,
                        )
                        raise ValueError(
                            f"Fee-Kurs für {fee['symbol'] or fee['token']} fehlt"
                        ) from None
                    collected_fees_usd += Decimal(str(fee["amount"])) * fee_price

                collected_fees = collected_fees_usd / eur_usd_rate
                combined = position_change + collected_fees
                row.update(
                    {
                        "position_change": float(position_change),
                        "collected_fees": float(collected_fees),
                        "combined": float(combined),
                    }
                )
                total_position_change += position_change
                total_collected_fees += collected_fees
                total_combined += combined
            except Exception as error:
                incomplete_count += 1
                if row["current_value"] is None:
                    current_values_complete = False
                row["error"] = str(error)
                logger.warning(
                    "Could not calculate portfolio performance for %s/%s",
                    table,
                    position_id,
                    exc_info=True,
                )
            rows.append(row)

        rows.sort(
            key=lambda row: row["position_date"] or datetime.min.replace(
                tzinfo=timezone.utc
            ),
            reverse=True,
        )
        protocols = sorted({str(row["protocol"]) for row in rows}, key=str.casefold)
        chains = sorted(
            {
                (str(row["chain"]), str(row["chain_label"]))
                for row in rows
            },
            key=lambda chain: chain[1].casefold(),
        )

        return templates.TemplateResponse(
            request,
            "_position_performance_summary.html",
            {
                "request": request,
                "currency": currency,
                "symbol": "$" if currency == "USD" else "€",
                "rows": rows,
                "protocols": protocols,
                "chains": chains,
                "total_current_value": (
                    float(total_current_value) if current_values_complete else None
                ),
                "total_position_change": float(total_position_change),
                "total_collected_fees": float(total_collected_fees),
                "total_combined": float(total_combined),
                "incomplete_count": incomplete_count,
            },
        )
    except Exception:
        logger.exception("Could not load open-position performance summary")
        return templates.TemplateResponse(
            request,
            "_position_detail_error.html",
            {
                "request": request,
                "message": "Wertentwicklung der Positionen konnte nicht geladen werden.",
            },
            status_code=502,
        )


@app.get(
    "/token-holdings-summary",
    response_class=HTMLResponse,
    name="token_holdings_summary",
)
def token_holdings_summary(
    request: Request,
    currency: Literal["USD", "EUR"] = "USD",
):
    try:
        groups: dict[str, _TokenHoldingGroup] = {}
        incomplete_positions = 0
        unpriced_tokens = 0
        chain_names = {
            "eth": "Ethereum",
            "arbitrum": "Arbitrum",
            "polygon": "Polygon",
            "bsc": "BNB Chain",
            "base": "Base",
            "optimism": "Optimism",
            "robinhood": "Robinhood",
            "solana": "Solana",
        }

        for table, position_id in sorted(get_recent_position_sources()):
            chain = PENDING_FEE_TABLES[table]
            try:
                details = get_position_details(table, position_id, currency)
            except ClosedPositionError:
                logger.info(
                    "Skipping closed position in token holdings summary: %s/%s",
                    table,
                    position_id,
                )
                continue
            except Exception:
                incomplete_positions += 1
                logger.exception(
                    "Could not load token holdings for %s/%s",
                    table,
                    position_id,
                )
                continue

            chain_key = normalize_chain(chain)
            for token in details["tokens"]:
                symbol = str(token.get("symbol") or "Unbekannt").strip() or "Unbekannt"
                normalized_symbol = symbol.casefold()
                group_key = (
                    "usdt" if normalized_symbol in USDT_SYMBOLS else normalized_symbol
                )
                group = groups.get(group_key)
                if group is None:
                    group = {
                        "symbol": (
                            USDT_GROUP_LABEL
                            if normalized_symbol in USDT_SYMBOLS
                            else symbol
                        ),
                        "chains": {},
                        "icons": [],
                    }
                    groups[group_key] = group
                chain_row = group["chains"].get(chain_key)
                if chain_row is None:
                    chain_row = {
                        "chain": chain_key,
                        "chain_label": chain_names.get(chain_key, chain_key),
                        "amount": Decimal(0),
                        "symbols": [],
                        "value": Decimal(0),
                        "complete": True,
                        "icons": [],
                    }
                    group["chains"][chain_key] = chain_row
                chain_row["amount"] += Decimal(
                    str(token.get("amount_exact", token["amount"]))
                )
                if symbol not in chain_row["symbols"]:
                    chain_row["symbols"].append(symbol)
                icon_url = token.get("icon_url")
                if icon_url:
                    if icon_url not in chain_row["icons"]:
                        chain_row["icons"].append(icon_url)
                    if icon_url not in group["icons"]:
                        group["icons"].append(icon_url)

                token_value = token.get("value")
                if token_value is None:
                    chain_row["complete"] = False
                    unpriced_tokens += 1
                else:
                    chain_row["value"] += Decimal(str(token_value))

        result_groups: list[_TokenHoldingDisplayGroup] = []
        total_value = Decimal(0)
        total_complete = incomplete_positions == 0 and unpriced_tokens == 0

        for group in groups.values():
            rows = list(group["chains"].values())
            for row in rows:
                if row["complete"]:
                    total_value += row["value"]
                else:
                    total_complete = False
            rows.sort(
                key=lambda row: (
                    row["complete"],
                    row["value"] if row["complete"] else Decimal(0),
                    row["chain_label"].casefold(),
                ),
                reverse=True,
            )
            group_value = (
                sum((row["value"] for row in rows), Decimal(0))
                if all(row["complete"] for row in rows)
                else None
            )
            result_group: _TokenHoldingDisplayGroup = {
                "symbol": group["symbol"],
                "icons": group["icons"],
                "rows": rows,
                "value": group_value,
            }
            result_groups.append(result_group)

        result_groups.sort(
            key=lambda group: (
                group["value"] is not None,
                group["value"] if group["value"] is not None else Decimal(0),
                group["symbol"].casefold(),
            ),
            reverse=True,
        )
        return templates.TemplateResponse(
            request,
            "_token_holdings_summary.html",
            {
                "request": request,
                "currency": currency,
                "symbol": "$" if currency == "USD" else "€",
                "groups": result_groups,
                "total_value": total_value if total_complete else None,
                "incomplete_positions": incomplete_positions,
                "unpriced_tokens": unpriced_tokens,
            },
        )
    except Exception:
        logger.exception("Could not load current token holdings summary")
        return templates.TemplateResponse(
            request,
            "_position_detail_error.html",
            {
                "request": request,
                "message": "Token-Anteile konnten nicht geladen werden.",
            },
            status_code=502,
        )
