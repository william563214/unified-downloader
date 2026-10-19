"""Bilibili extractor package (v0.7-A).

Adapted from ScottSloan/Bili23-Downloader under GPL-3.0.
See NOTICE for upstream commit SHA.
"""

from udl.extractors.bilibili.extractor import bilibili_extractor, matches, probe_info
from udl.extractors.bilibili.filter import select_filter
from udl.extractors.bilibili.login import login_stub_note, requires_login
from udl.extractors.bilibili.options import bili_options

__all__ = [
    "bilibili_extractor",
    "bili_options",
    "login_stub_note",
    "matches",
    "probe_info",
    "requires_login",
    "select_filter",
]
