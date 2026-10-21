"""Lazy extractor stub base (yt-dlp-inspired).

Design adapted from yt-dlp `devscripts/lazy_load_template.py`
(Unlicense; see NOTICE — yt-dlp commit 8973412).
"""

from __future__ import annotations

import importlib
from abc import ABCMeta
from typing import Any

from udl.extractors.base import extractor


class lazy_load_meta(ABCMeta):
    """load the real extractor class when a missing attribute is accessed."""

    def __getattr__(cls, name: str) -> Any:
        if name.startswith("_") or name in {"real_class", "suitable", "_match_valid_url"}:
            raise AttributeError(name)
        return getattr(cls.real_class(), name)


class lazy_load_extractor(extractor, metaclass=lazy_load_meta):
    """stub carrying `_VALID_URL` / registry metadata until first use."""

    _module: str | None = None

    @classmethod
    def real_class(cls) -> type[extractor]:
        cached = cls.__dict__.get("_real_class")
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        module_name = cls._module
        if not module_name:
            raise RuntimeError(f"{cls.__name__} has no _module for lazy load")
        mod = importlib.import_module(module_name)
        real = getattr(mod, cls.__name__)
        cls._real_class = real
        return real  # type: ignore[no-any-return]

    def __new__(cls, *args: Any, **kwargs: Any) -> extractor:
        real = cls.real_class()
        instance = real.__new__(real)
        instance.__init__(*args, **kwargs)
        return instance

    def resolve(self, url: str, *, password: str | None = None):  # type: ignore[override]
        # abstract on base; real instances come from __new__
        raise RuntimeError("lazy stub resolve should not run")
