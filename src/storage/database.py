"""PostgreSQL database connection manager."""

import psycopg2


_DDL_ITEMS = """
CREATE TABLE IF NOT EXISTS items (
    item_nameid       BIGINT       PRIMARY KEY,
    market_hash_name  VARCHAR(255) NOT NULL UNIQUE,
    added_at          TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP
);
"""

_DDL_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS order_book_snapshots (
    time               TIMESTAMPTZ NOT NULL,
    item_nameid        BIGINT      NOT NULL REFERENCES items(item_nameid),
    lowest_ask_price   NUMERIC(10, 2),
    highest_bid_price  NUMERIC(10, 2),
    ask_volume_top5    INT,
    bid_volume_top5    INT,
    total_sell_orders  INT,
    total_buy_orders   INT
);
"""

_DDL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_item_time
    ON order_book_snapshots (item_nameid, time DESC);
"""


class DatabaseConnection:
    """Manages a single psycopg2 connection with autocommit=True."""

    def __init__(self, config: dict):
        self._config = config
        self._conn = None

    def connect(self) -> None:
        """Open connection and initialize schema. Idempotent."""
        if self._conn is not None and self._conn.closed == 0:
            return
        self._conn = psycopg2.connect(**self._config)
        self._conn.autocommit = True
        self._init_schema()

    def close(self) -> None:
        """Close connection if open."""
        if self._conn is not None and self._conn.closed == 0:
            self._conn.close()

    @property
    def connection(self):
        """Return raw psycopg2 connection, or raise if not connected."""
        if self._conn is None or self._conn.closed != 0:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._conn

    def _init_schema(self) -> None:
        """Execute DDL statements to create tables and index."""
        with self._conn.cursor() as cur:
            cur.execute(_DDL_ITEMS)
            cur.execute(_DDL_SNAPSHOTS)
            cur.execute(_DDL_INDEX)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
