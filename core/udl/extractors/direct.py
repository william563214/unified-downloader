"""Generic direct-link extractor for any HTTP/HTTPS URL.

Lowest-priority router fallback: HEAD/GET probe for size filename content-type
Accept-Ranges ETag/Last-Modified then download via the shared HTTP engine.
"""

from __future__ import annotations

import hashlib
import re
from email.message import Message
from pathlib import Path, PurePosixPath
from threading import Event
from typing import Any
from urllib.parse import unquote, urlparse

import requests
from requests import Session

from udl.events import emitter
from udl.extractors.base import extract_result, extracted_file, extractor
from udl.metadata import item_metadata
from udl.naming import sanitize_component
from udl.network import active_network, apply_to_session

# lowercase constants per project convention
direct_timeout = 30.0
direct_probe_retries = 3
re_filename_star = re.compile(
    r"filename\*\s*=\s*(?:UTF-8''|utf-8'')([^;]+)",
    re.I,
)
re_filename = re.compile(
    r'filename\s*=\s*"([^"]+)"|filename\s*=\s*([^;]+)',
    re.I,
)


def matches(url: str) -> bool:
    """match any http/https URL (router registers this last)."""
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def filename_from_content_disposition(header: str | None) -> str | None:
    """parse Content-Disposition filename / filename*."""
    if not header:
        return None
    star = re_filename_star.search(header)
    if star:
        return unquote(star.group(1).strip().strip('"'))
    # email.message handles quoted / plain filename=
    msg = Message()
    msg["Content-Disposition"] = header
    name = msg.get_filename()
    if name:
        return unquote(name)
    plain = re_filename.search(header)
    if plain:
        raw = (plain.group(1) or plain.group(2) or "").strip().strip('"')
        return unquote(raw) if raw else None
    return None


def filename_from_url(url: str) -> str:
    """last path segment of URL path or host-based fallback."""
    parsed = urlparse(url)
    path = unquote(parsed.path or "")
    name = PurePosixPath(path).name if path else ""
    if name and name not in {".", ".."}:
        return name
    host = (parsed.hostname or "download").replace(".", "_")
    return f"{host}.bin"


def _truthy_ranges(value: str | None) -> bool:
    if not value:
        return False
    low = value.strip().lower()
    return low not in {"", "none"} and "bytes" in low


