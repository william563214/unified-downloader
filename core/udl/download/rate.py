"""Token-bucket rate limiters for the shared download engine."""

from __future__ import annotations

import time
from threading import Lock


class rate_limiter:
    """thread-safe token bucket. bps is bytes per second."""

    def __init__(self, bps: float) -> None:
        self.bps = float(bps)
        self.tokens = float(bps)
        self.updated = time.monotonic()
        self._lock = Lock()

    def acquire(self, nbytes: int) -> None:
        if self.bps <= 0 or nbytes <= 0:
            return
        remaining = float(nbytes)
        while remaining > 0:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self.updated
                self.updated = now
                self.tokens = min(self.bps, self.tokens + elapsed * self.bps)
                take = min(self.tokens, remaining)
                self.tokens -= take
                remaining -= take
                if remaining <= 0:
                    return
                wait = remaining / self.bps
            # sleep outside lock so other waiters can refill cooperatively
            time.sleep(min(max(wait, 0.001), 0.05))


# process-wide overall limiter (shared across jobs in one udl process)
_global_limiter: rate_limiter | None = None
_global_bps: int | None = None
_global_lock = Lock()


def configure_global_limiter(bps: int | None) -> None:
    """install or clear the process-wide speed cap."""
    global _global_limiter, _global_bps
    with _global_lock:
        if bps is None or bps <= 0:
            _global_limiter = None
            _global_bps = None
        else:
            _global_limiter = rate_limiter(bps)
            _global_bps = bps


def global_limiter() -> rate_limiter | None:
    return _global_limiter


def make_job_limiter(bps: int | None) -> rate_limiter | None:
    if bps is None or bps <= 0:
        return None
    return rate_limiter(bps)


def throttle(nbytes: int, job: rate_limiter | None = None) -> None:
    """consume nbytes from global and optional job limiters."""
    g = _global_limiter
    if g is not None:
        g.acquire(nbytes)
    if job is not None:
        job.acquire(nbytes)
