#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
import datetime
import io
from decimal import Decimal
from typing import Optional, Dict
from web3 import Web3
import mariadb
import asyncio
import telegram_send
from operator import itemgetter
from collections import defaultdict
from dotenv import load_dotenv
import httpx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Load environment variables from .env file
load_dotenv()

# Caches for Web3 instances and token mappings
_web3_instances: Dict = {}  # Cache for Web3 instances per chain
_token_maps: Dict = {}  # Cache for token address mappings per chain

TOKEN_COLORS = {
    "WETH": "#1f77b4",
    "WBTC": "#ff7f0e",
    "cbBTC": "#ff7f0e",
    "SOL": "#2ca02c",
    "WBNB": "#d62728",
    "BNB": "#d62728",
    "WPOL": "#9467bd",
    "POL": "#9467bd",
    "Cake": "#8c564b",
    "AERO": "#e377c2",
    "USDC": "#a9a7b4",
    "USDT": "#a9a7b4",
    "USDG": "#a9a7b4",
    "EURC": "#a9a7b4",
    "EURe": "#a9a7b4",
    "SPYx": "#1f77b4",
    "SPCXx": "#ff7f0e",
    "NVDAx": "#2ca02c",
    "MPLX": "#d62728",
    "CRCLx": "#9467bd",
    "TSLAx": "#8c564b",
    "AAPLx": "#e377c2",
    "NFLXx": "#7f7f7f",
    "MSTRx": "#bcbd22",
    "HOODx": "#17becf",
    "QQQx": "#ff9896",
    "GOOGLx": "#aec7e8",
    "STRCx": "#ffbb78",
    "METAx": "#98df8a",
    "MSFTx": "#c5b0d5",
    "INTCx": "#c49c94",
    "GLDx": "#f7b6d2",
    "COINx": "#dbdb8d",
    "MCDx": "#9edae5",
}

db_user = os.getenv('DB_USER')
db_pswd = os.getenv('DB_PASSWORD')
db_host = os.getenv('DB_HOST')
db_port_str = os.getenv('DB_PORT')
db_name = os.getenv('DB_NAME')

missing_db_vars = [
    name
    for name, value in (
        ('DB_USER', db_user),
        ('DB_PASSWORD', db_pswd),
        ('DB_HOST', db_host),
        ('DB_PORT', db_port_str),
        ('DB_NAME', db_name),
    )
    if not value
]
if missing_db_vars:
    raise ValueError(
        f"Missing database environment variables: {', '.join(missing_db_vars)}"
    )
try:
    db_port = int(db_port_str)
except ValueError as error:
    raise ValueError('DB_PORT must be an integer.') from error

CHAIN_IDS = {
    "eth": 1,
    "arbitrum": 42161,
    "polygon": 137,
    "bsc": 56,
    "base": 8453,
    "optimism": 10,
    "robinhood": 4663,
    "solana": "solana",
}

ALCHEMY_API_KEY = os.getenv('ALCHEMY_API_KEY')

if not ALCHEMY_API_KEY:
    raise ValueError('ALCHEMY_API_KEY environment variable is not set. Please configure .env file.')

HELIUS_API_KEY = os.getenv('HELIUS_API_KEY')

if not HELIUS_API_KEY:
    raise ValueError('HELIUS_API_KEY environment variable is not set. Please configure .env file.')

RPC_URLS = {
    "eth": f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
    "arbitrum": f"https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
    "polygon": f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
    "base": f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
    "optimism": f"https://opt-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
    "robinhood": f"https://robinhood-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
    "bsc": f"https://bnb-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
    "solana": f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",
    # BSC wird von Alchemy nicht immer/überall im gleichen Umfang angeboten.
    # Falls du BSC über Alchemy hast, hier deine BSC-URL eintragen:
    # "bsc": os.getenv("BSC_RPC_URL"),
}

# Chainlink AggregatorV3Interface, minimal ABI
AGGREGATOR_V3_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

