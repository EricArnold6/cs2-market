# cs2-market

A quantitative trading toolkit for the **CS2 (CS:GO) cosmetics market**.

The library helps you detect **market maker (盘主) activity** — large traders
who drive short-term price swings by going long or short on specific items —
and generates **trading signals** you can act on or back-test.

---

## Features

| Module | Description |
|---|---|
| `src/acquisition/models.py` | Data models: `PriceRecord`, `ItemHistory`, `TradeSignal`, `OrderBook` |
| `src/acquisition/http_client.py` | `SteamOrderBookFetcher`: real-time order-book snapshots (UA pool, retry, rate-limit) |
| `src/acquisition/initializer.py` | `NameIdInitializer`: batch pre-resolve item nameids before polling |
| `src/acquisition/scheduler.py` | `PollingScheduler`: periodic order-book polling with callback |
| `src/analysis/indicators.py` | Technical indicators: SMA, EMA, RSI, MACD, Bollinger Bands, volume ratio |
| `src/analysis/market_maker.py` | Market maker detection: volume spikes, price momentum, BB breakouts, composite score |
| `src/analysis/strategy/signal.py` | Trading signal generator (BUY / SELL / HOLD) |
| `src/analysis/backtest/engine.py` | Backtesting engine with P&L, win rate, and max-drawdown metrics |

---

## Project Structure

```
cs2-market/
├── README.md
├── example.py                         离线演示脚本（合成数据，无真实 HTTP）
├── requirements.txt
├── .gitignore
└── src/
    ├── __init__.py
    │
    ├── acquisition/                   ══ 模块一：数据获取与清洗 ══
    │   ├── __init__.py                re-export 所有公开符号（向后兼容枢纽）
    │   ├── exceptions.py              NameIdExtractionError, NameIdNotInitializedError
    │   ├── models.py                  PriceRecord, ItemHistory, TradeSignal, OrderBook
    │   ├── cache.py                   _NameIdCache（持久化 JSON 缓存）
    │   ├── http_client.py             SteamOrderBookFetcher（UA 池、重试、限速常量）
    │   ├── initializer.py             NameIdInitializer, InitResult
    │   ├── fetcher.py                 已废弃函数 + re-export（向后兼容层）
    │   └── scheduler.py               PollingScheduler
    │
    ├── storage/                       ══ 模块二：数据存储（预留）══
    │   ├── __init__.py                占位注释（PostgreSQL + TimescaleDB）
    │   ├── database.py                占位：连接池、会话管理
    │   └── repository.py              占位：CRUD / hypertable 接口
    │
    ├── analysis/                      ══ 模块三：特征工程与模型策略 ══
    │   ├── __init__.py
    │   ├── indicators.py              SMA / EMA / RSI / MACD / BB / 成交量
    │   ├── market_maker.py            放量 / 动量 / BB突破 / 合成评分
    │   ├── strategy/
    │   │   ├── __init__.py
    │   │   └── signal.py              generate_signals, latest_signal
    │   └── backtest/
    │       ├── __init__.py
    │       ├── models.py              Trade, BacktestResult
    │       └── engine.py              run_backtest
    │
    ├── alerting/                      ══ 模块四：预警与交互系统（预留）══
    │   ├── __init__.py                占位注释（Telegram Bot）
    │   ├── bot.py                     占位：TelegramBot 类
    │   ├── formatter.py               占位：TradeSignal → Markdown 消息模板
    │   └── dispatcher.py              占位：预警规则引擎、消息路由
    │
    └── data/                          ══ 兼容层（过渡期保留）══
        ├── __init__.py                从 src.acquisition 全量 re-export
        ├── fetcher.py                 re-export → src.acquisition.fetcher
        ├── models.py                  re-export → src.acquisition.models
        └── scheduler.py               re-export → src.acquisition.scheduler
```

> **兼容层说明**：`src/data/` 中的旧导入路径（`from src.data.fetcher import ...`）在过渡期内仍然有效，
> 所有符号均透明转发至 `src/acquisition/`，无需修改调用方代码。

---

## Quick Start

```bash
pip install -r requirements.txt
python example.py
```

Sample output:
```
============================================================
  CS2 Quantitative Market Analyser
  Item: AK-47 | Redline (Field-Tested)
============================================================

[ Market Maker Detection (last 10 days) ]
  Day 115 | Price= 64.49 | Vol=  30 | MM Score=0.45 | Dir=LONG ◄ ALERT
  ...

[ Trading Signals (last 10 days) ]
  BUY  | Price= 55.96 | Confidence=0.55 | Market maker LONG signal (score=0.55); ...
  ...

[ Backtest Results ]
  Initial capital : 500.00
  Final capital   : 494.87
  Total return    : -1.03%
  Trades executed : 1
  ...
```

---

## How It Works

### 1. 模块一：数据获取与清洗 (`src/acquisition/`)

#### 两阶段工作流

```
┌─────────────────────────────────────────────────────────┐
│  阶段一：初始化（只运行一次）                             │
│                                                         │
│  NameIdInitializer.run(item_names)                      │
│    ├─ 缓存命中 → 跳过，无 HTTP                           │
│    └─ 缓存未命中 → HTML 请求，间隔 5-10 s               │
│         └─ 写入 _NameIdCache（磁盘持久化 JSON）          │
└─────────────────────────────────────────────────────────┘
              ↓  初始化完成后
┌─────────────────────────────────────────────────────────┐
│  阶段二：轮询（持续运行）                                 │
│                                                         │
│  PollingScheduler.run_forever(stop_event)               │
│    └─ 每隔 750 s 调用一次 poll_once()                   │
│         └─ SteamOrderBookFetcher.fetch_multiple()       │
│              └─ fetch_order_book(item_name)             │
│                   ├─ cache.get(item_name) → nameid      │
│                   └─ JSON API 请求 → OrderBook          │
└─────────────────────────────────────────────────────────┘
```

