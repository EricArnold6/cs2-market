"""
Simple backtesting engine for the CS2 market trading strategy.

The engine simulates executing trades according to the signals produced by
:func:`~src.strategy.signal.generate_signals` and tracks portfolio performance.

Assumptions
-----------
* We can only hold one unit of a single item at a time.
* There is a configurable transaction cost (Steam charges 15 % on sales;
  buyers also typically pay a small premium on third-party sites).
* Positions are opened at the *next day's open price* after a signal
  (to avoid look-ahead bias) – here approximated as the same-day close price
  because daily data has no intraday granularity.
* Short-selling is not simulated (the CS2 market does not support shorting).

Output metrics
--------------
* ``total_return`` – overall percentage gain/loss on initial capital
* ``num_trades``   – number of completed buy+sell round trips
* ``win_rate``     – fraction of round trips that were profitable
* ``max_drawdown`` – maximum peak-to-trough decline in portfolio value
"""

from dataclasses import dataclass, field
from typing import List, Optional

from src.data.models import ItemHistory, TradeSignal
from src.strategy.signal import generate_signals


@dataclass
class Trade:
    """A single completed round-trip (buy then sell)."""

    item_name: str
    buy_timestamp: float
    sell_timestamp: float
    buy_price: float
    sell_price: float
    profit_pct: float  # After transaction costs


@dataclass
class BacktestResult:
    """Summary of a backtest run."""

    item_name: str
    initial_capital: float
    final_capital: float
    total_return: float  # (final - initial) / initial
    num_trades: int
    win_rate: float
    max_drawdown: float
    trades: List[Trade] = field(default_factory=list)


def run_backtest(
    history: ItemHistory,
    initial_capital: float = 1000.0,
    transaction_cost: float = 0.15,
    **signal_kwargs,
) -> BacktestResult:
    """Simulate trading *history* using the built-in signal strategy.

    Args:
        history: Full price/volume history for the item.
        initial_capital: Starting cash balance in the same currency as prices.
        transaction_cost: Fraction of the sale price charged as fees.
                          Default is 0.15 (15 %, matching Steam's cut).
        **signal_kwargs: Extra keyword arguments forwarded to
                         :func:`~src.strategy.signal.generate_signals`.

    Returns:
        A :class:`BacktestResult` describing portfolio performance.
    """
    signals: List[TradeSignal] = generate_signals(history, **signal_kwargs)

    capital = initial_capital
    holding: Optional[float] = None  # Buy price when in a position
    buy_ts: Optional[float] = None
    buy_price: Optional[float] = None

    trades: List[Trade] = []
    equity_curve: List[float] = [capital]
    peak = capital

    for signal in signals:
        price = signal.price or 0.0

        if signal.action == "BUY" and holding is None and capital >= price:
            # Open a long position
            holding = price
            buy_ts = signal.timestamp
            buy_price = price
            capital -= price

        elif signal.action == "SELL" and holding is not None:
            # Close the position
            proceeds = price * (1.0 - transaction_cost)
            profit_pct = (proceeds - holding) / holding
            trades.append(
                Trade(
                    item_name=history.item_name,
                    buy_timestamp=buy_ts,
                    sell_timestamp=signal.timestamp,
                    buy_price=buy_price,
                    sell_price=price,
                    profit_pct=profit_pct,
                )
            )
            capital += proceeds
            holding = None
            buy_ts = None
            buy_price = None

        # Mark-to-market equity
        current_equity = capital + (price if holding is not None else 0.0)
        equity_curve.append(current_equity)
        if current_equity > peak:
            peak = current_equity

    # Liquidate any open position at last available price
    if holding is not None and history.records:
        last_price = history.records[-1].price
        proceeds = last_price * (1.0 - transaction_cost)
        profit_pct = (proceeds - holding) / holding
        trades.append(
            Trade(
                item_name=history.item_name,
                buy_timestamp=buy_ts,
                sell_timestamp=history.records[-1].timestamp,
                buy_price=buy_price,
                sell_price=last_price,
                profit_pct=profit_pct,
            )
        )
        capital += proceeds
        holding = None

    final_capital = capital
    total_return = (final_capital - initial_capital) / initial_capital

    num_trades = len(trades)
    win_rate = (
        sum(1 for t in trades if t.profit_pct > 0) / num_trades
        if num_trades > 0
        else 0.0
    )

    # Max drawdown
    peak = initial_capital
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return BacktestResult(
        item_name=history.item_name,
        initial_capital=initial_capital,
        final_capital=final_capital,
        total_return=total_return,
        num_trades=num_trades,
        win_rate=win_rate,
        max_drawdown=max_dd,
        trades=trades,
    )