FEED_BY_CHAIN_ASSET = {
    "eth": {
        "ETH": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
        "BTC": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
        "USDC": "0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6",
        "USDT": "0x3E7d1eAB13ad0104d2750B8863b489D65364e32D",
        "EURC": "0xb49f677943BC038e9857d61E7d053CaA2C1734C1",
        "EURe": "0xb49f677943BC038e9857d61E7d053CaA2C1734C1",
        "SOL": "0x4ffC43a60e009B551865A93d232E33Fce9f01507",
    },
    "arbitrum": {
        "ETH": "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612",
        # "BTC": "0x6ce185860a4963106506C203335A2910413708e9",
        "WBTC": "0xd0C7101eACbB49F3deCcCc166d238410D6D46d57",
        "USDC": "0x50834F3163758fcC1Df9973b6e91f0F0F0434aD3",
        "USDT": "0x3f3f5dF88dC9F13eac63DF89EC16ef6e7E25DdE7",
        "EURe": "0xA14d53bC1F1c0F31B4aA3BD109344E5009051a84",
    },
    "polygon": {
        "ETH": "0xF9680D99D6C9589e2a93a78A04A279e509205945",
        # "BTC": "0xc907E116054Ad103354f2D350FD2514433D57F6f",
        "WBTC": "0xDE31F8bFBD8c84b5360CFACCa3539B938dd78ae6",
        "USDC": "0xfE4A8cc5b5B2366C1B58Bea3858e81843581b2F7",
        "USDT": "0x0A6513e40db6EB1b165753AD52E80663aeA50545",
        "POL": "0xAB594600376Ec9fD91F8e885dADF0CE036862dE0",
    },
    "bsc": {
        "BNB": "0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE",
        "WBNB": "0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE",
        "BTC": "0x264990fbd0A4796A3E3d8E37C4d5F87a3aCa5Ebf",
        "ETH": "0x9ef1B8c0E4F7dc8bF5719Ea496883DC6401d5b2e",
        "USDC": "0x51597f405303C4377E36123cBc172b13269EA163",
        "USDT": "0xB97AD0E74fa7d920791E90258A6E2085088b4320",
        "Cake": "0xB6064eD41d4f67e353768aA239cA86f4F73665a1",
    },
    "base": {
        "ETH": "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",
        "WBTC": "0x64c911996D3c6aC71f9b455B1E8E7266BcbD848F",
        "cbBTC": "0x10509b4053385b49145Fab2D6B1c58e96Eac5b79",
        "USDC": "0x7e860098F58bBFC8648a4311b374B1D669a2bc6B",
    },
    "optimism": {
        "ETH": "0x13e3Ee699D1909E989722E753853AE30b17e08c5",
        # "BTC": "0xD702DD976Fb76Fffc2D3963D037dfDae5b04E593",
        "WBTC": "0x718A5788b89454aAE3A028AE9c111A29Be6c2a6F",
        "USDC": "0x16a9FA2FDa030272Ce99B29CF780dFA30361E0f3",
        "USDT": "0xECef79E109e997bCA29c1c0897ec9d7b03647F5E",
    },
    "robinhood": {
        "ETH": "0x78F3556b67E17Df817D51Ef5a990cDaF09E8d3A9",
        "USDG": "0x61B7e5650328764B076A108EFF5fa7282a1B9aD2",
    },
    "solana": {
        # Pyth Oracle Feed IDs (hex format for Hermes API)
        # SOL and USDT use market-price fallbacks when Hermes feeds are unavailable.
        "SOL": "solana",
        "USDC": "0xeaa020c61cc479712813461ce153894a96a6c00b21ed0cfc2798d1f9a9e9c94a",    # USDC/USD
        "ETH": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",     # ETH/USD
        "BTC": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",    # BTC/USD
        "SPYx": "0x2817b78438c769357182c04346fddaad1178c82f4048828fe0997c3c64624e14",  # SP500 xStock/USD
    },
}

SOLANA_XSTOCK_ASSETS = {
    "SPYx",
    "SPCXx",
    "NVDAx",
    "MPLX",
    "CRCLx",
    "TSLAx",
    "AAPLx",
    "NFLXx",
    "MSTRx",
    "HOODx",
    "QQQx",
    "GOOGLx",
    "STRCx",
    "METAx",
    "MSFTx",
    "INTCx",
    "GLDx",
    "COINx",
    "MCDx",
}

