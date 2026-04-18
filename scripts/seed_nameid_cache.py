#!/usr/bin/env python3
"""
从本地饰品参考文件为 data/nameid_cache.json 填充 CSQAQ good_id。

直接从 data/饰品id（更新时间2026-01-23）.txt 中查找 config/settings.json
所定义的目标饰品，跳过 suggest API，无需任何网络请求。

用法
----
    python scripts/seed_nameid_cache.py              # 跳过已缓存的条目
    python scripts/seed_nameid_cache.py --overwrite  # 强制覆盖所有条目

退出码
------
    0 — 所有目标饰品均已写入缓存
    1 — 存在无法找到或写入的条目
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# ── sys.path 引导 ─────────────────────────────────────────────────────────────
# 在任何 src.* import 之前将项目根目录插入 sys.path，
# 确保无论从哪个工作目录运行脚本都能正确解析模块。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.acquisition.cache import _NameIdCache  # noqa: E402

# ── 日志 ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("seed_nameid_cache")

# ── 文件路径 ──────────────────────────────────────────────────────────────────
_TXT_PATH = _PROJECT_ROOT / "data" / "饰品id（更新时间2026-01-23）.txt"
_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.json"


def load_txt_index(txt_path: Path) -> dict[str, int]:
    """
    解析饰品参考 JSON 数组，返回 {market_hash_name: id} 索引。

    Raises
    ------
    FileNotFoundError : 文件不存在
    ValueError        : JSON 格式异常
    """
    logger.info("加载饰品参考文件: %s", txt_path)
    with open(txt_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if not isinstance(data, list):
        raise ValueError(f"期望 JSON 数组，实际得到 {type(data).__name__}")

    index: dict[str, int] = {}
    skipped = 0
    for i, entry in enumerate(data):
        mhn = entry.get("market_hash_name")
        eid = entry.get("id")
        if not mhn or eid is None:
            skipped += 1
            continue
        if not isinstance(eid, int):
            skipped += 1
            continue
        index[mhn] = eid

    logger.info("已加载 %d 条饰品记录（跳过 %d 条格式异常）", len(index), skipped)
    return index


def load_target_names(settings_path: Path) -> list[str]:
    """从 settings.json 读取目标饰品名称列表（target_items 的 values）。"""
    with open(settings_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    target_items = cfg.get("target_items", {})
    if isinstance(target_items, dict):
        return list(target_items.values())
    if isinstance(target_items, list):
        return target_items
    raise ValueError("settings.json['target_items'] 格式无效，必须为 dict 或 list")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="强制覆盖已有缓存条目（默认跳过）",
    )
    args = parser.parse_args()

    # ── 1. 加载参考文件 ───────────────────────────────────────────────────────
    try:
        txt_index = load_txt_index(_TXT_PATH)
    except FileNotFoundError:
        logger.error("找不到参考文件: %s", _TXT_PATH)
        logger.error("请确认已将 txt 文件移动至 data/ 目录")
        return 1
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("解析参考文件失败: %s", exc)
        return 1

    # ── 2. 加载目标饰品名称 ───────────────────────────────────────────────────
    try:
        target_names = load_target_names(_SETTINGS_PATH)
    except FileNotFoundError:
        logger.error("找不到配置文件: %s", _SETTINGS_PATH)
        return 1
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("解析配置文件失败: %s", exc)
        return 1

    logger.info("目标饰品数量: %d", len(target_names))

    # ── 3. 在参考文件中查找每个目标名称 ──────────────────────────────────────
    to_seed: dict[str, int] = {}
    not_found: list[str] = []

    for name in target_names:
        if name in txt_index:
            to_seed[name] = txt_index[name]
        else:
            not_found.append(name)

    if not_found:
        logger.warning(
            "以下 %d 个饰品在参考文件中未找到（可能需要手动处理）:\n  %s",
            len(not_found),
            "\n  ".join(not_found),
        )

    if not to_seed:
        logger.warning("没有找到任何可写入的条目")
        return 1 if not_found else 0

    # ── 4. 写入缓存 ───────────────────────────────────────────────────────────
    cache = _NameIdCache()  # 自动读取 data/nameid_cache.json

    # 提前统计已缓存条目，便于日志输出
    already_cached = [n for n in to_seed if cache.get(n) is not None]
    if already_cached and not args.overwrite:
        logger.info(
            "%d 个条目已在缓存中（将跳过）:\n  %s",
            len(already_cached),
            "\n  ".join(already_cached),
        )

    written = cache.load_from_dict(to_seed, overwrite=args.overwrite)
    logger.info(
        "缓存写入完成: 新增 %d 条，跳过（已有）%d 条，参考文件未找到 %d 条",
        written,
        len(to_seed) - written,
        len(not_found),
    )

    # ── 5. 最终校验 ───────────────────────────────────────────────────────────
    missing_from_cache = [n for n in target_names if cache.get(n) is None]
    if missing_from_cache:
        logger.error(
            "写入后仍有 %d 个目标饰品不在缓存中:\n  %s",
            len(missing_from_cache),
            "\n  ".join(missing_from_cache),
        )
        return 1

    logger.info("成功：所有 %d 个目标饰品均已写入缓存。", len(target_names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
