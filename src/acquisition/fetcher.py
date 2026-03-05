"""
Steam Market business layer + backward-compatible fetcher module.

This module provides two things:

1. **:class:`MarketDataFetcher`** — the new business-layer class that encapsulates
   Steam-specific URL assembly, CNY request parameters, and JSON parsing into
   clean :class:`~src.schemas.market.OrderBookSnapshot` objects.  It accepts a
   :class:`~src.acquisition.http_client.SteamHttpClient` via constructor injection
   so the network layer is fully decoupled.

2. **Backward-compat re-exports** — all public symbols from the sub-modules so
   that existing ``from src.acquisition.fetcher import ...`` statements continue
   to work unchanged.  The deprecated :func:`fetch_item_history` and
   :func:`fetch_multiple_items` functions are also retained here.
"""

import calendar
import logging
import time
import warnings
from typing import List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

# ---------------------------------------------------------------------------
# Re-exports (backward-compat hub)
# ---------------------------------------------------------------------------
from src.acquisition.exceptions import NameIdExtractionError, NameIdNotInitializedError  # noqa: F401
from src.acquisition.cache import _NameIdCache  # noqa: F401
from src.acquisition.http_client import (  # noqa: F401
    SteamHttpClient,
    SteamOrderBookFetcher,
    CS2_APP_ID,
    _USER_AGENTS,
    _LISTING_URL,
    _ORDERBOOK_URL,
    _NAMEID_REGEX,
    _RETRY_MAX,
    _RETRY_BASE_S,
    _SLEEP_MIN_S,
    _SLEEP_MAX_S,
    _HTML_FETCH_DELAY_MIN_S,
    _HTML_FETCH_DELAY_MAX_S,
    _HTML_EXTRA_HEADERS,
)
from src.acquisition.initializer import NameIdInitializer, InitResult  # noqa: F401
from src.acquisition.models import ItemHistory, PriceRecord, OrderBook  # noqa: F401
from src.schemas.market import OrderBookSnapshot  # noqa: F401

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MarketDataFetcher — Steam API business layer
# ---------------------------------------------------------------------------

class MarketDataFetcher:
    """Steam-specific order-book fetcher — business layer only, no network logic.

    Encapsulates Steam CNY parameters (``currency=23``, ``country=CN``,
    ``language=schinese``), URL assembly, and JSON-to-:class:`OrderBookSnapshot`
    parsing.  All network I/O is delegated to the injected
    :class:`~src.acquisition.http_client.SteamHttpClient`.

    Typical usage::

        from src.acquisition.http_client import SteamHttpClient
        from src.acquisition.fetcher import MarketDataFetcher

        client = SteamHttpClient()
        fetcher = MarketDataFetcher(client)
        snapshot = fetcher.fetch_order_book(item_nameid=176923345, item_name="AK-47 | Redline (Field-Tested)")

    Args:
        http_client: A :class:`~src.acquisition.http_client.SteamHttpClient`
                     (or any duck-typed object exposing a compatible ``.get()``
                     method) that handles transport concerns.
    """

    API_URL = "https://steamcommunity.com/market/itemordershistogram"

    def __init__(self, http_client: SteamHttpClient) -> None:
        self._client = http_client

    def fetch_order_book(
        self,
        item_nameid: int,
        item_name: str = "",
    ) -> Optional[OrderBookSnapshot]:
        """Fetch a single order-book snapshot from the Steam Market.

        Uses CNY market parameters (currency=23, country=CN, language=schinese).

        Args:
            item_nameid: The Steam internal item nameid (integer).
            item_name: Human-readable market hash name used to populate
                :attr:`~src.schemas.market.OrderBookSnapshot.item_name`.
                Defaults to an empty string if not provided.

        Returns:
            A populated :class:`~src.schemas.market.OrderBookSnapshot`, or
            ``None`` if the API response indicates failure (``success != 1``).

        Raises:
            RuntimeError: On network errors or after exhausting retries (raised
                by the underlying :class:`~src.acquisition.http_client.SteamHttpClient`).
        """
        params = {
            "item_nameid": item_nameid,
            "currency": 23,
            "country": "CN",
            "language": "schinese",
            "two_factor": 0,
        }
        resp = self._client.get(self.API_URL, params=params)
        raw = resp.json()
        if raw.get("success") != 1:
            logger.warning(
                "Steam API returned non-success for item_nameid=%d: %r",
                item_nameid,
                raw.get("success"),
            )
            return None
        return self._parse_histogram_data(raw, item_name)

    @staticmethod
    def _parse_histogram_data(raw: dict, item_name: str) -> Optional[OrderBookSnapshot]:
        """Parse ``itemordershistogram`` JSON into an :class:`OrderBookSnapshot`.

        Delegates to the same proven parsing logic used by
        :meth:`~src.acquisition.http_client.SteamOrderBookFetcher._parse_order_book`.
        Missing or malformed fields are silently replaced with zeros.

        Args:
            raw: Parsed JSON dict from the Steam histogram API.
            item_name: Human-readable item name for the returned snapshot.

        Returns:
            A fully populated :class:`~src.schemas.market.OrderBookSnapshot`.
        """
        return SteamOrderBookFetcher._parse_order_book(item_name, raw)


