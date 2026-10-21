"""Eager extractor class list (source of truth for the lazy build).

Importing this module loads every site package. Prefer
`udl.extractors.lazy_extractors` at runtime (see `udl.router`).
Regenerate lazy stubs with `python scripts/make_lazy_extractors.py`.
"""

from __future__ import annotations

from udl.extractors.base import extractor
from udl.extractors.bilibili import bilibili_extractor
from udl.extractors.bunkr import bunkr_extractor
from udl.extractors.direct import direct_extractor
from udl.extractors.external.gallerydl import gallerydl_extractor
from udl.extractors.gofile import gofile_extractor
from udl.extractors.iwara import iwara_extractor
from udl.extractors.mega import mega_extractor

# order matters — first suitable() match wins; direct must stay last
extractors: list[type[extractor]] = [
    gofile_extractor,
    bunkr_extractor,
    iwara_extractor,
    bilibili_extractor,
    mega_extractor,
    gallerydl_extractor,
    direct_extractor,
]

__all__ = ["extractors"]
