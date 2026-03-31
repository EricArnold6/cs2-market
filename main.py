"""QuantOrchestrator — CS2 market monitoring daemon (CSQAQ Batch Version).

Entry point for the full data pipeline:
    fetch(Batch) → store → detect anomalies → alert

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

        # 兼容旧配置：如果 target_items 还是字典 {"123": "AK47"}，就提取 values 变成列表
        raw_targets = cfg["target_items"]
        if isinstance(raw_targets, dict):
            self._target_items: list[str] = list(raw_targets.values())
        else:
            self._target_items: list[str] = raw_targets

        # Acquisition (接入 CSQAQ)
        csqaq_cfg = cfg.get("csqaq", {})
        api_token = csqaq_cfg.get("api_token", "YOUR_API_TOKEN")
        self._client = CSQAQClient(api_token=api_token)
        self._initializer = NameIdInitializer(self._client)

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
        """Resolve CSQAQ good_ids, open DB, send startup notification."""
        logger.info("=== QuantOrchestrator (CSQAQ Version) starting up ===")

        # Phase 1 — 使用联想查询接口批量获取所有饰品的专属 ID
        logger.info("Resolving CSQAQ IDs for %d item(s)…", len(self._target_items))
        result = self._initializer.run(self._target_items)
        logger.info("NameId init: %s", result)
        if not result.all_succeeded:
            failed_names = list(result.failed.keys())
            logger.warning("Some items failed to resolve ID: %s", failed_names)

        # Phase 2 — open DB connection
        self._db.connect()
        self._repo = OrderBookRepository(self._db.connection)
        logger.info("Database connection established.")

        # Phase 3 — register all items in metadata table
        for name in self._target_items:
            csqaq_id = self._client.cache.get(name)
            if csqaq_id is not None:
                try:
                    self._repo.init_item_metadata(csqaq_id, name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Could not register item metadata for %r: %s", name, exc
                    )

        # Phase 4 — send startup ping
        self._alerter.send_text("🟢 CS2 Market Monitor (CSQAQ节点) 已启动")
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
        """Main loop — fetches all items in one batch, then sleeps."""
        # 因为变成了批量接口，你可以把配置里的扫图间隔设为 1~3 分钟，而不是以前的 15 分钟
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

    def _scan_all_items_batch(self) -> None:
        """Run one full scan cycle with Market Breadth Risk Control."""
        logger.info("Fetching batch prices for %d items...", len(self._target_items))

        snapshots = self._client.fetch_batch_prices(self._target_items)
        if not snapshots:
            logger.warning("No snapshots returned from API in this cycle.")
            return

        # 暂存本轮所有的检测结果，用于事后统一分发
        pending_alerts = []
        # 用于计算大盘情绪的市场广度数组
        market_spreads = []

        # ==========================================
        # 阶段 1：入库与异常检测 (收集数据)
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
                result = self._detector.detect_anomalies(csqaq_id)
                if result is not None:
                    # 只要不是预热期，就收集该饰品当前的价格波动率
                    market_spreads.append(result.get("spread_ratio", 0.0))
                    pending_alerts.append((snap.item_name, result))
            except Exception as exc:
                logger.error("Anomaly detection failed for %r: %s", snap.item_name, exc)

        # ==========================================
        # 阶段 2：计算大盘情绪与熔断 (Market Risk Control)
        # ==========================================
        if market_spreads:
            # 计算全盘平均波动率
            avg_spread = sum(market_spreads) / len(market_spreads)
            logger.info("Market Breadth (Avg Spread): %.2f%%", avg_spread * 100)

            # 熔断条件：如果在短短 3 分钟内，监控池平均跌幅超过 1.5% (-0.015)
            # 这通常意味着系统级风险（V社发公告、大商户集体爆仓抛售）
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