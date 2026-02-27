"""Persistent JSON cache for Steam item_nameid lookups."""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from src.acquisition.exceptions import NameIdExtractionError, NameIdNotInitializedError  # noqa: F401

logger = logging.getLogger(__name__)

_CACHE_FILE = Path(__file__).parent / "nameid_cache.json"


class _NameIdCache:
    """Persistent JSON cache mapping item names to Steam ``item_nameid`` integers."""

    def __init__(self, cache_path: Path = _CACHE_FILE) -> None:
        self._path = cache_path
        self._data: dict = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        """Load cache from disk; silently ignore missing / corrupt files."""
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self._data = loaded
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def get(self, item_name: str) -> Optional[int]:
        """Return cached nameid, or ``None`` on a cache miss."""
        with self._lock:
            return self._data.get(item_name)

    def set(self, item_name: str, nameid: int) -> None:
        """Store *nameid* for *item_name* and flush to disk immediately."""
        with self._lock:
            self._data[item_name] = nameid
            self._flush()

    def _flush(self) -> None:
        """原子写盘；调用方必须持有 self._lock。"""
        tmp_path = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            logger.warning("Failed to flush nameid cache: %s", exc)

    def load_from_dict(self, mapping: dict, *, overwrite: bool = False) -> int:
        """
        批量注入已知 name→nameid 映射，无需任何 HTTP 请求。

        参数
        ----
        mapping  : {item_name: nameid(正整数)} 字典
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
                if not overwrite and self._data.get(name) is not None:
                    continue
                self._data[name] = nameid
                written += 1
            if written:
                self._flush()   # 整批只写一次磁盘
        return written
