"""
Unit tests for src/data/fetcher.py and src/data/scheduler.py.

All tests use unittest.mock exclusively — no real HTTP requests are made.
"""

import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from src.acquisition.http_client import (
    SteamOrderBookFetcher,
    _USER_AGENTS,
    NameIdExtractionError,
    NameIdNotInitializedError,
)
from src.acquisition.cache import _NameIdCache
from src.acquisition.initializer import NameIdInitializer, InitResult
from src.acquisition.models import OrderBook
from src.acquisition.scheduler import PollingScheduler

# ---------------------------------------------------------------------------
# Shared fixtures / fake data
# ---------------------------------------------------------------------------

FAKE_ORDERBOOK_RESPONSE = {
    "success": 1,
    "sell_order_count": "1,234",
    "buy_order_count": "567",
    "sell_order_graph": [
        [10.50, 3, "3 orders"],
        [10.75, 7, "7 orders"],
        [11.00, 5, "5 orders"],
        [11.25, 2, "2 orders"],
        [11.50, 4, "4 orders"],
        [12.00, 8, "8 orders"],
    ],
    "buy_order_graph": [
        [10.00, 6, "6 orders"],
        [9.75, 4, "4 orders"],
        [9.50, 9, "9 orders"],
        [9.25, 1, "1 order"],
        [9.00, 3, "3 orders"],
        [8.75, 2, "2 orders"],
    ],
}

FAKE_LISTING_HTML = (
    "<html><body>"
    "<script>Market_LoadOrderSpread(176923345);</script>"
    "</body></html>"
)


@pytest.fixture
def tmp_cache(tmp_path):
    """Return a _NameIdCache backed by an isolated temp directory."""
    return _NameIdCache(cache_path=tmp_path / "nameid_cache.json")


@pytest.fixture
def mock_session():
    """A MagicMock requests.Session that returns 200 + fake data by default."""
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = FAKE_ORDERBOOK_RESPONSE
    resp.text = FAKE_LISTING_HTML
    sess.get.return_value = resp
    return sess


@pytest.fixture
def fetcher(tmp_cache, mock_session):
    """SteamOrderBookFetcher with injected mock session and temp cache."""
    return SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)


# ===========================================================================
# Group A — _NameIdCache (5 tests)
# ===========================================================================

class TestNameIdCache:

    def test_cache_miss_returns_none(self, tmp_cache):
        """Cache miss returns None for unknown item."""
        assert tmp_cache.get("AK-47 | Redline (Field-Tested)") is None

    def test_cache_hit_after_set(self, tmp_cache):
        """After set(), get() returns the stored nameid."""
        tmp_cache.set("AK-47 | Redline (Field-Tested)", 176923345)
        assert tmp_cache.get("AK-47 | Redline (Field-Tested)") == 176923345

    def test_cache_persists_to_disk(self, tmp_path):
        """Data written by set() is readable by a new _NameIdCache instance."""
        cache_path = tmp_path / "nameid_cache.json"
        c1 = _NameIdCache(cache_path)
        c1.set("Glock-18 | Fade (Factory New)", 999)
        c2 = _NameIdCache(cache_path)
        assert c2.get("Glock-18 | Fade (Factory New)") == 999

    def test_cache_loads_existing_file(self, tmp_path):
        """_NameIdCache loads pre-existing JSON on construction."""
        cache_path = tmp_path / "nameid_cache.json"
        cache_path.write_text(json.dumps({"item": 42}), encoding="utf-8")
        c = _NameIdCache(cache_path)
        assert c.get("item") == 42

    def test_cache_handles_corrupt_file(self, tmp_path):
        """Corrupt JSON file is silently ignored; cache starts empty."""
        cache_path = tmp_path / "nameid_cache.json"
        cache_path.write_text("NOT_JSON{{{", encoding="utf-8")
        c = _NameIdCache(cache_path)
        assert c.get("anything") is None


# ===========================================================================
# Group B — _parse_order_book pure function (9 tests)
# ===========================================================================