def probe_direct(
    url: str,
    *,
    session: Session | None = None,
    timeout: float = direct_timeout,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """HEAD then Range GET fallback; return size name type range support validators."""
    own = session is None
    session = session or Session()
    try:
        if own:
            net = active_network()
            session.headers.update(
                {
                    "User-Agent": net.user_agent or "",
                    "Accept": "*/*",
                }
            )
            apply_to_session(session)
        req_headers = dict(headers or {})
        response = None
        last_err: Exception | None = None
        for attempt in range(direct_probe_retries):
            try:
                response = session.head(
                    url,
                    headers=req_headers,
                    allow_redirects=True,
                    timeout=timeout,
                )
                # some hosts reject HEAD
                if response.status_code in (403, 405, 501) or (
                    response.status_code >= 400 and response.status_code != 404
                ):
                    response = None
                break
            except requests.RequestException as exc:
                last_err = exc
                response = None
                if attempt + 1 >= direct_probe_retries:
                    break

        if response is None or response.status_code >= 400:
            range_headers = dict(req_headers)
            range_headers["Range"] = "bytes=0-0"
            try:
                response = session.get(
                    url,
                    headers=range_headers,
                    allow_redirects=True,
                    timeout=timeout,
                    stream=True,
                )
                # discard body
                response.close()
            except requests.RequestException as exc:
                raise RuntimeError(f"probe failed for {url}: {exc or last_err}") from exc

        if response.status_code == 404:
            raise RuntimeError(f"HTTP 404 for {url}")

        hdrs = {k: v for k, v in response.headers.items()}
        final_url = str(response.url or url)
        size = _size_from_headers(hdrs, response.status_code)
        accept_ranges = _truthy_ranges(hdrs.get("Accept-Ranges"))
        if response.status_code == 206:
            accept_ranges = True
        content_type = (hdrs.get("Content-Type") or "").split(";")[0].strip() or None
        etag = hdrs.get("ETag") or hdrs.get("Etag")
        last_modified = hdrs.get("Last-Modified")
        name = filename_from_content_disposition(hdrs.get("Content-Disposition"))
        if not name:
            name = filename_from_url(final_url)
        name = sanitize_component(name)
        return {
            "url": final_url,
            "name": name,
            "size": size,
            "content_type": content_type,
            "accept_ranges": accept_ranges,
            "etag": etag,
            "last_modified": last_modified,
            "headers": hdrs,
            "status": response.status_code,
        }
    finally:
        if own:
            session.close()


def _size_from_headers(headers: dict[str, str], status: int) -> int | None:
    cr = headers.get("Content-Range")
    if cr and "/" in cr:
        total = cr.split("/")[-1].strip()
        if total.isdigit():
            return int(total)
    cl = headers.get("Content-Length")
    if cl and cl.isdigit():
        # for 206 with bytes=0-0 Content-Length is usually 1
        if status == 206 and int(cl) <= 1 and not cr:
            return None
        if status == 206 and int(cl) <= 1:
            return None
        return int(cl)
    return None


def _item_id_for(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"direct-{digest}"


class direct_extractor(extractor):
    site = "direct"
    label = "Direct"

    def __init__(self) -> None:
        self._session = Session()
        net = active_network()
        self._session.headers.update(
            {
                "User-Agent": net.user_agent or "",
                "Accept": "*/*",
            }
        )
        apply_to_session(self._session)

    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        _ = password
        info = probe_direct(url, session=self._session)
        final_url = str(info["url"])
        name = str(info["name"])
        size = info.get("size")
        size_int = int(size) if isinstance(size, int) else None
        item_id = _item_id_for(final_url)
        meta = item_metadata(
            site=self.site,
            item_id=item_id,
            canonical_url=final_url,
            title=name,
            extra={
                "content_type": info.get("content_type"),
                "accept_ranges": bool(info.get("accept_ranges")),
                "etag": info.get("etag"),
                "last_modified": info.get("last_modified"),
                "probe_status": info.get("status"),
            },
        )
        file = extracted_file(
            url=final_url,
            name=name,
            size=size_int,
            meta={
                "etag": info.get("etag"),
                "last_modified": info.get("last_modified"),
                "accept_ranges": bool(info.get("accept_ranges")),
                "content_type": info.get("content_type"),
            },
            metadata=meta,
        )
        return extract_result(
            site=self.site,
            title=name,
            files=[file],
            kind="file",
            meta={"probe": {k: info[k] for k in (
                "content_type",
                "accept_ranges",
                "etag",
                "last_modified",
                "status",
            ) if k in info}},
            metadata=meta,
        )

    def download(
        self,
        result: extract_result,
        output_dir: Path,
        *,
        emit: emitter | None = None,
        cancel: Event | None = None,
        workers: int = 3,
        retries: int = 5,
        job_id: str | None = None,
        file_dest_overrides: dict[int, Path] | None = None,
        skip_indexes: set[int] | None = None,
    ) -> tuple[int, int]:
        """prefer segmented engine for direct links."""
        from udl.download.http import (
            download_item,
            download_many,
            options_from_network,
            should_use_segmented,
        )

        job = job_id or result.site
        opts = options_from_network(
            output_dir,
            workers=workers,
            retries=retries,
            cancel=cancel,
        )
        # direct uses segmented when network allows (Range fallback inside engine)
        opts.segmented = should_use_segmented(opts, site=self.site)
        items: list[download_item] = []
        skipped = 0
        for idx, f in enumerate(result.files):
            file_id = f"{job}:{idx}"
            dest_dir = output_dir / f.relative_dir if f.relative_dir else output_dir
            dest = dest_dir / f.name
            if file_dest_overrides and idx in file_dest_overrides:
                dest = file_dest_overrides[idx]
            file_meta = f.metadata or result.metadata
            meta_payload = file_meta.to_dict(omit_empty=True) if file_meta else None
            if skip_indexes and idx in skip_indexes:
                skipped += 1
                if emit:
                    emit.file(
                        job=job,
                        id=file_id,
                        name=f.name,
                        total=f.size,
                        path=str(dest),
                        index=idx,
                        metadata=meta_payload,
                        already_downloaded=True,
                    )
                    emit.done(
                        job=job,
                        file=file_id,
                        path=str(dest),
                        skipped=True,
                        already_downloaded=True,
                    )
                continue
            items.append(
                download_item(
                    url=f.url,
                    dest=dest,
                    file_id=file_id,
                    name=f.name,
                    expected_size=f.size,
                    headers=f.headers or None,
                    etag=(f.meta or {}).get("etag"),
                    last_modified=(f.meta or {}).get("last_modified"),
                    accept_ranges=(f.meta or {}).get("accept_ranges"),
                )
            )
            if emit:
                emit.file(
                    job=job,
                    id=file_id,
                    name=f.name,
                    total=f.size,
                    path=str(dest),
                    index=idx,
                    metadata=meta_payload,
                )

        ok, failed = download_many(items, opts, job_id=job, emit=emit)
        return ok + skipped, failed
