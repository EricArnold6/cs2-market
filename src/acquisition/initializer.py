"""Batch pre-resolver for CSQAQ good_ids.

解析顺序（每个 name 视为中文名）：
1. cache hit  → 直接用
2. cache miss → 查本地 TXT 文件（离线）
3. 仍未找到  → fallback 到 CSQAQ /search/suggest HTTP API
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


def _load_txt_index(txt_path: Path) -> Dict[str, Tuple[str, int]]:
    """解析本地饰品 ID 文件，返回 ``{中文名: (market_hash_name, id)}`` 索引。

    文件格式为 JSON 数组::

        [{"id": 1, "name": "中文名", "market_hash_name": "英文名"}, ...]

    解析失败时记录警告并返回空字典（不抛异常，允许程序继续运行）。
    """
    if not txt_path.exists():
        logger.warning("Local TXT index not found: %s", txt_path)
        return {}
    try:
        with open(txt_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            logger.warning("TXT index is not a JSON array: %s", txt_path)
            return {}
        index: Dict[str, Tuple[str, int]] = {}
        for entry in data:
            cn = entry.get("name", "")
            en = entry.get("market_hash_name", "")
            item_id = entry.get("id")
            if cn and en and isinstance(item_id, int) and item_id > 0:
                index[cn] = (en, item_id)
        logger.info("Loaded %d entries from local TXT index: %s", len(index), txt_path)
        return index
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load TXT index %s: %s", txt_path, exc)
        return {}


class NameIdInitializer:
    """批量解析并缓存饰品的 CSQAQ 专属 good_id。

    解析顺序（name 为中文名）：
    1. cache hit（中文名已有映射）→ 直接用
    2. cache miss → 查本地 TXT 文件 → 命中则写 cache
    3. 仍未找到 → HTTP fallback（CSQAQ /search/suggest）
    """

    def __init__(self, client: CSQAQClient) -> None:
        self._client = client

    def run(
        self,
        item_names: List[str],
        *,
        skip_cached: bool = True,
        txt_path: Optional[Path] = None,
    ) -> InitResult:
        """解析所有 *item_names* 的 CSQAQ good_id。

        参数
        ----
        item_names  : 中文名列表（来自 settings.json ``target_items``）
        skip_cached : True（默认）跳过已有缓存的条目
        txt_path    : 本地饰品 ID 文件路径；None 则跳过本地查找步骤
        """
        result = InitResult()
        to_fetch: List[str] = []

        # Step 1：cache 命中检查
        for name in item_names:
            if skip_cached and self._client.cache.get_by_chinese(name) is not None:
                result.from_cache.append(name)
            else:
                to_fetch.append(name)

        logger.info(
            "NameIdInitializer: %d cache hits, %d to resolve",
            len(result.from_cache), len(to_fetch),
        )

        if not to_fetch:
            logger.info("NameIdInitializer complete: %s", result)
            return result

        # Step 2：本地 TXT 离线查找
        txt_index: Dict[str, Tuple[str, int]] = {}
        if txt_path is not None:
            txt_index = _load_txt_index(txt_path)

        still_missing: List[str] = []
        for idx, name in enumerate(to_fetch):
            if name in txt_index:
                english_name, item_id = txt_index[name]
                self._client.cache.set_full(name, english_name, item_id)
                result.resolved.append(name)
                logger.info(
                    "Resolved from local TXT [%d/%d]: %r → %r (id=%d)",
                    idx + 1, len(to_fetch), name, english_name, item_id,
                )
            else:
                still_missing.append(name)

        # Step 3：HTTP fallback（中文名直接搜索 CSQAQ suggest API）
        if still_missing:
            logger.info(
                "%d item(s) not found in TXT, falling back to Suggest API",
                len(still_missing),
            )
        for idx, name in enumerate(still_missing):
            try:
                self._client.resolve_item_nameid(name)
                result.resolved.append(name)
                logger.info(
                    "Resolved via HTTP API [%d/%d]: %r",
                    idx + 1, len(still_missing), name,
                )
            except Exception as exc:
                result.failed[name] = exc
                logger.warning("Failed to resolve ID for %r: %s", name, exc)

        logger.info("NameIdInitializer complete: %s", result)
        return result