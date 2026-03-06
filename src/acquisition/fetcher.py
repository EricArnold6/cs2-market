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
import re
from typing import List, Optional, Dict

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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MarketDataFetcher — Steam API business layer
# ---------------------------------------------------------------------------

class MarketDataFetcher:
    """Steam-specific order-book fetcher — business layer only, no network logic.

    Encapsulates Steam CNY parameters, URL assembly, and JSON parsing.
    All network I/O is delegated to the injected SteamHttpClient.
    """

    API_URL = "https://steamcommunity.com/market/itemordershistogram"

    def __init__(self, http_client: SteamHttpClient) -> None:
        self._client = http_client

    def fetch_order_book(
            self,
            item_nameid: int,
            item_name: str = "",
    ) -> Optional[Dict]:
        """Fetch a single order-book snapshot from the Steam Market."""
        params = {
            "item_nameid": item_nameid,
            "currency": 23,
            "country": "CN",
            "language": "schinese",
            "two_factor": 0,
        }

        # 兼容不同 client 封装的 get 方法
        if hasattr(self._client, 'safe_get'):
            resp = self._client.safe_get(self.API_URL, params=params)
        else:
            resp = self._client.get(self.API_URL, params=params)

        if not resp:
            return None

        try:
            raw = resp.json()
        except ValueError:
            logger.error(f"Failed to decode JSON for item {item_nameid}")
            return None

        if raw.get("success") != 1:
            logger.warning(f"Steam API returned non-success for item_nameid={item_nameid}")
            return None

        return self._parse_histogram_data(raw, item_nameid)

    def _parse_histogram_data(self, raw_data: Dict, item_nameid: int) -> Dict:
        """核心清洗逻辑：将图表坐标点转化为量化所需的 5 档盘口因子"""
        try:
            asks = raw_data.get('sell_order_graph', [])
            bids = raw_data.get('buy_order_graph', [])

            top_5_asks = asks[:5] if len(asks) >= 5 else asks
            top_5_bids = bids[:5] if len(bids) >= 5 else bids

            snapshot = {
                "item_nameid": item_nameid,
                "timestamp": int(time.time()),

                "lowest_ask_price": float(top_5_asks[0][0]) if top_5_asks else None,
                "highest_bid_price": float(top_5_bids[0][0]) if top_5_bids else None,

                "ask_volume_top5": int(top_5_asks[-1][1]) if top_5_asks else 0,
                "bid_volume_top5": int(top_5_bids[-1][1]) if top_5_bids else 0,

                # 使用三重兜底提取法，无惧 V社 隐藏字段
                "total_sell_orders": self._extract_total(raw_data, 'sell_order_summary', 'sell_order_count', 'sell_order_graph'),
                "total_buy_orders": self._extract_total(raw_data, 'buy_order_summary', 'buy_order_count', 'buy_order_graph')
            }
            return snapshot

        except Exception as e:
            logger.error(f"清洗订单簿数据时发生未知错误: {e} | ID: {item_nameid}")
            return {}

    def _extract_total(self, raw_data: Dict, summary_key: str, count_key: str, graph_key: str) -> int:
        """三重兜底提取总单量，兼容不同饰品类型（消耗品 vs 武器皮肤）"""

        # 1. 尝试直接获取数字键
        if count_key in raw_data and raw_data[count_key]:
            try:
                return int(str(raw_data[count_key]).replace(',', ''))
            except ValueError:
                pass

        # 2. 尝试从 HTML 摘要正则提取
        summary_html = raw_data.get(summary_key, '')
        if summary_html:
            clean_text = re.sub(r'<[^>]+>', '', str(summary_html))
            match = re.search(r'([\d,]+)', clean_text)
            if match:
                try:
                    return int(match.group(1).replace(',', ''))
                except ValueError:
                    pass

        # 3. 终极兜底：由于武器皮肤的 API 不返回 summary，
        # 我们直接取图表数组的最后一个节点的累计挂单量。
        # 这是量化分析中最可靠的“盘口可见深度”数据。
        graph = raw_data.get(graph_key, [])
        if graph and len(graph) > 0:
            try:
                # graph 的每一项是 [price, cumulative_volume, description]
                # 取最后一个元素的 cumulative_volume
                return int(graph[-1][1])
            except (IndexError, ValueError):
                pass

        return 0


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
    parts = date_str.strip().split()
    date_part = " ".join(parts[:3])
    t = time.strptime(date_part, "%b %d %Y")
    return float(calendar.timegm(t))


def fetch_item_history(
        item_name: str,
        appid: int = CS2_APP_ID,
        session: Optional[object] = None,
) -> ItemHistory:
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
        history.records.append(
            PriceRecord(
                timestamp=_parse_steam_date(entry[0]),
                price=float(entry[1]),
                volume=int(entry[2]),
            )
        )
    return history


def fetch_multiple_items(
        item_names: List[str],
        appid: int = CS2_APP_ID,
        delay: float = _REQUEST_DELAY,
) -> List[ItemHistory]:
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