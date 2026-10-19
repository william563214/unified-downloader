"""Bilibili extractor package (v0.3).

Adapted from ScottSloan/Bili23-Downloader under GPL-3.0.
See NOTICE for upstream commit SHA.
"""

from udl.extractors.bilibili.extractor import bilibili_extractor, matches, probe_info

__all__ = ["bilibili_extractor", "matches", "probe_info"]
