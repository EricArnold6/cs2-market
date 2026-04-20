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
from src.analysis.prediction.predictor import MultiFactorPredictor

logger = logging.getLogger(__name__)

_MIN_ROWS = 18
_FEATURE_COLS = ["obi", "spread_ratio", "sdr", "price_momentum_dev", "platform_spread", "price_volatility"]
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
    def __init__(self, db_config: dict, client, detection_cfg: dict | None = None) -> None:
        self._db = DatabaseConnection(db_config)
        self._client = client

        cfg = detection_cfg or {}
        z = cfg.get("z_score", {})
        vol = cfg.get("volume", {})
        arb = cfg.get("arbitrage", {})
        filt = cfg.get("accumulation_filter", {})
        whale_cfg = cfg.get("whale", {})
        model_cfg = cfg.get("model", {})

        # Z-Score 阈值
        self._accum_obi_z   = z.get("accumulation_obi_z", 1.8)
        self._accum_sdr_z   = z.get("accumulation_sdr_z", 2.0)
        self._dump_obi_z    = z.get("dump_risk_obi_z", -1.8)
        # 成交量放量倍数
        self._accum_vol_ratio = vol.get("accumulation_vol_ratio", 1.2)
        self._dump_vol_ratio  = vol.get("dump_risk_vol_ratio", 1.2)
        # 跨平台套利价差
        self._arb_spread    = arb.get("platform_spread_threshold", 0.05)
        # 建仓二次过滤
        self._spread_min    = filt.get("spread_ratio_min", 0.0)
        self._volatility_max = filt.get("volatility_max", 0.05)

        # IsolationForest 模型参数
        self._if_n_estimators  = model_cfg.get("n_estimators", 100)
        self._if_contamination = model_cfg.get("contamination", 0.05)
        self._if_random_state  = model_cfg.get("random_state", 42)
        # 数据窗口与 V5.0 预筛参数
        self._lookback_hours      = model_cfg.get("lookback_hours", 24)
        self._pre_filter_obi_z    = model_cfg.get("pre_filter_obi_z", 1.2)
        self._kline_lookback      = model_cfg.get("kline_lookback", 6)
        self._whale_ranking_limit = model_cfg.get("whale_ranking_limit", 10)

        self._whale_tracker = WhaleTracker(client, whale_cfg)
        self._predictor = MultiFactorPredictor(cfg=cfg.get("prediction"))

        user = db_config.get("user")
        password = db_config.get("password")
        host = db_config.get("host")
        port = db_config.get("port", 5432)
        dbname = db_config.get("dbname")
        self._engine_uri = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"
        self._engine = create_engine(self._engine_uri)

    # ... (fetch_recent_data 和 engineer_features 保持不变) ...
    def fetch_recent_data(self, item_nameid: int, hours: int | None = None) -> pd.DataFrame:
        hours = hours if hours is not None else self._lookback_hours
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

        model = IsolationForest(
            n_estimators=self._if_n_estimators,
            contamination=self._if_contamination,
            random_state=self._if_random_state,
        )
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
        prediction_data = None

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

        # ==========================================
        # 3. V5.0 预测路径：孤立森林未报警但盘口轻微异动
        # ==========================================
        # 当孤立森林认为"正常"，但 OBI Z-Score 已经悄悄超过 1.2σ，
        # 说明可能有庄家悄悄建仓还未触发硬阈值。启动多因子打分引擎全面体检。
        elif signal_type == "NORMAL":
            obi_z = float(last_row["obi_z"])
            platform_spread = float(last_row["platform_spread"])

            if platform_spread > self._arb_spread:
                signal_type = "ARBITRAGE_OPPORTUNITY"

            elif obi_z > self._pre_filter_obi_z and float(last_row["obi"]) > 0:
                logger.info(
                    "ID %s 突破 V5.0 初筛阈值 (obi_z=%.2f)，启动多因子预测引擎...",
                    item_nameid, obi_z,
                )

                # ── 提取 K线成交量因子 ──────────────────────────────────
                klines = self._client.fetch_kline_data(item_nameid, periods="1hour")
                vol_ratio = 1.0
                if klines and len(klines) >= self._kline_lookback:
                    recent_vols = [float(k.get("v", 0)) for k in klines[-self._kline_lookback:-1]]
                    avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 1.0
                    curr_vol = float(klines[-1].get("v", 0))
                    vol_ratio = curr_vol / (avg_vol + 1e-6)

                # ── 提取巨鲸筹码因子 ────────────────────────────────────
                top_holders = self._client.fetch_whale_ranking(item_nameid, limit=self._whale_ranking_limit)
                total_net_flow = 0
                total_active = 0
                total_lock = 0
                for holder in top_holders:
                    task_id = holder.get("id")
                    if task_id:
                        dyn = self._client.fetch_user_inventory_dynamics(task_id, item_nameid)
                        total_net_flow += dyn.get("net_change", 0)
                        total_active   += dyn.get("active_volume", 0)
                        total_lock     += dyn.get("lock_volume", 0)

                lock_rate = (total_lock / total_active) if total_active > 0 else 0.0

                # ── 多因子打分 ───────────────────────────────────────────
                factors = {
                    "obi_z":           obi_z,
                    "vol_ratio":       vol_ratio,
                    "whale_net_flow":  total_net_flow,
                    "lock_rate":       lock_rate,
                }
                prediction = self._predictor.predict(factors)

                if prediction["signal_type"] == "STRONG_PREDICTIVE_BUY":
                    signal_type = "STRONG_PREDICTIVE_BUY"
                    prediction_data = prediction

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

        if prediction_data:
            result["prediction"] = prediction_data

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

        if platform_spread > self._arb_spread:
            return "ARBITRAGE_OPPORTUNITY"

        if sdr_z > self._accum_sdr_z and obi_z > self._accum_obi_z and obi > 0:
            if spread_ratio >= self._spread_min and volatility < self._volatility_max:
                return "ACCUMULATION"
            else:
                return "IRREGULAR"

        if obi_z < self._dump_obi_z and obi < 0:
            if spread_ratio < -0.01:
                return "DUMP_RISK"
            else:
                return "IRREGULAR"

        return "IRREGULAR"

    def _verify_volume_breakout(self, csqaq_id: int, signal_type: str) -> bool:
        """K线真实成交量校验"""
        klines = self._client.fetch_kline_data(csqaq_id, periods="1hour")
        if not klines or len(klines) < self._kline_lookback:
            return True

        recent_vols = [float(k.get("v", 0)) for k in klines[-self._kline_lookback:-1]]
        avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 1.0
        curr_vol = float(klines[-1].get("v", 0))

        vol_ratio = curr_vol / (avg_vol + 1e-6)

        if signal_type == "ACCUMULATION":
            return vol_ratio > self._accum_vol_ratio
        elif signal_type == "DUMP_RISK":
            return vol_ratio > self._dump_vol_ratio

        return True