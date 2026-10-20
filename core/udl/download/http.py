"""Shared HTTP download engine with resume retries concurrency and cancel."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

import requests
from requests import Response, Session

from udl.download.rate import (
    configure_global_limiter,
    make_job_limiter,
    rate_limiter,
    throttle,
)
from udl.download.segmented import (
    SegmentedUnsupported,
    clear_state,
    default_connections,
    download_file_segmented,
    probe_validators,
    segment_state,
    validators_match,
)
from udl.events import emitter
from udl.network import active_network, apply_to_session, default_user_agent

# defaults (lowercase constants per project convention)
default_chunk_size = 1024 * 1024
default_timeout = 30.0
default_retries = 5
default_workers = 3

# sites that must not use multi-connection segmented downloads
# (auth cookies signed URLs or non-HTTP backends)
segmented_excluded_sites = frozenset({"gofile", "bunkr", "iwara", "mega"})


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
    # per-job speed cap (bytes/sec); global cap comes from network_settings
    speed_limit_bps: int | None = None
    aria2_enabled: bool | None = None
    aria2_rpc_url: str | None = None
    aria2_rpc_secret: str | None = None
    # multi-connection segmented (IDM-style); None = follow network_settings
    segmented: bool | None = None
    connections: int | None = None
    per_host_connections: int | None = None
    # filled by engine
    _job_limiter: rate_limiter | None = field(default=None, repr=False, compare=False)


@dataclass
class download_item:
    """one remote file to fetch."""

    url: str
    dest: Path
    file_id: str
    name: str
    expected_size: int | None = None
    headers: dict[str, str] | None = None
    etag: str | None = None
    last_modified: str | None = None
    accept_ranges: bool | None = None


ProgressCb = Callable[[str, int, int | None, float], None]


def options_from_network(
    output_dir: Path,
    *,
    workers: int = default_workers,
    retries: int = default_retries,
    cancel: Event | None = None,
    headers: dict[str, str] | None = None,
    speed_limit_bps: int | None = None,
    segmented: bool | None = None,
    connections: int | None = None,
) -> download_options:
    """build download_options seeded from active network_settings."""
    net = active_network()
    job_limit = speed_limit_bps
    if job_limit is None:
        job_limit = net.job_speed_limit_bps
    configure_global_limiter(net.speed_limit_bps)
    conns = connections if connections is not None else net.connections
    return download_options(
        output_dir=output_dir,
        workers=workers,
        retries=retries,
        cancel=cancel,
        headers=headers,
        user_agent=net.user_agent or default_user_agent,
        speed_limit_bps=job_limit,
        aria2_enabled=net.aria2_enabled,
        aria2_rpc_url=net.aria2_rpc_url,
        aria2_rpc_secret=net.aria2_rpc_secret,
        # None = decide later via should_use_segmented (site-aware)
        segmented=segmented,
        connections=conns,
        per_host_connections=net.per_host_connections,
        _job_limiter=make_job_limiter(job_limit),
    )


def effective_connections(opts: download_options) -> int:
    net = active_network()
    conns = opts.connections if opts.connections is not None else net.connections
    host_cap = (
        opts.per_host_connections
        if opts.per_host_connections is not None
        else net.per_host_connections
    )
    conns = int(conns or default_connections)
    host_cap = int(host_cap or default_connections)
    return max(1, min(conns, host_cap, 32))


def should_use_segmented(opts: download_options, *, site: str | None = None) -> bool:
    """decide whether multi-connection segmented download is allowed.

    - denylist sites (gofile bunkr iwara mega) never use segmented
    - opts.segmented True/False forces on/off (still respects denylist)
    - otherwise follow network.segmented_enabled
    - non-direct sites also need network.segmented_for_sites
    """
    if site and site in segmented_excluded_sites:
        return False
    net = active_network()
    if opts.segmented is False:
        return False
    if opts.segmented is True:
        return True
    if not net.segmented_enabled:
        return False
    if site and site != "direct" and not net.segmented_for_sites:
        return False
    # direct (or unknown) with segmented_enabled
    return True


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
    apply_to_session(session)
    # keep explicit User-Agent from opts after apply_to_session
    session.headers["User-Agent"] = opts.user_agent
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
    # prefer aria2 when enabled and reachable; only fall back if unreachable
    aria2_client = None
    try:
        from udl.download.aria2 import download_file_aria2, try_aria2_client

        aria2_client = try_aria2_client(opts)
    except Exception:  # noqa: BLE001 — treat as unreachable
        aria2_client = None
    if aria2_client is not None:
        try:
            return download_file_aria2(
                item, opts, aria2_client, job_id=job_id, emit=emit
            )
        finally:
            aria2_client.close()

    own_session = session is None
    session = session or _make_session(opts)
    cancel = opts.cancel
    dest = item.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    job_limiter = opts._job_limiter or make_job_limiter(opts.speed_limit_bps)

    if dest.exists() and dest.stat().st_size > 0:
        if emit:
            emit.done(job=job_id, file=item.file_id, path=str(dest))
        if own_session:
            session.close()
        return dest

    # multi-connection segmented path (falls back on SegmentedUnsupported)
    # callers (base/direct) set opts.segmented via should_use_segmented(site=...)
    if opts.segmented is True and effective_connections(opts) > 1:
        try:
            path = download_file_segmented(
                url=item.url,
                dest=dest,
                file_id=item.file_id,
                job_id=job_id,
                session=session,
                connections=effective_connections(opts),
                retries=opts.retries,
                timeout=opts.timeout,
                chunk_size=opts.chunk_size,
                progress_interval=opts.progress_interval,
                cancel=cancel,
                emit=emit,
                headers=item.headers,
                expected_size=item.expected_size,
                etag=item.etag,
                last_modified=item.last_modified,
                accept_ranges=item.accept_ranges,
                job_limiter=job_limiter,
            )
            if own_session:
                session.close()
            return path
        except SegmentedUnsupported:
            pass
        except Exception:
            if own_session:
                session.close()
            raise

    last_error: Exception | None = None
    try:
        # validate ETag / Last-Modified before single-connection resume
        part_size = tmp.stat().st_size if tmp.is_file() else 0
        if part_size > 0 and (item.etag or item.last_modified):
            if not _resume_validators_ok(session, item, opts):
                tmp.unlink(missing_ok=True)
                clear_state(tmp)
                part_size = 0

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
                        job_limiter=job_limiter,
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


def _resume_validators_ok(
    session: Session,
    item: download_item,
    opts: download_options,
) -> bool:
    """HEAD/Range probe; False means remote changed and resume must restart."""
    try:
        probe = probe_validators(
            session,
            item.url,
            headers=item.headers,
            timeout=opts.timeout,
        )
        state = segment_state(
            etag=item.etag,
            last_modified=item.last_modified,
            total=int(item.expected_size or 0),
        )
        return validators_match(
            state,
            etag=probe.get("etag"),
            last_modified=probe.get("last_modified"),
            total=probe.get("total"),
        )
    except Exception:  # noqa: BLE001 — probe failure: allow resume attempt
        return True


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
    job_limiter: rate_limiter | None = None,
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
            throttle(len(chunk), job_limiter)
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

    configure_global_limiter(active_network().speed_limit_bps)
    if opts._job_limiter is None and opts.speed_limit_bps:
        opts._job_limiter = make_job_limiter(opts.speed_limit_bps)

    # probe aria2 once; if down, all files use built-in
    aria2 = None
    try:
        from udl.download.aria2 import try_aria2_client

        aria2 = try_aria2_client(opts)
    except Exception:  # noqa: BLE001
        aria2 = None
    if aria2 is None:
        # prevent per-file re-probe inside download_file
        opts.aria2_enabled = False

    session = _make_session(opts)
    ok = 0
    failed = 0
    try:
        workers = max(1, min(opts.workers, len(items)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for item in items:
                if aria2 is not None:
                    from udl.download.aria2 import download_file_aria2

                    fut = pool.submit(
                        download_file_aria2,
                        item,
                        opts,
                        aria2,
                        job_id=job_id,
                        emit=emit,
                    )
                else:
                    fut = pool.submit(
                        download_file,
                        item,
                        opts,
                        session,
                        job_id=job_id,
                        emit=emit,
                    )
                futures[fut] = item
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
        if aria2 is not None:
            aria2.close()
    return ok, failed
