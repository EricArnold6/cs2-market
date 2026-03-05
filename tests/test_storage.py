"""Unit tests for src.storage (DatabaseConnection and OrderBookRepository)."""

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.acquisition.models import OrderBook
from src.storage import DatabaseConnection, OrderBookRepository


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_orderbook(
    item_name="AK-47 | Redline (Field-Tested)",
    timestamp=None,
    lowest_ask=10.50,
    highest_bid=9.80,
    ask_vol=100,
    bid_vol=200,
    total_sell=500,
    total_buy=300,
) -> OrderBook:
    return OrderBook(
        item_name=item_name,
        timestamp=timestamp or int(time.time()),
        lowest_ask_price=lowest_ask,
        highest_bid_price=highest_bid,
        ask_volume_top5_cumulative=ask_vol,
        bid_volume_top5_cumulative=bid_vol,
        total_sell_orders=total_sell,
        total_buy_orders=total_buy,
    )


# ---------------------------------------------------------------------------
# TestDatabaseConnection
# ---------------------------------------------------------------------------

class TestDatabaseConnection:

    def _mock_conn(self):
        mock_conn = MagicMock()
        mock_conn.closed = 0
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return mock_conn, mock_cur

    def test_connect_calls_psycopg2_with_config(self):
        config = {"host": "localhost", "dbname": "cs2"}
        with patch("src.storage.database.psycopg2.connect") as mock_connect:
            mock_conn, _ = self._mock_conn()
            mock_connect.return_value = mock_conn
            db = DatabaseConnection(config)
            db.connect()
            mock_connect.assert_called_once_with(**config)

    def test_connect_sets_autocommit(self):
        config = {"host": "localhost", "dbname": "cs2"}
        with patch("src.storage.database.psycopg2.connect") as mock_connect:
            mock_conn, _ = self._mock_conn()
            mock_connect.return_value = mock_conn
            db = DatabaseConnection(config)
            db.connect()
            assert mock_conn.autocommit is True

    def test_connect_executes_three_ddl_statements(self):
        config = {"dbname": "cs2"}
        with patch("src.storage.database.psycopg2.connect") as mock_connect:
            mock_conn, mock_cur = self._mock_conn()
            mock_connect.return_value = mock_conn
            db = DatabaseConnection(config)
            db.connect()
            assert mock_cur.execute.call_count == 3

    def test_connect_is_idempotent(self):
        config = {"dbname": "cs2"}
        with patch("src.storage.database.psycopg2.connect") as mock_connect:
            mock_conn, _ = self._mock_conn()
            mock_connect.return_value = mock_conn
            db = DatabaseConnection(config)
            db.connect()
            db.connect()
            assert mock_connect.call_count == 1

    def test_connection_property_raises_before_connect(self):
        db = DatabaseConnection({})
        with pytest.raises(RuntimeError):
            _ = db.connection

    def test_context_manager_calls_close(self):
        config = {"dbname": "cs2"}
        with patch("src.storage.database.psycopg2.connect") as mock_connect:
            mock_conn, _ = self._mock_conn()
            mock_connect.return_value = mock_conn
            db = DatabaseConnection(config)
            with db:
                pass
            mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# TestInitItemMetadata
# ---------------------------------------------------------------------------

