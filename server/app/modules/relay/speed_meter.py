import threading
import time
from collections import deque
from collections.abc import Callable

WINDOW_SECONDS = 3.0
BUCKET_SECONDS = 0.25
MIN_SPAN_SECONDS = 1.0


class SpeedMeter:
    """Csúszóablakos átviteli sebesség mérő (bájt / másodperc)."""

    def __init__(
        self,
        window_seconds: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._window_seconds = window_seconds
        self._clock = clock
        self._started_at = clock()
        self._buckets: deque[tuple[float, int]] = deque()
        # Az event loop ír bele, a prioritáskezelő külön szálról olvassa.
        self._lock = threading.Lock()

    def add(self, byte_count: int) -> None:
        now = self._clock()

        with self._lock:
            if self._buckets and now - self._buckets[-1][0] < BUCKET_SECONDS:
                bucket_started_at, bucket_bytes = self._buckets[-1]
                self._buckets[-1] = (bucket_started_at, bucket_bytes + byte_count)
            else:
                self._buckets.append((now, byte_count))

            self._prune(now)

    @property
    def speed(self) -> int:
        now = self._clock()

        with self._lock:
            self._prune(now)
            total = sum(byte_count for _, byte_count in self._buckets)

        # Frissen indult streamnél ne az egész ablakkal osszunk, különben az
        # első másodpercekben a valósnál jóval kisebb értéket mutatna.
        elapsed = now - self._started_at
        span = min(self._window_seconds, max(elapsed, MIN_SPAN_SECONDS))

        return int(total / span)

    def _prune(self, now: float) -> None:
        while self._buckets and now - self._buckets[0][0] > self._window_seconds:
            self._buckets.popleft()
