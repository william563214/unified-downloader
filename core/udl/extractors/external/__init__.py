"""External-tool extractors (subprocess only — never import/link foreign libs)."""

from udl.extractors.external.base import external_extractor
from udl.extractors.external.mega import matches as mega_matches
from udl.extractors.external.mega import mega_extractor

__all__ = [
    "external_extractor",
    "mega_extractor",
    "mega_matches",
]
