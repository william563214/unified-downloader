"""CLI helpers for `udl queue schedule ...`."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from udl.scheduler import evaluate_scheduler, export_scheduler_config
from udl.store.schedule import (
    after_actions,
    delete_named_queue,
    delete_speed_profile,
    ensure_default_queue,
    get_named_queue,
    get_speed_profile,
    get_traffic_quota,
    list_named_queues,
    list_speed_profiles,
    mark_queue_once_completed,
    named_queue,
    reset_queue_once,
    save_named_queue,
    save_speed_profile,
    save_traffic_quota,
    speed_profile,
    speed_window,
)

re_window = re.compile(
    r"^(?P<days>[^:]+):(?P<start>\d{1,2}:\d{2})-(?P<end>\d{1,2}:\d{2})"
    r"(?::(?P<limit>.+))?$"
)


def add_schedule_parser(queue_sub: argparse._SubParsersAction) -> None:
    p_sched = queue_sub.add_parser(
        "schedule",
        help="named queues time windows speed profiles and traffic quota",
    )
    sched_sub = p_sched.add_subparsers(dest="schedule_command", required=True)

    p_list = sched_sub.add_parser("list", help="list named queues and live status")
    p_list.add_argument("--json", action="store_true")

    p_show = sched_sub.add_parser("show", help="show one named queue")
    p_show.add_argument("name")
    p_show.add_argument("--json", action="store_true")

    p_set = sched_sub.add_parser("set", help="create or update a named queue schedule")
    p_set.add_argument("name", help="queue name")
    p_set.add_argument(
        "--enabled",
        dest="enabled",
        default=None,
        action=argparse.BooleanOptionalAction,
        help="enable or disable the queue (--enabled / --no-enabled)",
    )
    p_set.add_argument("--max-concurrent", type=int, default=None)
    p_set.add_argument(
        "--days",
        default=None,
        help="daily | weekdays | weekend | comma ISO weekdays 0=Mon..6=Sun",
    )
    p_set.add_argument("--start", default=None, help="daily start HH:MM (local)")
    p_set.add_argument("--end", default=None, help="daily end HH:MM (local)")
    p_set.add_argument(
        "--always",
        action="store_true",
        help="clear start/end so the queue runs whenever enabled",
    )
    p_set.add_argument(
        "--repeat",
        dest="repeat_mode",
        action="store_const",
        const="repeat",
        default=None,
    )
    p_set.add_argument(
        "--once",
        dest="repeat_mode",
        action="store_const",
        const="once",
    )
    p_set.add_argument(
        "--after",
        choices=sorted(after_actions),
        default=None,
        help="action when the queue finishes all jobs",
    )
    p_set.add_argument(
        "--speed-profile",
        default=None,
        help="attach a named speed profile (empty string clears)",
    )
    p_set.add_argument("--json", action="store_true")

    p_delete = sched_sub.add_parser("delete", help="delete a named queue (not default)")
    p_delete.add_argument("name")
    p_delete.add_argument("--json", action="store_true")

    p_reset = sched_sub.add_parser(
        "reset-once",
        help="clear run-once completion so the queue can fire again",
    )
    p_reset.add_argument("name")
    p_reset.add_argument("--json", action="store_true")

    p_complete = sched_sub.add_parser(
        "complete-once",
        help="mark a run-once queue as completed for this cycle",
    )
    p_complete.add_argument("name")
    p_complete.add_argument("--json", action="store_true")

    p_status = sched_sub.add_parser(
        "status",
        help="evaluate which queues are active right now",
    )
    p_status.add_argument("--json", action="store_true")
    p_status.add_argument(
        "--speed-limit",
        default=None,
        help="optional base speed limit to combine with profiles",
    )

    p_export = sched_sub.add_parser("export", help="dump queues profiles quota status")
    p_export.add_argument("--json", action="store_true")

    p_profile = sched_sub.add_parser("profile", help="manage time-based speed profiles")
    profile_sub = p_profile.add_subparsers(dest="profile_command", required=True)

    p_prof_list = profile_sub.add_parser("list", help="list speed profiles")
    p_prof_list.add_argument("--json", action="store_true")

    p_prof_show = profile_sub.add_parser("show", help="show one speed profile")
    p_prof_show.add_argument("name")
    p_prof_show.add_argument("--json", action="store_true")

    p_prof_set = profile_sub.add_parser("set", help="create or update a speed profile")
    p_prof_set.add_argument("name")
    p_prof_set.add_argument(
        "--default-limit",
        default=None,
        help="fallback speed e.g. 2M (empty = unlimited)",
    )
    p_prof_set.add_argument(
        "--window",
        action="append",
        default=[],
        metavar="SPEC",
        help=(
            "window SPEC as days:start-end:limit "
            "e.g. weekdays:09:00-18:00:512K (repeatable)"
        ),
    )
    p_prof_set.add_argument(
        "--clear-windows",
        action="store_true",
        help="replace windows with only --window entries (or empty)",
    )
    p_prof_set.add_argument("--json", action="store_true")

    p_prof_del = profile_sub.add_parser("delete", help="delete a speed profile")
    p_prof_del.add_argument("name")
    p_prof_del.add_argument("--json", action="store_true")

    p_quota = sched_sub.add_parser("quota", help="configure or show traffic quota")
    p_quota.add_argument("--json", action="store_true")
    p_quota.add_argument(
        "--enable",
        dest="quota_enabled",
        default=None,
        action=argparse.BooleanOptionalAction,
        help="enable or disable quota (--enable / --no-enable)",
    )
    p_quota.add_argument("--hour-mb", type=float, default=None, help="max MB per hour")
    p_quota.add_argument("--day-mb", type=float, default=None, help="max MB per day")
    p_quota.add_argument(
        "--clear-pause",
        action="store_true",
        help="clear paused_until so downloads can resume",
    )


def _print(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _parse_days(raw: str | None) -> str | list[int] | None:
    if raw is None:
        return None
    text = raw.strip().lower()
    if text in {"daily", "weekdays", "weekend"}:
        return text
    if "," in text or text.isdigit():
        days: list[int] = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            day = int(part)
            if day < 0 or day > 6:
                raise ValueError(f"weekday out of range: {day}")
            days.append(day)
        return days
    raise ValueError(f"invalid --days value: {raw}")


def _parse_window_spec(spec: str) -> speed_window:
    # days:HH:MM-HH:MM[:limit] e.g. weekdays:09:00-18:00:512K
    m = re_window.match(spec.strip())
    if not m:
        raise ValueError(
            f"invalid --window {spec!r}; expected days:HH:MM-HH:MM[:limit]"
        )
    days = _parse_days(m.group("days"))
    if days is None:
        days = "daily"
    return speed_window(
        day_spec=days,
        start_time=m.group("start"),
        end_time=m.group("end"),
        speed_limit=(m.group("limit") or "").strip(),
    )


def cmd_queue_schedule(conn, args: argparse.Namespace) -> int:
    ensure_default_queue(conn)
    cmd = args.schedule_command
    as_json = bool(getattr(args, "json", False))

    if cmd == "list":
        status = evaluate_scheduler(conn)
        payload = {
            "queues": [q.to_dict() for q in list_named_queues(conn)],
            "status": status.to_dict(),
        }
        _print(payload, as_json)
        return 0

    if cmd == "show":
        queue = get_named_queue(conn, args.name)
        if queue is None:
            _print({"error": "not found", "name": args.name}, as_json)
            return 1
        status = evaluate_scheduler(conn)
        runtime = next((q for q in status.queues if q.name == args.name), None)
        _print(
            {
                "queue": queue.to_dict(),
                "runtime": runtime.to_dict() if runtime else None,
            },
            as_json,
        )
        return 0

    if cmd == "set":
        existing = get_named_queue(conn, args.name) or named_queue(name=args.name)
        if args.enabled is not None:
            existing.enabled = bool(args.enabled)
        if args.max_concurrent is not None:
            existing.max_concurrent = max(1, int(args.max_concurrent))
        if args.days is not None:
            existing.day_spec = _parse_days(args.days) or "daily"
        if args.always:
            existing.start_time = None
            existing.end_time = None
        else:
            if args.start is not None:
                existing.start_time = args.start.strip() or None
            if args.end is not None:
                existing.end_time = args.end.strip() or None
        if args.repeat_mode is not None:
            existing.repeat_mode = args.repeat_mode
            if args.repeat_mode == "repeat":
                existing.once_completed_at = None
        if args.after is not None:
            existing.after_completion = args.after
        if args.speed_profile is not None:
            existing.speed_profile = args.speed_profile.strip() or None
        saved = save_named_queue(conn, existing)
        _print(saved.to_dict(), as_json)
        return 0

    if cmd == "delete":
        try:
            ok = delete_named_queue(conn, args.name)
        except ValueError as exc:
            _print({"error": str(exc)}, as_json)
            return 1
        _print({"removed": ok, "name": args.name}, as_json)
        return 0 if ok else 1

    if cmd == "reset-once":
        queue = reset_queue_once(conn, args.name)
        if queue is None:
            _print({"error": "not found", "name": args.name}, as_json)
            return 1
        _print(queue.to_dict(), as_json)
        return 0

    if cmd == "complete-once":
        queue = get_named_queue(conn, args.name)
        if queue is None:
            _print({"error": "not found", "name": args.name}, as_json)
            return 1
        mark_queue_once_completed(conn, args.name)
        queue = get_named_queue(conn, args.name)
        _print(queue.to_dict() if queue else {"name": args.name}, as_json)
        return 0

    if cmd == "status":
        status = evaluate_scheduler(
            conn,
            base_speed_limit=getattr(args, "speed_limit", None),
        )
        _print(status.to_dict(), as_json)
        return 0

    if cmd == "export":
        _print(export_scheduler_config(conn), as_json)
        return 0

    if cmd == "profile":
        return _cmd_profile(conn, args)

    if cmd == "quota":
        return _cmd_quota(conn, args)

    return 2


def _cmd_profile(conn, args: argparse.Namespace) -> int:
    as_json = bool(getattr(args, "json", False))
    sub = args.profile_command
    if sub == "list":
        _print([p.to_dict() for p in list_speed_profiles(conn)], as_json)
        return 0
    if sub == "show":
        profile = get_speed_profile(conn, args.name)
        if profile is None:
            _print({"error": "not found", "name": args.name}, as_json)
            return 1
        _print(profile.to_dict(), as_json)
        return 0
    if sub == "set":
        existing = get_speed_profile(conn, args.name) or speed_profile(name=args.name)
        if args.default_limit is not None:
            existing.default_speed_limit = args.default_limit
        windows = list(existing.windows)
        if args.clear_windows:
            windows = []
        for spec in args.window or []:
            windows.append(_parse_window_spec(spec))
        if args.window or args.clear_windows:
            existing.windows = windows
        saved = save_speed_profile(conn, existing)
        _print(saved.to_dict(), as_json)
        return 0
    if sub == "delete":
        ok = delete_speed_profile(conn, args.name)
        _print({"removed": ok, "name": args.name}, as_json)
        return 0 if ok else 1
    return 2


def _cmd_quota(conn, args: argparse.Namespace) -> int:
    as_json = bool(getattr(args, "json", False))
    settings = get_traffic_quota(conn)
    changed = False
    if args.quota_enabled is not None:
        settings.enabled = bool(args.quota_enabled)
        changed = True
    if args.hour_mb is not None:
        settings.max_mb_per_hour = args.hour_mb if args.hour_mb > 0 else None
        changed = True
    if args.day_mb is not None:
        settings.max_mb_per_day = args.day_mb if args.day_mb > 0 else None
        changed = True
    if args.clear_pause:
        settings.paused_until = None
        changed = True
    if changed:
        settings = save_traffic_quota(conn, settings)
    # include live status
    status = evaluate_scheduler(conn)
    _print({"quota": settings.to_dict(), "status": status.quota.to_dict()}, as_json)
    return 0
