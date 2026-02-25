"""
Technical indicators used for CS2 market price analysis.

All functions accept plain Python lists and return lists of the same length
(or shorter when a warm-up period is required).  ``None`` is used as the
placeholder for positions that cannot yet be computed.
"""

from typing import List, Optional


# ---------------------------------------------------------------------------
# Moving Averages
# ---------------------------------------------------------------------------

def sma(prices: List[float], period: int) -> List[Optional[float]]:
    """Simple Moving Average.

    Returns a list of the same length as *prices*.  The first
    ``period - 1`` values are ``None``.
    """
    result: List[Optional[float]] = [None] * (period - 1)
    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1 : i + 1]
        result.append(sum(window) / period)
    return result


def ema(prices: List[float], period: int) -> List[Optional[float]]:
    """Exponential Moving Average.

    Uses the standard smoothing factor ``k = 2 / (period + 1)``.
    The first ``period - 1`` values are ``None``, and position ``period - 1``
    is seeded with the simple average of the first *period* prices.
    """
    if len(prices) < period:
        return [None] * len(prices)

    k = 2.0 / (period + 1)
    result: List[Optional[float]] = [None] * (period - 1)
    seed = sum(prices[:period]) / period
    result.append(seed)
    for price in prices[period:]:
        result.append(price * k + result[-1] * (1 - k))
    return result


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

def rsi(prices: List[float], period: int = 14) -> List[Optional[float]]:
    """Relative Strength Index.

    Returns values in the range ``[0, 100]``.  The first *period* values are
    ``None``.
    """
    if len(prices) <= period:
        return [None] * len(prices)

    result: List[Optional[float]] = [None] * period

    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, period + 1):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def _rsi_value(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)

    result.append(_rsi_value(avg_gain, avg_loss))

    for i in range(period + 1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result.append(_rsi_value(avg_gain, avg_loss))

    return result


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

def macd(
    prices: List[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> dict:
    """MACD indicator.

    Returns a dict with keys:

    * ``"macd_line"``   – MACD line (fast EMA − slow EMA)
    * ``"signal_line"`` – Signal line (EMA of MACD line)
    * ``"histogram"``   – MACD histogram (MACD line − signal line)

    All lists are the same length as *prices*, with ``None`` for warm-up
    positions.
    """
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)

    macd_line: List[Optional[float]] = []
    for f, s in zip(fast_ema, slow_ema):
        if f is None or s is None:
            macd_line.append(None)
        else:
            macd_line.append(f - s)

    # Compute EMA of the MACD line over valid values only
    valid_macd = [v for v in macd_line if v is not None]
    if len(valid_macd) < signal_period:
        signal_line: List[Optional[float]] = [None] * len(prices)
        histogram: List[Optional[float]] = [None] * len(prices)
        return {"macd_line": macd_line, "signal_line": signal_line, "histogram": histogram}

    signal_values = ema(valid_macd, signal_period)

    # Re-align signal_line with original length
    none_count = sum(1 for v in macd_line if v is None)
    signal_line = [None] * none_count + signal_values  # type: ignore[assignment]

    histogram = []
    for m, sig in zip(macd_line, signal_line):
        if m is None or sig is None:
            histogram.append(None)
        else:
            histogram.append(m - sig)

    return {"macd_line": macd_line, "signal_line": signal_line, "histogram": histogram}


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def bollinger_bands(
    prices: List[float], period: int = 20, num_std: float = 2.0
) -> dict:
    """Bollinger Bands.

    Returns a dict with keys:

    * ``"middle"`` – SMA of *prices*
    * ``"upper"``  – middle + num_std × rolling std-dev
    * ``"lower"``  – middle − num_std × rolling std-dev

    All lists are the same length as *prices*.
    """
    middle = sma(prices, period)
    upper: List[Optional[float]] = []
    lower: List[Optional[float]] = []

    for i in range(len(prices)):
        if middle[i] is None:
            upper.append(None)
            lower.append(None)
        else:
            window = prices[i - period + 1 : i + 1]
            mean = middle[i]
            variance = sum((p - mean) ** 2 for p in window) / period
            std = variance ** 0.5
            upper.append(mean + num_std * std)
            lower.append(mean - num_std * std)

    return {"middle": middle, "upper": upper, "lower": lower}


# ---------------------------------------------------------------------------
# Volume indicators
# ---------------------------------------------------------------------------

def volume_sma(volumes: List[int], period: int = 10) -> List[Optional[float]]:
    """Simple moving average of volume."""
    return sma([float(v) for v in volumes], period)


def volume_ratio(
    volumes: List[int], period: int = 10
) -> List[Optional[float]]:
    """Ratio of current volume to the rolling average volume.

    A value significantly above 1.0 (e.g. > 2.0) indicates a volume spike,
    which is often associated with market maker activity.
    """
    vol_avg = volume_sma(volumes, period)
    result: List[Optional[float]] = []
    for v, avg in zip(volumes, vol_avg):
        if avg is None or avg == 0:
            result.append(None)
        else:
            result.append(v / avg)
    return result