class TestInitItemMetadata:

    def _make_repo(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return OrderBookRepository(mock_conn), mock_cur

    def test_executes_on_conflict_do_nothing_sql(self):
        repo, mock_cur = self._make_repo()
        repo.init_item_metadata(123, "AK-47 | Redline (Field-Tested)")
        sql_called = mock_cur.execute.call_args[0][0]
        assert "ON CONFLICT DO NOTHING" in sql_called

    def test_passes_correct_item_nameid(self):
        repo, mock_cur = self._make_repo()
        repo.init_item_metadata(999, "Some Item")
        args = mock_cur.execute.call_args[0][1]
        assert args[0] == 999

    def test_no_error_on_second_call(self):
        repo, mock_cur = self._make_repo()
        repo.init_item_metadata(1, "Item A")
        repo.init_item_metadata(1, "Item A")
        assert mock_cur.execute.call_count == 2


# ---------------------------------------------------------------------------
# TestInsertSnapshot
# ---------------------------------------------------------------------------

class TestInsertSnapshot:

    def _make_repo(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return OrderBookRepository(mock_conn), mock_cur

    def test_calls_execute_values(self):
        repo, mock_cur = self._make_repo()
        with patch("src.storage.repository.execute_values") as mock_ev:
            ob = _make_orderbook()
            repo.insert_snapshot(ob, 42)
            mock_ev.assert_called_once()

    def test_timestamp_converted_to_utc_datetime(self):
        repo, _ = self._make_repo()
        ts = 1_700_000_000
        ob = _make_orderbook(timestamp=ts)
        with patch("src.storage.repository.execute_values") as mock_ev:
            repo.insert_snapshot(ob, 42)
            rows = mock_ev.call_args[0][2]
            dt = rows[0][0]
            assert isinstance(dt, datetime)
            assert dt.tzinfo is not None
            assert dt == datetime.fromtimestamp(ts, tz=timezone.utc)

    def test_item_nameid_in_row_index_1(self):
        repo, _ = self._make_repo()
        ob = _make_orderbook()
        with patch("src.storage.repository.execute_values") as mock_ev:
            repo.insert_snapshot(ob, 77)
            rows = mock_ev.call_args[0][2]
            assert rows[0][1] == 77

    def test_zero_ask_price_becomes_none(self):
        repo, _ = self._make_repo()
        ob = _make_orderbook(lowest_ask=0.0)
        with patch("src.storage.repository.execute_values") as mock_ev:
            repo.insert_snapshot(ob, 1)
            rows = mock_ev.call_args[0][2]
            assert rows[0][2] is None

    def test_zero_bid_price_becomes_none(self):
        repo, _ = self._make_repo()
        ob = _make_orderbook(highest_bid=0.0)
        with patch("src.storage.repository.execute_values") as mock_ev:
            repo.insert_snapshot(ob, 1)
            rows = mock_ev.call_args[0][2]
            assert rows[0][3] is None


# ---------------------------------------------------------------------------
# TestInsertSnapshotsBulk
# ---------------------------------------------------------------------------

class TestInsertSnapshotsBulk:

    def _make_repo(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return OrderBookRepository(mock_conn), mock_cur

    def test_returns_correct_count(self):
        repo, _ = self._make_repo()
        snapshots = [_make_orderbook("Item A"), _make_orderbook("Item B")]
        nameid_map = {"Item A": 1, "Item B": 2}
        with patch("src.storage.repository.execute_values"):
            count = repo.insert_snapshots_bulk(snapshots, nameid_map)
            assert count == 2

    def test_skips_unknown_items(self):
        repo, _ = self._make_repo()
        snapshots = [_make_orderbook("Item A"), _make_orderbook("Unknown")]
        nameid_map = {"Item A": 1}
        with patch("src.storage.repository.execute_values") as mock_ev:
            count = repo.insert_snapshots_bulk(snapshots, nameid_map)
            assert count == 1
            rows = mock_ev.call_args[0][2]
            assert len(rows) == 1

    def test_empty_list_returns_zero_without_db_call(self):
        repo, _ = self._make_repo()
        with patch("src.storage.repository.execute_values") as mock_ev:
            count = repo.insert_snapshots_bulk([], {})
            assert count == 0
            mock_ev.assert_not_called()

    def test_all_unknown_returns_zero(self):
        repo, _ = self._make_repo()
        snapshots = [_make_orderbook("Ghost Item")]
        with patch("src.storage.repository.execute_values") as mock_ev:
            count = repo.insert_snapshots_bulk(snapshots, {})
            assert count == 0
            mock_ev.assert_not_called()

    def test_single_execute_values_call_for_batch(self):
        repo, _ = self._make_repo()
        snapshots = [_make_orderbook(f"Item {i}") for i in range(5)]
        nameid_map = {f"Item {i}": i for i in range(5)}
        with patch("src.storage.repository.execute_values") as mock_ev:
            repo.insert_snapshots_bulk(snapshots, nameid_map)
            assert mock_ev.call_count == 1


# ---------------------------------------------------------------------------
# TestGetLatestSnapshot
# ---------------------------------------------------------------------------

class TestGetLatestSnapshot:

    def _make_repo_with_fetchone(self, row):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = row
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return OrderBookRepository(mock_conn), mock_cur

    def test_returns_none_when_no_row(self):
        repo, _ = self._make_repo_with_fetchone(None)
        result = repo.get_latest_snapshot(42)
        assert result is None

    def test_returns_dict_with_correct_keys(self):
        row = (datetime.now(tz=timezone.utc), 42, 10.5, 9.8, 100, 200, 500, 300)
        repo, _ = self._make_repo_with_fetchone(row)
        result = repo.get_latest_snapshot(42)
        assert isinstance(result, dict)
        expected_keys = {
            "time", "item_nameid", "lowest_ask_price", "highest_bid_price",
            "ask_volume_top5", "bid_volume_top5", "total_sell_orders", "total_buy_orders",
        }
        assert set(result.keys()) == expected_keys

    def test_query_contains_order_by_time_desc(self):
        repo, mock_cur = self._make_repo_with_fetchone(None)
        repo.get_latest_snapshot(1)
        sql = mock_cur.execute.call_args[0][0]
        assert "ORDER BY time DESC" in sql
