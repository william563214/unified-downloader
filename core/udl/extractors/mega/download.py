"""Multi-connection MEGA segmented download with AES-CTR decrypt + resume."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event, Lock

import requests
from requests import Session

from udl.download.rate import make_job_limiter, throttle
from udl.download.segment import (
    plan_mega_transfer_chunks,
    segment_state,
    state_path_for,
)
from udl.events import emitter
from udl.extractors.mega.api import mega_client, public_file_info
from udl.extractors.mega.crypto import decrypt_ctr_chunk, verify_cbc_mac
from udl.extractors.mega.errors import (
    mega_download_error,
    mega_integrity_error,
    mega_quota_error,
)
from udl.network import apply_to_session, default_user_agent

default_workers = 6
default_retries = 8
default_timeout = 30.0
connection_close = {"Connection": "close"}


def download_mega_file(
    client: mega_client,
    info: public_file_info,
    dest: Path,
    *,
    public: bool = True,
    workers: int = default_workers,
    retries: int = default_retries,
    cancel: Event | None = None,
    emit: emitter | None = None,
    job_id: str = "mega",
    file_id: str = "mega:0",
    verify_mac: bool = True,
    speed_limit_bps: int | None = None,
    progress_interval: float = 0.25,
) -> Path:
    """download one MEGA file with parallel encrypted ranges + CTR decrypt."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size == info.size:
        if emit:
            emit.done(job=job_id, file=file_id, path=str(dest))
        return dest

    part = dest.with_name(dest.name + ".part")
    state = segment_state.load_or_create(
        state_path_for(dest),
        total_size=info.size,
        meta={"handle": info.handle, "name": info.name},
    )
    plan = plan_mega_transfer_chunks(info.size, size_multi=20)
    job_limiter = make_job_limiter(speed_limit_bps)

    if not part.is_file() or part.stat().st_size != info.size:
        with open(part, "wb") as fh:
            if info.size > 0:
                fh.truncate(info.size)

    bytes_done = sum(seg.size for seg in plan.segments if state.is_done(seg.index))
    done_lock = Lock()
    last_emit = 0.0
    last_bytes = bytes_done
    last_t = time.monotonic()
    url_lock = Lock()
    download_url = info.download_url

    def current_url() -> str:
        with url_lock:
            return download_url

    def refresh_url() -> str:
        nonlocal download_url
        with url_lock:
            download_url = client.refresh_download_url(info, public=public)
            return download_url

    def emit_progress(force: bool = False) -> None:
        nonlocal last_emit, last_bytes, last_t
        if not emit:
            return
        now = time.monotonic()
        if not force and (now - last_emit) < progress_interval:
            return
        dt = max(now - last_t, 1e-6)
        bps = (bytes_done - last_bytes) / dt
        last_emit = now
        last_bytes = bytes_done
        last_t = now
        emit.progress(
            job=job_id,
            file=file_id,
            bytes=bytes_done,
            total=info.size,
            bps=bps,
        )

    def fetch_segment(seg_index: int, start: int, end: int) -> None:
        nonlocal bytes_done
        if cancel is not None and cancel.is_set():
            raise InterruptedError("cancelled")
        if state.is_done(seg_index):
            return
        size = end - start
        last_error: Exception | None = None
        for attempt in range(retries):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            session = Session()
            try:
                apply_to_session(session)
                session.headers["User-Agent"] = (
                    client.session.headers.get("User-Agent") or default_user_agent
                )
                # isolate sockets per MegaBasterd v8.33 guidance
                session.headers.update(connection_close)
                url = current_url()
                if end >= info.size:
                    range_url = f"{url}/{start}"
                else:
                    range_url = f"{url}/{start}-{end - 1}"
                try:
                    resp = session.get(range_url, stream=True, timeout=default_timeout)
                except requests.RequestException as exc:
                    last_error = exc
                    time.sleep(min(2**attempt, 20))
                    continue

                if resp.status_code == 509:
                    wait = None
                    ra = resp.headers.get("Retry-After")
                    if ra and str(ra).isdigit():
                        wait = int(ra)
                    raise mega_quota_error(
                        "MEGA transfer quota exceeded (HTTP 509). "
                        "Please wait for the quota to reset. "
                        "SmartProxy / IP rotation / quota bypass are not supported."
                        + (f" Suggested wait: ~{wait}s." if wait else ""),
                        wait_seconds=wait,
                    )
                if resp.status_code in (403, 404):
                    try:
                        refresh_url()
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                    time.sleep(min(2**attempt, 20))
                    continue
                if resp.status_code not in (200, 206):
                    last_error = mega_download_error(
                        f"CDN HTTP {resp.status_code} for range {start}-{end}"
                    )
                    time.sleep(min(2**attempt, 20))
                    continue

                buf = bytearray()
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError("cancelled")
                    if not chunk:
                        continue
                    throttle(len(chunk), job_limiter)
                    buf.extend(chunk)

                if len(buf) != size:
                    last_error = mega_download_error(
                        f"short read chunk {seg_index}: got {len(buf)} want {size}"
                    )
                    time.sleep(min(2**attempt, 20))
                    continue

                plain = decrypt_ctr_chunk(
                    bytes(buf), info.key_a32, info.iv_a32, offset=start
                )
                with open(part, "r+b") as fh:
                    fh.seek(start)
                    fh.write(plain)
                    fh.flush()
                state.mark_done(seg_index)
                with done_lock:
                    bytes_done += size
                    emit_progress()
                return
            finally:
                session.close()

        raise mega_download_error(
            f"failed segment {seg_index} after retries: {last_error}"
        )

    pending = [s for s in plan.segments if not state.is_done(s.index)]
    workers_n = max(1, min(workers, len(pending) or 1))
    try:
        with ThreadPoolExecutor(max_workers=workers_n) as pool:
            futures = [
                pool.submit(fetch_segment, s.index, s.start, s.end) for s in pending
            ]
            for fut in as_completed(futures):
                if cancel is not None and cancel.is_set():
                    for f in futures:
                        f.cancel()
                    raise InterruptedError("cancelled")
                fut.result()
    except InterruptedError:
        raise
    except mega_quota_error:
        raise
    except Exception as exc:  # noqa: BLE001
        raise mega_download_error(str(exc)) from exc

    if cancel is not None and cancel.is_set():
        raise InterruptedError("cancelled")

    if verify_mac and info.size > 0:
        ok = verify_cbc_mac(
            str(part),
            file_size=info.size,
            key_a32=info.key_a32,
            iv_a32=info.iv_a32,
            meta_mac=info.meta_mac,
        )
        if not ok:
            raise mega_integrity_error(
                f"CBC-MAC integrity check failed for {info.name}"
            )

    part.replace(dest)
    state.clear()
    if emit:
        emit.progress(
            job=job_id,
            file=file_id,
            bytes=info.size,
            total=info.size,
            bps=0,
        )
        emit.done(job=job_id, file=file_id, path=str(dest))
    return dest
