"""
Steam Market business layer + backward-compatible fetcher module.
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


class MarketDataFetcher:
    API_URL = "https://steamcommunity.com/market/itemordershistogram"

    def __init__(self, http_client: SteamHttpClient) -> None:
        self._client = http_client

    def fetch_order_book(self, item_nameid: int, item_name: str = "") -> Optional[Dict]:
        params = {
            "item_nameid": item_nameid,
            "currency": 23,
            "country": "CN",
            "language": "schinese",
            "two_factor": 0,
        }

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

                # 调用改版后的聚合比对函数
                "total_sell_orders": self._extract_total(raw_data, 'sell_order_summary', 'sell_order_count', 'sell_order_graph'),
                "total_buy_orders": self._extract_total(raw_data, 'buy_order_summary', 'buy_order_count', 'buy_order_graph')
            }
            return snapshot

        except Exception as e:
            logger.error(f"清洗订单簿数据时发生未知错误: {e} | ID: {item_nameid}")
            return {}

    def _extract_total(self, raw_data: Dict, summary_key: str, count_key: str, graph_key: str) -> int:
        """核心修复：提取所有可能的值并取 max()，无视 Steam 喂过来的 0"""
        candidates = [0] # 设定最低底线为 0

        # 路线 1：解析直接返回的 count（可能为 0，没关系，先存起来）
        val = raw_data.get(count_key)
        if val is not None:
            try:
                candidates.append(int(str(val).replace(',', '')))
            except ValueError:
                pass

        # 路线 2：解析 HTML 文本
        summary_html = raw_data.get(summary_key, '')
        if summary_html:
            clean_text = re.sub(r'<[^>]+>', '', str(summary_html))
            match = re.search(r'([\d,]+)', clean_text)
            if match:
                try:
                    candidates.append(int(match.group(1).replace(',', '')))
                except ValueError:
                    pass

        # 路线 3：直接读取坐标图表里的最后一个累计挂单量
        graph = raw_data.get(graph_key, [])
        if isinstance(graph, list) and len(graph) > 0:
            try:
                candidates.append(int(graph[-1][1]))
            except (IndexError, ValueError):
                pass

        # 无论前面拿到了什么鬼东西，我们只要最大、最真实的那个数字
        return max(candidates)


# ---------------------------------------------------------------------------
# Legacy functions
# ---------------------------------------------------------------------------

_PRICE_HISTORY_URL = "https://steamcommunity.com/market/pricehistory/?appid={appid}&market_hash_name={name}"
_REQUEST_DELAY = 3.0

def _parse_steam_date(date_str: str) -> float:
    parts = date_str.strip().split()
    date_part = " ".join(parts[:3])
    t = time.strptime(date_part, "%b %d %Y")
    return float(calendar.timegm(t))

def fetch_item_history(item_name: str, appid: int = CS2_APP_ID, session: Optional[object] = None) -> ItemHistory:
    warnings.warn("deprecated", DeprecationWarning, stacklevel=2)
    if requests is None: raise ImportError("requests is required")
    url = _PRICE_HISTORY_URL.format(appid=appid, name=requests.utils.quote(item_name))
    sess = session or requests.Session()
    resp = sess.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    history = ItemHistory(item_name=item_name)
    for entry in data.get("prices", []):
        history.records.append(PriceRecord(timestamp=_parse_steam_date(entry[0]), price=float(entry[1]), volume=int(entry[2])))
    return history

def fetch_multiple_items(item_names: List[str], appid: int = CS2_APP_ID, delay: float = _REQUEST_DELAY) -> List[ItemHistory]:
    warnings.warn("deprecated", DeprecationWarning, stacklevel=2)
    results: List[ItemHistory] = []
    session = requests.Session() if requests else None
    for i, name in enumerate(item_names):
        if i > 0: time.sleep(delay)
        results.append(fetch_item_history(name, appid=appid, session=session))
    return results