TOKEN_TO_ASSET = {
    "eth": {
        "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2": "ETH",   # WETH
        "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599": "BTC",   # WBTC
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": "USDC",
        "0xdAC17F958D2ee523a2206206994597C13D831ec7": "USDT",
        "0x39b8B6385416f4cA36a20319F70D28621895279D": "EURe",
        "0x1aBaEA1f7C830bD89Acc67eC4af516284b1bC33c": "EURC",
        "0xD31a59c85aE9D8edEFeC411D448f90841571b89c": "SOL",   # Wormhole Wrapped SOL
    },
    "arbitrum": {
        "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1": "ETH",    # WETH
        "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f": "WBTC",
        "0xaf88d065e77c8cC2239327C5EDb3A432268e5831": "USDC",
        "0xFd086bC7CD5C481DCC9C85ebe478A1C0b69FCbb9": "USDT",
        "0x0c06cCF38114ddfc35e07427B9424adcca9F44F8": "EURe",
    },
    "polygon": {
        "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619": "ETH",    # WETH
        "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6": "WBTC",
        "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174": "USDC",
        "0x3c499c542cef5e3811E1192ce70d8cC03d5c3359": "USDC",
        "0xc2132D05D31c914a87C6611C10748AEb04B58e8F": "USDT",
        "0x0000000000000000000000000000000000001010": "POL",
        "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270": "POL",
    },
    "bsc": {
        "0xBB4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c": "BNB",
        "0xBB4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c": "WBNB",    # WBNB
        "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c": "BTCB",   # BTCB
        "0x0555E30da8f98308EdB960aa94C0Db47230d2B9c": "BTC",
        "0x2170Ed0880ac9A755fd29B2688956BD959F933F8": "ETH",
        "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d": "USDC",
        "0x55d398326f99059fF775485246999027B3197955": "USDT",
        "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82": "Cake",
    },
    "base": {
        "0x4200000000000000000000000000000000000006": "ETH",    # WETH
        "0x0555E30da8f98308EdB960aa94C0Db47230d2B9c": "WBTC",
        "0x1ceA84203673764244E05693e42E6Ace62bE9BA5": "WBTC",
        "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf": "cbBTC",
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913": "USDC",
        "0x940181a94A35A4569E4529A3CDfB74e38FD98631": "AERO",
    },
    "optimism": {
        "0x4200000000000000000000000000000000000006": "ETH",
        # "0x68f180fcCe6836688e9084f035309E29Bf0A2095": "BTC",
        "0x68f180fcCe6836688e9084f035309E29Bf0A2095": "WBTC",
        "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85": "USDC",
        "0x7F5c764cBc14f9669B88837ca1490cCa17c31607": "USDC",   # USDC.e
        "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58": "USDT",
    },
    "robinhood": {
        "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73": "ETH",  # Robinhood Wrapped ETH
        "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168": "USDG",
    },
    "solana": {
        # SPL Token Addresses
        "So11111111111111111111111111111111111111112": "SOL",    # Wrapped SOL
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",  # USDT
        "2FPyTwcZLUg1MDrwsyoP4D6s1tM7hAkHYRjkNb5w6Pxk": "ETH",  # Wrapped Ether (Sollet)
        "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E": "BTC",  # Wrapped Bitcoin (Sollet)
        "XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W": "SPYx",  # SP500 xStock
        "Xs3oZwbHvqis4NYcf4YKWmEia2eC84wSiVrcYcTqpH8": "SPCXx",
        "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh": "NVDAx",
        "METAewgxyPbgwsseH8T16a39CQ5VyVxZi9zXiDPY18m": "MPLX",
        "XsueG8BtpquVJX9LVLLEGuViXUungE6WmK5YZ3p3bd1": "CRCLx",
        "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB": "TSLAx",
        "XsbEhLAtcf6HdfpFZ5xEMdqW8nfAvcsP5bdudRLJzJp": "AAPLx",
        "XsEH7wWfJJu2ZT3UCFeVfALnVA6CP5ur7Ee11KmzVpL": "NFLXx",
        "XsP7xzNPvEHS1m6qfanPUGjNmdnmsLKEoNAnHjdxxyZ": "MSTRx",
        "XsvNBAYkrDRNhA7wPHQfX3ZUXZyZLdnCQDfHZ56bzpg": "HOODx",
        "Xs8S1uUs1zvS2p7iwtsG3b6fkhpvmwz4GYU3gWAmWHZ": "QQQx",
        "XsCPL9dNWBMvFtTmwcCA5v3xWPSMEBCszbQdiLLq6aN": "GOOGLx",
        "Xs78JED6PFZxWc2wCEPspZW9kL3Se5J7L5TChKgsidH": "STRCx",
        "Xsa62P5mvPszXL1krVUnU5ar38bBSVcWAB6fmPCo5Zu": "METAx",
        "XspzcW1PRtgf6Wj92HCiZdjzKCyFekVD8P5Ueh3dRMX": "MSFTx",
        "XshPgPdXFRWB8tP1j82rebb2Q9rPgGX37RuqzohmArM": "INTCx",
        "Xsv9hRk1z5ystj9MhnA7Lq4vjSsLwzL2nxrwmwtD3re": "GLDx",
        "Xs7ZdzSHLU9ftNJsii5fCeJhoRWSC32SQGzGQtePxNu": "COINx",
        "XsqE9cRRpzxcGKDXj1BJ7Xmg4GRhZoyY1KpmGSxAWT2": "MCDx",
    },
}


