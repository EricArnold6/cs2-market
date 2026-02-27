"""
Market maker (盘主) detection for the CS2 cosmetics market.

Market makers are large traders who can influence prices by placing large buy
or sell orders.  This module provides heuristics to detect signs of market
maker activity based on price action and volume patterns:

* **Volume spike** – a sudden increase in trading volume relative to the
  recent average often precedes or accompanies a directional move driven by a
  large participant.
* **Price momentum** – a rapid run-up or run-down in price (above/below a
  momentum threshold) suggests a coordinated push.
* **Bollinger Band breakout** – when the price closes outside the bands,
  momentum may be driven by a large participant.
* **Composite score** – a weighted combination of the above signals.
"""

from typing import List, Optional, Tuple

from src.acquisition.models import ItemHistory
from src.analysis.indicators import (
    bollinger_bands,
    rsi,
    volume_ratio,
)


# ---------------------------------------------------------------------------
# Individual signal detectors
# ---------------------------------------------------------------------------

def detect_volume_spikes(
    history: ItemHistory,
    vol_period: int = 10,
    spike_threshold: float = 2.0,
) -> List[bool]:
    """Return a boolean list where ``True`` marks a volume spike.

    A spike is defined as the current volume being at least *spike_threshold*
    times the rolling average volume over *vol_period* days.
    """
    ratios = volume_ratio(history.volumes, vol_period)
    return [
        (r is not None and r >= spike_threshold)
        for r in ratios
    ]


def detect_price_momentum(
    history: ItemHistory,
    lookback: int = 3,
    momentum_pct: float = 0.05,
) -> List[Optional[str]]:
    """Return a direction for each data point based on short-term momentum.

    * ``"UP"``   – price rose by more than *momentum_pct* over *lookback* days
    * ``"DOWN"`` – price fell by more than *momentum_pct* over *lookback* days
    * ``None``   – no significant momentum (or insufficient data)
    """
    prices = history.prices
    result: List[Optional[str]] = [None] * lookback
    for i in range(lookback, len(prices)):
        change = (prices[i] - prices[i - lookback]) / prices[i - lookback]
        if change >= momentum_pct:
            result.append("UP")
        elif change <= -momentum_pct:
            result.append("DOWN")
        else:
            result.append(None)
    return result


def detect_bollinger_breakout(
    history: ItemHistory,
    bb_period: int = 20,
    num_std: float = 2.0,
) -> List[Optional[str]]:
    """Return the direction of a Bollinger Band breakout for each data point.

    * ``"UP"``   – price is above the upper band
    * ``"DOWN"`` – price is below the lower band
    * ``None``   – price is within the bands or insufficient data
    """
    prices = history.prices
    bands = bollinger_bands(prices, bb_period, num_std)
    result: List[Optional[str]] = []
    for price, upper, lower in zip(prices, bands["upper"], bands["lower"]):
        if upper is None or lower is None:
            result.append(None)
        elif price > upper:
            result.append("UP")
        elif price < lower:
            result.append("DOWN")
        else:
            result.append(None)
    return result


# ---------------------------------------------------------------------------
# Composite market-maker signal
# ---------------------------------------------------------------------------

def market_maker_score(
    history: ItemHistory,
    vol_period: int = 10,
    spike_threshold: float = 2.0,
    momentum_lookback: int = 3,
    momentum_pct: float = 0.05,
    bb_period: int = 20,
    rsi_period: int = 14,
) -> List[Tuple[float, str]]:
    """Compute a composite market-maker activity score for each data point.

    Returns a list of ``(score, direction)`` tuples, one per record.

    * **score** – a value in ``[0.0, 1.0]`` representing the estimated
      probability of market-maker activity.  Higher values mean more evidence.
    * **direction** – ``"LONG"`` (buying pressure), ``"SHORT"`` (selling
      pressure), or ``"NEUTRAL"``.

    Scoring weights
    ---------------
    ============ =======
    Signal       Weight
    ============ =======
    Volume spike  0.35
    Momentum      0.35
    BB breakout   0.20
    RSI extreme   0.10
    ============ =======
    """
    n = len(history.records)
    if n == 0:
        return []

    spikes = detect_volume_spikes(history, vol_period, spike_threshold)
    momenta = detect_price_momentum(history, momentum_lookback, momentum_pct)
    bb_breaks = detect_bollinger_breakout(history, bb_period)
    rsi_values = rsi(history.prices, rsi_period)

    results: List[Tuple[float, str]] = []
    for i in range(n):
        score = 0.0
        long_votes = 0
        short_votes = 0

        # Volume spike contributes direction-neutral evidence
        if spikes[i]:
            score += 0.35

        # Momentum direction
        mom = momenta[i]
        if mom == "UP":
            score += 0.35
            long_votes += 1
        elif mom == "DOWN":
            score += 0.35
            short_votes += 1

        # Bollinger breakout
        bb = bb_breaks[i]
        if bb == "UP":
            score += 0.20
            long_votes += 1
        elif bb == "DOWN":
            score += 0.20
            short_votes += 1

        # RSI extreme
        r = rsi_values[i]
        if r is not None:
            if r >= 70:
                score += 0.10
                long_votes += 1
            elif r <= 30:
                score += 0.10
                short_votes += 1

        score = min(score, 1.0)

        if long_votes > short_votes:
            direction = "LONG"
        elif short_votes > long_votes:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"

        results.append((round(score, 4), direction))

    return results
