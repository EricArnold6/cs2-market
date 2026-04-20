"""V5.0 Multi-Factor Probability Predictor.

Converts raw market signals into a normalized 0.0–1.0 probability score
by weighting four independent factors. A composite score ≥ 0.65 triggers
a ``STRONG_PREDICTIVE_BUY`` signal even when no single factor is extreme.
"""

import logging

logger = logging.getLogger(__name__)


class MultiFactorPredictor:
    """V5.0 多因子综合概率预测引擎。

    Parameters
    ----------
    weights : dict, optional
        Custom weight mapping. Keys: ``whale_flow``, ``liquidity_lock``,
        ``volume_breakout``, ``order_book_z``. Must sum to 1.0.
    threshold : float, optional
        Minimum probability to trigger ``STRONG_PREDICTIVE_BUY``. Default 0.65.
    """

    def __init__(
        self,
        weights: dict | None = None,
        threshold: float = 0.60,
    ) -> None:
        # 华尔街金科玉律：底层筹码数据 > K线量价数据 > 盘口挂单数据
        self.weights = weights or {
            "whale_flow":      0.35,  # 巨鲸净流入 (跟踪庄家底牌)
            "liquidity_lock":  0.25,  # 筹码锁死度 (宏观流通盘缩减)
            "volume_breakout": 0.25,  # K线成交量倍率 (真金白银共振)
            "order_book_z":    0.15,  # 孤立森林盘口异动 (情绪与价差)
        }
        self.threshold = threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def predict(self, factors: dict) -> dict:
        """Score market factors and return a prediction dict.

        Parameters
        ----------
        factors : dict
            Expected keys (all optional, default 0):

            * ``whale_net_flow``  – net units absorbed by top holders (int)
            * ``lock_rate``       – fraction of active volume that was locked (0–1 float)
            * ``vol_ratio``       – current candle volume / recent 5-candle average (float)
            * ``obi_z``           – order-book imbalance Z-score (float)

        Returns
        -------
        dict
            Keys: ``probability`` (float 0–1), ``signal_type`` (str),
            ``factors`` (echo of raw inputs), ``insight_msg`` (str).
        """
        raw_flow = factors.get("whale_net_flow", 0)
        l_score  = factors.get("lock_rate", 0.0)
        raw_vol  = factors.get("vol_ratio", 1.0)
        raw_z    = factors.get("obi_z", 0.0)

        # ── 因子归一化：将无限延伸的数值映射到 [0.0, 1.0] ──────────────
        # 巨鲸净流入：吸筹 25 把即满分，净流出得 0 分
        w_score = max(0.0, min(1.0, raw_flow / 25.0))

        # 筹码锁死度：已经是 0–1 的比例，直接使用
        l_score = max(0.0, min(1.0, float(l_score)))

        # K线成交量：达到日常 2.0 倍即满分（1.0 倍以下得 0 分）
        v_score = max(0.0, min(1.0, (raw_vol - 1.0) / 1.0))

        # 盘口异动：Z-Score 达到 2.0σ 即满分
        z_score = max(0.0, min(1.0, raw_z / 2.0))

        # ── 加权总分 ────────────────────────────────────────────────────
        probability = (
            w_score * self.weights["whale_flow"]
            + l_score * self.weights["liquidity_lock"]
            + v_score * self.weights["volume_breakout"]
            + z_score * self.weights["order_book_z"]
        )

        # ── 洞察简报 ────────────────────────────────────────────────────
        insights = []
        if w_score > 0.8:
            insights.append("巨鲸疯狂吸筹")
        if l_score > 0.7:
            insights.append("流通筹码被大面积锁死")
        if v_score > 0.8:
            insights.append("底层成交量暴力放大")
        if z_score > 0.8:
            insights.append("盘口供应严重断层")

        insight_msg = (
            "，".join(insights) + "。" if insights else "多因子中等强度共振。"
        )

        signal_type = (
            "STRONG_PREDICTIVE_BUY" if probability >= self.threshold else "NEUTRAL"
        )

        logger.debug(
            "MultiFactorPredictor: prob=%.3f signal=%s [w=%.2f l=%.2f v=%.2f z=%.2f]",
            probability, signal_type, w_score, l_score, v_score, z_score,
        )

        return {
            "probability":   probability,
            "signal_type":   signal_type,
            "factors": {
                "whale_net_flow": raw_flow,
                "lock_rate":      l_score,
                "vol_ratio":      raw_vol,
                "obi_z":          raw_z,
            },
            "insight_msg": insight_msg,
        }
