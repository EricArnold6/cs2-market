"""
Unit tests for src/acquisition — cache, models, CSQAQClient.

All tests use unittest.mock exclusively — no real HTTP requests are made.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.acquisition.exceptions import NameIdExtractionError
from src.acquisition.cache import _NameIdCache
from src.acquisition.initializer import NameIdInitializer, InitResult
from src.acquisition.models import OrderBook
from src.acquisition.csqaq_client import CSQAQClient


# ===========================================================================
# Group A — _NameIdCache (5 tests)
# ===========================================================================

class TestNameIdCache:

    @pytest.fixture
    def tmp_cache(self, tmp_path):
        """Return a _NameIdCache backed by an isolated temp directory."""
        return _NameIdCache(cache_path=tmp_path / "nameid_cache.json")

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
        """_NameIdCache loads pre-existing JSON on construction (v1 compat format)."""
        cache_path = tmp_path / "nameid_cache.json"
        # Write v1 flat format — backwards compat should handle it
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
# Group H — _NameIdCache.load_from_dict() (10 tests)
# ===========================================================================

class TestNameIdCacheLoadFromDict:

    @pytest.fixture
    def tmp_cache(self, tmp_path):
        return _NameIdCache(cache_path=tmp_path / "nameid_cache.json")

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
# Group C — CSQAQClient unit tests (7 tests)
# ===========================================================================

def _make_client(tmp_path=None) -> CSQAQClient:
    """Return a CSQAQClient with isolated cache and mocked session."""
    cache = None
    if tmp_path is not None:
        cache = _NameIdCache(cache_path=tmp_path / "nameid_cache.json")
    client = CSQAQClient(
        api_token="TEST_API_TOKEN",
        vip_token="TEST_VIP_TOKEN",
        cache=cache,
        base_url_public="https://api.test.com/api/v1",
        base_url_vip="https://vip.test.com/api/v1",
    )
    client._session = MagicMock()
    return client


class TestCSQAQClientConstructor:

    def test_base_url_public_stored(self):
        client = _make_client()
        assert client.BASE_URL_PUBLIC == "https://api.test.com/api/v1"

    def test_base_url_vip_stored(self):
        client = _make_client()
        assert client.BASE_URL_VIP == "https://vip.test.com/api/v1"

    def test_headers_public_use_api_token(self):
        client = _make_client()
        assert client._headers_public["ApiToken"] == "TEST_API_TOKEN"

    def test_headers_vip_use_vip_token(self):
        client = _make_client()
        assert client._headers_vip["ApiToken"] == "TEST_VIP_TOKEN"


class TestCSQAQClientResolveNameid:

    def test_cache_hit_returns_without_http(self, tmp_path):
        """resolve_item_nameid returns cached value without any HTTP call."""
        client = _make_client(tmp_path)
        client.cache.set("AK-47 | Redline (Field-Tested)", 176923345)
        result = client.resolve_item_nameid("AK-47 | Redline (Field-Tested)")
        assert result == 176923345
        client._session.get.assert_not_called()


class TestCSQAQClientFetchBatchPrices:

    def test_empty_list_returns_empty(self, tmp_path):
        """fetch_batch_prices([]) must return [] without making HTTP calls."""
        client = _make_client(tmp_path)
        result = client.fetch_batch_prices([])
        assert result == []
        client._session.post.assert_not_called()

    def test_successful_response_returns_snapshots(self, tmp_path):
        """A well-formed API response is parsed into OrderBookSnapshot objects."""
        client = _make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": 200,
            "data": {
                "success": {
                    "AK-47 | Redline (Field-Tested)": {
                        "marketHashName": "AK-47 | Redline (Field-Tested)",
                        "buffSellPrice": "65.50",
                        "buffBuyPrice": "64.00",
                        "buffSellNum": "430",
                        "buffBuyNum": "210",
                        "yyypSellPrice": "64.00",
                        "yyypLeasePrice": "0.30",
                    }
                },
                "error": [],
            },
        }
        client._session.post.return_value = mock_resp
        results = client.fetch_batch_prices(["AK-47 | Redline (Field-Tested)"])
        assert len(results) == 1
        snap = results[0]
        assert snap.item_name == "AK-47 | Redline (Field-Tested)"
        assert snap.lowest_ask_price == pytest.approx(65.50)
        assert snap.highest_bid_price == pytest.approx(64.00)


class TestCSQAQClientWhaleAndInventory:

    def test_fetch_whale_ranking_uses_public_url(self, tmp_path):
        """fetch_whale_ranking must POST to BASE_URL_PUBLIC."""
        client = _make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 200, "data": []}
        client._session.post.return_value = mock_resp

        client.fetch_whale_ranking(csqaq_id=12345)

        called_url = client._session.post.call_args[0][0]
        assert "api.test.com" in called_url
        assert "vip.test.com" not in called_url

    def test_fetch_whale_ranking_uses_public_headers(self, tmp_path):
        """fetch_whale_ranking must use _headers_public, not _headers_vip."""
        client = _make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 200, "data": []}
        client._session.post.return_value = mock_resp

        client.fetch_whale_ranking(csqaq_id=12345)

        call_kwargs = client._session.post.call_args[1]
        headers_used = call_kwargs.get("headers", {})
        assert headers_used.get("ApiToken") == "TEST_API_TOKEN"

    def test_fetch_user_inventory_dynamics_uses_public_url(self, tmp_path):
        """fetch_user_inventory_dynamics must POST to BASE_URL_PUBLIC."""
        client = _make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 200, "data": {"trades": []}}
        client._session.post.return_value = mock_resp

        client.fetch_user_inventory_dynamics(task_id=99, target_good_id=12345)

        called_url = client._session.post.call_args[0][0]
        assert "api.test.com" in called_url
        assert "vip.test.com" not in called_url
