"""Post-download processing helpers shared across extractors."""

from udl.postprocess.artifacts import artifact_result, run_postprocess
from udl.postprocess.cover import (
    cover_dest_path,
    cover_error,
    cover_image_url,
    download_cover,
    embed_cover,
)
from udl.postprocess.ffmpeg import (
    ffmpeg_missing_error,
    find_ffmpeg,
    merge_dash,
    merge_error,
)
from udl.postprocess.ffmpeg_provision import (
    ffmpeg_provision_error,
    install_ffmpeg,
    probe_ffmpeg,
    provision_supported,
)
from udl.postprocess.nfo import build_nfo_xml, choose_nfo_kind, write_nfo
from udl.postprocess.options import parse_site_toggle_list, postprocess_options
from udl.postprocess.zip_extract import (
    after_extract_classify_hook,
    extract_options,
    extract_result,
    extract_zip,
    is_zip_candidate,
    retry_extract_path,
)

__all__ = [
    "after_extract_classify_hook",
    "artifact_result",
    "build_nfo_xml",
    "choose_nfo_kind",
    "cover_dest_path",
    "cover_error",
    "cover_image_url",
    "download_cover",
    "embed_cover",
    "extract_options",
    "extract_result",
    "extract_zip",
    "ffmpeg_missing_error",
    "ffmpeg_provision_error",
    "find_ffmpeg",
    "install_ffmpeg",
    "is_zip_candidate",
    "merge_dash",
    "merge_error",
    "parse_site_toggle_list",
    "postprocess_options",
    "probe_ffmpeg",
    "provision_supported",
    "retry_extract_path",
    "run_postprocess",
    "write_nfo",
]