class TestParseOrderBook:

    def test_lowest_ask_price(self):
        ob = SteamOrderBookFetcher._parse_order_book("item", FAKE_ORDERBOOK_RESPONSE)
        assert ob.lowest_ask_price == 10.50

    def test_highest_bid_price(self):
        ob = SteamOrderBookFetcher._parse_order_book("item", FAKE_ORDERBOOK_RESPONSE)
        assert ob.highest_bid_price == 10.00

    def test_ask_volume_top5(self):
        """Top-5 ask volumes: 3+7+5+2+4 = 21."""
        ob = SteamOrderBookFetcher._parse_order_book("item", FAKE_ORDERBOOK_RESPONSE)
        assert ob.ask_volume_top5 == 21

    def test_bid_volume_top5(self):
        """Top-5 bid volumes: 6+4+9+1+3 = 23."""
        ob = SteamOrderBookFetcher._parse_order_book("item", FAKE_ORDERBOOK_RESPONSE)
        assert ob.bid_volume_top5 == 23

    def test_total_sell_orders_parses_comma(self):
        """'1,234' sell orders → 1234."""
        ob = SteamOrderBookFetcher._parse_order_book("item", FAKE_ORDERBOOK_RESPONSE)
        assert ob.total_sell_orders == 1234

    def test_total_buy_orders(self):
        ob = SteamOrderBookFetcher._parse_order_book("item", FAKE_ORDERBOOK_RESPONSE)
        assert ob.total_buy_orders == 567

    def test_timestamp_is_int(self):
        ob = SteamOrderBookFetcher._parse_order_book("item", FAKE_ORDERBOOK_RESPONSE)
        assert isinstance(ob.timestamp, int)

    def test_empty_graphs_return_zeros(self):
        """Completely empty response must not crash; all numeric fields are 0."""
        ob = SteamOrderBookFetcher._parse_order_book("item", {})
        assert ob.lowest_ask_price == 0.0
        assert ob.highest_bid_price == 0.0
        assert ob.ask_volume_top5 == 0
        assert ob.bid_volume_top5 == 0
        assert ob.total_sell_orders == 0
        assert ob.total_buy_orders == 0

    def test_fewer_than_5_levels_no_crash(self):
        """Only 2 levels in each graph — must not raise IndexError."""
        data = {
            "sell_order_graph": [[5.0, 2, ""], [6.0, 3, ""]],
            "buy_order_graph": [[4.0, 1, ""]],
            "sell_order_count": "5",
            "buy_order_count": "1",
        }
        ob = SteamOrderBookFetcher._parse_order_book("item", data)
        assert ob.ask_volume_top5 == 5   # 2+3
        assert ob.bid_volume_top5 == 1


# ===========================================================================
# Group C — resolve_item_nameid (4 tests)
# ===========================================================================

class TestResolveItemNameid:

    def test_cache_hit_skips_http(self, tmp_cache, mock_session):
        """Cache hit should result in zero HTTP calls."""
        tmp_cache.set("AK-47 | Redline (Field-Tested)", 176923345)
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        result = f.resolve_item_nameid("AK-47 | Redline (Field-Tested)")
        assert result == 176923345
        mock_session.get.assert_not_called()

    def test_cache_miss_fetches_html(self, tmp_cache, mock_session):
        """Cache miss triggers an HTTP request and parses the nameid."""
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        result = f.resolve_item_nameid("AK-47 | Redline (Field-Tested)")
        assert result == 176923345
        mock_session.get.assert_called_once()

    def test_second_call_uses_cache(self, tmp_cache, mock_session):
        """Second call for same item must not make additional HTTP requests."""
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        f.resolve_item_nameid("AK-47 | Redline (Field-Tested)")
        f.resolve_item_nameid("AK-47 | Redline (Field-Tested)")
        assert mock_session.get.call_count == 1

    def test_no_match_raises_nameid_extraction_error(self, tmp_cache, mock_session):
        """HTML without Market_LoadOrderSpread must raise NameIdExtractionError."""
        mock_session.get.return_value.text = "<html>no match here</html>"
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        with pytest.raises(NameIdExtractionError):
            f.resolve_item_nameid("Unknown Item")


# ===========================================================================
# Group D — fetch_order_book (5 tests)
# ===========================================================================

