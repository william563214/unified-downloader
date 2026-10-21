"""Shared fragmented media download base (HLS / DASH / HTTP segment lists).

Resume retry and append-merge live here. Per-fragment HTTP uses the shared
engine (v0.9-A segmented + optional aria2 RPC). External aria2c / ffmpeg
share the same call surface via ``udl.download.external``.

Adapted from yt-dlp ``yt_dlp/downloader/fragment.py`` (Unlicense
commit 8973412). No DRM or encrypted-stream bypass: AES-128 / SAMPLE-AES /
Widevine-style keys are rejected with a clear error.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Event, Lock
from typing import Any
from urllib.parse import urljoin

import requests
from requests import Session

from udl.download.http import (
    download_file,
    download_item,
    download_options,
    options_from_network,
)
from udl.download.rate import make_job_limiter, rate_limiter, throttle
from udl.events import emitter
from udl.network import apply_to_session, default_user_agent

# lowercase constants
fragment_state_suffix = ".udl.frag.json"
fragment_state_version = 1
default_fragment_retries = 5
default_concurrent_fragments = 1
default_chunk_size = 1024 * 1024
min_progress_interval = 0.25


class fragment_error(RuntimeError):
    """fragment download failure."""

    code = "fragment_failed"


class encrypted_stream_error(fragment_error):
    """raised when a playlist or fragment requires decryption we refuse."""

    code = "encrypted_stream"


@dataclass
class media_fragment:
    """one downloadable media fragment (HLS segment DASH chunk or HTTP piece)."""

    url: str
    index: int
    headers: dict[str, str] = field(default_factory=dict)
    # optional HTTP byte range [start, end) like yt-dlp byte_range
    byte_start: int | None = None
    byte_end: int | None = None
    # estimated size when known
    size: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class fragment_options:
    """options for a fragmented download job."""

    retries: int = default_fragment_retries
    concurrent: int = default_concurrent_fragments
    timeout: float = 30.0
    chunk_size: int = default_chunk_size
    progress_interval: float = min_progress_interval
    keep_fragments: bool = False
    skip_unavailable: bool = False
    cancel: Event | None = None
    headers: dict[str, str] | None = None
    speed_limit_bps: int | None = None
    # native | aria2c | ffmpeg | None (= native unless network says otherwise)
    backend: str | None = None
    # when True use v0.9-A multi-connection for each fragment that supports Range
    segmented_fragments: bool = False
    # pass-through download_options fields for HTTP engine
    http_opts: download_options | None = None
    _job_limiter: rate_limiter | None = field(default=None, repr=False, compare=False)


@dataclass
class fragment_state:
    """persisted resume bookkeeping beside the .part file."""

    version: int = fragment_state_version
    # next fragment index to download (0-based); completed count when sequential
    fragment_index: int = 0
    fragment_count: int | None = None
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "fragment_index": self.fragment_index,
            "fragment_count": self.fragment_count,
            "source": self.source,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> fragment_state:
        return cls(
            version=int(data.get("version") or fragment_state_version),
            fragment_index=int(data.get("fragment_index") or 0),
            fragment_count=(
                int(data["fragment_count"])
                if data.get("fragment_count") is not None
                else None
            ),
            source=str(data.get("source") or ""),
            extra=dict(data.get("extra") or {}),
        )


def state_path_for(tmp: Path) -> Path:
    return tmp.with_name(tmp.name + fragment_state_suffix)


def load_fragment_state(tmp: Path) -> fragment_state | None:
    path = state_path_for(tmp)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return fragment_state.from_dict(data)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_fragment_state(tmp: Path, state: fragment_state) -> None:
    path = state_path_for(tmp)
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_fragment_state(tmp: Path) -> None:
    state_path_for(tmp).unlink(missing_ok=True)


def reject_encrypted_markers(text: str, *, where: str = "manifest") -> None:
    """raise if playlist text asks for encryption we will not bypass."""
    upper = text.upper()
    markers = (
        "#EXT-X-KEY",
        "#EXT-X-SESSION-KEY",
        "SAMPLE-AES",
        "SAMPLE-AES-CTR",
        "com.widevine",
        "com.microsoft.playready",
        "com.apple.streamingkeydelivery",
        "urn:uuid:edef8ba9",
        "cenc:default_kid",
        "CONTENTPROTECTION",
    )
    for marker in markers:
        if marker.upper() in upper:
            raise encrypted_stream_error(
                f"encrypted or DRM-protected stream detected in {where} "
                f"({marker}); udl does not fetch keys or bypass encryption"
            )


def parse_m3u8_media_fragments(
    playlist_text: str,
    *,
    base_url: str,
) -> list[media_fragment]:
    """parse an unencrypted HLS media playlist into fragments.

    Master playlists (EXT-X-STREAM-INF) are rejected — callers should pick a
    variant URL first. Encrypted playlists raise encrypted_stream_error.
    """
    reject_encrypted_markers(playlist_text, where="HLS playlist")
    if "#EXT-X-STREAM-INF" in playlist_text.upper():
        raise fragment_error(
            "HLS master playlist is not a media segment list; "
            "resolve a variant URL first"
        )
    fragments: list[media_fragment] = []
    index = 0
    byte_start: int | None = None
    byte_length: int | None = None
    for raw in playlist_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.upper().startswith("#EXT-X-BYTERANGE:"):
                # length[@offset]
                spec = line.split(":", 1)[1].strip()
                if "@" in spec:
                    length_s, offset_s = spec.split("@", 1)
                    byte_length = int(length_s)
                    byte_start = int(offset_s)
                else:
                    byte_length = int(spec)
                    byte_start = 0 if byte_start is None else byte_start
            continue
        url = urljoin(base_url, line)
        frag = media_fragment(url=url, index=index)
        if byte_length is not None:
            start = int(byte_start or 0)
            frag.byte_start = start
            frag.byte_end = start + int(byte_length)
            byte_start = frag.byte_end
            byte_length = None
        fragments.append(frag)
        index += 1
    if not fragments:
        raise fragment_error("HLS media playlist has no segments")
    return fragments


def _is_cancelled(cancel: Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def _frag_temp_path(tmp: Path, index: int) -> Path:
    return tmp.with_name(f"{tmp.name}-Frag{index}")


def _merge_headers(
    base: dict[str, str] | None,
    extra: dict[str, str] | None,
    frag: media_fragment,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if base:
        headers.update(base)
    if extra:
        headers.update(extra)
    if frag.headers:
        headers.update(frag.headers)
    if frag.byte_start is not None and frag.byte_end is not None:
        headers["Range"] = f"bytes={frag.byte_start}-{frag.byte_end - 1}"
    elif frag.byte_start is not None:
        headers["Range"] = f"bytes={frag.byte_start}-"
    return headers


def _download_one_fragment_http(
    frag: media_fragment,
    dest_frag: Path,
    opts: fragment_options,
    *,
    job_id: str,
    file_id: str,
    session: Session,
    emit: emitter | None,
) -> Path:
    """download a single fragment via the shared HTTP engine."""
    if _is_cancelled(opts.cancel):
        raise InterruptedError("cancelled")

    http_opts = opts.http_opts
    if http_opts is None:
        http_opts = options_from_network(
            dest_frag.parent,
            retries=opts.retries,
            cancel=opts.cancel,
            headers=opts.headers,
            speed_limit_bps=opts.speed_limit_bps,
            segmented=opts.segmented_fragments,
        )
    else:
        http_opts = replace(
            http_opts,
            retries=opts.retries,
            timeout=opts.timeout,
            chunk_size=opts.chunk_size,
            cancel=opts.cancel,
            progress_interval=opts.progress_interval,
            speed_limit_bps=opts.speed_limit_bps or http_opts.speed_limit_bps,
            segmented=opts.segmented_fragments,
            _job_limiter=http_opts._job_limiter or opts._job_limiter,
        )

    headers = _merge_headers(http_opts.headers, opts.headers, frag)
    item = download_item(
        url=frag.url,
        dest=dest_frag,
        file_id=f"{file_id}:frag{frag.index}",
        name=dest_frag.name,
        expected_size=frag.size,
        headers=headers or None,
        accept_ranges=True if frag.byte_start is not None else None,
    )
    # quiet per-fragment done events; parent emits overall progress
    return download_file(
        item,
        http_opts,
        session=session,
        job_id=job_id,
        emit=None,
    )


def _append_bytes(dest_stream: Any, data: bytes, job_limiter: rate_limiter | None) -> None:
    if not data:
        return
    dest_stream.write(data)
    dest_stream.flush()
    if job_limiter is not None:
        throttle(len(data), job_limiter)


def download_fragments(
    fragments: list[media_fragment],
    dest: Path,
    *,
    job_id: str,
    file_id: str,
    opts: fragment_options | None = None,
    emit: emitter | None = None,
    source: str = "",
    session: Session | None = None,
) -> Path:
    """download fragments in order append-merge into dest with resume.

    Returns the final path. Uses native HTTP (shared engine) unless
    ``opts.backend`` is ``aria2c`` or ``ffmpeg`` — those go through
    ``udl.download.external``.
    """
    if not fragments:
        raise fragment_error("no fragments to download")

    options = opts or fragment_options()
    backend = (options.backend or "native").strip().lower()
    if backend in {"aria2c", "ffmpeg"}:
        from udl.download.external import download_fragments_external

        return download_fragments_external(
            fragments,
            dest,
            backend=backend,
            job_id=job_id,
            file_id=file_id,
            opts=options,
            emit=emit,
            source=source,
        )
    if backend not in {"native", "", "http"}:
        raise fragment_error(f"unknown fragment backend: {backend}")

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        if emit:
            emit.done(job=job_id, file=file_id, path=str(dest))
        return dest

    tmp = dest.with_name(dest.name + ".part")
    job_limiter = options._job_limiter or make_job_limiter(options.speed_limit_bps)
    own_session = session is None
    if session is None:
        http_opts = options.http_opts or options_from_network(
            dest.parent,
            retries=options.retries,
            cancel=options.cancel,
            headers=options.headers,
            speed_limit_bps=options.speed_limit_bps,
        )
        session = Session()
        session.headers.update(
            {
                "User-Agent": http_opts.user_agent or default_user_agent,
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
        )
        if http_opts.headers:
            session.headers.update(http_opts.headers)
        apply_to_session(session)
        session.headers["User-Agent"] = http_opts.user_agent or default_user_agent

    state = load_fragment_state(tmp)
    resume_from = 0
    open_mode = "wb"
    if state is not None and tmp.is_file() and tmp.stat().st_size > 0:
        resume_from = max(0, int(state.fragment_index))
        if resume_from > 0:
            open_mode = "ab"
        if state.fragment_count is not None and state.fragment_count != len(fragments):
            # playlist changed — restart
            resume_from = 0
            open_mode = "wb"
            tmp.unlink(missing_ok=True)
            clear_fragment_state(tmp)
    elif tmp.is_file() and (state is None or state.fragment_index == 0):
        # inconsistent partial without state
        tmp.unlink(missing_ok=True)
        clear_fragment_state(tmp)
        resume_from = 0
        open_mode = "wb"

    remaining = [f for f in fragments if f.index >= resume_from]
    total_frags = len(fragments)
    completed_bytes = tmp.stat().st_size if tmp.is_file() and open_mode == "ab" else 0
    state = fragment_state(
        fragment_index=resume_from,
        fragment_count=total_frags,
        source=source,
    )
    save_fragment_state(tmp, state)

    last_emit = 0.0
    lock = Lock()
    started = time.time()

    def emit_progress(done_index: int, nbytes: int) -> None:
        nonlocal last_emit, completed_bytes
        now = time.perf_counter()
        with lock:
            completed_bytes += nbytes
            cur_bytes = completed_bytes
            if emit and (now - last_emit) >= options.progress_interval:
                elapsed = max(time.time() - started, 1e-6)
                emit.progress(
                    job=job_id,
                    file=file_id,
                    bytes=cur_bytes,
                    total=None,
                    bps=cur_bytes / elapsed,
                    fragment_index=done_index,
                    fragment_count=total_frags,
                )
                last_emit = now

    try:
        concurrent = max(1, int(options.concurrent or 1))
        with tmp.open(open_mode) as dest_stream:
            if concurrent <= 1:
                for frag in remaining:
                    if _is_cancelled(options.cancel):
                        raise InterruptedError("cancelled")
                    ok = _download_and_append(
                        frag,
                        tmp,
                        dest_stream,
                        options,
                        job_id=job_id,
                        file_id=file_id,
                        session=session,
                        emit=emit,
                        job_limiter=job_limiter,
                        on_bytes=lambda n, idx=frag.index: emit_progress(idx + 1, n),
                    )
                    if not ok:
                        if options.skip_unavailable and frag.index > 0:
                            state.fragment_index = frag.index + 1
                            save_fragment_state(tmp, state)
                            continue
                        raise fragment_error(
                            f"failed to download fragment {frag.index}: {frag.url}"
                        )
                    state.fragment_index = frag.index + 1
                    save_fragment_state(tmp, state)
            else:
                # download ahead then append in order
                pending = list(remaining)
                next_append = resume_from
                ready: dict[int, Path] = {}
                with ThreadPoolExecutor(max_workers=concurrent) as pool:
                    futures = {
                        pool.submit(
                            _fetch_fragment_file,
                            frag,
                            tmp,
                            options,
                            job_id=job_id,
                            file_id=file_id,
                            session=session,
                        ): frag
                        for frag in pending
                    }
                    for future in as_completed(futures):
                        if _is_cancelled(options.cancel):
                            raise InterruptedError("cancelled")
                        frag = futures[future]
                        try:
                            path = future.result()
                        except Exception as exc:  # noqa: BLE001
                            if options.skip_unavailable and frag.index > 0:
                                ready[frag.index] = Path()  # marker skip
                                continue
                            raise fragment_error(
                                f"failed to download fragment {frag.index}: {exc}"
                            ) from exc
                        ready[frag.index] = path
                        while next_append in ready:
                            path = ready.pop(next_append)
                            if path is not None and path != Path() and path.is_file():
                                data = path.read_bytes()
                                _append_bytes(dest_stream, data, job_limiter)
                                emit_progress(next_append + 1, len(data))
                                if not options.keep_fragments:
                                    path.unlink(missing_ok=True)
                            elif path == Path() and options.skip_unavailable:
                                pass
                            else:
                                raise fragment_error(
                                    f"missing fragment file for index {next_append}"
                                )
                            state.fragment_index = next_append + 1
                            save_fragment_state(tmp, state)
                            next_append += 1

        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise fragment_error("fragment download produced an empty file")
        tmp.rename(dest)
        clear_fragment_state(tmp)
        if emit:
            emit.done(job=job_id, file=file_id, path=str(dest))
        return dest
    except InterruptedError:
        raise
    finally:
        if own_session:
            session.close()


def _fetch_fragment_file(
    frag: media_fragment,
    tmp: Path,
    opts: fragment_options,
    *,
    job_id: str,
    file_id: str,
    session: Session,
) -> Path:
    dest_frag = _frag_temp_path(tmp, frag.index)
    last_error: Exception | None = None
    for attempt in range(max(1, opts.retries)):
        if _is_cancelled(opts.cancel):
            raise InterruptedError("cancelled")
        try:
            return _download_one_fragment_http(
                frag,
                dest_frag,
                opts,
                job_id=job_id,
                file_id=file_id,
                session=session,
                emit=None,
            )
        except InterruptedError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(min(2**attempt, 8))
    raise fragment_error(str(last_error) if last_error else "fragment download failed")


def _download_and_append(
    frag: media_fragment,
    tmp: Path,
    dest_stream: Any,
    opts: fragment_options,
    *,
    job_id: str,
    file_id: str,
    session: Session,
    emit: emitter | None,
    job_limiter: rate_limiter | None,
    on_bytes: Callable[[int], None],
) -> bool:
    dest_frag = _frag_temp_path(tmp, frag.index)
    last_error: Exception | None = None
    for attempt in range(max(1, opts.retries)):
        if _is_cancelled(opts.cancel):
            raise InterruptedError("cancelled")
        try:
            path = _download_one_fragment_http(
                frag,
                dest_frag,
                opts,
                job_id=job_id,
                file_id=file_id,
                session=session,
                emit=emit,
            )
            data = path.read_bytes()
            _append_bytes(dest_stream, data, job_limiter)
            on_bytes(len(data))
            if not opts.keep_fragments:
                path.unlink(missing_ok=True)
            return True
        except InterruptedError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(min(2**attempt, 8))
    if last_error and not opts.skip_unavailable:
        raise fragment_error(
            f"fragment {frag.index} failed after retries: {last_error}"
        ) from last_error
    return False


def download_http_segmented_file(
    url: str,
    dest: Path,
    *,
    job_id: str,
    file_id: str,
    name: str | None = None,
    opts: download_options | None = None,
    emit: emitter | None = None,
    headers: dict[str, str] | None = None,
    expected_size: int | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> Path:
    """HTTP single-file download via the shared engine (v0.9-A segmented + aria2).

    Convenience wrapper so HLS/DASH/HTTP callers share one entry surface with
    fragment list downloads.
    """
    from udl.download.http import options_from_network

    dest = Path(dest)
    http_opts = opts or options_from_network(dest.parent)
    item = download_item(
        url=url,
        dest=dest,
        file_id=file_id,
        name=name or dest.name,
        expected_size=expected_size,
        headers=headers,
        etag=etag,
        last_modified=last_modified,
    )
    return download_file(item, http_opts, job_id=job_id, emit=emit)


def fetch_text(
    url: str,
    *,
    session: Session | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> str:
    """GET a playlist/manifest as text (no encryption key fetch)."""
    own = session is None
    session = session or requests.Session()
    try:
        resp = session.get(url, headers=headers or {}, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    finally:
        if own:
            session.close()


def fragment_options_from_network(
    *,
    cancel: Event | None = None,
    headers: dict[str, str] | None = None,
    speed_limit_bps: int | None = None,
    backend: str | None = None,
    concurrent: int | None = None,
    retries: int | None = None,
    http_opts: download_options | None = None,
) -> fragment_options:
    """build fragment_options seeded from active network_settings."""
    from udl.download.external import resolve_fragment_backend
    from udl.network import active_network

    net = active_network()
    resolved = resolve_fragment_backend(backend)
    conc = concurrent if concurrent is not None else net.concurrent_fragments
    return fragment_options(
        retries=retries if retries is not None else default_fragment_retries,
        concurrent=max(1, min(int(conc or 1), 32)),
        cancel=cancel,
        headers=headers,
        speed_limit_bps=speed_limit_bps if speed_limit_bps is not None else net.job_speed_limit_bps,
        backend=resolved,
        http_opts=http_opts,
        _job_limiter=make_job_limiter(
            speed_limit_bps if speed_limit_bps is not None else net.job_speed_limit_bps
        ),
    )
