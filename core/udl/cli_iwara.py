"""CLI handlers for ``udl iwara`` browse / auth / subscription commands."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any

from udl.extractors.iwara_auth import iwara_auth_error
from udl.extractors.iwara_auth import login as iwara_login
from udl.extractors.iwara_auth import logout as iwara_logout
from udl.extractors.iwara_auth import whoami as iwara_whoami
from udl.extractors.iwara_browse import annotate_local_status, iwara_browser
from udl.extractors.iwara_filters import filters_from_args
from udl.session import open_store
from udl.store.iwara_subs import (
    add_home_section,
    add_subscription,
    enqueue_videos,
    fetch_home_section,
    import_following_authors,
    list_home_sections,
    list_new_items,
    list_subscriptions,
    mark_items_seen,
    remove_home_section,
    remove_subscription,
    set_subscription_enabled,
    sync_all_subscriptions,
)


def add_iwara_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_iwara = sub.add_parser(
        "iwara",
        help="Iwara browse search subscribe sync login",
    )
    iw_sub = p_iwara.add_subparsers(dest="iwara_command", required=True)

    p_login = iw_sub.add_parser("login", help="login and store tokens in OS keychain")
    p_login.add_argument("--email", default=None, help="email or username")
    p_login.add_argument("--password", default=None, help="password (prefer prompt)")
    p_login.add_argument("--cloudflare-bypass", action="store_true")
    p_login.add_argument("--json", action="store_true")

    p_logout = iw_sub.add_parser("logout", help="clear Iwara secrets from keychain")
    p_logout.add_argument("--json", action="store_true")

    p_whoami = iw_sub.add_parser("whoami", help="validate session and show account")
    p_whoami.add_argument("--cloudflare-bypass", action="store_true")
    p_whoami.add_argument("--json", action="store_true")

    p_import = iw_sub.add_parser(
        "import-following",
        help="import account following into local subscriptions",
    )
    p_import.add_argument("--json", action="store_true")
    p_import.add_argument("--max-pages", type=int, default=100)
    p_import.add_argument("--cloudflare-bypass", action="store_true")
    p_import.add_argument(
        "--sync",
        action="store_true",
        help="after import run sync to enqueue new content",
    )
    p_import.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="output dir required when --sync",
    )
    p_import.add_argument("--quality", default=None)
    p_import.add_argument("--max-sync-pages", type=int, default=5)
    p_import.add_argument("--no-enqueue", action="store_true")

    def _common_browse(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true", help="emit JSON")
        p.add_argument("--page", type=int, default=0)
        p.add_argument("--limit", type=int, default=32)
        p.add_argument(
            "--sort",
            default=None,
            help="date|trending|popularity|views|likes (search: relevance|date|views|likes)",
        )
        p.add_argument(
            "--rating",
            default=None,
            help="SFW/NSFW filter: all|ecchi|general",
        )
        p.add_argument("--cloudflare-bypass", action="store_true")
        p.add_argument(
            "--token",
            default=None,
            help="optional Bearer override (else keychain session)",
        )
        p.add_argument("--min-likes", type=int, default=None)
        p.add_argument("--max-likes", type=int, default=None)
        p.add_argument("--min-views", type=int, default=None)
        p.add_argument("--max-views", type=int, default=None)
        p.add_argument("--date-from", default=None)
        p.add_argument("--date-to", default=None)
        p.add_argument("--include-tags", default=None, help="comma-separated include tags")
        p.add_argument("--exclude-tags", default=None, help="comma-separated exclude tags")
        p.add_argument("--title", default=None, help="title keyword(s)")
        p.add_argument(
            "--title-mode",
            choices=("all", "any"),
            default="all",
        )
        p.add_argument(
            "--no-status",
            action="store_true",
            help="skip local downloaded/queued annotation",
        )

    p_search = iw_sub.add_parser("search", help="keyword search")
    p_search.add_argument("query", help="search keyword")
    p_search.add_argument(
        "--type",
        dest="search_type",
        default="videos",
        choices=("videos", "users", "playlists"),
    )
    _common_browse(p_search)

    p_tag = iw_sub.add_parser("tag", help="list videos by tag")
    p_tag.add_argument("tag", help="tag id / name")
    _common_browse(p_tag)

    p_author = iw_sub.add_parser("author", help="list author videos")
    p_author.add_argument("username", help="author username")
    _common_browse(p_author)

    p_playlist = iw_sub.add_parser("playlist", help="list playlist videos")
    p_playlist.add_argument("playlist_id", help="playlist id")
    _common_browse(p_playlist)

    p_home = iw_sub.add_parser("home", help="home / recommendation lists")
    _common_browse(p_home)

    p_preview = iw_sub.add_parser("preview", help="resolve HTML5 stream URL for a video")
    p_preview.add_argument("video_id")
    p_preview.add_argument("--json", action="store_true")
    p_preview.add_argument("--cloudflare-bypass", action="store_true")
    p_preview.add_argument("--token", default=None)

    p_enqueue = iw_sub.add_parser("enqueue", help="enqueue video ids into persistent queue")
    p_enqueue.add_argument("video_ids", nargs="+")
    p_enqueue.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        help="output directory for queued jobs",
    )
    p_enqueue.add_argument("--json", action="store_true")
    p_enqueue.add_argument("--quality", default=None)
    p_enqueue.add_argument("--cloudflare-bypass", action="store_true")

    # subscriptions
    p_sub = iw_sub.add_parser("subscribe", help="add author or playlist subscription")
    p_sub.add_argument("target", help="username, playlist id, or profile/playlist URL")
    p_sub.add_argument(
        "--kind",
        choices=("author", "playlist", "auto"),
        default="auto",
    )
    p_sub.add_argument("--title", default=None)
    p_sub.add_argument("--json", action="store_true")

    p_unsub = iw_sub.add_parser("unsubscribe", help="remove subscription by id")
    p_unsub.add_argument("id", type=int)
    p_unsub.add_argument("--json", action="store_true")

    p_subs = iw_sub.add_parser("subscriptions", help="list local subscriptions")
    p_subs.add_argument("--json", action="store_true")
    p_subs.add_argument("--enabled-only", action="store_true")

    p_enable = iw_sub.add_parser("sub-enable", help="enable / disable a subscription")
    p_enable.add_argument("id", type=int)
    p_enable.add_argument("--disable", action="store_true")
    p_enable.add_argument("--json", action="store_true")

    p_sync = iw_sub.add_parser("sync", help="sync subscriptions and enqueue new videos")
    p_sync.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        help="output directory for enqueued downloads",
    )
    p_sync.add_argument("--json", action="store_true")
    p_sync.add_argument("--max-pages", type=int, default=5)
    p_sync.add_argument("--no-enqueue", action="store_true")
    p_sync.add_argument("--quality", default=None)
    p_sync.add_argument("--cloudflare-bypass", action="store_true")
    p_sync.add_argument("--token", default=None)

    p_new = iw_sub.add_parser("new", help="list unseen subscription items")
    p_new.add_argument("--json", action="store_true")
    p_new.add_argument("--source-id", type=int, default=None)
    p_new.add_argument("--limit", type=int, default=100)
    p_new.add_argument("--mark-seen", action="store_true")

    # home sections
    p_sec_add = iw_sub.add_parser("section-add", help="add custom home section")
    p_sec_add.add_argument("name")
    p_sec_add.add_argument(
        "--kind",
        required=True,
        choices=("home", "search", "tag", "author", "playlist"),
    )
    p_sec_add.add_argument("--value", default="", help="query / tag / username / playlist id")
    p_sec_add.add_argument("--sort", default="date")
    p_sec_add.add_argument("--rating", default=None)
    p_sec_add.add_argument("--json", action="store_true")

    p_sec_list = iw_sub.add_parser("sections", help="list custom home sections")
    p_sec_list.add_argument("--json", action="store_true")

    p_sec_rm = iw_sub.add_parser("section-remove", help="remove a home section")
    p_sec_rm.add_argument("id", type=int)
    p_sec_rm.add_argument("--json", action="store_true")

    p_sec_fetch = iw_sub.add_parser("section-fetch", help="fetch one home section page")
    p_sec_fetch.add_argument("id", type=int)
    p_sec_fetch.add_argument("--json", action="store_true")
    p_sec_fetch.add_argument("--page", type=int, default=0)
    p_sec_fetch.add_argument("--limit", type=int, default=32)
    p_sec_fetch.add_argument("--cloudflare-bypass", action="store_true")
    p_sec_fetch.add_argument("--token", default=None)


def _filters_from_ns(args: argparse.Namespace):
    return filters_from_args(
        min_likes=getattr(args, "min_likes", None),
        max_likes=getattr(args, "max_likes", None),
        min_views=getattr(args, "min_views", None),
        max_views=getattr(args, "max_views", None),
        date_from=getattr(args, "date_from", None),
        date_to=getattr(args, "date_to", None),
        include_tags=getattr(args, "include_tags", None),
        exclude_tags=getattr(args, "exclude_tags", None),
        title=getattr(args, "title", None),
        title_mode=getattr(args, "title_mode", "all") or "all",
        rating=getattr(args, "rating", None),
    )


def _print(payload: Any, *, as_json: bool) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=None if as_json else 2)
    print(text)


def _browser_from_args(args: argparse.Namespace) -> iwara_browser:
    return iwara_browser(
        token=getattr(args, "token", None),
        use_cloudflare_bypass=bool(getattr(args, "cloudflare_bypass", False)),
    )


def _annotate(args: argparse.Namespace, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if getattr(args, "no_status", False) or not items:
        return items
    conn = open_store(getattr(args, "db", None))
    try:
        return annotate_local_status(conn, items)
    finally:
        conn.close()


def _detect_subscribe_target(target: str, kind: str) -> tuple[str, str]:
    from udl.extractors.iwara import parse_iwara_url

    raw = target.strip()
    if kind != "auto":
        return kind, raw.lstrip("@").strip("/")
    parsed = parse_iwara_url(raw)
    if parsed:
        pkind, value = parsed
        if pkind == "user":
            return "author", value
        if pkind == "playlist":
            return "playlist", value
    if "/" in raw or raw.startswith("http"):
        raise ValueError(f"cannot detect subscription kind from: {target}")
    # bare username vs playlist id: treat as author by default
    return "author", raw.lstrip("@")


def cmd_iwara(args: argparse.Namespace) -> int:
    cmd = args.iwara_command
    as_json = bool(getattr(args, "json", False))

    if cmd == "login":
        email = (args.email or "").strip()
        password = args.password
        if not email:
            try:
                email = input("Iwara email/username: ").strip()
            except EOFError:
                email = ""
        if not password:
            try:
                password = getpass.getpass("Iwara password: ")
            except EOFError:
                password = ""
        try:
            account = iwara_login(
                email,
                password or "",
                use_cloudflare_bypass=bool(args.cloudflare_bypass),
            )
        except iwara_auth_error as exc:
            _print({"ok": False, "error": str(exc)}, as_json=as_json)
            return 1
        payload = {"ok": True, "account": account.to_dict()}
        _print(payload, as_json=as_json)
        return 0

    if cmd == "logout":
        iwara_logout()
        _print({"ok": True, "logged_out": True}, as_json=as_json)
        return 0

    if cmd == "whoami":
        try:
            account = iwara_whoami(
                use_cloudflare_bypass=bool(args.cloudflare_bypass),
            )
        except iwara_auth_error as exc:
            _print({"ok": False, "error": str(exc), "logged_in": False}, as_json=as_json)
            return 1
        payload = {"ok": True, **account.to_dict()}
        _print(payload, as_json=as_json)
        return 0 if account.logged_in else 1

    if cmd == "import-following":
        if args.sync and args.output is None:
            print("--output is required with --sync", file=sys.stderr)
            return 2
        conn = open_store(getattr(args, "db", None))
        try:
            try:
                payload = import_following_authors(
                    conn,
                    use_cloudflare_bypass=bool(args.cloudflare_bypass),
                    max_pages=int(args.max_pages),
                )
            except iwara_auth_error as exc:
                _print({"ok": False, "error": str(exc)}, as_json=as_json)
                return 1
            if args.sync:
                assert args.output is not None
                args.output.mkdir(parents=True, exist_ok=True)
                browser = _browser_from_args(args)
                try:
                    sync_payload = sync_all_subscriptions(
                        conn,
                        browser,
                        output_dir=str(args.output.resolve()),
                        enqueue=not bool(args.no_enqueue),
                        max_pages=int(args.max_sync_pages),
                        cloudflare_bypass=bool(args.cloudflare_bypass),
                        quality=args.quality,
                    )
                finally:
                    browser.close()
                payload["sync"] = sync_payload
            payload["ok"] = True
        finally:
            conn.close()
        _print(payload, as_json=as_json)
        return 0

    if cmd in {"search", "tag", "author", "playlist", "home"}:
        browser = _browser_from_args(args)
        filters = _filters_from_ns(args)
        try:
            if cmd == "search":
                sort = args.sort or "relevance"
                page = browser.search(
                    args.query,
                    search_type=args.search_type,
                    page=args.page,
                    limit=args.limit,
                    sort=sort,
                    rating=args.rating,
                    filters=filters if args.search_type == "videos" else None,
                )
            elif cmd == "tag":
                page = browser.tag(
                    args.tag,
                    page=args.page,
                    limit=args.limit,
                    sort=args.sort or "date",
                    rating=args.rating,
                    filters=filters,
                )
            elif cmd == "author":
                page = browser.author(
                    args.username,
                    page=args.page,
                    limit=args.limit,
                    sort=args.sort or "date",
                    rating=args.rating,
                    filters=filters,
                )
            elif cmd == "playlist":
                page = browser.playlist(
                    args.playlist_id,
                    page=args.page,
                    limit=args.limit,
                    filters=filters,
                )
            else:
                page = browser.home(
                    sort=args.sort or "date",
                    page=args.page,
                    limit=args.limit,
                    rating=args.rating,
                    filters=filters,
                )
        finally:
            browser.close()
        payload = page.to_dict()
        items = payload.get("items") or []
        if items and isinstance(items[0], dict) and "video_id" in items[0]:
            payload["items"] = _annotate(args, items)
        if page.error:
            _print(payload, as_json=as_json)
            return 1
        _print(payload, as_json=as_json)
        return 0

    if cmd == "preview":
        browser = _browser_from_args(args)
        try:
            payload = browser.preview_stream(args.video_id)
        finally:
            browser.close()
        _print(payload, as_json=as_json)
        return 0 if payload.get("ok") else 1

    if cmd == "enqueue":
        conn = open_store(args.db)
        try:
            items = [
                {"video_id": vid, "canonical_url": f"https://www.iwara.tv/video/{vid}"}
                for vid in args.video_ids
            ]
            payload = enqueue_videos(
                conn,
                items,
                output_dir=str(args.output.resolve()),
                cloudflare_bypass=bool(args.cloudflare_bypass),
                quality=args.quality,
            )
        finally:
            conn.close()
        _print(payload, as_json=as_json)
        return 0

    if cmd == "subscribe":
        try:
            kind, key = _detect_subscribe_target(args.target, args.kind)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        conn = open_store(args.db)
        try:
            sub = add_subscription(conn, kind=kind, key=key, title=args.title)
            payload = sub.to_dict()
        finally:
            conn.close()
        _print(payload, as_json=as_json)
        return 0

    if cmd == "unsubscribe":
        conn = open_store(args.db)
        try:
            ok = remove_subscription(conn, args.id)
            payload = {"removed": ok, "id": args.id}
        finally:
            conn.close()
        _print(payload, as_json=as_json)
        return 0 if ok else 1

    if cmd == "subscriptions":
        conn = open_store(args.db)
        try:
            rows = list_subscriptions(conn, enabled_only=bool(args.enabled_only))
            payload = [r.to_dict() for r in rows]
        finally:
            conn.close()
        _print(payload, as_json=as_json)
        return 0

    if cmd == "sub-enable":
        conn = open_store(args.db)
        try:
            sub = set_subscription_enabled(conn, args.id, enabled=not args.disable)
            payload = sub.to_dict() if sub else {"error": "not found", "id": args.id}
        finally:
            conn.close()
        _print(payload, as_json=as_json)
        return 0 if sub else 1

    if cmd == "sync":
        args.output.mkdir(parents=True, exist_ok=True)
        browser = _browser_from_args(args)
        conn = open_store(args.db)
        try:
            payload = sync_all_subscriptions(
                conn,
                browser,
                output_dir=str(args.output.resolve()),
                enqueue=not bool(args.no_enqueue),
                max_pages=args.max_pages,
                cloudflare_bypass=bool(args.cloudflare_bypass),
                quality=args.quality,
            )
        finally:
            browser.close()
            conn.close()
        _print(payload, as_json=as_json)
        return 0

    if cmd == "new":
        conn = open_store(args.db)
        try:
            items = list_new_items(conn, source_id=args.source_id, limit=args.limit)
            if args.mark_seen:
                mark_items_seen(conn, source_id=args.source_id)
            payload = {"items": items, "count": len(items)}
        finally:
            conn.close()
        _print(payload, as_json=as_json)
        return 0

    if cmd == "section-add":
        conn = open_store(args.db)
        try:
            section = add_home_section(
                conn,
                name=args.name,
                query_kind=args.kind,
                query_value=args.value,
                sort=args.sort,
                rating=args.rating,
            )
            payload = section.to_dict()
        finally:
            conn.close()
        _print(payload, as_json=as_json)
        return 0

    if cmd == "sections":
        conn = open_store(args.db)
        try:
            payload = [s.to_dict() for s in list_home_sections(conn)]
        finally:
            conn.close()
        _print(payload, as_json=as_json)
        return 0

    if cmd == "section-remove":
        conn = open_store(args.db)
        try:
            ok = remove_home_section(conn, args.id)
            payload = {"removed": ok, "id": args.id}
        finally:
            conn.close()
        _print(payload, as_json=as_json)
        return 0 if ok else 1

    if cmd == "section-fetch":
        conn = open_store(args.db)
        browser = _browser_from_args(args)
        try:
            sections = {s.id: s for s in list_home_sections(conn)}
            section = sections.get(args.id)
            if section is None:
                _print({"error": "not found", "id": args.id}, as_json=as_json)
                return 1
            payload = fetch_home_section(
                browser,
                section,
                page=args.page,
                limit=args.limit,
                conn=conn,
            )
        finally:
            browser.close()
            conn.close()
        _print(payload, as_json=as_json)
        return 1 if payload.get("error") else 0

    print(f"unknown iwara command: {cmd}", file=sys.stderr)
    return 2
