"""
Trading signal generator for the CS2 market.

Combines technical indicators (RSI, MACD, Bollinger Bands) with the
market-maker detection module to produce :class:`~src.acquisition.models.TradeSignal`
objects that recommend BUY, SELL, or HOLD for a given item.

Strategy overview
-----------------
A BUY signal is generated when ALL of the following are true:

1. The composite market-maker score exceeds *mm_threshold* and the inferred
   direction is ``"LONG"`` (i.e. a large buyer is accumulating).
2. RSI is not yet overbought (< *rsi_overbought*).
3. The MACD histogram is positive (momentum is upward).
4. The price is above the Bollinger middle band (momentum confirmed).

A SELL signal is generated when ALL of the following are true:

1. The composite market-maker score exceeds *mm_threshold* and direction is
   ``"SHORT"`` (a large seller is distributing).
2. RSI is not yet oversold (> *rsi_oversold*).
3. The MACD histogram is negative.
4. The price is below the Bollinger middle band (downtrend confirmed).

Otherwise a HOLD signal is returned.
"""

import time
from typing import List, Optional

from src.acquisition.models import ItemHistory, TradeSignal
from src.analysis.indicators import rsi, macd, bollinger_bands
from src.analysis.market_maker import market_maker_score


def generate_signals(
    history: ItemHistory,
    mm_threshold: float = 0.4,
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_period: int = 20,
) -> List[TradeSignal]:
    """Generate a trading signal for every data point in *history*.

    Args:
        history: Price/volume history for the item.
        mm_threshold: Minimum market-maker score to act on.
        rsi_overbought: RSI level above which we consider the item overbought.
        rsi_oversold: RSI level below which we consider the item oversold.
        rsi_period: Period for RSI calculation.
        macd_fast: Fast EMA period for MACD.
        macd_slow: Slow EMA period for MACD.
        macd_signal: Signal EMA period for MACD.
        bb_period: Period for Bollinger Bands.

    Returns:
        List of :class:`~src.acquisition.models.TradeSignal`, one per record.
    """
    prices = history.prices
    n = len(prices)
    if n == 0:
        return []

    rsi_values = rsi(prices, rsi_period)
    macd_result = macd(prices, macd_fast, macd_slow, macd_signal)
    bb = bollinger_bands(prices, bb_period)
    mm_scores = market_maker_score(history)

    signals: List[TradeSignal] = []
    for i in range(n):
        record = history.records[i]
        price = record.price
        ts = record.timestamp

        mm_score_val, mm_dir = mm_scores[i]
        rsi_val: Optional[float] = rsi_values[i]
        macd_hist: Optional[float] = macd_result["histogram"][i]
        bb_middle: Optional[float] = bb["middle"][i]

        action = "HOLD"
        reasons: List[str] = []

        sufficient_data = (
            rsi_val is not None
            and macd_hist is not None
            and bb_middle is not None
        )

        if sufficient_data and mm_score_val >= mm_threshold:
            if mm_dir == "LONG":
                buy_conditions = [
                    rsi_val < rsi_overbought,
                    macd_hist > 0,
                    bb_middle is not None and price > bb_middle,
                ]
                if all(buy_conditions):
                    action = "BUY"
                    reasons.append(f"Market maker LONG signal (score={mm_score_val:.2f})")
                    reasons.append(f"RSI={rsi_val:.1f} (not overbought)")
                    reasons.append("MACD histogram positive")
                    reasons.append("Price above BB middle (momentum confirmed)")

            elif mm_dir == "SHORT":
                sell_conditions = [
                    rsi_val > rsi_oversold,
                    macd_hist < 0,
                    bb_middle is not None and price < bb_middle,
                ]
                if all(sell_conditions):
                    action = "SELL"
                    reasons.append(f"Market maker SHORT signal (score={mm_score_val:.2f})")
                    reasons.append(f"RSI={rsi_val:.1f} (not oversold)")
                    reasons.append("MACD histogram negative")
                    reasons.append("Price below BB middle (downtrend confirmed)")

        if not reasons:
            reasons.append("No actionable market maker signal detected")

        signals.append(
            TradeSignal(
                item_name=history.item_name,
                timestamp=ts,
                action=action,
                confidence=mm_score_val,
                reason="; ".join(reasons),
                price=price,
            )
        )

    return signals


def latest_signal(history: ItemHistory, **kwargs) -> Optional[TradeSignal]:
    """Return the most recent trading signal for *history*.

    This is a convenience wrapper around :func:`generate_signals` that returns
    only the last element (i.e. the signal for today / the most recent record).
    Returns ``None`` if *history* contains no records.
    """
    signals = generate_signals(history, **kwargs)
    return signals[-1] if signals else None
