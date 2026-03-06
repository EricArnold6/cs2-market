"""QuantOrchestrator — CS2 market monitoring daemon.

Entry point for the full data pipeline:
    fetch → store → detect anomalies → alert

Usage
-----
    python main.py

Configuration is read from ``config/settings.json``.
Set real credentials before running.
"""

import json
import logging
import random
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

from src.acquisition.http_client import SteamOrderBookFetcher
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
    """End-to-end orchestrator that ties together all four modules.

    Lifecycle
    ---------
    1. ``__init__``: load config, wire up all components.
    2. ``startup``: resolve item nameids, open DB connection, send "online" ping.
    3. ``run_forever``: main scan loop — per-item fetch→store→detect→alert.
    4. ``shutdown``: close DB, send "stopped" notification.
    """

    def __init__(self, config_path: Path = _CONFIG_PATH) -> None:
        # load config
        with config_path.open(encoding="utf-8") as fh:
            cfg = json.load(fh)

        self._db_config: dict = cfg["database"]
        self._system_cfg: dict = cfg["system"]
        # target_items: {"nameid_str": "item_name", ...}
        self._target_items: dict[str, str] = cfg["target_items"]

        # Acquisition
        self._fetcher = SteamOrderBookFetcher()
        self._initializer = NameIdInitializer(self._fetcher)

        # Storage
        self._db = DatabaseConnection(self._db_config)
        self._repo: OrderBookRepository | None = None

        # Anomaly detection (shares the same DB config)
        self._detector = MarketAnomalyDetector(self._db_config)

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
        """Resolve nameids, open DB, send startup notification."""
        logger.info("=== QuantOrchestrator starting up ===")

        # Phase 1 — pre-inject nameids that are already known from config
        #           (avoids unnecessary HTML scraping for fully-configured items)
        known_ids = {
            name: int(nameid_str)
            for nameid_str, name in self._target_items.items()
        }
        written = self._fetcher._cache.load_from_dict(known_ids)
        logger.info("Pre-injected %d nameid(s) from config into cache.", written)

        # Phase 1b — resolve any items whose nameid is still unknown (scraping fallback)
        item_names = list(self._target_items.values())
        logger.info("Resolving nameids for %d item(s)…", len(item_names))
        result = self._initializer.run(item_names)
        logger.info("NameId init: %s", result)
        if not result.all_succeeded:
            failed_names = list(result.failed.keys())
            logger.warning("Some nameids failed to resolve: %s", failed_names)

        # Phase 2 — open DB connection
        self._db.connect()
        self._repo = OrderBookRepository(self._db.connection)
        logger.info("Database connection established.")

        # Phase 3 — register all items in metadata table (idempotent)
        for nameid_str, item_name in self._target_items.items():
            try:
                self._repo.init_item_metadata(int(nameid_str), item_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not register item metadata for %r: %s", item_name, exc
                )

        # Phase 4 — send startup ping
        self._alerter.send_text("🟢 CS2 Market Monitor 已启动 (System online)")
        logger.info("Startup complete.")

    def shutdown(self) -> None:
        """Send shutdown notification and close DB."""
        logger.info("Shutting down…")
        try:
            self._alerter.send_text("🔴 CS2 Market Monitor 已停止 (System stopped)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not send shutdown notification: %s", exc)
        self._db.close()
        logger.info("Database connection closed. Goodbye.")

    def run_forever(self) -> None:
        """Main loop — scans all items, then sleeps until the next cycle."""
        scan_interval_s = self._system_cfg["scan_interval_minutes"] * 60
        sleep_min = self._system_cfg["anti_spider_sleep_min"]
        sleep_max = self._system_cfg["anti_spider_sleep_max"]

        logger.info(
            "Entering scan loop: %d item(s), interval=%ds",
            len(self._target_items),
            scan_interval_s,
        )

        while True:
            try:
                self._scan_all_items(sleep_min, sleep_max)
            except psycopg2.Error as db_exc:
                # 专门处理数据库断线/接口异常，触发自愈重连
                logger.exception("Database error occurred during scan: %s", db_exc)
                self._send_alert_safe(
                    f"⚠️ CS2 Monitor 数据库异常，正在尝试自愈：{db_exc}"
                )
                self._recover_database()
                time.sleep(60)

            except Exception as exc:  # noqa: BLE001
                # 其他未预期异常（API 超时、数据解析错误、代码 Bug 等）
                # 不触发数据库重连，只记录日志并等待下次重试
                logger.exception("Unexpected error in scan cycle: %s", exc)
                self._send_alert_safe(
                    f"⚠️ CS2 Monitor 出现未知错误，60秒后重试：{exc}"
                )
                time.sleep(60)

            else:
                logger.info("Scan cycle complete. Sleeping %ds…", scan_interval_s)
                time.sleep(scan_interval_s)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_all_items(self, sleep_min: float, sleep_max: float) -> None:
        """Run one full scan cycle across all configured items."""
        items = list(self._target_items.items())
        for idx, (nameid_str, item_name) in enumerate(items):
            try:
                self._process_item(int(nameid_str), item_name)
            except psycopg2.Error:
                # 数据库异常必须向上冒泡，交由 run_forever 的专用 handler 处理
                raise
            except Exception as exc:  # noqa: BLE001
                # 单品异常（API 超时、解析错误等）只记录日志，不中断整轮扫描
                logger.error(
                    "Error processing item %r (nameid=%s): %s",
                    item_name, nameid_str, exc,
                )

            # Anti-spider delay between items (skip after last item)
            if idx < len(items) - 1:
                delay = random.uniform(sleep_min, sleep_max)
                logger.debug("Anti-spider delay: %.1fs", delay)
                time.sleep(delay)

    def _process_item(self, nameid: int, item_name: str) -> None:
        """Full pipeline for a single item: fetch → store → detect → alert."""
        logger.info("Processing %r (nameid=%d)", item_name, nameid)

        # Step 1 — fetch order book snapshot
        snapshot = self._fetcher.fetch_order_book(item_name)
        logger.debug(
            "Fetched snapshot: ask=%.2f bid=%.2f",
            snapshot.lowest_ask_price,
            snapshot.highest_bid_price,
        )

        # Step 2 — persist to DB
        self._repo.insert_snapshot(snapshot, nameid)
        logger.debug("Snapshot stored for nameid=%d", nameid)

        # Step 3 — anomaly detection
        result = self._detector.detect_anomalies(nameid)
        if result is None:
            logger.info(
                "Insufficient data for anomaly detection on %r, skipping.", item_name
            )
            return

        logger.info(
            "Detection result for %r: signal=%s score=%.4f",
            item_name,
            result["signal_type"],
            result["anomaly_score"],
        )

        # Step 4 — dispatch alert if warranted
        self._dispatcher.dispatch(item_name, result)

    def _recover_database(self) -> None:
        """独立的数据库自愈逻辑：关闭死连接并重新建立连接。"""
        logger.info("Attempting to re-establish database connection...")
        try:
            self._db.close()
            self._db.connect()
            self._repo = OrderBookRepository(self._db.connection)
            logger.info("Database connection successfully recovered.")
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to recover database connection: %s", exc)

    def _send_alert_safe(self, message: str) -> None:
        """安全发送告警——不会抛出异常，但告警失败时记录 error 日志。"""
        try:
            self._alerter.send_text(message)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to send alert. Message: %r, Error: %s", message, exc
            )



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