def db_connect():
    try:
        connection = mariadb.connect(
            user=db_user,
            password=db_pswd,
            host=db_host,
            port=db_port,
            database=db_name
        )

    except mariadb.Error as e:
        print(f'Error connecting to MariaDB Platform: {e}')
        sys.exit(1)

    cursor = connection.cursor()

    return connection, cursor


def get_db_entries(cursor, connection, timestamp):
    query = """
    SELECT *
    FROM total
    WHERE `timestamp` >= CURDATE()
    AND `timestamp` < CURDATE() + INTERVAL 1 DAY
    """

    cursor.execute(query)

    rows = cursor.fetchall()

    sums_of_token = defaultdict(lambda: {
        "symbol": None,
        "chain": None,
        "fee": Decimal("0"),
    })

    for uid, pos_id, timestamp, token, symbol, fee, chain in rows:
        sums_of_token[token]["symbol"] = symbol
        sums_of_token[token]["chain"] = chain
        sums_of_token[token]["fee"] += fee

    result = [
        (token, data["symbol"], data["fee"], data["chain"])
        for token, data in sums_of_token.items()
    ]

    result.sort(key=itemgetter(1))

    return result


def get_db_entries_for_range(cursor, start_time, end_time):
    query = """
    SELECT `timestamp`, token, symbol, fee, chain
    FROM total
    WHERE `timestamp` >= %s
    AND `timestamp` < %s
    """

    cursor.execute(query, (start_time, end_time))
    return cursor.fetchall()


def get_yesterday_bounds():
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    start = datetime.datetime.combine(yesterday, datetime.time.min)
    end = datetime.datetime.combine(today, datetime.time.min)
    return start, end


