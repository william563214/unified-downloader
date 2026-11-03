"""Per-host AIMD adaptive connection concurrency.

Inspired by OpenSelena/omniget ``adaptive_concurrency`` (see NOTICE):
ramp up slowly on healthy responses, cut back fast on pressure. Never used to
evade site rate limits or quotas — only reduces pressure when the site pushes
back, and never exceeds the user-configured max.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

# lowercase constants per project convention
min_connections = 1
max_connections_hard = 32
default_initial_connections = 8
default_per_host_max = 8
default_global_max = 24
# healthy responses needed before additive increase
success_streak_for_increase = 3
# exponential backoff
default_backoff_base = 1.0
default_backoff_cap = 60.0

pressure_http_statuses = frozenset({429, 503})
pressure_reasons = frozenset(
    {
        "http_429",
        "http_503",
        "timeout",
        "connection_reset",
        "connection_error",
    }
)


def host_key_from_url(url: str | None) -> str:
    """canonicalize a download URL into a host bucket key."""
    if not url:
        return "unknown"
    try:
        parsed = urlparse(url)
    except ValueError:
        return "unknown"
    host = (parsed.hostname or "").lower().strip()
    if not host:
        return "unknown"
    if host.startswith("www."):
        host = host[4:]
    # group common CDN families so pressure on one node cools the bucket
    if "bilivideo.com" in host or host.endswith("bilibili.com"):
        return "bilibili"
    if "googlevideo.com" in host or host.endswith("youtube.com") or host == "youtu.be":
        return "youtube"
    if "cdninstagram.com" in host or host.endswith("instagram.com"):
        return "instagram"
    return host


def parse_retry_after(value: str | None) -> float | None:
    """parse Retry-After header (seconds or HTTP-date) into delay seconds."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return float(max(0, int(text)))
    # HTTP-date
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(text)
        if dt is None:
            return None
        delay = dt.timestamp() - time.time()
        return max(0.0, float(delay))
    except (TypeError, ValueError, OverflowError):
        return None


def retry_delay(
    attempt: int,
    *,
    retry_after: float | None = None,
    base: float = default_backoff_base,
    cap: float = default_backoff_cap,
) -> float:
    """exponential backoff with jitter; Retry-After wins when present."""
    if retry_after is not None and retry_after >= 0:
        return float(retry_after) + random.uniform(0.0, 0.5)
    exp = min(cap, base * (2 ** max(0, attempt)))
    jitter = random.uniform(0.0, exp * 0.25)
    return min(cap, exp + jitter)


def classify_pressure(
    *,
    status: int | None = None,
    exc: BaseException | None = None,
) -> str | None:
    """return a pressure reason string or None when the response looks healthy."""
    if status in pressure_http_statuses:
        return f"http_{status}"
    if exc is None:
        return None
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timed out" in msg or "timeout" in msg:
        return "timeout"
    if (
        "connectionreset" in name
        or "connection aborted" in msg
        or "connection reset" in msg
        or "broken pipe" in msg
    ):
        return "connection_reset"
    if "connection" in name or "connection error" in msg:
        return "connection_error"
    return None


@dataclass
class host_limit_state:
    """learned / live concurrency for one host bucket."""

    host: str
    limit: int
    success_streak: int = 0
    pressure_count: int = 0
    updated_at: float = field(default_factory=time.time)

    def clamp(self, ceiling: int) -> int:
        ceiling = max(min_connections, min(int(ceiling), max_connections_hard))
        self.limit = max(min_connections, min(int(self.limit), ceiling))
        return self.limit


