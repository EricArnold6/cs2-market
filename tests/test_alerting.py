"""Unit tests for src.alerting (formatter, bot, dispatcher)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.alerting.bot import DingTalkAlerter
from src.alerting.dispatcher import AlertDispatcher
from src.alerting.formatter import format_anomaly_alert


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _make_result(
    signal_type: str = "NORMAL",
    obi: float = 0.1,
    sdr: float = 0.05,
    spread_ratio: float = 0.02,
    price_momentum_dev: float = 0.01,
    anomaly_score: float = -0.08,
    timestamp: str = "2026-03-05T12:00:00+00:00",
) -> dict:
    """Return a detector result dict with sensible defaults."""
    return {
        "signal_type": signal_type,
        "obi": obi,
        "sdr": sdr,
        "spread_ratio": spread_ratio,
        "price_momentum_dev": price_momentum_dev,
        "anomaly_score": anomaly_score,
        "timestamp": timestamp,
    }


def _make_alerter(
    webhook_url: str = "https://oapi.dingtalk.com/robot/send?access_token=FAKE",
    secret: str | None = None,
) -> DingTalkAlerter:
    """Return a DingTalkAlerter without making any real HTTP calls."""
    return DingTalkAlerter(webhook_url=webhook_url, secret=secret)


def _make_dispatcher(alerter: DingTalkAlerter | None = None) -> AlertDispatcher:
    """Return an AlertDispatcher backed by a mock alerter by default."""
    if alerter is None:
        alerter = MagicMock(spec=DingTalkAlerter)
        alerter.send.return_value = True
    return AlertDispatcher(alerter)


# ---------------------------------------------------------------------------
# TestFormatter
# ---------------------------------------------------------------------------

class TestFormatter:

    def test_payload_has_msgtype_markdown(self):
        result = _make_result(signal_type="NORMAL")
        payload = format_anomaly_alert("AK-47 | Redline (Field-Tested)", result)
        assert payload["msgtype"] == "markdown"
        assert "markdown" in payload
        assert "title" in payload["markdown"]
        assert "text" in payload["markdown"]

    def test_normal_signal_produces_valid_payload(self):
        """format_anomaly_alert always returns a valid payload regardless of signal type."""
        result = _make_result(signal_type="NORMAL")
        payload = format_anomaly_alert("Some Item", result)
        assert isinstance(payload["markdown"]["title"], str)
        assert isinstance(payload["markdown"]["text"], str)
        assert len(payload["markdown"]["title"]) > 0
        assert len(payload["markdown"]["text"]) > 0

    def test_accumulation_text_contains_red_color(self):
        result = _make_result(signal_type="ACCUMULATION", obi=0.7, sdr=0.2)
        payload = format_anomaly_alert("AWP | Asiimov (Field-Tested)", result)
        text = payload["markdown"]["text"]
        assert "red" in text

    def test_dump_risk_text_contains_orange_color(self):
        result = _make_result(signal_type="DUMP_RISK", obi=-0.8, spread_ratio=0.1)
        payload = format_anomaly_alert("AWP | Asiimov (Field-Tested)", result)
        text = payload["markdown"]["text"]
        assert "orange" in text

    def test_item_name_appears_in_text(self):
        item_name = "M4A4 | Howl (Factory New)"
        result = _make_result(signal_type="ACCUMULATION")
        payload = format_anomaly_alert(item_name, result)
        assert item_name in payload["markdown"]["text"]

    def test_metrics_appear_in_text(self):
        result = _make_result(
            signal_type="IRREGULAR",
            obi=0.1234,
            sdr=0.0567,
            spread_ratio=0.0891,
            price_momentum_dev=-0.0234,
            anomaly_score=-0.4321,
        )
        payload = format_anomaly_alert("Test Item", result)
        text = payload["markdown"]["text"]
        # Spot-check that formatted values appear
        assert "0.1234" in text
        assert "0.0567" in text

    def test_timestamp_appears_in_text(self):
        ts = "2026-01-15T08:30:00+00:00"
        result = _make_result(timestamp=ts)
        payload = format_anomaly_alert("Test Item", result)
        assert ts in payload["markdown"]["text"]


# ---------------------------------------------------------------------------
# TestDingTalkAlerterSigning
# ---------------------------------------------------------------------------

class TestDingTalkAlerterSigning:

    def test_no_secret_returns_bare_url(self):
        url = "https://oapi.dingtalk.com/robot/send?access_token=FAKE"
        alerter = _make_alerter(webhook_url=url, secret=None)
        signed = alerter._get_signed_url()
        assert signed == url
        assert "timestamp" not in signed
        assert "sign" not in signed

    def test_with_secret_appends_timestamp_and_sign(self):
        url = "https://oapi.dingtalk.com/robot/send?access_token=FAKE"
        alerter = _make_alerter(webhook_url=url, secret="SEC_testsecret")
        signed = alerter._get_signed_url()
        assert signed.startswith(url)
        assert "&timestamp=" in signed
        assert "&sign=" in signed

    def test_with_secret_sign_is_not_empty(self):
        alerter = _make_alerter(secret="SEC_anothersecret")
        signed = alerter._get_signed_url()
        # Extract sign value — must be a non-trivial base64 string
        sign_part = [p for p in signed.split("&") if p.startswith("sign=")]
        assert len(sign_part) == 1
        sign_value = sign_part[0][len("sign="):]
        assert len(sign_value) > 10

    def test_send_success_returns_true(self):
        alerter = _make_alerter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}

        with patch("src.alerting.bot._requests") as mock_requests:
            mock_requests.post.return_value = mock_resp
            result = alerter.send({"msgtype": "text", "text": {"content": "hi"}})

        assert result is True
        mock_requests.post.assert_called_once()

    def test_send_non_200_returns_false(self):
        alerter = _make_alerter()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("src.alerting.bot._requests") as mock_requests:
            mock_requests.post.return_value = mock_resp
            result = alerter.send({"msgtype": "text", "text": {"content": "hi"}})

        assert result is False

    def test_send_network_error_returns_false(self):
        alerter = _make_alerter()

        with patch("src.alerting.bot._requests") as mock_requests:
            mock_requests.post.side_effect = ConnectionError("Connection refused")
            result = alerter.send({"msgtype": "text", "text": {"content": "hi"}})

        assert result is False

    def test_send_dingtalk_errcode_nonzero_returns_false(self):
        alerter = _make_alerter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 310000, "errmsg": "sign not match"}

        with patch("src.alerting.bot._requests") as mock_requests:
            mock_requests.post.return_value = mock_resp
            result = alerter.send({"msgtype": "text", "text": {"content": "hi"}})

        assert result is False

    def test_send_text_calls_post_with_text_payload(self):
        alerter = _make_alerter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}

        with patch("src.alerting.bot._requests") as mock_requests:
            mock_requests.post.return_value = mock_resp
            result = alerter.send_text("Hello, DingTalk!")

        assert result is True
        call_kwargs = mock_requests.post.call_args
        # Verify the posted body contains the text content
        posted_body = call_kwargs[1].get("data") or call_kwargs[0][1]
        posted_dict = json.loads(posted_body.decode("utf-8"))
        assert posted_dict["msgtype"] == "text"
        assert posted_dict["text"]["content"] == "Hello, DingTalk!"


# ---------------------------------------------------------------------------
# TestAlertDispatcher
# ---------------------------------------------------------------------------

class TestAlertDispatcher:

    def test_dispatch_none_result_returns_false(self):
        mock_alerter = MagicMock(spec=DingTalkAlerter)
        dispatcher = AlertDispatcher(mock_alerter)
        result = dispatcher.dispatch("AK-47 | Redline", None)
        assert result is False
        mock_alerter.send.assert_not_called()

    def test_dispatch_normal_signal_returns_false(self):
        mock_alerter = MagicMock(spec=DingTalkAlerter)
        dispatcher = AlertDispatcher(mock_alerter)
        result = dispatcher.dispatch("AK-47 | Redline", _make_result(signal_type="NORMAL"))
        assert result is False
        mock_alerter.send.assert_not_called()

    def test_dispatch_accumulation_calls_alerter_and_returns_true(self):
        mock_alerter = MagicMock(spec=DingTalkAlerter)
        mock_alerter.send.return_value = True
        dispatcher = AlertDispatcher(mock_alerter)
        result = dispatcher.dispatch(
            "AWP | Asiimov",
            _make_result(signal_type="ACCUMULATION", obi=0.7, sdr=0.2),
        )
        assert result is True
        mock_alerter.send.assert_called_once()

    def test_dispatch_dump_risk_calls_alerter_and_returns_true(self):
        mock_alerter = MagicMock(spec=DingTalkAlerter)
        mock_alerter.send.return_value = True
        dispatcher = AlertDispatcher(mock_alerter)
        result = dispatcher.dispatch(
            "AWP | Asiimov",
            _make_result(signal_type="DUMP_RISK", obi=-0.8, spread_ratio=0.1),
        )
        assert result is True
        mock_alerter.send.assert_called_once()

    def test_dispatch_irregular_calls_alerter_and_returns_true(self):
        mock_alerter = MagicMock(spec=DingTalkAlerter)
        mock_alerter.send.return_value = True
        dispatcher = AlertDispatcher(mock_alerter)
        result = dispatcher.dispatch(
            "Some Item",
            _make_result(signal_type="IRREGULAR"),
        )
        assert result is True
        mock_alerter.send.assert_called_once()

    def test_alerter_send_failure_returns_false(self):
        mock_alerter = MagicMock(spec=DingTalkAlerter)
        mock_alerter.send.return_value = False
        dispatcher = AlertDispatcher(mock_alerter)
        result = dispatcher.dispatch(
            "Some Item",
            _make_result(signal_type="ACCUMULATION"),
        )
        assert result is False
        mock_alerter.send.assert_called_once()

    def test_dispatch_passes_correct_payload_structure(self):
        """The payload sent by the dispatcher must be a valid DingTalk markdown dict."""
        mock_alerter = MagicMock(spec=DingTalkAlerter)
        mock_alerter.send.return_value = True
        dispatcher = AlertDispatcher(mock_alerter)
        dispatcher.dispatch("Test Item", _make_result(signal_type="DUMP_RISK"))

        call_args = mock_alerter.send.call_args
        payload = call_args[0][0]
        assert payload["msgtype"] == "markdown"
        assert "markdown" in payload
        assert "title" in payload["markdown"]
        assert "text" in payload["markdown"]
