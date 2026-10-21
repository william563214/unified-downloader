#!/usr/bin/env python3
"""Generate `core/udl/extractors/lazy_extractors.py` from live extractor classes.

Inspired by yt-dlp `devscripts/make_lazy_extractors.py` (Unlicense,
commit 8973412 — see NOTICE). Adapted for udl: per-site `_VALID_URL`,
registry metadata, and deferred suitable() for extractors that cannot
match with regex alone (e.g. gallery-dl subprocess `-E`).
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

root = Path(__file__).resolve().parents[1]
core = root / "core"
sys.path.insert(0, str(core))

# force eager imports while generating
os.environ["UDL_NO_LAZY_EXTRACTORS"] = "1"

from udl.extractors._extractors import extractors  # noqa: E402
from udl.extractors.base import extractor  # noqa: E402

outfile = core / "udl" / "extractors" / "lazy_extractors.py"

header = '''\
"""AUTO-GENERATED lazy extractor stubs — do not edit by hand.

Regenerate:
  python scripts/make_lazy_extractors.py

Inspired by yt-dlp make_lazy_extractors (Unlicense, commit 8973412).
"""

from __future__ import annotations

from udl.extractors.base import extractor
from udl.extractors.lazy_load import lazy_load_extractor

'''

stub_template = '''\
class {name}(lazy_load_extractor):
    _module = {module!r}
    site = {site!r}
    label = {label!r}
    status = {status!r}
    kind_hint = {kind_hint!r}
    _VALID_URL = {valid_url}
{extra}
'''

custom_suitable_template = '''\

    @classmethod
    def suitable(cls, url: str) -> bool:
        if cls._match_valid_url(url) is not None:
            return True
        from {module} import matches
        return matches(url)
'''


def needs_custom_suitable(cls: type[extractor]) -> bool:
    """true when suitable is overridden or `_VALID_URL` cannot match alone."""
    if cls.__dict__.get("_VALID_URL") is False:
        return True
    own = cls.__dict__.get("suitable")
    if own is not None and own is not extractor.suitable:
        return True
    return False


def format_valid_url(value: object) -> str:
    if value is False:
        return "False"
    if value is None:
        return "None"
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, (tuple, list)):
        if not value:
            return "()"
        inner = ",\n        ".join(repr(item) for item in value)
        return f"(\n        {inner},\n    )"
    return repr(value)


def build_stub(cls: type[extractor]) -> str:
    valid_url = cls.__dict__.get("_VALID_URL", getattr(cls, "_VALID_URL", None))
    extra = ""
    if needs_custom_suitable(cls):
        extra = custom_suitable_template.format(module=cls.__module__)
    return stub_template.format(
        name=cls.__name__,
        module=cls.__module__,
        site=cls.site,
        label=cls.label,
        status=cls.status,
        kind_hint=cls.kind_hint,
        valid_url=format_valid_url(valid_url),
        extra=extra,
    )


def main() -> int:
    parts = [header]
    names: list[str] = []
    for cls in extractors:
        parts.append(build_stub(cls))
        names.append(cls.__name__)
    names_lines = ",\n    ".join(names)
    all_lines = ",\n    ".join(repr(n) for n in ["extractors", *names])
    parts.append(f"extractors: list[type[extractor]] = [\n    {names_lines},\n]\n")
    parts.append(f"__all__ = [\n    {all_lines},\n]\n")
    text = "\n".join(parts)
    # normalize trailing whitespace
    text = textwrap.dedent(text)
    if not text.endswith("\n"):
        text += "\n"
    outfile.write_text(text, encoding="utf-8")
    print(f"wrote {outfile.relative_to(root)} ({len(names)} extractors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
