"""CSQAQ REST API Client for market data acquisition."""

import logging
import time
from typing import List, Optional

import requests

from src.schemas.market import OrderBookSnapshot
from src.acquisition.cache import _NameIdCache
from src.acquisition.exceptions import NameIdExtractionError

logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL_PUBLIC = "https://api.csqaq.com/api/v1"
_DEFAULT_BASE_URL_VIP    = "https://private-api.csqaq.com/api/v1"


class CSQAQClient:
    """High-performance REST client for CSQAQ Data API."""

    def __init__(
        self,
        api_token: str,
        vip_token: str = "",
        cache: Optional[_NameIdCache] = None,
        base_url_public: str = _DEFAULT_BASE_URL_PUBLIC,
        base_url_vip: str = _DEFAULT_BASE_URL_VIP,
    ) -> None:
        """
        初始化客户端
        :param api_token:       CSQAQ 普通接口 ApiToken
        :param vip_token:       CSQAQ VIP 接口 ApiToken
        :param base_url_public: 公共 API 域名（默认从 settings.json 注入）
        :param base_url_vip:    VIP API 域名（默认从 settings.json 注入）
        """
        self.BASE_URL_PUBLIC = base_url_public
        self.BASE_URL_VIP    = base_url_vip
        self._cache = cache if cache is not None else _NameIdCache()

        self._session = requests.Session()

        # 普通接口请求头
        self._headers_public = {
            "ApiToken": api_token,
            "Content-Type": "application/json"
        }
        # VIP 接口请求头
        self._headers_vip = {
            "ApiToken": vip_token,
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

        url = f"{self.BASE_URL_PUBLIC}/search/suggest"
        params = {"text": item_name}

        try:
            time.sleep(1.1)  # respect 1 req/s rate limit
            for _attempt in range(3):
                resp = self._session.get(url, params=params, headers=self._headers_public, timeout=10)
                if resp.status_code == 429:
                    logger.warning("resolve_item_nameid: 429 rate-limited, retrying in 2s (attempt %d/3)", _attempt + 1)
                    time.sleep(2.0)
                    continue
                break
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

        url = f"{self.BASE_URL_PUBLIC}/goods/getPriceByMarketHashName"
        snapshots = []

        chunk_size = 50
        for idx, i in enumerate(range(0, len(market_hash_names), chunk_size)):
            if idx > 0:
                time.sleep(1.1)  # respect 1 req/s rate limit between chunks
            chunk = market_hash_names[i:i + chunk_size]
            payload = {"marketHashNameList": chunk}

            try:
                for _attempt in range(3):
                    resp = self._session.post(url, json=payload, headers=self._headers_public, timeout=15)
                    if resp.status_code == 429:
                        logger.warning("fetch_batch_prices: 429 rate-limited, retrying in 2s (attempt %d/3)", _attempt + 1)
                        time.sleep(2.0)
                        continue
                    break
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

    def fetch_kline_data(self, csqaq_id: int, periods: str = "1hour") -> list[dict]:
        """
        [POST] /info/simple/chartAll
        获取单件饰品K线数据，用于触发异常后的"二级量价真实性确认"。

        :param csqaq_id: 饰品的 CSQAQ ID
        :param periods: K线周期 (推荐 "1hour" 或 "4hour")
        """
        url = f"{self.BASE_URL_VIP}/info/simple/chartAll"

        # 构造当前时间的 13 位毫秒时间戳作为 max_time
        current_ms = int(time.time() * 1000)

        payload = {
            "good_id": str(csqaq_id),  # API 示例中传入的是字符串
            "plat": 1,                 # 1 代表获取 BUFF 平台的真实成交记录
            "periods": periods,
            "max_time": current_ms
        }

        try:
            resp = self._session.post(url, json=payload, headers=self._headers_vip, timeout=10)
            resp.raise_for_status()
            res_json = resp.json()

            if res_json.get("code") != 200:
                logger.error("K-Line API Error for ID %s: %s", csqaq_id, res_json.get("msg"))
                return []

            return res_json.get("data", [])
        except Exception as exc:
            logger.error("Failed to fetch K-Line for ID %s: %s", csqaq_id, exc)
            return []

    def fetch_whale_ranking(self, csqaq_id: int, limit: int = 10) -> list[dict]:
        """
        [POST] /api/v1/monitor/rank
        获取指定饰品的持仓大户排行榜。
        """
        url = f"{self.BASE_URL}/monitor/rank"
        payload = {"good_id": str(csqaq_id)}

        try:
            resp = self._session.post(url, json=payload, headers=self._headers, timeout=10)
            resp.raise_for_status()
            res_json = resp.json()

            if res_json.get("code") != 200:
                logger.error("Whale Rank API Error for ID %s: %s", csqaq_id, res_json.get("msg"))
                return []

            data = res_json.get("data", [])
            # 按照持有量 (num) 降序排序，取前 limit 名大户
            sorted_whales = sorted(data, key=lambda x: x.get("num", 0), reverse=True)
            return sorted_whales[:limit]

        except Exception as exc:
            logger.error("Failed to fetch Whale Ranking for ID %s: %s", csqaq_id, exc)
            return []

    def fetch_user_inventory_dynamics(self, task_id: int, target_good_id: int) -> dict:
        """
        [POST] /api/v1/task/get_task_business
        获取单个大户近期的库存动态记录，并统计目标饰品的净流入/流出量。
        """
        url = f"{self.BASE_URL}/task/get_task_business"
        payload = {
            "page_index": 1,
            "page_size": 50,  # 获取最近 50 条动态
            "task_id": task_id,
            "type": "ALL"
        }

        try:
            resp = self._session.post(url, json=payload, headers=self._headers, timeout=10)
            resp.raise_for_status()
            res_json = resp.json()

            if res_json.get("code") != 200:
                return {"net_change": 0, "active_volume": 0}

            trades = res_json.get("data", {}).get("trades", [])

            net_change = 0
            active_volume = 0

            for trade in trades:
                # 只关心我们要狙击的那把枪
                if str(trade.get("good_id")) == str(target_good_id):
                    count = int(trade.get("count", 0))
                    trade_type = trade.get("type")

                    # ==========================================
                    # V4.0 官方枚举逻辑 (筹码真实流向判定)
                    # ==========================================
                    # 4: 取出组件 (锁死筹码离开市场，庄家吸筹)
                    # 7: 卖出/存入组件 (推向市场准备套现，庄家抛售)
                    # 0(默认) 和 5(CD恢复) 作为无实质转移被忽略

                    if trade_type == 4:
                        net_change += count  # 净流入增加 (看涨)
                    elif trade_type == 7:
                        net_change -= count  # 净流入减少 (看跌)

                    # 只要是 4 或 7，都算作有效活跃度
                    if trade_type in (4, 7):
                        active_volume += count

            return {"net_change": net_change, "active_volume": active_volume}

        except Exception as exc:
            logger.error("Failed to fetch dynamics for task_id %s: %s", task_id, exc)
            return {"net_change": 0, "active_volume": 0}