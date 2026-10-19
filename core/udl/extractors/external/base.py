"""Base for extractors that download via an external CLI subprocess."""

from __future__ import annotations

from udl.extractors.base import extractor


class external_extractor(extractor):
    """marker base for subprocess-backed sites (MEGA etc).

    Subclasses override download() to run a CLI tool and map its output
    onto the shared JSON-lines events. Do not import or link foreign
    GPL-2.0-only libraries into this process.
    """

    transport: str = "external"