class TestFetchOrderBook:

    def test_returns_orderbook_instance(self, fetcher, tmp_cache):
        """fetch_order_book must return an OrderBook."""
        tmp_cache.set("AK-47 | Redline (Field-Tested)", 176923345)
        result = fetcher.fetch_order_book("AK-47 | Redline (Field-Tested)")
        assert isinstance(result, OrderBook)

    def test_request_params_contain_nameid(self, fetcher, tmp_cache, mock_session):
        """The HTTP request must include item_nameid in its parameters."""
        tmp_cache.set("AK-47 | Redline (Field-Tested)", 176923345)
        fetcher.fetch_order_book("AK-47 | Redline (Field-Tested)")
        # The last call should be the orderbook call (after possible nameid call)
        last_call_kwargs = mock_session.get.call_args
        params = last_call_kwargs[1].get("params") or last_call_kwargs[0][1] if len(last_call_kwargs[0]) > 1 else last_call_kwargs[1].get("params", {})
        # params may be in kwargs
        if last_call_kwargs.kwargs:
            params = last_call_kwargs.kwargs.get("params", {})
        else:
            params = {}
            for c in mock_session.get.call_args_list:
                if c.kwargs.get("params"):
                    params = c.kwargs["params"]
        assert params.get("item_nameid") == 176923345

    def test_429_retry_succeeds(self, tmp_cache, mock_session):
        """A single 429 followed by 200 should succeed after retry."""
        tmp_cache.set("AK-47 | Redline (Field-Tested)", 176923345)

        resp_429 = MagicMock()
        resp_429.status_code = 429

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = FAKE_ORDERBOOK_RESPONSE
        resp_200.text = FAKE_LISTING_HTML

        mock_session.get.side_effect = [resp_429, resp_200]
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)

        with patch("src.acquisition.http_client.time.sleep"):
            result = f.fetch_order_book("AK-47 | Redline (Field-Tested)")
        assert isinstance(result, OrderBook)

    def test_too_many_429_raises_runtime_error(self, tmp_cache, mock_session):
        """Exceeding _RETRY_MAX 429 responses must raise RuntimeError."""
        tmp_cache.set("AK-47 | Redline (Field-Tested)", 176923345)

        resp_429 = MagicMock()
        resp_429.status_code = 429
        mock_session.get.return_value = resp_429

        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        with patch("src.acquisition.http_client.time.sleep"):
            with pytest.raises(RuntimeError):
                f.fetch_order_book("AK-47 | Redline (Field-Tested)")

    def test_user_agent_in_known_list(self, tmp_cache, mock_session):
        """The User-Agent header sent must be one of the known UA strings."""
        tmp_cache.set("AK-47 | Redline (Field-Tested)", 176923345)
        fetcher = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        fetcher.fetch_order_book("AK-47 | Redline (Field-Tested)")
        # Find the headers used in any get() call
        used_ua = None
        for c in mock_session.get.call_args_list:
            headers = c.kwargs.get("headers") or {}
            if "User-Agent" in headers:
                used_ua = headers["User-Agent"]
        assert used_ua in _USER_AGENTS


# ===========================================================================
# Group E — fetch_multiple (3 tests)
# ===========================================================================

class TestFetchMultiple:

    def test_returns_correct_length(self, tmp_cache, mock_session):
        """fetch_multiple returns one OrderBook per item name."""
        tmp_cache.set("Item A", 111)
        tmp_cache.set("Item B", 222)
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        with patch("src.acquisition.http_client.time.sleep"):
            results = f.fetch_multiple(["Item A", "Item B"])
        assert len(results) == 2

    def test_sleep_called_between_items(self, tmp_cache, mock_session):
        """sleep() must be called exactly (n-1) times for n items."""
        for i, name in enumerate(["Item A", "Item B", "Item C"]):
            tmp_cache.set(name, i + 100)
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        with patch("src.acquisition.http_client.time.sleep") as mock_sleep:
            f.fetch_multiple(["Item A", "Item B", "Item C"])
        assert mock_sleep.call_count == 2

    def test_single_item_failure_skipped(self, tmp_cache, mock_session):
        """A failure on one item must not propagate; other items are returned."""
        tmp_cache.set("Good Item", 111)
        tmp_cache.set("Bad Item", 222)

        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.json.return_value = FAKE_ORDERBOOK_RESPONSE
        good_resp.text = FAKE_LISTING_HTML

        bad_resp = MagicMock()
        bad_resp.status_code = 500
        bad_resp.raise_for_status.side_effect = Exception("Server error")

        # First item ("Good Item") uses good_resp; second ("Bad Item") uses bad_resp
        mock_session.get.side_effect = [good_resp, bad_resp]

        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        with patch("src.acquisition.http_client.time.sleep"):
            results = f.fetch_multiple(["Good Item", "Bad Item"])
        assert len(results) == 1
        assert results[0].item_name == "Good Item"


