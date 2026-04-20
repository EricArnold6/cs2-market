# cs2-market

量化交易工具包，用于 **CS2（反恐精英 2）饰品市场**的盘主（庄家）行为检测与交易信号生成。

系统接入 **CSQAQ** 数据平台，以 3 分钟为周期轮询 BUFF 订单簿，通过孤立森林异常检测 + K线量价确认 + 巨鲸筹码追踪三级验证，向钉钉群机器人推送高置信度告警。

---

## 目录

- [功能概览](#功能概览)
- [项目结构](#项目结构)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
- [安装](#安装)
- [配置](#配置)
- [模块参考](#模块参考)
  - [1. 数据模型](#1-数据模型)
  - [2. 数据获取](#2-数据获取)
  - [3. 技术指标](#3-技术指标)
  - [4. 盘主评分](#4-盘主评分)
  - [5. 交易信号](#5-交易信号)
  - [6. 回测引擎](#6-回测引擎)
  - [7. 异常检测](#7-异常检测)
  - [8. 数据存储](#8-数据存储)
  - [9. 告警推送](#9-告警推送)
- [运行测试](#运行测试)
- [注意事项](#注意事项)

---

## 功能概览

| 模块 | 描述 |
|------|------|
| `src/acquisition/csqaq_client.py` | CSQAQ REST API 客户端（批量价格、K线、巨鲸排行） |
| `src/acquisition/cache.py` | 持久化 JSON 缓存（中文名 ↔ 英文名 ↔ CSQAQ ID） |
| `src/acquisition/initializer.py` | 三级解析：缓存 → 本地 TXT → HTTP |
| `src/schemas/market.py` | `OrderBookSnapshot` — 全系统共享数据契约 |
| `src/analysis/indicators.py` | SMA / EMA / RSI / MACD / 布林带 / 量比 |
| `src/analysis/market_maker.py` | 量价突破、动量、布林突破、综合盘主评分 |
| `src/analysis/anomaly/` | `MarketAnomalyDetector` — 孤立森林 + Z-Score + K线确认 + 巨鲸追踪 |
| `src/analysis/prediction/whale_tracker.py` | `WhaleTracker` — 大户筹码动态追踪 |
| `src/strategy/signal.py` | `generate_signals()` / `latest_signal()` → BUY / SELL / HOLD |
| `src/backtest/` | `run_backtest()` — P&L、胜率、最大回撤 |
| `src/storage/` | PostgreSQL 持久化（`DatabaseConnection`、`OrderBookRepository`） |
| `src/alerting/` | 钉钉 Webhook 告警（HMAC-SHA256 签名、熔断机制） |

---

## 项目结构

```
cs2-market/
├── README.md
├── CLAUDE.md                          # AI 助手指令
├── main.py                            # 生产入口 — QuantOrchestrator
├── example.py                         # 离线演示（合成数据，无 HTTP）
├── requirements.txt
├── config/
│   └── settings.json                  # 数据库 / 钉钉 / CSQAQ 配置（勿提交）
├── data/
│   ├── nameid_cache.json              # 持久化 ID 缓存（自动维护）
│   └── 饰品id（更新时间2026-01-23）.txt  # 本地 ID 离线文件（可选）
│
├── src/
│   ├── schemas/
│   │   └── market.py                  # OrderBookSnapshot（单一数据契约）
│   ├── acquisition/
│   │   ├── csqaq_client.py            # CSQAQClient — REST API 客户端
│   │   ├── cache.py                   # _NameIdCache — 持久化 JSON 缓存
│   │   ├── initializer.py             # NameIdInitializer — 三级解析
│   │   ├── models.py                  # PriceRecord, ItemHistory, TradeSignal
│   │   └── exceptions.py             # NameIdExtractionError, NameIdNotInitializedError
│   ├── analysis/
│   │   ├── indicators.py              # SMA / EMA / RSI / MACD / BB / 量比
│   │   ├── market_maker.py            # 量价突破 / 动量 / BB突破 / 综合评分
│   │   ├── anomaly/
│   │   │   ├── features.py            # engineer_features() → 7特征 + Z-Score
│   │   │   └── detector.py            # MarketAnomalyDetector
│   │   └── prediction/
│   │       └── whale_tracker.py       # WhaleTracker — 大户筹码追踪
│   ├── strategy/
│   │   └── signal.py                  # generate_signals(), latest_signal()
│   ├── backtest/
│   │   ├── engine.py                  # run_backtest()
│   │   └── models.py                  # Trade, BacktestResult
│   ├── storage/
│   │   ├── database.py                # DatabaseConnection（psycopg2, autocommit）
│   │   └── repository.py              # OrderBookRepository（CRUD, 批量写入）
│   └── alerting/
│       ├── bot.py                     # DingTalkAlerter（HMAC-SHA256 Webhook）
│       ├── formatter.py               # format_anomaly_alert() → 钉钉 Markdown
│       └── dispatcher.py              # AlertDispatcher（过滤 + 熔断）
│
└── tests/
    ├── test_acquisition.py            # _NameIdCache, CSQAQClient
    ├── test_indicators.py             # SMA / EMA / RSI / MACD / BB / 量比
    ├── test_market_maker.py           # 盘主评分各子检测器
    ├── test_backtest.py               # run_backtest()
    ├── test_anomaly.py                # 特征工程 + MarketAnomalyDetector
    ├── test_alerting.py               # DingTalkAlerter, formatter, dispatcher
    └── test_storage.py                # DatabaseConnection, OrderBookRepository
```

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         cs2-market 数据流（V4.0）                             │
│                                                                              │
│  CSQAQ REST API                                                              │
│        │                                                                     │
│        ▼                                                                     │
│  CSQAQClient.fetch_batch_prices()          CSQAQClient.fetch_kline_data()   │
│  （批量获取 BUFF 订单簿快照）               （K线量价，二次确认）              │
│        │                                           │                         │
│        ▼                                           │                         │
│  QuantOrchestrator._scan_all_items_batch()         │                         │
│        │                                           │                         │
│        ├──► OrderBookRepository.insert_snapshot()  │                         │
│        │    （PostgreSQL 持久化）                   │                         │
│        │                                           │                         │
│        └──► MarketAnomalyDetector.detect_anomalies()                        │
│                  │                                                           │
│                  ├─ 1. IsolationForest（孤立森林基础检测）                    │
│                  ├─ 2. _verify_volume_breakout()  ◄── K线放量确认             │
│                  └─ 3. WhaleTracker.calculate_accumulation_index()           │
│                            （大户筹码净流入确认）                              │
│                                 │                                            │
│                                 ▼                                            │
│                       信号类型输出                                            │
│                       WHALE_CONFIRMED_BUY / ACCUMULATION /                   │
│                       DUMP_RISK / ARBITRAGE_OPPORTUNITY /                    │
│                       IRREGULAR / NORMAL                                     │
│                                 │                                            │
│                                 ▼                                            │
│                       AlertDispatcher.dispatch()                             │
│                       （过滤 NORMAL + IRREGULAR，熔断拦截逆势做多）            │
│                                 │                                            │
│                                 ▼                                            │
│                       DingTalkAlerter.send()                                 │
│                       （HMAC-SHA256 签名 Webhook）                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 快速开始

```bash
git clone <repo-url>
cd cs2-market

# 安装依赖
pip install -r requirements.txt

# 编辑配置（填入真实的数据库和 API 凭据）
# 见下方「配置」章节

# 启动生产监控守护进程
python main.py
```

**离线演示**（合成数据，无任何 HTTP 请求）：

```bash
python example.py
```

---

## 安装

**要求**：Python 3.10+

```bash
pip install -r requirements.txt
```

| 包 | 版本要求 | 用途 |
|----|---------|------|
| `requests` | ≥ 2.31 | HTTP 客户端（CSQAQ API） |
| `numpy` | ≥ 1.24 | 数值计算 |
| `pandas` | ≥ 2.0 | 特征工程 DataFrame |
| `scikit-learn` | ≥ 1.3 | Isolation Forest |
| `sqlalchemy` | ≥ 2.0 | 异常检测模块的 DB 查询层 |
| `psycopg2-binary` | ≥ 2.9 | PostgreSQL 适配器 |
| `pytest` | ≥ 7.4 | 测试框架 |

**Windows 虚拟环境**：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## 配置

所有配置集中在 `config/settings.json`：

```json
{
    "database": {
        "dbname": "postgres",
        "user": "postgres",
        "password": "YOUR_PASSWORD",
        "host": "localhost",
        "port": 5432
    },
    "dingtalk": {
        "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN",
        "secret": "SECxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    },
    "system": {
        "scan_interval_minutes": 3
    },
    "csqaq": {
        "api_token": "YOUR_CSQAQ_API_TOKEN",
        "vip_token": "YOUR_CSQAQ_VIP_TOKEN",
        "base_url_public": "https://api.csqaq.com/api/v1",
        "base_url_vip": "https://private-api.csqaq.com/api/v1"
    },
    "target_items": [
        "AK-47 | 表面淬火 (崭新出厂)",
        "M4A4 | 地狱烈焰 (久经沙场)"
    ]
}
```

> ⚠️ **安全提示**：`settings.json` 包含真实凭据，已加入 `.gitignore`，请勿提交到版本库。

---

## 模块参考

### 1. 数据模型

#### `src/schemas/market.py` — `OrderBookSnapshot`

全系统共享的数据契约，字段名与 PostgreSQL 表列名完全一致。

```python
from src.schemas.market import OrderBookSnapshot

snap = OrderBookSnapshot(
    item_name="AK-47 | Redline (Field-Tested)",
    timestamp=1700000000.0,
    lowest_ask_price=65.50,   # BUFF 最低卖价；0.0 = 无挂单
    highest_bid_price=64.00,  # BUFF 最高买价；0.0 = 无求购
    total_sell_orders=430,
    total_buy_orders=210,
    yyyp_sell_price=64.00,    # YYYP 平台卖价（用于跨平台套利检测）
    yyyp_lease_price=0.30,    # YYYP 租金日价
)

print(snap.spread)         # 1.50   (卖价 - 买价)
print(snap.mid_price)      # 64.75  ((卖价 + 买价) / 2)
print(snap.spread_ratio)   # 0.0234 ((卖价 - 买价) / 买价)
```

#### `src/acquisition/models.py` — 历史价格模型

```python
from src.acquisition.models import PriceRecord, ItemHistory, TradeSignal

record = PriceRecord(timestamp=1700000000.0, price=64.5, volume=12)
history = ItemHistory(item_name="AK-47 | Redline (Field-Tested)", records=[record])

print(history.prices)      # [64.5]
print(history.volumes)     # [12]
print(history.timestamps)  # [1700000000.0]
```

---

### 2. 数据获取

#### `CSQAQClient` — API 客户端

```python
from src.acquisition.csqaq_client import CSQAQClient

client = CSQAQClient(
    api_token="YOUR_API_TOKEN",
    vip_token="YOUR_VIP_TOKEN",
)

# 批量获取订单簿快照（≤ 50 个/次，自动分块）
snapshots = client.fetch_batch_prices(["AK-47 | Redline (Field-Tested)", ...])

# K线数据（用于量价确认）
klines = client.fetch_kline_data(csqaq_id=176923345, periods="1hour")

# 大户排行（用于巨鲸追踪）
whales = client.fetch_whale_ranking(csqaq_id=176923345, limit=10)
```

| 方法 | 接口 | 说明 |
|------|------|------|
| `resolve_item_nameid(name)` | `GET /search/suggest` | 解析饰品 CSQAQ ID（有本地缓存） |
| `fetch_batch_prices(names)` | `POST /goods/getPriceByMarketHashName` | 批量获取 BUFF/YYYP 价格快照 |
| `fetch_kline_data(id)` | `POST /info/simple/chartAll` | K线数据（VIP 接口） |
| `fetch_whale_ranking(id)` | `POST /monitor/rank` | 大户持仓排行 |
| `fetch_user_inventory_dynamics(task_id, good_id)` | `POST /task/get_task_business` | 大户库存动态 |

#### `NameIdInitializer` — 三级 ID 解析

```python
from pathlib import Path
from src.acquisition.initializer import NameIdInitializer

initializer = NameIdInitializer(client)
result = initializer.run(
    item_names=["M4A4 | 地狱烈焰 (久经沙场)", ...],
    txt_path=Path("data/饰品id（更新时间2026-01-23）.txt"),  # 可选本地离线文件
)

print(result.from_cache)   # 缓存命中的品种
print(result.resolved)     # 本次新解析的品种
print(result.failed)       # 解析失败 {name: exception}
print(result.all_succeeded)  # bool
```

解析优先级：**本地缓存** → **本地 TXT 文件** → **HTTP API**

#### `_NameIdCache.load_from_dict()` — 批量预注入

已知 ID 时可跳过所有网络请求：

```python
from src.acquisition.cache import _NameIdCache
from pathlib import Path

cache = _NameIdCache(Path("data/nameid_cache.json"))
written = cache.load_from_dict({
    "AK-47 | Redline (Field-Tested)": 176923345,
    "AWP | Asiimov (Field-Tested)":   696692904,
})
# overwrite=True 可强制覆盖已有条目
print(f"写入 {written} 条")
```

---

### 3. 技术指标

`src/analysis/indicators.py` 所有函数接受 `list[float]`，返回等长列表，热身期填 `None`。

```python
from src.analysis.indicators import sma, ema, rsi, macd, bollinger_bands, volume_ratio

prices = [60.0, 61.5, 63.0, 62.5, 64.0, 65.5, 63.5, 66.0, 67.0, 65.5]

sma_5  = sma(prices, period=5)
ema_5  = ema(prices, period=5)
rsi_14 = rsi(prices, period=14)
m      = macd(prices)           # {"macd_line": [...], "signal_line": [...], "histogram": [...]}
bb     = bollinger_bands(prices, period=20, num_std=2.0)  # {"middle", "upper", "lower"}
vr     = volume_ratio([10, 12, 30, ...], period=10)       # > 2.0 = 放量
```

| 函数 | 热身行数 |
|------|---------|
| `sma(prices, period)` | `period - 1` |
| `ema(prices, period)` | `period - 1` |
| `rsi(prices, period=14)` | `period` |
| `macd(prices, fast=12, slow=26, signal_period=9)` | `slow + signal_period - 2` |
| `bollinger_bands(prices, period=20, num_std=2.0)` | `period - 1` |
| `volume_ratio(volumes, period=10)` | `period - 1` |

---

### 4. 盘主评分

`market_maker_score()` 将四个信号合并为每个数据点的 **(score, direction)** 对，score ≥ 0.4 为显著。

```python
from src.analysis.market_maker import market_maker_score

scores = market_maker_score(history)
for score, direction in scores[-5:]:
    flag = " ◄ 预警" if score >= 0.4 else ""
    print(f"Score={score:.2f}  Dir={direction}{flag}")
```

| 信号 | 权重 | 触发条件 |
|------|------|---------|
| 量能突破 | **35%** | 当期成交量 ≥ 10日均量 × 2 |
| 价格动量 | **35%** | 3日内涨跌幅 ≥ 5% |
| 布林突破 | **20%** | 价格突破 2σ 带 |
| RSI 极值 | **10%** | RSI ≥ 70（LONG）或 ≤ 30（SHORT） |

---

### 5. 交易信号

```python
from src.strategy.signal import generate_signals, latest_signal

signals = generate_signals(history, mm_threshold=0.4)
for sig in signals[-5:]:
    print(f"{sig.action:4s} | Price={sig.price:.2f} | Conf={sig.confidence:.2f} | {sig.reason}")

sig = latest_signal(history)
print(sig.action, sig.price, sig.reason)
```

**BUY 条件**（全部满足）：盘主评分 ≥ 阈值 且方向 LONG / RSI < 70 / MACD 柱正 / 价格在布林中轨上方

**SELL 条件**（全部满足）：盘主评分 ≥ 阈值 且方向 SHORT / RSI > 30 / MACD 柱负 / 价格在布林中轨下方

---

### 6. 回测引擎

```python
from src.backtest.engine import run_backtest

result = run_backtest(history, initial_capital=1000.0, transaction_cost=0.15)

print(f"收益率    : {result.total_return:.1%}")
print(f"交易次数  : {result.num_trades}")
print(f"胜率      : {result.win_rate:.1%}")
print(f"最大回撤  : {result.max_drawdown:.1%}")
```

> ⚠️ Steam 卖家手续费约 13–15%，一件饰品至少需涨 **~18%** 才能保本。这是为什么低波动品种回测无交易的原因。

---

### 7. 异常检测

`MarketAnomalyDetector` 对订单簿微结构特征运行孤立森林，结合 K线量价验证和巨鲸筹码追踪，输出三级验证后的信号。

#### 特征工程（`engineer_features(df)`）

共 **7 个基础特征 + 3 个 Z-Score**，最小行数要求 `_MIN_ROWS = 18`：

| 特征 | 公式 | 热身行 | 含义 |
|------|------|--------|------|
| `obi` | `(prev_sell - cur_sell) / prev_sell` | 1 | 短周期供应变化率 |
| `spread_ratio` | `(ask - prev_ask) / prev_ask` | 1 | 价格变动率 |
| `sdr` | `(MA6_sell - cur_sell) / MA6_sell` | 5 | 供应萎缩偏差 |
| `price_momentum_dev` | `(ask - MA12_ask) / MA12_ask` | 11 | 价格偏离均线 |
| `platform_spread` | `(buff_ask - yyyp_ask) / yyyp_ask` | 0 | 跨平台套利空间 |
| `lease_roi` | `yyyp_lease / buff_ask` | 0 | 日租金/价格比 |
| `price_volatility` | `std12(ask) / mean12(ask)` | 11 | 价格波动率 |
| `obi_z` | Z-Score of `obi`（窗口 12） | 12 | 动态 OBI 偏差 |
| `sdr_z` | Z-Score of `sdr`（窗口 12） | 12 | 动态 SDR 偏差 |
| `spread_z` | Z-Score of `spread_ratio`（窗口 12） | 12 | 动态价差偏差 |

#### 六种信号类型

| 信号 | 触发条件 | 含义 |
|------|---------|------|
| `NORMAL` | 孤立森林 label == 1 | 无异常 |
| `ACCUMULATION` | label==-1, `sdr_z > 2.0` AND `obi_z > 2.5` AND `obi > 0`，spread 稳定，低波动 | 建仓扫货 |
| `DUMP_RISK` | label==-1, `obi_z < -2.5` AND `obi < 0` AND `spread_ratio < -0.01` | 撤单/砸盘 |
| `ARBITRAGE_OPPORTUNITY` | `platform_spread > 0.05`（优先级最高） | 跨平台搬砖 |
| `WHALE_CONFIRMED_BUY` | `ACCUMULATION` + K线放量确认 + 巨鲸净流入 ≥ +20 | 巨鲸绝杀建仓 |
| `IRREGULAR` | label==-1，不满足上述任何条件；或 K线/巨鲸验证拦截的假信号 | 疑似洗盘 |

#### 三级验证流程

```
孤立森林检测 → ACCUMULATION/DUMP_RISK?
    │
    ├─ 否 → NORMAL / IRREGULAR / ARBITRAGE_OPPORTUNITY
    │
    └─ 是 → K线放量验证（_verify_volume_breakout）
                │
                ├─ 无量 → 降级为 IRREGULAR（无量空涨/假信号）
                │
                └─ 放量 → 巨鲸筹码追踪（WhaleTracker）
                              │
                              ├─ STRONG_PREDICTIVE_BUY → 升级为 WHALE_CONFIRMED_BUY
                              ├─ PREDICTIVE_DUMP       → 降级为 IRREGULAR（借势出货）
                              └─ NEUTRAL               → 保持 ACCUMULATION/DUMP_RISK
```

#### 用法

```python
from src.analysis.anomaly.detector import MarketAnomalyDetector
from src.acquisition.csqaq_client import CSQAQClient

client = CSQAQClient(api_token="...", vip_token="...")
db_config = {"host": "localhost", "dbname": "cs2market", "user": "postgres", "password": "..."}

detector = MarketAnomalyDetector(db_config, client)
result = detector.detect_anomalies(item_nameid=176923345)

if result is None:
    print("数据不足（< 18 行干净数据）")
else:
    print(result["signal_type"])    # "WHALE_CONFIRMED_BUY", "ACCUMULATION" 等
    print(result["anomaly_score"])  # 孤立森林分数（越负越异常）
    print(result["obi"])            # 订单簿供需变化率
    print(result["sdr"])            # 供应萎缩偏差
```

#### `result` 字典字段

| 键 | 类型 | 描述 |
|----|------|------|
| `timestamp` | `str` | ISO-8601 时间戳 |
| `anomaly_score` | `float` | 孤立森林分数（越低越异常） |
| `obi` | `float` | 订单簿供需变化率 |
| `spread_ratio` | `float` | 价格变动率 |
| `sdr` | `float` | 供应萎缩偏差 |
| `price_momentum_dev` | `float` | 价格偏离均线 |
| `platform_spread` | `float` | 跨平台价差 |
| `price_volatility` | `float` | 价格波动率 |
| `signal_type` | `str` | 信号类型 |
| `whale_msg` | `str` | 仅 `WHALE_CONFIRMED_BUY` 时出现，巨鲸情报摘要 |

---

### 8. 数据存储

PostgreSQL，schema 在首次 `connect()` 时自动建表。

#### 表结构

```sql
CREATE TABLE items (
    item_nameid       BIGINT       PRIMARY KEY,
    market_hash_name  VARCHAR(255) NOT NULL UNIQUE,
    added_at          TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE order_book_snapshots (
    time               TIMESTAMPTZ NOT NULL,
    item_nameid        BIGINT      NOT NULL REFERENCES items(item_nameid),
    lowest_ask_price   NUMERIC(10, 2),   -- NULL 表示无挂单
    highest_bid_price  NUMERIC(10, 2),   -- NULL 表示无求购
    total_sell_orders  INT,
    total_buy_orders   INT,
    yyyp_sell_price    NUMERIC(10, 2),
    yyyp_lease_price   NUMERIC(10, 4)
);

CREATE INDEX idx_item_time ON order_book_snapshots (item_nameid, time DESC);
```

#### 用法

```python
from src.storage.database import DatabaseConnection
from src.storage.repository import OrderBookRepository

db_config = {"host": "localhost", "dbname": "cs2market", "user": "postgres", "password": "..."}

with DatabaseConnection(db_config) as db:
    repo = OrderBookRepository(db.connection)
    repo.init_item_metadata(item_nameid=176923345, market_hash_name="AK-47 | Redline (Field-Tested)")
    repo.insert_snapshot(snapshot, item_nameid=176923345)
    latest = repo.get_latest_snapshot(item_nameid=176923345)
```

---

### 9. 告警推送

#### 信号元数据与触发规则

| 信号 | 图标 | 是否触发告警 | 备注 |
|------|------|------------|------|
| `WHALE_CONFIRMED_BUY` | 🐳🚀 | ✅ | 附巨鲸情报 |
| `ACCUMULATION` | 🚀 | ✅ | 大盘崩盘时熔断拦截 |
| `DUMP_RISK` | 🚨 | ✅ | |
| `ARBITRAGE_OPPORTUNITY` | 💎 | ✅ | |
| `IRREGULAR` | ⚠️ | ❌ 静默 | 疑似假信号，不打扰 |
| `NORMAL` | 🟢 | ❌ 静默 | |

#### 熔断机制

当监控池**平均跨平台价差 < -1.5%**（大盘整体下跌）时，自动屏蔽 `ACCUMULATION` 和 `WHALE_CONFIRMED_BUY` 信号，防止逆势接飞刀。

```python
from src.alerting.bot import DingTalkAlerter
from src.alerting.dispatcher import AlertDispatcher

alerter = DingTalkAlerter(
    webhook_url="https://oapi.dingtalk.com/robot/send?access_token=TOKEN",
    secret="SECxxxxx",  # 可选 HMAC 签名
)
dispatcher = AlertDispatcher(alerter)

# 更新大盘风险状态（由 main.py 的 QuantOrchestrator 自动驱动）
dispatcher.update_market_status(is_crashing=False)

# 分发检测结果
sent = dispatcher.dispatch("AK-47 | Redline (Field-Tested)", result)
```

---

## 运行测试

```bash
# 全部测试
python -m pytest tests/ -v

# Windows 虚拟环境
.venv\Scripts\python -m pytest tests/ -v

# 单个文件
.venv\Scripts\python -m pytest tests/test_anomaly.py -v

# 单个用例
.venv\Scripts\python -m pytest tests/test_anomaly.py::TestEvaluateSignal::test_accumulation_via_sdr_z_and_obi_z -v
```

所有测试均使用 `MagicMock` 模拟 DB 和网络，无需真实连接。**129 个测试，全部通过。**

| 文件 | 覆盖内容 |
|------|---------|
| `test_acquisition.py` | `_NameIdCache`（含 `load_from_dict`）、`CSQAQClient` |
| `test_indicators.py` | SMA / EMA / RSI / MACD / 布林带 / 量比 |
| `test_market_maker.py` | 量能突破、价格动量、布林突破、综合评分 |
| `test_backtest.py` | `run_backtest()`、`BacktestResult` |
| `test_anomaly.py` | 特征工程、`MarketAnomalyDetector`、`_evaluate_signal` |
| `test_alerting.py` | `DingTalkAlerter`、`format_anomaly_alert()`、`AlertDispatcher` |
| `test_storage.py` | `DatabaseConnection`、`OrderBookRepository` |

---

## 注意事项

- **Steam 手续费 13–15%**：饰品名义涨幅需超过 **~18%** 才能保本。回测结果偏保守正是这个原因。
- **日频数据局限**：Steam 历史价格为日线。更高频策略需接入第三方数据源。
- **不支持做空**：CS2 市场无法做空，回测只模拟单向多头。
- **孤立森林为无监督学习**：无标注样本，`contamination=0.05` 默认假设 5% 的数据点为异常，实际使用需根据品种调整。
- **K线二次确认为关键风控**：单纯 IsolationForest 误报率较高；量价确认 + 巨鲸追踪两道过滤大幅提升信号精度。
- **仅用于学习与研究**：本工具不构成任何投资建议，过往表现不代表未来收益，请自行管理风险。
