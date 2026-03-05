"""Anomaly detection for CS2 order-book snapshots using Isolation Forest.

Detects low-density data points that correspond to unusual market-microstructure
events (sudden OBI spikes, supply collapses, spread widening) that are
statistically consistent with market-maker (盘主) activity.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pandas as pd
from sklearn.ensemble import IsolationForest
from sqlalchemy import create_engine, text

from src.storage.database import DatabaseConnection
from src.analysis.anomaly.features import engineer_features


_MIN_ROWS = 12          # driven by price_momentum_dev warm-up (rolling-12)
_FEATURE_COLS = ["obi", "spread_ratio", "sdr", "price_momentum_dev"]

# 使用 SQLAlchemy 标准的命名占位符 (:参数名) 替代 psycopg2 的 %s
_SQL = """
    SELECT time, lowest_ask_price, highest_bid_price,
           ask_volume_top5, bid_volume_top5, total_sell_orders, total_buy_orders
    FROM order_book_snapshots
    WHERE item_nameid = :item_nameid AND time >= :cutoff
    ORDER BY time ASC
"""


class MarketAnomalyDetector:
    """Fit an Isolation Forest on recent order-book snapshots and score them.

    Parameters
    ----------
    db_config : dict
        psycopg2 connection keyword arguments (host, dbname, user, password,
        port, …).
    """

    def __init__(self, db_config: dict) -> None:
        self._db = DatabaseConnection(db_config)

        # 组装 SQLAlchemy 标准的连接 URI
        user = db_config.get("user")
        password = db_config.get("password")
        host = db_config.get("host")
        port = db_config.get("port", 5432)
        dbname = db_config.get("dbname")
        self._engine_uri = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_recent_data(self, item_nameid: int, hours: int = 24) -> pd.DataFrame:
        """Query the last *hours* hours of snapshots for *item_nameid*.

        Safely uses SQLAlchemy engine to prevent Pandas DBAPI2 warnings.
        The engine is created lazily and disposed of properly.

        Returns
        -------
        pd.DataFrame
            Columns: time, lowest_ask_price, highest_bid_price,
            ask_volume_top5, bid_volume_top5, total_sell_orders,
            total_buy_orders.  May be empty if no data exist.
        """
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)

        # 实例化数据库引擎
        engine = create_engine(self._engine_uri)
        try:
            # 使用安全上下文管理器建立连接并读取数据
            with engine.connect() as conn:
                return pd.read_sql_query(
                    text(_SQL),
                    conn,
                    params={"item_nameid": item_nameid, "cutoff": cutoff},
                )
        finally:
            # 释放连接池资源
            engine.dispose()

    def engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Delegate to the module-level pure function."""
        return engineer_features(df)

    def detect_anomalies(self, item_nameid: int) -> dict | None:
        """Run the full anomaly-detection pipeline for one item.

        Returns
        -------
        dict | None
            *None* if there is not enough clean data (< ``_MIN_ROWS`` rows
            after feature engineering and NaN-dropping).

            Otherwise a dict with keys:
                * ``timestamp``          – ISO-8601 string of the latest row
                * ``anomaly_score``      – continuous Isolation Forest score
                  (more negative = more anomalous)
                * ``obi``
                * ``spread_ratio``
                * ``sdr``
                * ``price_momentum_dev``
                * ``signal_type``        – ``"NORMAL"``, ``"ACCUMULATION"``,
                  ``"DUMP_RISK"``, or ``"IRREGULAR"``
        """
        df_raw = self.fetch_recent_data(item_nameid)

        # 拦截空数据，防止特征工程报错
        if df_raw.empty:
            return None

        df_feat = self.engineer_features(df_raw)
        df_feat["time"] = df_raw["time"].values

        df_clean = df_feat.dropna(subset=_FEATURE_COLS).reset_index(drop=True)
        if len(df_clean) < _MIN_ROWS:
            return None

        X = df_clean[_FEATURE_COLS].values

        model = IsolationForest(
            n_estimators=100,
            contamination=0.05,
            random_state=42,
        )
        labels = model.fit_predict(X)           # 1 = normal, -1 = anomaly
        scores = model.score_samples(X)         # continuous; lower = more anomalous

        last_idx = len(df_clean) - 1
        last_row = df_clean.iloc[last_idx]
        last_label = labels[last_idx]
        last_score = float(scores[last_idx])
        last_status = last_row[_FEATURE_COLS]

        if last_label == -1:
            signal_type = self._evaluate_signal(last_status)
        else:
            signal_type = "NORMAL"

        ts = last_row["time"]
        if isinstance(ts, pd.Timestamp):
            timestamp = ts.isoformat()
        else:
            timestamp = str(ts)

        return {
            "timestamp": timestamp,
            "anomaly_score": last_score,
            "obi": float(last_row["obi"]),
            "spread_ratio": float(last_row["spread_ratio"]),
            "sdr": float(last_row["sdr"]),
            "price_momentum_dev": float(last_row["price_momentum_dev"]),
            "signal_type": signal_type,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evaluate_signal(self, status: pd.Series) -> str:
        """Classify an anomalous row into a market-event label.

        Parameters
        ----------
        status : pd.Series
            Must contain ``obi``, ``spread_ratio``, ``sdr``,
            ``price_momentum_dev``.

        Returns
        -------
        str
            ``"ACCUMULATION"`` (建仓扫货), ``"DUMP_RISK"`` (撤单/砸盘),
            or ``"IRREGULAR"``.
        """
        sdr = float(status["sdr"])
        obi = float(status["obi"])
        spread_ratio = float(status["spread_ratio"])

        if sdr > 0.10 and obi > 0.5:
            return "ACCUMULATION"
        if obi < -0.6 and spread_ratio > 0.05:
            return "DUMP_RISK"
        return "IRREGULAR"