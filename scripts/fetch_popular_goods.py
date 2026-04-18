#!/usr/bin/env python3
"""
从 CSQAQ VIP 接口获取全量饰品热度排名，并更新 target_items。

功能
----
1. 调用 POST https://private-api.csqaq.com/api/v1/info/get_popular_goods
2. 将完整响应保存到 data/popular_goods_<timestamp>.json
3. 按 rank_num（热度排名，数字越小越热）升序排列，取前 N 名（默认 100）
4. 将这些饰品的中文名去重后写入 config/settings.json 的 target_items

用法
----
    python scripts/fetch_popular_goods.py              # 取前 100，追加去重
    python scripts/fetch_popular_goods.py --top 50     # 取前 50
    python scripts/fetch_popular_goods.py --overwrite  # 覆盖 target_items（不保留原有条目）

退出码
------
    0 — 成功
    1 — 网络错误或响应异常
"""

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ── 项目根目录 ────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.json"
_DATA_DIR = _PROJECT_ROOT / "data"

# ── API 配置 ──────────────────────────────────────────────────────────────────
_API_URL = "https://private-api.csqaq.com/api/v1/info/get_popular_goods"

# ── 日志 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("fetch_popular_goods")


def load_api_token(settings_path: Path) -> str:
    """从 settings.json 读取 csqaq.vip_token。"""
    with open(settings_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    token = cfg.get("csqaq", {}).get("vip_token", "")
    if not token:
        raise ValueError("settings.json 中未找到 csqaq.vip_token")
    return token


def fetch_popular_goods(api_token: str) -> list[dict]:
    """
    调用接口，返回原始 data 列表。

    每条记录字段示例：
      {"id": 1, "name": "运动手套（★）...", "market_hash_name": "...",
       "rank_num": 304, "rank_num_change": 144, "turnover_number": 0}
    """
    req = urllib.request.Request(
        _API_URL,
        method="POST",
        headers={"ApiToken": api_token},
    )
    logger.info("正在请求: %s", _API_URL)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络请求失败: {exc}") from exc

    payload = json.loads(body)
    if payload.get("code") != 200:
        raise RuntimeError(f"接口返回错误: code={payload.get('code')}, msg={payload.get('msg')}")

    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError(f"响应 data 字段格式异常: {type(data)}")

    logger.info("接口返回 %d 条记录", len(data))
    return data


def save_raw(data: list[dict]) -> Path:
    """将原始数据保存到 data/popular_goods_<timestamp>.json，返回保存路径。"""
    _DATA_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = _DATA_DIR / f"popular_goods_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    logger.info("原始数据已保存: %s", out_path)
    return out_path


def top_items_by_rank(data: list[dict], top_n: int) -> list[str]:
    """
    按 rank_num 升序（数字越小热度越高）取前 top_n 条，返回中文名列表。
    过滤掉 name 为空或 rank_num 为 0 的记录。
    """
    valid = [d for d in data if d.get("name") and d.get("rank_num", 0) > 0]
    sorted_items = sorted(valid, key=lambda d: d["rank_num"])
    top = sorted_items[:top_n]
    logger.info(
        "热度 Top%d 范围: rank_num %d ~ %d",
        top_n,
        top[0]["rank_num"] if top else 0,
        top[-1]["rank_num"] if top else 0,
    )
    return [item["name"] for item in top]


def update_settings(settings_path: Path, new_names: list[str], overwrite: bool) -> None:
    """将 new_names 写入 settings.json 的 target_items（去重）。"""
    with open(settings_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    existing: list[str] = cfg.get("target_items", [])
    if not isinstance(existing, list):
        existing = []

    if overwrite:
        # 完全替换，但仍保持 new_names 内部去重
        seen: set[str] = set()
        merged = []
        for name in new_names:
            if name not in seen:
                seen.add(name)
                merged.append(name)
        logger.info("覆盖模式: target_items 更新为 %d 个饰品", len(merged))
    else:
        # 追加模式：保留原有条目，追加 new_names 中的新条目
        seen = set(existing)
        merged = list(existing)
        added = 0
        for name in new_names:
            if name not in seen:
                seen.add(name)
                merged.append(name)
                added += 1
        logger.info(
            "追加模式: 原有 %d 个，新增 %d 个，合并后共 %d 个",
            len(existing), added, len(merged),
        )

    cfg["target_items"] = merged
    with open(settings_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=4)
    logger.info("settings.json 已更新")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--top",
        type=int,
        default=100,
        metavar="N",
        help="取热度前 N 名饰品写入 target_items（默认 100）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖 target_items（默认为追加去重模式）",
    )
    args = parser.parse_args()

    # 1. 读取 token
    try:
        token = load_api_token(_SETTINGS_PATH)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("读取 API Token 失败: %s", exc)
        return 1

    # 2. 调用接口
    try:
        raw_data = fetch_popular_goods(token)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    # 3. 保存原始数据
    save_raw(raw_data)

    # 4. 提取 Top N
    top_names = top_items_by_rank(raw_data, args.top)
    if not top_names:
        logger.error("未能从接口数据中提取有效饰品名称")
        return 1

    # 5. 更新 settings.json
    update_settings(_SETTINGS_PATH, top_names, overwrite=args.overwrite)

    return 0


if __name__ == "__main__":
    sys.exit(main())