# ===========================================================================
# Group F — OrderBook computed properties (3 tests)
# ===========================================================================

class TestOrderBookProperties:

    def _make_ob(self, ask, bid):
        return OrderBook(
            item_name="test",
            timestamp=int(time.time()),
            lowest_ask_price=ask,
            highest_bid_price=bid,
            ask_volume_top5=10,
            bid_volume_top5=10,
            total_buy_orders=100,
            total_sell_orders=100,
        )

    def test_spread(self):
        ob = self._make_ob(ask=10.50, bid=10.00)
        assert ob.spread == pytest.approx(0.50, abs=1e-4)

    def test_mid_price(self):
        ob = self._make_ob(ask=10.50, bid=10.00)
        assert ob.mid_price == pytest.approx(10.25, abs=1e-4)

    def test_zero_when_one_side_empty(self):
        ob_no_ask = self._make_ob(ask=0.0, bid=10.00)
        assert ob_no_ask.spread == 0.0
        assert ob_no_ask.mid_price == 0.0
        ob_no_bid = self._make_ob(ask=10.00, bid=0.0)
        assert ob_no_bid.spread == 0.0
        assert ob_no_bid.mid_price == 0.0


# ===========================================================================
# Group G — PollingScheduler (5 tests)
# ===========================================================================

class TestPollingScheduler:

    def _make_scheduler(self, tmp_cache, mock_session, callback=None):
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        tmp_cache.set("AK-47 | Redline (Field-Tested)", 176923345)
        return PollingScheduler(
            fetcher=f,
            item_names=["AK-47 | Redline (Field-Tested)"],
            interval_seconds=1.0,
            on_snapshot=callback,
        )

    def test_callback_is_called(self, tmp_cache, mock_session):
        """on_snapshot callback must be called after poll_once."""
        received = []
        sched = self._make_scheduler(tmp_cache, mock_session, callback=received.append)
        with patch("src.acquisition.http_client.time.sleep"):
            sched.poll_once()
        assert len(received) == 1

    def test_poll_once_returns_list(self, tmp_cache, mock_session):
        """poll_once must return a list of OrderBook objects."""
        sched = self._make_scheduler(tmp_cache, mock_session)
        with patch("src.acquisition.http_client.time.sleep"):
            result = sched.poll_once()
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], OrderBook)

    def test_stop_event_exits_run_forever(self, tmp_cache, mock_session):
        """Setting stop_event must cause run_forever to exit within a few seconds."""
        sched = self._make_scheduler(tmp_cache, mock_session)
        stop = threading.Event()

        def _run():
            sched.run_forever(stop_event=stop)

        with patch("src.acquisition.http_client.time.sleep"), \
             patch("src.acquisition.scheduler.time.sleep"):
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            time.sleep(0.1)
            stop.set()
            t.join(timeout=5.0)
        assert not t.is_alive(), "run_forever did not exit after stop_event was set"

    def test_initial_last_poll_time_is_none(self, tmp_cache, mock_session):
        """Before any poll, last_poll_time must be None."""
        sched = self._make_scheduler(tmp_cache, mock_session)
        assert sched.last_poll_time is None

    def test_last_poll_time_updated_after_poll(self, tmp_cache, mock_session):
        """After poll_once, last_poll_time must be a positive float."""
        sched = self._make_scheduler(tmp_cache, mock_session)
        with patch("src.acquisition.http_client.time.sleep"):
            sched.poll_once()
        assert sched.last_poll_time is not None
        assert sched.last_poll_time > 0


# ===========================================================================
# Group H — _NameIdCache.load_from_dict() (10 tests)
# ===========================================================================

