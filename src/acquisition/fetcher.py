"""
Backward-compatible fetcher module for src.acquisition.

This module retains the deprecated ``fetch_item_history()`` and
``fetch_multiple_items()`` functions and re-exports all public symbols
from the acquisition sub-modules so that existing ``from src.acquisition.fetcher
import ...`` statements continue to work.
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

# Re-exports (backward-compat hub)
from src.acquisition.exceptions import NameIdExtractionError, NameIdNotInitializedError  # noqa: F401
from src.acquisition.cache import _NameIdCache  # noqa: F401
from src.acquisition.http_client import (  # noqa: F401
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

logger = logging.getLogger(__name__)

# Legacy price-history constant (kept for deprecated functions)
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
