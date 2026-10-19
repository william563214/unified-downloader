"""Optional aria2 JSON-RPC download backend.

When enabled the shared engine hands file downloads to aria2 and maps
tellStatus progress into standard JSON-lines events. Falls back to the
built-in HTTP engine when the RPC endpoint is unreachable.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from udl.download.http import download_item, download_options
from udl.download.rate import make_job_limiter, throttle
from udl.events import emitter
from udl.network import active_network, resolve_aria2_secret

default_poll_interval = 0.25


class aria2_error(RuntimeError):
    """aria2 RPC failure."""

    code = "download_failed"


class aria2_client:
    """minimal aria2 JSON-RPC client (aria2.addUri / tellStatus / remove)."""

    def __init__(
        self,
        rpc_url: str,
        *,
        secret: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.rpc_url = rpc_url.rstrip("/")
        self.secret = secret
        self.timeout = timeout
        self._session = requests.Session()
        self._session.trust_env = False

    def close(self) -> None:
        self._session.close()

    def _params(self, extra: list[Any] | None = None) -> list[Any]:
        params: list[Any] = []
        if self.secret:
            params.append(f"token:{self.secret}")
        if extra:
            params.extend(extra)
        return params

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": "udl",
            "method": method,
            "params": self._params(params),
        }
        try:
            resp = self._session.post(self.rpc_url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise aria2_error(f"aria2 RPC unreachable: {exc}") from exc
        if "error" in data:
            err = data["error"]
            raise aria2_error(
                f"aria2 {method} error: {err.get('message', err)}"
            )
        return data.get("result")

    def ping(self) -> bool:
        try:
            self.call("aria2.getVersion")
            return True
        except aria2_error:
            return False

    def add_uri(self, uris: list[str], options: dict[str, Any]) -> str:
        gid = self.call("aria2.addUri", [uris, options])
        if not isinstance(gid, str):
            raise aria2_error(f"aria2.addUri returned unexpected gid: {gid}")
        return gid

    def tell_status(self, gid: str) -> dict[str, Any]:
        result = self.call(
            "aria2.tellStatus",
            [
                gid,
                [
                    "gid",
                    "status",
                    "totalLength",
                    "completedLength",
                    "downloadSpeed",
                    "errorCode",
                    "errorMessage",
                    "files",
                ],
            ],
        )
        if not isinstance(result, dict):
            raise aria2_error(f"aria2.tellStatus bad result: {result}")
        return result

    def pause(self, gid: str) -> None:
        self.call("aria2.pause", [gid])

    def unpause(self, gid: str) -> None:
        self.call("aria2.unpause", [gid])

    def remove(self, gid: str, *, force: bool = False) -> None:
        method = "aria2.forceRemove" if force else "aria2.remove"
        try:
            self.call(method, [gid])
        except aria2_error:
            if not force:
                try:
                    self.call("aria2.forceRemove", [gid])
                except aria2_error:
                    pass


def try_aria2_client(opts: download_options | None = None) -> aria2_client | None:
    """return a live client when aria2 is enabled and reachable else None."""
    net = active_network()
    if opts is not None and opts.aria2_enabled is False:
        return None
    enabled = net.aria2_enabled if opts is None else (
        opts.aria2_enabled if opts.aria2_enabled is not None else net.aria2_enabled
    )
    if not enabled:
        return None
    url = (opts.aria2_rpc_url if opts and opts.aria2_rpc_url else None) or net.aria2_rpc_url
    secret = (
        (opts.aria2_rpc_secret if opts and opts.aria2_rpc_secret else None)
        or resolve_aria2_secret(net)
    )
    client = aria2_client(url, secret=secret)
    if not client.ping():
        client.close()
        return None
    return client


def _header_list(item: download_item, opts: download_options) -> list[str]:
    headers: dict[str, str] = {"User-Agent": opts.user_agent}
    if opts.headers:
        headers.update(opts.headers)
    if item.headers:
        headers.update(item.headers)
    return [f"{k}: {v}" for k, v in headers.items()]


def _cookie_header(item: download_item, opts: download_options) -> str | None:
    # cookie may arrive as a Cookie header already
    for source in (item.headers, opts.headers):
        if not source:
            continue
        for key, value in source.items():
            if key.lower() == "cookie":
                return value
    return None


def download_file_aria2(
    item: download_item,
    opts: download_options,
    client: aria2_client,
    *,
    job_id: str,
    emit: emitter | None = None,
) -> Path:
    """download one file via aria2 RPC. raises InterruptedError on cancel."""
    dest = item.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        if emit:
            emit.done(job=job_id, file=item.file_id, path=str(dest))
        return dest

    job_limiter = make_job_limiter(opts.speed_limit_bps)
    options: dict[str, Any] = {
        "dir": str(dest.parent.resolve()),
        "out": dest.name,
        "continue": "true",
        "allow-overwrite": "true",
        "auto-file-renaming": "false",
        "header": _header_list(item, opts),
    }
    cookie = _cookie_header(item, opts)
    if cookie:
        options["header"] = list(options["header"]) + [f"Cookie: {cookie}"]
    if opts.speed_limit_bps and opts.speed_limit_bps > 0:
        options["max-download-limit"] = str(opts.speed_limit_bps)

    # referer as dedicated option when present
    for source in (item.headers, opts.headers):
        if not source:
            continue
        if "Referer" in source:
            options["referer"] = source["Referer"]
            break
        if "referer" in source:
            options["referer"] = source["referer"]
            break

    gid = client.add_uri([item.url], options)
    last_completed = 0
    last_emit = 0.0
    try:
        while True:
            if opts.cancel is not None and opts.cancel.is_set():
                client.remove(gid, force=True)
                raise InterruptedError("cancelled")
            status = client.tell_status(gid)
            state = (status.get("status") or "").lower()
            completed = int(status.get("completedLength") or 0)
            total_raw = status.get("totalLength")
            total = int(total_raw) if total_raw not in (None, "", "0") else item.expected_size
            if total == 0:
                total = None
            bps = float(status.get("downloadSpeed") or 0)
            delta = max(0, completed - last_completed)
            if delta:
                throttle(delta, job_limiter)
                last_completed = completed
            now = time.perf_counter()
            if emit and (now - last_emit) >= opts.progress_interval:
                emit.progress(
                    job=job_id,
                    file=item.file_id,
                    bytes=completed,
                    total=total,
                    bps=bps,
                )
                last_emit = now
            if state == "complete":
                break
            if state in {"error", "removed"}:
                msg = status.get("errorMessage") or status.get("errorCode") or state
                raise aria2_error(f"aria2 download failed: {msg}")
            if state == "paused":
                # honour pause until cancel or external unpause
                time.sleep(default_poll_interval)
                continue
            time.sleep(default_poll_interval)
    except InterruptedError:
        raise
    except aria2_error:
        raise
    except Exception as exc:  # noqa: BLE001
        try:
            client.remove(gid, force=True)
        except Exception:  # noqa: BLE001
            pass
        raise aria2_error(str(exc)) from exc

    # aria2 writes final file to dir/out (may leave .aria2 control file)
    if not dest.exists():
        # sometimes name differs; inspect files list
        raise aria2_error(f"aria2 finished but file missing: {dest}")
    if emit:
        emit.done(job=job_id, file=item.file_id, path=str(dest))
    return dest


def pause_gid(client: aria2_client, gid: str) -> None:
    client.pause(gid)


def rpc_host_ok(url: str) -> bool:
    """cheap sanity check that the URL looks like an HTTP endpoint."""
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
