"""Shared download engine."""

from udl.download.http import download_file, download_options, options_from_network
from udl.download.segmented import download_file_segmented

__all__ = [
    "download_file",
    "download_file_segmented",
    "download_options",
    "options_from_network",
]
