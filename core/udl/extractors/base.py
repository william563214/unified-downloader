"""Extractor interface for all site modules.

Other agents adding sites should implement `extractor`
with `_VALID_URL` and register via `_extractors.py`
(lazy stubs from `scripts/make_lazy_extractors.py`).
See docs/extractor-interface.md.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

from udl.events import emitter
from udl.metadata import item_metadata


@dataclass
class extracted_file:
    """one downloadable file discovered by an extractor."""

    url: str
    name: str
    relative_dir: str = ""
    size: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    metadata: item_metadata | None = None


@dataclass
class extract_result:
    """result of resolving a URL into downloadable files."""

    site: str
    title: str
    files: list[extracted_file]
    kind: str = "unknown"
    meta: dict[str, Any] = field(default_factory=dict)
    metadata: item_metadata | None = None


def _as_url_patterns(value: Any) -> tuple[str, ...]:
    if value is None or value is False:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return (str(value),)


class extractor(ABC):
    """base class for site extractors.

    Lifecycle for `udl get`:
      1. router.match(url) -> extractor class (via `_VALID_URL` / suitable)
      2. inst = cls()  # lazy stubs import the real module here
      3. result = inst.resolve(url, password=...)
      4. inst.download(result, output_dir, emit=..., cancel=...)

    URL matching follows the yt-dlp idea: each class declares `_VALID_URL`
    (regex or sequence of regexes). Build-time lazy stubs carry only that
    metadata so sidecar startup does not import every site module.
    """

    site: str = "unknown"
    # human label for UI badge
    label: str = "Unknown"
    # registry metadata (implemented | stub | external)
    status: str = "implemented"
    kind_hint: str = "unknown"
    # yt-dlp-style URL pattern(s); False disables regex matching
    # (override suitable() instead, e.g. gallery-dl subprocess probe)
    _VALID_URL: Any = None
    # set on lazy stubs: dotted module path of the real class
    _module: str | None = None

    @classmethod
    def _match_valid_url(cls, url: str) -> re.Match[str] | None:
        """return the first `_VALID_URL` match or None."""
        patterns = _as_url_patterns(cls.__dict__.get("_VALID_URL", cls._VALID_URL))
        if not patterns:
            return None
        cache_key = "_VALID_URL_RE"
        compiled = cls.__dict__.get(cache_key)
        if compiled is None:
            compiled = tuple(re.compile(p) for p in patterns)
            setattr(cls, cache_key, compiled)
        text = (url or "").strip()
        for regex in compiled:
            matched = regex.match(text)
            if matched is not None:
                return matched
        return None

    @classmethod
    def suitable(cls, url: str) -> bool:
        """true when this extractor should handle the URL.

        Must stay import-light on overrides so lazy stubs can call it
        without pulling every site module at process start.
        """
        return cls._match_valid_url(url) is not None

    @abstractmethod
    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        """parse URL and return file list without downloading."""

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
        """download all files from an extract_result. returns (ok, failed)."""
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
        # site-aware: denylist + optional segmented_for_sites
        opts.segmented = should_use_segmented(opts, site=result.site)
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
            file_meta_dict = f.meta or {}
            items.append(
                download_item(
                    url=f.url,
                    dest=dest,
                    file_id=file_id,
                    name=f.name,
                    expected_size=f.size,
                    headers=f.headers or None,
                    etag=file_meta_dict.get("etag"),
                    last_modified=file_meta_dict.get("last_modified"),
                    accept_ranges=file_meta_dict.get("accept_ranges"),
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
