"""Tests for technical indicators."""

import pytest
from src.analysis.indicators import sma, ema, rsi, macd, bollinger_bands, volume_ratio


# ---------------------------------------------------------------------------
# SMA
# ---------------------------------------------------------------------------

def test_sma_basic():
    prices = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = sma(prices, 3)
    assert result[:2] == [None, None]
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)
    assert result[4] == pytest.approx(4.0)


def test_sma_period_1():
    prices = [10.0, 20.0, 30.0]
    result = sma(prices, 1)
    assert result == prices


def test_sma_length_preserved():
    prices = list(range(1, 11))
    result = sma(prices, 5)
    assert len(result) == len(prices)


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------

def test_ema_length_preserved():
    prices = [float(i) for i in range(1, 21)]
    result = ema(prices, 5)
    assert len(result) == len(prices)


def test_ema_insufficient_data():
    prices = [1.0, 2.0]
    result = ema(prices, 5)
    assert all(v is None for v in result)


def test_ema_seed_equals_sma():
    prices = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = ema(prices, 3)
    # Seed at index 2 should equal SMA(3) = 2.0
    assert result[2] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

def test_rsi_length_preserved():
    prices = [float(i) for i in range(1, 31)]
    result = rsi(prices, 14)
    assert len(result) == len(prices)


def test_rsi_none_for_warmup():
    prices = [float(i) for i in range(1, 31)]
    result = rsi(prices, 14)
    assert all(v is None for v in result[:14])


def test_rsi_range():
    import random
    random.seed(42)
    prices = [100.0 + random.uniform(-5, 5) for _ in range(50)]
    result = rsi(prices, 14)
    for v in result:
        if v is not None:
            assert 0.0 <= v <= 100.0


def test_rsi_all_up_is_100():
    # Strictly increasing prices → RSI should reach 100
    prices = [float(i) for i in range(1, 30)]
    result = rsi(prices, 14)
    assert result[-1] == pytest.approx(100.0)


def test_rsi_all_down_is_0():
    # Strictly decreasing prices → RSI should reach 0
    prices = [float(30 - i) for i in range(30)]
    result = rsi(prices, 14)
    assert result[-1] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

def test_macd_keys():
    prices = [float(i) for i in range(1, 50)]
    result = macd(prices)
    assert set(result.keys()) == {"macd_line", "signal_line", "histogram"}


def test_macd_length_preserved():
    prices = [float(i) for i in range(1, 50)]
    result = macd(prices)
    for key in result:
        assert len(result[key]) == len(prices)


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def test_bollinger_keys():
    prices = [float(i) for i in range(1, 30)]
    result = bollinger_bands(prices, 20)
    assert set(result.keys()) == {"middle", "upper", "lower"}


def test_bollinger_upper_above_lower():
    prices = [float(i) + (i % 3) for i in range(1, 50)]
    result = bollinger_bands(prices, 20)
    for u, l in zip(result["upper"], result["lower"]):
        if u is not None and l is not None:
            assert u >= l


def test_bollinger_length_preserved():
    prices = list(range(1, 40))
    result = bollinger_bands(prices, 20)
    for key in result:
        assert len(result[key]) == len(prices)


# ---------------------------------------------------------------------------
# Volume ratio
# ---------------------------------------------------------------------------

def test_volume_ratio_spike():
    # A spike of 100 when prior average is 10.
    # SMA window at the spike index: last 10 values = [10]*9 + [100], avg=19
    # ratio = 100/19 ≈ 5.26, which is well above a 2x threshold.
    volumes = [10] * 20 + [100]
    ratios = volume_ratio(volumes, period=10)
    assert ratios[-1] is not None
    assert ratios[-1] > 2.0  # Clearly a spike relative to the rolling average


def test_volume_ratio_length():
    volumes = list(range(1, 21))
    result = volume_ratio(volumes, 5)
    assert len(result) == len(volumes)
