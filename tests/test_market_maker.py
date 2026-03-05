"""Tests for market maker detection."""

import pytest
from src.acquisition.models import ItemHistory, PriceRecord
from src.analysis.market_maker import (
    detect_volume_spikes,
    detect_price_momentum,
    detect_bollinger_breakout,
    market_maker_score,
)


def _make_history(prices, volumes=None):
    if volumes is None:
        volumes = [10] * len(prices)
    records = [
        PriceRecord(timestamp=float(i), price=p, volume=v)
        for i, (p, v) in enumerate(zip(prices, volumes))
    ]
    return ItemHistory(item_name="Test Item", records=records)


# ---------------------------------------------------------------------------
# Volume spikes
# ---------------------------------------------------------------------------

def test_volume_spike_detected():
    # 10 normal days, then a spike
    volumes = [10] * 10 + [100]
    history = _make_history([1.0] * 11, volumes)
    spikes = detect_volume_spikes(history, vol_period=10, spike_threshold=2.0)
    assert spikes[-1] is True


def test_no_spike_when_normal():
    volumes = [10] * 15
    history = _make_history([1.0] * 15, volumes)
    spikes = detect_volume_spikes(history, vol_period=10, spike_threshold=2.0)
    # All ratios = 1.0, well below threshold
    assert not any(spikes[10:])


def test_volume_spike_length():
    history = _make_history([1.0] * 20)
    spikes = detect_volume_spikes(history)
    assert len(spikes) == 20


# ---------------------------------------------------------------------------
# Price momentum
# ---------------------------------------------------------------------------

def test_momentum_up():
    # Price jumps 20 % in 3 days
    prices = [100.0, 100.0, 100.0, 120.0]
    history = _make_history(prices)
    mom = detect_price_momentum(history, lookback=3, momentum_pct=0.05)
    assert mom[-1] == "UP"


def test_momentum_down():
    prices = [100.0, 100.0, 100.0, 80.0]
    history = _make_history(prices)
    mom = detect_price_momentum(history, lookback=3, momentum_pct=0.05)
    assert mom[-1] == "DOWN"


def test_momentum_neutral():
    prices = [100.0, 100.0, 100.0, 101.0]
    history = _make_history(prices)
    mom = detect_price_momentum(history, lookback=3, momentum_pct=0.05)
    assert mom[-1] is None


def test_momentum_warmup_is_none():
    prices = [100.0, 101.0, 102.0, 110.0]
    history = _make_history(prices)
    mom = detect_price_momentum(history, lookback=3, momentum_pct=0.05)
    assert all(v is None for v in mom[:3])


# ---------------------------------------------------------------------------
# Bollinger breakout
# ---------------------------------------------------------------------------

def test_bollinger_breakout_up():
    # A price that shoots well above the Bollinger band
    base = [10.0] * 25
    base[-1] = 1000.0  # extreme outlier breaks upper band
    history = _make_history(base)
    breaks = detect_bollinger_breakout(history, bb_period=20)
    assert breaks[-1] == "UP"


def test_bollinger_breakout_down():
    base = [10.0] * 25
    base[-1] = 0.001  # extreme drop breaks lower band
    history = _make_history(base)
    breaks = detect_bollinger_breakout(history, bb_period=20)
    assert breaks[-1] == "DOWN"


def test_bollinger_breakout_length():
    history = _make_history([10.0] * 30)
    breaks = detect_bollinger_breakout(history, bb_period=20)
    assert len(breaks) == 30


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

def test_market_maker_score_length():
    history = _make_history([float(i) for i in range(1, 51)])
    scores = market_maker_score(history)
    assert len(scores) == 50


def test_market_maker_score_range():
    import random
    random.seed(0)
    prices = [100 + random.uniform(-10, 10) for _ in range(60)]
    volumes = [random.randint(5, 50) for _ in range(60)]
    history = _make_history(prices, volumes)
    scores = market_maker_score(history)
    for score, direction in scores:
        assert 0.0 <= score <= 1.0
        assert direction in ("LONG", "SHORT", "NEUTRAL")


def test_market_maker_score_empty():
    history = ItemHistory(item_name="Empty")
    assert market_maker_score(history) == []
