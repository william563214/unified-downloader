"""Extractor interface for all site modules.

Other agents adding Iwara / Bilibili should implement `extractor`
and register patterns in `udl.router`. See docs/extractor-interface.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

from udl.events import emitter


@dataclass
class extracted_file:
    """one downloadable file discovered by an extractor."""

    url: str
    name: str
    relative_dir: str = ""
    size: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class extract_result:
    """result of resolving a URL into downloadable files."""

    site: str
    title: str
    files: list[extracted_file]
    kind: str = "unknown"
    meta: dict[str, Any] = field(default_factory=dict)


class extractor(ABC):
    """base class for site extractors.

    Lifecycle for `udl get`:
      1. router.match(url) -> extractor class
      2. inst = cls()
      3. result = inst.resolve(url, password=...)
      4. inst.download(result, output_dir, emit=..., cancel=...)
    """

    site: str = "unknown"
    # human label for UI badge
    label: str = "Unknown"

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
    ) -> tuple[int, int]:
        """download all files from an extract_result. returns (ok, failed)."""
        from udl.download.http import download_item, download_many, download_options

        job = job_id or result.site
        opts = download_options(
            output_dir=output_dir,
            workers=workers,
            retries=retries,
            cancel=cancel,
        )
        items: list[download_item] = []
        for idx, f in enumerate(result.files):
            file_id = f"{job}:{idx}"
            dest_dir = output_dir / f.relative_dir if f.relative_dir else output_dir
            dest = dest_dir / f.name
            items.append(
                download_item(
                    url=f.url,
                    dest=dest,
                    file_id=file_id,
                    name=f.name,
                    expected_size=f.size,
                    headers=f.headers or None,
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
                )

        return download_many(items, opts, job_id=job, emit=emit)
