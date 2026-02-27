"""
Polling scheduler for periodic Steam Market order-book snapshots.

Typical usage (background thread)::

    import threading
    from src.acquisition.http_client import SteamOrderBookFetcher
    from src.acquisition.scheduler import PollingScheduler

    fetcher = SteamOrderBookFetcher()
    stop = threading.Event()
    sched = PollingScheduler(
        fetcher,
        ["AK-47 | Redline (Field-Tested)"],
        on_snapshot=lambda snaps: print(snaps),
    )
    t = threading.Thread(target=sched.run_forever, kwargs={"stop_event": stop}, daemon=True)
    t.start()
    # ... later ...
    stop.set()
    t.join()
"""

import logging
import threading
import time
from typing import Callable, List, Optional

from src.acquisition.http_client import SteamOrderBookFetcher
from src.acquisition.models import OrderBook

logger = logging.getLogger(__name__)


class PollingScheduler:
    """Periodically polls the Steam Market for order-book snapshots.

    Args:
        fetcher: A :class:`~src.acquisition.http_client.SteamOrderBookFetcher` instance.
        item_names: List of market hash names to poll.
        interval_seconds: Polling interval in seconds (default 750 = 12.5 min).
        on_snapshot: Optional callback invoked after each successful poll with
                     the list of :class:`~src.acquisition.models.OrderBook` results.
    """

    DEFAULT_INTERVAL: float = 750.0  # 12.5 minutes — midpoint of 10–15 min range

    def __init__(
        self,
        fetcher: SteamOrderBookFetcher,
        item_names: List[str],
        interval_seconds: float = DEFAULT_INTERVAL,
        on_snapshot: Optional[Callable[[List[OrderBook]], None]] = None,
    ) -> None:
        self._fetcher = fetcher
        self._item_names = list(item_names)
        self._interval = interval_seconds
        self._on_snapshot = on_snapshot
        self._last_poll_time: Optional[float] = None

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def poll_once(self) -> List[OrderBook]:
        """Execute one full polling cycle immediately.

        Fetches order books for all configured items, invokes the
        ``on_snapshot`` callback (if set), and updates
        :attr:`last_poll_time`.

        Returns:
            List of :class:`~src.acquisition.models.OrderBook` snapshots.
        """
        logger.info("Polling %d items …", len(self._item_names))
        snapshots = self._fetcher.fetch_multiple(self._item_names)
        self._last_poll_time = time.time()
        if self._on_snapshot is not None:
            try:
                self._on_snapshot(snapshots)
            except Exception as exc:  # noqa: BLE001
                logger.warning("on_snapshot callback raised: %s", exc)
        return snapshots

    def run_forever(
        self,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        """Block and poll repeatedly until *stop_event* is set (or forever).

        The loop:
        1. Calls :meth:`poll_once`.
        2. Waits for the remaining interval time (checking *stop_event* every
           second so it responds promptly to stop requests).
        3. Repeats.

        Args:
            stop_event: A :class:`threading.Event`; when set the loop exits
                        cleanly after the current sleep tick.
        """
        while True:
            if stop_event is not None and stop_event.is_set():
                logger.info("Stop event received; exiting polling loop.")
                break

            poll_start = time.time()
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("Unhandled error in poll_once: %s", exc)

            elapsed = time.time() - poll_start
            remaining = self._interval - elapsed
            logger.debug(
                "Poll took %.2fs; sleeping %.2fs until next cycle.", elapsed, remaining
            )

            # Wait for the remaining interval, waking every second to check stop_event
            waited = 0.0
            while waited < remaining:
                if stop_event is not None and stop_event.is_set():
                    logger.info("Stop event received during sleep; exiting.")
                    return
                sleep_chunk = min(1.0, remaining - waited)
                time.sleep(sleep_chunk)
                waited += sleep_chunk

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def last_poll_time(self) -> Optional[float]:
        """Unix timestamp of the most recent completed poll, or ``None``."""
        return self._last_poll_time
