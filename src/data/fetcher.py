"""兼容层：src.data.fetcher → src.acquisition.fetcher"""
from src.acquisition.fetcher import *        # noqa: F401, F403
from src.acquisition.http_client import *    # noqa: F401, F403
from src.acquisition.initializer import *   # noqa: F401, F403
from src.acquisition.exceptions import *    # noqa: F401, F403
# Explicit imports for underscore-prefixed names (not exported by *)
from src.acquisition.cache import _NameIdCache  # noqa: F401
from src.acquisition.http_client import (  # noqa: F401
    _USER_AGENTS,
    _LISTING_URL,
    _ORDERBOOK_URL,
    _NAMEID_REGEX,
    _RETRY_MAX,
    _RETRY_BASE_S,
    _SLEEP_MIN_S,
    _SLEEP_MAX_S,
    _HTML_FETCH_DELAY_MIN_S,
    _HTML_FETCH_DELAY_MAX_S,
    _HTML_EXTRA_HEADERS,
)
