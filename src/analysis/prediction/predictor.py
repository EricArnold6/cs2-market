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
    cfg : dict, optional
        Sub-dict from ``settings["detection"]["prediction"]``.  All other
        parameters are read from here when provided.
    weights : dict, optional
        Explicit weight override (takes precedence over *cfg*).
        Keys: ``whale_flow``, ``liquidity_lock``, ``volume_breakout``,
        ``order_book_z``. Must sum to 1.0.
    threshold : float, optional
        Minimum probability to trigger ``STRONG_PREDICTIVE_BUY``.
        Overrides *cfg* when provided explicitly.
    """

    _DEFAULT_WEIGHTS = {
        "whale_flow":      0.35,  # 巨鲸净流入 (跟踪庄家底牌)
        "liquidity_lock":  0.25,  # 筹码锁死度 (宏观流通盘缩减)
        "volume_breakout": 0.25,  # K线成交量倍率 (真金白银共振)
        "order_book_z":    0.15,  # 孤立森林盘口异动 (情绪与价差)
    }
    _DEFAULT_THRESHOLD = 0.60

    def __init__(
        self,
        cfg: dict | None = None,
        weights: dict | None = None,
        threshold: float | None = None,
    ) -> None:
        c = cfg or {}
        norm = c.get("normalization", {})
        ins  = c.get("insight_thresholds", {})

        # 华尔街金科玉律：底层筹码数据 > K线量价数据 > 盘口挂单数据
        self.weights = (
            weights
            or c.get("weights")
            or self._DEFAULT_WEIGHTS
        )
        self.threshold = (
            threshold
            if threshold is not None
            else c.get("threshold", self._DEFAULT_THRESHOLD)
        )

        # 归一化上限（从 settings 读取，保留原值作为后备）
        self._whale_flow_max  = norm.get("whale_flow_max",  25.0)
        self._obi_z_max       = norm.get("obi_z_max",        2.0)
        self._vol_ratio_base  = norm.get("vol_ratio_base",   1.0)
        self._vol_ratio_range = norm.get("vol_ratio_range",  1.0)

        # 洞察简报触发阈值
        self._ins_whale  = ins.get("whale",   0.8)
        self._ins_lock   = ins.get("lock",    0.7)
        self._ins_volume = ins.get("volume",  0.8)
        self._ins_z      = ins.get("z_score", 0.8)

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
        # 巨鲸净流入：吸筹达 whale_flow_max 把即满分，净流出得 0 分
        w_score = max(0.0, min(1.0, raw_flow / self._whale_flow_max))

        # 筹码锁死度：已经是 0–1 的比例，直接使用
        l_score = max(0.0, min(1.0, float(l_score)))

        # K线成交量：达到日常 (vol_ratio_base + vol_ratio_range) 倍即满分
        v_score = max(0.0, min(1.0, (raw_vol - self._vol_ratio_base) / self._vol_ratio_range))

        # 盘口异动：Z-Score 达到 obi_z_max σ 即满分
        z_score = max(0.0, min(1.0, raw_z / self._obi_z_max))

        # ── 加权总分 ────────────────────────────────────────────────────
        probability = (
            w_score * self.weights["whale_flow"]
            + l_score * self.weights["liquidity_lock"]
            + v_score * self.weights["volume_breakout"]
            + z_score * self.weights["order_book_z"]
        )

        # ── 洞察简报 ────────────────────────────────────────────────────
        insights = []
        if w_score > self._ins_whale:
            insights.append("巨鲸疯狂吸筹")
        if l_score > self._ins_lock:
            insights.append("流通筹码被大面积锁死")
        if v_score > self._ins_volume:
            insights.append("底层成交量暴力放大")
        if z_score > self._ins_z:
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
