"""Token-bucket rate limiters for the shared download engine."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock

# lowercase constants
traffic_flush_bytes = 256 * 1024
traffic_flush_secs = 2.0


class rate_limiter:
    """thread-safe token bucket. bps is bytes per second."""

    def __init__(self, bps: float) -> None:
        self.bps = float(bps)
        self.tokens = float(bps)
        self.updated = time.monotonic()
        self._lock = Lock()

    def set_bps(self, bps: float) -> None:
        """hot-swap the bucket rate without dropping waiters."""
        with self._lock:
            self.bps = float(bps)
            self.tokens = min(self.tokens, self.bps)

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

# traffic accounting hooks (quota); flushed in batches to SQLite
_traffic_pending = 0
_traffic_last_flush = 0.0
_traffic_recorder: Callable[[int], None] | None = None
_traffic_lock = Lock()


def configure_global_limiter(bps: int | None) -> None:
    """install or clear the process-wide speed cap."""
    global _global_limiter, _global_bps
    with _global_lock:
        if bps is None or bps <= 0:
            _global_limiter = None
            _global_bps = None
        elif _global_limiter is not None and _global_bps == bps:
            return
        elif _global_limiter is not None:
            _global_limiter.set_bps(bps)
            _global_bps = bps
        else:
            _global_limiter = rate_limiter(bps)
            _global_bps = bps


def global_limiter() -> rate_limiter | None:
    return _global_limiter


def make_job_limiter(bps: int | None) -> rate_limiter | None:
    if bps is None or bps <= 0:
        return None
    return rate_limiter(bps)


def set_traffic_recorder(callback: Callable[[int], None] | None) -> None:
    """install a callback that receives aggregated downloaded bytes."""
    global _traffic_recorder
    with _traffic_lock:
        _traffic_recorder = callback


def _flush_traffic(force: bool = False) -> None:
    global _traffic_pending, _traffic_last_flush
    with _traffic_lock:
        pending = _traffic_pending
        recorder = _traffic_recorder
        now = time.monotonic()
        if pending <= 0 or recorder is None:
            return
        if (
            not force
            and pending < traffic_flush_bytes
            and (now - _traffic_last_flush) < traffic_flush_secs
        ):
            return
        _traffic_pending = 0
        _traffic_last_flush = now
    try:
        recorder(pending)
    except Exception:
        # quota accounting must never break downloads
        pass


def note_downloaded_bytes(nbytes: int) -> None:
    """accumulate downloaded bytes for traffic quota via the rate layer."""
    if nbytes <= 0:
        return
    global _traffic_pending
    with _traffic_lock:
        _traffic_pending += int(nbytes)
    _flush_traffic()


def flush_traffic_accounting() -> None:
    _flush_traffic(force=True)


def throttle(nbytes: int, job: rate_limiter | None = None) -> None:
    """consume nbytes from global and optional job limiters; record traffic."""
    note_downloaded_bytes(nbytes)
    g = _global_limiter
    if g is not None:
        g.acquire(nbytes)
    if job is not None:
        job.acquire(nbytes)
