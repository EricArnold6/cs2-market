"""
Steam Community Market price history fetcher.

The Steam API endpoint used here is the publicly accessible price-history
endpoint.  No authentication is required for items that are publicly listed.

Example URL:
  https://steamcommunity.com/market/pricehistory/?appid=730&market_hash_name=AK-47+%7C+Redline+%28Field-Tested%29

The response JSON looks like::

    {
        "success": true,
        "price_prefix": "$",
        "price_suffix": "",
        "prices": [
            ["Nov 01 2023 01: +0", "5.23", "12"],
            ...
        ]
    }

Each element is [date_string, median_price, volume].
"""

import time
import calendar
from typing import List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

from src.data.models import ItemHistory, PriceRecord

# Steam appid for CS2 / CS:GO
CS2_APP_ID = 730

_PRICE_HISTORY_URL = (
    "https://steamcommunity.com/market/pricehistory/"
    "?appid={appid}&market_hash_name={name}"
)

# Polite delay between requests to avoid rate limiting (seconds)
_REQUEST_DELAY = 3.0


def _parse_steam_date(date_str: str) -> float:
    """Convert a Steam date string like 'Nov 01 2023 01: +0' to a Unix timestamp."""
    # Strip the time component – Steam only provides daily granularity
    parts = date_str.strip().split()
    # parts[0]=month, parts[1]=day, parts[2]=year
    date_part = " ".join(parts[:3])
    t = time.strptime(date_part, "%b %d %Y")
    return float(calendar.timegm(t))


def fetch_item_history(
    item_name: str,
    appid: int = CS2_APP_ID,
    session: Optional[object] = None,
) -> ItemHistory:
    """Fetch price history for a single CS2 item from the Steam Market.

    Args:
        item_name: The market hash name of the item, e.g.
                   ``"AK-47 | Redline (Field-Tested)"``.
        appid: Steam application ID (default 730 for CS2).
        session: Optional ``requests.Session`` to reuse.  If *None* a new
                 session is created for each call.

    Returns:
        :class:`~src.data.models.ItemHistory` populated with all available
        daily records.

    Raises:
        RuntimeError: If the Steam API returns an unsuccessful response.
        ImportError: If the ``requests`` package is not installed.
    """
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

    Args:
        item_names: List of market hash names.
        appid: Steam application ID.
        delay: Seconds to wait between requests.

    Returns:
        List of :class:`~src.data.models.ItemHistory` objects.
    """
    results: List[ItemHistory] = []
    session = requests.Session() if requests else None
    for i, name in enumerate(item_names):
        if i > 0:
            time.sleep(delay)
        results.append(fetch_item_history(name, appid=appid, session=session))
    return results
