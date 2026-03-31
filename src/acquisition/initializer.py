"""Batch pre-resolver for CSQAQ good_ids using suggest API."""

import logging
from dataclasses import dataclass, field
from typing import List

from src.acquisition.csqaq_client import CSQAQClient

logger = logging.getLogger(__name__)


@dataclass
class InitResult:
    """Result of a NameIdInitializer.run() call."""
    resolved:   List[str] = field(default_factory=list)
    from_cache: List[str] = field(default_factory=list)
    failed:     dict      = field(default_factory=dict)

    @property
    def all_succeeded(self) -> bool:
        return len(self.failed) == 0

    def __str__(self) -> str:
        return (f"InitResult(resolved={len(self.resolved)}, "
                f"cache_hits={len(self.from_cache)}, failed={len(self.failed)})")


class NameIdInitializer:
    """批量联想查询并缓存饰品的 CSQAQ 专属 good_id。"""

    def __init__(self, client: CSQAQClient) -> None:
        self._client = client

    def run(self, item_names: List[str], *, skip_cached: bool = True) -> InitResult:
        result = InitResult()
        to_fetch = []

        for name in item_names:
            if skip_cached and self._client.cache.get(name) is not None:
                result.from_cache.append(name)
            else:
                to_fetch.append(name)

        logger.info("NameIdInitializer: %d cache hits, %d to fetch via Suggest API",
                    len(result.from_cache), len(to_fetch))

        for idx, name in enumerate(to_fetch):
            try:
                self._client.resolve_item_nameid(name)
                result.resolved.append(name)
                logger.info("Resolved CSQAQ ID [%d/%d]: %r", idx + 1, len(to_fetch), name)
            except Exception as exc:
                result.failed[name] = exc
                logger.warning("Failed to resolve ID for %r: %s", name, exc)

        logger.info("NameIdInitializer complete: %s", result)
        return result