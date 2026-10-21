"""Shared download engine."""

from udl.download.external import (
    get_external_downloader,
    list_external_downloaders,
    probe_external_downloaders,
    resolve_fragment_backend,
)
from udl.download.fragment import (
    download_fragments,
    download_http_segmented_file,
    fragment_options_from_network,
    media_fragment,
    parse_m3u8_media_fragments,
)
from udl.download.http import download_file, download_options, options_from_network
from udl.download.segmented import download_file_segmented

__all__ = [
    "download_file",
    "download_file_segmented",
    "download_fragments",
    "download_http_segmented_file",
    "download_options",
    "fragment_options_from_network",
    "get_external_downloader",
    "list_external_downloaders",
    "media_fragment",
    "options_from_network",
    "parse_m3u8_media_fragments",
    "probe_external_downloaders",
    "resolve_fragment_backend",
]
