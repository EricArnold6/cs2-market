"""Anomaly detection with Dynamic Z-Score Thresholds."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pandas as pd
from sklearn.ensemble import IsolationForest
from sqlalchemy import create_engine, text

from src.storage.database import DatabaseConnection
from src.analysis.anomaly.features import engineer_features


# 关键修改：因为 SDR 自身需要 6 个周期，再算 Z-Score 又需要 12 个周期
# 6 + 12 - 1 = 17。为了保险起见，我们将模型预热期延长至 18 个数据点（约 54 分钟）。
_MIN_ROWS = 18

_FEATURE_COLS = [
    "obi", "spread_ratio", "sdr", "price_momentum_dev",
    "platform_spread", "lease_roi", "price_volatility"
]

# 数据清洗时，需要额外验证这些 Z-Score 是否计算完成
_EVAL_COLS = _FEATURE_COLS + ["obi_z", "sdr_z", "spread_z"]

_SQL = """
    SELECT time, lowest_ask_price, highest_bid_price,
           total_sell_orders, total_buy_orders,
           yyyp_sell_price, yyyp_lease_price
    FROM order_book_snapshots
    WHERE item_nameid = :item_nameid AND time >= :cutoff
    ORDER BY time ASC
"""


class MarketAnomalyDetector:
    """Fit an Isolation Forest on recent order-book snapshots and score them."""

    def __init__(self, db_config: dict) -> None:
        self._db = DatabaseConnection(db_config)

        user = db_config.get("user")
        password = db_config.get("password")
        host = db_config.get("host")
        port = db_config.get("port", 5432)
        dbname = db_config.get("dbname")
        self._engine_uri = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"

    def fetch_recent_data(self, item_nameid: int, hours: int = 24) -> pd.DataFrame:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)

        engine = create_engine(self._engine_uri)
        try:
            with engine.connect() as conn:
                return pd.read_sql_query(
                    text(_SQL),
                    conn,
                    params={"item_nameid": item_nameid, "cutoff": cutoff},
                )
        finally:
            engine.dispose()

    def engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        return engineer_features(df)

    def detect_anomalies(self, item_nameid: int) -> dict | None:
        df_raw = self.fetch_recent_data(item_nameid)
        if df_raw.empty:
            return None

        df_feat = self.engineer_features(df_raw)
        df_feat["time"] = df_raw["time"].values

        # 这里使用包含 Z-Score 的新列表来丢弃预热期空值
        df_clean = df_feat.dropna(subset=_EVAL_COLS).reset_index(drop=True)
        if len(df_clean) < _MIN_ROWS:
            return None

        X = df_clean[_FEATURE_COLS].values

        model = IsolationForest(
            n_estimators=100,
            contamination=0.05,
            random_state=42,
        )
        labels = model.fit_predict(X)
        scores = model.score_samples(X)

        last_idx = len(df_clean) - 1
        last_row = df_clean.iloc[last_idx]
        last_label = labels[last_idx]
        last_score = float(scores[last_idx])

        if last_label == -1:
            # 修改：将包含 Z-Score 的完整 Series 传给诊断函数
            signal_type = self._evaluate_signal(last_row)
        else:
            signal_type = "NORMAL"

        ts = last_row["time"]
        timestamp = ts.isoformat() if isinstance(ts, pd.Timestamp) else str(ts)

        return {
            "timestamp": timestamp,
            "anomaly_score": last_score,
            "obi": float(last_row["obi"]),
            "spread_ratio": float(last_row["spread_ratio"]),
            "sdr": float(last_row["sdr"]),
            "price_momentum_dev": float(last_row["price_momentum_dev"]),
            "platform_spread": float(last_row["platform_spread"]),
            "signal_type": signal_type,
        }

    def _evaluate_signal(self, status: pd.Series) -> str:
        """Classify anomalous row using dynamic Z-Score and Volatility confirmation."""

        obi = float(status["obi"])
        obi_z = float(status["obi_z"])
        sdr_z = float(status["sdr_z"])
        platform_spread = float(status["platform_spread"])
        spread_ratio = float(status["spread_ratio"])
        volatility = float(status["price_volatility"])

        # 1. 跨平台搬砖信号
        if platform_spread > 0.05:
            return "ARBITRAGE_OPPORTUNITY"

        # 2. 终极建仓/突破信号 (ACCUMULATION)
        # 条件 A: 供应显著萎缩 (sdr_z > 2.0)
        # 条件 B: 瞬间被扫货 (obi_z > 2.5 且 obi > 0)
        if sdr_z > 2.0 and obi_z > 2.5 and obi > 0:
            # --- 增加波动率与价差确认（防左手倒右手假拉升） ---
            # 如果庄家只是自己挂高价自己买，价格涨了（spread_ratio > 0），
            # 但其实没人跟风，平时的波动率极大（常常上蹿下跳）。
            # 真正的建仓突破往往伴随着：平时波动率极低（volatility < 0.05 即 5%），但此刻价格上扬。
            if spread_ratio >= 0 and volatility < 0.05:
                return "ACCUMULATION"
            else:
                return "IRREGULAR"  # 疑似高波动率的“骗炮”洗盘，降级为普通异动

        # 3. 终极砸盘预警 (DUMP_RISK)
        # 抛压瞬间激增，且价格实质性下挫
        if obi_z < -2.5 and obi < 0:
            if spread_ratio < -0.01:  # 伴随至少 1% 的实质性破位下跌
                return "DUMP_RISK"
            else:
                return "IRREGULAR"  # 光挂单不砸价（可能是压盘），降级

        return "IRREGULAR"