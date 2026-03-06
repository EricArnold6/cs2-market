# cs2-market

A quantitative trading toolkit for the **CS2 (Counter-Strike 2) cosmetics market**.

The library detects **market maker (盘主) activity** — large traders who drive short-term price swings by accumulating or distributing specific items — and generates **actionable trading signals** you can act on or back-test.

---

## Table of Contents

- [Features](#features)
- [Project Structure](#project-structure)
- [Architecture Overview](#architecture-overview)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Module Reference](#module-reference)
  - [1. Data Models (`src/schemas/`, `src/acquisition/models.py`)](#1-data-models)
  - [2. Data Acquisition (`src/acquisition/`)](#2-data-acquisition)
  - [3. Technical Indicators (`src/analysis/indicators.py`)](#3-technical-indicators)
  - [4. Market Maker Detection (`src/analysis/market_maker.py`)](#4-market-maker-detection)
  - [5. Trading Signals (`src/strategy/signal.py`)](#5-trading-signals)
  - [6. Backtesting (`src/backtest/`)](#6-backtesting)
  - [7. Anomaly Detection (`src/analysis/anomaly/`)](#7-anomaly-detection)
  - [8. Storage (`src/storage/`)](#8-storage)
  - [9. Alerting (`src/alerting/`)](#9-alerting)
- [Import Paths & Compatibility](#import-paths--compatibility)
- [Running Tests](#running-tests)
- [Notes and Risks](#notes-and-risks)

---

## Features

| Module | Description |
|--------|-------------|
| `src/schemas/market.py` | `OrderBookSnapshot` — system-wide canonical data contract |
| `src/acquisition/` | Steam order-book fetcher, nameid cache, batch initializer |
| `src/analysis/indicators.py` | SMA, EMA, RSI, MACD, Bollinger Bands, volume ratio |
| `src/analysis/market_maker.py` | Volume spike, price momentum, BB breakout, composite MM score |
| `src/strategy/signal.py` | `generate_signals()` / `latest_signal()` → BUY / SELL / HOLD |
| `src/backtest/` | `run_backtest()` — P&L, win rate, max drawdown |
| `src/analysis/anomaly/` | `MarketAnomalyDetector` — Isolation Forest over order-book microstructure |
| `src/storage/` | PostgreSQL persistence (`DatabaseConnection`, `OrderBookRepository`) |
| `src/alerting/` | DingTalk Webhook alerts with HMAC-SHA256 signing |

---

## Project Structure

```
cs2-market/
├── README.md
├── CLAUDE.md                          # AI assistant instructions
├── example.py                         # Offline demo (synthetic data, no HTTP)
├── requirements.txt
├── .gitignore
│
├── src/
│   ├── __init__.py
│   │
│   ├── schemas/                       ══ 全系统共享数据契约 ══
│   │   ├── __init__.py
│   │   └── market.py                  OrderBookSnapshot (single source of truth)
│   │
│   ├── acquisition/                   ══ 模块一：数据获取与清洗 ══
│   │   ├── __init__.py                re-export 所有公开符号
│   │   ├── exceptions.py              NameIdExtractionError, NameIdNotInitializedError
│   │   ├── models.py                  PriceRecord, ItemHistory, TradeSignal, OrderBook
│   │   ├── cache.py                   _NameIdCache（持久化 JSON 缓存）
│   │   ├── http_client.py             SteamHttpClient, SteamOrderBookFetcher
│   │   ├── initializer.py             NameIdInitializer, InitResult
│   │   └── fetcher.py                 MarketDataFetcher（CNY）+ 向后兼容 re-export hub
│   │
│   ├── storage/                       ══ 模块二：数据存储 ══
│   │   ├── __init__.py
│   │   ├── database.py                DatabaseConnection（psycopg2, autocommit, DDL init）
│   │   └── repository.py              OrderBookRepository（CRUD, bulk insert）
│   │
│   ├── analysis/                      ══ 模块三：特征工程与模型策略 ══
│   │   ├── __init__.py
│   │   ├── indicators.py              SMA / EMA / RSI / MACD / BB / volume ratio
│   │   ├── market_maker.py            volume spike / momentum / BB breakout / composite score
│   │   └── anomaly/
│   │       ├── __init__.py            exports MarketAnomalyDetector
│   │       ├── features.py            engineer_features(df) → 4-column DataFrame
│   │       └── detector.py            MarketAnomalyDetector（Isolation Forest pipeline）
│   │
│   ├── strategy/                      ══ 模块五：信号生成 ══
│   │   ├── __init__.py
│   │   └── signal.py                  generate_signals(), latest_signal()
│   │
│   ├── backtest/                      ══ 模块六：回测引擎 ══
│   │   ├── __init__.py
│   │   ├── models.py                  Trade, BacktestResult
│   │   └── engine.py                  run_backtest()
│   │
│   ├── alerting/                      ══ 模块四：预警与推送 ══
│   │   ├── __init__.py
│   │   ├── bot.py                     DingTalkAlerter（HMAC-SHA256 Webhook）
│   │   ├── formatter.py               format_anomaly_alert() → DingTalk Markdown payload
│   │   └── dispatcher.py              AlertDispatcher（过滤 NORMAL，路由异常信号）
│
└── tests/
    ├── test_indicators.py
    ├── test_market_maker.py
    ├── test_fetcher.py
    ├── test_storage.py
    ├── test_backtest.py
    ├── test_anomaly.py
    └── test_alerting.py
```

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         cs2-market data flow                             │
│                                                                          │
│  Steam Community Market                                                  │
│        │                                                                 │
│        ▼                                                                 │
│  SteamOrderBookFetcher ──────────────────────────────────────────────┐  │
│  (UA pool, retry, rate-limit)                                        │  │
│        │                                                             │  │
│        ▼                                                             ▼  │
│  QuantOrchestrator                                         OrderBookRepository
│  run_forever() → _scan_all_items()                         (PostgreSQL)     │
│        │                                                             │  │
│        ▼                                                             │  │
│  _process_item()                                                     │  │
│        │                                                             │  │
│        ├──► MarketAnomalyDetector ◄────────── fetch_recent_data() ──┘  │
│        │    (Isolation Forest)                                          │
│        │         │                                                      │
│        │         ▼                                                      │
│        │    AlertDispatcher                                             │
│        │    (suppress NORMAL)                                          │
│        │         │                                                      │
│        │         ▼                                                      │
│        │    DingTalkAlerter                                             │
│        │    (HMAC Webhook)                                              │
│        │                                                               │
│        └──► ItemHistory                                                 │
│                  │                                                      │
│                  ├──► indicators (SMA/EMA/RSI/MACD/BB)                  │
│                  ├──► market_maker_score()                              │
│                  ├──► generate_signals() → TradeSignal[]               │
│                  └──► run_backtest()     → BacktestResult              │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
git clone <repo-url>
cd cs2-market
pip install -r requirements.txt

# Run the offline demo (synthetic data, no HTTP requests)
python example.py
```

Sample output:

```
============================================================
  CS2 Quantitative Market Analyser
  Item: AK-47 | Redline (Field-Tested)
============================================================

[ Market Maker Detection (last 10 days) ]
  Day 115 | Price= 64.49 | Vol=  30 | MM Score=0.45 | Dir=LONG  ◄ ALERT
  ...

[ Trading Signals (last 10 days) ]
  BUY  | Price= 55.96 | Confidence=0.55 | Market maker LONG signal (score=0.55); ...
  ...

[ Backtest Results ]
  Initial capital : 500.00
  Final capital   : 494.87
  Total return    : -1.03%
  Trades executed : 1
  Win rate        : 0.00%
  Max drawdown    : 2.07%
```

---

## Installation

**Requirements**: Python 3.10+

```bash
pip install -r requirements.txt
```

| Package | Version | Purpose |
|---------|---------|---------|
| `requests` | ≥ 2.31 | HTTP client for Steam API |
| `numpy` | ≥ 1.24 | Numerical computation |
| `pandas` | ≥ 2.0 | DataFrame for anomaly features |
| `scikit-learn` | ≥ 1.3 | Isolation Forest |
| `psycopg2-binary` | ≥ 2.9 | PostgreSQL adapter |
| `pytest` | ≥ 7.4 | Test framework |

**Virtual environment (Windows)**:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## Module Reference

### 1. Data Models

#### `src/schemas/market.py` — `OrderBookSnapshot`

The **system-wide canonical data contract**, shared across acquisition, storage, and analysis layers.
Field names match the `order_book_snapshots` database columns exactly to eliminate naming drift.

```python
from src.schemas.market import OrderBookSnapshot

snap = OrderBookSnapshot(
    item_name="AK-47 | Redline (Field-Tested)",
    timestamp=1700000000,
    lowest_ask_price=65.50,
    highest_bid_price=64.00,
    ask_volume_top5=120,
    bid_volume_top5=85,
    total_sell_orders=430,
    total_buy_orders=210,
)

print(snap.spread)          # 1.5    (ask − bid)
print(snap.mid_price)       # 64.75  ((ask + bid) / 2)
print(snap.spread_ratio)    # 0.0234 ((ask − bid) / bid)
```

| Field | Type | Description |
|-------|------|-------------|
| `item_name` | `str` | Steam market hash name |
| `timestamp` | `int` | UTC Unix timestamp (seconds) |
| `lowest_ask_price` | `float` | Cheapest sell listing; `0.0` = no orders |
| `highest_bid_price` | `float` | Highest buy request; `0.0` = no orders |
| `ask_volume_top5` | `int` | Cumulative sell volume across top-5 price levels |
| `bid_volume_top5` | `int` | Cumulative buy volume across top-5 price levels |
| `total_sell_orders` | `int` | Total sell orders in the order book |
| `total_buy_orders` | `int` | Total buy orders in the order book |

#### `src/acquisition/models.py` — Price history models

```python
from src.acquisition.models import PriceRecord, ItemHistory, TradeSignal

record = PriceRecord(timestamp=1700000000, price=64.5, volume=12)
history = ItemHistory(item_name="AK-47 | Redline (Field-Tested)", records=[record, ...])

print(history.prices)      # [64.5, ...]
print(history.volumes)     # [12, ...]
print(history.timestamps)  # [1700000000, ...]
```

---

### 2. Data Acquisition

The acquisition pipeline is **two-phase**: first resolve item nameids (once), then poll the order-book API indefinitely.

#### Two-phase workflow

```
┌──────────────────────────────────────────────────────────┐
│  Phase 1: Initialization (run once)                      │
│                                                          │
│  NameIdInitializer.run(item_names)                       │
│    ├─ Cache hit  → skip, no HTTP                         │
│    └─ Cache miss → HTML request (5–10 s delay each)      │
│         └─ writes to _NameIdCache (disk-persistent JSON) │
└──────────────────────────────────────────────────────────┘
               ↓  initialization complete
┌──────────────────────────────────────────────────────────┐
│  Phase 2: Polling (runs forever)                         │
│                                                          │
│  QuantOrchestrator.run_forever(stop_event)               │
│    └─ every 750 s → _scan_all_items()                    │
│         └─ _process_item(item_name)                      │
│              ├─ SteamOrderBookFetcher.fetch_order_book() │
│              ├─ OrderBookRepository.insert_snapshot()    │
│              ├─ MarketAnomalyDetector.detect_anomalies() │
│              └─ AlertDispatcher.dispatch()               │
└──────────────────────────────────────────────────────────┘
```

#### Complete usage example

```python
import threading
from src.acquisition import SteamOrderBookFetcher, NameIdInitializer

ITEMS = [
    "AK-47 | Redline (Field-Tested)",
    "AWP | Asiimov (Field-Tested)",
]

# Phase 1: Resolve nameids (cached to disk)
fetcher = SteamOrderBookFetcher()
init = NameIdInitializer(fetcher)
result = init.run(ITEMS)

if not result.all_succeeded:
    raise RuntimeError(f"Init failed for: {list(result.failed)}")

print(f"Init: {len(result.from_cache)} cache hits, {len(result.resolved)} resolved")

# Phase 2: Run the full pipeline via QuantOrchestrator (main.py)
# QuantOrchestrator.run_forever() drives _scan_all_items() → _process_item()
# combining order-book fetching, anomaly detection, PostgreSQL storage, and alerting.
```

#### Offline pre-injection: `_NameIdCache.load_from_dict()`

If you already know item nameids (e.g. from a database), skip all HTML requests entirely:

```python
from src.acquisition import SteamOrderBookFetcher
from src.acquisition.cache import _NameIdCache
from pathlib import Path

cache = _NameIdCache(Path("my_cache.json"))
cache.load_from_dict({
    "AK-47 | Redline (Field-Tested)": 176923345,
    "AWP | Asiimov (Field-Tested)":   696692904,
})
# overwrite=True to force-update existing entries

fetcher = SteamOrderBookFetcher(cache=cache)
ob = fetcher.fetch_order_book("AK-47 | Redline (Field-Tested)")
print(ob.mid_price)
```

- `overwrite=False` (default): existing entries are not overwritten
- `overwrite=True`: force-update even already-cached nameids
- Returns the number of entries actually written to disk
- Entire batch is written atomically (`.tmp` + `os.replace`)

#### `NameIdInitializer` — `InitResult`

```python
@dataclass
class InitResult:
    resolved:   list[str]        # newly fetched via HTTP
    from_cache: list[str]        # found in local JSON cache
    failed:     dict[str, Exception]  # name → error

    @property
    def all_succeeded(self) -> bool: ...
```

#### Anti-ban design

| Request type | Delay strategy | Notes |
|---|---|---|
| HTML listing page (init) | 5–10 s random jitter | Steam rate-limits HTML more strictly than JSON |
| JSON API (polling) | 2–5 s random jitter | Applied between each item in `fetch_multiple()` |
| HTTP 429 retry | Exponential back-off (60 s × attempt) | Max 3 retries; raises `RuntimeError` after exhaustion |
| User-Agent pool | 7 real UA strings, random per request | Chrome/Firefox/Safari/Edge on Windows/macOS/Linux |
| Default poll interval | 750 s (12.5 minutes) | Midpoint of Steam's ~10–15 min rate-limit window |

#### `SteamHttpClient` — transport layer

The `SteamHttpClient` class handles all network transport concerns (UA rotation, proxy dispatch, 429 back-off) with **no knowledge of Steam API semantics**. `SteamOrderBookFetcher` depends on it via constructor injection, making unit testing straightforward with a mock session.

```python
from src.acquisition.http_client import SteamHttpClient, SteamOrderBookFetcher

# Optional: inject a proxy list
http_client = SteamHttpClient(proxies=["http://proxy1:8080", "http://proxy2:8080"])
fetcher = SteamOrderBookFetcher(http_client=http_client)
```

---

### 3. Technical Indicators

All functions in `src/analysis/indicators.py` accept plain Python `list[float]` and return a list of the same length. Positions within the warm-up period are filled with `None`.

```python
from src.analysis.indicators import sma, ema, rsi, macd, bollinger_bands, volume_ratio

prices = [60.0, 61.5, 63.0, 62.5, 64.0, 65.5, 63.5, 66.0, 67.0, 65.5]

# Simple / Exponential Moving Average
sma_5  = sma(prices, period=5)   # [None, None, None, None, 62.2, ...]
ema_5  = ema(prices, period=5)   # [None, None, None, None, 62.2, ...]

# RSI (default period=14; requires >14 data points)
rsi_14 = rsi(prices, period=14)

# MACD → dict with keys: macd_line, signal_line, histogram
m = macd(prices, fast=12, slow=26, signal_period=9)
print(m["histogram"][-1])

# Bollinger Bands → dict with keys: middle, upper, lower
bb = bollinger_bands(prices, period=20, num_std=2.0)
print(bb["upper"][-1], bb["lower"][-1])

# Volume ratio (current vol / rolling average vol)
volumes = [10, 12, 8, 15, 30, 11, 9, 14, 11, 10]
vr = volume_ratio(volumes, period=10)   # > 2.0 = volume spike
```

| Function | Warm-up rows | Return type |
|----------|-------------|-------------|
| `sma(prices, period)` | `period - 1` | `list[float \| None]` |
| `ema(prices, period)` | `period - 1` | `list[float \| None]` |
| `rsi(prices, period=14)` | `period` | `list[float \| None]` |
| `macd(prices, fast=12, slow=26, signal_period=9)` | `slow + signal_period - 2` | `dict` |
| `bollinger_bands(prices, period=20, num_std=2.0)` | `period - 1` | `dict` |
| `volume_sma(volumes, period=10)` | `period - 1` | `list[float \| None]` |
| `volume_ratio(volumes, period=10)` | `period - 1` | `list[float \| None]` |

---

### 4. Market Maker Detection

`market_maker_score()` combines four heuristics into a single **(score, direction)** pair per data point. A score above **0.4** is considered significant.

```python
from src.acquisition.models import ItemHistory, PriceRecord
from src.analysis.market_maker import market_maker_score

history = ItemHistory("AK-47 | Redline (FT)", records=[...])
scores = market_maker_score(history)

for score, direction in scores[-5:]:
    flag = " ◄ ALERT" if score >= 0.4 else ""
    print(f"Score={score:.2f}  Dir={direction}{flag}")
```

**Scoring weights:**

| Signal | Weight | Trigger condition |
|--------|--------|-------------------|
| Volume spike | **35%** | Current volume ≥ 2× rolling 10-day average |
| Price momentum | **35%** | ≥ 5% price move over 3 days (UP or DOWN) |
| Bollinger breakout | **20%** | Price outside 2σ bands |
| RSI extreme | **10%** | RSI ≥ 70 (overbought → LONG) or ≤ 30 (oversold → SHORT) |

**Direction logic:**

- `"LONG"` — more LONG votes than SHORT votes (accumulation)
- `"SHORT"` — more SHORT votes than LONG votes (distribution)
- `"NEUTRAL"` — tied or no directional votes

Individual detectors are also available:

```python
from src.analysis.market_maker import (
    detect_volume_spikes,    # list[bool]
    detect_price_momentum,   # list["UP" | "DOWN" | None]
    detect_bollinger_breakout,  # list["UP" | "DOWN" | None]
)
```

---

### 5. Trading Signals

`generate_signals()` combines the market-maker score with MACD and Bollinger Band confirmation to produce a `TradeSignal` for each data point.

**BUY conditions** (all must be true):
1. MM score ≥ `mm_threshold` (default 0.4) AND direction is `LONG`
2. RSI < `rsi_overbought` (default 70) — not yet overbought
3. MACD histogram > 0 — upward momentum confirmed
4. Price > BB middle band — price is extended above the mean

**SELL conditions** (all must be true):
1. MM score ≥ `mm_threshold` AND direction is `SHORT`
2. RSI > `rsi_oversold` (default 30) — not yet oversold
3. MACD histogram < 0 — downward momentum confirmed
4. Price < BB middle band — price is below the mean

```python
from src.acquisition.models import ItemHistory
from src.strategy.signal import generate_signals, latest_signal

history = ItemHistory("AK-47 | Redline (FT)", records=[...])

# All signals
signals = generate_signals(history, mm_threshold=0.4)
for sig in signals[-5:]:
    print(f"{sig.action:4s} | Price={sig.price:.2f} | Confidence={sig.confidence:.2f} | {sig.reason}")

# Latest signal only
sig = latest_signal(history)
print(sig.action, sig.confidence, sig.reason)
```

**`TradeSignal` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `item_name` | `str` | Market hash name |
| `timestamp` | `float` | Unix timestamp of the record |
| `action` | `str` | `"BUY"`, `"SELL"`, or `"HOLD"` |
| `confidence` | `float` | Market-maker score (0.0–1.0) |
| `reason` | `str` | Human-readable explanation |
| `price` | `float \| None` | Price at signal time |

---

### 6. Backtesting

`run_backtest()` simulates executing signals on historical data and reports portfolio performance metrics.

```python
from src.acquisition.models import ItemHistory
from src.backtest.engine import run_backtest

history = ItemHistory("AK-47 | Redline (FT)", records=[...])
result = run_backtest(history, initial_capital=1000.0, transaction_cost=0.15)

print(f"Return    : {result.total_return:.1%}")
print(f"Trades    : {result.num_trades}")
print(f"Win rate  : {result.win_rate:.1%}")
print(f"Max DD    : {result.max_drawdown:.1%}")

for trade in result.trades:
    print(f"  Buy={trade.buy_price:.2f} → Sell={trade.sell_price:.2f} | P&L={trade.profit_pct:.1%}")
```

**Key assumptions:**

- Only one unit held at a time (no fractional positions)
- **15% transaction cost** by default (Steam's cut on sales)
- Short-selling is not supported
- An open position at backtest end is liquidated at the last available price

> **Important**: Steam charges a 13–15% seller fee. An item must gain at least **~18% nominally** to break even after fees. This is why flat-price or low-volatility items generate zero profitable trades.

**`BacktestResult` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `initial_capital` | `float` | Starting cash |
| `final_capital` | `float` | Ending cash |
| `total_return` | `float` | `(final − initial) / initial` |
| `num_trades` | `int` | Completed buy+sell round trips |
| `win_rate` | `float` | Fraction of profitable trades |
| `max_drawdown` | `float` | Max peak-to-trough equity decline |
| `trades` | `list[Trade]` | Individual trade records |

---

### 7. Anomaly Detection

`MarketAnomalyDetector` (in `src.analysis.anomaly`) fits an **Isolation Forest** over four order-book microstructure features to surface market-maker events without labeled data.

#### Four engineered features (`engineer_features(df)`)

| Feature | Formula | Warm-up rows | Meaning |
|---------|---------|-------------|---------|
| `obi` | `(bid_vol5 − ask_vol5) / (bid_vol5 + ask_vol5)` | 0 (NaN when both=0) | Order Book Imbalance — positive = buy-side pressure |
| `spread_ratio` | `(ask_price − bid_price) / bid_price` | 0 (NaN when bid=0) | Relative bid-ask spread; widens during uncertainty |
| `sdr` | `(supply_ma_6 − sell_orders) / supply_ma_6` | 5 | Supply Deviation Ratio — positive = sudden supply collapse |
| `price_momentum_dev` | `(bid_price − bid_ma_12) / bid_ma_12` | 11 | Deviation of current bid from 12-period MA |

**Minimum data requirement**: `_MIN_ROWS = 12` clean rows after NaN-dropping (driven by the 12-period MA warm-up).

#### Four signal types

| Signal | Condition | Meaning |
|--------|-----------|---------|
| `"NORMAL"` | Isolation Forest label == 1 | No anomaly detected |
| `"ACCUMULATION"` | label == -1, `sdr > 0.10` AND `obi > 0.5` | 建仓扫货 — large buyer absorbing supply |
| `"DUMP_RISK"` | label == -1, `obi < -0.6` AND `spread_ratio > 0.05` | 撤单/砸盘 — bid side collapsing |
| `"IRREGULAR"` | label == -1, neither above | Unusual microstructure, unclassified |

#### Usage

```python
from src.analysis.anomaly import MarketAnomalyDetector

db_config = {
    "host": "localhost",
    "dbname": "cs2market",
    "user": "postgres",
    "password": "secret",
    "port": 5432,
}

detector = MarketAnomalyDetector(db_config)
result = detector.detect_anomalies(item_nameid=176923345)

if result is None:
    print("Not enough data (< 12 clean rows)")
else:
    print(result["signal_type"])    # "ACCUMULATION", "DUMP_RISK", "IRREGULAR", or "NORMAL"
    print(result["anomaly_score"])  # continuous score (more negative = more anomalous)
    print(result["obi"])
    print(result["sdr"])
    print(result["spread_ratio"])
    print(result["price_momentum_dev"])
    print(result["timestamp"])      # ISO-8601 string
```

#### Result dict schema

| Key | Type | Description |
|-----|------|-------------|
| `timestamp` | `str` | ISO-8601 timestamp of the latest row |
| `anomaly_score` | `float` | Isolation Forest score (lower = more anomalous) |
| `obi` | `float` | Order Book Imbalance at latest row |
| `spread_ratio` | `float` | Bid-ask spread ratio at latest row |
| `sdr` | `float` | Supply Deviation Ratio at latest row |
| `price_momentum_dev` | `float` | Price momentum deviation at latest row |
| `signal_type` | `str` | `"NORMAL"` / `"ACCUMULATION"` / `"DUMP_RISK"` / `"IRREGULAR"` |

---

### 8. Storage

The storage layer uses **PostgreSQL** with `psycopg2`. Schema is auto-initialized on first `connect()`.

#### Database schema

```sql
-- Item metadata
CREATE TABLE items (
    item_nameid       BIGINT       PRIMARY KEY,
    market_hash_name  VARCHAR(255) NOT NULL UNIQUE,
    added_at          TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP
);

-- Order book time series
CREATE TABLE order_book_snapshots (
    time               TIMESTAMPTZ NOT NULL,
    item_nameid        BIGINT      NOT NULL REFERENCES items(item_nameid),
    lowest_ask_price   NUMERIC(10, 2),   -- NULL when no sell orders
    highest_bid_price  NUMERIC(10, 2),   -- NULL when no buy orders
    ask_volume_top5    INT,
    bid_volume_top5    INT,
    total_sell_orders  INT,
    total_buy_orders   INT
);

CREATE INDEX idx_item_time ON order_book_snapshots (item_nameid, time DESC);
```

> Prices of `0.0` are stored as `NULL` (meaning no orders on that side).

#### Usage

```python
from src.storage.database import DatabaseConnection
from src.storage.repository import OrderBookRepository

db_config = {"host": "localhost", "dbname": "cs2market", "user": "postgres", "password": "secret"}

with DatabaseConnection(db_config) as db:
    repo = OrderBookRepository(db.connection)

    # Register item metadata (idempotent)
    repo.init_item_metadata(item_nameid=176923345, market_hash_name="AK-47 | Redline (Field-Tested)")

    # Single insert
    repo.insert_snapshot(snapshot, item_nameid=176923345)

    # Bulk insert (returns rows inserted)
    nameid_map = {"AK-47 | Redline (Field-Tested)": 176923345}
    n = repo.insert_snapshots_bulk(snapshots, nameid_map)
    print(f"Inserted {n} rows")

    # Query latest
    row = repo.get_latest_snapshot(item_nameid=176923345)
    print(row)  # dict or None
```

#### `OrderBookRepository` API

| Method | Description |
|--------|-------------|
| `init_item_metadata(item_nameid, market_hash_name)` | `INSERT ... ON CONFLICT DO NOTHING` |
| `insert_snapshot(snapshot, item_nameid)` | Insert a single `OrderBookSnapshot` |
| `insert_snapshots_bulk(snapshots, nameid_map)` | Batch insert; returns row count |
| `get_latest_snapshot(item_nameid)` | Returns `dict` or `None` |

---

### 9. Alerting

The alerting stack routes anomaly-detector results to a **DingTalk group robot** via Webhook, with optional HMAC-SHA256 signing.

#### Components

| Class / Function | Role |
|---------|------|
| `DingTalkAlerter` | Low-level Webhook POST with optional HMAC-SHA256 signed URL |
| `format_anomaly_alert()` | Pure function: `(item_name, result_dict) → DingTalk Markdown payload` |
| `AlertDispatcher` | Filters `"NORMAL"` results; routes non-normal signals through the alerter |

#### End-to-end usage

```python
from src.alerting.bot import DingTalkAlerter
from src.alerting.dispatcher import AlertDispatcher
from src.analysis.anomaly import MarketAnomalyDetector

# 1. Configure alerter (with optional HMAC signing)
alerter = DingTalkAlerter(
    webhook_url="https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN",
    secret="SECxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",  # optional
)

# 2. Wrap in dispatcher (suppresses NORMAL results automatically)
dispatcher = AlertDispatcher(alerter)

# 3. Run detector and dispatch
detector = MarketAnomalyDetector(db_config)
result = detector.detect_anomalies(item_nameid=176923345)

sent = dispatcher.dispatch("AK-47 | Redline (Field-Tested)", result)
print("Alert sent" if sent else "No alert (NORMAL or insufficient data)")
```

#### DingTalk message format

`format_anomaly_alert()` builds a Markdown message with a colour-coded signal label and a metrics table:

```
## 🔴 建仓扫货 (ACCUMULATION)

**饰品：** AK-47 | Redline (Field-Tested)
**时间：** 2026-03-06T12:00:00+00:00
**摘要：** 检测到大量买单堆积，疑似盘主入场吸筹。

---

### 📊 订单簿指标

| 指标           | 数值   |
|----------------|--------|
| OBI（订单失衡）| 0.6200 |
| SDR（供应萎缩率）| 0.1500 |
| 价差比率       | 0.0180 |
| 价格动量偏差   | 0.0320 |
| 异常得分       | -0.1240|
```

| Signal type | Icon | Summary |
|-------------|------|---------|
| `ACCUMULATION` | 🔴 | 建仓扫货 — large buyer absorbing supply |
| `DUMP_RISK` | 🟠 | 撤单砸盘风险 — bid collapse / dumping risk |
| `IRREGULAR` | 🟡 | 异常波动 — unclassified anomaly |
| `NORMAL` | 🟢 | 正常 — no anomaly (suppressed by dispatcher) |

#### `DingTalkAlerter` API

| Method | Description |
|--------|-------------|
| `send(payload: dict) → bool` | POST a fully-formed DingTalk message payload |
| `send_text(message: str) → bool` | Convenience: send a plain-text message |

Returns `True` on success (HTTP 200 + `errcode == 0`), `False` on any error.

---

## Import Paths & Compatibility

### Canonical paths (use these in new code)

```python
from src.acquisition import SteamOrderBookFetcher, NameIdInitializer
from src.acquisition.models import PriceRecord, ItemHistory, TradeSignal
from src.acquisition.cache import _NameIdCache
from src.schemas.market import OrderBookSnapshot
from src.analysis.indicators import sma, ema, rsi, macd, bollinger_bands
from src.analysis.market_maker import market_maker_score
from src.analysis.anomaly import MarketAnomalyDetector
from src.strategy.signal import generate_signals, latest_signal
from src.backtest.engine import run_backtest
from src.backtest.models import Trade, BacktestResult
from src.storage.database import DatabaseConnection
from src.storage.repository import OrderBookRepository
from src.alerting.bot import DingTalkAlerter
from src.alerting.formatter import format_anomaly_alert
from src.alerting.dispatcher import AlertDispatcher
```

### Compatibility layers (kept for backwards compatibility)

| Old path | Redirects to |
|----------|--------------|
| `from src.acquisition.models import OrderBook` | `src.schemas.market.OrderBookSnapshot` |

---

## Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Single test file
python -m pytest tests/test_indicators.py -v

# Single test case
python -m pytest tests/test_indicators.py::TestSMA::test_sma_basic -v

# Windows (using venv)
.venv\Scripts\python -m pytest tests/ -v
```

The test suite has **161 tests** covering all modules. All database and network I/O is mocked with `MagicMock` — no real connections are needed to run the tests.

Test file map:

| File | Covers |
|------|--------|
| `test_indicators.py` | SMA, EMA, RSI, MACD, Bollinger Bands, volume ratio |
| `test_market_maker.py` | Volume spike, momentum, BB breakout, composite score |
| `test_fetcher.py` | `SteamOrderBookFetcher`, `NameIdInitializer`, `_NameIdCache` |
| `test_storage.py` | `DatabaseConnection`, `OrderBookRepository` |
| `test_backtest.py` | `run_backtest()`, `Trade`, `BacktestResult` |
| `test_anomaly.py` | `engineer_features()`, `MarketAnomalyDetector` |
| `test_alerting.py` | `DingTalkAlerter`, `format_anomaly_alert()`, `AlertDispatcher` |

---

## Notes and Risks

- **Steam's 13–15% transaction fee** means every sold item loses ~15% of its value. A position must gain at least **~18% nominally** just to break even. Consider third-party platforms with lower fees (e.g. Buff163 charges ~2.5%).
- **Daily data granularity**: Price history from Steam is aggregated daily. For higher-frequency strategies, you need a third-party data provider.
- **No short-selling**: The CS2 market does not support shorting. The backtest engine only simulates long positions.
- **Isolation Forest is unsupervised**: The anomaly detector has no ground truth. `contamination=0.05` assumes ~5% of data points are anomalous. Tune this for your dataset.
- **Rate limiting**: Steam aggressively rate-limits the market API. The default 750-second polling interval is conservative but safe. Reducing it significantly increases ban risk.
- **Educational / research purposes only**: Past performance does not guarantee future results. Always manage risk carefully. This toolkit does not constitute financial advice.