@dataclass
class adaptive_tuner:
    """process-wide AIMD tuner with optional SQLite persistence hooks."""

    initial: int = default_initial_connections
    per_host_max: int = default_per_host_max
    global_max: int = default_global_max
    enabled: bool = True
    hosts: dict[str, host_limit_state] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _active_downloads: int = field(default=0, repr=False)
    _persist: Any = field(default=None, repr=False)
    _dirty: set[str] = field(default_factory=set, repr=False)

    def configure(
        self,
        *,
        initial: int | None = None,
        per_host_max: int | None = None,
        global_max: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        with self._lock:
            if initial is not None:
                self.initial = max(min_connections, min(int(initial), max_connections_hard))
            if per_host_max is not None:
                self.per_host_max = max(
                    min_connections, min(int(per_host_max), max_connections_hard)
                )
            if global_max is not None:
                self.global_max = max(
                    min_connections, min(int(global_max), max_connections_hard * 4)
                )
            if enabled is not None:
                self.enabled = bool(enabled)

    def set_persist(self, store: Any | None) -> None:
        """attach a host_limits store (load/save/reset API)."""
        with self._lock:
            self._persist = store
            if store is not None:
                self._load_from_store()

    def _load_from_store(self) -> None:
        store = self._persist
        if store is None:
            return
        try:
            rows = store.load_all()
        except Exception:  # noqa: BLE001 — corrupt DB should not break downloads
            return
        for row in rows:
            host = str(row.get("host") or "").strip()
            if not host:
                continue
            limit = int(row.get("limit") or self.initial)
            self.hosts[host] = host_limit_state(
                host=host,
                limit=limit,
                success_streak=int(row.get("success_streak") or 0),
                pressure_count=int(row.get("pressure_count") or 0),
                updated_at=float(row.get("updated_at") or time.time()),
            )

    def _flush_host(self, host: str) -> None:
        store = self._persist
        state = self.hosts.get(host)
        if store is None or state is None:
            return
        try:
            store.save_one(
                host=state.host,
                limit=state.limit,
                success_streak=state.success_streak,
                pressure_count=state.pressure_count,
            )
            self._dirty.discard(host)
        except Exception:  # noqa: BLE001
            pass

    def acquire_download(self) -> None:
        with self._lock:
            self._active_downloads += 1

    def release_download(self) -> None:
        with self._lock:
            self._active_downloads = max(0, self._active_downloads - 1)

    def active_downloads(self) -> int:
        with self._lock:
            return self._active_downloads

    def per_download_ceiling(self) -> int:
        """share global connection budget across active downloads."""
        with self._lock:
            active = max(1, self._active_downloads)
            shared = max(min_connections, self.global_max // active)
            return min(shared, self.per_host_max, max_connections_hard)

    def _ensure(self, host: str) -> host_limit_state:
        key = host or "unknown"
        state = self.hosts.get(key)
        if state is None:
            state = host_limit_state(host=key, limit=self.initial)
            self.hosts[key] = state
        return state

    def ceiling_for(self, host: str | None = None) -> int:
        del host  # host-specific ceilings can be added later
        return min(self.per_host_max, self.per_download_ceiling(), max_connections_hard)

    def suggest(
        self,
        host: str | None,
        *,
        ceiling: int | None = None,
        initial: int | None = None,
    ) -> int:
        """next concurrency for host; clamped to user max and global budget."""
        key = host_key_from_url(host) if host and "://" in host else (host or "unknown")
        with self._lock:
            if not self.enabled:
                cap = ceiling if ceiling is not None else self.per_host_max
                start = initial if initial is not None else self.initial
                return max(min_connections, min(int(start), int(cap), max_connections_hard))
            state = self._ensure(key)
            start = initial if initial is not None else self.initial
            if state.limit <= 0:
                state.limit = start
            cap = self.ceiling_for(key)
            if ceiling is not None:
                cap = min(cap, max(min_connections, int(ceiling)))
            return state.clamp(cap)

    def record_success(self, host: str | None, *, n: int = 1) -> int:
        """AIMD additive increase after a streak of healthy responses."""
        key = host_key_from_url(host) if host and "://" in str(host) else (host or "unknown")
        with self._lock:
            if not self.enabled:
                return self.suggest(key)
            state = self._ensure(key)
            state.success_streak += max(1, int(n))
            state.updated_at = time.time()
            cap = self.ceiling_for(key)
            if state.success_streak >= success_streak_for_increase and state.limit < cap:
                state.limit = min(cap, state.limit + 1)
                state.success_streak = 0
            state.clamp(cap)
            self._dirty.add(key)
            self._flush_host(key)
            return state.limit

    def record_pressure(self, host: str | None, reason: str = "connection_error") -> int:
        """AIMD multiplicative decrease on 429/503/reset/timeout."""
        key = host_key_from_url(host) if host and "://" in str(host) else (host or "unknown")
        reason = reason if reason in pressure_reasons else "connection_error"
        with self._lock:
            if not self.enabled:
                return self.suggest(key)
            state = self._ensure(key)
            state.pressure_count += 1
            state.success_streak = 0
            state.limit = max(min_connections, state.limit // 2)
            state.updated_at = time.time()
            state.clamp(self.ceiling_for(key))
            self._dirty.add(key)
            self._flush_host(key)
            return state.limit

    def current(self, host: str | None) -> int:
        key = host_key_from_url(host) if host and "://" in str(host) else (host or "unknown")
        with self._lock:
            state = self.hosts.get(key)
            if state is None:
                return self.suggest(key)
            return state.clamp(self.ceiling_for(key))

    def list_limits(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = []
            for state in sorted(self.hosts.values(), key=lambda s: s.host):
                rows.append(
                    {
                        "host": state.host,
                        "limit": state.limit,
                        "success_streak": state.success_streak,
                        "pressure_count": state.pressure_count,
                        "updated_at": state.updated_at,
                    }
                )
            return rows

    def reset(self, host: str | None = None) -> int:
        """clear learned limits (one host or all); returns removed count."""
        with self._lock:
            removed = 0
            if host:
                key = host_key_from_url(host) if "://" in host else host
                if key in self.hosts:
                    del self.hosts[key]
                    removed = 1
                store = self._persist
                if store is not None:
                    try:
                        store.delete_one(key)
                    except Exception:  # noqa: BLE001
                        pass
                return removed
            removed = len(self.hosts)
            self.hosts.clear()
            self._dirty.clear()
            store = self._persist
            if store is not None:
                try:
                    store.delete_all()
                except Exception:  # noqa: BLE001
                    pass
            return removed


_tuner: adaptive_tuner | None = None
_tuner_lock = threading.Lock()


def global_tuner() -> adaptive_tuner:
    """process-wide singleton shared by RequestHandler downloads and segments."""
    global _tuner
    with _tuner_lock:
        if _tuner is None:
            _tuner = adaptive_tuner()
            try:
                from udl.store.host_limits import host_limits_store

                _tuner.set_persist(host_limits_store())
            except Exception:  # noqa: BLE001 — DB optional at import time
                pass
        return _tuner


def reset_global_tuner() -> None:
    """test / settings helper: drop the process-wide tuner."""
    global _tuner
    with _tuner_lock:
        _tuner = None


def configure_adaptive_from_network(settings: Any) -> adaptive_tuner:
    """sync tuner knobs from network_settings."""
    tuner = global_tuner()
    tuner.configure(
        initial=int(getattr(settings, "connections", default_initial_connections) or 8),
        per_host_max=int(
            getattr(settings, "per_host_connections", default_per_host_max) or 8
        ),
        global_max=int(
            getattr(settings, "global_max_connections", default_global_max) or 24
        ),
        enabled=bool(getattr(settings, "adaptive_concurrency", True)),
    )
    return tuner


class active_download_guard:
    """count a download against the global connection budget."""

    def __init__(self, tuner: adaptive_tuner | None = None) -> None:
        self._tuner = tuner or global_tuner()

    def __enter__(self) -> active_download_guard:
        self._tuner.acquire_download()
        return self

    def __exit__(self, *exc: object) -> None:
        self._tuner.release_download()
