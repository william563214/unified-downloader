"""SQLite connection and schema migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from udl.paths import default_db_path as _default_db_path

default_db_path = _default_db_path

# ordered migrations: (version, sql statements)
migrations: list[tuple[int, str]] = [
    (
        1,
        """
        create table if not exists schema_migrations (
          version integer primary key,
          applied_at text not null default (datetime('now'))
        );

        create table if not exists history_jobs (
          id text primary key,
          site text not null,
          item_id text,
          canonical_url text,
          title text,
          author text,
          upload_date text,
          tags_json text not null default '[]',
          description text,
          duration real,
          resolution text,
          thumbnail_url text,
          cover_url text,
          view_count integer,
          like_count integer,
          collection text,
          collection_id text,
          extra_json text not null default '{}',
          source_url text,
          output_dir text,
          status text not null,
          ok integer not null default 0,
          failed integer not null default 0,
          error_message text,
          created_at text not null default (datetime('now')),
          updated_at text not null default (datetime('now')),
          finished_at text
        );

        create index if not exists idx_history_jobs_site on history_jobs(site);
        create index if not exists idx_history_jobs_item on history_jobs(site, item_id);
        create index if not exists idx_history_jobs_status on history_jobs(status);
        create index if not exists idx_history_jobs_created on history_jobs(created_at);
        create index if not exists idx_history_jobs_url on history_jobs(canonical_url);

        create table if not exists history_files (
          id integer primary key autoincrement,
          job_id text not null references history_jobs(id) on delete cascade,
          file_id text,
          site text not null,
          item_id text,
          canonical_url text,
          name text,
          path text,
          size integer,
          sha256 text,
          status text not null,
          metadata_json text not null default '{}',
          created_at text not null default (datetime('now')),
          updated_at text not null default (datetime('now')),
          unique(job_id, file_id)
        );

        create index if not exists idx_history_files_site_item
          on history_files(site, item_id);
        create index if not exists idx_history_files_url on history_files(canonical_url);
        create index if not exists idx_history_files_sha on history_files(sha256);
        create index if not exists idx_history_files_status on history_files(status);

        create table if not exists queue_jobs (
          id text primary key,
          url text not null,
          site text,
          title text,
          output_dir text not null,
          status text not null,
          quality text,
          part text,
          cloudflare_bypass integer not null default 0,
          workers integer,
          ffmpeg text,
          on_duplicate text not null default 'skip',
          metadata_json text not null default '{}',
          options_json text not null default '{}',
          error_message text,
          created_at text not null default (datetime('now')),
          updated_at text not null default (datetime('now')),
          started_at text,
          finished_at text
        );

        create index if not exists idx_queue_jobs_status on queue_jobs(status);
        create index if not exists idx_queue_jobs_updated on queue_jobs(updated_at);
        """,
    ),
    (
        2,
        """
        alter table history_jobs add column cover_path text;
        alter table history_files add column role text;

        create index if not exists idx_history_files_role on history_files(role);
        """,
    ),
    (
        3,
        """
        create table if not exists iwara_subscriptions (
          id integer primary key autoincrement,
          kind text not null,
          key text not null,
          title text,
          remote_id text,
          enabled integer not null default 1,
          last_checked_at text,
          last_seen_video_id text,
          filter_json text not null default '{}',
          new_count integer not null default 0,
          created_at text not null default (datetime('now')),
          updated_at text not null default (datetime('now')),
          unique(kind, key)
        );

        create index if not exists idx_iwara_subs_enabled
          on iwara_subscriptions(enabled);

        create table if not exists iwara_subscription_items (
          id integer primary key autoincrement,
          source_id integer not null references iwara_subscriptions(id) on delete cascade,
          video_id text not null,
          title text,
          payload_json text not null default '{}',
          seen integer not null default 0,
          enqueued integer not null default 0,
          discovered_at text not null default (datetime('now')),
          unique(source_id, video_id)
        );

        create index if not exists idx_iwara_sub_items_seen
          on iwara_subscription_items(source_id, seen);

        create table if not exists iwara_home_sections (
          id integer primary key autoincrement,
          name text not null,
          query_kind text not null,
          query_value text not null default '',
          sort text not null default 'date',
          rating text,
          enabled integer not null default 1,
          position integer not null default 0,
          filter_json text not null default '{}',
          created_at text not null default (datetime('now')),
          updated_at text not null default (datetime('now'))
        );

        create index if not exists idx_iwara_home_pos
          on iwara_home_sections(position);
        """,
    ),
    (
        4,
        """
        create table if not exists named_queues (
          name text primary key,
          enabled integer not null default 1,
          max_concurrent integer not null default 2,
          day_spec text not null default 'daily',
          start_time text,
          end_time text,
          repeat_mode text not null default 'repeat',
          once_completed_at text,
          after_completion text not null default 'none',
          speed_profile text,
          created_at text not null default (datetime('now')),
          updated_at text not null default (datetime('now'))
        );

        insert or ignore into named_queues(name) values ('default');

        create table if not exists speed_profiles (
          name text primary key,
          default_speed_limit text not null default '',
          windows_json text not null default '[]',
          created_at text not null default (datetime('now')),
          updated_at text not null default (datetime('now'))
        );

        create table if not exists traffic_quota (
          id integer primary key check (id = 1),
          enabled integer not null default 0,
          max_mb_per_hour real,
          max_mb_per_day real,
          paused_until text,
          updated_at text not null default (datetime('now'))
        );

        insert or ignore into traffic_quota(id) values (1);

        create table if not exists traffic_usage (
          bucket_kind text not null,
          bucket_key text not null,
          bytes_used integer not null default 0,
          updated_at text not null default (datetime('now')),
          primary key (bucket_kind, bucket_key)
        );

        alter table queue_jobs add column queue_name text not null default 'default';
        create index if not exists idx_queue_jobs_queue_name
          on queue_jobs(queue_name);
        """,
    ),
]


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = default_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # timeout helps GUI + MCP (or multiple CLI) share the same DB safely
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    # WAL allows concurrent readers while a writer holds a short transaction
    try:
        conn.execute("pragma journal_mode=WAL")
    except sqlite3.Error:
        pass
    try:
        conn.execute("pragma busy_timeout=5000")
    except sqlite3.Error:
        pass
    migrate(conn)
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"pragma table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists schema_migrations (
          version integer primary key,
          applied_at text not null default (datetime('now'))
        )
        """
    )
    applied = {
        int(row[0])
        for row in conn.execute("select version from schema_migrations").fetchall()
    }
    for version, sql in migrations:
        if version in applied:
            continue
        if version == 2:
            # idempotent column adds for interrupted prior runs
            job_cols = _table_columns(conn, "history_jobs")
            file_cols = _table_columns(conn, "history_files")
            if "cover_path" not in job_cols:
                conn.execute("alter table history_jobs add column cover_path text")
            if "role" not in file_cols:
                conn.execute("alter table history_files add column role text")
            conn.execute(
                "create index if not exists idx_history_files_role on history_files(role)"
            )
        elif version == 4:
            conn.executescript(
                """
                create table if not exists named_queues (
                  name text primary key,
                  enabled integer not null default 1,
                  max_concurrent integer not null default 2,
                  day_spec text not null default 'daily',
                  start_time text,
                  end_time text,
                  repeat_mode text not null default 'repeat',
                  once_completed_at text,
                  after_completion text not null default 'none',
                  speed_profile text,
                  created_at text not null default (datetime('now')),
                  updated_at text not null default (datetime('now'))
                );
                insert or ignore into named_queues(name) values ('default');
                create table if not exists speed_profiles (
                  name text primary key,
                  default_speed_limit text not null default '',
                  windows_json text not null default '[]',
                  created_at text not null default (datetime('now')),
                  updated_at text not null default (datetime('now'))
                );
                create table if not exists traffic_quota (
                  id integer primary key check (id = 1),
                  enabled integer not null default 0,
                  max_mb_per_hour real,
                  max_mb_per_day real,
                  paused_until text,
                  updated_at text not null default (datetime('now'))
                );
                insert or ignore into traffic_quota(id) values (1);
                create table if not exists traffic_usage (
                  bucket_kind text not null,
                  bucket_key text not null,
                  bytes_used integer not null default 0,
                  updated_at text not null default (datetime('now')),
                  primary key (bucket_kind, bucket_key)
                );
                """
            )
            q_cols = _table_columns(conn, "queue_jobs")
            if "queue_name" not in q_cols:
                conn.execute(
                    "alter table queue_jobs add column queue_name "
                    "text not null default 'default'"
                )
            conn.execute(
                "create index if not exists idx_queue_jobs_queue_name "
                "on queue_jobs(queue_name)"
            )
        else:
            conn.executescript(sql)
        conn.execute(
            "insert or ignore into schema_migrations(version) values (?)",
            (version,),
        )
        conn.commit()


def executemany(
    conn: sqlite3.Connection,
    sql: str,
    rows: Iterable[tuple[object, ...]],
) -> None:
    conn.executemany(sql, rows)
    conn.commit()
