"""MCP stdio server for AI clients (optional ``mcp`` extra).

Exposes queue / history / router tools over stdio only. Never opens a
network listen port. Never returns credentials, cookies, keychain
contents, or ZIP password plaintext. Cloudflare bypass and browser
impersonation cannot be enabled through MCP tools.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from udl.paths import app_data_dir, default_db_path
from udl.router import registry, resolve
from udl.session import open_store
from udl.store.history import get_history, search_history
from udl.store.queue import (
    list_queue,
    load_queue_job,
    queue_job,
    save_queue_job,
    update_queue_status,
)

settings_filename = "settings.json"
mcp_enabled_key = "mcpEnabled"
download_dir_key = "downloadDir"
udl_mcp_force_env = "UDL_MCP_FORCE"
udl_download_root_env = "UDL_DOWNLOAD_ROOT"

# option keys MCP must never accept or echo
_blocked_option_keys = frozenset(
    {
        "cloudflare_bypass",
        "cloudflareBypass",
        "impersonate",
        "curl_cffi_impersonate",
        "curlCffiImpersonate",
        "password",
        "passwords",
        "cookie",
        "cookies",
        "token",
        "secret",
        "secrets",
        "proxy_password",
        "proxyPassword",
        "aria2_secret",
        "aria2RpcSecret",
        "gallerydl_password",
        "gallerydlPassword",
        "gallerydl_cookies",
        "gallerydlCookies",
        "zip_password",
        "zipPassword",
        "extract_password",
        "extractPassword",
    }
)

_sensitive_substrings = (
    "password",
    "cookie",
    "secret",
    "token",
    "keychain",
    "credential",
)


class mcp_error(Exception):
    """user-facing MCP tool failure."""

    def __init__(self, message: str, *, code: str = "mcp_error") -> None:
        super().__init__(message)
        self.code = code


def settings_path() -> Path:
    return app_data_dir() / settings_filename


def load_app_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def is_mcp_enabled(*, force: bool = False) -> bool:
    if force:
        return True
    env = os.environ.get(udl_mcp_force_env, "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    settings = load_app_settings()
    return bool(settings.get(mcp_enabled_key))


def download_root() -> Path:
    env = os.environ.get(udl_download_root_env, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    settings = load_app_settings()
    configured = str(settings.get(download_dir_key) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    raise mcp_error(
        "download root is not configured; set downloadDir in GUI settings "
        f"or {udl_download_root_env}",
        code="download_root_missing",
    )


def confine_output_path(output_dir: str | None, *, root: Path | None = None) -> Path:
    """resolve output_dir under download root; reject path traversal."""
    base = (root or download_root()).resolve()
    if output_dir is None or not str(output_dir).strip():
        return base
    raw = Path(str(output_dir).strip()).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (base / raw).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise mcp_error(
            "output path must stay inside the configured download root",
            code="path_traversal",
        ) from exc
    return candidate


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    if lowered in {k.lower() for k in _blocked_option_keys}:
        return True
    return any(part in lowered for part in _sensitive_substrings)


def sanitize_mapping(data: dict[str, Any] | None) -> dict[str, Any]:
    """drop secrets / login-related keys from public payloads."""
    if not data:
        return {}
    out: dict[str, Any] = {}
    for key, value in data.items():
        if _is_sensitive_key(str(key)):
            continue
        if isinstance(value, dict):
            out[str(key)] = sanitize_mapping(value)
        else:
            out[str(key)] = value
    return out


def public_queue_job(job: queue_job) -> dict[str, Any]:
    payload = job.to_dict()
    # MCP must never advertise cloudflare bypass as something it can enable
    payload["cloudflare_bypass"] = False
    payload["metadata"] = sanitize_mapping(
        payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    )
    payload["options"] = sanitize_mapping(
        payload.get("options") if isinstance(payload.get("options"), dict) else {}
    )
    return payload


def public_history_entry(entry: Any) -> dict[str, Any]:
    payload = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
    if isinstance(payload.get("extra"), dict):
        payload["extra"] = sanitize_mapping(payload["extra"])
    files = payload.get("files")
    if isinstance(files, list):
        cleaned_files = []
        for row in files:
            if isinstance(row, dict):
                cleaned = dict(row)
                if isinstance(cleaned.get("metadata"), dict):
                    cleaned["metadata"] = sanitize_mapping(cleaned["metadata"])
                cleaned_files.append(cleaned)
            else:
                cleaned_files.append(row)
        payload["files"] = cleaned_files
    return payload


def add_download(
    url: str,
    *,
    output_dir: str | None = None,
    name_template: str | None = None,
    quality: str | None = None,
    part: str | None = None,
    on_duplicate: str | None = None,
    queue_name: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """enqueue a download job (cloudflare bypass always off)."""
    url = (url or "").strip()
    if not url:
        raise mcp_error("url is required", code="invalid_url")
    info = resolve(url)
    if not info.supported:
        raise mcp_error(
            info.message or f"unsupported URL: {url}",
            code="unsupported_url",
        )
    dest = confine_output_path(output_dir)
    job_id = str(uuid.uuid4())
    options: dict[str, Any] = {"source": "mcp"}
    if name_template:
        options["name_template"] = str(name_template)
    duplicate = (on_duplicate or "skip").strip().lower()
    if duplicate not in {"skip", "overwrite", "rename"}:
        raise mcp_error(
            "on_duplicate must be skip|overwrite|rename",
            code="invalid_option",
        )
    job = queue_job(
        id=job_id,
        url=info.url or url,
        output_dir=str(dest),
        status="queued",
        site=info.site,
        title=None,
        quality=quality,
        part=part,
        cloudflare_bypass=False,
        on_duplicate=duplicate,
        queue_name=(queue_name or "default").strip() or "default",
        metadata={
            "site": info.site,
            "canonical_url": info.url or url,
            "source": "mcp",
        },
        options=options,
    )
    conn = open_store(db_path)
    try:
        save_queue_job(conn, job)
        stored = load_queue_job(conn, job_id)
    finally:
        conn.close()
    return {
        "ok": True,
        "job": public_queue_job(stored or job),
        "resolve": {
            "site": info.site,
            "label": info.label,
            "kind": info.kind,
            "supported": info.supported,
            "status": info.status,
            "url": info.url or url,
        },
    }


def tool_list_queue(
    *,
    status: str | None = None,
    unfinished_only: bool = False,
    queue_name: str | None = None,
    limit: int = 200,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    rows = list_queue(
        status=status,
        unfinished_only=unfinished_only,
        queue_name=queue_name,
        limit=limit,
        db_path=db_path,
    )
    return {"jobs": [public_queue_job(row) for row in rows], "count": len(rows)}


def get_status(
    job_id: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    job_id = (job_id or "").strip()
    if not job_id:
        raise mcp_error("id is required", code="invalid_id")
    conn = open_store(db_path)
    try:
        queued = load_queue_job(conn, job_id)
        if queued is not None:
            return {"source": "queue", "job": public_queue_job(queued)}
        history = get_history(conn, job_id)
        if history is not None:
            return {"source": "history", "job": public_history_entry(history)}
    finally:
        conn.close()
    raise mcp_error(f"job not found: {job_id}", code="not_found")


def _set_queue_status(
    job_id: str,
    status: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    job_id = (job_id or "").strip()
    if not job_id:
        raise mcp_error("id is required", code="invalid_id")
    conn = open_store(db_path)
    try:
        job = load_queue_job(conn, job_id)
        if job is None:
            raise mcp_error(f"queue job not found: {job_id}", code="not_found")
        update_queue_status(conn, job_id, status)
        updated = load_queue_job(conn, job_id)
    finally:
        conn.close()
    return {"ok": True, "job": public_queue_job(updated or job)}


def pause_download(job_id: str, *, db_path: str | Path | None = None) -> dict[str, Any]:
    return _set_queue_status(job_id, "paused", db_path=db_path)


def resume_download(job_id: str, *, db_path: str | Path | None = None) -> dict[str, Any]:
    return _set_queue_status(job_id, "queued", db_path=db_path)


def cancel_download(job_id: str, *, db_path: str | Path | None = None) -> dict[str, Any]:
    return _set_queue_status(job_id, "cancelled", db_path=db_path)


def tool_search_history(
    query: str | None = None,
    *,
    site: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    conn = open_store(db_path)
    try:
        rows = search_history(
            conn,
            site=site,
            status=status,
            tag=tag,
            date_from=date_from,
            date_to=date_to,
            query=query,
            limit=limit,
            offset=offset,
            include_files=True,
        )
    finally:
        conn.close()
    return {
        "entries": [public_history_entry(row) for row in rows],
        "count": len(rows),
    }


def list_supported_sites() -> dict[str, Any]:
    rows = [
        {
            "site": entry.site,
            "label": entry.label,
            "status": entry.status,
            "kind": entry.kind_hint,
        }
        for entry in registry
    ]
    return {"sites": rows, "count": len(rows)}


def resolve_url(url: str) -> dict[str, Any]:
    url = (url or "").strip()
    if not url:
        raise mcp_error("url is required", code="invalid_url")
    info = resolve(url)
    return {
        "site": info.site,
        "label": info.label,
        "kind": info.kind,
        "url": info.url or url,
        "supported": info.supported,
        "status": info.status,
        "message": info.message,
    }


def _json_result(payload: dict[str, Any] | list[Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_server(*, db_path: str | Path | None = None):
    """construct MCPServer; requires the optional ``mcp`` package."""
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:  # pragma: no cover - optional dep
        raise mcp_error(
            "MCP SDK not installed; run: uv sync --extra mcp",
            code="mcp_missing",
        ) from exc

    resolved_db = str(default_db_path(db_path)) if db_path is not None else None

    server = MCPServer(
        name="unified-downloader",
        instructions=(
            "Control the unified-downloader queue and history. "
            "stdio only. Cannot enable Cloudflare bypass or change login/secrets. "
            "Output paths must stay under the configured download root."
        ),
    )

    @server.tool(
        name="add_download",
        description="Enqueue a download URL into the persistent queue",
    )
    def tool_add_download(
        url: str,
        output_dir: str | None = None,
        name_template: str | None = None,
        quality: str | None = None,
        part: str | None = None,
        on_duplicate: str | None = None,
        queue_name: str | None = None,
    ) -> str:
        try:
            result = add_download(
                url,
                output_dir=output_dir,
                name_template=name_template,
                quality=quality,
                part=part,
                on_duplicate=on_duplicate,
                queue_name=queue_name,
                db_path=resolved_db,
            )
            return _json_result(result)
        except mcp_error as exc:
            return _json_result({"ok": False, "error": str(exc), "code": exc.code})

    @server.tool(name="list_queue", description="List download queue jobs")
    def tool_list_queue_mcp(
        status: str | None = None,
        unfinished_only: bool = False,
        queue_name: str | None = None,
        limit: int = 200,
    ) -> str:
        try:
            result = tool_list_queue(
                status=status,
                unfinished_only=unfinished_only,
                queue_name=queue_name,
                limit=limit,
                db_path=resolved_db,
            )
            return _json_result(result)
        except mcp_error as exc:
            return _json_result({"ok": False, "error": str(exc), "code": exc.code})

    @server.tool(
        name="get_status",
        description="Get queue or history status by job id",
    )
    def tool_get_status(id: str) -> str:
        try:
            return _json_result(get_status(id, db_path=resolved_db))
        except mcp_error as exc:
            return _json_result({"ok": False, "error": str(exc), "code": exc.code})

    @server.tool(name="pause", description="Pause a queued or running download job")
    def tool_pause(id: str) -> str:
        try:
            return _json_result(pause_download(id, db_path=resolved_db))
        except mcp_error as exc:
            return _json_result({"ok": False, "error": str(exc), "code": exc.code})

    @server.tool(name="resume", description="Resume a paused download job")
    def tool_resume(id: str) -> str:
        try:
            return _json_result(resume_download(id, db_path=resolved_db))
        except mcp_error as exc:
            return _json_result({"ok": False, "error": str(exc), "code": exc.code})

    @server.tool(name="cancel", description="Cancel a download queue job")
    def tool_cancel(id: str) -> str:
        try:
            return _json_result(cancel_download(id, db_path=resolved_db))
        except mcp_error as exc:
            return _json_result({"ok": False, "error": str(exc), "code": exc.code})

    @server.tool(
        name="search_history",
        description="Search download history with optional filters",
    )
    def tool_search_history_mcp(
        query: str | None = None,
        site: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        try:
            result = tool_search_history(
                query,
                site=site,
                status=status,
                tag=tag,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
                offset=offset,
                db_path=resolved_db,
            )
            return _json_result(result)
        except mcp_error as exc:
            return _json_result({"ok": False, "error": str(exc), "code": exc.code})

    @server.tool(
        name="list_supported_sites",
        description="List registered extractors / supported sites",
    )
    def tool_list_supported_sites() -> str:
        return _json_result(list_supported_sites())

    @server.tool(
        name="resolve_url",
        description="Resolve which extractor would handle a URL",
    )
    def tool_resolve_url(url: str) -> str:
        try:
            return _json_result(resolve_url(url))
        except mcp_error as exc:
            return _json_result({"ok": False, "error": str(exc), "code": exc.code})

    @server.resource(
        "udl://queue",
        name="queue",
        description="Read-only snapshot of the download queue",
        mime_type="application/json",
    )
    def queue_resource() -> str:
        return _json_result(tool_list_queue(unfinished_only=True, db_path=resolved_db))

    @server.resource(
        "udl://history",
        name="history",
        description="Read-only recent download history",
        mime_type="application/json",
    )
    def history_resource() -> str:
        return _json_result(tool_search_history(limit=50, db_path=resolved_db))

    _ = (
        tool_add_download,
        tool_list_queue_mcp,
        tool_get_status,
        tool_pause,
        tool_resume,
        tool_cancel,
        tool_search_history_mcp,
        tool_list_supported_sites,
        tool_resolve_url,
        queue_resource,
        history_resource,
    )
    return server


def run_stdio_server(
    *,
    db_path: str | Path | None = None,
    force: bool = False,
) -> int:
    """run MCP over stdio (default transport; no network port)."""
    if not is_mcp_enabled(force=force):
        print(
            "MCP server is disabled. Enable it in GUI settings "
            f"('Enable MCP server') or set {udl_mcp_force_env}=1 / pass --force.",
            file=sys.stderr,
        )
        return 2
    try:
        server = build_server(db_path=db_path)
    except mcp_error as exc:
        print(str(exc), file=sys.stderr)
        return 1
    # stdio only — never sse / streamable-http from this entrypoint
    server.run(transport="stdio")
    return 0
