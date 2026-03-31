"""Feature engineering for CS2 order-book anomaly detection (Z-Score Version)."""

import pandas as pd


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    ask_price = df["lowest_ask_price"].replace(0, float("nan"))
    sell_orders = df["total_sell_orders"].replace(0, float("nan"))
    yyyp_price = df["yyyp_sell_price"].replace(0, float("nan"))
    yyyp_lease = df["yyyp_lease_price"].replace(0, float("nan"))

    # ==========================================
    # 1. 基础特征计算 (Base Features)
    # ==========================================
    out["obi"] = (sell_orders.shift(1) - sell_orders) / sell_orders.shift(1)
    out["spread_ratio"] = (ask_price - ask_price.shift(1)) / ask_price.shift(1)

    supply_ma6 = sell_orders.rolling(window=6, min_periods=6).mean().replace(0, float("nan"))
    out["sdr"] = (supply_ma6 - sell_orders) / supply_ma6

    ask_ma12 = ask_price.rolling(window=12, min_periods=12).mean().replace(0, float("nan"))
    out["price_momentum_dev"] = (ask_price - ask_ma12) / ask_ma12

    # (升级 1 新增) 跨平台套利与租金比
    out["platform_spread"] = (ask_price - yyyp_price) / yyyp_price
    out["lease_roi"] = yyyp_lease / ask_price

    # ==========================================
    # 2. 动态阈值 Z-Score 计算 (Dynamic Thresholds)
    # ==========================================
    # 使用过去 12 个周期（36分钟）作为动态对比窗口
    z_window = 12

    # --- 新增指标：价格波动率 (Price Volatility) ---
    # 公式：标准差 / 均值。用于识别“低波动率下的突然爆发”
    price_std = ask_price.rolling(z_window, min_periods=z_window).std() + 1e-6
    ask_mean = ask_price.rolling(z_window, min_periods=z_window).mean()
    out["price_volatility"] = price_std / ask_mean

    # 加上 1e-6 (极小值) 以防止标准差为 0 导致除以零报错
    obi_mean = out["obi"].rolling(z_window, min_periods=z_window).mean()
    obi_std = out["obi"].rolling(z_window, min_periods=z_window).std() + 1e-6
    out["obi_z"] = (out["obi"] - obi_mean) / obi_std

    sdr_mean = out["sdr"].rolling(z_window, min_periods=z_window).mean()
    sdr_std = out["sdr"].rolling(z_window, min_periods=z_window).std() + 1e-6
    out["sdr_z"] = (out["sdr"] - sdr_mean) / sdr_std

    spread_mean = out["spread_ratio"].rolling(z_window, min_periods=z_window).mean()
    spread_std = out["spread_ratio"].rolling(z_window, min_periods=z_window).std() + 1e-6
    out["spread_z"] = (out["spread_ratio"] - spread_mean) / spread_std

    return out