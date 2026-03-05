"""Data models for the backtesting engine."""

from dataclasses import dataclass, field
from typing import List


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
