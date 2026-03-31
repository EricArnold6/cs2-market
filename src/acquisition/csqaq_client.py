"""CSQAQ REST API Client for market data acquisition."""

import logging
import time
from typing import List, Optional

import requests

from src.schemas.market import OrderBookSnapshot
from src.acquisition.cache import _NameIdCache
from src.acquisition.exceptions import NameIdExtractionError

logger = logging.getLogger(__name__)


class CSQAQClient:
    """High-performance REST client for CSQAQ Data API."""

    BASE_URL = "https://api.csqaq.com/api/v1"

    def __init__(self, api_token: str, cache: Optional[_NameIdCache] = None) -> None:
        """
        初始化客户端
        :param api_token: CSQAQ 提供的 ApiToken
        """
        self._api_token = api_token
        self._cache = cache if cache is not None else _NameIdCache()

        self._session = requests.Session()

        # 显式定义字典，避开 requests.Session 的首字母大写强转逻辑
        self._headers = {
            "ApiToken": self._api_token,
            "Content-Type": "application/json"
        }

    @property
    def cache(self) -> _NameIdCache:
        return self._cache

    def resolve_item_nameid(self, item_name: str) -> int:
        """
        [GET] /search/suggest
        调用 CSQAQ 的联想查询接口，获取饰品的库表 good_id。
        """
        cached = self._cache.get(item_name)
        if cached is not None:
            return cached

        url = f"{self.BASE_URL}/search/suggest"
        params = {"text": item_name}

        try:
            # 修复点：强制在此处携带 self._headers
            resp = self._session.get(url, params=params, headers=self._headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 200:
                raise ValueError(f"API Error: {data.get('msg')}")

            results = data.get("data", [])
            if not results:
                raise ValueError("No results found in suggest API.")

            nameid = int(results[0]["id"])

            self._cache.set(item_name, nameid)
            logger.debug("Resolved CSQAQ ID for %r: %d", item_name, nameid)
            return nameid

        except Exception as exc:
            raise NameIdExtractionError(f"Failed to resolve ID for {item_name!r}: {exc}") from exc

    def fetch_batch_prices(self, market_hash_names: List[str]) -> List[OrderBookSnapshot]:
        """
        [POST] /goods/getPriceByMarketHashName
        批量获取饰品出售价格数据。自动处理 API 的长度 ≤ 50 限制。
        """
        if not market_hash_names:
            return []

        url = f"{self.BASE_URL}/goods/getPriceByMarketHashName"
        snapshots = []

        chunk_size = 50
        for i in range(0, len(market_hash_names), chunk_size):
            chunk = market_hash_names[i:i + chunk_size]
            payload = {"marketHashNameList": chunk}

            try:
                # 修复点：强制在此处携带 self._headers
                resp = self._session.post(url, json=payload, headers=self._headers, timeout=15)
                resp.raise_for_status()
                res_json = resp.json()

                if res_json.get("code") != 200:
                    logger.error("CSQAQ Batch API Error: %s", res_json.get("msg"))
                    continue

                success_data = res_json.get("data", {}).get("success", {})
                for name, item in success_data.items():
                    snapshots.append(OrderBookSnapshot(
                        item_name=item.get("marketHashName", name),
                        timestamp=time.time(),
                        lowest_ask_price=float(item.get("buffSellPrice", 0.0)),
                        highest_bid_price=float(item.get("buffBuyPrice", 0.0)),
                        total_sell_orders=int(item.get("buffSellNum", 0)),
                        total_buy_orders=int(item.get("buffBuyNum", 0)),
                        # --- 注入新数据 ---
                        yyyp_sell_price=float(item.get("yyypSellPrice", 0.0)),
                        yyyp_lease_price=float(item.get("yyypLeasePrice", 0.0))
                    ))

                error_list = res_json.get("data", {}).get("error", [])
                if error_list:
                    logger.warning("CSQAQ API could not fetch data for: %s", error_list)

            except Exception as exc:
                logger.error("Failed to fetch batch prices for chunk: %s", exc)

        return snapshots