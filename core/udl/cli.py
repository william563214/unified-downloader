"""udl CLI — resolve, probe, and get with optional JSON-lines progress."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import uuid
from pathlib import Path
from threading import Event

from udl import __version__
from udl.events import dump_schema, emitter
from udl.router import get_extractor, resolve


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="udl",
        description="整合下載器 unified downloader core",
    )
    parser.add_argument("--version", action="version", version=f"udl {__version__}")
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
        help="bilibili video qn (e.g. 64) or omit for best available without login",
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

    p_schema = sub.add_parser("schema", help="print JSON-lines event schema")
    p_schema.add_argument(
        "--json",
        action="store_true",
        default=True,
        help="print schema as JSON (default)",
    )

    p_sites = sub.add_parser("sites", help="list registered sites")
    p_sites.add_argument("--json", action="store_true")

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

    try:
        quality = _parse_quality(args.quality)
    except ValueError:
        msg = f"invalid --quality: {args.quality}"
        if args.json:
            emit.error(job=job_id, message=msg, code="resolve_failed")
        else:
            print(msg, file=sys.stderr)
        return 1

    try:
        if info.site == "bilibili":
            from udl.extractors.bilibili import bilibili_extractor

            ext = bilibili_extractor(
                quality=quality,
                part=args.part,
                ffmpeg=args.ffmpeg,
            )
        else:
            ext = get_extractor(args.url)
        result = ext.resolve(args.url, password=args.password)
    except Exception as exc:  # noqa: BLE001
        if args.json:
            emit.error(job=job_id, message=str(exc), code="resolve_failed")
        else:
            print(f"resolve failed: {exc}", file=sys.stderr)
        return 1

    emit.job(
        id=job_id,
        site=result.site,
        title=result.title,
        url=args.url,
        files=len(result.files),
    )
    if not args.json:
        print(
            f"[{result.site}] {result.title} — {len(result.files)} file(s) → {out}",
            file=sys.stderr,
        )

    try:
        ok, failed = ext.download(
            result,
            out,
            emit=emit if args.json else None,
            cancel=cancel,
            workers=args.workers,
            retries=args.retries,
            job_id=job_id,
        )
    except InterruptedError:
        emit.cancel(job=job_id)
        return 130
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None) or "download_failed"
        if args.json:
            emit.error(job=job_id, message=str(exc), code=str(code))
        else:
            print(f"download failed: {exc}", file=sys.stderr)
        return 1
    finally:
        close = getattr(ext, "close", None)
        if callable(close):
            close()

    emit.done(job=job_id, path=str(out.resolve()), ok=ok, failed=failed)
    if not args.json:
        print(f"done: ok={ok} failed={failed}", file=sys.stderr)

    if cancel.is_set():
        return 130
    return 0 if failed == 0 else 1


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
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