def create_token_report_images(hourly_data, daily_token_data, report_date):
    hours = list(range(24))
    hour_labels = [f"{hour:02d}:00" for hour in hours]

    plt.rcParams.update({
        'figure.facecolor': 'white',
        'axes.facecolor': 'white',
        'axes.edgecolor': '#444444',
        'axes.titlepad': 8,
        'font.size': 10,
        'axes.labelsize': 10,
        'xtick.labelsize': 9,
        'ytick.labelsize': 9,
        'legend.fontsize': 9,
    })

    bar_buffer = io.BytesIO()
    fig, ax = plt.subplots(figsize=(12, 7), dpi=150)
    bottom = [0.0] * 24

    for symbol in sorted(hourly_data.keys()):
        values = [float(hourly_data[symbol].get(hour, Decimal('0'))) for hour in hours]
        ax.barh(
            hours,
            values,
            left=bottom,
            color=TOKEN_COLORS.get(symbol, '#999999'),
            edgecolor='none',
            height=0.72,
            label=symbol,
        )
        bottom = [bottom[i] + values[i] for i in range(24)]

    ax.set_yticks(hours)
    ax.set_yticklabels(hour_labels)
    ax.invert_yaxis()
    ax.set_xlabel('USD')
    ax.set_ylabel('Stunde')
    ax.set_title(f'Fees pro Stunde (USD) - {report_date}')
    ax.legend(title='Token', loc='lower right')
    ax.grid(axis='x', linestyle='--', linewidth=0.7, alpha=0.3)
    fig.tight_layout()
    fig.savefig(bar_buffer, format='png', bbox_inches='tight', pad_inches=0.2)
    plt.close(fig)
    bar_buffer.seek(0)

    pie_buffer = io.BytesIO()
    fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
    token_labels = [symbol for symbol, value in daily_token_data if value > 0]
    token_values = [float(value) for symbol, value in daily_token_data if value > 0]
    token_colors = [TOKEN_COLORS.get(symbol, '#999999') for symbol in token_labels]

    ax.pie(
        token_values,
        labels=token_labels,
        colors=token_colors,
        autopct='%1.1f%%',
        startangle=90,
        pctdistance=0.6,
        labeldistance=1.1,
        wedgeprops={'linewidth': 0.8, 'edgecolor': 'white'},
    )
    ax.set_title(
        f'Anteil der Token am Tageswert - {report_date}',
        pad=18,
    )
    ax.axis('equal')
    fig.tight_layout()
    fig.savefig(pie_buffer, format='png', bbox_inches='tight', pad_inches=0.2)
    plt.close(fig)
    pie_buffer.seek(0)

    return bar_buffer, pie_buffer


def normalize_chain(chain: str) -> str:
    key = chain.strip().lower()
    if "ethereum" in key:
        key = "eth"
    if key in {"robinhood chain", "robinhoodchain"}:
        key = "robinhood"
    if key not in CHAIN_IDS:
        raise ValueError(f"Unsupported chain: {chain}")
    return key


def get_web3_instance(chain_key: str, rpc_url: str) -> Web3:
    """Get or create a cached Web3 instance for the given chain."""
    if chain_key not in _web3_instances:
        _web3_instances[chain_key] = Web3(Web3.HTTPProvider(rpc_url))
    return _web3_instances[chain_key]


def get_token_map(chain_key: str) -> Dict:
    """Get or create a cached token address mapping for the given chain (EVM only)."""
    if chain_key == "solana":
        raise ValueError("Solana token mapping must be handled separately")
    
    if chain_key not in _token_maps:
        _token_maps[chain_key] = {
            Web3.to_checksum_address(addr): asset
            for addr, asset in TOKEN_TO_ASSET.get(chain_key, {}).items()
        }
    return _token_maps[chain_key]


def get_dexscreener_token_usd_price(
    token_address: str,
    asset: str,
    chain_id: str,
) -> Decimal:
    response = httpx.get(
        f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
        timeout=10.0,
    )
    response.raise_for_status()
    token_address_lower = token_address.lower()
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


