"""Evaluate named-queue schedules speed profiles and traffic quota."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from udl.network import parse_speed_limit
from udl.store.db import connect
from udl.store.schedule import (
    default_queue_name,
    ensure_default_queue,
    get_named_queue,
    get_traffic_quota,
    list_named_queues,
    list_speed_profiles,
    mark_queue_once_completed,
    named_queue,
    record_traffic_bytes,
    save_traffic_quota,
    speed_profile,
    traffic_used_bytes,
)


def _parse_hhmm(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    parts = str(text).strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def day_matches(day_spec: str | list[int], weekday: int) -> bool:
    """weekday is ISO-style 0=Mon .. 6=Sun (datetime.weekday())."""
    if isinstance(day_spec, list):
        return weekday in day_spec
    preset = str(day_spec or "daily").lower()
    if preset == "daily":
        return True
    if preset == "weekdays":
        return weekday <= 4
    if preset == "weekend":
        return weekday >= 5
    return True


def time_in_window(
    now: datetime,
    start_time: str | None,
    end_time: str | None,
) -> bool:
    """True when now is inside [start, end). Cross-midnight supported."""
    start = _parse_hhmm(start_time)
    end = _parse_hhmm(end_time)
    if start is None and end is None:
        return True
    if start is None:
        start = (0, 0)
    if end is None:
        end = (23, 59)
    minutes = now.hour * 60 + now.minute
    start_m = start[0] * 60 + start[1]
    end_m = end[0] * 60 + end[1]
    if start_m == end_m:
        # treat as full day
        return True
    if start_m < end_m:
        return start_m <= minutes < end_m
    # overnight window e.g. 22:00-06:00
    return minutes >= start_m or minutes < end_m


def queue_window_active(queue: named_queue, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if not queue.enabled:
        return False
    if queue.repeat_mode == "once" and queue.once_completed_at:
        return False
    if not day_matches(queue.day_spec, now.weekday()):
        return False
    return time_in_window(now, queue.start_time, queue.end_time)


def resolve_profile_speed_bps(
    profile: speed_profile | None,
    now: datetime | None = None,
) -> int | None:
    """pick the tightest matching window speed else profile default."""
    now = now or datetime.now()
    if profile is None:
        return None
    matched: list[int] = []
    for window in profile.windows:
        if not day_matches(window.day_spec, now.weekday()):
            continue
        if not time_in_window(now, window.start_time, window.end_time):
            continue
        bps = parse_speed_limit(window.speed_limit or None)
        if bps is not None:
            matched.append(bps)
    if matched:
        return min(matched)
    return parse_speed_limit(profile.default_speed_limit or None)


def next_quota_resume_at(now: datetime | None = None, *, kind: str = "hour") -> datetime:
    now = now or datetime.now()
    if kind == "day":
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return nxt
    nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return nxt


@dataclass
class quota_status:
    enabled: bool = False
    exceeded: bool = False
    paused: bool = False
    paused_until: str | None = None
    hour_used_mb: float = 0.0
    day_used_mb: float = 0.0
    hour_limit_mb: float | None = None
    day_limit_mb: float | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "exceeded": self.exceeded,
            "paused": self.paused,
            "paused_until": self.paused_until,
            "hour_used_mb": round(self.hour_used_mb, 3),
            "day_used_mb": round(self.day_used_mb, 3),
            "hour_limit_mb": self.hour_limit_mb,
            "day_limit_mb": self.day_limit_mb,
            "reason": self.reason,
        }


def evaluate_quota(
    conn,
    now: datetime | None = None,
    *,
    auto_resume: bool = True,
) -> quota_status:
    now = now or datetime.now()
    settings = get_traffic_quota(conn)
    hour_used = traffic_used_bytes(conn, kind="hour", when=now)
    day_used = traffic_used_bytes(conn, kind="day", when=now)
    status = quota_status(
        enabled=settings.enabled,
        hour_used_mb=hour_used / (1000 * 1000),
        day_used_mb=day_used / (1000 * 1000),
        hour_limit_mb=settings.max_mb_per_hour,
        day_limit_mb=settings.max_mb_per_day,
        paused_until=settings.paused_until,
    )
    if not settings.enabled:
        return status

    # clear pause when window rolled over
    if settings.paused_until and auto_resume:
        try:
            resume_at = datetime.fromisoformat(settings.paused_until)
        except ValueError:
            resume_at = None
        if resume_at is not None and now >= resume_at:
            settings.paused_until = None
            save_traffic_quota(conn, settings)
            status.paused_until = None

    if settings.paused_until:
        try:
            resume_at = datetime.fromisoformat(settings.paused_until)
        except ValueError:
            resume_at = None
        if resume_at is not None and now < resume_at:
            status.paused = True
            status.exceeded = True
            status.reason = "quota_paused"
            return status

    hour_limit = settings.max_mb_per_hour
    day_limit = settings.max_mb_per_day
    hour_cap = (
        int(hour_limit * 1000 * 1000) if hour_limit is not None and hour_limit > 0 else None
    )
    day_cap = (
        int(day_limit * 1000 * 1000) if day_limit is not None and day_limit > 0 else None
    )
    if hour_cap is not None and hour_used >= hour_cap:
        resume = next_quota_resume_at(now, kind="hour")
        settings.paused_until = resume.isoformat(timespec="seconds")
        save_traffic_quota(conn, settings)
        status.exceeded = True
        status.paused = True
        status.paused_until = settings.paused_until
        status.reason = "hour_quota_exceeded"
        return status
    if day_cap is not None and day_used >= day_cap:
        resume = next_quota_resume_at(now, kind="day")
        settings.paused_until = resume.isoformat(timespec="seconds")
        save_traffic_quota(conn, settings)
        status.exceeded = True
        status.paused = True
        status.paused_until = settings.paused_until
        status.reason = "day_quota_exceeded"
        return status
    return status


@dataclass
class queue_runtime:
    name: str
    active: bool
    max_concurrent: int
    after_completion: str
    speed_limit_bps: int | None
    speed_limit: str | None
    speed_profile: str | None
    reason: str | None = None
    queue: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "active": self.active,
            "max_concurrent": self.max_concurrent,
            "after_completion": self.after_completion,
            "speed_limit_bps": self.speed_limit_bps,
            "speed_limit": self.speed_limit,
            "speed_profile": self.speed_profile,
            "reason": self.reason,
            "queue": self.queue,
        }


@dataclass
class scheduler_status:
    now: str
    active: bool
    quota: quota_status
    queues: list[queue_runtime]
    global_speed_limit_bps: int | None
    global_speed_limit: str | None
    default_queue: str = default_queue_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "now": self.now,
            "active": self.active,
            "quota": self.quota.to_dict(),
            "queues": [q.to_dict() for q in self.queues],
            "global_speed_limit_bps": self.global_speed_limit_bps,
            "global_speed_limit": self.global_speed_limit,
            "default_queue": self.default_queue,
        }


def _format_bps(bps: int | None) -> str | None:
    if bps is None or bps <= 0:
        return None
    if bps >= 1000**2 and bps % (1000**2) == 0:
        return f"{bps // (1000**2)}M"
    if bps >= 1000 and bps % 1000 == 0:
        return f"{bps // 1000}K"
    return str(bps)


def evaluate_scheduler(
    conn=None,
    *,
    now: datetime | None = None,
    db_path: str | Path | None = None,
    base_speed_limit: str | None = None,
) -> scheduler_status:
    own = conn is None
    conn = conn or connect(db_path)
    try:
        ensure_default_queue(conn)
        now = now or datetime.now()
        quota = evaluate_quota(conn, now)
        profiles = {p.name: p for p in list_speed_profiles(conn)}
        base_bps = parse_speed_limit(base_speed_limit)
        runtimes: list[queue_runtime] = []
        for queue in list_named_queues(conn):
            active = queue_window_active(queue, now)
            reason = None
            if not queue.enabled:
                reason = "disabled"
            elif queue.repeat_mode == "once" and queue.once_completed_at:
                reason = "once_completed"
            elif not day_matches(queue.day_spec, now.weekday()):
                reason = "outside_days"
            elif not time_in_window(now, queue.start_time, queue.end_time):
                reason = "outside_window"
            if quota.exceeded:
                active = False
                reason = quota.reason or "quota_exceeded"
            profile = profiles.get(queue.speed_profile) if queue.speed_profile else None
            profile_bps = resolve_profile_speed_bps(profile, now)
            candidates = [b for b in (base_bps, profile_bps) if b is not None and b > 0]
            effective = min(candidates) if candidates else None
            runtimes.append(
                queue_runtime(
                    name=queue.name,
                    active=active,
                    max_concurrent=queue.max_concurrent,
                    after_completion=queue.after_completion,
                    speed_limit_bps=effective,
                    speed_limit=_format_bps(effective),
                    speed_profile=queue.speed_profile,
                    reason=None if active else reason,
                    queue=queue.to_dict(),
                )
            )
        any_active = any(r.active for r in runtimes) and not quota.exceeded
        # global effective = tightest among active queues that have a limit
        active_limits = [
            r.speed_limit_bps
            for r in runtimes
            if r.active and r.speed_limit_bps is not None
        ]
        global_bps = min(active_limits) if active_limits else base_bps
        return scheduler_status(
            now=now.isoformat(timespec="seconds"),
            active=any_active,
            quota=quota,
            queues=runtimes,
            global_speed_limit_bps=global_bps,
            global_speed_limit=_format_bps(global_bps),
        )
    finally:
        if own:
            conn.close()


def complete_once_if_needed(conn, queue_name: str) -> None:
    queue = get_named_queue(conn, queue_name)
    if queue is None:
        return
    if queue.repeat_mode == "once" and not queue.once_completed_at:
        mark_queue_once_completed(conn, queue_name)


def note_traffic(nbytes: int, *, db_path: str | Path | None = None) -> None:
    """record downloaded bytes for quota accounting."""
    if nbytes <= 0:
        return
    conn = connect(db_path)
    try:
        record_traffic_bytes(conn, nbytes)
        evaluate_quota(conn)
    finally:
        conn.close()


def export_scheduler_config(
    conn=None,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    own = conn is None
    conn = conn or connect(db_path)
    try:
        ensure_default_queue(conn)
        return {
            "queues": [q.to_dict() for q in list_named_queues(conn)],
            "profiles": [p.to_dict() for p in list_speed_profiles(conn)],
            "quota": get_traffic_quota(conn).to_dict(),
            "status": evaluate_scheduler(conn).to_dict(),
        }
    finally:
        if own:
            conn.close()
