"""Named queues schedules speed profiles and traffic quota persistence."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from udl.store.db import connect

default_queue_name = "default"
after_actions = frozenset({"none", "notify", "quit", "sleep", "shutdown"})
repeat_modes = frozenset({"repeat", "once"})
day_presets = frozenset({"daily", "weekdays", "weekend"})


@dataclass
class named_queue:
    name: str
    enabled: bool = True
    max_concurrent: int = 2
    # day_spec: "daily" | "weekdays" | "weekend" | list[int] ISO weekday 0=Mon..6=Sun
    day_spec: str | list[int] = "daily"
    start_time: str | None = None  # HH:MM local; None = always (with end_time None)
    end_time: str | None = None
    repeat_mode: str = "repeat"
    once_completed_at: str | None = None
    after_completion: str = "none"
    speed_profile: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "max_concurrent": self.max_concurrent,
            "day_spec": self.day_spec,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "repeat_mode": self.repeat_mode,
            "once_completed_at": self.once_completed_at,
            "after_completion": self.after_completion,
            "speed_profile": self.speed_profile,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class speed_window:
    day_spec: str | list[int] = "daily"
    start_time: str = "00:00"
    end_time: str = "23:59"
    speed_limit: str = ""  # human e.g. 512K; empty = unlimited

    def to_dict(self) -> dict[str, Any]:
        return {
            "day_spec": self.day_spec,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "speed_limit": self.speed_limit,
        }


@dataclass
class speed_profile:
    name: str
    default_speed_limit: str = ""
    windows: list[speed_window] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "default_speed_limit": self.default_speed_limit,
            "windows": [w.to_dict() for w in self.windows],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class traffic_quota_settings:
    enabled: bool = False
    max_mb_per_hour: float | None = None
    max_mb_per_day: float | None = None
    paused_until: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_mb_per_hour": self.max_mb_per_hour,
            "max_mb_per_day": self.max_mb_per_day,
            "paused_until": self.paused_until,
            "updated_at": self.updated_at,
        }


def _parse_day_spec(raw: Any) -> str | list[int]:
    if isinstance(raw, list):
        out: list[int] = []
        for item in raw:
            try:
                day = int(item)
            except (TypeError, ValueError):
                continue
            if 0 <= day <= 6:
                out.append(day)
        return out or "daily"
    text = str(raw or "daily").strip().lower()
    if text in day_presets:
        return text
    if "," in text:
        parts = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                day = int(part)
            except ValueError:
                continue
            if 0 <= day <= 6:
                parts.append(day)
        return parts or "daily"
    return "daily"


def _encode_day_spec(spec: str | list[int]) -> str:
    if isinstance(spec, list):
        return json.dumps(spec, ensure_ascii=False)
    return str(spec)


def _row_to_queue(row: sqlite3.Row) -> named_queue:
    day_raw = row["day_spec"] or "daily"
    try:
        parsed = json.loads(day_raw)
    except (TypeError, json.JSONDecodeError):
        parsed = day_raw
    return named_queue(
        name=row["name"],
        enabled=bool(row["enabled"]),
        max_concurrent=max(1, int(row["max_concurrent"] or 1)),
        day_spec=_parse_day_spec(parsed),
        start_time=row["start_time"],
        end_time=row["end_time"],
        repeat_mode=row["repeat_mode"] or "repeat",
        once_completed_at=row["once_completed_at"],
        after_completion=row["after_completion"] or "none",
        speed_profile=row["speed_profile"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_profile(row: sqlite3.Row) -> speed_profile:
    windows_raw = json.loads(row["windows_json"] or "[]")
    windows: list[speed_window] = []
    if isinstance(windows_raw, list):
        for item in windows_raw:
            if not isinstance(item, dict):
                continue
            windows.append(
                speed_window(
                    day_spec=_parse_day_spec(item.get("day_spec", "daily")),
                    start_time=str(item.get("start_time") or "00:00"),
                    end_time=str(item.get("end_time") or "23:59"),
                    speed_limit=str(item.get("speed_limit") or ""),
                )
            )
    return speed_profile(
        name=row["name"],
        default_speed_limit=row["default_speed_limit"] or "",
        windows=windows,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def ensure_default_queue(conn: sqlite3.Connection) -> named_queue:
    existing = get_named_queue(conn, default_queue_name)
    if existing is not None:
        return existing
    q = named_queue(name=default_queue_name)
    save_named_queue(conn, q)
    return q


def save_named_queue(conn: sqlite3.Connection, queue: named_queue) -> named_queue:
    if queue.repeat_mode not in repeat_modes:
        raise ValueError(f"invalid repeat_mode: {queue.repeat_mode}")
    if queue.after_completion not in after_actions:
        raise ValueError(f"invalid after_completion: {queue.after_completion}")
    conn.execute(
        """
        insert into named_queues (
          name, enabled, max_concurrent, day_spec, start_time, end_time,
          repeat_mode, once_completed_at, after_completion, speed_profile,
          updated_at
        ) values (?,?,?,?,?,?,?,?,?,?, datetime('now'))
        on conflict(name) do update set
          enabled=excluded.enabled,
          max_concurrent=excluded.max_concurrent,
          day_spec=excluded.day_spec,
          start_time=excluded.start_time,
          end_time=excluded.end_time,
          repeat_mode=excluded.repeat_mode,
          once_completed_at=excluded.once_completed_at,
          after_completion=excluded.after_completion,
          speed_profile=excluded.speed_profile,
          updated_at=datetime('now')
        """,
        (
            queue.name,
            1 if queue.enabled else 0,
            max(1, int(queue.max_concurrent)),
            _encode_day_spec(queue.day_spec),
            queue.start_time,
            queue.end_time,
            queue.repeat_mode,
            queue.once_completed_at,
            queue.after_completion,
            queue.speed_profile,
        ),
    )
    conn.commit()
    loaded = get_named_queue(conn, queue.name)
    assert loaded is not None
    return loaded


def get_named_queue(conn: sqlite3.Connection, name: str) -> named_queue | None:
    row = conn.execute(
        "select * from named_queues where name = ?",
        (name,),
    ).fetchone()
    return _row_to_queue(row) if row else None


def list_named_queues(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: str | Path | None = None,
) -> list[named_queue]:
    own = conn is None
    conn = conn or connect(db_path)
    try:
        ensure_default_queue(conn)
        rows = conn.execute(
            "select * from named_queues order by name collate nocase"
        ).fetchall()
        return [_row_to_queue(row) for row in rows]
    finally:
        if own:
            conn.close()


def delete_named_queue(conn: sqlite3.Connection, name: str) -> bool:
    if name == default_queue_name:
        raise ValueError("cannot delete the default queue")
    cur = conn.execute("delete from named_queues where name = ?", (name,))
    conn.commit()
    return cur.rowcount > 0


def mark_queue_once_completed(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(
        """
        update named_queues
        set once_completed_at = datetime('now'), updated_at = datetime('now')
        where name = ?
        """,
        (name,),
    )
    conn.commit()


def reset_queue_once(conn: sqlite3.Connection, name: str) -> named_queue | None:
    conn.execute(
        """
        update named_queues
        set once_completed_at = null, updated_at = datetime('now')
        where name = ?
        """,
        (name,),
    )
    conn.commit()
    return get_named_queue(conn, name)


def save_speed_profile(conn: sqlite3.Connection, profile: speed_profile) -> speed_profile:
    windows_json = json.dumps([w.to_dict() for w in profile.windows], ensure_ascii=False)
    conn.execute(
        """
        insert into speed_profiles (
          name, default_speed_limit, windows_json, updated_at
        ) values (?,?,?, datetime('now'))
        on conflict(name) do update set
          default_speed_limit=excluded.default_speed_limit,
          windows_json=excluded.windows_json,
          updated_at=datetime('now')
        """,
        (profile.name, profile.default_speed_limit or "", windows_json),
    )
    conn.commit()
    loaded = get_speed_profile(conn, profile.name)
    assert loaded is not None
    return loaded


def get_speed_profile(conn: sqlite3.Connection, name: str) -> speed_profile | None:
    row = conn.execute(
        "select * from speed_profiles where name = ?",
        (name,),
    ).fetchone()
    return _row_to_profile(row) if row else None


def list_speed_profiles(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: str | Path | None = None,
) -> list[speed_profile]:
    own = conn is None
    conn = conn or connect(db_path)
    try:
        rows = conn.execute(
            "select * from speed_profiles order by name collate nocase"
        ).fetchall()
        return [_row_to_profile(row) for row in rows]
    finally:
        if own:
            conn.close()


def delete_speed_profile(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("delete from speed_profiles where name = ?", (name,))
    # clear references
    conn.execute(
        "update named_queues set speed_profile = null where speed_profile = ?",
        (name,),
    )
    conn.commit()
    return cur.rowcount > 0


def get_traffic_quota(conn: sqlite3.Connection) -> traffic_quota_settings:
    row = conn.execute(
        "select * from traffic_quota where id = 1"
    ).fetchone()
    if row is None:
        settings = traffic_quota_settings()
        save_traffic_quota(conn, settings)
        return settings
    return traffic_quota_settings(
        enabled=bool(row["enabled"]),
        max_mb_per_hour=row["max_mb_per_hour"],
        max_mb_per_day=row["max_mb_per_day"],
        paused_until=row["paused_until"],
        updated_at=row["updated_at"],
    )


def save_traffic_quota(
    conn: sqlite3.Connection,
    settings: traffic_quota_settings,
) -> traffic_quota_settings:
    conn.execute(
        """
        insert into traffic_quota (
          id, enabled, max_mb_per_hour, max_mb_per_day, paused_until, updated_at
        ) values (1,?,?,?,?, datetime('now'))
        on conflict(id) do update set
          enabled=excluded.enabled,
          max_mb_per_hour=excluded.max_mb_per_hour,
          max_mb_per_day=excluded.max_mb_per_day,
          paused_until=excluded.paused_until,
          updated_at=datetime('now')
        """,
        (
            1 if settings.enabled else 0,
            settings.max_mb_per_hour,
            settings.max_mb_per_day,
            settings.paused_until,
        ),
    )
    conn.commit()
    return get_traffic_quota(conn)


def record_traffic_bytes(
    conn: sqlite3.Connection,
    nbytes: int,
    *,
    when: datetime | None = None,
) -> None:
    """accumulate nbytes into hour and day buckets."""
    if nbytes <= 0:
        return
    now = when or datetime.now()
    hour_key = now.strftime("%Y-%m-%dT%H")
    day_key = now.strftime("%Y-%m-%d")
    for kind, key in (("hour", hour_key), ("day", day_key)):
        conn.execute(
            """
            insert into traffic_usage (bucket_kind, bucket_key, bytes_used, updated_at)
            values (?,?,?, datetime('now'))
            on conflict(bucket_kind, bucket_key) do update set
              bytes_used = bytes_used + excluded.bytes_used,
              updated_at = datetime('now')
            """,
            (kind, key, int(nbytes)),
        )
    conn.commit()


def traffic_used_bytes(
    conn: sqlite3.Connection,
    *,
    kind: str,
    when: datetime | None = None,
) -> int:
    now = when or datetime.now()
    if kind == "hour":
        key = now.strftime("%Y-%m-%dT%H")
    else:
        key = now.strftime("%Y-%m-%d")
    row = conn.execute(
        """
        select bytes_used from traffic_usage
        where bucket_kind = ? and bucket_key = ?
        """,
        (kind, key),
    ).fetchone()
    return int(row["bytes_used"]) if row else 0


def new_queue_id() -> str:
    return str(uuid.uuid4())
