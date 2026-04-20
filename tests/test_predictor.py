"""Unit tests for src.analysis.prediction.predictor.MultiFactorPredictor (V5.0)."""

import pytest
from src.analysis.prediction.predictor import MultiFactorPredictor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_predictor(**kwargs) -> MultiFactorPredictor:
    return MultiFactorPredictor(**kwargs)


# ---------------------------------------------------------------------------
# Class: TestMultiFactorPredictorBasics
# ---------------------------------------------------------------------------

class TestMultiFactorPredictorBasics:
    """Smoke tests: constructor defaults, output keys, types."""

    def test_default_weights_sum_to_one(self):
        p = _make_predictor()
        total = sum(p.weights.values())
        assert abs(total - 1.0) < 1e-9

    def test_predict_returns_required_keys(self):
        p = _make_predictor()
        result = p.predict({})
        assert "probability" in result
        assert "signal_type" in result
        assert "factors" in result
        assert "insight_msg" in result

    def test_factors_echo_raw_inputs(self):
        p = _make_predictor()
        result = p.predict({"whale_net_flow": 5, "vol_ratio": 1.5, "obi_z": 1.0, "lock_rate": 0.3})
        assert result["factors"]["whale_net_flow"] == 5
        assert result["factors"]["vol_ratio"] == 1.5
        assert result["factors"]["obi_z"] == 1.0


# ---------------------------------------------------------------------------
# Class: TestMultiFactorPredictorEdgeCases
# ---------------------------------------------------------------------------

class TestMultiFactorPredictorEdgeCases:
    """Boundary normalization: zero inputs → 0.0, saturated inputs → 1.0."""

    def test_all_zeros_gives_zero_probability(self):
        p = _make_predictor()
        result = p.predict({"whale_net_flow": 0, "lock_rate": 0.0, "vol_ratio": 1.0, "obi_z": 0.0})
        assert result["probability"] == pytest.approx(0.0)
        assert result["signal_type"] == "NEUTRAL"

    def test_all_saturated_gives_probability_one(self):
        p = _make_predictor()
        result = p.predict({"whale_net_flow": 25, "lock_rate": 1.0, "vol_ratio": 2.0, "obi_z": 2.0})
        assert result["probability"] == pytest.approx(1.0)
        assert result["signal_type"] == "STRONG_PREDICTIVE_BUY"

    def test_negative_flow_clamped_to_zero(self):
        p = _make_predictor()
        result = p.predict({"whale_net_flow": -100, "lock_rate": 0.0, "vol_ratio": 1.0, "obi_z": 0.0})
        assert result["probability"] == pytest.approx(0.0)

    def test_oversaturated_inputs_clamped(self):
        p = _make_predictor()
        result = p.predict({"whale_net_flow": 999, "lock_rate": 5.0, "vol_ratio": 100.0, "obi_z": 50.0})
        assert result["probability"] == pytest.approx(1.0)

    def test_empty_factors_dict_does_not_raise(self):
        p = _make_predictor()
        result = p.predict({})
        assert 0.0 <= result["probability"] <= 1.0


# ---------------------------------------------------------------------------
# Class: TestMultiFactorPredictorThreshold
# ---------------------------------------------------------------------------

class TestMultiFactorPredictorThreshold:
    """Signal type flips correctly around the 65% threshold."""

    def test_below_threshold_returns_neutral(self):
        p = _make_predictor(threshold=0.65)
        # All factors at half strength → prob ≈ 0.5
        result = p.predict({"whale_net_flow": 12, "lock_rate": 0.5, "vol_ratio": 1.5, "obi_z": 1.0})
        assert result["probability"] < 0.65
        assert result["signal_type"] == "NEUTRAL"

    def test_above_threshold_returns_strong_buy(self):
        p = _make_predictor(threshold=0.65)
        # whale=25 (full), vol=2.0 (full), others = 0 → 0.35+0.25=0.60
        # Add lock=1.0 as well → 0.35+0.25+0.25=0.85
        result = p.predict({"whale_net_flow": 25, "lock_rate": 1.0, "vol_ratio": 2.0, "obi_z": 0.0})
        assert result["probability"] >= 0.65
        assert result["signal_type"] == "STRONG_PREDICTIVE_BUY"

    def test_custom_threshold_respected(self):
        p = _make_predictor(threshold=0.30)
        result = p.predict({"whale_net_flow": 10, "lock_rate": 0.0, "vol_ratio": 1.0, "obi_z": 0.0})
        # whale at 10/25 = 0.4, weight 0.35 → score = 0.14 < 0.30
        assert result["signal_type"] == "NEUTRAL"

        p2 = _make_predictor(threshold=0.10)
        result2 = p2.predict({"whale_net_flow": 10, "lock_rate": 0.0, "vol_ratio": 1.0, "obi_z": 0.0})
        assert result2["signal_type"] == "STRONG_PREDICTIVE_BUY"


# ---------------------------------------------------------------------------
# Class: TestMultiFactorPredictorInsights
# ---------------------------------------------------------------------------

class TestMultiFactorPredictorInsights:
    """insight_msg triggers correctly for each factor."""

    def test_no_insights_gives_fallback_msg(self):
        p = _make_predictor()
        result = p.predict({})
        assert result["insight_msg"] == "多因子中等强度共振。"

    def test_high_whale_flow_triggers_insight(self):
        p = _make_predictor()
        result = p.predict({"whale_net_flow": 22})  # w_score = 22/25 = 0.88 > 0.8
        assert "巨鲸疯狂吸筹" in result["insight_msg"]

    def test_high_lock_rate_triggers_insight(self):
        p = _make_predictor()
        result = p.predict({"lock_rate": 0.75})  # > 0.7
        assert "流通筹码被大面积锁死" in result["insight_msg"]

    def test_high_vol_ratio_triggers_insight(self):
        p = _make_predictor()
        result = p.predict({"vol_ratio": 1.85})  # v_score = 0.85 > 0.8
        assert "底层成交量暴力放大" in result["insight_msg"]

    def test_high_obi_z_triggers_insight(self):
        p = _make_predictor()
        result = p.predict({"obi_z": 1.7})  # z_score = 0.85 > 0.8
        assert "盘口供应严重断层" in result["insight_msg"]

    def test_multiple_insights_joined_with_comma(self):
        p = _make_predictor()
        result = p.predict({"whale_net_flow": 25, "obi_z": 2.0})
        assert "，" in result["insight_msg"]
        assert result["insight_msg"].endswith("。")