class TestNameIdCacheLoadFromDict:

    def test_load_basic(self, tmp_cache):
        """load_from_dict({"K": 1}) → cache.get("K") == 1, returns 1."""
        written = tmp_cache.load_from_dict({"K": 1})
        assert tmp_cache.get("K") == 1
        assert written == 1

    def test_load_no_overwrite_by_default(self, tmp_cache):
        """Already-existing entry is preserved by default; written count is 0."""
        tmp_cache.set("K", 999)
        written = tmp_cache.load_from_dict({"K": 42})
        assert tmp_cache.get("K") == 999
        assert written == 0

    def test_load_overwrite_true(self, tmp_cache):
        """overwrite=True replaces an existing entry; written count is 1."""
        tmp_cache.set("K", 999)
        written = tmp_cache.load_from_dict({"K": 42}, overwrite=True)
        assert tmp_cache.get("K") == 42
        assert written == 1

    def test_load_persists_to_disk(self, tmp_path):
        """Data from load_from_dict is visible to a freshly-loaded cache."""
        cache_path = tmp_path / "nameid_cache.json"
        c1 = _NameIdCache(cache_path)
        c1.load_from_dict({"SkinA": 100, "SkinB": 200})
        c2 = _NameIdCache(cache_path)
        assert c2.get("SkinA") == 100
        assert c2.get("SkinB") == 200

    def test_load_single_flush(self, tmp_cache):
        """Injecting 50 entries triggers exactly one _flush() call."""
        mapping = {f"item_{i}": i + 1 for i in range(50)}
        with patch.object(tmp_cache, "_flush", wraps=tmp_cache._flush) as mock_flush:
            tmp_cache.load_from_dict(mapping)
        mock_flush.assert_called_once()

    def test_load_rejects_non_int(self, tmp_cache):
        """nameid that is a string raises TypeError before any write."""
        with pytest.raises(TypeError):
            tmp_cache.load_from_dict({"K": "abc"})

    def test_load_rejects_zero(self, tmp_cache):
        """nameid == 0 raises ValueError."""
        with pytest.raises(ValueError):
            tmp_cache.load_from_dict({"K": 0})

    def test_load_rejects_negative(self, tmp_cache):
        """nameid < 0 raises ValueError."""
        with pytest.raises(ValueError):
            tmp_cache.load_from_dict({"K": -1})

    def test_load_empty_is_noop(self, tmp_cache):
        """load_from_dict({}) returns 0 without touching disk."""
        with patch.object(tmp_cache, "_flush") as mock_flush:
            written = tmp_cache.load_from_dict({})
        assert written == 0
        mock_flush.assert_not_called()

    def test_load_validation_before_write(self, tmp_cache):
        """If any entry is invalid the whole call raises; nothing is written."""
        with pytest.raises((TypeError, ValueError)):
            tmp_cache.load_from_dict({"Good": 1, "Bad": -5, "AlsoGood": 2})
        # None of the entries should have been written
        assert tmp_cache.get("Good") is None
        assert tmp_cache.get("AlsoGood") is None


# ===========================================================================
# Group I — resolve_item_nameid() HTML path hardening (5 tests)
# ===========================================================================

class TestResolveItemNameidHTMLPath:

    def test_resolve_sends_html_headers(self, tmp_cache, mock_session):
        """_request is called with extra_headers containing the 'Accept' key."""
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        with patch.object(f, "_request", wraps=f._request) as mock_req:
            f.resolve_item_nameid("AK-47 | Redline (Field-Tested)")
        mock_req.assert_called_once()
        _, kwargs = mock_req.call_args
        extra = kwargs.get("extra_headers") or mock_req.call_args.args[2] if len(mock_req.call_args.args) > 2 else None
        # extra_headers may be positional or keyword
        call_args = mock_req.call_args
        extra_headers = call_args.kwargs.get("extra_headers")
        assert extra_headers is not None
        assert "Accept" in extra_headers

    def test_resolve_handles_whitespace(self, tmp_cache, mock_session):
        """Regex should match even when spaces surround the nameid."""
        mock_session.get.return_value.text = (
            "<script>Market_LoadOrderSpread(  42  );</script>"
        )
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        result = f.resolve_item_nameid("Some Item")
        assert result == 42

    def test_resolve_raises_nameid_extraction_error(self, tmp_cache, mock_session):
        """No match in HTML raises NameIdExtractionError, not ValueError."""
        mock_session.get.return_value.text = "<html>nothing here</html>"
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        with pytest.raises(NameIdExtractionError):
            f.resolve_item_nameid("Unknown Item")

    def test_resolve_url_encodes_pipe(self, tmp_cache, mock_session):
        """Item name with '|' should appear URL-encoded in the request URL."""
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        f.resolve_item_nameid("AK-47 | Redline (Field-Tested)")
        called_url = mock_session.get.call_args.args[0]
        # %7C is the percent-encoded '|'; some encoders may use upper or lower
        assert "%7C" in called_url or "%7c" in called_url

    def test_resolve_caches_on_success(self, tmp_cache, mock_session):
        """After a successful HTML resolution, a second call makes no HTTP request."""
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        f.resolve_item_nameid("AK-47 | Redline (Field-Tested)")
        assert mock_session.get.call_count == 1
        f.resolve_item_nameid("AK-47 | Redline (Field-Tested)")
        assert mock_session.get.call_count == 1  # still 1 — no new HTTP call