# ---------------------------------------------------------------------------
# Legacy price-history constant (kept for deprecated functions)
# ---------------------------------------------------------------------------

_PRICE_HISTORY_URL = (
    "https://steamcommunity.com/market/pricehistory/"
    "?appid={appid}&market_hash_name={name}"
)
_REQUEST_DELAY = 3.0


# ---------------------------------------------------------------------------
# Deprecated legacy functions (kept for backward compatibility)
# ---------------------------------------------------------------------------

def _parse_steam_date(date_str: str) -> float:
    """Convert a Steam date string like 'Nov 01 2023 01: +0' to a Unix timestamp."""
    parts = date_str.strip().split()
    date_part = " ".join(parts[:3])
    t = time.strptime(date_part, "%b %d %Y")
    return float(calendar.timegm(t))


def fetch_item_history(
    item_name: str,
    appid: int = CS2_APP_ID,
    session: Optional[object] = None,
) -> ItemHistory:
    """Fetch price history for a single CS2 item from the Steam Market.

    .. deprecated::
        Use :class:`SteamOrderBookFetcher` instead.
    """
    warnings.warn(
        "fetch_item_history() is deprecated; use SteamOrderBookFetcher instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if requests is None:  # pragma: no cover
        raise ImportError("requests is required: pip install requests")

    url = _PRICE_HISTORY_URL.format(appid=appid, name=requests.utils.quote(item_name))
    sess = session or requests.Session()
    resp = sess.get(url, timeout=15)
    resp.raise_for_status()

    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Steam API returned failure for item: {item_name!r}")

    history = ItemHistory(item_name=item_name)
    for entry in data.get("prices", []):
        date_str, price_str, vol_str = entry[0], entry[1], entry[2]
        history.records.append(
            PriceRecord(
                timestamp=_parse_steam_date(date_str),
                price=float(price_str),
                volume=int(vol_str),
            )
        )

    return history


def fetch_multiple_items(
    item_names: List[str],
    appid: int = CS2_APP_ID,
    delay: float = _REQUEST_DELAY,
) -> List[ItemHistory]:
    """Fetch price history for multiple items with a polite delay between requests.

    .. deprecated::
        Use :class:`SteamOrderBookFetcher` instead.
    """
    warnings.warn(
        "fetch_multiple_items() is deprecated; use SteamOrderBookFetcher instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    results: List[ItemHistory] = []
    session = requests.Session() if requests else None
    for i, name in enumerate(item_names):
        if i > 0:
            time.sleep(delay)
        results.append(fetch_item_history(name, appid=appid, session=session))
    return results
