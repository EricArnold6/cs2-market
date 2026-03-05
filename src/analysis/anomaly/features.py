"""Feature engineering for CS2 order-book anomaly detection.

All logic is pure and stateless: given a DataFrame of raw snapshot columns,
return a new DataFrame with four derived features ready for the Isolation
Forest.  The caller is responsible for dropping NaN rows before model fitting.
"""

import pandas as pd


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute four market-microstructure features from raw snapshot data.

    Parameters
    ----------
    df : pd.DataFrame
        Columns expected (matching ``order_book_snapshots`` schema):
            * ``highest_bid_price``
            * ``lowest_ask_price``
            * ``bid_volume_top5``
            * ``ask_volume_top5``
            * ``total_sell_orders``

    Returns
    -------
    pd.DataFrame
        Same index as *df* with columns:
            * ``obi``                – Order Book Imbalance
            * ``spread_ratio``       – Bid-ask spread relative to bid
            * ``sdr``                – Supply Deviation Ratio (rolling 6)
            * ``price_momentum_dev`` – Bid deviation from rolling 12 MA

    NaN is used for warm-up rows and zero-denominator cases so that the
    caller can safely call ``dropna(subset=[...])`` before model fitting.
    """
    out = pd.DataFrame(index=df.index)

    bid_vol = df["bid_volume_top5"].replace(0, float("nan"))
    ask_vol = df["ask_volume_top5"].replace(0, float("nan"))
    bid_price = df["highest_bid_price"].replace(0, float("nan"))
    ask_price = df["lowest_ask_price"]
    sell_orders = df["total_sell_orders"]

    # ------------------------------------------------------------------
    # OBI — Order Book Imbalance
    # (bid_vol - ask_vol) / (bid_vol + ask_vol)
    # NaN when both sides are zero (denominator propagates NaN from above).
    # ------------------------------------------------------------------
    vol_sum = bid_vol + ask_vol          # NaN when both were 0
    out["obi"] = (bid_vol - ask_vol) / vol_sum

    # ------------------------------------------------------------------
    # Spread Ratio — (ask - bid) / bid
    # NaN when bid_price == 0.
    # ------------------------------------------------------------------
    out["spread_ratio"] = (ask_price - bid_price) / bid_price

    # ------------------------------------------------------------------
    # SDR — Supply Deviation Ratio
    # (supply_ma_6 - sell_orders) / supply_ma_6
    # Warm-up: first 5 rows are NaN (min_periods=6 → rolling 6).
    # ------------------------------------------------------------------
    supply_ma6 = (
        sell_orders
        .rolling(window=6, min_periods=6)
        .mean()
        .replace(0, float("nan"))
    )
    out["sdr"] = (supply_ma6 - sell_orders) / supply_ma6

    # ------------------------------------------------------------------
    # Price Momentum Deviation
    # (bid_price - bid_ma_12) / bid_ma_12
    # Warm-up: first 11 rows are NaN (min_periods=12 → rolling 12).
    # ------------------------------------------------------------------
    bid_ma12 = (
        bid_price
        .rolling(window=12, min_periods=12)
        .mean()
        .replace(0, float("nan"))
    )
    out["price_momentum_dev"] = (bid_price - bid_ma12) / bid_ma12

    return out
