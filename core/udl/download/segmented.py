"""IDM-style multi-connection segmented HTTP download with resume.

When a connection finishes it splits the largest remaining incomplete segment
in half and reuses the free connection. Segment map is persisted beside the
.part file for restart resume. Falls back to single-connection when the host
does not advertise Accept-Ranges / HTTP 206.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Any

import requests
from requests import Session

from udl.download.rate import make_job_limiter, rate_limiter, throttle
from udl.events import emitter

# lowercase constants
default_connections = 8
default_per_host_cap = 8
min_segment_bytes = 256 * 1024
segment_state_suffix = ".udl-seg.json"
segment_state_version = 1


@dataclass
class segment:
    """one byte range [start, end] inclusive end; done when current > end."""

    id: int
    start: int
    end: int
    current: int = 0

    @property
    def remaining(self) -> int:
        if self.current > self.end:
            return 0
        return self.end - self.current + 1

    @property
    def done(self) -> bool:
        return self.current > self.end

    @property
    def downloaded(self) -> int:
        if self.current <= self.start:
            return 0
        return min(self.current, self.end + 1) - self.start

    def to_dict(self) -> dict[str, int]:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "current": self.current,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> segment:
        return cls(
            id=int(data["id"]),
            start=int(data["start"]),
            end=int(data["end"]),
            current=int(data.get("current", data["start"])),
        )


@dataclass
class segment_state:
    """persisted multi-connection download state for one file."""

    version: int = segment_state_version
    url: str = ""
    total: int = 0
    etag: str | None = None
    last_modified: str | None = None
    segments: list[segment] = field(default_factory=list)
    next_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "url": self.url,
            "total": self.total,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "segments": [s.to_dict() for s in self.segments],
            "next_id": self.next_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> segment_state:
        segs = [segment.from_dict(s) for s in (data.get("segments") or [])]
        next_id = int(data.get("next_id") or 0)
        if segs and next_id <= max(s.id for s in segs):
            next_id = max(s.id for s in segs) + 1
        return cls(
            version=int(data.get("version") or segment_state_version),
            url=str(data.get("url") or ""),
            total=int(data.get("total") or 0),
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
            segments=segs,
            next_id=next_id,
        )

    def alloc_id(self) -> int:
        sid = self.next_id
        self.next_id += 1
        return sid

    def total_downloaded(self) -> int:
        return sum(s.downloaded for s in self.segments)


def state_path_for(tmp: Path) -> Path:
    return tmp.with_name(tmp.name + segment_state_suffix)


def load_state(path: Path) -> segment_state | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return segment_state.from_dict(data)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_state(path: Path, state: segment_state) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def clear_state(tmp: Path) -> None:
    path = state_path_for(tmp)
    path.unlink(missing_ok=True)


def validators_match(
    state: segment_state,
    *,
    etag: str | None,
    last_modified: str | None,
    total: int | None,
) -> bool:
    """True when resume is safe; False means restart from scratch."""
    if total is not None and state.total and total != state.total:
        return False
    if state.etag and etag and _norm_etag(state.etag) != _norm_etag(etag):
        return False
    if state.last_modified and last_modified and state.last_modified != last_modified:
        return False
    # if we had validators and remote no longer sends them treat as changed
    if state.etag and not etag and state.last_modified and not last_modified:
        return False
    return True


def _norm_etag(value: str) -> str:
    return value.strip()


def initial_segments(total: int, connections: int) -> list[segment]:
    """split [0, total-1] into up to `connections` contiguous ranges."""
    if total <= 0:
        return []
    n = max(1, min(connections, total))
    # avoid tiny first pieces when file is small
    while n > 1 and (total // n) < min_segment_bytes:
        n -= 1
    segs: list[segment] = []
    base = total // n
    rem = total % n
    start = 0
    for i in range(n):
        length = base + (1 if i < rem else 0)
        end = start + length - 1
        segs.append(segment(id=i, start=start, end=end, current=start))
        start = end + 1
    return segs


def split_largest(state: segment_state) -> segment | None:
    """split the largest incomplete segment in half; return the new half or None."""
    candidates = [s for s in state.segments if not s.done and s.remaining >= min_segment_bytes * 2]
    if not candidates:
        return None
    target = max(candidates, key=lambda s: s.remaining)
    # split remaining bytes in half from current cursor
    remaining = target.remaining
    first_len = remaining // 2
    if first_len < min_segment_bytes:
        return None
    mid = target.current + first_len - 1
    new_start = mid + 1
    new_end = target.end
    if new_start > new_end:
        return None
    target.end = mid
    new_seg = segment(
        id=state.alloc_id(),
        start=new_start,
        end=new_end,
        current=new_start,
    )
    state.segments.append(new_seg)
    return new_seg


def probe_validators(
    session: Session,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """lightweight HEAD/Range probe for size ETag Last-Modified Accept-Ranges."""
    req = dict(headers or {})
    response = None
    try:
        response = session.head(url, headers=req, allow_redirects=True, timeout=timeout)
        if response.status_code in (403, 405, 501) or response.status_code >= 500:
            response = None
    except requests.RequestException:
        response = None
    if response is None or response.status_code >= 400:
        range_headers = dict(req)
        range_headers["Range"] = "bytes=0-0"
        response = session.get(
            url,
            headers=range_headers,
            allow_redirects=True,
            timeout=timeout,
            stream=True,
        )
        response.close()
    hdrs = response.headers
    total = None
    cr = hdrs.get("Content-Range")
    if cr and "/" in cr:
        t = cr.split("/")[-1].strip()
        if t.isdigit():
            total = int(t)
    if total is None:
        cl = hdrs.get("Content-Length")
        if cl and cl.isdigit() and response.status_code != 206:
            total = int(cl)
    accept = (hdrs.get("Accept-Ranges") or "").lower()
    accept_ranges = "bytes" in accept or response.status_code == 206
    return {
        "url": str(response.url or url),
        "total": total,
        "etag": hdrs.get("ETag") or hdrs.get("Etag"),
        "last_modified": hdrs.get("Last-Modified"),
        "accept_ranges": accept_ranges,
        "status": response.status_code,
    }


@dataclass
class _runtime:
    state: segment_state
    lock: Lock = field(default_factory=Lock)
    cancel: Event | None = None
    last_save: float = 0.0
    last_emit: float = 0.0
    started: float = field(default_factory=time.perf_counter)
    bytes_at_start: int = 0


def download_file_segmented(
    *,
    url: str,
    dest: Path,
    file_id: str,
    job_id: str,
    session: Session,
    connections: int = default_connections,
    retries: int = 5,
    timeout: float = 30.0,
    chunk_size: int = 1024 * 1024,
    progress_interval: float = 0.25,
    cancel: Event | None = None,
    emit: emitter | None = None,
    headers: dict[str, str] | None = None,
    expected_size: int | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    accept_ranges: bool | None = None,
    job_limiter: rate_limiter | None = None,
    state_save_interval: float = 1.0,
) -> Path:
    """multi-connection download into dest; returns final path.

    Falls back to raising ``SegmentedUnsupported`` when Range is unavailable
    so the caller can use the single-connection path.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    meta_path = state_path_for(tmp)

    if dest.exists() and dest.stat().st_size > 0:
        if emit:
            emit.done(job=job_id, file=file_id, path=str(dest))
        return dest

    probe = probe_validators(session, url, headers=headers, timeout=timeout)
    final_url = str(probe.get("url") or url)
    total = probe.get("total")
    if total is None:
        total = expected_size
    remote_etag = probe.get("etag") or etag
    remote_lm = probe.get("last_modified") or last_modified
    can_range = bool(probe.get("accept_ranges"))
    if accept_ranges is False:
        can_range = False
    if accept_ranges is True:
        can_range = True

    if not can_range or not total or int(total) <= 0:
        raise SegmentedUnsupported("server does not support ranged downloads")

    total = int(total)
    existing = load_state(meta_path)
    if existing is not None:
        if not validators_match(
            existing,
            etag=remote_etag,
            last_modified=remote_lm,
            total=total,
        ):
            # remote changed — restart
            tmp.unlink(missing_ok=True)
            clear_state(tmp)
            existing = None
        elif existing.total != total:
            tmp.unlink(missing_ok=True)
            clear_state(tmp)
            existing = None

    if existing is None or not existing.segments:
        conns = max(1, min(connections, default_per_host_cap))
        segs = initial_segments(total, conns)
        state = segment_state(
            url=final_url,
            total=total,
            etag=remote_etag,
            last_modified=remote_lm,
            segments=segs,
            next_id=len(segs),
        )
        # preallocate sparse-ish file
        with tmp.open("wb") as fh:
            if total > 0:
                fh.truncate(total)
        save_state(meta_path, state)
    else:
        state = existing
        state.url = final_url
        state.etag = remote_etag or state.etag
        state.last_modified = remote_lm or state.last_modified
        if not tmp.is_file() or tmp.stat().st_size < total:
            with tmp.open("wb") as fh:
                fh.truncate(total)

    rt = _runtime(
        state=state,
        cancel=cancel,
        bytes_at_start=state.total_downloaded(),
    )
    limiter = job_limiter or make_job_limiter(None)
    max_workers = max(1, min(connections, len([s for s in state.segments if not s.done]) or 1))

    def _emit_progress(force: bool = False) -> None:
        if not emit:
            return
        now = time.perf_counter()
        if not force and (now - rt.last_emit) < progress_interval:
            return
        with rt.lock:
            downloaded = state.total_downloaded()
            segs_payload = [
                {
                    "id": s.id,
                    "start": s.start,
                    "end": s.end,
                    "current": s.current,
                    "downloaded": s.downloaded,
                    "total": s.end - s.start + 1,
                    "done": s.done,
                }
                for s in sorted(state.segments, key=lambda x: x.start)
            ]
        elapsed = max(now - rt.started, 1e-6)
        bps = max(0.0, (downloaded - rt.bytes_at_start) / elapsed)
        active = sum(
            1
            for s in segs_payload
            if not s["done"] and s["downloaded"] < s["total"]
        )
        emit.progress(
            job=job_id,
            file=file_id,
            bytes=downloaded,
            total=total,
            bps=bps,
            segments=segs_payload,
            connections=active,
        )
        rt.last_emit = now

    def _maybe_save(force: bool = False) -> None:
        now = time.perf_counter()
        if not force and (now - rt.last_save) < state_save_interval:
            return
        with rt.lock:
            save_state(meta_path, state)
        rt.last_save = now

    def _download_one(seg_id: int) -> None:
        last_error: Exception | None = None
        for attempt in range(retries):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            with rt.lock:
                seg = next((s for s in state.segments if s.id == seg_id), None)
                if seg is None or seg.done:
                    return
                start = seg.current
                end = seg.end
            if start > end:
                return
            req_headers = dict(headers or {})
            req_headers["Range"] = f"bytes={start}-{end}"
            try:
                response = session.get(
                    final_url,
                    headers=req_headers,
                    stream=True,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2**attempt, 8))
                continue

            with response:
                if response.status_code not in (206, 200):
                    last_error = RuntimeError(f"HTTP {response.status_code}")
                    if response.status_code in (403, 404, 416):
                        break
                    time.sleep(min(2**attempt, 8))
                    continue
                if response.status_code == 200 and start > 0:
                    # server ignored Range — not usable for segmented
                    raise SegmentedUnsupported("server ignored Range header")

                offset = start
                try:
                    with tmp.open("r+b") as fh:
                        fh.seek(offset)
                        for chunk in response.iter_content(chunk_size=chunk_size):
                            if cancel is not None and cancel.is_set():
                                raise InterruptedError("cancelled")
                            if not chunk:
                                continue
                            # do not write past segment end
                            with rt.lock:
                                seg = next(
                                    (s for s in state.segments if s.id == seg_id),
                                    None,
                                )
                                if seg is None:
                                    return
                                remain = seg.end - offset + 1
                                if remain <= 0:
                                    return
                                if len(chunk) > remain:
                                    chunk = chunk[:remain]
                            throttle(len(chunk), limiter)
                            fh.write(chunk)
                            offset += len(chunk)
                            with rt.lock:
                                seg = next(
                                    (s for s in state.segments if s.id == seg_id),
                                    None,
                                )
                                if seg is not None:
                                    seg.current = offset
                            _emit_progress()
                            _maybe_save()
                            if offset > end:
                                break
                except InterruptedError:
                    _maybe_save(force=True)
                    raise
                except OSError as exc:
                    last_error = exc
                    time.sleep(min(2**attempt, 8))
                    continue

            with rt.lock:
                seg = next((s for s in state.segments if s.id == seg_id), None)
                if seg is not None and seg.current > seg.end:
                    _maybe_save(force=True)
                    return
                if seg is not None and seg.current > start:
                    # partial progress kept; retry remaining
                    last_error = RuntimeError("incomplete segment read")
                    time.sleep(min(2**attempt, 8))
                    continue
            last_error = last_error or RuntimeError("segment download stalled")
            time.sleep(min(2**attempt, 8))

        if cancel is not None and cancel.is_set():
            raise InterruptedError("cancelled")
        raise RuntimeError(f"segment {seg_id} failed: {last_error}")

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            inflight: dict[Future[None], int] = {}

            def _submit_pending() -> None:
                with rt.lock:
                    pending = [s for s in state.segments if not s.done]
                    active_ids = set(inflight.values())
                    free_slots = max_workers - len(inflight)
                    for seg in pending:
                        if free_slots <= 0:
                            break
                        if seg.id in active_ids:
                            continue
                        fut = pool.submit(_download_one, seg.id)
                        inflight[fut] = seg.id
                        free_slots -= 1

            _submit_pending()
            _emit_progress(force=True)

            while inflight:
                if cancel is not None and cancel.is_set():
                    for fut in list(inflight):
                        fut.cancel()
                    raise InterruptedError("cancelled")
                done_futs = [f for f in inflight if f.done()]
                if not done_futs:
                    time.sleep(0.05)
                    continue
                for fut in done_futs:
                    inflight.pop(fut)
                    # _download_one already retries; surface failure to caller
                    fut.result()

                    # dynamic split: reuse freed connection on the largest remnant
                    with rt.lock:
                        new_seg = split_largest(state)
                        if new_seg is not None:
                            save_state(meta_path, state)
                    _submit_pending()
                    _emit_progress()

            with rt.lock:
                incomplete = [s for s in state.segments if not s.done]
            if incomplete:
                raise RuntimeError(
                    f"{len(incomplete)} segment(s) incomplete after worker pool exit"
                )

        with rt.lock:
            downloaded = state.total_downloaded()
        if downloaded != total:
            # verify file size as well
            actual = tmp.stat().st_size if tmp.is_file() else 0
            if actual != total:
                raise RuntimeError(
                    f"size mismatch: got {downloaded}/{actual} expected {total}"
                )

        _emit_progress(force=True)
        tmp.rename(dest)
        clear_state(tmp)
        if emit:
            emit.done(job=job_id, file=file_id, path=str(dest))
        return dest
    except InterruptedError:
        _maybe_save(force=True)
        raise
    except SegmentedUnsupported:
        raise
    except Exception:
        _maybe_save(force=True)
        raise


class SegmentedUnsupported(RuntimeError):
    """raised when the remote does not support multi-connection Range downloads."""


# silence unused asdict lint when helpers grow
_ = asdict
