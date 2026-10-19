"""Shared HTTP download engine with resume retries concurrency and cancel."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Event

import requests
from requests import Response, Session

from udl.events import emitter

# defaults (lowercase constants per project convention)
default_chunk_size = 1024 * 1024
default_timeout = 30.0
default_retries = 5
default_workers = 3
default_user_agent = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class download_options:
    """options for a batch of HTTP downloads."""

    output_dir: Path
    workers: int = default_workers
    retries: int = default_retries
    timeout: float = default_timeout
    chunk_size: int = default_chunk_size
    user_agent: str = default_user_agent
    headers: dict[str, str] | None = None
    cancel: Event | None = None
    progress_interval: float = 0.25


@dataclass
class download_item:
    """one remote file to fetch."""

    url: str
    dest: Path
    file_id: str
    name: str
    expected_size: int | None = None
    headers: dict[str, str] | None = None


ProgressCb = Callable[[str, int, int | None, float], None]


def _make_session(opts: download_options) -> Session:
    session = Session()
    headers = {
        "User-Agent": opts.user_agent,
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    if opts.headers:
        headers.update(opts.headers)
    session.headers.update(headers)
    return session


def _is_cancelled(cancel: Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def _valid_status(status: int, part_size: int) -> bool:
    if status in (403, 404, 405, 416, 500, 502, 503):
        return False
    if part_size == 0:
        return status in (200, 206)
    return status == 206


def _total_from_headers(headers: dict[str, str], part_size: int) -> int | None:
    if part_size == 0:
        cl = headers.get("Content-Length")
        return int(cl) if cl else None
    cr = headers.get("Content-Range")
    if cr and "/" in cr:
        total = cr.split("/")[-1]
        if total.isdigit():
            return int(total)
    return None


def download_file(
    item: download_item,
    opts: download_options,
    session: Session | None = None,
    *,
    job_id: str,
    emit: emitter | None = None,
) -> Path:
    """download one file with Range resume and retries. returns final path."""
    own_session = session is None
    session = session or _make_session(opts)
    cancel = opts.cancel
    dest = item.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")

    if dest.exists() and dest.stat().st_size > 0:
        if emit:
            emit.done(job=job_id, file=item.file_id, path=str(dest))
        if own_session:
            session.close()
        return dest

    last_error: Exception | None = None
    try:
        for attempt in range(opts.retries):
            if _is_cancelled(cancel):
                raise InterruptedError("cancelled")

            part_size = tmp.stat().st_size if tmp.is_file() else 0
            req_headers: dict[str, str] = {}
            if item.headers:
                req_headers.update(item.headers)
            if part_size > 0:
                req_headers["Range"] = f"bytes={part_size}-"

            try:
                response = session.get(
                    item.url,
                    headers=req_headers,
                    stream=True,
                    timeout=opts.timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2**attempt, 8))
                continue

            with response:
                if not _valid_status(response.status_code, part_size):
                    last_error = RuntimeError(
                        f"HTTP {response.status_code} for {item.url}"
                    )
                    if response.status_code in (403, 404):
                        break
                    time.sleep(min(2**attempt, 8))
                    continue

                content_type = (response.headers.get("Content-Type") or "").lower()
                if "text/html" in content_type and item.expected_size is not None:
                    # likely an auth/error interstitial rather than the payload
                    last_error = RuntimeError(
                        f"unexpected HTML response for {item.url} "
                        f"(status {response.status_code}); check auth headers"
                    )
                    if tmp.exists():
                        tmp.unlink(missing_ok=True)
                    time.sleep(min(2**attempt, 8))
                    continue

                total = _total_from_headers(response.headers, part_size)
                if total is None and item.expected_size is not None:
                    total = item.expected_size

                try:
                    _write_stream(
                        response,
                        tmp,
                        part_size=part_size,
                        total=total,
                        opts=opts,
                        job_id=job_id,
                        file_id=item.file_id,
                        emit=emit,
                    )
                except InterruptedError:
                    raise
                except OSError as exc:
                    last_error = exc
                    time.sleep(min(2**attempt, 8))
                    continue

                if total is not None and tmp.stat().st_size != total:
                    last_error = RuntimeError(
                        f"size mismatch: got {tmp.stat().st_size} expected {total}"
                    )
                    if tmp.exists():
                        tmp.unlink(missing_ok=True)
                    continue

                tmp.rename(dest)
                if emit:
                    emit.done(job=job_id, file=item.file_id, path=str(dest))
                return dest
    finally:
        if own_session:
            session.close()

    if _is_cancelled(cancel):
        raise InterruptedError("cancelled")
    raise RuntimeError(f"failed to download {item.name}: {last_error}")


def _write_stream(
    response: Response,
    tmp: Path,
    *,
    part_size: int,
    total: int | None,
    opts: download_options,
    job_id: str,
    file_id: str,
    emit: emitter | None,
) -> None:
    downloaded = part_size
    start = time.perf_counter()
    last_emit = 0.0
    with tmp.open("ab") as fh:
        for chunk in response.iter_content(chunk_size=opts.chunk_size):
            if _is_cancelled(opts.cancel):
                raise InterruptedError("cancelled")
            if not chunk:
                continue
            fh.write(chunk)
            downloaded += len(chunk)
            now = time.perf_counter()
            if emit and (now - last_emit) >= opts.progress_interval:
                elapsed = max(now - start, 1e-6)
                bps = (downloaded - part_size) / elapsed
                emit.progress(
                    job=job_id,
                    file=file_id,
                    bytes=downloaded,
                    total=total,
                    bps=bps,
                )
                last_emit = now
    if emit:
        elapsed = max(time.perf_counter() - start, 1e-6)
        bps = (downloaded - part_size) / elapsed
        emit.progress(
            job=job_id,
            file=file_id,
            bytes=downloaded,
            total=total,
            bps=bps,
        )


def download_many(
    items: list[download_item],
    opts: download_options,
    *,
    job_id: str,
    emit: emitter | None = None,
) -> tuple[int, int]:
    """download multiple files with a shared thread pool. returns (ok, failed)."""
    if not items:
        return 0, 0

    session = _make_session(opts)
    ok = 0
    failed = 0
    try:
        workers = max(1, min(opts.workers, len(items)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    download_file,
                    item,
                    opts,
                    session,
                    job_id=job_id,
                    emit=emit,
                ): item
                for item in items
            }
            for fut in as_completed(futures):
                item = futures[fut]
                if _is_cancelled(opts.cancel):
                    break
                try:
                    fut.result()
                    ok += 1
                except InterruptedError:
                    failed += 1
                    if emit:
                        emit.cancel(job=job_id)
                    break
                except Exception as exc:  # noqa: BLE001 — surface per-file errors
                    failed += 1
                    if emit:
                        emit.error(
                            job=job_id,
                            file=item.file_id,
                            message=str(exc),
                            code="download_failed",
                        )
    finally:
        session.close()
    return ok, failed
