"""udl CLI — resolve, probe, get, history, queue with optional JSON-lines."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import uuid
from pathlib import Path
from threading import Event
from typing import Any

from udl import __version__
from udl.checksum import expected_from_cli
from udl.cli_iwara import add_iwara_parser, cmd_iwara
from udl.cli_mega import add_mega_parser, cmd_mega
from udl.cli_schedule import add_schedule_parser, cmd_queue_schedule
from udl.config import load_config, save_config, udl_config
from udl.download.rate import flush_traffic_accounting, set_traffic_recorder
from udl.events import dump_schema, emitter
from udl.metadata import item_metadata
from udl.naming import preview_template
from udl.network import configure_network, settings_from_cli_args
from udl.page import page_filter, parse_extensions, parse_size_bound, parse_types, scan_page
from udl.postprocess.artifacts import run_postprocess
from udl.postprocess.hook import hook_options
from udl.postprocess.options import parse_site_toggle_list, postprocess_options
from udl.repair import apply_repair_actions, scan_repair
from udl.router import get_extractor, resolve
from udl.rules import parse_rules, test_rules_against_metadata
from udl.scheduler import note_traffic
from udl.secrets import delete_secret, get_secret, set_secret
from udl.session import (
    finalize_download,
    metadata_payload,
    open_store,
    plan_file_destinations,
    post_download_verify,
    prepare_download,
)
from udl.store.duplicates import duplicate_mode
from udl.store.history import delete_history, get_history, list_history
from udl.store.queue import delete_queue_job, list_queue, load_queue_job, update_queue_status


def _add_gallerydl_args(parser: argparse.ArgumentParser, *, download: bool = False) -> None:
    """shared gallery-dl subprocess flags (resolve needs them before match)."""
    parser.add_argument(
        "--gallerydl-tool",
        default=None,
        help="path to gallery-dl executable (or UDL_GALLERY_DL)",
    )
    parser.add_argument(
        "--no-gallerydl",
        action="store_true",
        help="disable gallery-dl fallback extractor for this run",
    )
    if download:
        parser.add_argument(
            "--gallerydl-cookies",
            default=None,
            help="Netscape cookies.txt passed to gallery-dl --cookies",
        )
        parser.add_argument(
            "--gallerydl-option",
            action="append",
            default=None,
            metavar="KEY=VALUE",
            help="pass -o KEY=VALUE to gallery-dl (repeatable)",
        )
        parser.add_argument(
            "--gallerydl-username",
            default=None,
            help="gallery-dl login username (prefer udl.secrets gallerydl.username)",
        )
        parser.add_argument(
            "--gallerydl-password",
            default=None,
            help="gallery-dl login password (prefer udl.secrets; never logged)",
        )
        parser.add_argument(
            "--gallerydl-archive",
            nargs="?",
            const="default",
            default=None,
            help=(
                "use gallery-dl --download-archive (optional path; "
                "default: app data gallery-dl-archive.sqlite3)"
            ),
        )


def _add_network_args(parser: argparse.ArgumentParser, *, download: bool = False) -> None:
    """shared proxy / UA / speed / aria2 flags for resolve probe get."""
    parser.add_argument(
        "--user-agent",
        default=None,
        help="custom User-Agent for API calls and downloads",
    )
    parser.add_argument(
        "--proxy-mode",
        choices=["none", "system", "custom"],
        default="none",
        help="proxy mode: none (default) system or custom",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="custom proxy URL (http/https/socks5) or shorthand none|system",
    )
    parser.add_argument("--proxy-user", default=None, help="proxy username for custom mode")
    parser.add_argument(
        "--proxy-password",
        default=None,
        help="proxy password (prefer OS keychain via `udl secrets`)",
    )
    if download:
        parser.add_argument(
            "--speed-limit",
            default=None,
            help="overall speed cap e.g. 1M 512K (bytes/sec human form)",
        )
        parser.add_argument(
            "--job-speed-limit",
            default=None,
            help="per-job speed cap e.g. 500K",
        )
        parser.add_argument(
            "--aria2",
            action="store_true",
            help="hand file downloads to aria2 via JSON-RPC when reachable",
        )
        parser.add_argument(
            "--aria2-rpc",
            default=None,
            help="aria2 JSON-RPC URL (default http://127.0.0.1:6800/jsonrpc)",
        )
        parser.add_argument(
            "--aria2-secret",
            default=None,
            help="aria2 RPC secret token (or store via udl secrets aria2.rpc_secret)",
        )
        parser.add_argument(
            "--connections",
            type=int,
            default=None,
            help="max connections per file for segmented downloads (default 8)",
        )
        parser.add_argument(
            "--per-host-connections",
            type=int,
            default=None,
            help="per-host connection cap for segmented downloads (default = --connections)",
        )
        parser.add_argument(
            "--no-segmented",
            action="store_true",
            help="disable multi-connection segmented downloads (single connection)",
        )
        parser.add_argument(
            "--segmented-for-sites",
            action="store_true",
            help=(
                "also use segmented downloads for site extractors that yield plain "
                "HTTP file URLs (gofile bunkr iwara mega excluded)"
            ),
        )
    parser.add_argument(
        "--cdn-hosts",
        default=None,
        help=(
            "comma-separated preferred Bilibili CDN hostnames "
            "(e.g. upos-sz-mirrorcos.bilivideo.com)"
        ),
    )
    parser.add_argument(
        "--prefer-cdn",
        action="store_true",
        help="prefer replaced CDN hosts ahead of original playurl URLs",
    )
    parser.add_argument(
        "--custom-host",
        default=None,
        help="optional Host header override for media downloads",
    )


def _apply_network(args: argparse.Namespace) -> None:
    configure_network(settings_from_cli_args(args))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="udl",
        description="整合下載器 unified downloader core",
    )
    parser.add_argument("--version", action="version", version=f"udl {__version__}")
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite path (default: app data dir / udl.sqlite3 or UDL_DB)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_resolve = sub.add_parser("resolve", help="detect site for a URL")
    p_resolve.add_argument("url", help="content URL")
    p_resolve.add_argument(
        "--json",
        action="store_true",
        help="emit a JSON-lines resolve event (default: pretty JSON object)",
    )
    _add_network_args(p_resolve)
    _add_gallerydl_args(p_resolve)

    p_probe = sub.add_parser(
        "probe",
        help="fetch site metadata (parts / qualities) without downloading",
    )
    p_probe.add_argument(
        "url",
        nargs="+",
        help="content URL(s); multiple = batch probe (bilibili)",
    )
    p_probe.add_argument(
        "--json",
        action="store_true",
        help="emit a JSON-lines probe event (default: pretty JSON object)",
    )
    p_probe.add_argument(
        "--page",
        type=int,
        default=1,
        help="bilibili list page number (space/favlist/collection)",
    )
    p_probe.add_argument(
        "--keyword",
        default=None,
        help="bilibili keyword filter for space/favlist server search",
    )
    p_probe.add_argument(
        "--expand-all",
        action="store_true",
        help=(
            "bilibili: paginate lists and expand followed bangumi / "
            "favorite folders / subscribed collections / UP space"
        ),
    )
    p_probe.add_argument(
        "--filter-title",
        default=None,
        help="bilibili filter during expand-all: title keyword",
    )
    p_probe.add_argument(
        "--filter-duration-min",
        type=float,
        default=None,
        help="bilibili filter during expand-all: min duration seconds",
    )
    p_probe.add_argument(
        "--filter-duration-max",
        type=float,
        default=None,
        help="bilibili filter during expand-all: max duration seconds",
    )
    p_probe.add_argument(
        "--part-range",
        default=None,
        help="bilibili filter during expand-all: page range e.g. 1-5",
    )
    _add_network_args(p_probe)
    _add_gallerydl_args(p_probe)

    p_get = sub.add_parser("get", help="download a URL")
    p_get.add_argument("url", help="content URL")
    p_get.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        help="output directory",
    )
    p_get.add_argument(
        "--json",
        action="store_true",
        help="emit JSON-lines progress events on stdout",
    )
    p_get.add_argument("--password", default=None, help="content password if required")
    p_get.add_argument(
        "--workers",
        type=int,
        default=3,
        help="concurrent file downloads (default 3)",
    )
    p_get.add_argument(
        "--retries",
        type=int,
        default=5,
        help="per-file retry count (default 5)",
    )
    p_get.add_argument(
        "--job-id",
        default=None,
        help="optional job id for events (default: random)",
    )
    p_get.add_argument(
        "--quality",
        default=None,
        help=(
            "site quality: Iwara Source|540|360; "
            "Bilibili qn number (e.g. 64) or omit for best available without login"
        ),
    )
    p_get.add_argument(
        "--part",
        default=None,
        help="bilibili part/page number (1-based) or 'all'",
    )
    p_get.add_argument(
        "--codec",
        default=None,
        help="bilibili video codec: avc|hevc|av1 or codecid 7|12|13",
    )
    p_get.add_argument(
        "--audio-quality",
        default=None,
        help="bilibili audio id (30251 Hi-Res / 30250 Dolby / 30280 / 30232 / 30216)",
    )
    p_get.add_argument(
        "--container",
        choices=("mp4", "mkv"),
        default="mp4",
        help="bilibili output container (default mp4)",
    )
    p_get.add_argument(
        "--danmaku",
        choices=("xml", "ass", "json"),
        default=None,
        help="export bilibili danmaku in this format",
    )
    p_get.add_argument(
        "--subtitle",
        choices=("srt", "lrc", "txt", "ass", "json"),
        default=None,
        help="export bilibili subtitles in this format",
    )
    p_get.add_argument(
        "--subtitle-lang",
        default=None,
        help="comma-separated subtitle language codes (default: all available)",
    )
    p_get.add_argument(
        "--embed-danmaku",
        action="store_true",
        help="embed ASS danmaku as subtitle track (implies --danmaku ass)",
    )
    p_get.add_argument(
        "--embed-subtitle",
        action="store_true",
        help="embed ASS subtitles into mkv/mp4 (implies --subtitle ass)",
    )
    p_get.add_argument(
        "--no-chapters",
        action="store_true",
        help="skip writing bilibili chapters when available",
    )
    p_get.add_argument(
        "--filter-title",
        default=None,
        help="bilibili auto-select: title keyword",
    )
    p_get.add_argument(
        "--filter-date-from",
        default=None,
        help="bilibili auto-select: upload date from YYYY-MM-DD",
    )
    p_get.add_argument(
        "--filter-date-to",
        default=None,
        help="bilibili auto-select: upload date to YYYY-MM-DD",
    )
    p_get.add_argument(
        "--filter-duration-min",
        type=float,
        default=None,
        help="bilibili auto-select: min duration seconds",
    )
    p_get.add_argument(
        "--filter-duration-max",
        type=float,
        default=None,
        help="bilibili auto-select: max duration seconds",
    )
    p_get.add_argument(
        "--part-range",
        default=None,
        help="bilibili auto-select: page range e.g. 1-5",
    )
    p_get.add_argument(
        "--expand-all",
        action="store_true",
        help=(
            "bilibili: paginate and expand nested lists before download "
            "(followed bangumi / fav folders / collections / UP space)"
        ),
    )
    p_get.add_argument(
        "--node-names",
        default=None,
        help=(
            "bilibili interactive: JSON map of page/edge/cid -> title "
            'e.g. \'{"1":"開頭","edge:12":"壞結局"}\''
        ),
    )
    p_get.add_argument(
        "--sessdata",
        default=None,
        help=(
            "optional Bilibili SESSDATA for this run (in-memory). "
            "Prefer `udl bili login` which stores session in OS keychain"
        ),
    )
    p_get.add_argument(
        "--ffmpeg",
        default=None,
        help="path to ffmpeg binary (default: PATH or UDL_FFMPEG)",
    )
    p_get.add_argument(
        "--cloudflare-bypass",
        action="store_true",
        help="Iwara opt-in Cloudflare bypass via cloudscraper (disabled by default)",
    )
    p_get.add_argument(
        "--token",
        default=None,
        help="optional Iwara Bearer override (else use keychain session)",
    )
    p_get.add_argument(
        "--mega-tool",
        default=None,
        help="path to megatools or mega-get when --mega-backend=external",
    )
    p_get.add_argument(
        "--mega-backend",
        choices=("builtin", "external"),
        default=None,
        help="MEGA download backend (default: builtin; or UDL_MEGA_BACKEND)",
    )
    p_get.add_argument(
        "--mega-select",
        default=None,
        help="comma-separated MEGA node handles to download from a folder",
    )
    p_get.add_argument(
        "--mega-no-verify",
        action="store_true",
        help="skip MEGA CBC-MAC integrity check after download",
    )
    _add_gallerydl_args(p_get, download=True)
    p_get.add_argument(
        "--on-duplicate",
        choices=("skip", "overwrite", "rename"),
        default="skip",
        help="when history/path already has this item (default: skip)",
    )
    p_get.add_argument(
        "--no-history",
        action="store_true",
        help="do not write history / queue to SQLite",
    )
    p_get.add_argument(
        "--write-nfo",
        action="store_true",
        help="write Kodi/Jellyfin/Emby .nfo sidecars from metadata",
    )
    p_get.add_argument(
        "--write-cover",
        action="store_true",
        help="download cover/thumbnail next to media (poster or folder.jpg)",
    )
    p_get.add_argument(
        "--embed-cover",
        action="store_true",
        help="embed downloaded cover into mp4/mkv via FFmpeg (implies --write-cover)",
    )
    p_get.add_argument(
        "--cover-name",
        choices=("poster", "folder"),
        default="poster",
        help="cover filename style: <stem>-poster.jpg or folder.jpg (default: poster)",
    )
    p_get.add_argument(
        "--nfo-sites",
        default="all",
        help=(
            "sites allowed for NFO when --write-nfo is set "
            "(all|none|comma list; prefix - to disable e.g. all,-mega)"
        ),
    )
    p_get.add_argument(
        "--cover-sites",
        default="all",
        help=(
            "sites allowed for cover when --write-cover/--embed-cover is set "
            "(all|none|comma list; prefix - to disable)"
        ),
    )
    p_get.add_argument(
        "--name-template",
        default=None,
        help=(
            "output path template using metadata fields "
            "e.g. '{site}/{author}/{date:%%Y-%%m}/{title} [{id}].{ext}'"
        ),
    )
    p_get.add_argument(
        "--config",
        default=None,
        help="path to udl.config.json / .toml (naming templates + classification rules)",
    )
    p_get.add_argument(
        "--checksum",
        default=None,
        help="expected digest as algo:hex (md5|sha1|sha256) or bare hex",
    )
    p_get.add_argument("--md5", default=None, help="expected MD5 hex digest")
    p_get.add_argument("--sha1", default=None, help="expected SHA-1 hex digest")
    p_get.add_argument("--sha256", default=None, help="expected SHA-256 hex digest")
    p_get.add_argument(
        "--no-checksum-auto",
        action="store_true",
        help="do not auto-pick checksum from site metadata or .sha256 sidecar",
    )
    p_get.add_argument(
        "--post-cmd",
        default=None,
        help="post-download command template ({path} = local file); off unless set",
    )
    p_get.add_argument(
        "--post-cmd-preset",
        choices=("defender", "clamav", "windows_defender", "clamscan"),
        default=None,
        help="post-download antivirus preset (defender / clamav)",
    )
    p_get.add_argument(
        "--post-cmd-timeout",
        type=int,
        default=None,
        help="post-download command timeout seconds (default 120)",
    )
    _add_network_args(p_get, download=True)

    p_page = sub.add_parser(
        "page",
        help="scan a web page for downloadable candidate links (Download All)",
    )
    p_page.add_argument("url", help="web page URL")
    p_page.add_argument(
        "--json",
        action="store_true",
        help="emit a JSON page event (default: pretty JSON object)",
    )
    p_page.add_argument(
        "--ext",
        default=None,
        help="comma-separated extensions filter e.g. mp4,mkv,zip",
    )
    p_page.add_argument(
        "--type",
        dest="types",
        default=None,
        help="comma-separated kinds: video,audio,image,archive,document,file,site",
    )
    p_page.add_argument("--size-min", default=None, help="min size via HEAD e.g. 1M")
    p_page.add_argument("--size-max", default=None, help="max size via HEAD e.g. 2G")
    p_page.add_argument("--regex", default=None, help="regex filter on url/name/text")
    p_page.add_argument(
        "--glob",
        default=None,
        help="glob filter on name/url; use | for alternatives",
    )
    p_page.add_argument(
        "--same-domain",
        action="store_true",
        help="only keep candidates on the page host",
    )
    p_page.add_argument(
        "--images",
        action="store_true",
        help="include <img> sources",
    )
    p_page.add_argument(
        "--no-head",
        action="store_true",
        help="skip HEAD size probes",
    )
    _add_network_args(p_page)

    p_schema = sub.add_parser("schema", help="print JSON-lines event schema")
    p_schema.add_argument(
        "--json",
        action="store_true",
        default=True,
        help="print schema as JSON (default)",
    )

    p_sites = sub.add_parser("sites", help="list registered sites")
    p_sites.add_argument("--json", action="store_true")

    p_gdl = sub.add_parser(
        "gallerydl",
        help="gallery-dl external tool status / supported sites (subprocess)",
    )
    gdl_sub = p_gdl.add_subparsers(dest="gallerydl_command", required=True)
    p_gdl_status = gdl_sub.add_parser("status", help="detect gallery-dl executable")
    p_gdl_status.add_argument(
        "--gallerydl-tool",
        default=None,
        help="explicit gallery-dl path to check",
    )
    p_gdl_status.add_argument("--json", action="store_true")
    p_gdl_sites = gdl_sub.add_parser(
        "sites",
        help="list gallery-dl modules or extractors (--extractors for full list)",
    )
    p_gdl_sites.add_argument(
        "--gallerydl-tool",
        default=None,
        help="explicit gallery-dl path",
    )
    p_gdl_sites.add_argument(
        "--extractors",
        action="store_true",
        help="list extractor classes (slower) instead of module names",
    )
    p_gdl_sites.add_argument("--json", action="store_true")

    p_ffmpeg = sub.add_parser("ffmpeg", help="locate or install a static FFmpeg build")
    ff_sub = p_ffmpeg.add_subparsers(dest="ffmpeg_command", required=True)
    p_ff_status = ff_sub.add_parser("status", help="show whether FFmpeg is available")
    p_ff_status.add_argument(
        "--ffmpeg",
        default=None,
        help="explicit ffmpeg path to check",
    )
    p_ff_status.add_argument("--json", action="store_true")
    p_ff_install = ff_sub.add_parser(
        "install",
        help="download a checksum-verified static build into the app data dir",
    )
    p_ff_install.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="install directory (default: platform app data /ffmpeg)",
    )
    p_ff_install.add_argument("--json", action="store_true")

    p_history = sub.add_parser("history", help="search manage download history")
    hist_sub = p_history.add_subparsers(dest="history_command", required=True)
    p_hist_list = hist_sub.add_parser("list", help="list / search history entries")
    p_hist_list.add_argument("--json", action="store_true")
    p_hist_list.add_argument("--site", default=None)
    p_hist_list.add_argument("--status", default=None)
    p_hist_list.add_argument("--tag", default=None)
    p_hist_list.add_argument("--query", "-q", default=None)
    p_hist_list.add_argument("--from", dest="date_from", default=None)
    p_hist_list.add_argument("--to", dest="date_to", default=None)
    p_hist_list.add_argument("--sort", default="created_at")
    p_hist_list.add_argument("--order", choices=("asc", "desc"), default="desc")
    p_hist_list.add_argument("--limit", type=int, default=50)
    p_hist_list.add_argument("--offset", type=int, default=0)
    p_hist_list.add_argument("--no-files", action="store_true")

    p_hist_show = hist_sub.add_parser("show", help="show one history job")
    p_hist_show.add_argument("id", help="job id")
    p_hist_show.add_argument("--json", action="store_true")

    p_hist_delete = hist_sub.add_parser("delete", help="remove history entries")
    p_hist_delete.add_argument("ids", nargs="+", help="job ids")
    p_hist_delete.add_argument(
        "--delete-files",
        action="store_true",
        help="also delete output files on disk",
    )
    p_hist_delete.add_argument("--json", action="store_true")

    p_queue = sub.add_parser("queue", help="persistent task queue")
    queue_sub = p_queue.add_subparsers(dest="queue_command", required=True)
    p_q_list = queue_sub.add_parser("list", help="list queue jobs")
    p_q_list.add_argument("--json", action="store_true")
    p_q_list.add_argument("--status", default=None)
    p_q_list.add_argument(
        "--unfinished",
        action="store_true",
        help="only queued/running/paused jobs",
    )
    p_q_list.add_argument("--limit", type=int, default=200)

    p_q_show = queue_sub.add_parser("show", help="show one queue job")
    p_q_show.add_argument("id", help="job id")
    p_q_show.add_argument("--json", action="store_true")

    p_q_delete = queue_sub.add_parser("delete", help="remove queue jobs")
    p_q_delete.add_argument("ids", nargs="+")
    p_q_delete.add_argument("--json", action="store_true")

    p_q_status = queue_sub.add_parser("set-status", help="update queue job status")
    p_q_status.add_argument("id")
    p_q_status.add_argument(
        "status",
        choices=("queued", "running", "paused", "done", "error", "cancelled", "skipped"),
    )
    p_q_status.add_argument("--message", default=None)
    p_q_status.add_argument("--json", action="store_true")

    p_q_list.add_argument(
        "--queue",
        default=None,
        dest="queue_name",
        help="filter by named queue (v0.9 scheduler)",
    )
    add_schedule_parser(queue_sub)

    p_naming = sub.add_parser("naming", help="name template preview helpers")
    naming_sub = p_naming.add_subparsers(dest="naming_command", required=True)
    p_naming_preview = naming_sub.add_parser("preview", help="preview a name template")
    p_naming_preview.add_argument(
        "--template",
        required=True,
        help="template string with {field} / {date:%%Y-%%m} placeholders",
    )
    p_naming_preview.add_argument(
        "--metadata",
        default="{}",
        help="JSON object of item_metadata fields",
    )
    p_naming_preview.add_argument(
        "--name",
        default="example.mp4",
        help="original filename used for {ext} / {name}",
    )
    p_naming_preview.add_argument("--json", action="store_true")

    p_rules = sub.add_parser("rules", help="classification rules")
    rules_sub = p_rules.add_subparsers(dest="rules_command", required=True)
    p_rules_list = rules_sub.add_parser("list", help="list rules from config")
    p_rules_list.add_argument("--config", default=None)
    p_rules_list.add_argument("--json", action="store_true")
    p_rules_test = rules_sub.add_parser("test", help="test rules against a URL or metadata")
    p_rules_test.add_argument("url", nargs="?", default=None, help="optional URL to probe")
    p_rules_test.add_argument("--config", default=None)
    p_rules_test.add_argument("--metadata", default=None, help="JSON metadata override")
    p_rules_test.add_argument("--name", default=None, help="original filename hint")
    p_rules_test.add_argument("--size", type=int, default=None)
    p_rules_test.add_argument("--json", action="store_true")

    p_config = sub.add_parser("config", help="show or write naming/rules config")
    config_sub = p_config.add_subparsers(dest="config_command", required=True)
    p_cfg_show = config_sub.add_parser("show", help="print effective config")
    p_cfg_show.add_argument("--config", default=None)
    p_cfg_show.add_argument("--json", action="store_true")
    p_cfg_save = config_sub.add_parser("save", help="write config JSON from stdin or --data")
    p_cfg_save.add_argument("--config", default=None)
    p_cfg_save.add_argument("--data", default=None, help="JSON config payload")
    p_cfg_save.add_argument("--json", action="store_true")

    p_repair = sub.add_parser("repair", help="scan / fix history vs filesystem")
    repair_sub = p_repair.add_subparsers(dest="repair_command", required=True)
    p_repair_scan = repair_sub.add_parser("scan", help="scan for missing/partial/mismatch")
    p_repair_scan.add_argument("--json", action="store_true")
    p_repair_scan.add_argument(
        "--dir",
        action="append",
        dest="dirs",
        default=None,
        help="extra output dir to scan for orphans (repeatable)",
    )
    p_repair_scan.add_argument(
        "--verify-hash",
        action="store_true",
        help="recompute sha256 (slower)",
    )
    p_repair_scan.add_argument(
        "--no-orphans",
        action="store_true",
        help="skip orphan file discovery under output dirs",
    )
    p_repair_fix = repair_sub.add_parser("fix", help="apply a repair action")
    p_repair_fix.add_argument(
        "action",
        choices=("requeue", "relink", "remove-stale", "cleanup-partials"),
    )
    p_repair_fix.add_argument("--json", action="store_true")
    p_repair_fix.add_argument("--job-id", action="append", dest="job_ids", default=None)
    p_repair_fix.add_argument(
        "--file-row-id",
        action="append",
        dest="file_row_ids",
        type=int,
        default=None,
    )
    p_repair_fix.add_argument("--relink-path", default=None)
    p_repair_fix.add_argument("--output", default=None, help="output dir for requeue")
    p_repair_fix.add_argument(
        "--issues",
        default=None,
        help="JSON array of issue objects from repair scan",
    )

    p_secrets = sub.add_parser(
        "secrets", help="OS keychain helpers for proxy / login tokens"
    )
    sec_sub = p_secrets.add_subparsers(dest="secrets_command", required=True)
    p_sec_set = sec_sub.add_parser("set", help="store a secret")
    p_sec_set.add_argument("key", help="secret key e.g. proxy.custom bilibili.session")
    p_sec_set.add_argument("value", nargs="?", default=None, help="value (or read stdin)")
    p_sec_get = sec_sub.add_parser("get", help="read a secret (prints nothing if missing)")
    p_sec_get.add_argument("key", help="secret key")
    p_sec_get.add_argument("--json", action="store_true")
    p_sec_del = sec_sub.add_parser("delete", help="delete a secret")
    p_sec_del.add_argument("key", help="secret key")
    p_sec_del.add_argument("--json", action="store_true")

    add_iwara_parser(sub)
    add_mega_parser(sub)

    p_bili = sub.add_parser("bili", help="Bilibili account login / session")
    bili_sub = p_bili.add_subparsers(dest="bili_command", required=True)
    p_bili_login = bili_sub.add_parser("login", help="QR Cookie SMS login helpers")
    login_sub = p_bili_login.add_subparsers(dest="login_command", required=True)

    p_login_status = login_sub.add_parser("status", help="validate stored session")
    p_login_status.add_argument("--json", action="store_true")
    _add_network_args(p_login_status)

    p_login_refresh = login_sub.add_parser("refresh", help="refresh / re-validate session")
    p_login_refresh.add_argument("--json", action="store_true")
    _add_network_args(p_login_refresh)

    p_login_logout = login_sub.add_parser("logout", help="clear session from keychain")
    p_login_logout.add_argument("--json", action="store_true")
    _add_network_args(p_login_logout)

    p_login_qr = login_sub.add_parser("qr", help="generate QR code for scan login")
    p_login_qr.add_argument("--json", action="store_true")
    p_login_qr.add_argument(
        "--no-image",
        action="store_true",
        help="omit PNG base64 in JSON (url/key only)",
    )
    _add_network_args(p_login_qr)

    p_login_qr_poll = login_sub.add_parser("qr-poll", help="poll QR scan status")
    p_login_qr_poll.add_argument("--key", required=True, help="qrcode_key from qr")
    p_login_qr_poll.add_argument("--json", action="store_true")
    _add_network_args(p_login_qr_poll)

    p_login_cookie = login_sub.add_parser("cookie", help="login by pasting Cookie text")
    p_login_cookie.add_argument(
        "--text",
        default=None,
        help="cookie header or JSON (default: read stdin)",
    )
    p_login_cookie.add_argument("--json", action="store_true")
    _add_network_args(p_login_cookie)

    p_login_sms_cap = login_sub.add_parser(
        "sms-captcha",
        help="init GeeTest captcha for SMS (best-effort; needs browser solve)",
    )
    p_login_sms_cap.add_argument("--json", action="store_true")
    _add_network_args(p_login_sms_cap)

    p_login_sms_send = login_sub.add_parser(
        "sms-send",
        help="send SMS code after external GeeTest (best-effort)",
    )
    p_login_sms_send.add_argument("--tel", required=True)
    p_login_sms_send.add_argument("--cid", default="86")
    p_login_sms_send.add_argument("--token", required=True)
    p_login_sms_send.add_argument("--challenge", required=True)
    p_login_sms_send.add_argument("--validate", required=True)
    p_login_sms_send.add_argument("--seccode", required=True)
    p_login_sms_send.add_argument("--json", action="store_true")
    _add_network_args(p_login_sms_send)

    p_login_sms = login_sub.add_parser("sms", help="complete SMS login (best-effort)")
    p_login_sms.add_argument("--tel", required=True)
    p_login_sms.add_argument("--cid", default="86")
    p_login_sms.add_argument("--code", required=True, help="SMS verification code")
    p_login_sms.add_argument("--captcha-key", required=True)
    p_login_sms.add_argument("--json", action="store_true")
    _add_network_args(p_login_sms)

    return parser


def _apply_gallerydl(args: argparse.Namespace) -> None:
    """apply gallery-dl process settings before router.match."""
    from udl.extractors.external.gallerydl import configure_gallerydl

    enabled = not bool(getattr(args, "no_gallerydl", False))
    archive_raw = getattr(args, "gallerydl_archive", None)
    use_archive = archive_raw is not None
    archive_path = None if archive_raw in (None, "default") else archive_raw
    configure_gallerydl(
        enabled=enabled,
        tool=getattr(args, "gallerydl_tool", None),
        cookies=getattr(args, "gallerydl_cookies", None),
        options=getattr(args, "gallerydl_option", None) or [],
        username=getattr(args, "gallerydl_username", None),
        password=getattr(args, "gallerydl_password", None),
        use_archive=use_archive,
        archive=archive_path,
    )


def cmd_resolve(args: argparse.Namespace) -> int:
    _apply_network(args)
    _apply_gallerydl(args)
    info = resolve(args.url)
    payload = {
        "site": info.site,
        "label": info.label,
        "kind": info.kind,
        "url": info.url,
        "supported": info.supported,
        "status": info.status,
        "message": info.message,
    }
    if args.json:
        emitter().resolve(
            site=info.site,
            kind=info.kind,
            url=info.url,
            supported=info.supported,
            message=info.message,
        )
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if info.supported or info.status == "stub" else 2


def cmd_probe(args: argparse.Namespace) -> int:
    _apply_network(args)
    _apply_gallerydl(args)
    urls = args.url if isinstance(args.url, list) else [args.url]
    if len(urls) > 1:
        return _cmd_probe_batch(urls, args)

    url = urls[0]
    info = resolve(url)
    if not info.supported:
        msg = info.message or "unsupported URL"
        if args.json:
            emitter().error(message=msg, code="unsupported")
        else:
            print(msg, file=sys.stderr)
        return 2

    ext = None
    try:
        if info.site == "bilibili":
            from udl.extractors.bilibili import bilibili_extractor

            probe_url = url
            if getattr(args, "keyword", None):
                sep = "&" if "?" in probe_url else "?"
                probe_url = f"{probe_url}{sep}keyword={args.keyword}"
            from udl.extractors.bilibili import select_filter
            from udl.extractors.bilibili.filter import parse_part_range

            page_from, page_to = parse_part_range(getattr(args, "part_range", None))
            select = select_filter(
                title_keyword=getattr(args, "filter_title", None)
                or getattr(args, "keyword", None),
                duration_min=getattr(args, "filter_duration_min", None),
                duration_max=getattr(args, "filter_duration_max", None),
                page_from=page_from,
                page_to=page_to,
            )
            progress_em = emitter(enabled=bool(args.json))

            def _on_expand(phase: str, current: int, total: int, message: str) -> None:
                progress_em.expand(
                    phase=phase, current=current, total=total, message=message
                )

            ext = bilibili_extractor(
                page_num=int(getattr(args, "page", 1) or 1),
                expand_all=bool(getattr(args, "expand_all", False)),
                select=select,
                on_progress=_on_expand if getattr(args, "expand_all", False) else None,
            )
            probe = ext.probe(probe_url)
            payload = probe.to_dict()
        else:
            ext = get_extractor(url)
            if hasattr(ext, "probe"):
                probe = ext.probe(url)
                payload = probe.to_dict() if hasattr(probe, "to_dict") else dict(probe)
            else:
                result = ext.resolve(url)
                payload = {
                    "site": result.site,
                    "kind": result.kind,
                    "title": result.title,
                    "url": url,
                    "parts": result.meta.get("parts") or [],
                    "qualities": result.meta.get("qualities") or [],
                    "default_part": result.meta.get("default_part"),
                    "default_quality": result.meta.get("default_quality"),
                    "message": None,
                    "files": [
                        {"name": f.name, "size": f.size, "path": f.relative_dir}
                        for f in result.files
                    ],
                }
                meta = metadata_payload(result)
                if meta:
                    payload["metadata"] = meta
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None) or "resolve_failed"
        if args.json:
            emitter().error(message=str(exc), code=str(code))
        else:
            print(f"probe failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if ext is not None:
            close = getattr(ext, "close", None)
            if callable(close):
                close()

    if args.json:
        emitter().probe(**payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _parse_quality(raw: str | None) -> int | None:
    if raw is None or raw == "" or raw.lower() in {"auto", "best"}:
        return None
    return int(raw)


def _parse_codec(raw: str | None) -> int | None:
    if raw is None or raw == "" or raw.lower() in {"auto", "best"}:
        return None
    key = str(raw).strip().lower()
    mapping = {
        "avc": 7,
        "h264": 7,
        "hevc": 12,
        "h265": 12,
        "av1": 13,
        "7": 7,
        "12": 12,
        "13": 13,
    }
    if key in mapping:
        return mapping[key]
    return int(key)


def _cmd_probe_batch(urls: list[str], args: argparse.Namespace) -> int:
    from udl.extractors.bilibili import bilibili_extractor, select_filter
    from udl.extractors.bilibili.filter import parse_part_range
    from udl.extractors.bilibili.ids import split_batch_urls

    # flatten if user pasted multi-line as one arg
    expanded: list[str] = []
    for u in urls:
        expanded.extend(split_batch_urls(u))
    page_from, page_to = parse_part_range(getattr(args, "part_range", None))
    select = select_filter(
        title_keyword=getattr(args, "filter_title", None)
        or getattr(args, "keyword", None),
        duration_min=getattr(args, "filter_duration_min", None),
        duration_max=getattr(args, "filter_duration_max", None),
        page_from=page_from,
        page_to=page_to,
    )
    progress_em = emitter(enabled=bool(args.json))

    def _on_expand(phase: str, current: int, total: int, message: str) -> None:
        progress_em.expand(phase=phase, current=current, total=total, message=message)

    ext = bilibili_extractor(
        page_num=int(getattr(args, "page", 1) or 1),
        expand_all=bool(getattr(args, "expand_all", False)),
        select=select,
        on_progress=_on_expand if getattr(args, "expand_all", False) else None,
    )
    try:
        results = ext.probe_batch("\n".join(expanded))
        payload = {
            "batch": True,
            "count": len(results),
            "items": [
                r.to_dict() if hasattr(r, "to_dict") else r for r in results
            ],
        }
    finally:
        ext.close()
    if args.json:
        emitter().probe(**payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _bilibili_from_args(args: argparse.Namespace):
    from udl.extractors.bilibili import bili_options, bilibili_extractor, select_filter
    from udl.extractors.bilibili.filter import parse_part_range

    quality = _parse_quality(args.quality)
    codecid = _parse_codec(getattr(args, "codec", None))
    audio_q = getattr(args, "audio_quality", None)
    audio_quality = int(audio_q) if audio_q not in (None, "") else None
    danmaku = getattr(args, "danmaku", None)
    subtitle = getattr(args, "subtitle", None)
    embed_danmaku = bool(getattr(args, "embed_danmaku", False))
    embed_subtitle = bool(getattr(args, "embed_subtitle", False))
    if embed_danmaku and not danmaku:
        danmaku = "ass"
    if embed_subtitle and not subtitle:
        subtitle = "ass"
    page_from, page_to = parse_part_range(getattr(args, "part_range", None))
    select = select_filter(
        title_keyword=getattr(args, "filter_title", None),
        date_from=getattr(args, "filter_date_from", None),
        date_to=getattr(args, "filter_date_to", None),
        duration_min=getattr(args, "filter_duration_min", None),
        duration_max=getattr(args, "filter_duration_max", None),
        page_from=page_from,
        page_to=page_to,
    )
    langs_raw = getattr(args, "subtitle_lang", None) or ""
    langs = [x.strip() for x in langs_raw.split(",") if x.strip()]
    cookies = None
    sessdata = getattr(args, "sessdata", None)
    if sessdata:
        cookies = {"SESSDATA": str(sessdata)}
    node_names: dict = {}
    raw_names = getattr(args, "node_names", None)
    if raw_names:
        try:
            parsed = json.loads(raw_names)
            if isinstance(parsed, dict):
                node_names = {str(k): str(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            pass
    opts = bili_options(
        quality=quality,
        codecid=codecid,
        audio_quality=audio_quality,
        part=args.part,
        container=getattr(args, "container", None) or "mp4",
        danmaku_format=danmaku,
        subtitle_format=subtitle,
        subtitle_languages=langs,
        embed_danmaku=embed_danmaku,
        embed_subtitle=embed_subtitle,
        write_chapters=not bool(getattr(args, "no_chapters", False)),
        select=select,
        expand_all=bool(getattr(args, "expand_all", False)),
        node_names=node_names,
        cookies=cookies,
    )
    return bilibili_extractor(options=opts, ffmpeg=args.ffmpeg)


def cmd_get(args: argparse.Namespace) -> int:
    try:
        _apply_network(args)
    except ValueError as exc:
        print(f"invalid network option: {exc}", file=sys.stderr)
        return 2
    _apply_gallerydl(args)

    out: Path = args.output
    out.mkdir(parents=True, exist_ok=True)

    info = resolve(args.url)
    if not info.supported:
        msg = info.message or "unsupported URL"
        if args.json:
            emitter().error(message=msg, code="unsupported")
        else:
            print(msg, file=sys.stderr)
        return 2

    cancel = Event()

    def _on_sigint(_signum: int, _frame: object) -> None:
        cancel.set()

    signal.signal(signal.SIGINT, _on_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_sigint)

    emit = emitter(enabled=args.json)
    job_id = args.job_id or str(uuid.uuid4())
    on_dup: duplicate_mode = args.on_duplicate
    conn = None if args.no_history else open_store(args.db)
    cfg = load_config(getattr(args, "config", None))
    # traffic quota accounting via rate limiter hook
    db_for_traffic = args.db
    set_traffic_recorder(lambda n: note_traffic(n, db_path=db_for_traffic))

    ext = None
    skip_indexes: set[int] = set()
    overrides: dict[int, Path] = {}
    try:
        if info.site == "bilibili":
            try:
                ext = _bilibili_from_args(args)
            except ValueError as exc:
                msg = f"invalid bilibili option: {exc}"
                if args.json:
                    emit.error(job=job_id, message=msg, code="resolve_failed")
                else:
                    print(msg, file=sys.stderr)
                return 1
        else:
            ext = get_extractor(args.url)
            _apply_site_options(ext, args)
        result = ext.resolve(args.url, password=args.password)
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None) or "resolve_failed"
        if args.json:
            emit.error(job=job_id, message=str(exc), code=str(code))
        else:
            print(f"resolve failed: {exc}", file=sys.stderr)
        if conn is not None:
            conn.close()
        return 1

    meta_dict = metadata_payload(result)
    already_all = False
    name_tpl = getattr(args, "name_template", None)
    if conn is not None:
        skip_indexes, overrides, already_all = prepare_download(
            conn,
            job_id=job_id,
            url=args.url,
            output_dir=out,
            result=result,
            on_duplicate=on_dup,
            emit=emit if args.json else None,
            quality=args.quality,
            part=args.part,
            cloudflare_bypass=bool(args.cloudflare_bypass),
            workers=args.workers,
            ffmpeg=args.ffmpeg,
            config=cfg,
            name_template=name_tpl,
        )
    else:
        skip_indexes, overrides, _decisions = plan_file_destinations(
            result,
            out,
            config=cfg,
            name_template=name_tpl,
            on_duplicate=on_dup,
        )

    emit.job(
        id=job_id,
        site=result.site,
        title=result.title,
        url=args.url,
        files=len(result.files),
        metadata=meta_dict,
        already_downloaded=already_all or None,
    )
    if not args.json:
        print(
            f"[{result.site}] {result.title} — {len(result.files)} file(s) → {out}",
            file=sys.stderr,
        )
        if already_all:
            print("already downloaded — skipping", file=sys.stderr)

    ok = 0
    failed = 0
    file_failures: dict[int, dict[str, Any]] = {}
    try:
        if already_all:
            ok = len(result.files)
            for idx, f in enumerate(result.files):
                dest_dir = out / f.relative_dir if f.relative_dir else out
                dest = overrides.get(idx, dest_dir / f.name)
                file_meta = f.metadata or result.metadata
                emit.file(
                    job=job_id,
                    id=f"{job_id}:{idx}",
                    name=f.name,
                    total=f.size,
                    path=str(dest),
                    index=idx,
                    metadata=file_meta.to_dict(omit_empty=True) if file_meta else None,
                    already_downloaded=True,
                )
                emit.done(
                    job=job_id,
                    file=f"{job_id}:{idx}",
                    path=str(dest),
                    skipped=True,
                    already_downloaded=True,
                )
        else:
            ok, failed = ext.download(
                result,
                out,
                emit=emit if args.json else None,
                cancel=cancel,
                workers=args.workers,
                retries=args.retries,
                job_id=job_id,
                file_dest_overrides=overrides or None,
                skip_indexes=skip_indexes or None,
            )
    except InterruptedError:
        emit.cancel(job=job_id)
        if conn is not None:
            finalize_download(
                conn,
                job_id=job_id,
                result=result,
                output_dir=out,
                ok=ok,
                failed=failed,
                cancelled=True,
                skip_indexes=skip_indexes,
                file_dest_overrides=overrides,
            )
            conn.close()
        return 130
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None) or "download_failed"
        if args.json:
            emit.error(job=job_id, message=str(exc), code=str(code))
        else:
            print(f"download failed: {exc}", file=sys.stderr)
        if conn is not None:
            finalize_download(
                conn,
                job_id=job_id,
                result=result,
                output_dir=out,
                ok=ok,
                failed=max(failed, 1),
                error_message=str(exc),
                skip_indexes=skip_indexes,
                file_dest_overrides=overrides,
            )
            conn.close()
        return 1
    finally:
        close = getattr(ext, "close", None)
        if callable(close):
            close()

    if not cancel.is_set() and failed == 0 and not already_all:
        try:
            cli_checksum = expected_from_cli(
                checksum=getattr(args, "checksum", None),
                md5=getattr(args, "md5", None),
                sha1=getattr(args, "sha1", None),
                sha256=getattr(args, "sha256", None),
            )
        except ValueError as exc:
            if args.json:
                emit.error(job=job_id, message=str(exc), code="checksum_invalid")
            else:
                print(f"invalid checksum: {exc}", file=sys.stderr)
            if conn is not None:
                finalize_download(
                    conn,
                    job_id=job_id,
                    result=result,
                    output_dir=out,
                    ok=ok,
                    failed=max(failed, 1),
                    error_message=str(exc),
                    skip_indexes=skip_indexes,
                    file_dest_overrides=overrides,
                )
                conn.close()
            return 1
        checksum_auto = bool(cfg.checksum_auto) and not bool(
            getattr(args, "no_checksum_auto", False)
        )
        hook = _hook_from_args(args, cfg)
        file_failures, _checked = post_download_verify(
            result,
            out,
            job_id=job_id,
            skip_indexes=skip_indexes,
            file_dest_overrides=overrides,
            cli_checksum=cli_checksum,
            checksum_auto=checksum_auto,
            hook=hook,
            emit=emit if args.json else None,
        )
        if file_failures:
            failed += len(file_failures)
            ok = max(0, ok - len(file_failures))

    err_msg = None
    if file_failures:
        err_msg = next(iter(file_failures.values())).get("message") or "checksum/hook failed"

    if conn is not None:
        finalize_download(
            conn,
            job_id=job_id,
            result=result,
            output_dir=out,
            ok=ok,
            failed=failed,
            cancelled=cancel.is_set(),
            error_message=err_msg,
            skip_indexes=skip_indexes,
            file_dest_overrides=overrides,
            file_failures=file_failures or None,
        )

    pp_opts = _postprocess_from_args(args)
    if (
        not cancel.is_set()
        and failed == 0
        and pp_opts.any_enabled()
        and (ok > 0 or already_all)
    ):
        try:
            run_postprocess(
                result,
                out,
                pp_opts,
                job_id=job_id,
                emit=emit if args.json else None,
                conn=conn,
                cancel=cancel,
                skip_indexes=skip_indexes,
                file_dest_overrides=overrides,
                kind_hint=result.kind,
            )
        except Exception as exc:  # noqa: BLE001
            if args.json:
                emit.error(job=job_id, message=str(exc), code="postprocess_failed")
            else:
                print(f"postprocess failed: {exc}", file=sys.stderr)

    flush_traffic_accounting()
    set_traffic_recorder(None)
    if conn is not None:
        conn.close()

    emit.done(
        job=job_id,
        path=str(out.resolve()),
        ok=ok,
        failed=failed,
        already_downloaded=already_all or None,
        metadata=meta_dict,
    )
    if not args.json:
        print(f"done: ok={ok} failed={failed}", file=sys.stderr)

    if cancel.is_set():
        return 130
    return 0 if failed == 0 else 1


def _hook_from_args(args: argparse.Namespace, cfg: udl_config) -> hook_options:
    preset = getattr(args, "post_cmd_preset", None) or cfg.post_cmd_preset or None
    command = getattr(args, "post_cmd", None)
    if command is None:
        command = cfg.post_cmd
    timeout = getattr(args, "post_cmd_timeout", None)
    if timeout is None:
        timeout = cfg.post_cmd_timeout
    cfg_on = bool(cfg.post_cmd_enabled and (cfg.post_cmd or cfg.post_cmd_preset))
    enabled = bool(command or preset) or cfg_on
    if getattr(args, "post_cmd", None) is not None or getattr(args, "post_cmd_preset", None):
        enabled = True
    return hook_options(
        enabled=enabled,
        command=str(command or ""),
        preset=str(preset).strip() if preset else None,
        timeout=int(timeout or 120),
    )


def cmd_page(args: argparse.Namespace) -> int:
    try:
        _apply_network(args)
    except ValueError as exc:
        print(f"invalid network option: {exc}", file=sys.stderr)
        return 2
    try:
        size_min = parse_size_bound(args.size_min)
        size_max = parse_size_bound(args.size_max)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    filt = page_filter(
        extensions=parse_extensions(args.ext),
        types=parse_types(args.types),
        size_min=size_min,
        size_max=size_max,
        regex=args.regex,
        glob=args.glob,
        same_domain=bool(args.same_domain),
        include_images=bool(args.images),
        head_sizes=not bool(args.no_head),
    )
    result = scan_page(args.url, filters=filt)
    payload = result.to_dict()
    if args.json:
        emitter().page(**{k: v for k, v in payload.items() if k != "ev"})
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if result.message and not result.candidates:
        return 1
    return 0


def _postprocess_from_args(args: argparse.Namespace) -> postprocess_options:
    write_cover = bool(args.write_cover or args.embed_cover)
    return postprocess_options(
        write_nfo=bool(args.write_nfo),
        write_cover=write_cover,
        embed_cover=bool(args.embed_cover),
        cover_naming=args.cover_name,
        nfo_sites=parse_site_toggle_list(args.nfo_sites),
        cover_sites=parse_site_toggle_list(args.cover_sites),
        ffmpeg=args.ffmpeg,
    )


def _apply_site_options(ext: object, args: argparse.Namespace) -> None:
    """pass optional CLI flags to site extractors that understand them."""
    configure = getattr(ext, "configure", None)
    if not callable(configure):
        return
    kwargs: dict[str, object] = {}
    if getattr(args, "quality", None):
        kwargs["quality"] = args.quality
    if getattr(args, "token", None) is not None:
        kwargs["token"] = args.token
    if getattr(args, "cloudflare_bypass", False):
        kwargs["cloudflare_bypass"] = True
    if getattr(args, "mega_tool", None):
        kwargs["mega_tool"] = args.mega_tool
    if getattr(args, "mega_backend", None):
        kwargs["mega_backend"] = args.mega_backend
    if getattr(args, "mega_select", None):
        kwargs["selected_handles"] = args.mega_select
    if getattr(args, "mega_no_verify", False):
        kwargs["verify_mac"] = False
    if getattr(args, "gallerydl_tool", None):
        kwargs["gallerydl_tool"] = args.gallerydl_tool
    if getattr(args, "gallerydl_cookies", None):
        kwargs["gallerydl_cookies"] = args.gallerydl_cookies
    if getattr(args, "gallerydl_option", None):
        kwargs["gallerydl_options"] = args.gallerydl_option
    if getattr(args, "gallerydl_username", None):
        kwargs["gallerydl_username"] = args.gallerydl_username
    if getattr(args, "gallerydl_password", None):
        kwargs["gallerydl_password"] = args.gallerydl_password
    if getattr(args, "gallerydl_archive", None) is not None:
        kwargs["gallerydl_use_archive"] = True
        if args.gallerydl_archive != "default":
            kwargs["gallerydl_archive"] = args.gallerydl_archive
    if kwargs:
        configure(**kwargs)


def cmd_schema(_args: argparse.Namespace) -> int:
    print(json.dumps(dump_schema(), ensure_ascii=False, indent=2))
    return 0


def cmd_sites(args: argparse.Namespace) -> int:
    from udl.router import registry

    rows = [
        {
            "site": e.site,
            "label": e.label,
            "status": e.status,
            "kind": e.kind_hint,
        }
        for e in registry
    ]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"{r['site']:12} {r['status']:12} {r['label']} ({r['kind']})")
    return 0


def cmd_gallerydl(args: argparse.Namespace) -> int:
    from udl.extractors.external import gallerydl as gdl

    if args.gallerydl_command == "status":
        payload = gdl.tool_status(getattr(args, "gallerydl_tool", None))
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if payload["found"]:
                print(
                    f"gallery-dl: {payload['path']} "
                    f"({payload.get('kind')}) version={payload.get('version')}"
                )
            else:
                print(payload["message"], file=sys.stderr)
                return 1
        return 0
    if args.gallerydl_command == "sites":
        try:
            if getattr(args, "extractors", False):
                rows = gdl.list_extractors(getattr(args, "gallerydl_tool", None))
            else:
                rows = [
                    {"module": name}
                    for name in gdl.list_modules(getattr(args, "gallerydl_tool", None))
                ]
        except gdl.gallerydl_tool_missing_error as exc:
            if args.json:
                print(json.dumps({"error": str(exc), "code": exc.code}))
            else:
                print(str(exc), file=sys.stderr)
            return 1
        except gdl.gallerydl_download_error as exc:
            if args.json:
                print(json.dumps({"error": str(exc), "code": exc.code}))
            else:
                print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                if "module" in row:
                    print(row["module"])
                else:
                    print(
                        f"{row.get('category', ''):16} {row.get('subcategory', ''):16} "
                        f"{row.get('example', '')}"
                    )
        return 0
    return 2


def cmd_secrets(args: argparse.Namespace) -> int:
    if args.secrets_command == "set":
        value = args.value
        if value is None:
            value = sys.stdin.read()
            if value.endswith("\n"):
                value = value[:-1]
        set_secret(args.key, value)
        print("ok", file=sys.stderr)
        return 0
    if args.secrets_command == "get":
        value = get_secret(args.key)
        # never echo bilibili.session cookie blob to stdout unless explicitly asked
        # --json only reports presence
        if args.json:
            print(json.dumps({"key": args.key, "found": value is not None}, ensure_ascii=False))
        elif value is not None:
            # never print credential values for login-related keys
            if args.key.strip() in {
                "bilibili.session",
                "iwara.password",
                "iwara.user_token",
                "iwara.access_token",
                "mega.session",
            }:
                print("(redacted — use --json to check presence)", file=sys.stderr)
                return 0
            print(value)
        return 0 if value is not None else 1
    if args.secrets_command == "delete":
        removed = delete_secret(args.key)
        if args.json:
            print(json.dumps({"key": args.key, "deleted": removed}, ensure_ascii=False))
        else:
            print("deleted" if removed else "missing", file=sys.stderr)
        return 0 if removed else 1
    return 2


def cmd_bili(args: argparse.Namespace) -> int:
    if args.bili_command != "login":
        return 2
    try:
        _apply_network(args)
    except ValueError as exc:
        print(f"invalid network option: {exc}", file=sys.stderr)
        return 2
    from udl.extractors.bilibili import login as bili_login

    cmd = args.login_command
    as_json = bool(getattr(args, "json", False))
    try:
        if cmd == "status":
            profile = bili_login.validate_session(persist=False)
            if as_json:
                print(json.dumps(profile.to_dict(), ensure_ascii=False))
            elif profile.logged_in:
                vip = profile.vip_label or (
                    f"vipType={profile.vip_type}" if profile.vip_type else "非大會員"
                )
                print(
                    f"已登入：{profile.uname} (mid={profile.mid}) "
                    f"Lv{profile.level} {vip}"
                )
            else:
                print(profile.message or "未登入")
            return 0 if profile.logged_in else 1
        if cmd == "refresh":
            profile = bili_login.refresh_session()
            if as_json:
                print(json.dumps(profile.to_dict(), ensure_ascii=False))
            else:
                print("已重新驗證" if profile.logged_in else (profile.message or "未登入"))
            return 0 if profile.logged_in else 1
        if cmd == "logout":
            profile = bili_login.logout_session()
            if as_json:
                print(json.dumps(profile.to_dict(), ensure_ascii=False))
            else:
                print("已登出")
            return 0
        if cmd == "qr":
            challenge = bili_login.qr_generate(with_image=not bool(args.no_image))
            if as_json:
                print(json.dumps(challenge.to_dict(), ensure_ascii=False))
            else:
                print(f"qrcode_key={challenge.qrcode_key}")
                print(challenge.url)
                print("用手機 B 站 App 掃描後執行: udl bili login qr-poll --key <key>")
            return 0
        if cmd == "qr-poll":
            result = bili_login.qr_poll(args.key)
            if as_json:
                print(json.dumps(result.to_dict(), ensure_ascii=False))
            else:
                print(f"{result.message} ({result.code})")
                if result.profile and result.profile.logged_in:
                    print(f"已登入：{result.profile.uname}")
            return 0 if result.code == bili_login.qr_code_success else 1
        if cmd == "cookie":
            text = args.text
            if text is None:
                text = sys.stdin.read()
            profile = bili_login.login_with_cookies(text)
            if as_json:
                print(json.dumps(profile.to_dict(), ensure_ascii=False))
            else:
                print(f"已登入：{profile.uname} (mid={profile.mid})")
            return 0
        if cmd == "sms-captcha":
            payload = bili_login.sms_captcha_init()
            print(json.dumps(payload, ensure_ascii=False, indent=None if as_json else 2))
            if not as_json:
                print(bili_login.sms_geetest_note, file=sys.stderr)
            return 0
        if cmd == "sms-send":
            payload = bili_login.sms_send(
                tel=args.tel,
                cid=args.cid,
                token=args.token,
                challenge=args.challenge,
                validate=args.validate,
                seccode=args.seccode,
            )
            if as_json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(f"captcha_key={payload.get('captcha_key')}")
            return 0
        if cmd == "sms":
            profile = bili_login.sms_login(
                tel=args.tel,
                cid=args.cid,
                code=args.code,
                captcha_key=args.captcha_key,
            )
            if as_json:
                print(json.dumps(profile.to_dict(), ensure_ascii=False))
            else:
                print(f"已登入：{profile.uname} (mid={profile.mid})")
            return 0
    except bili_login.login_error as exc:
        if as_json:
            print(json.dumps({"error": str(exc), "code": exc.code}, ensure_ascii=False))
        else:
            print(f"login error: {exc}", file=sys.stderr)
        return 1
    return 2


def cmd_ffmpeg(args: argparse.Namespace) -> int:
    from udl.postprocess.ffmpeg_provision import (
        ffmpeg_provision_error,
        install_ffmpeg,
        probe_ffmpeg,
        provision_supported,
    )

    if args.ffmpeg_command == "status":
        status = probe_ffmpeg(args.ffmpeg)
        payload = {
            "found": status.found,
            "path": status.path,
            "source": status.source,
            "platform": status.platform_key,
            "provision_supported": provision_supported(),
            "message": status.message,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if status.found else 1

    if args.ffmpeg_command == "install":
        try:
            path = install_ffmpeg(args.dir)
        except ffmpeg_provision_error as exc:
            payload = {"ok": False, "message": str(exc), "code": exc.code}
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(str(exc), file=sys.stderr)
            return 1
        payload = {
            "ok": True,
            "path": str(path),
            "platform": probe_ffmpeg(path).platform_key,
            "message": "FFmpeg installed",
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    return 2


def cmd_history(args: argparse.Namespace) -> int:
    conn = open_store(args.db)
    try:
        if args.history_command == "list":
            rows = list_history(
                conn,
                site=args.site,
                status=args.status,
                tag=args.tag,
                date_from=args.date_from,
                date_to=args.date_to,
                query=args.query,
                sort=args.sort,
                order=args.order,
                limit=args.limit,
                offset=args.offset,
                include_files=not args.no_files,
            )
            payload = [r.to_dict() for r in rows]
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.history_command == "show":
            entry = get_history(conn, args.id)
            if entry is None:
                print(json.dumps({"error": "not found", "id": args.id}))
                return 1
            payload = entry.to_dict()
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.history_command == "delete":
            result = delete_history(
                conn,
                list(args.ids),
                delete_files=bool(args.delete_files),
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    finally:
        conn.close()
    return 2


def cmd_queue(args: argparse.Namespace) -> int:
    conn = open_store(args.db)
    try:
        if args.queue_command == "schedule":
            return cmd_queue_schedule(conn, args)
        if args.queue_command == "list":
            rows = list_queue(
                conn,
                status=args.status,
                unfinished_only=bool(args.unfinished),
                queue_name=getattr(args, "queue_name", None),
                limit=args.limit,
            )
            payload = [r.to_dict() for r in rows]
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.queue_command == "show":
            job = load_queue_job(conn, args.id)
            if job is None:
                print(json.dumps({"error": "not found", "id": args.id}))
                return 1
            payload = job.to_dict()
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.queue_command == "delete":
            removed = delete_queue_job(conn, list(args.ids))
            payload: dict[str, Any] = {"removed": removed}
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.queue_command == "set-status":
            update_queue_status(
                conn,
                args.id,
                args.status,
                error_message=args.message,
            )
            job = load_queue_job(conn, args.id)
            payload = job.to_dict() if job else {"id": args.id, "status": args.status}
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
    finally:
        flush_traffic_accounting()
        set_traffic_recorder(None)
        conn.close()
    return 2


def _print_json(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_naming(args: argparse.Namespace) -> int:
    if args.naming_command == "preview":
        try:
            meta_raw = json.loads(args.metadata or "{}")
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": f"invalid metadata JSON: {exc}"}))
            return 1
        if not isinstance(meta_raw, dict):
            print(json.dumps({"error": "metadata must be a JSON object"}))
            return 1
        if "site" not in meta_raw:
            meta_raw["site"] = "example"
        payload = preview_template(
            args.template,
            meta_raw,
            original_name=args.name,
        )
        _print_json(payload, bool(args.json))
        return 0
    return 2


def cmd_rules(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.rules_command == "list":
        payload = {"rules": [r.to_dict() for r in cfg.rules]}
        _print_json(payload, bool(args.json))
        return 0
    if args.rules_command == "test":
        meta: dict[str, Any] | item_metadata
        original_name = args.name
        size = args.size
        if args.metadata:
            try:
                meta = json.loads(args.metadata)
            except json.JSONDecodeError as exc:
                print(json.dumps({"error": f"invalid metadata JSON: {exc}"}))
                return 1
            if not isinstance(meta, dict):
                print(json.dumps({"error": "metadata must be a JSON object"}))
                return 1
        elif args.url:
            info = resolve(args.url)
            if not info.supported:
                print(json.dumps({"error": info.message or "unsupported URL"}))
                return 2
            ext = None
            try:
                ext = get_extractor(args.url)
                result = ext.resolve(args.url)
                meta = result.metadata or item_metadata(site=result.site, title=result.title)
                if result.files:
                    original_name = original_name or result.files[0].name
                    size = size if size is not None else result.files[0].size
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"error": str(exc)}))
                return 1
            finally:
                if ext is not None:
                    close = getattr(ext, "close", None)
                    if callable(close):
                        close()
        else:
            print(json.dumps({"error": "provide url or --metadata"}))
            return 2
        payload = test_rules_against_metadata(
            cfg.rules,
            meta,
            size=size,
            original_name=original_name,
        )
        _print_json(payload, bool(args.json))
        return 0
    return 2


def cmd_config(args: argparse.Namespace) -> int:
    if args.config_command == "show":
        cfg = load_config(args.config)
        _print_json(cfg.to_dict(), bool(args.json))
        return 0
    if args.config_command == "save":
        raw = args.data
        if raw is None:
            raw = sys.stdin.read()
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": f"invalid JSON: {exc}"}))
            return 1
        if not isinstance(data, dict):
            print(json.dumps({"error": "config must be a JSON object"}))
            return 1
        cfg = udl_config.from_dict(data)
        # ensure rules parse even if only rules key present
        if "rules" in data:
            cfg.rules = parse_rules(data)
        path = save_config(cfg, args.config)
        payload = {"ok": True, "path": str(path), "config": cfg.to_dict()}
        _print_json(payload, bool(args.json))
        return 0
    return 2


def cmd_repair(args: argparse.Namespace) -> int:
    conn = open_store(args.db)
    try:
        if args.repair_command == "scan":
            result = scan_repair(
                conn,
                output_dirs=args.dirs,
                verify_hash=bool(args.verify_hash),
                include_orphans=not bool(args.no_orphans),
            )
            _print_json(result.to_dict(), bool(args.json))
            return 0
        if args.repair_command == "fix":
            issues = None
            if args.issues:
                try:
                    issues = json.loads(args.issues)
                except json.JSONDecodeError as exc:
                    print(json.dumps({"error": f"invalid --issues JSON: {exc}"}))
                    return 1
                if not isinstance(issues, list):
                    print(json.dumps({"error": "--issues must be a JSON array"}))
                    return 1
            payload = apply_repair_actions(
                conn,
                action=args.action,
                issues=issues,
                job_ids=args.job_ids,
                file_row_ids=args.file_row_ids,
                relink_path=args.relink_path,
                output_dir=args.output,
            )
            _print_json(payload, bool(args.json))
            return 0 if not payload.get("errors") else 1
    finally:
        conn.close()
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "resolve":
        return cmd_resolve(args)
    if args.command == "probe":
        return cmd_probe(args)
    if args.command == "get":
        return cmd_get(args)
    if args.command == "page":
        return cmd_page(args)
    if args.command == "schema":
        return cmd_schema(args)
    if args.command == "sites":
        return cmd_sites(args)
    if args.command == "gallerydl":
        return cmd_gallerydl(args)
    if args.command == "ffmpeg":
        return cmd_ffmpeg(args)
    if args.command == "history":
        return cmd_history(args)
    if args.command == "queue":
        return cmd_queue(args)
    if args.command == "naming":
        return cmd_naming(args)
    if args.command == "rules":
        return cmd_rules(args)
    if args.command == "config":
        return cmd_config(args)
    if args.command == "repair":
        return cmd_repair(args)
    if args.command == "secrets":
        return cmd_secrets(args)
    if args.command == "iwara":
        _apply_network(args)
        return cmd_iwara(args)
    if args.command == "mega":
        _apply_network(args)
        return cmd_mega(args)
    if args.command == "bili":
        return cmd_bili(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
