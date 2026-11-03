"""Persist learned per-host connection limits in SQLite."""

from __future__ import annotations

import time
from typing import Any

from udl.store.db import connect

# lowercase constants
table_name = "host_connection_limits"


class host_limits_store:
    """CRUD for adaptive per-host connection ceilings."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def _conn(self):
        return connect(self._db_path)

    def load_all(self) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                f"""
                select host, limit_n, success_streak, pressure_count,
                       strftime('%s', updated_at) as updated_epoch
                from {table_name}
                order by host
                """
            ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                epoch = row["updated_epoch"]
                out.append(
                    {
                        "host": str(row["host"]),
                        "limit": int(row["limit_n"]),
                        "success_streak": int(row["success_streak"] or 0),
                        "pressure_count": int(row["pressure_count"] or 0),
                        "updated_at": float(epoch) if epoch is not None else time.time(),
                    }
                )
            return out
        finally:
            conn.close()

    def save_one(
        self,
        *,
        host: str,
        limit: int,
        success_streak: int = 0,
        pressure_count: int = 0,
    ) -> None:
        host = (host or "").strip()
        if not host:
            return
        conn = self._conn()
        try:
            conn.execute(
                f"""
                insert into {table_name}(host, limit_n, success_streak, pressure_count, updated_at)
                values (?, ?, ?, ?, datetime('now'))
                on conflict(host) do update set
                  limit_n = excluded.limit_n,
                  success_streak = excluded.success_streak,
                  pressure_count = excluded.pressure_count,
                  updated_at = datetime('now')
                """,
                (host, int(limit), int(success_streak), int(pressure_count)),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_one(self, host: str) -> int:
        host = (host or "").strip()
        if not host:
            return 0
        conn = self._conn()
        try:
            cur = conn.execute(f"delete from {table_name} where host = ?", (host,))
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()

    def delete_all(self) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(f"delete from {table_name}")
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()


def list_host_limits(db_path: str | None = None) -> list[dict[str, Any]]:
    return host_limits_store(db_path).load_all()


def reset_host_limits(host: str | None = None, db_path: str | None = None) -> int:
    store = host_limits_store(db_path)
    if host:
        return store.delete_one(host)
    return store.delete_all()
