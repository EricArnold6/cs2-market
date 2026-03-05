"""DingTalk Webhook alerter with optional HMAC-SHA256 signed URL.

Only stdlib + requests (already in requirements.txt) are used.
"""

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse

try:
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None  # type: ignore

logger = logging.getLogger(__name__)


class DingTalkAlerter:
    """Send messages to a DingTalk group robot via Webhook.

    Parameters
    ----------
    webhook_url : str
        The full webhook URL, including the ``access_token`` query parameter.
    secret : str or None
        The HMAC signing secret (starts with ``SEC_``).  When provided, a
        timestamp + HMAC-SHA256 signature are appended to every request URL.
        When ``None`` (default), the bare URL is used without signing.
    """

    def __init__(self, webhook_url: str, secret: str | None = None) -> None:
        self._webhook_url = webhook_url
        self._secret = secret

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, payload: dict) -> bool:
        """POST *payload* to the DingTalk webhook.

        Parameters
        ----------
        payload : dict
            A fully-formed DingTalk message payload (e.g. as produced by
            :func:`~src.alerting.formatter.format_anomaly_alert`).

        Returns
        -------
        bool
            ``True`` if the message was accepted (HTTP 200 and ``errcode==0``),
            ``False`` on any network error or non-zero DingTalk error code.
        """
        if _requests is None:  # pragma: no cover
            logger.error("requests library is not installed.")
            return False

        url = self._get_signed_url()
        try:
            resp = _requests.post(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("DingTalk network error: %s", exc)
            return False

        if resp.status_code != 200:
            logger.error(
                "DingTalk HTTP %d: %s", resp.status_code, resp.text[:200]
            )
            return False

        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            logger.error("DingTalk non-JSON response: %s", resp.text[:200])
            return False

        if body.get("errcode", 0) != 0:
            logger.error(
                "DingTalk API error %d: %s",
                body.get("errcode"),
                body.get("errmsg", ""),
            )
            return False

        return True

    def send_text(self, message: str) -> bool:
        """Send a plain-text message.  Convenience wrapper around :meth:`send`.

        Parameters
        ----------
        message : str
            The text content to send.

        Returns
        -------
        bool
            Same semantics as :meth:`send`.
        """
        payload = {
            "msgtype": "text",
            "text": {"content": message},
        }
        return self.send(payload)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_signed_url(self) -> str:
        """Return the webhook URL, optionally with HMAC-SHA256 signature appended.

        When :attr:`_secret` is set, the DingTalk-specified signing scheme is
        applied:

        1. Build the sign string as ``{timestamp}\\n{secret}``.
        2. Compute HMAC-SHA256 of that string (key = secret bytes).
        3. Base64-encode the digest, then URL-encode the result.
        4. Append ``&timestamp=...&sign=...`` to the webhook URL.

        Returns
        -------
        str
            Bare webhook URL (no secret) or signed URL (with secret).
        """
        if not self._secret:
            return self._webhook_url

        timestamp = str(int(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self._secret}"
        hmac_code = hmac.new(
            self._secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"{self._webhook_url}&timestamp={timestamp}&sign={sign}"
