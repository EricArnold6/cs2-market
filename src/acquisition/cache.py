"""Persistent JSON cache for CSQAQ item nameid lookups.

磁盘格式（v2）::

    {
      "id_map":   {"英文名": nameid, ...},
      "name_map": {"中文名": "英文名", ...}
    }

向后兼容旧格式（flat ``{str: int}`` 字典）：读取时自动迁移，下次 flush 后写成 v2 格式。
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from src.acquisition.exceptions import NameIdExtractionError, NameIdNotInitializedError  # noqa: F401

logger = logging.getLogger(__name__)

_CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "nameid_cache.json"


class _NameIdCache:
    """Persistent JSON cache mapping item names to CSQAQ ``good_id`` integers.

    内部维护两张表：

    * ``_id_map``   — ``{英文市场名: nameid}``，用于 API 调用
    * ``_name_map`` — ``{中文名: 英文名}``，用于中文配置 → 英文名转换
    """

    def __init__(self, cache_path: Path = _CACHE_FILE) -> None:
        self._path = cache_path
        self._id_map: dict[str, int] = {}
        self._name_map: dict[str, str] = {}
        self._lock = threading.Lock()
        self._load()

    # ------------------------------------------------------------------
    # 磁盘 I/O
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load cache from disk; silently ignore missing / corrupt files.

        兼容旧格式（``{str: int}`` flat dict）：自动迁移到 ``_id_map``。
        """
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if not isinstance(loaded, dict):
                return
            # v2 格式：含 id_map / name_map 键
            if "id_map" in loaded or "name_map" in loaded:
                raw_id = loaded.get("id_map", {})
                raw_nm = loaded.get("name_map", {})
                if isinstance(raw_id, dict):
                    self._id_map = {k: v for k, v in raw_id.items() if isinstance(v, int)}
                if isinstance(raw_nm, dict):
                    self._name_map = {k: v for k, v in raw_nm.items() if isinstance(v, str)}
            else:
                # v1 旧格式：flat {英文名: int}，全部放进 _id_map
                self._id_map = {k: v for k, v in loaded.items() if isinstance(v, int)}
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _flush(self) -> None:
        """原子写盘（v2 格式）；调用方必须持有 self._lock。"""
        tmp_path = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"id_map": self._id_map, "name_map": self._name_map}
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            logger.warning("Failed to flush nameid cache: %s", exc)

    # ------------------------------------------------------------------
    # 英文名接口（向后兼容原有调用方）
    # ------------------------------------------------------------------

    def get(self, item_name: str) -> Optional[int]:
        """用英文名查 nameid；cache miss 返回 ``None``。"""
        with self._lock:
            return self._id_map.get(item_name)

    def set(self, item_name: str, nameid: int) -> None:
        """存入英文名 → nameid 映射并立即刷盘。"""
        with self._lock:
            self._id_map[item_name] = nameid
            self._flush()

    # ------------------------------------------------------------------
    # 中文名接口（新增）
    # ------------------------------------------------------------------

    def get_english_name(self, chinese_name: str) -> Optional[str]:
        """通过中文名查对应英文名；未找到返回 ``None``。"""
        with self._lock:
            return self._name_map.get(chinese_name)

    def get_by_chinese(self, chinese_name: str) -> Optional[int]:
        """通过中文名查 nameid（中文→英文→id）；未找到返回 ``None``。"""
        with self._lock:
            english = self._name_map.get(chinese_name)
            if english is None:
                return None
            return self._id_map.get(english)

    def set_full(self, chinese_name: str, english_name: str, nameid: int) -> None:
        """同时写入 ``name_map[中文]=英文`` 和 ``id_map[英文]=id``，一次刷盘。"""
        with self._lock:
            self._name_map[chinese_name] = english_name
            self._id_map[english_name] = nameid
            self._flush()

    # ------------------------------------------------------------------
    # 批量注入（保留原有接口）
    # ------------------------------------------------------------------

    def load_from_dict(self, mapping: dict, *, overwrite: bool = False) -> int:
        """
        批量注入已知 name→nameid 映射，无需任何 HTTP 请求。

        参数
        ----
        mapping  : {英文item_name: nameid(正整数)} 字典
        overwrite: False（默认）保留已有缓存条目；True 强制覆盖

        返回
        ----
        实际写入磁盘的条目数（全部命中缓存时返回 0）

        异常
        ----
        TypeError  : nameid 不是 int
        ValueError : nameid 不是正整数
        """
        if not mapping:
            return 0
        # 全量校验，先于任何写操作（fail fast & clean）
        for name, nameid in mapping.items():
            if not isinstance(nameid, int):
                raise TypeError(f"nameid for {name!r} must be int, got {type(nameid).__name__!r}")
            if nameid <= 0:
                raise ValueError(f"nameid for {name!r} must be positive, got {nameid!r}")

        written = 0
        with self._lock:
            for name, nameid in mapping.items():
                if not overwrite and self._id_map.get(name) is not None:
                    continue
                self._id_map[name] = nameid
                written += 1
            if written:
                self._flush()   # 整批只写一次磁盘
        return written
