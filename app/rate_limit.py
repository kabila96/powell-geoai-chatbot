"""Simple in-memory sliding-window rate limiter (per client IP).

Good for a single-process deployment. For several workers or instances, replace the store with
Redis (see README, "Optional future improvements").
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, per_minute: int, per_day: int):
        self.limits = [(60, per_minute), (86_400, per_day)]
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds) and record the hit when allowed."""
        now = time.time()
        with self._lock:
            hits = self._hits[key]
            longest = max(window for window, _ in self.limits)
            while hits and now - hits[0] > longest:
                hits.popleft()
            for window, limit in self.limits:
                in_window = [t for t in hits if now - t <= window]
                if limit > 0 and len(in_window) >= limit:
                    retry_after = int(window - (now - in_window[0])) + 1
                    return False, retry_after
            hits.append(now)
            if len(self._hits) > 50_000:  # crude memory guard against IP floods
                self._hits.clear()
            return True, 0
