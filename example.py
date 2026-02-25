"""
Example: CS2 market quant-trading strategy demo.

This script generates synthetic price/volume data for a hypothetical CS2 item,
runs the market-maker detection and signal generation pipeline, and then
back-tests the strategy.  No real Steam API calls are made.

Run with:
    python example.py
"""

import random
import math
from src.data.models import ItemHistory, PriceRecord
from src.analysis.market_maker import market_maker_score
from src.strategy.signal import generate_signals
from src.backtest.engine import run_backtest


# ---------------------------------------------------------------------------
# Generate synthetic price history that mimics a market maker going "LONG"
# ---------------------------------------------------------------------------

def generate_synthetic_history(
    item_name: str = "AK-47 | Redline (Field-Tested)",
    days: int = 120,
    base_price: float = 50.0,
    seed: int = 42,
) -> ItemHistory:
    """Create a fake but realistic-looking price history with a MM pump event."""
    random.seed(seed)
    records = []
    price = base_price

    for i in range(days):
        # Normal random walk
        drift = random.uniform(-0.5, 0.5)
        noise = random.gauss(0, 0.3)
        volume = random.randint(5, 30)

        # Simulate a market maker LONG push between day 80 and 90
        if 80 <= i <= 90:
            drift += 0.8              # Extra upward drift
            volume = random.randint(60, 120)  # Volume spike

        # Simulate a market maker SHORT dump between day 100 and 108
        if 100 <= i <= 108:
            drift -= 0.9              # Downward drift (dump)
            volume = random.randint(50, 100)  # Volume spike on the way down

        price = max(1.0, price + drift + noise)
        records.append(
            PriceRecord(timestamp=float(i * 86400), price=round(price, 2), volume=volume)
        )

    return ItemHistory(item_name=item_name, records=records)


def main():
    item_name = "AK-47 | Redline (Field-Tested)"
    print(f"\n{'='*60}")
    print(f"  CS2 Quantitative Market Analyser")
    print(f"  Item: {item_name}")
    print(f"{'='*60}\n")

    history = generate_synthetic_history(item_name)

    # --- Market-maker activity scores ---
    print("[ Market Maker Detection (last 10 days) ]")
    scores = market_maker_score(history)
    for i, (score, direction) in enumerate(scores[-10:], start=len(scores) - 10):
        price = history.records[i].price
        vol = history.records[i].volume
        marker = " ◄ ALERT" if score >= 0.4 else ""
        print(
            f"  Day {i:3d} | Price={price:6.2f} | Vol={vol:4d} | "
            f"MM Score={score:.2f} | Dir={direction}{marker}"
        )

    # --- Trading signals ---
    print("\n[ Trading Signals (last 10 days) ]")
    signals = generate_signals(history)
    buy_count = sell_count = hold_count = 0
    for sig in signals:
        if sig.action == "BUY":
            buy_count += 1
        elif sig.action == "SELL":
            sell_count += 1
        else:
            hold_count += 1

    for sig in signals[-10:]:
        marker = " ◄" if sig.action != "HOLD" else ""
        print(
            f"  {sig.action:4s} | Price={sig.price:6.2f} | "
            f"Confidence={sig.confidence:.2f} | {sig.reason}{marker}"
        )
    print(
        f"\n  Total signals → BUY: {buy_count}, SELL: {sell_count}, HOLD: {hold_count}"
    )

    # --- Backtest ---
    print("\n[ Backtest Results ]")
    result = run_backtest(history, initial_capital=500.0)
    print(f"  Initial capital : {result.initial_capital:.2f}")
    print(f"  Final capital   : {result.final_capital:.2f}")
    print(f"  Total return    : {result.total_return * 100:.2f}%")
    print(f"  Trades executed : {result.num_trades}")
    print(f"  Win rate        : {result.win_rate * 100:.1f}%")
    print(f"  Max drawdown    : {result.max_drawdown * 100:.2f}%")

    if result.trades:
        print("\n  Individual trades:")
        for t in result.trades:
            pnl_str = f"+{t.profit_pct*100:.1f}%" if t.profit_pct >= 0 else f"{t.profit_pct*100:.1f}%"
            print(
                f"    Buy Day {int(t.buy_timestamp//86400):3d} @ {t.buy_price:.2f}"
                f" → Sell Day {int(t.sell_timestamp//86400):3d} @ {t.sell_price:.2f}"
                f"  P&L: {pnl_str}"
            )

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
