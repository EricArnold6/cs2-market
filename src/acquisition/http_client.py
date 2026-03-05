"""Steam Community Market HTTP client for order-book data."""

import logging
import random
import re
import time
from typing import List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

from src.acquisition.exceptions import NameIdExtractionError, NameIdNotInitializedError
from src.acquisition.cache import _NameIdCache
from src.schemas.market import OrderBookSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CS2_APP_ID = 730

_LISTING_URL = "https://steamcommunity.com/market/listings/{appid}/{name}"
_ORDERBOOK_URL = "https://steamcommunity.com/market/itemordershistogram"

_NAMEID_REGEX = re.compile(r"Market_LoadOrderSpread\(\s*(\d+)\s*\)")

_RETRY_MAX = 3       # maximum retry attempts on HTTP 429
_RETRY_BASE_S = 60   # back-off base in seconds (multiplied by attempt number)
_SLEEP_MIN_S = 2.0   # minimum random sleep between requests
_SLEEP_MAX_S = 5.0   # maximum random sleep between requests

# HTML 陈列页专用延迟（比 JSON API 慢 2-5x，Steam 对 HTML 限速更严格）
_HTML_FETCH_DELAY_MIN_S: float = 5.0
_HTML_FETCH_DELAY_MAX_S: float = 10.0

# 请求 HTML 陈列页时附加的浏览器头（JSON API 不需要这些）
_HTML_EXTRA_HEADERS: dict = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
}

# ---------------------------------------------------------------------------
# User-agent pool
# ---------------------------------------------------------------------------

_USER_AGENTS: List[str] = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
    "Gecko/20100101 Firefox/123.0",
    # Firefox on Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) "
    "Gecko/20100101 Firefox/122.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


# ---------------------------------------------------------------------------
# SteamHttpClient — pure network layer (UA rotation, proxy, 429 back-off)
# ---------------------------------------------------------------------------

class SteamHttpClient:
    """Thin network shield: UA rotation, proxy dispatch, 429 exponential back-off.

    This class has **no knowledge** of Steam API semantics — it only handles
    the transport concerns.  Business-layer classes (e.g. :class:`SteamOrderBookFetcher`
    and :class:`~src.acquisition.fetcher.MarketDataFetcher`) depend on this via
    constructor injection, enabling easy unit-testing with a mock session.

    Args:
        proxies: Optional list of proxy URLs; one is chosen at random per
                 request.  ``None`` or ``[]`` disables proxy use.
        session: A ``requests.Session`` to reuse.  ``None`` creates a new one.
    """

    def __init__(
        self,
        proxies: Optional[List[str]] = None,
        session: Optional[object] = None,
    ) -> None:
        self._proxies = proxies or []
        self._session = session or (requests.Session() if requests else None)

    def get(self, url: str, params, extra_headers: dict = None) -> object:
        """Issue an HTTP GET with random UA, optional proxy, and 429 back-off.

        Args:
            url: Request URL.
            params: Query-string parameters dict, or ``None``.
            extra_headers: Additional headers to merge into the request
                (e.g. browser-like headers for HTML pages).

        Returns:
            ``requests.Response`` with ``status_code == 200``.

        Raises:
            RuntimeError: After exhausting retries, or on non-429 errors.
        """
        if requests is None:  # pragma: no cover
            raise ImportError("requests is required: pip install requests")

        ua = random.choice(_USER_AGENTS)
        headers = {"User-Agent": ua}
        if extra_headers:
            headers.update(extra_headers)
        proxy_cfg = None
        if self._proxies:
            proxy_url = random.choice(self._proxies)
            proxy_cfg = {"http": proxy_url, "https": proxy_url}

        for attempt in range(1, _RETRY_MAX + 2):  # up to _RETRY_MAX retries
            try:
                resp = self._session.get(
                    url,
                    params=params,
                    headers=headers,
                    proxies=proxy_cfg,
                    timeout=15,
                )
            except Exception as exc:
                raise RuntimeError(f"Network error fetching {url!r}: {exc}") from exc

            if resp.status_code == 429:
                if attempt > _RETRY_MAX:
                    raise RuntimeError(
                        f"HTTP 429 rate-limited after {_RETRY_MAX} retries: {url!r}"
                    )
                wait = _RETRY_BASE_S * attempt
                logger.warning(
                    "HTTP 429 on attempt %d/%d, sleeping %ds …",
                    attempt, _RETRY_MAX, wait,
                )
                time.sleep(wait)
                continue

            try:
                resp.raise_for_status()
            except Exception as exc:
                raise RuntimeError(
                    f"HTTP {resp.status_code} fetching {url!r}: {exc}"
                ) from exc

            return resp

        # Should never be reached
        raise RuntimeError(f"Exhausted retries for {url!r}")  # pragma: no cover


