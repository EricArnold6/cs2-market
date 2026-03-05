"""Order book CRUD operations."""

import logging
from datetime import datetime, timezone

from psycopg2.extras import execute_values

from src.schemas.market import OrderBookSnapshot

logger = logging.getLogger(__name__)


class OrderBookRepository:
    """CRUD operations against items and order_book_snapshots tables."""

    def __init__(self, conn):
        self._conn = conn

    def init_item_metadata(self, item_nameid: int, market_hash_name: str) -> None:
        """Insert item metadata, silently ignoring duplicates."""
        sql = """
            INSERT INTO items (item_nameid, market_hash_name)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, (item_nameid, market_hash_name))

    def insert_snapshot(self, snapshot: OrderBookSnapshot, item_nameid: int) -> None:
        """Insert a single order book snapshot."""
        row = self._orderbook_to_row(snapshot, item_nameid)
        sql = """
            INSERT INTO order_book_snapshots (
                time, item_nameid, lowest_ask_price, highest_bid_price,
                ask_volume_top5, bid_volume_top5, total_sell_orders, total_buy_orders
            ) VALUES %s
        """
        with self._conn.cursor() as cur:
            execute_values(cur, sql, [row])

    def insert_snapshots_bulk(self, snapshots: list, nameid_map: dict) -> int:
        """Batch insert snapshots. Returns number of rows inserted."""
        if not snapshots:
            return 0

        rows = []
        for snapshot in snapshots:
            nameid = nameid_map.get(snapshot.item_name)
            if nameid is None:
                logger.warning("Skipping snapshot for unknown item: %s", snapshot.item_name)
                continue
            rows.append(self._orderbook_to_row(snapshot, nameid))

        if not rows:
            return 0

        sql = """
            INSERT INTO order_book_snapshots (
                time, item_nameid, lowest_ask_price, highest_bid_price,
                ask_volume_top5, bid_volume_top5, total_sell_orders, total_buy_orders
            ) VALUES %s
        """
        with self._conn.cursor() as cur:
            execute_values(cur, sql, rows)

        return len(rows)

    def get_latest_snapshot(self, item_nameid: int) -> dict | None:
        """Return the most recent snapshot for an item, or None."""
        sql = """
            SELECT time, item_nameid, lowest_ask_price, highest_bid_price,
                   ask_volume_top5, bid_volume_top5, total_sell_orders, total_buy_orders
            FROM order_book_snapshots
            WHERE item_nameid = %s
            ORDER BY time DESC
            LIMIT 1
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, (item_nameid,))
            row = cur.fetchone()

        if row is None:
            return None

        keys = [
            "time", "item_nameid", "lowest_ask_price", "highest_bid_price",
            "ask_volume_top5", "bid_volume_top5", "total_sell_orders", "total_buy_orders",
        ]
        return dict(zip(keys, row))

    @staticmethod
    def _orderbook_to_row(snapshot: OrderBookSnapshot, item_nameid: int) -> tuple:
        """Convert an OrderBookSnapshot to a DB row tuple."""
        ts = datetime.fromtimestamp(snapshot.timestamp, tz=timezone.utc)
        ask = snapshot.lowest_ask_price if snapshot.lowest_ask_price != 0.0 else None
        bid = snapshot.highest_bid_price if snapshot.highest_bid_price != 0.0 else None
        return (
            ts,
            item_nameid,
            ask,
            bid,
            snapshot.ask_volume_top5,
            snapshot.bid_volume_top5,
            snapshot.total_sell_orders,
            snapshot.total_buy_orders,
        )
