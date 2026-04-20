"""Anomaly detection with Dynamic Z-Score, Volume Verification, and Whale Tracking."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import pandas as pd
from sklearn.ensemble import IsolationForest
from sqlalchemy import create_engine, text

from src.storage.database import DatabaseConnection
from src.analysis.anomaly.features import engineer_features
from src.analysis.prediction.whale_tracker import WhaleTracker

logger = logging.getLogger(__name__)

_MIN_ROWS = 18
_FEATURE_COLS = ["obi", "spread_ratio", "sdr", "price_momentum_dev", "platform_spread", "lease_roi", "price_volatility"]
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
    """Fit an Isolation Forest and verify signals with Level-2/Whale data."""

    # ⚠️ 架构升级：注入 API 客户端，让探测器能拉取验证数据
    def __init__(self, db_config: dict, client) -> None:
        self._db = DatabaseConnection(db_config)
        self._client = client
        self._whale_tracker = WhaleTracker(client)

        user = db_config.get("user")
        password = db_config.get("password")
        host = db_config.get("host")
        port = db_config.get("port", 5432)
        dbname = db_config.get("dbname")
        self._engine_uri = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"
        self._engine = create_engine(self._engine_uri)

    # ... (fetch_recent_data 和 engineer_features 保持不变) ...
    def fetch_recent_data(self, item_nameid: int, hours: int = 24) -> pd.DataFrame:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        with self._engine.connect() as conn:
            return pd.read_sql_query(
                text(_SQL),
                conn,
                params={"item_nameid": item_nameid, "cutoff": cutoff},
            )

    def engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        return engineer_features(df)

    def detect_anomalies(self, item_nameid: int) -> dict | None:
        df_raw = self.fetch_recent_data(item_nameid)
        if df_raw.empty:
            return None

        df_feat = self.engineer_features(df_raw)
        df_feat["time"] = df_raw["time"].values

        df_clean = df_feat.dropna(subset=_EVAL_COLS).reset_index(drop=True)
        if len(df_clean) < _MIN_ROWS:
            return None

        X = df_clean[_FEATURE_COLS].values

        model = IsolationForest(n_estimators=100, contamination=0.05, random_state=42)
        labels = model.fit_predict(X)
        scores = model.score_samples(X)

        last_idx = len(df_clean) - 1
        last_row = df_clean.iloc[last_idx]
        last_label = labels[last_idx]
        last_score = float(scores[last_idx])

        # 1. 基础异常评估
        if last_label == -1:
            signal_type = self._evaluate_signal(last_row)
        else:
            signal_type = "NORMAL"

        whale_msg = None

        # ==========================================
        # 2. 信号二次/三次升级验证逻辑 (核心重构点)
        # ==========================================
        if signal_type in ("ACCUMULATION", "DUMP_RISK"):
            is_valid_volume = self._verify_volume_breakout(item_nameid, signal_type)

            if is_valid_volume:
                # K线放量确认，触发巨鲸追踪
                whale_data = self._whale_tracker.calculate_accumulation_index(item_nameid)

                if whale_data["status"] == "STRONG_PREDICTIVE_BUY" and signal_type == "ACCUMULATION":
                    signal_type = "WHALE_CONFIRMED_BUY"
                    whale_msg = whale_data["msg"]
                elif whale_data["status"] == "PREDICTIVE_DUMP" and signal_type == "ACCUMULATION":
                    # 庄家借大盘拉升出货，拦截！
                    signal_type = "IRREGULAR"
            else:
                # 无量空涨，拦截！
                signal_type = "IRREGULAR"

        ts = last_row["time"]
        timestamp = ts.isoformat() if isinstance(ts, pd.Timestamp) else str(ts)

        result = {
            "timestamp": timestamp,
            "anomaly_score": last_score,
            "obi": float(last_row["obi"]),
            "spread_ratio": float(last_row["spread_ratio"]),
            "sdr": float(last_row["sdr"]),
            "price_momentum_dev": float(last_row["price_momentum_dev"]),
            "platform_spread": float(last_row["platform_spread"]),
            "price_volatility": float(last_row["price_volatility"]),
            "signal_type": signal_type,
        }

        if whale_msg:
            result["whale_msg"] = whale_msg

        return result

    def _evaluate_signal(self, status: pd.Series) -> str:
        """Classify anomalous row using dynamic Z-Score logic."""
        # ... (保持你之前的隔离森林判定逻辑不变) ...
        obi = float(status["obi"])
        obi_z = float(status["obi_z"])
        sdr_z = float(status["sdr_z"])
        platform_spread = float(status["platform_spread"])
        spread_ratio = float(status["spread_ratio"])
        volatility = float(status["price_volatility"])

        if platform_spread > 0.05:
            return "ARBITRAGE_OPPORTUNITY"

        if sdr_z > 2.0 and obi_z > 2.5 and obi > 0:
            if spread_ratio >= 0 and volatility < 0.05:
                return "ACCUMULATION"
            else:
                return "IRREGULAR"

        if obi_z < -2.5 and obi < 0:
            if spread_ratio < -0.01:
                return "DUMP_RISK"
            else:
                return "IRREGULAR"

        return "IRREGULAR"

    def _verify_volume_breakout(self, csqaq_id: int, signal_type: str) -> bool:
        """K线真实成交量校验"""
        klines = self._client.fetch_kline_data(csqaq_id, periods="1hour")
        if not klines or len(klines) < 6:
            return True

        recent_vols = [float(k.get("v", 0)) for k in klines[-6:-1]]
        avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 1.0
        curr_vol = float(klines[-1].get("v", 0))

        vol_ratio = curr_vol / (avg_vol + 1e-6)

        if signal_type == "ACCUMULATION":
            return vol_ratio > 1.5
        elif signal_type == "DUMP_RISK":
            return vol_ratio > 1.2

        return True