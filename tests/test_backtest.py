"""Tests for the backtesting engine."""

import pytest
from src.data.models import ItemHistory, PriceRecord
from src.backtest.engine import run_backtest, BacktestResult


def _make_history(prices, volumes=None, name="Test Item"):
    if volumes is None:
        volumes = [10] * len(prices)
    records = [
        PriceRecord(timestamp=float(i), price=p, volume=v)
        for i, (p, v) in enumerate(zip(prices, volumes))
    ]
    return ItemHistory(item_name=name, records=records)


def test_backtest_returns_result():
    prices = [float(i) for i in range(1, 60)]
    history = _make_history(prices)
    result = run_backtest(history)
    assert isinstance(result, BacktestResult)


def test_backtest_fields_present():
    prices = [float(i) for i in range(1, 60)]
    history = _make_history(prices)
    result = run_backtest(history, initial_capital=500.0)
    assert result.initial_capital == 500.0
    assert isinstance(result.final_capital, float)
    assert isinstance(result.total_return, float)
    assert isinstance(result.num_trades, int)
    assert 0.0 <= result.win_rate <= 1.0
    assert result.max_drawdown >= 0.0


def test_backtest_no_trades_on_flat_price():
    # Completely flat price with uniform volume → no momentum → no signals
    prices = [10.0] * 60
    history = _make_history(prices)
    result = run_backtest(history)
    # With no price movement there should be zero completed trades
    assert result.num_trades == 0


def test_backtest_win_rate_zero_trades():
    prices = [10.0] * 60
    history = _make_history(prices)
    result = run_backtest(history)
    assert result.win_rate == 0.0


def test_backtest_max_drawdown_non_negative():
    import random
    random.seed(1)
    prices = [50.0 + random.uniform(-5, 5) for _ in range(80)]
    history = _make_history(prices)
    result = run_backtest(history)
    assert result.max_drawdown >= 0.0


def test_backtest_empty_history():
    history = ItemHistory(item_name="Empty")
    result = run_backtest(history)
    assert result.num_trades == 0
    assert result.final_capital == result.initial_capital
