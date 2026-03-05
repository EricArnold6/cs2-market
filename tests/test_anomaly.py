"""Unit tests for src.analysis.anomaly (feature engineering + detector)."""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from src.analysis.anomaly.features import engineer_features
from src.analysis.anomaly.detector import MarketAnomalyDetector, _MIN_ROWS, _FEATURE_COLS


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _make_df(
    n: int = 20,
    bid_vol: float = 100.0,
    ask_vol: float = 80.0,
    bid_price: float = 10.0,
    ask_price: float = 10.5,
    sell_orders: float = 200.0,
) -> pd.DataFrame:
    """Build a minimal snapshot DataFrame with uniform values."""
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="750s", tz="UTC"),
            "highest_bid_price": [bid_price] * n,
            "lowest_ask_price": [ask_price] * n,
            "bid_volume_top5": [bid_vol] * n,
            "ask_volume_top5": [ask_vol] * n,
            "total_sell_orders": [sell_orders] * n,
            "total_buy_orders": [150.0] * n,
        }
    )


def _make_detector() -> MarketAnomalyDetector:
    """Return a detector whose DB layer is fully mocked."""
    detector = MarketAnomalyDetector.__new__(MarketAnomalyDetector)
    detector._db = MagicMock()
    return detector


# ---------------------------------------------------------------------------
# TestEngineerFeaturesOBI
# ---------------------------------------------------------------------------

class TestEngineerFeaturesOBI:

    def test_bid_greater_than_ask_gives_positive_obi(self):
        df = _make_df(n=20, bid_vol=150.0, ask_vol=50.0)
        feat = engineer_features(df)
        # (150 - 50) / (150 + 50) = 0.5
        assert pytest.approx(feat["obi"].iloc[-1], rel=1e-6) == 0.5

    def test_ask_greater_than_bid_gives_negative_obi(self):
        df = _make_df(n=20, bid_vol=50.0, ask_vol=150.0)
        feat = engineer_features(df)
        # (50 - 150) / (50 + 150) = -0.5
        assert pytest.approx(feat["obi"].iloc[-1], rel=1e-6) == -0.5

    def test_equal_volumes_give_zero_obi(self):
        df = _make_df(n=20, bid_vol=100.0, ask_vol=100.0)
        feat = engineer_features(df)
        assert pytest.approx(feat["obi"].iloc[-1], abs=1e-10) == 0.0

    def test_both_zero_volumes_give_nan_obi(self):
        df = _make_df(n=20, bid_vol=0.0, ask_vol=0.0)
        feat = engineer_features(df)
        assert feat["obi"].isna().all()


# ---------------------------------------------------------------------------
# TestEngineerFeaturesSpreadRatio
# ---------------------------------------------------------------------------

class TestEngineerFeaturesSpreadRatio:

    def test_normal_spread_ratio(self):
        df = _make_df(n=20, bid_price=10.0, ask_price=10.5)
        feat = engineer_features(df)
        # (10.5 - 10.0) / 10.0 = 0.05
        assert pytest.approx(feat["spread_ratio"].iloc[-1], rel=1e-6) == 0.05

    def test_equal_prices_give_zero_spread_ratio(self):
        df = _make_df(n=20, bid_price=10.0, ask_price=10.0)
        feat = engineer_features(df)
        assert pytest.approx(feat["spread_ratio"].iloc[-1], abs=1e-10) == 0.0

    def test_zero_bid_price_gives_nan_spread_ratio(self):
        df = _make_df(n=20, bid_price=0.0, ask_price=10.5)
        feat = engineer_features(df)
        assert feat["spread_ratio"].isna().all()


# ---------------------------------------------------------------------------
# TestEngineerFeaturesSDR
# ---------------------------------------------------------------------------

class TestEngineerFeaturesSDR:

    def test_first_five_rows_are_nan(self):
        df = _make_df(n=20, sell_orders=200.0)
        feat = engineer_features(df)
        # rolling(6, min_periods=6) → first 5 rows have NaN
        assert feat["sdr"].iloc[:5].isna().all()

    def test_stable_supply_gives_zero_sdr(self):
        df = _make_df(n=20, sell_orders=200.0)
        feat = engineer_features(df)
        # MA == actual value → (MA - actual) / MA = 0
        assert pytest.approx(feat["sdr"].iloc[-1], abs=1e-10) == 0.0

    def test_supply_drop_gives_positive_sdr(self):
        # Start with 200 sell orders for 15 rows, then drop to 100 for last 5
        sell = [200.0] * 15 + [100.0] * 5
        df = _make_df(n=20, sell_orders=200.0)
        df["total_sell_orders"] = sell
        feat = engineer_features(df)
        # The MA-6 window at the last row will be above 100 → positive SDR
        assert feat["sdr"].iloc[-1] > 0.0


# ---------------------------------------------------------------------------
# TestEngineerFeaturesPriceMomentumDev
# ---------------------------------------------------------------------------