def get_token_usd_price(
    token_address: str,
    chain: str,
    rpc_url: Optional[str] = None,
    max_age_seconds: int = 3600,
) -> Decimal:
    chain_key = normalize_chain(chain)
    
    # Handle Solana separately as it's not EVM-compatible
    if chain_key == "solana":
        return get_token_usd_price_solana(token_address, chain_key, rpc_url)
    
    rpc_url = rpc_url or RPC_URLS.get(chain_key)

    if not rpc_url:
        raise ValueError(f"Missing RPC URL for {chain_key}")

    # Use cached Web3 instance
    w3 = get_web3_instance(chain_key, rpc_url)

    checksum_token = Web3.to_checksum_address(token_address)
    # Use cached token mapping
    token_map = get_token_map(chain_key)

    asset = token_map.get(checksum_token)
    if not asset:
        raise ValueError(f"No token mapping for {checksum_token} on {chain_key}")

    if chain_key == "base" and asset == "AERO":
        return get_dexscreener_token_usd_price(checksum_token, asset, chain_key)

    feed_address = FEED_BY_CHAIN_ASSET.get(chain_key, {}).get(asset)
    if not feed_address:
        raise ValueError(f"No Chainlink USD feed configured for {asset} on {chain_key}")

    feed = w3.eth.contract(
        address=Web3.to_checksum_address(feed_address),
        abi=AGGREGATOR_V3_ABI,
    )

    decimals = feed.functions.decimals().call()
    round_id, answer, _started_at, updated_at, _answered_in_round = (
        feed.functions.latestRoundData().call()
    )

    if answer <= 0:
        raise RuntimeError(f"Invalid Chainlink answer for {asset}/USD on {chain_key}")
    # if updated_at == 0 or time.time() - updated_at > max_age_seconds:
    #     raise RuntimeError(f"Stale Chainlink price for {asset}/USD on {chain_key}")

    return Decimal(answer) / (Decimal(10) ** decimals)


def get_token_usd_price_solana(
    token_address: str,
    chain_key: str,
    rpc_url: Optional[str] = None,
) -> Decimal:
    """Get a Solana token USD price from Pyth, Jupiter, or its liquidity pool."""
    # Get token mapping for Solana
    token_map = TOKEN_TO_ASSET.get(chain_key, {})
    asset = token_map.get(token_address)
    
    if not asset:
        raise ValueError(f"No token mapping for {token_address} on {chain_key}")

    feed_address = FEED_BY_CHAIN_ASSET.get(chain_key, {}).get(asset)
    try:
        return asyncio.run(
            _fetch_solana_price_with_fallbacks_async(
                token_address, asset, feed_address
            )
        )
    except Exception as e:
        if asset == "SOL":
            try:
                wrapped_sol_address = "0xD31a59c85aE9D8edEFeC411D448f90841571b89c"
                return get_token_usd_price(wrapped_sol_address, "eth")
            except Exception as eth_error:
                raise RuntimeError(
                    f"Failed to get SOL price: Pyth/Jupiter/pool failed ({e}), "
                    f"Ethereum fallback (Wormhole Wrapped SOL) also failed ({eth_error})"
                )
        else:
            raise RuntimeError(f"Failed to get price for {asset}/USD on {chain_key}: {e}")


async def _fetch_solana_price_with_fallbacks_async(
    token_address: str,
    asset: str,
    pyth_account: Optional[str],
) -> Decimal:
    errors = []
    async with httpx.AsyncClient() as client:
        if pyth_account:
            try:
                return await _fetch_pyth_price_async(client, pyth_account, asset)
            except Exception as error:
                errors.append(f"Pyth: {error}")

        try:
            return await _fetch_jupiter_price_async(client, token_address, asset)
        except Exception as error:
            errors.append(f"Jupiter: {error}")

        try:
            return await _fetch_dexscreener_price_async(
                client, token_address, asset
            )
        except Exception as error:
            errors.append(f"Pool: {error}")

    raise RuntimeError("; ".join(errors))


async def _fetch_jupiter_price_async(
    client: httpx.AsyncClient,
    token_address: str,
    asset: str,
) -> Decimal:
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


async def _fetch_pyth_price_async(
    client: httpx.AsyncClient,
    pyth_account: str,
    asset: str,
) -> Decimal:
    """Fetch the latest Pyth price from Hermes."""
    pyth_feed_ids = {
        "ETH": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
        "BTC": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
        "USDC": "0xeaa020c61cc479712813461ce153894a96a6c00b21ed0cfc2798d1f9a9e9c94a",
        "WBTC": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    }
    feed_id = pyth_feed_ids.get(asset, pyth_account)
    if not feed_id.startswith("0x"):
        raise RuntimeError(f"No Hermes feed configured for {asset}")

    hermes_url = "https://hermes.pyth.network/api/latest_price_feeds"
    response = await client.get(
        hermes_url, params={"ids[]": feed_id}, timeout=10.0
    )
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


