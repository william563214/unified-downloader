"""SQLite-backed history queue and duplicate helpers owned by the core."""

from __future__ import annotations

from udl.store.db import connect, default_db_path, migrate
from udl.store.duplicates import (
    duplicate_action,
    duplicate_hit,
    duplicate_mode,
    resolve_duplicate,
)
from udl.store.history import (
    delete_history,
    history_entry,
    list_history,
    record_file_done,
    record_job_finished,
    record_job_started,
    search_history,
    set_job_cover_path,
)
from udl.store.iwara_subs import (
    add_home_section,
    add_subscription,
    enqueue_videos,
    fetch_home_section,
    list_home_sections,
    list_new_items,
    list_subscriptions,
    mark_items_seen,
    remove_home_section,
    remove_subscription,
    set_subscription_enabled,
    sync_all_subscriptions,
    sync_subscription,
)
from udl.store.queue import (
    delete_queue_job,
    list_queue,
    load_queue_job,
    queue_job,
    save_queue_job,
    update_queue_status,
)

__all__ = [
    "connect",
    "default_db_path",
    "migrate",
    "duplicate_action",
    "duplicate_hit",
    "duplicate_mode",
    "resolve_duplicate",
    "delete_history",
    "history_entry",
    "list_history",
    "record_file_done",
    "record_job_finished",
    "record_job_started",
    "search_history",
    "set_job_cover_path",
    "delete_queue_job",
    "list_queue",
    "load_queue_job",
    "queue_job",
    "save_queue_job",
    "update_queue_status",
    "add_home_section",
    "add_subscription",
    "enqueue_videos",
    "fetch_home_section",
    "list_home_sections",
    "list_new_items",
    "list_subscriptions",
    "mark_items_seen",
    "remove_home_section",
    "remove_subscription",
    "set_subscription_enabled",
    "sync_all_subscriptions",
    "sync_subscription",
]