# ===========================================================================
# Group J — fetch_order_book() strict guard (4 tests)
# ===========================================================================

class TestFetchOrderBookGuard:

    def test_fetch_raises_if_not_initialized(self, tmp_cache, mock_session):
        """Calling fetch_order_book without pre-loading the cache raises NameIdNotInitializedError."""
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        with pytest.raises(NameIdNotInitializedError):
            f.fetch_order_book("AK-47 | Redline (Field-Tested)")

    def test_fetch_does_not_call_resolve(self, tmp_cache, mock_session):
        """When cache is empty, fetch_order_book must NOT call resolve_item_nameid."""
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        with patch.object(f, "resolve_item_nameid") as mock_resolve:
            with pytest.raises(NameIdNotInitializedError):
                f.fetch_order_book("AK-47 | Redline (Field-Tested)")
        mock_resolve.assert_not_called()

    def test_fetch_succeeds_after_preload(self, tmp_cache, mock_session):
        """After load_from_dict preloads the cache, fetch_order_book returns OrderBook."""
        tmp_cache.load_from_dict({"AK-47 | Redline (Field-Tested)": 176923345})
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        result = f.fetch_order_book("AK-47 | Redline (Field-Tested)")
        assert isinstance(result, OrderBook)

    def test_fetch_error_message_mentions_initializer(self, tmp_cache, mock_session):
        """The NameIdNotInitializedError message must reference NameIdInitializer."""
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        with pytest.raises(NameIdNotInitializedError) as exc_info:
            f.fetch_order_book("AK-47 | Redline (Field-Tested)")
        assert "NameIdInitializer" in str(exc_info.value)


# ===========================================================================
# Group K — NameIdInitializer (9 tests)
# ===========================================================================

