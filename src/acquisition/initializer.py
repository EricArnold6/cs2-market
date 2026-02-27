"""Batch pre-resolver for Steam item_nameid values."""

import logging
import random
import time
from dataclasses import dataclass, field
from typing import List

from src.acquisition.http_client import (
    SteamOrderBookFetcher,
    _HTML_FETCH_DELAY_MIN_S,
    _HTML_FETCH_DELAY_MAX_S,
)

logger = logging.getLogger(__name__)


@dataclass
class InitResult:
    """Result of a NameIdInitializer.run() call."""
    resolved:   List[str] = field(default_factory=list)
    from_cache: List[str] = field(default_factory=list)
    failed:     dict      = field(default_factory=dict)  # name → Exception

    @property
    def all_succeeded(self) -> bool:
        return len(self.failed) == 0

    def __str__(self) -> str:
        return (f"InitResult(resolved={len(self.resolved)}, "
                f"cache_hits={len(self.from_cache)}, failed={len(self.failed)})")


class NameIdInitializer:
    """
    批量预解析饰品 item_nameid 的初始化工具。

    - 缓存命中 → 直接跳过，无 HTTP，无延迟
    - 缓存未命中 → 请求 HTML 页面，间隔 5-10s
    - 单个失败 → 收集到 result.failed，不中止整批

    典型用法：
        init = NameIdInitializer(fetcher)
        result = init.run(["AK-47 | Redline (Field-Tested)", ...])
        if not result.all_succeeded:
            raise RuntimeError(f"Init failed: {list(result.failed)}")
        # 此后可安全启动轮询循环
    """

    def __init__(
        self,
        fetcher: SteamOrderBookFetcher,
        delay_min_s: float = _HTML_FETCH_DELAY_MIN_S,
        delay_max_s: float = _HTML_FETCH_DELAY_MAX_S,
    ) -> None:
        self._fetcher   = fetcher
        self._delay_min = delay_min_s
        self._delay_max = delay_max_s

    def run(self, item_names: List[str], *, skip_cached: bool = True) -> InitResult:
        """
        参数
        ----
        item_names  : 需要解析的饰品名列表
        skip_cached : True（默认）跳过已缓存条目；False 强制重新抓取

        返回 InitResult（不抛出，失败收集在 result.failed）
        """
        result = InitResult()
        to_fetch = []

        for name in item_names:
            if skip_cached and self._fetcher._cache.get(name) is not None:
                result.from_cache.append(name)
            else:
                to_fetch.append(name)

        logger.info("NameIdInitializer: %d cache hits, %d to fetch",
                    len(result.from_cache), len(to_fetch))

        for idx, name in enumerate(to_fetch):
            try:
                self._fetcher.resolve_item_nameid(name)
                result.resolved.append(name)
                logger.info("resolved [%d/%d]: %r", idx + 1, len(to_fetch), name)
            except Exception as exc:
                result.failed[name] = exc
                logger.warning("failed to resolve %r: %s", name, exc)

            # 最后一个条目之后不等待（下一步是启动轮询，不是再次请求 HTML）
            if idx < len(to_fetch) - 1:
                delay = random.uniform(self._delay_min, self._delay_max)
                logger.debug("inter-HTML delay: %.1fs", delay)
                time.sleep(delay)

        logger.info("NameIdInitializer complete: %s", result)
        return result