#### 完整用法示例

```python
import threading
from src.acquisition import (
    SteamOrderBookFetcher,
    NameIdInitializer,
    PollingScheduler,
)

ITEMS = [
    "AK-47 | Redline (Field-Tested)",
    "AWP | Asiimov (Field-Tested)",
]

# 阶段一：初始化（批量解析 item_nameid）
fetcher = SteamOrderBookFetcher()
init = NameIdInitializer(fetcher)
result = init.run(ITEMS)

if not result.all_succeeded:
    raise RuntimeError(f"Init failed for: {list(result.failed)}")

print(f"Init: {len(result.from_cache)} cache hits, {len(result.resolved)} resolved")

# 阶段二：启动轮询循环
def on_snapshot(snapshots):
    for ob in snapshots:
        print(f"{ob.item_name}: ask=${ob.lowest_ask_price:.2f} bid=${ob.highest_bid_price:.2f}")

stop = threading.Event()
sched = PollingScheduler(fetcher, ITEMS, on_snapshot=on_snapshot)
t = threading.Thread(target=sched.run_forever, kwargs={"stop_event": stop}, daemon=True)
t.start()

# ... 若干时间后 ...
stop.set()
t.join()
```

#### 离线预注入：`_NameIdCache.load_from_dict()`

若已知道饰品的 nameid（例如从数据库读取），可完全跳过 HTML 请求，直接注入缓存：

```python
from src.acquisition import SteamOrderBookFetcher
from src.acquisition.cache import _NameIdCache
from pathlib import Path

cache = _NameIdCache(Path("my_cache.json"))
# 批量预注入，无需任何网络请求
cache.load_from_dict({
    "AK-47 | Redline (Field-Tested)": 176923345,
    "AWP | Asiimov (Field-Tested)":   696692904,
})

fetcher = SteamOrderBookFetcher(cache=cache)
ob = fetcher.fetch_order_book("AK-47 | Redline (Field-Tested)")
print(ob.mid_price)
```

参数说明：
- `overwrite=False`（默认）：已存在的条目不被覆盖
- `overwrite=True`：强制更新缓存中的 nameid
- 返回值：实际写入磁盘的条目数（全部命中时为 0）
- 整批只写一次磁盘（原子操作，`.tmp` 临时文件 + `os.replace`）

#### 防封控设计

| 请求类型 | 延迟策略 | 说明 |
|---|---|---|
| HTML 陈列页（初始化） | 5–10 s 随机间隔 | Steam 对 HTML 限速更严，初始化阶段每次请求后等待 |
| JSON API（轮询） | 2–5 s 随机间隔 | `fetch_multiple` 内置，每个饰品之间随机等待 |
| 429 重试 | 指数退避（60 s × attempt） | 最多重试 3 次，超限抛出 `RuntimeError` |
| User-Agent 池 | 7 个真实 UA 随机轮换 | Chrome/Firefox/Safari/Edge，Windows/macOS/Linux |
| 默认轮询间隔 | 750 s（12.5 分钟） | Steam 官方频率限制约 10–15 分钟/次 |

---

### 2. Market Maker Detection

`market_maker_score()` combines four heuristics into a single
**(score, direction)** pair for every data point:

| Signal | Weight | Logic |
|---|---|---|
| Volume spike | 35 % | Volume ≥ 2× rolling 10-day average |
| Price momentum | 35 % | ≥ 5 % move over 3 days |
| Bollinger breakout | 20 % | Price outside 2σ bands |
| RSI extreme | 10 % | RSI ≥ 70 (long) or ≤ 30 (short) |

A score above **0.4** with direction **LONG** suggests a large buyer is
accumulating; **SHORT** suggests distribution/selling.

```python
from src.analysis.market_maker import market_maker_score

scores = market_maker_score(history)
for score, direction in scores[-3:]:
    print(f"Score={score:.2f}  Direction={direction}")
```

### 3. Trading Signals

`generate_signals()` combines the market-maker score with MACD and Bollinger
Band confirmation to produce a `TradeSignal` for each day:

- **BUY**: MM score ≥ threshold, direction LONG, RSI not overbought, MACD
  histogram positive, price above BB middle
- **SELL**: MM score ≥ threshold, direction SHORT, RSI not oversold, MACD
  histogram negative, price below BB middle
- **HOLD**: all other cases

```python
from src.analysis.strategy.signal import latest_signal

signal = latest_signal(history)
print(signal.action, signal.confidence, signal.reason)
```

### 4. Backtesting

`run_backtest()` simulates executing signals on historical data and reports
performance metrics. The default transaction cost is **15 %** (Steam's cut),
so significant price moves are needed to profit.

```python
from src.analysis.backtest.engine import run_backtest

result = run_backtest(history, initial_capital=1000.0)
print(f"Return: {result.total_return:.1%}, Trades: {result.num_trades}, Win rate: {result.win_rate:.1%}")
```

---

## Running Tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

---

## Notes and Risks

- **Steam Market** charges a **13–15 % transaction fee** on every sale. A
  position must gain at least ~18 % nominally just to break even. Consider
  using third-party platforms with lower fees (e.g. Buff163 charges ~2.5 %).
- Price history from Steam is **daily** granularity. For higher-frequency
  strategies, you would need a third-party data provider.
- This toolkit is for **educational / research purposes**. Past performance
  does not guarantee future results. Always manage risk carefully.
