# cs2-market

A quantitative trading toolkit for the **CS2 (CS:GO) cosmetics market**.

The library helps you detect **market maker (盘主) activity** — large traders
who drive short-term price swings by going long or short on specific items —
and generates **trading signals** you can act on or back-test.

---

## Features

| Module | Description |
|---|---|
| `src/data/models.py` | Data models: `PriceRecord`, `ItemHistory`, `TradeSignal` |
| `src/data/fetcher.py` | Fetch historical price/volume data from the Steam Community Market API |
| `src/analysis/indicators.py` | Technical indicators: SMA, EMA, RSI, MACD, Bollinger Bands, volume ratio |
| `src/analysis/market_maker.py` | Market maker detection: volume spikes, price momentum, BB breakouts, composite score |
| `src/strategy/signal.py` | Trading signal generator (BUY / SELL / HOLD) |
| `src/backtest/engine.py` | Backtesting engine with P&L, win rate, and max-drawdown metrics |

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

### 1. Data Collection

`fetch_item_history()` calls the publicly accessible Steam Market price-history
endpoint and returns an `ItemHistory` object containing daily price and volume
records.

```python
from src.data.fetcher import fetch_item_history

history = fetch_item_history("AK-47 | Redline (Field-Tested)")
print(history.prices[-5:])   # Last 5 daily closing prices
```

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
from src.strategy.signal import latest_signal

signal = latest_signal(history)
print(signal.action, signal.confidence, signal.reason)
```

### 4. Backtesting

`run_backtest()` simulates executing signals on historical data and reports
performance metrics. The default transaction cost is **15 %** (Steam's cut),
so significant price moves are needed to profit.

```python
from src.backtest.engine import run_backtest

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