# ---------------------------------------------------------------------------
# SteamOrderBookFetcher
# ---------------------------------------------------------------------------

class SteamOrderBookFetcher:
    """Fetches real-time order-book snapshots from the Steam Community Market.

    All external dependencies (``requests.Session``, nameid cache) are
    injectable for easy unit testing.

    Args:
        appid: Steam application ID (default 730 for CS2).
        proxies: Optional list of proxy URLs; one is chosen at random per
                 request.
        session: A ``requests.Session`` to reuse.  ``None`` creates a new one.
        cache: A :class:`_NameIdCache` instance.  ``None`` uses the default
               on-disk cache at :data:`_CACHE_FILE`.
        http_client: A :class:`SteamHttpClient` instance.  ``None`` creates one
                     automatically from *proxies* and *session*.
    """

    def __init__(
        self,
        appid: int = CS2_APP_ID,
        proxies: Optional[List[str]] = None,
        session: Optional[object] = None,
        cache: Optional[_NameIdCache] = None,
        http_client: Optional[SteamHttpClient] = None,
    ) -> None:
        self._appid = appid
        self._cache = cache if cache is not None else _NameIdCache()
        self._http_client = http_client or SteamHttpClient(
            proxies=proxies, session=session
        )
        # Keep these for backward compatibility (tests may inspect them directly)
        self._proxies = self._http_client._proxies
        self._session = self._http_client._session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_item_nameid(self, item_name: str) -> int:
        """初始化阶段专用。轮询热路径禁止调用此方法。

        Return the Steam ``item_nameid`` for *item_name*.

        Checks the local cache first; falls back to an HTTP request to the
        item listing page to scrape the ID from the page JavaScript.

        Raises:
            NameIdExtractionError: If the nameid cannot be found in the page HTML.
            RuntimeError: If the HTTP request fails.
        """
        cached = self._cache.get(item_name)
        if cached is not None:
            logger.debug("Cache hit for %r: nameid=%d", item_name, cached)
            return cached

        if requests is None:  # pragma: no cover
            raise ImportError("requests is required: pip install requests")

        url = _LISTING_URL.format(
            appid=self._appid,
            name=requests.utils.quote(item_name),
        )
        resp = self._request(url, params=None, extra_headers=_HTML_EXTRA_HEADERS)
        match = _NAMEID_REGEX.search(resp.text)
        if not match:
            raise NameIdExtractionError(
                f"Could not find item_nameid for {item_name!r} in page HTML. URL: {url}"
            )
        nameid = int(match.group(1))
        self._cache.set(item_name, nameid)
        logger.debug("Resolved nameid for %r: %d", item_name, nameid)
        return nameid

    def fetch_order_book(self, item_name: str) -> OrderBookSnapshot:
        """Fetch and return a cleaned :class:`~src.schemas.market.OrderBookSnapshot` snapshot.

        Args:
            item_name: Market hash name of the item.

        Returns:
            :class:`~src.schemas.market.OrderBookSnapshot` with the latest bid/ask data.

        Raises:
            NameIdNotInitializedError: If item_nameid not in cache (run
                NameIdInitializer.run() first before starting the poll loop).
            RuntimeError: On HTTP errors or after exhausting retries.
        """
        # ——— 守卫：轮询路径绝不触发 HTML 请求 ———
        nameid = self._cache.get(item_name)
        if nameid is None:
            raise NameIdNotInitializedError(
                f"item_nameid for {item_name!r} not in cache. "
                f"Run NameIdInitializer.run() before starting the poll loop."
            )
        # ——— JSON API 逻辑（nameid 来源为 cache.get()）———
        params = {
            "item_nameid": nameid,
            "currency": 1,
            "country": "US",
            "language": "english",
            "two_factor": 0,
        }
        resp = self._request(_ORDERBOOK_URL, params=params)
        raw = resp.json()
        return self._parse_order_book(item_name, raw)

    def fetch_multiple(self, item_names: List[str]) -> List[OrderBookSnapshot]:
        """Fetch order books for multiple items, sleeping between each request.

        Individual item failures are logged as warnings and skipped; they do
        not propagate as exceptions.

        Args:
            item_names: List of market hash names.

        Returns:
            List of successfully fetched :class:`~src.schemas.market.OrderBookSnapshot`
            objects (may be shorter than *item_names* if some failed).
        """
        results: List[OrderBookSnapshot] = []
        for i, name in enumerate(item_names):
            if i > 0:
                sleep_s = random.uniform(_SLEEP_MIN_S, _SLEEP_MAX_S)
                time.sleep(sleep_s)
            try:
                results.append(self.fetch_order_book(name))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to fetch order book for %r: %s", name, exc)
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _request(self, url: str, params, extra_headers: dict = None) -> object:
        """Delegate to :class:`SteamHttpClient` — preserved for backward compatibility.

        Args:
            url: Request URL.
            params: Query-string parameters dict, or ``None``.
            extra_headers: Additional headers merged into the request.

        Returns:
            ``requests.Response`` with ``status_code == 200``.

        Raises:
            RuntimeError: After exhausting retries, or on non-429 errors.
        """
        return self._http_client.get(url, params, extra_headers)

    @staticmethod
    def _parse_order_book(item_name: str, raw: dict) -> OrderBookSnapshot:
        """Parse the raw ``itemordershistogram`` JSON into an :class:`OrderBookSnapshot`.

        This is a pure function with no side-effects.  Missing fields are
        silently replaced with zeros to avoid crashes on partial responses.

        Args:
            item_name: The item this snapshot belongs to.
            raw: Parsed JSON dict from the Steam API.

        Returns:
            A fully populated :class:`~src.schemas.market.OrderBookSnapshot`.
        """
        sell_graph = raw.get("sell_order_graph", [])
        buy_graph = raw.get("buy_order_graph", [])

        def _price(graph, idx):
            try:
                return float(graph[idx][0])
            except (IndexError, TypeError, ValueError):
                return 0.0

        def _vol_top5(graph):
            total = 0
            for lvl in graph[:5]:
                try:
                    total += int(lvl[1])
                except (IndexError, TypeError, ValueError):
                    pass
            return total

        def _total_orders(raw_dict, key):
            try:
                return int(str(raw_dict[key]).replace(",", ""))
            except (KeyError, ValueError, TypeError):
                return 0

        return OrderBookSnapshot(
            item_name=item_name,
            timestamp=int(time.time()),
            lowest_ask_price=_price(sell_graph, 0),
            highest_bid_price=_price(buy_graph, 0),
            ask_volume_top5=_vol_top5(sell_graph),
            bid_volume_top5=_vol_top5(buy_graph),
            total_sell_orders=_total_orders(raw, "sell_order_count"),
            total_buy_orders=_total_orders(raw, "buy_order_count"),
        )
