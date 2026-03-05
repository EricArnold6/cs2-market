"""Alert dispatcher — decides whether a detector result warrants a DingTalk alert.

Keeps ``main.py`` clean by encapsulating the "should we alert?" logic in one place.
"""

import logging

from src.alerting.bot import DingTalkAlerter
from src.alerting.formatter import format_anomaly_alert

logger = logging.getLogger(__name__)

# Signal types that are NOT worth alerting (market is quiet)
_SILENT_SIGNALS = frozenset({"NORMAL"})


class AlertDispatcher:
    """Route anomaly-detector results to DingTalk when they warrant an alert.

    Parameters
    ----------
    alerter : DingTalkAlerter
        The configured alerter instance used to deliver messages.
    """

    def __init__(self, alerter: DingTalkAlerter) -> None:
        self._alerter = alerter

    def dispatch(self, item_name: str, result: dict | None) -> bool:
        """Send a DingTalk alert when *result* is non-None and non-NORMAL.

        Parameters
        ----------
        item_name : str
            Human-readable market hash name (used as the alert title).
        result : dict or None
            The dict returned by ``MarketAnomalyDetector.detect_anomalies()``,
            or ``None`` when there is insufficient data.

        Returns
        -------
        bool
            ``True`` if an alert was successfully sent, ``False`` otherwise
            (no alert needed, or the send failed).
        """
        if result is None:
            logger.debug("dispatch: no result for %r (insufficient data)", item_name)
            return False

        signal_type: str = result.get("signal_type", "UNKNOWN")
        if signal_type in _SILENT_SIGNALS:
            logger.debug("dispatch: %r is NORMAL, skipping alert", item_name)
            return False

        logger.info(
            "dispatch: sending %s alert for %r", signal_type, item_name
        )
        payload = format_anomaly_alert(item_name, result)
        success = self._alerter.send(payload)
        if not success:
            logger.warning(
                "dispatch: alert delivery failed for %r (%s)", item_name, signal_type
            )
        return success