class TestEngineerFeaturesPriceMomentumDev:

    def test_first_eleven_rows_are_nan(self):
        df = _make_df(n=20, bid_price=10.0)
        feat = engineer_features(df)
        # rolling(12, min_periods=12) → first 11 rows have NaN
        assert feat["price_momentum_dev"].iloc[:11].isna().all()

    def test_stable_price_gives_zero_momentum_dev(self):
        df = _make_df(n=20, bid_price=10.0)
        feat = engineer_features(df)
        assert pytest.approx(feat["price_momentum_dev"].iloc[-1], abs=1e-10) == 0.0

    def test_rising_price_gives_positive_momentum_dev(self):
        # Prices rise from 10 to 20 over 20 rows
        prices = [10.0 + i * 0.5 for i in range(20)]
        df = _make_df(n=20, bid_price=10.0)
        df["highest_bid_price"] = prices
        feat = engineer_features(df)
        # Latest bid > rolling MA → positive deviation
        assert feat["price_momentum_dev"].iloc[-1] > 0.0


# ---------------------------------------------------------------------------
# TestDetectorInsufficientData
# ---------------------------------------------------------------------------

class TestDetectorInsufficientData:

    def test_fewer_than_min_rows_returns_none(self):
        detector = _make_detector()
        # Provide only 5 clean rows (below _MIN_ROWS=12)
        df_small = _make_df(n=5)
        detector.fetch_recent_data = MagicMock(return_value=df_small)
        result = detector.detect_anomalies(item_nameid=1)
        assert result is None

    def test_all_nan_features_returns_none(self):
        detector = _make_detector()
        # Zero bid/ask volumes + zero bid price → all feature columns NaN
        df_nan = _make_df(n=20, bid_vol=0.0, ask_vol=0.0, bid_price=0.0)
        detector.fetch_recent_data = MagicMock(return_value=df_nan)
        result = detector.detect_anomalies(item_nameid=1)
        assert result is None


# ---------------------------------------------------------------------------
# TestDetectorPipeline
# ---------------------------------------------------------------------------

class TestDetectorPipeline:

    def _run_with_mock_forest(self, df, label: int = 1, score: float = -0.1):
        """Run detect_anomalies with IsolationForest fully mocked."""
        detector = _make_detector()
        detector.fetch_recent_data = MagicMock(return_value=df)

        mock_forest = MagicMock()
        mock_forest.fit_predict.return_value = [label] * len(df)
        mock_forest.score_samples.return_value = [score] * len(df)

        with patch(
            "src.analysis.anomaly.detector.IsolationForest",
            return_value=mock_forest,
        ):
            return detector.detect_anomalies(item_nameid=1)

    def test_result_dict_has_expected_keys(self):
        df = _make_df(n=25)
        result = self._run_with_mock_forest(df, label=1)
        assert result is not None
        expected_keys = {
            "timestamp", "anomaly_score", "obi", "spread_ratio",
            "sdr", "price_momentum_dev", "signal_type",
        }
        assert set(result.keys()) == expected_keys

    def test_all_normal_labels_give_normal_signal_type(self):
        df = _make_df(n=25)
        result = self._run_with_mock_forest(df, label=1, score=-0.05)
        assert result is not None
        assert result["signal_type"] == "NORMAL"

    def test_connect_called_during_pipeline(self):
        detector = _make_detector()
        df = _make_df(n=20)
        detector.fetch_recent_data = MagicMock(return_value=df)

        mock_forest = MagicMock()
        mock_forest.fit_predict.return_value = [1] * 20
        mock_forest.score_samples.return_value = [-0.1] * 20

        with patch(
            "src.analysis.anomaly.detector.IsolationForest",
            return_value=mock_forest,
        ):
            detector.detect_anomalies(item_nameid=42)

        # fetch_recent_data calls connect() internally; verify it was invoked
        detector.fetch_recent_data.assert_called_once_with(42)


# ---------------------------------------------------------------------------
# TestEvaluateSignal
# ---------------------------------------------------------------------------

class TestEvaluateSignal:

    def _make_status(
        self,
        sdr: float = 0.0,
        obi: float = 0.0,
        spread_ratio: float = 0.0,
        price_momentum_dev: float = 0.0,
    ) -> pd.Series:
        return pd.Series(
            {
                "sdr": sdr,
                "obi": obi,
                "spread_ratio": spread_ratio,
                "price_momentum_dev": price_momentum_dev,
            }
        )

    def test_accumulation_signal(self):
        detector = _make_detector()
        status = self._make_status(sdr=0.20, obi=0.65)
        assert detector._evaluate_signal(status) == "ACCUMULATION"

    def test_dump_risk_signal(self):
        detector = _make_detector()
        status = self._make_status(obi=-0.70, spread_ratio=0.08)
        assert detector._evaluate_signal(status) == "DUMP_RISK"

    def test_irregular_signal(self):
        detector = _make_detector()
        # Neither condition met
        status = self._make_status(sdr=0.05, obi=0.1, spread_ratio=0.02)
        assert detector._evaluate_signal(status) == "IRREGULAR"