class TestNameIdInitializer:

    def _make_fetcher(self, tmp_cache, mock_session):
        return SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)

    def test_init_skips_cached(self, tmp_cache, mock_session):
        """All items already in cache → resolve_item_nameid not called; all in from_cache."""
        tmp_cache.set("Item A", 111)
        tmp_cache.set("Item B", 222)
        f = self._make_fetcher(tmp_cache, mock_session)
        initializer = NameIdInitializer(f, delay_min_s=0, delay_max_s=0)
        with patch.object(f, "resolve_item_nameid") as mock_resolve:
            result = initializer.run(["Item A", "Item B"])
        mock_resolve.assert_not_called()
        assert set(result.from_cache) == {"Item A", "Item B"}
        assert result.resolved == []

    def test_init_fetches_uncached(self, tmp_cache, mock_session):
        """All 3 items are uncached → all appear in result.resolved."""
        f = self._make_fetcher(tmp_cache, mock_session)
        initializer = NameIdInitializer(f, delay_min_s=0, delay_max_s=0)
        with patch("src.acquisition.http_client.time.sleep"):
            result = initializer.run(["Item A", "Item B", "Item C"])
        assert set(result.resolved) == {"Item A", "Item B", "Item C"}
        assert result.failed == {}

    def test_init_collects_failures(self, tmp_cache, mock_session):
        """If the 2nd item raises, it lands in result.failed; 1st and 3rd succeed."""
        f = self._make_fetcher(tmp_cache, mock_session)
        initializer = NameIdInitializer(f, delay_min_s=0, delay_max_s=0)

        call_count = [0]
        def _side_effect(name):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("network error")
            # Simulate a successful resolve that also writes to cache
            tmp_cache.set(name, call_count[0])
            return call_count[0]

        with patch.object(f, "resolve_item_nameid", side_effect=_side_effect):
            with patch("src.acquisition.http_client.time.sleep"):
                result = initializer.run(["Item A", "Item B", "Item C"])

        assert "Item B" in result.failed
        assert "Item A" in result.resolved
        assert "Item C" in result.resolved

    def test_init_all_succeeded_true(self, tmp_cache, mock_session):
        """When no failures, all_succeeded is True."""
        f = self._make_fetcher(tmp_cache, mock_session)
        initializer = NameIdInitializer(f, delay_min_s=0, delay_max_s=0)
        with patch("src.acquisition.http_client.time.sleep"):
            result = initializer.run(["Item A"])
        assert result.all_succeeded is True

    def test_init_all_succeeded_false(self, tmp_cache, mock_session):
        """When there is at least one failure, all_succeeded is False."""
        f = self._make_fetcher(tmp_cache, mock_session)
        initializer = NameIdInitializer(f, delay_min_s=0, delay_max_s=0)
        with patch.object(f, "resolve_item_nameid", side_effect=RuntimeError("fail")):
            result = initializer.run(["Item A"])
        assert result.all_succeeded is False

    def test_init_delay_between_requests(self, tmp_cache, mock_session):
        """3 items to fetch → sleep called exactly 2 times (not after last one)."""
        f = self._make_fetcher(tmp_cache, mock_session)
        initializer = NameIdInitializer(f, delay_min_s=0, delay_max_s=0)
        with patch("src.acquisition.http_client.time.sleep") as mock_sleep:
            result = initializer.run(["Item A", "Item B", "Item C"])
        assert mock_sleep.call_count == 2

    def test_init_no_delay_for_cache_hits(self, tmp_cache, mock_session):
        """2 cached + 1 to fetch (last in to_fetch) → sleep called 0 times."""
        tmp_cache.set("Item A", 111)
        tmp_cache.set("Item B", 222)
        f = self._make_fetcher(tmp_cache, mock_session)
        initializer = NameIdInitializer(f, delay_min_s=0, delay_max_s=0)
        with patch("src.acquisition.http_client.time.sleep") as mock_sleep:
            # Item C is the only uncached item → it's both first and last in to_fetch
            result = initializer.run(["Item A", "Item B", "Item C"])
        assert mock_sleep.call_count == 0

    def test_init_skip_cached_false_refetches(self, tmp_cache, mock_session):
        """skip_cached=False means cached items are still passed to resolve_item_nameid."""
        tmp_cache.set("Item A", 111)
        f = self._make_fetcher(tmp_cache, mock_session)
        initializer = NameIdInitializer(f, delay_min_s=0, delay_max_s=0)
        with patch.object(f, "resolve_item_nameid", return_value=111) as mock_resolve:
            result = initializer.run(["Item A"], skip_cached=False)
        mock_resolve.assert_called_once_with("Item A")

    def test_init_empty_list(self, tmp_cache, mock_session):
        """run([]) returns an empty InitResult with zero HTTP calls."""
        f = self._make_fetcher(tmp_cache, mock_session)
        initializer = NameIdInitializer(f, delay_min_s=0, delay_max_s=0)
        with patch.object(f, "resolve_item_nameid") as mock_resolve:
            result = initializer.run([])
        mock_resolve.assert_not_called()
        assert result.resolved == []
        assert result.from_cache == []
        assert result.failed == {}


# ===========================================================================
# Group L — _request() backward compatibility (2 tests)
# ===========================================================================

class TestRequestBackwardCompat:

    def test_request_no_extra_headers_default(self, tmp_cache, mock_session):
        """Calling _request without extra_headers does not raise any error."""
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        # Should succeed without extra_headers argument
        resp = f._request("https://example.com", params=None)
        assert resp is not None

    def test_request_extra_headers_merged(self, tmp_cache, mock_session):
        """Extra headers passed to _request appear in the outgoing request."""
        f = SteamOrderBookFetcher(session=mock_session, cache=tmp_cache)
        f._request("https://example.com", params=None, extra_headers={"X-Test": "1"})
        call_kwargs = mock_session.get.call_args.kwargs
        headers_sent = call_kwargs.get("headers", {})
        assert headers_sent.get("X-Test") == "1"
