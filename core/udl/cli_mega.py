"""CLI helpers for MEGA login, browse, and streaming."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def add_mega_parser(sub: Any) -> None:
    p_mega = sub.add_parser("mega", help="MEGA account / browse / stream")
    mega_sub = p_mega.add_subparsers(dest="mega_command", required=True)

    p_login = mega_sub.add_parser("login", help="email/password login → OS keychain")
    p_login.add_argument("--email", required=True)
    p_login.add_argument("--password", required=True)
    p_login.add_argument("--json", action="store_true")

    p_logout = mega_sub.add_parser("logout", help="clear MEGA session from keychain")
    p_logout.add_argument("--json", action="store_true")

    p_who = mega_sub.add_parser("whoami", help="show logged-in MEGA account")
    p_who.add_argument("--json", action="store_true")

    p_browse = mega_sub.add_parser(
        "browse", help="list public folder tree or cloud drive"
    )
    p_browse.add_argument(
        "url",
        nargs="?",
        default=None,
        help="public folder/file URL; omit to list cloud drive (login required)",
    )
    p_browse.add_argument("--json", action="store_true")

    p_stream = mega_sub.add_parser(
        "stream", help="start localhost decrypting HTTP stream for preview"
    )
    p_stream.add_argument("url", help="MEGA public file URL")
    p_stream.add_argument(
        "--wait",
        action="store_true",
        help="keep process alive until Ctrl+C (prints URL then waits)",
    )
    p_stream.add_argument("--json", action="store_true")


def cmd_mega(args: argparse.Namespace) -> int:
    from udl.extractors.mega import auth as mega_auth
    from udl.extractors.mega.api import build_folder_paths, mega_client
    from udl.extractors.mega.errors import mega_auth_error, mega_error
    from udl.extractors.mega.links import parse_link
    from udl.extractors.mega.stream import start_stream_for_link

    cmd = args.mega_command
    try:
        if cmd == "login":
            payload = mega_auth.login(args.email, args.password)
            return _print(payload, args.json)
        if cmd == "logout":
            payload = mega_auth.logout()
            return _print(payload, args.json)
        if cmd == "whoami":
            payload = mega_auth.whoami()
            return _print(payload, args.json)

        if cmd == "browse":
            url = getattr(args, "url", None)
            if url:
                info = parse_link(url)
                if info is None:
                    raise mega_error(f"not a MEGA link: {url}")
                client = mega_client()
                try:
                    if info.kind == "file":
                        if not info.key:
                            raise mega_error("file link missing key")
                        f = client.get_public_file(info.handle, info.key)
                        payload = {
                            "kind": "file",
                            "handle": f.handle,
                            "name": f.name,
                            "size": f.size,
                            "tree": [
                                {
                                    "handle": f.handle,
                                    "name": f.name,
                                    "kind": "file",
                                    "size": f.size,
                                    "path": f.name,
                                }
                            ],
                        }
                    else:
                        if not info.key:
                            raise mega_error("folder link missing key")
                        nodes = client.list_public_folder(
                            info.handle, info.key, sub_handle=info.sub_handle
                        )
                        paths = build_folder_paths(nodes)
                        payload = {
                            "kind": "folder",
                            "handle": info.handle,
                            "tree": [
                                {
                                    "handle": n.handle,
                                    "parent": n.parent,
                                    "name": n.name,
                                    "kind": "file" if n.is_file else "folder",
                                    "size": n.size if n.is_file else None,
                                    "path": paths.get(n.handle, n.name),
                                }
                                for n in nodes
                            ],
                        }
                finally:
                    client.close()
                return _print(payload, args.json)

            client = mega_auth.load_session()
            if client is None:
                raise mega_auth_error("not logged in — run: udl mega login")
            try:
                nodes = client.list_cloud_drive()
                paths = build_folder_paths(nodes)
                payload = {
                    "kind": "cloud",
                    "email": client.email,
                    "tree": [
                        {
                            "handle": n.handle,
                            "parent": n.parent,
                            "name": n.name,
                            "kind": "file" if n.is_file else "folder",
                            "size": n.size if n.is_file else None,
                            "path": paths.get(n.handle, n.name),
                        }
                        for n in nodes
                    ],
                }
            finally:
                client.close()
            return _print(payload, args.json)

        if cmd == "stream":
            started = start_stream_for_link(args.url)
            payload = {
                "url": started["url"],
                "token": started["token"],
                "name": started["name"],
                "size": started["size"],
                "handle": started["handle"],
            }
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(started["url"])
            if args.wait:
                server = started["server"]
                try:
                    print("streaming on localhost — Ctrl+C to stop", file=sys.stderr)
                    threading_event_wait()
                except KeyboardInterrupt:
                    pass
                finally:
                    server.stop()
                    if started.get("owns_client"):
                        started["client"].close()
            else:
                # detach: keep server alive via daemon thread until process exit
                # without --wait, print URL and exit (server dies) — require --wait
                # for useful streaming; document this.
                if not args.json:
                    print(
                        "note: pass --wait to keep the stream server alive",
                        file=sys.stderr,
                    )
                server = started["server"]
                server.stop()
                if started.get("owns_client"):
                    started["client"].close()
            return 0

        print(f"unknown mega command: {cmd}", file=sys.stderr)
        return 2
    except mega_error as exc:
        code = getattr(exc, "code", "mega_error")
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"ok": False, "error": str(exc), "code": code},
                    ensure_ascii=False,
                )
            )
        else:
            print(str(exc), file=sys.stderr)
        return 1


def threading_event_wait() -> None:
    import threading

    event = threading.Event()
    event.wait()


def _print(payload: dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0
