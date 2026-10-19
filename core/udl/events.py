"""JSON-lines progress event protocol for CLI and GUI sidecar."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, TextIO

# event type names (lowercase per project naming)
ev_job = "job"
ev_file = "file"
ev_progress = "progress"
ev_done = "done"
ev_error = "error"
ev_cancel = "cancel"
ev_resolve = "resolve"


@dataclass
class event:
    """one JSON-lines event object."""

    ev: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"ev": self.ev}
        out.update(self.data)
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


class emitter:
    """writes one JSON object per line to a stream."""

    def __init__(self, stream: TextIO | None = None, *, enabled: bool = True) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._enabled = enabled

    def emit(self, ev: str, **fields: Any) -> None:
        if not self._enabled:
            return
        line = event(ev=ev, data=fields).to_json()
        self._stream.write(line + "\n")
        self._stream.flush()

    def job(
        self,
        *,
        id: str,
        site: str,
        title: str | None = None,
        url: str | None = None,
        files: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"id": id, "site": site}
        if title is not None:
            payload["title"] = title
        if url is not None:
            payload["url"] = url
        if files is not None:
            payload["files"] = files
        self.emit(ev_job, **payload)

    def file(
        self,
        *,
        job: str,
        id: str,
        name: str,
        total: int | None = None,
        path: str | None = None,
        index: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"job": job, "id": id, "name": name}
        if total is not None:
            payload["total"] = total
        if path is not None:
            payload["path"] = path
        if index is not None:
            payload["index"] = index
        self.emit(ev_file, **payload)

    def progress(
        self,
        *,
        job: str,
        file: str,
        bytes: int,
        total: int | None = None,
        bps: float | None = None,
    ) -> None:
        payload: dict[str, Any] = {"job": job, "file": file, "bytes": bytes}
        if total is not None:
            payload["total"] = total
        if bps is not None:
            payload["bps"] = round(bps, 1)
        self.emit(ev_progress, **payload)

    def done(
        self,
        *,
        job: str,
        file: str | None = None,
        path: str | None = None,
        ok: int | None = None,
        failed: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"job": job}
        if file is not None:
            payload["file"] = file
        if path is not None:
            payload["path"] = path
        if ok is not None:
            payload["ok"] = ok
        if failed is not None:
            payload["failed"] = failed
        self.emit(ev_done, **payload)

    def error(
        self,
        *,
        job: str | None = None,
        file: str | None = None,
        message: str,
        code: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"message": message}
        if job is not None:
            payload["job"] = job
        if file is not None:
            payload["file"] = file
        if code is not None:
            payload["code"] = code
        self.emit(ev_error, **payload)

    def cancel(self, *, job: str) -> None:
        self.emit(ev_cancel, job=job)

    def resolve(
        self,
        *,
        site: str | None,
        kind: str | None,
        url: str,
        supported: bool,
        message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "site": site,
            "kind": kind,
            "url": url,
            "supported": supported,
        }
        if message is not None:
            payload["message"] = message
        self.emit(ev_resolve, **payload)


def dump_schema() -> dict[str, Any]:
    """return a machine-readable overview of event fields."""
    return {
        "format": "newline-delimited JSON (JSON-lines) on stdout",
        "events": {
            ev_resolve: {
                "site": "str|null",
                "kind": "str|null",
                "url": "str",
                "supported": "bool",
                "message": "str?",
            },
            ev_job: {
                "id": "str",
                "site": "str",
                "title": "str?",
                "url": "str?",
                "files": "int?",
            },
            ev_file: {
                "job": "str",
                "id": "str",
                "name": "str",
                "total": "int?",
                "path": "str?",
                "index": "int?",
            },
            ev_progress: {
                "job": "str",
                "file": "str",
                "bytes": "int",
                "total": "int?",
                "bps": "float?",
            },
            ev_done: {
                "job": "str",
                "file": "str?",
                "path": "str?",
                "ok": "int?",
                "failed": "int?",
            },
            ev_error: {
                "job": "str?",
                "file": "str?",
                "message": "str",
                "code": "str?",
            },
            ev_cancel: {"job": "str"},
        },
    }


# silence unused import lint for asdict when schema helpers grow
_ = asdict
