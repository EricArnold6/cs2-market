"""QuantOrchestrator — CS2 market monitoring daemon (CSQAQ Batch Version).

Entry point for the full data pipeline:
    fetch(Batch) → store → detect anomalies (w/ Whale Tracking) → alert

Usage
-----
    python main.py

Configuration is read from ``config/settings.json``.
Set real credentials before running.
"""

import json
import logging
import sys
import time
from pathlib import Path

import psycopg2

# ---------------------------------------------------------------------------
# Logging setup — must happen before any src.* imports that use loggers
# ---------------------------------------------------------------------------

_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_DIR / "orchestrator.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("orchestrator")

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------

from src.acquisition.csqaq_client import CSQAQClient
from src.acquisition.initializer import NameIdInitializer
from src.alerting import DingTalkAlerter, AlertDispatcher
from src.analysis.anomaly.detector import MarketAnomalyDetector
from src.storage.database import DatabaseConnection
from src.storage.repository import OrderBookRepository

# ---------------------------------------------------------------------------
# Config path
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path("config") / "settings.json"


# ---------------------------------------------------------------------------
# QuantOrchestrator
# ---------------------------------------------------------------------------

class QuantOrchestrator:
    """End-to-end orchestrator using CSQAQ Batch API."""

    def __init__(self, config_path: Path = _CONFIG_PATH) -> None:
        # load config
        with config_path.open(encoding="utf-8") as fh:
            cfg = json.load(fh)

        self._db_config: dict = cfg["database"]
        self._system_cfg: dict = cfg["system"]

        # target_items 现在是中文名列表，如 ["M4A4 | 地狱烈火 (久经沙场)", ...]
        self._target_items: list[str] = cfg["target_items"]

        # Acquisition (接入 CSQAQ)
        csqaq_cfg = cfg.get("csqaq", {})
        api_token = csqaq_cfg.get("api_token", "")
        vip_token = csqaq_cfg.get("vip_token", "")
        self._client = CSQAQClient(
            api_token=api_token,
            vip_token=vip_token,
            base_url_public=csqaq_cfg.get("base_url_public", ""),
            base_url_vip=csqaq_cfg.get("base_url_vip", ""),
        )
        self._initializer = NameIdInitializer(self._client)

        # Storage
        self._db = DatabaseConnection(self._db_config)
        self._repo: OrderBookRepository | None = None

        # Anomaly detection
        # ⚠️ 架构升级：将 API 客户端注入 Detector，让 Detector 原生拥有 K线和巨鲸的调用能力
        self._detector = MarketAnomalyDetector(self._db_config, self._client)

        # Alerting
        dt_cfg = cfg["dingtalk"]
        self._alerter = DingTalkAlerter(
            webhook_url=dt_cfg["webhook_url"],
            secret=dt_cfg.get("secret"),
        )
        self._dispatcher = AlertDispatcher(self._alerter)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def startup(self) -> None:
        """Resolve CSQAQ good_ids, open DB, send startup notification."""
        logger.info("=== QuantOrchestrator (CSQAQ Version) starting up ===")

        # Phase 1 — 优先从本地 TXT 离线文件解析中文名对应的 CSQAQ ID，再 fallback HTTP
        _TXT_PATH = Path("data") / "饰品id（更新时间2026-01-23）.txt"
        logger.info("Resolving CSQAQ IDs for %d item(s)…", len(self._target_items))
        result = self._initializer.run(self._target_items, txt_path=_TXT_PATH)
        logger.info("NameId init: %s", result)
        if not result.all_succeeded:
            failed_names = list(result.failed.keys())
            logger.warning("Some items failed to resolve ID: %s", failed_names)

        # Phase 2 — open DB connection
        self._db.connect()
        self._repo = OrderBookRepository(self._db.connection)
        logger.info("Database connection established.")

        # Phase 3 — register all items in metadata table
        for chinese_name in self._target_items:
            english_name = self._client.cache.get_english_name(chinese_name)
            if english_name is None:
                logger.warning("No English name cached for %r, skipping metadata init", chinese_name)
                continue
            csqaq_id = self._client.cache.get(english_name)
            if csqaq_id is not None:
                try:
                    self._repo.init_item_metadata(csqaq_id, english_name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Could not register item metadata for %r: %s", english_name, exc
                    )

        logger.info("Startup complete.")

    def shutdown(self) -> None:
        """Send shutdown notification and close DB."""
        logger.info("Shutting down…")
        try:
            logger.info("CS2 Market Monitor 已停止 (System stopped)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not send shutdown notification: %s", exc)
        self._db.close()
        logger.info("Database connection closed. Goodbye.")

    def run_forever(self) -> None:
        """Main loop — fetches all items in one batch, then sleeps."""
        scan_interval_s = self._system_cfg.get("scan_interval_minutes", 3) * 60

        logger.info(
            "Entering batch scan loop: %d item(s), interval=%ds",
            len(self._target_items),
            scan_interval_s,
        )

        while True:
            try:
                self._scan_all_items_batch()
            except psycopg2.Error as db_exc:
                logger.exception("Database error occurred during scan: %s", db_exc)
                self._send_alert_safe(f"⚠️ 数据库异常，正在尝试自愈：{db_exc}")
                self._recover_database()
                time.sleep(60)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected error in scan cycle: %s", exc)
                self._send_alert_safe(f"⚠️ API 或未知错误，60秒后重试：{exc}")
                time.sleep(60)
            else:
                logger.info("Batch scan cycle complete. Sleeping %ds…", scan_interval_s)
                time.sleep(scan_interval_s)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_english_names(self) -> list[str]:
        """将中文名列表转换为英文名列表（通过 name_map cache）。"""
        english_names = []
        for chinese_name in self._target_items:
            english = self._client.cache.get_english_name(chinese_name)
            if english is not None:
                english_names.append(english)
            else:
                logger.warning("Cannot resolve English name for %r, skipping in this cycle", chinese_name)
        return english_names

    def _scan_all_items_batch(self) -> None:
        """Run one full scan cycle with Market Breadth Risk Control."""
        logger.info("Fetching batch prices for %d items...", len(self._target_items))

        english_names = self._get_english_names()
        snapshots = self._client.fetch_batch_prices(english_names)
        if not snapshots:
            logger.warning("No snapshots returned from API in this cycle.")
            return

        pending_alerts = []
        market_spreads = []

        # ==========================================
        # 阶段 1：入库与异常检测 (完全委托给 Detector)
        # ==========================================
        for snap in snapshots:
            csqaq_id = self._client.cache.get(snap.item_name)
            if not csqaq_id:
                continue

            try:
                self._repo.insert_snapshot(snap, csqaq_id)
            except psycopg2.Error:
                raise  # 触发自愈
            except Exception as exc:
                logger.error("Failed to store snapshot for %r: %s", snap.item_name, exc)
                continue

            try:
                # Detector 内部已集成 K线 与 巨鲸追踪，直接输出终极定性信号
                result = self._detector.detect_anomalies(csqaq_id)
                if result is not None:
                    market_spreads.append(result.get("spread_ratio", 0.0))
                    # 原封不动推入发送队列
                    pending_alerts.append((snap.item_name, result))
            except Exception as exc:
                logger.error("Anomaly detection failed for %r: %s", snap.item_name, exc)

        # ==========================================
        # 阶段 2：计算大盘情绪与熔断 (Market Risk Control)
        # ==========================================
        if market_spreads:
            avg_spread = sum(market_spreads) / len(market_spreads)
            logger.info("Market Breadth (Avg Spread): %.2f%%", avg_spread * 100)

            # 熔断条件：监控池平均跌幅超过 1.5%
            is_crashing = avg_spread < -0.015
            self._dispatcher.update_market_status(is_crashing)

        # ==========================================
        # 阶段 3：分发告警 (Dispatch)
        # ==========================================
        for item_name, result in pending_alerts:
            self._dispatcher.dispatch(item_name, result)

    def _recover_database(self) -> None:
        """数据库断线自愈。"""
        logger.info("Attempting to re-establish database connection...")
        try:
            self._db.close()
            self._db.connect()
            self._repo = OrderBookRepository(self._db.connection)
            logger.info("Database connection successfully recovered.")
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to recover database connection: %s", exc)

    def _send_alert_safe(self, message: str) -> None:
        """安全发送系统告警。"""
        try:
            self._alerter.send_text(message)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send alert. Message: %r, Error: %s", message, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    orchestrator = QuantOrchestrator()
    try:
        orchestrator.startup()
        orchestrator.run_forever()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received.")
        orchestrator.shutdown()
    except Exception as exc:
        logger.exception("Unhandled exception in main: %s", exc)
        orchestrator.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()