async def _get_pyth_price(rpc_url: str, pyth_account: str) -> Decimal:
    """Fetch price from Pyth Oracle account on Solana."""
    raise NotImplementedError("Use _fetch_pyth_price_async instead")


def batch_get_token_prices(token_list: list) -> Dict[tuple, Decimal]:
    """
    Batch fetch USD prices for unique (token_address, chain) pairs.
    This reduces RPC calls by fetching each price only once.
    
    Args:
        token_list: List of DB rows with (timestamp, token, symbol, fee, chain)
    
    Returns:
        Dictionary mapping (token_address, chain) -> Decimal price
    """
    price_cache = {}
    
    # Collect unique (token_address, chain) pairs
    unique_pairs = set()
    for row in token_list:
        row_timestamp, token, symbol, fee, chain = row
        unique_pairs.add((token, chain))
    
    # Fetch prices only once per unique pair
    for token_address, chain in unique_pairs:
        try:
            quote = get_token_usd_price(token_address, chain)
            price_cache[(token_address, chain)] = quote
        except Exception as e:
            print(f"Error fetching price for {token_address} on {chain}: {e}")
            # Use 0 as fallback to skip invalid tokens
            price_cache[(token_address, chain)] = Decimal("0")
    
    return price_cache


def clear_caches():
    """Clear all caches. Useful for testing or manual resets."""
    global _web3_instances, _token_maps
    _web3_instances.clear()
    _token_maps.clear()


async def telegram(tm, images=None, captions=None):
    await telegram_send.send(
        conf=('./telegram.conf'),
        parse_mode=('html'),
        messages=[tm],
        images=images,
        captions=captions,
    )


def main():
    connection, cursor = db_connect()

    try:
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        now = datetime.datetime.now()
        report_start, report_end = get_yesterday_bounds()
        report_date = report_start.date().strftime('%d.%m.%Y')

        rows = get_db_entries_for_range(cursor, report_start, report_end)
        hourly_token_totals = defaultdict(lambda: defaultdict(lambda: Decimal('0')))
        daily_token_totals = defaultdict(lambda: Decimal('0'))

        # Batch fetch all prices once per unique (token, chain) pair
        prices = batch_get_token_prices(rows)

        for row in rows:
            row_timestamp, token, symbol, fee, chain = row
            
            # Use cached prices - no additional RPC calls
            quote = prices[(token, chain)]
            
            # Skip tokens with invalid/zero prices
            if quote == 0:
                continue

            usd_value = fee * quote
            hour = row_timestamp.hour if hasattr(row_timestamp, 'hour') else 0
            hourly_token_totals[symbol][hour] += usd_value
            daily_token_totals[symbol] += usd_value

        earnings = sorted(
            daily_token_totals.items(),
            key=lambda item: item[1],
            reverse=True,
        )

        total_usd = sum(
            (usd_value for symbol, usd_value in earnings),
            Decimal("0"),
        )

        message_lines = [
            '<b>Gestern verdient:</b>',
            '',
            f'Bericht für den {report_date}',
            '',
            '<u>Nach Einzelwerten:</u>',
        ]

        for symbol, usd_value in earnings:
            message_lines.append(f'{symbol}: {usd_value:.6f} USD')

        message_lines.extend([
            '',
            '<u>Gesamt:</u>',
            f'{total_usd:.2f} USD',
        ])

        tm = '\n'.join(message_lines)

        daily_token_data = sorted(
            daily_token_totals.items(),
            key=lambda item: item[1],
            reverse=True,
        )

        bar_buffer, pie_buffer = create_token_report_images(
            hourly_token_totals,
            daily_token_data,
            report_date,
        )

        try:
            asyncio.run(
                telegram(
                    tm,
                    images=[bar_buffer, pie_buffer],
                    captions=[
                        f'Fees pro Stunde für {report_date}',
                        f'Token-Anteile für {report_date}',
                    ],
                )
            )
        except Exception as error:
            print(f'##### {timestamp} #####')
            print(error)

    finally:
        # Ensure database connection is always closed
        cursor.close()
        connection.close()


if __name__ == '__main__':
    main()
