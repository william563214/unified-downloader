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
from udl.events import dump_schema, emitter
from udl.postprocess.artifacts import run_postprocess
from udl.postprocess.options import parse_site_toggle_list, postprocess_options
from udl.router import get_extractor, resolve
from udl.session import (
    finalize_download,
    metadata_payload,
    open_store,
    prepare_download,
)
from udl.store.duplicates import duplicate_mode
from udl.store.history import delete_history, get_history, list_history
from udl.store.queue import delete_queue_job, list_queue, load_queue_job, update_queue_status


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

    p_probe = sub.add_parser(
        "probe",
        help="fetch site metadata (parts / qualities) without downloading",
    )
    p_probe.add_argument("url", help="content URL")
    p_probe.add_argument(
        "--json",
        action="store_true",
        help="emit a JSON-lines probe event (default: pretty JSON object)",
    )

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
        help="optional Iwara Bearer token (in-memory only not stored)",
    )
    p_get.add_argument(
        "--mega-tool",
        default=None,
        help="path to megatools or mega-get (default: PATH / UDL_MEGA_TOOL)",
    )
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

    p_schema = sub.add_parser("schema", help="print JSON-lines event schema")
    p_schema.add_argument(
        "--json",
        action="store_true",
        default=True,
        help="print schema as JSON (default)",
    )

    p_sites = sub.add_parser("sites", help="list registered sites")
    p_sites.add_argument("--json", action="store_true")

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

    return parser


def cmd_resolve(args: argparse.Namespace) -> int:
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
    info = resolve(args.url)
    if not info.supported:
        msg = info.message or "unsupported URL"
        if args.json:
            emitter().error(message=msg, code="unsupported")
        else:
            print(msg, file=sys.stderr)
        return 2

    ext = None
    try:
        ext = get_extractor(args.url)
        if hasattr(ext, "probe"):
            probe = ext.probe(args.url)
            payload = probe.to_dict() if hasattr(probe, "to_dict") else dict(probe)
        else:
            result = ext.resolve(args.url)
            payload = {
                "site": result.site,
                "kind": result.kind,
                "title": result.title,
                "url": args.url,
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
        if args.json:
            emitter().error(message=str(exc), code="resolve_failed")
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


def cmd_get(args: argparse.Namespace) -> int:
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

    ext = None
    skip_indexes: set[int] = set()
    overrides: dict[int, Path] = {}
    try:
        if info.site == "bilibili":
            from udl.extractors.bilibili import bilibili_extractor

            try:
                quality = _parse_quality(args.quality)
            except ValueError:
                msg = f"invalid bilibili --quality (expect qn int): {args.quality}"
                if args.json:
                    emit.error(job=job_id, message=msg, code="resolve_failed")
                else:
                    print(msg, file=sys.stderr)
                return 1
            ext = bilibili_extractor(
                quality=quality,
                part=args.part,
                ffmpeg=args.ffmpeg,
            )
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

    if conn is not None:
        finalize_download(
            conn,
            job_id=job_id,
            result=result,
            output_dir=out,
            ok=ok,
            failed=failed,
            cancelled=cancel.is_set(),
            skip_indexes=skip_indexes,
            file_dest_overrides=overrides,
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
        if args.queue_command == "list":
            rows = list_queue(
                conn,
                status=args.status,
                unfinished_only=bool(args.unfinished),
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
    if args.command == "schema":
        return cmd_schema(args)
    if args.command == "sites":
        return cmd_sites(args)
    if args.command == "ffmpeg":
        return cmd_ffmpeg(args)
    if args.command == "history":
        return cmd_history(args)
    if args.command == "queue":
        return cmd_queue(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
