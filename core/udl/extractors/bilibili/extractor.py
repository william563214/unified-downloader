"""Bilibili extractor — v0.7 parity with Bili23-Downloader (GPL-3.0).

Decoupled from Qt. No paywall VIP-only or region-lock bypass —
only streams the current session is entitled to. Login via login.py
and udl.secrets (bilibili.session).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urlparse

from udl.download.http import (
    download_file,
    download_item,
    download_options,
    options_from_network,
)
from udl.events import emitter
from udl.extractors.base import extract_result, extracted_file, extractor
from udl.extractors.bilibili.cdn import candidate_urls, get_netloc, host_health, stream_headers
from udl.extractors.bilibili.chapters import (
    to_ffmetadata,
    view_points_from_player,
    write_ffmetadata,
)
from udl.extractors.bilibili.client import bilibili_api_error, bilibili_client
from udl.extractors.bilibili.danmaku import write_danmaku
from udl.extractors.bilibili.filter import apply_filter, select_filter
from udl.extractors.bilibili.ids import (
    classify_url,
    login_kinds,
    public_kinds,
    safe_filename,
    split_batch_urls,
    url_target,
)
from udl.extractors.bilibili.login import login_note, requires_login, session_cookies_from_store
from udl.extractors.bilibili.options import bili_options
from udl.extractors.bilibili.quality import (
    available_audio_qualities,
    available_codecs,
    available_qualities,
    pick_audio_stream,
    pick_video_stream,
    stream_urls,
)
from udl.extractors.bilibili.subtitles import (
    filter_tracks,
    to_iso_639_2,
    tracks_from_player,
    write_subtitle,
)
from udl.extractors.bilibili.templates import default_template_for, type_template_key
from udl.metadata import item_metadata
from udl.postprocess.ffmpeg import ffmpeg_missing_error, find_ffmpeg, merge_dash, merge_error

blocked_badges = frozenset(
    {
        "会员",
        "大會員",
        "大会员",
        "付费",
        "付費",
        "付费抢先",
        "充电专属",
        "充電專屬",
    }
)

list_kinds = frozenset(
    {
        "space",
        "favlist",
        "popular",
        "collection",
        "series",
        "audio_menu",
        "watchlater",
        "history",
        "followed_bangumi",
        "subscribed_collections",
        "favorite_folders",
    }
)

expand_all_kinds = frozenset(
    {
        "space",
        "favlist",
        "collection",
        "series",
        "followed_bangumi",
        "subscribed_collections",
        "favorite_folders",
        "watchlater",
        "history",
    }
)


@dataclass
class part_info:
    page: int
    cid: int
    title: str
    bvid: str
    aid: int | None = None
    ep_id: int | None = None
    badge: str = ""
    duration: int | None = None
    upload_date: str | None = None
    edge_id: int | None = None
    needs_expand: bool = False
    kind: str = "video"
    audio_id: int | None = None


@dataclass
class probe_info:
    """metadata for GUI quality / part selection."""

    site: str
    kind: str
    title: str
    url: str
    parts: list[dict[str, Any]]
    qualities: list[dict[str, Any]]
    default_part: int
    default_quality: int | None
    message: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    codecs: list[dict[str, Any]] = field(default_factory=list)
    audio_qualities: list[dict[str, Any]] = field(default_factory=list)
    subtitle_tracks: list[dict[str, Any]] = field(default_factory=list)
    pagination: dict[str, Any] | None = None
    naming_template: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "site": self.site,
            "kind": self.kind,
            "title": self.title,
            "url": self.url,
            "parts": self.parts,
            "qualities": self.qualities,
            "codecs": self.codecs,
            "audio_qualities": self.audio_qualities,
            "subtitle_tracks": self.subtitle_tracks,
            "default_part": self.default_part,
            "default_quality": self.default_quality,
            "message": self.message,
            "meta": self.meta,
            "pagination": self.pagination,
            "naming_template": self.naming_template,
        }
        metadata = self.meta.get("metadata") if isinstance(self.meta, dict) else None
        if metadata is not None:
            payload["metadata"] = metadata
        return payload


class bilibili_extractor(extractor):
    site = "bilibili"
    label = "Bilibili"
    status = "implemented"
    kind_hint = "video|bangumi|cheese|audio|space|favlist|popular|collection"
    _VALID_URL = (
        r"(?i)https?://(?:[^/]+\.)?(?:bilibili\.com|b23\.tv|bili2233\.cn|biliapi\.net)"
        r"(?:[/?#]|$|/.*)"
    )

    def __init__(
        self,
        *,
        quality: int | None = None,
        part: int | str | None = None,
        ffmpeg: str | Path | None = None,
        client: bilibili_client | None = None,
        options: bili_options | None = None,
        codecid: int | None = None,
        audio_quality: int | None = None,
        container: str = "mp4",
        danmaku_format: str | None = None,
        subtitle_format: str | None = None,
        subtitle_languages: list[str] | None = None,
        embed_danmaku: bool = False,
        embed_subtitle: bool = False,
        write_chapters: bool = True,
        select: select_filter | None = None,
        page_num: int = 1,
        expand_all: bool = False,
        node_names: dict[str, str] | None = None,
        on_progress: Any | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        opts = options or bili_options()
        if quality is not None:
            opts.quality = quality
        if part is not None:
            opts.part = part
        if codecid is not None:
            opts.codecid = codecid
        if audio_quality is not None:
            opts.audio_quality = audio_quality
        if container:
            opts.container = container
        if danmaku_format is not None:
            opts.danmaku_format = danmaku_format
        if subtitle_format is not None:
            opts.subtitle_format = subtitle_format
        if subtitle_languages is not None:
            opts.subtitle_languages = list(subtitle_languages)
        opts.embed_danmaku = embed_danmaku or opts.embed_danmaku
        opts.embed_subtitle = embed_subtitle or opts.embed_subtitle
        opts.write_chapters = write_chapters
        if select is not None:
            opts.select = select
        opts.page_num = page_num
        opts.expand_all = expand_all or opts.expand_all
        if node_names is not None:
            opts.node_names = dict(node_names)
        if on_progress is not None:
            opts.on_progress = on_progress
        if cookies:
            opts.cookies = cookies
        self.options = opts
        self.quality = opts.quality
        self.part = opts.part
        self.ffmpeg = ffmpeg
        self._client = client
        self._own_client = client is None

    def _cli(self) -> bilibili_client:
        if self._client is None:
            cookies = self.options.cookies
            if not cookies:
                cookies = session_cookies_from_store()
            self._client = bilibili_client(cookies=cookies)
            self._own_client = True
        elif self.options.cookies:
            self._client.set_session_cookies(self.options.cookies)
        return self._client

    def close(self) -> None:
        if self._own_client and self._client is not None:
            self._client.close()
            self._client = None

    def _progress(
        self,
        phase: str,
        current: int,
        total: int,
        message: str = "",
    ) -> None:
        cb = self.options.on_progress
        if callable(cb):
            cb(phase, current, total, message)

    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        del password
        probe = self.probe(url)
        parts = self._select_parts(probe)
        if not parts:
            raise RuntimeError("no downloadable parts after filtering")

        multi = len(parts) > 1 or len(probe.parts) > 1
        files: list[extracted_file] = []
        job_meta = self._job_metadata(probe)
        ext = ".m4a" if probe.kind in {"audio", "audio_menu"} else f".{self.options.container}"
        for part in parts:
            part_id = self._part_item_id(part, probe.kind)
            part_meta = item_metadata(
                site=self.site,
                item_id=part_id,
                canonical_url=self._part_url(part, probe.kind),
                title=part.title or probe.title,
                author=job_meta.author,
                upload_date=part.upload_date or job_meta.upload_date,
                tags=list(job_meta.tags),
                description=job_meta.description,
                duration=float(part.duration) if part.duration is not None else job_meta.duration,
                resolution=(
                    str(self.quality or probe.default_quality)
                    if (self.quality or probe.default_quality) is not None
                    else None
                ),
                thumbnail_url=job_meta.thumbnail_url,
                cover_url=job_meta.cover_url,
                view_count=job_meta.view_count,
                like_count=job_meta.like_count,
                collection=probe.title if multi else job_meta.collection,
                collection_id=job_meta.collection_id or (part.bvid if multi else None),
                extra={
                    "bvid": part.bvid,
                    "cid": part.cid,
                    "page": part.page,
                    "aid": part.aid,
                    "ep_id": part.ep_id,
                    "edge_id": part.edge_id,
                    "kind": probe.kind if probe.kind not in list_kinds else part.kind,
                    "audio_id": part.audio_id,
                    "nfo_type": "musicvideo" if probe.kind.startswith("audio") else None,
                    "type_template": type_template_key(probe.kind),
                },
            )
            files.append(
                extracted_file(
                    url=f"bilibili://{part_id}",
                    name=self._output_name(probe.title, part, multi, ext=ext),
                    relative_dir=safe_filename(probe.title) if len(parts) > 1 else "",
                    size=None,
                    headers={"Referer": "https://www.bilibili.com/"},
                    meta={
                        "bvid": part.bvid,
                        "cid": part.cid,
                        "page": part.page,
                        "part_title": part.title,
                        "aid": part.aid,
                        "ep_id": part.ep_id,
                        "edge_id": part.edge_id,
                        "kind": probe.kind if probe.kind not in list_kinds else part.kind,
                        "probe_kind": probe.kind,
                        "quality": self.quality or probe.default_quality,
                        "codecid": self.options.codecid,
                        "audio_quality": self.options.audio_quality,
                        "needs_expand": part.needs_expand,
                        "audio_id": part.audio_id,
                        "container": self.options.container,
                        "duration": part.duration,
                    },
                    metadata=part_meta,
                )
            )

        return extract_result(
            site=self.site,
            title=probe.title,
            files=files,
            kind=probe.kind,
            meta={
                "parts": probe.parts,
                "qualities": probe.qualities,
                "codecs": probe.codecs,
                "audio_qualities": probe.audio_qualities,
                "default_part": probe.default_part,
                "default_quality": probe.default_quality,
                "login_note": login_note,
                "source_url": probe.url,
                "naming_template": probe.naming_template,
                "options": self.options.to_dict(),
                "logged_in": bool(self._cli().has_session),
            },
            metadata=job_meta,
        )

    def probe(self, url: str) -> probe_info:
        cli = self._cli()
        cli.bootstrap()
        target, final_url = self._resolve_target(url)
        if target.kind in login_kinds and not cli.has_session:
            raise requires_login(target.kind)
        dispatch = {
            "video": self._probe_video,
            "bangumi": self._probe_bangumi,
            "cheese": self._probe_cheese,
            "audio": self._probe_audio,
            "audio_menu": self._probe_audio_menu,
            "space": self._probe_space,
            "favlist": self._probe_favlist,
            "popular": self._probe_popular,
            "collection": self._probe_collection,
            "series": self._probe_series,
            "watchlater": self._probe_watchlater,
            "history": self._probe_history,
            "followed_bangumi": self._probe_followed_bangumi,
            "subscribed_collections": self._probe_subscribed_collections,
            "favorite_folders": self._probe_favorite_folders,
        }
        handler = dispatch.get(target.kind)
        if handler is None:
            raise RuntimeError(f"unsupported bilibili URL kind: {target.kind}")
        probe = handler(cli, target, final_url)
        if self.options.expand_all and probe.kind in expand_all_kinds:
            probe = self._expand_probe(cli, probe, target)
        probe.naming_template = default_template_for(
            probe.kind, multi=len(probe.parts) > 1
        )
        return probe

    def probe_batch(self, text: str) -> list[probe_info | dict[str, Any]]:
        """probe multiple pasted links; errors become dict entries."""
        results: list[probe_info | dict[str, Any]] = []
        for raw in split_batch_urls(text):
            try:
                results.append(self.probe(raw))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "site": self.site,
                        "url": raw,
                        "error": str(exc),
                        "code": getattr(exc, "code", None) or "probe_failed",
                    }
                )
        return results

    def download(
        self,
        result: extract_result,
        output_dir: Path,
        *,
        emit: emitter | None = None,
        cancel: Event | None = None,
        workers: int = 3,
        retries: int = 5,
        job_id: str | None = None,
        file_dest_overrides: dict[int, Path] | None = None,
        skip_indexes: set[int] | None = None,
    ) -> tuple[int, int]:
        del workers
        job = job_id or result.site
        if result.kind not in {"audio", "audio_menu"}:
            find_ffmpeg(self.ffmpeg)

        cli = self._cli()
        opts = options_from_network(
            output_dir,
            workers=1,
            retries=retries,
            cancel=cancel,
            headers={"Referer": "https://www.bilibili.com/"},
        )
        ok = 0
        failed = 0
        for idx, f in enumerate(result.files):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            file_id = f"{job}:{idx}"
            dest_dir = output_dir / f.relative_dir if f.relative_dir else output_dir
            dest = dest_dir / f.name
            if file_dest_overrides and idx in file_dest_overrides:
                dest = file_dest_overrides[idx]
            file_meta = f.metadata or result.metadata
            meta_payload = file_meta.to_dict(omit_empty=True) if file_meta else None
            if skip_indexes and idx in skip_indexes:
                ok += 1
                if emit:
                    emit.file(
                        job=job,
                        id=file_id,
                        name=f.name,
                        total=f.size,
                        path=str(dest),
                        index=idx,
                        metadata=meta_payload,
                        already_downloaded=True,
                    )
                    emit.done(
                        job=job,
                        file=file_id,
                        path=str(dest),
                        skipped=True,
                        already_downloaded=True,
                        metadata=meta_payload,
                    )
                continue
            if emit:
                emit.file(
                    job=job,
                    id=file_id,
                    name=f.name,
                    total=f.size,
                    path=str(dest),
                    index=idx,
                    metadata=meta_payload,
                )
            try:
                self._download_one(
                    cli,
                    f,
                    dest=dest,
                    file_id=file_id,
                    job_id=job,
                    opts=opts,
                    emit=emit,
                    cancel=cancel,
                )
                ok += 1
                if emit:
                    emit.done(
                        job=job,
                        file=file_id,
                        path=str(dest),
                        metadata=meta_payload,
                    )
            except InterruptedError:
                raise
            except Exception as exc:  # noqa: BLE001
                failed += 1
                if emit:
                    emit.error(
                        job=job,
                        file=file_id,
                        message=str(exc),
                        code=_error_code(exc),
                    )
        return ok, failed

    # --- probe handlers ---

    def _probe_video(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        bvid = target.bvid
        if not bvid:
            raise RuntimeError("missing bvid")
        data = cli.view(bvid)
        if data.get("is_upower_exclusive"):
            raise bilibili_api_error(
                "charging-exclusive content is not supported — no paywall bypass",
            )
        redirect = data.get("redirect_url")
        if redirect:
            return self.probe(str(redirect))

        # interactive / stein gate
        rights = data.get("rights") if isinstance(data.get("rights"), dict) else {}
        if rights.get("is_stein_gate"):
            return self._probe_interactive(cli, data, final_url)

        title = str(data.get("title") or bvid)
        pages_raw = data.get("pages") or []
        parts: list[part_info] = []
        if pages_raw:
            for p in pages_raw:
                parts.append(
                    part_info(
                        page=int(p.get("page") or len(parts) + 1),
                        cid=int(p["cid"]),
                        title=str(p.get("part") or title),
                        bvid=str(data.get("bvid") or bvid),
                        aid=int(data["aid"]) if data.get("aid") is not None else None,
                        duration=int(p["duration"])
                        if p.get("duration") is not None
                        else None,
                    )
                )
        else:
            parts.append(
                part_info(
                    page=1,
                    cid=int(data["cid"]),
                    title=title,
                    bvid=str(data.get("bvid") or bvid),
                    aid=int(data["aid"]) if data.get("aid") is not None else None,
                    duration=int(data["duration"])
                    if data.get("duration") is not None
                    else None,
                )
            )

        # ugc season hint → collection link in meta
        ugc = data.get("ugc_season") if isinstance(data.get("ugc_season"), dict) else None

        pinned = target.page or data.get("current_page_number")
        default_part = int(pinned) if pinned is not None else 1
        if default_part < 1 or default_part > len(parts):
            default_part = 1

        sample = parts[default_part - 1]
        play = cli.playurl_video(bvid=sample.bvid, cid=sample.cid, qn=127)
        dash = play.get("dash") or {}
        qualities = available_qualities(dash) if isinstance(dash, dict) else []
        codecs = (
            available_codecs(dash, quality=qualities[0]["qn"] if qualities else None, play=play)
            if isinstance(dash, dict)
            else []
        )
        audio_qualities = (
            available_audio_qualities(dash) if isinstance(dash, dict) else []
        )
        default_quality = qualities[0]["qn"] if qualities else None

        subtitle_tracks: list[dict[str, Any]] = []
        try:
            player = cli.player_v2(cid=sample.cid, bvid=sample.bvid)
            subtitle_tracks = [t.to_dict() for t in tracks_from_player(player)]
        except bilibili_api_error:
            player = {}

        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        stat = data.get("stat") if isinstance(data.get("stat"), dict) else {}
        tags = _extract_tags(data)
        upload_date = _pubdate_iso(data.get("pubdate"))
        metadata = item_metadata(
            site=self.site,
            item_id=str(bvid),
            canonical_url=f"https://www.bilibili.com/video/{bvid}",
            title=title,
            author=_str_or_none(owner.get("name")),
            upload_date=upload_date,
            tags=tags,
            description=_str_or_none(data.get("desc")),
            duration=_float_or_none(data.get("duration")),
            thumbnail_url=_str_or_none(data.get("pic")),
            cover_url=_str_or_none(data.get("pic")),
            view_count=_int_or_none(stat.get("view")),
            like_count=_int_or_none(stat.get("like")),
            collection=_str_or_none((ugc or {}).get("title")),
            collection_id=str((ugc or {}).get("id")) if ugc and ugc.get("id") else None,
            extra={
                "aid": data.get("aid"),
                "bvid": bvid,
                "owner_mid": owner.get("mid"),
                "interactive": False,
            },
        )
        return probe_info(
            site=self.site,
            kind="video",
            title=title,
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=qualities,
            codecs=codecs,
            audio_qualities=audio_qualities,
            subtitle_tracks=subtitle_tracks,
            default_part=default_part,
            default_quality=default_quality,
            meta={
                "login_note": login_note,
                "aid": data.get("aid"),
                "bvid": bvid,
                "url_pinned_part": pinned is not None,
                "author": owner.get("name"),
                "upload_date": upload_date,
                "tags": tags,
                "description": data.get("desc"),
                "duration": data.get("duration"),
                "pic": data.get("pic"),
                "thumbnail_url": data.get("pic"),
                "view_count": stat.get("view"),
                "like_count": stat.get("like"),
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    def _probe_interactive(
        self,
        cli: bilibili_client,
        data: dict[str, Any],
        final_url: str,
    ) -> probe_info:
        bvid = str(data.get("bvid") or "")
        title = str(data.get("title") or bvid)
        # graph_version from pages[0] or player
        pages = data.get("pages") or []
        if not pages:
            raise bilibili_api_error("interactive video missing pages")
        root_cid = int(pages[0]["cid"])
        player = cli.player_v2(cid=root_cid, bvid=bvid)
        interaction = player.get("interaction") or data.get("interaction") or {}
        graph_version = int(
            interaction.get("graph_version")
            or (interaction.get("graph_version") if isinstance(interaction, dict) else 0)
            or 0
        )
        if not graph_version:
            # fallback: stein edge without version sometimes works with portal=1
            edge = cli.stein_edge(bvid=bvid, graph_version=1, portal=1)
            graph_version = int(
                ((edge.get("hidden_vars") or [{}])[0]).get("value")
                or edge.get("graph_version")
                or 1
            )
        nodes = self._walk_stein(cli, bvid=bvid, graph_version=graph_version)
        if not nodes:
            # treat as single-page video
            return self._probe_video(
                cli,
                url_target(kind="video", bvid=bvid),
                final_url,
            )
        parts = [
            part_info(
                page=i,
                cid=n["cid"],
                title=str(n.get("title") or f"node{i}"),
                bvid=bvid,
                aid=int(data["aid"]) if data.get("aid") is not None else None,
                edge_id=n.get("edge_id"),
                kind="interactive",
            )
            for i, n in enumerate(nodes, start=1)
        ]
        sample = parts[0]
        play = cli.playurl_video(bvid=bvid, cid=sample.cid, qn=127)
        dash = play.get("dash") or {}
        qualities = available_qualities(dash) if isinstance(dash, dict) else []
        codecs = available_codecs(dash, play=play) if isinstance(dash, dict) else []
        audio_qualities = (
            available_audio_qualities(dash) if isinstance(dash, dict) else []
        )
        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        metadata = item_metadata(
            site=self.site,
            item_id=bvid,
            canonical_url=f"https://www.bilibili.com/video/{bvid}",
            title=title,
            author=_str_or_none(owner.get("name")),
            cover_url=_str_or_none(data.get("pic")),
            thumbnail_url=_str_or_none(data.get("pic")),
            extra={"interactive": True, "graph_version": graph_version},
        )
        return probe_info(
            site=self.site,
            kind="interactive",
            title=title,
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=qualities,
            codecs=codecs,
            audio_qualities=audio_qualities,
            default_part=1,
            default_quality=qualities[0]["qn"] if qualities else None,
            message=f"interactive video — {len(parts)} graph nodes",
            meta={
                "login_note": login_note,
                "bvid": bvid,
                "graph_version": graph_version,
                "author": owner.get("name"),
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    def _walk_stein(
        self,
        cli: bilibili_client,
        *,
        bvid: str,
        graph_version: int,
        max_nodes: int = 2048,
    ) -> list[dict[str, Any]]:
        """BFS over all stein edges (questions + skips); dedupe by cid."""
        queue: list[int | None] = [None]
        seen_edges: set[int] = set()
        visited_root = False
        nodes: list[dict[str, Any]] = []
        seen_cids: set[int] = set()

        def _enqueue(nid: Any) -> None:
            if nid is None:
                return
            try:
                value = int(nid)
            except (TypeError, ValueError):
                return
            if value not in seen_edges:
                queue.append(value)

        def _add_node(cid: int, title: str, edge_id: int | None) -> None:
            if not cid or cid in seen_cids or len(nodes) >= max_nodes:
                return
            seen_cids.add(cid)
            nodes.append({"cid": cid, "title": title, "edge_id": edge_id})

        while queue and len(nodes) < max_nodes:
            edge_id = queue.pop(0)
            if edge_id is None:
                if visited_root:
                    continue
                visited_root = True
            elif edge_id in seen_edges:
                continue
            else:
                seen_edges.add(edge_id)
            try:
                data = cli.stein_edge(
                    bvid=bvid,
                    graph_version=graph_version,
                    edge_id=edge_id,
                    portal=1 if edge_id is None else 0,
                )
            except bilibili_api_error:
                if edge_id is None:
                    break
                continue
            title = str(data.get("title") or "") if data.get("title") else ""
            page = data.get("page") if isinstance(data.get("page"), dict) else {}
            cur_cid = int(page["cid"]) if page.get("cid") else 0
            if cur_cid:
                _add_node(cur_cid, title or f"edge-{edge_id or 0}", edge_id)
            for item in data.get("story_list") or []:
                if not isinstance(item, dict):
                    continue
                cid = int(item.get("cid") or 0)
                item_edge = item.get("edge_id")
                item_edge_i = int(item_edge) if item_edge is not None else None
                _add_node(
                    cid,
                    str(item.get("title") or item_edge or f"node-{cid}"),
                    item_edge_i,
                )
                _enqueue(item_edge_i)
            edges_obj = data.get("edges") if isinstance(data.get("edges"), dict) else {}
            for q in edges_obj.get("questions") or []:
                if not isinstance(q, dict):
                    continue
                for choice in q.get("choices") or []:
                    if isinstance(choice, dict):
                        _enqueue(choice.get("id") or choice.get("edge_id"))
            for skip in edges_obj.get("skips") or []:
                if isinstance(skip, dict):
                    _enqueue(skip.get("id") or skip.get("edge_id"))

        names = self.options.node_names or {}
        if names:
            for index, n in enumerate(nodes, start=1):
                eid = n.get("edge_id")
                override = names.get(f"page:{index}") or names.get(str(index))
                if not override and eid is not None:
                    override = names.get(str(eid)) or names.get(f"edge:{eid}")
                if not override:
                    override = names.get(f"cid:{n.get('cid')}")
                if override:
                    n["title"] = override
        return nodes

    def _probe_bangumi(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        result = cli.bangumi_season(
            ep_id=target.ep_id,
            season_id=target.season_id,
            media_id=target.media_id,
        )
        title = str(result.get("title") or "bangumi")
        episodes = list(result.get("episodes") or [])
        for section in result.get("section") or []:
            for ep in section.get("episodes") or []:
                if ep.get("bvid") and ep.get("cid"):
                    episodes.append(ep)

        parts: list[part_info] = []
        blocked: list[str] = []
        for i, ep in enumerate(episodes, start=1):
            badge = str(ep.get("badge") or "").strip()
            bvid = ep.get("bvid")
            cid = ep.get("cid")
            if not bvid or not cid:
                continue
            if badge in blocked_badges:
                blocked.append(f"ep{ep.get('id')}:{badge}")
                continue
            if badge in {"预告", "預告"}:
                continue
            show = str(
                ep.get("share_copy") or ep.get("long_title") or ep.get("title") or f"P{i}"
            )
            parts.append(
                part_info(
                    page=len(parts) + 1,
                    cid=int(cid),
                    title=show,
                    bvid=str(bvid),
                    aid=int(ep["aid"]) if ep.get("aid") is not None else None,
                    ep_id=int(ep["id"]) if ep.get("id") is not None else None,
                    badge=badge,
                    duration=int(ep["duration"] // 1000)
                    if isinstance(ep.get("duration"), int)
                    else None,
                    kind="bangumi",
                )
            )

        if not parts:
            detail = ", ".join(blocked[:5]) if blocked else "no free episodes"
            raise bilibili_api_error(
                f"no freely accessible bangumi episodes ({detail}) — "
                "VIP/paid content is not downloaded",
            )

        default_part = 1
        current_ep = result.get("current_ep_id")
        if current_ep is not None:
            for p in parts:
                if p.ep_id == int(current_ep):
                    default_part = p.page
                    break

        sample = parts[default_part - 1]
        qualities: list[dict[str, Any]] = []
        codecs: list[dict[str, Any]] = []
        audio_qualities: list[dict[str, Any]] = []
        default_quality: int | None = None
        message: str | None = None
        try:
            play = cli.playurl_bangumi(bvid=sample.bvid, cid=sample.cid, qn=80)
            dash = play.get("dash") or {}
            if isinstance(dash, dict):
                qualities = available_qualities(dash)
                codecs = available_codecs(dash, play=play)
                audio_qualities = available_audio_qualities(dash)
                default_quality = qualities[0]["qn"] if qualities else None
        except bilibili_api_error as exc:
            message = str(exc)

        season_id = result.get("season_id")
        cover = result.get("cover") or result.get("square_cover")
        metadata = item_metadata(
            site=self.site,
            item_id=str(season_id or target.ep_id or title),
            canonical_url=final_url,
            title=title,
            cover_url=_str_or_none(cover),
            thumbnail_url=_str_or_none(cover),
            collection=title,
            collection_id=str(season_id) if season_id is not None else None,
            extra={
                "season_id": season_id,
                "media_id": result.get("media_id"),
                "episode_count": len(parts),
                "nfo_type": "episodedetails",
            },
        )
        return probe_info(
            site=self.site,
            kind="bangumi",
            title=title,
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=qualities,
            codecs=codecs,
            audio_qualities=audio_qualities,
            default_part=default_part,
            default_quality=default_quality,
            message=message,
            meta={
                "login_note": login_note,
                "season_id": season_id,
                "url_pinned_part": target.ep_id is not None,
                "cover": cover,
                "cover_url": cover,
                "thumbnail_url": cover,
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    def _probe_cheese(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        data = cli.cheese_season(ep_id=target.ep_id, season_id=target.season_id)
        title = str(data.get("title") or "cheese")
        parts: list[part_info] = []
        blocked: list[str] = []
        for section in data.get("sections") or []:
            for ep in section.get("episodes") or []:
                badge = str(ep.get("badge") or ep.get("label") or "").strip()
                if badge in blocked_badges or ep.get("episode_can_view") is False:
                    blocked.append(f"ep{ep.get('id')}:{badge or 'locked'}")
                    continue
                cid = ep.get("cid")
                aid = ep.get("aid")
                if not cid or not aid:
                    continue
                parts.append(
                    part_info(
                        page=len(parts) + 1,
                        cid=int(cid),
                        title=str(ep.get("title") or f"E{len(parts)+1}"),
                        bvid="",
                        aid=int(aid),
                        ep_id=int(ep["id"]) if ep.get("id") is not None else None,
                        badge=badge,
                        duration=int(ep["duration"])
                        if isinstance(ep.get("duration"), int)
                        else None,
                        kind="cheese",
                    )
                )
        if not parts:
            raise bilibili_api_error(
                f"no freely accessible cheese episodes "
                f"({', '.join(blocked[:5]) or 'locked'}) — no paywall bypass",
            )
        default_part = 1
        current = data.get("current_ep_id")
        if current is not None:
            for p in parts:
                if p.ep_id == int(current):
                    default_part = p.page
                    break
        sample = parts[default_part - 1]
        qualities: list[dict[str, Any]] = []
        codecs: list[dict[str, Any]] = []
        audio_qualities: list[dict[str, Any]] = []
        default_quality: int | None = None
        message: str | None = None
        try:
            play = cli.playurl_cheese(
                avid=int(sample.aid or 0),
                ep_id=int(sample.ep_id or 0),
                cid=sample.cid,
                qn=80,
            )
            dash = play.get("dash") or {}
            if isinstance(dash, dict):
                qualities = available_qualities(dash)
                codecs = available_codecs(dash, play=play)
                audio_qualities = available_audio_qualities(dash)
                default_quality = qualities[0]["qn"] if qualities else None
        except bilibili_api_error as exc:
            message = str(exc)
        cover = data.get("cover")
        metadata = item_metadata(
            site=self.site,
            item_id=str(data.get("season_id") or title),
            canonical_url=final_url,
            title=title,
            cover_url=_str_or_none(cover),
            thumbnail_url=_str_or_none(cover),
            collection=title,
            collection_id=str(data.get("season_id") or ""),
            extra={"kind": "cheese", "nfo_type": "episodedetails"},
        )
        return probe_info(
            site=self.site,
            kind="cheese",
            title=title,
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=qualities,
            codecs=codecs,
            audio_qualities=audio_qualities,
            default_part=default_part,
            default_quality=default_quality,
            message=message,
            meta={
                "login_note": login_note,
                "season_id": data.get("season_id"),
                "url_pinned_part": target.ep_id is not None,
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    def _probe_audio(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        sid = target.audio_id
        if sid is None:
            raise RuntimeError("missing audio id")
        data = cli.audio_info(sid)
        title = str(data.get("title") or f"au{sid}")
        duration = _int_or_none(data.get("duration"))
        author = _str_or_none(data.get("uname") or data.get("author"))
        cover = data.get("cover")
        part = part_info(
            page=1,
            cid=0,
            title=title,
            bvid="",
            duration=duration,
            kind="audio",
            audio_id=sid,
            upload_date=_pubdate_iso(data.get("passtime") or data.get("ctime")),
        )
        metadata = item_metadata(
            site=self.site,
            item_id=f"au{sid}",
            canonical_url=f"https://www.bilibili.com/audio/au{sid}",
            title=title,
            author=author,
            duration=float(duration) if duration is not None else None,
            cover_url=_str_or_none(cover),
            thumbnail_url=_str_or_none(cover),
            extra={"nfo_type": "musicvideo", "audio_id": sid},
        )
        return probe_info(
            site=self.site,
            kind="audio",
            title=title,
            url=final_url,
            parts=[_part_dict(part)],
            qualities=[],
            default_part=1,
            default_quality=None,
            meta={
                "login_note": login_note,
                "author": author,
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    def _probe_audio_menu(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        sid = target.audio_id
        if sid is None:
            raise RuntimeError("missing audio menu id")
        songs = cli.audio_menu_songs(sid)
        info = cli.audio_menu_info(sid)
        title = str(info.get("title") or f"am{sid}")
        data_list = songs.get("data") or songs.get("list") or []
        if isinstance(songs.get("data"), dict):
            data_list = songs["data"].get("data") or songs["data"].get("list") or []
        parts: list[part_info] = []
        for i, song in enumerate(data_list, start=1):
            if not isinstance(song, dict):
                continue
            aid = int(song.get("id") or song.get("sid") or 0)
            if not aid:
                continue
            parts.append(
                part_info(
                    page=i,
                    cid=0,
                    title=str(song.get("title") or f"au{aid}"),
                    bvid="",
                    duration=_int_or_none(song.get("duration")),
                    kind="audio",
                    audio_id=aid,
                )
            )
        if not parts:
            raise bilibili_api_error("audio menu is empty")
        metadata = item_metadata(
            site=self.site,
            item_id=f"am{sid}",
            canonical_url=f"https://www.bilibili.com/audio/am{sid}",
            title=title,
            collection=title,
            collection_id=str(sid),
            extra={"nfo_type": "musicvideo"},
        )
        return probe_info(
            site=self.site,
            kind="audio_menu",
            title=title,
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=[],
            default_part=1,
            default_quality=None,
            meta={
                "login_note": login_note,
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    def _probe_space(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        mid = target.mid
        if mid is None:
            raise RuntimeError("missing mid")
        keyword = target.keyword or ""
        data = cli.space_videos(
            mid, pn=self.options.page_num, keyword=keyword
        )
        try:
            card = cli.space_card(mid)
            uname = str(((card.get("card") or {}).get("name")) or mid)
        except bilibili_api_error:
            uname = str(mid)
        vlist = ((data.get("list") or {}).get("vlist")) or []
        parts = [_vlist_part(i, v) for i, v in enumerate(vlist, start=1)]
        page = data.get("page") or {}
        total = int(page.get("count") or len(parts))
        ps = int(page.get("ps") or 40)
        pn = int(page.get("pn") or self.options.page_num)
        metadata = item_metadata(
            site=self.site,
            item_id=f"space:{mid}",
            canonical_url=f"https://space.bilibili.com/{mid}/video",
            title=f"{uname} 的投稿",
            author=uname,
            collection=uname,
            collection_id=str(mid),
        )
        return probe_info(
            site=self.site,
            kind="space",
            title=f"{uname} 的投稿",
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=[],
            default_part=1,
            default_quality=None,
            message="list items expand to videos on download" + (
                f" (keyword={keyword})" if keyword else ""
            ),
            pagination={
                "total_pages": max(1, (total + ps - 1) // ps),
                "total_items": total,
                "current_page": pn,
            },
            meta={
                "login_note": login_note,
                "mid": mid,
                "keyword": keyword,
                "author": uname,
                "server_search": True,
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    def _probe_favlist(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        fid = target.fid
        if fid is None:
            raise RuntimeError("missing favlist media_id")
        keyword = target.keyword or ""
        data = cli.favlist(fid, pn=self.options.page_num, keyword=keyword)
        info = data.get("info") or {}
        title = str(info.get("title") or f"ml{fid}")
        medias = data.get("medias") or []
        parts: list[part_info] = []
        for i, m in enumerate(medias, start=1):
            if not isinstance(m, dict):
                continue
            bvid = str(m.get("bvid") or "")
            if not bvid:
                continue
            parts.append(
                part_info(
                    page=i,
                    cid=int(m.get("cid") or 0),
                    title=str(m.get("title") or bvid),
                    bvid=bvid,
                    aid=int(m["id"]) if m.get("id") is not None else None,
                    duration=_int_or_none(m.get("duration")),
                    upload_date=_pubdate_iso(m.get("fav_time") or m.get("ctime")),
                    needs_expand=not m.get("cid"),
                    kind="video",
                )
            )
        total = int(info.get("media_count") or len(parts))
        metadata = item_metadata(
            site=self.site,
            item_id=f"ml{fid}",
            canonical_url=final_url,
            title=title,
            collection=title,
            collection_id=str(fid),
        )
        return probe_info(
            site=self.site,
            kind="favlist",
            title=title,
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=[],
            default_part=1,
            default_quality=None,
            message="public favlist" + (f" keyword={keyword}" if keyword else ""),
            pagination={
                "total_pages": max(1, (total + 39) // 40),
                "total_items": total,
                "current_page": self.options.page_num,
            },
            meta={
                "login_note": login_note,
                "fid": fid,
                "keyword": keyword,
                "server_search": True,
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    def _probe_watchlater(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        keyword = target.keyword or ""
        data = cli.watchlater(pn=self.options.page_num, keyword=keyword)
        lst = data.get("list") or []
        parts: list[part_info] = []
        for i, v in enumerate(lst, start=1):
            if not isinstance(v, dict):
                continue
            bvid = str(v.get("bvid") or "")
            if not bvid:
                continue
            parts.append(
                part_info(
                    page=i,
                    cid=int(v.get("cid") or 0),
                    title=str(v.get("title") or bvid),
                    bvid=bvid,
                    aid=int(v["aid"]) if v.get("aid") is not None else None,
                    duration=_int_or_none(v.get("duration")),
                    needs_expand=not v.get("cid"),
                    kind="video",
                )
            )
        total = int(data.get("count") or len(parts))
        ps = 20
        return probe_info(
            site=self.site,
            kind="watchlater",
            title="稍後再看",
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=[],
            default_part=1,
            default_quality=None,
            message="watch-later list" + (f" keyword={keyword}" if keyword else ""),
            pagination={
                "total_pages": max(1, (total + ps - 1) // ps),
                "total_items": total,
                "current_page": self.options.page_num,
            },
            meta={
                "login_note": login_note,
                "keyword": keyword,
                "server_search": True,
            },
        )

    def _probe_history(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        keyword = target.keyword or ""
        data = cli.history(pn=self.options.page_num, keyword=keyword)
        lst = data.get("list") or []
        parts: list[part_info] = []
        for i, v in enumerate(lst, start=1):
            if not isinstance(v, dict):
                continue
            history = v.get("history") or {}
            bvid = str(history.get("bvid") or v.get("bvid") or "")
            if not bvid:
                continue
            parts.append(
                part_info(
                    page=i,
                    cid=int(history.get("cid") or 0),
                    title=str(v.get("title") or bvid),
                    bvid=bvid,
                    aid=int(history["oid"]) if history.get("oid") is not None else None,
                    duration=_int_or_none(v.get("duration")),
                    needs_expand=not history.get("cid"),
                    kind="video",
                )
            )
        page = data.get("page") or {}
        total = int(page.get("total") or len(parts))
        ps = int(page.get("ps") or 20)
        return probe_info(
            site=self.site,
            kind="history",
            title="觀看歷史",
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=[],
            default_part=1,
            default_quality=None,
            message="history list" + (f" keyword={keyword}" if keyword else ""),
            pagination={
                "total_pages": max(1, (total + ps - 1) // ps),
                "total_items": total,
                "current_page": self.options.page_num,
            },
            meta={
                "login_note": login_note,
                "keyword": keyword,
                "server_search": True,
            },
        )

    def _probe_followed_bangumi(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        data = cli.followed_bangumi(pn=self.options.page_num)
        lst = data.get("list") or []
        parts: list[part_info] = []
        for i, entry in enumerate(lst, start=1):
            if not isinstance(entry, dict):
                continue
            season_id = entry.get("season_id")
            if season_id is None:
                continue
            title = str(entry.get("title") or f"ss{season_id}")
            parts.append(
                part_info(
                    page=i,
                    cid=0,
                    title=title,
                    bvid="",
                    ep_id=None,
                    needs_expand=True,
                    kind="bangumi",
                )
            )
            parts[-1].aid = int(season_id)
        total = int(data.get("total") or len(parts))
        return probe_info(
            site=self.site,
            kind="followed_bangumi",
            title="追番／追劇",
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=[],
            default_part=1,
            default_quality=None,
            message="followed bangumi — expand each season on download",
            pagination={
                "total_pages": max(1, (total + 23) // 24),
                "total_items": total,
                "current_page": self.options.page_num,
            },
            meta={"login_note": login_note, "seasons": True},
        )

    def _probe_subscribed_collections(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        data = cli.subscribed_collections(pn=self.options.page_num)
        lst = data.get("list") or []
        parts: list[part_info] = []
        for i, entry in enumerate(lst, start=1):
            if not isinstance(entry, dict):
                continue
            collection_id = entry.get("id")
            mid = entry.get("mid")
            title = str(entry.get("title") or collection_id or f"item{i}")
            parts.append(
                part_info(
                    page=i,
                    cid=0,
                    title=title,
                    bvid="",
                    aid=int(collection_id) if collection_id is not None else None,
                    needs_expand=True,
                    kind="collection",
                )
            )
            # stash mid in edge_id unused field for expand helpers
            if mid is not None:
                parts[-1].edge_id = int(mid)
        total = int(data.get("count") or data.get("total") or len(parts))
        return probe_info(
            site=self.site,
            kind="subscribed_collections",
            title="訂閱合集",
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=[],
            default_part=1,
            default_quality=None,
            message="subscribed collections — expand on download",
            pagination={
                "total_pages": max(1, (total + 49) // 50),
                "total_items": total,
                "current_page": self.options.page_num,
            },
            meta={"login_note": login_note},
        )

    def _probe_favorite_folders(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        data = cli.favorite_folders()
        lst = data.get("list") or []
        parts: list[part_info] = []
        for i, entry in enumerate(lst, start=1):
            if not isinstance(entry, dict):
                continue
            fid = entry.get("id")
            title = str(entry.get("title") or f"ml{fid}")
            count = int(entry.get("media_count") or 0)
            parts.append(
                part_info(
                    page=i,
                    cid=0,
                    title=f"{title} ({count})",
                    bvid="",
                    aid=int(fid) if fid is not None else None,
                    needs_expand=True,
                    kind="favlist",
                )
            )
        return probe_info(
            site=self.site,
            kind="favorite_folders",
            title="我的收藏夾",
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=[],
            default_part=1,
            default_quality=None,
            message="own favorite folders — open each fid as favlist",
            meta={"login_note": login_note, "folders": True},
        )

    def _probe_popular(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        data = cli.popular_weekly(target.weekly_number)
        config = data.get("config") or {}
        title = str(config.get("name") or "每周必看")
        lst = data.get("list") or []
        parts: list[part_info] = []
        for i, v in enumerate(lst, start=1):
            if not isinstance(v, dict):
                continue
            bvid = str(v.get("bvid") or "")
            if not bvid:
                continue
            parts.append(
                part_info(
                    page=i,
                    cid=0,
                    title=str(v.get("title") or bvid),
                    bvid=bvid,
                    aid=int(v["aid"]) if v.get("aid") is not None else None,
                    duration=_int_or_none(v.get("duration")),
                    needs_expand=True,
                    kind="video",
                )
            )
        metadata = item_metadata(
            site=self.site,
            item_id=f"weekly:{config.get('number') or target.weekly_number or 0}",
            canonical_url=final_url,
            title=title,
            collection=title,
        )
        return probe_info(
            site=self.site,
            kind="popular",
            title=title,
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=[],
            default_part=1,
            default_quality=None,
            meta={
                "login_note": login_note,
                "weekly_number": config.get("number"),
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    def _probe_collection(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        mid = target.mid
        season_id = target.collection_id
        if mid is None or season_id is None:
            # try resolve via video ugc_season
            if target.bvid:
                data = cli.view(target.bvid)
                ugc = data.get("ugc_season") or {}
                mid = int(ugc.get("mid") or mid or 0)
                season_id = int(ugc.get("id") or season_id or 0)
            if not mid or not season_id:
                raise RuntimeError("collection requires mid and season_id")
        data = cli.collection_archives(
            mid=mid, season_id=season_id, pn=self.options.page_num
        )
        meta = data.get("meta") or {}
        title = str(meta.get("name") or f"collection-{season_id}")
        archives = data.get("archives") or []
        parts = [
            part_info(
                page=i,
                cid=0,
                title=str(a.get("title") or a.get("bvid")),
                bvid=str(a.get("bvid") or ""),
                aid=int(a["aid"]) if a.get("aid") is not None else None,
                duration=_int_or_none(a.get("duration")),
                needs_expand=True,
                kind="video",
            )
            for i, a in enumerate(archives, start=1)
            if isinstance(a, dict) and a.get("bvid")
        ]
        page = data.get("page") or {}
        total = int(page.get("total") or len(parts))
        metadata = item_metadata(
            site=self.site,
            item_id=f"collection:{season_id}",
            canonical_url=final_url,
            title=title,
            collection=title,
            collection_id=str(season_id),
        )
        return probe_info(
            site=self.site,
            kind="collection",
            title=title,
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=[],
            default_part=1,
            default_quality=None,
            pagination={
                "total_pages": max(1, (total + 29) // 30),
                "total_items": total,
                "current_page": self.options.page_num,
            },
            meta={
                "login_note": login_note,
                "mid": mid,
                "season_id": season_id,
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    def _probe_series(
        self,
        cli: bilibili_client,
        target: url_target,
        final_url: str,
    ) -> probe_info:
        mid = target.mid
        series_id = target.series_id
        if mid is None or series_id is None:
            raise RuntimeError("series requires mid and series_id")
        # if sid might be collection, try meta probe
        meta = cli.series_meta(series_id)
        if meta is None:
            return self._probe_collection(
                cli,
                url_target(
                    kind="collection",
                    mid=mid,
                    collection_id=series_id,
                    bvid=target.bvid,
                ),
                final_url,
            )
        data = cli.series_archives(
            mid=mid, series_id=series_id, pn=self.options.page_num
        )
        title = str(meta.get("name") or f"series-{series_id}")
        archives = data.get("archives") or []
        parts = [
            part_info(
                page=i,
                cid=0,
                title=str(a.get("title") or a.get("bvid")),
                bvid=str(a.get("bvid") or ""),
                aid=int(a["aid"]) if a.get("aid") is not None else None,
                duration=_int_or_none(a.get("duration")),
                needs_expand=True,
                kind="video",
            )
            for i, a in enumerate(archives, start=1)
            if isinstance(a, dict) and a.get("bvid")
        ]
        page = data.get("page") or {}
        total = int(page.get("total") or len(parts))
        metadata = item_metadata(
            site=self.site,
            item_id=f"series:{series_id}",
            canonical_url=final_url,
            title=title,
            collection=title,
            collection_id=str(series_id),
        )
        return probe_info(
            site=self.site,
            kind="series",
            title=title,
            url=final_url,
            parts=[_part_dict(p) for p in parts],
            qualities=[],
            default_part=1,
            default_quality=None,
            pagination={
                "total_pages": max(1, (total + 29) // 30),
                "total_items": total,
                "current_page": self.options.page_num,
            },
            meta={
                "login_note": login_note,
                "mid": mid,
                "series_id": series_id,
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    # --- download ---

    def _job_metadata(self, probe: probe_info) -> item_metadata:
        meta = probe.meta if isinstance(probe.meta, dict) else {}
        raw = meta.get("metadata")
        if isinstance(raw, dict):
            return item_metadata.from_dict(raw, site=self.site)
        return item_metadata(
            site=self.site,
            item_id=str(meta.get("bvid") or meta.get("season_id") or "") or None,
            canonical_url=probe.url,
            title=probe.title,
            author=_str_or_none(meta.get("author") or meta.get("owner")),
            upload_date=_str_or_none(meta.get("upload_date") or meta.get("pubdate")),
            tags=[str(t) for t in (meta.get("tags") or [])],
            description=_str_or_none(meta.get("description")),
            duration=_float_or_none(meta.get("duration")),
            thumbnail_url=_str_or_none(meta.get("thumbnail_url") or meta.get("pic")),
            cover_url=_str_or_none(meta.get("cover_url") or meta.get("cover")),
            view_count=_int_or_none(meta.get("view_count") or meta.get("view")),
            like_count=_int_or_none(meta.get("like_count") or meta.get("like")),
            extra={"kind": probe.kind, "login_note": login_note},
        )

    def _download_one(
        self,
        cli: bilibili_client,
        f: extracted_file,
        *,
        dest: Path,
        file_id: str,
        job_id: str,
        opts: download_options,
        emit: emitter | None,
        cancel: Event | None,
    ) -> Path:
        if dest.exists() and dest.stat().st_size > 0:
            return dest

        meta = dict(f.meta)
        kind = str(meta.get("kind") or "video")

        if meta.get("needs_expand") and meta.get("bvid"):
            view = cli.view(str(meta["bvid"]))
            pages = view.get("pages") or []
            if pages:
                meta["cid"] = int(pages[0]["cid"])
                meta["aid"] = view.get("aid")
                if not meta.get("part_title"):
                    meta["part_title"] = view.get("title")
            elif view.get("cid"):
                meta["cid"] = int(view["cid"])

        if kind == "audio" or meta.get("audio_id"):
            return self._download_audio(
                cli, meta, dest=dest, file_id=file_id, job_id=job_id, opts=opts, emit=emit
            )

        bvid = str(meta.get("bvid") or "")
        cid = int(meta.get("cid") or 0)
        if not cid:
            raise RuntimeError("missing cid for download")
        qn = meta.get("quality")
        qn_int = int(qn) if qn is not None else None
        codecid = meta.get("codecid")
        codec_int = int(codecid) if codecid is not None else self.options.codecid
        audio_q = meta.get("audio_quality")
        audio_int = (
            int(audio_q) if audio_q is not None else self.options.audio_quality
        )

        if kind == "bangumi":
            play = cli.playurl_bangumi(bvid=bvid, cid=cid, qn=qn_int or 80)
        elif kind == "cheese":
            play = cli.playurl_cheese(
                avid=int(meta.get("aid") or 0),
                ep_id=int(meta.get("ep_id") or 0),
                cid=cid,
                qn=qn_int or 80,
            )
        else:
            play = cli.playurl_video(bvid=bvid or "BV1", cid=cid, qn=127)

        dash = play.get("dash")
        if not isinstance(dash, dict):
            durl = play.get("durl") or []
            if not durl:
                raise RuntimeError("playurl has neither DASH nor durl streams")
            url = str(durl[0].get("url") or "")
            if not url:
                raise RuntimeError("empty durl url")
            item = download_item(
                url=url,
                dest=dest,
                file_id=file_id,
                name=f.name,
                headers=stream_headers(),
            )
            path = download_file(item, opts, job_id=job_id, emit=emit)
            self._write_extras(cli, meta, dest=path, emit=emit, job_id=job_id, file_id=file_id)
            return path

        video_stream, used_qn = pick_video_stream(
            dash, quality=qn_int, codecid=codec_int
        )
        audio_stream = pick_audio_stream(dash, audio_quality=audio_int)
        v_urls = candidate_urls(stream_urls(video_stream))
        if not v_urls:
            raise RuntimeError("DASH video entry has no URL")
        a_urls = (
            candidate_urls(stream_urls(audio_stream)) if audio_stream else []
        )

        work = dest.parent / f".udl-bili-{file_id.replace(':', '_')}"
        work.mkdir(parents=True, exist_ok=True)
        video_part = work / f"video_q{used_qn}.m4s"
        audio_part = work / "audio.m4s" if a_urls else None
        chapters_path: Path | None = None
        embed_subs: list[tuple[Path, str | None]] = []
        media_headers = stream_headers()

        try:
            self._download_with_fallbacks(
                v_urls,
                video_part,
                file_id=f"{file_id}:v",
                name=video_part.name,
                opts=opts,
                job_id=job_id,
                emit=emit,
                headers=media_headers,
            )
            if audio_part is not None and a_urls:
                self._download_with_fallbacks(
                    a_urls,
                    audio_part,
                    file_id=f"{file_id}:a",
                    name=audio_part.name,
                    opts=opts,
                    job_id=job_id,
                    emit=emit,
                    headers=media_headers,
                )
            else:
                audio_part = None

            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")

            # chapters + subtitle fetch for embed
            need_player = (
                self.options.write_chapters
                or self.options.subtitle_format
                or self.options.embed_subtitle
            )
            if need_player:
                try:
                    player = cli.player_v2(
                        cid=cid,
                        bvid=bvid or None,
                        aid=int(meta["aid"]) if meta.get("aid") else None,
                    )
                except bilibili_api_error:
                    player = {}
                if self.options.write_chapters:
                    points = view_points_from_player(player)
                    ff = to_ffmetadata(
                        points, duration_sec=_float_or_none(meta.get("duration"))
                    )
                    if ff:
                        chapters_path = work / "chapters.txt"
                        write_ffmetadata(ff, chapters_path)
                if self.options.embed_subtitle and self.options.subtitle_format == "ass":
                    tracks = filter_tracks(
                        tracks_from_player(player),
                        languages=self.options.subtitle_languages or None,
                    )
                    for track in tracks:
                        try:
                            raw = cli.get_json_url(track.url)
                            sub_path = work / f"sub_{safe_filename(track.language)}.ass"
                            write_subtitle(
                                raw,
                                sub_path,
                                fmt="ass",
                                title=track.language_doc,
                                style=self.options.ass_style,
                            )
                            embed_subs.append(
                                (sub_path, to_iso_639_2(track.language))
                            )
                        except bilibili_api_error:
                            continue

            if emit:
                emit.merging(
                    job=job_id,
                    file=file_id,
                    path=str(dest),
                    message="merging DASH video+audio with FFmpeg",
                )
            try:
                merge_dash(
                    video_part,
                    audio_part,
                    dest,
                    ffmpeg=self.ffmpeg,
                    chapters_path=chapters_path,
                    subtitle_paths=embed_subs or None,
                )
            except ffmpeg_missing_error:
                raise
            except merge_error:
                raise

            self._write_extras(
                cli,
                meta,
                dest=dest,
                emit=emit,
                job_id=job_id,
                file_id=file_id,
                player_hint=None,
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)

        return dest

    def _download_audio(
        self,
        cli: bilibili_client,
        meta: dict[str, Any],
        *,
        dest: Path,
        file_id: str,
        job_id: str,
        opts: download_options,
        emit: emitter | None,
    ) -> Path:
        sid = int(meta.get("audio_id") or 0)
        if not sid:
            raise RuntimeError("missing audio_id")
        play = cli.audio_playurl(sid)
        cdns = play.get("cdns") or play.get("cdn") or []
        if isinstance(cdns, str):
            cdns = [cdns]
        if not cdns:
            raise RuntimeError("audio playurl has no cdns")
        if dest.suffix.lower() not in {".m4a", ".mp3", ".flac"}:
            dest = dest.with_suffix(".m4a")
        last: Exception | None = None
        headers = stream_headers()
        for url in candidate_urls([str(u) for u in cdns if u]):
            try:
                item = download_item(
                    url=str(url),
                    dest=dest,
                    file_id=file_id,
                    name=dest.name,
                    headers=headers,
                )
                path = download_file(item, opts, job_id=job_id, emit=emit)
                host_health.report_success(get_netloc(str(url)))
                return path
            except InterruptedError:
                raise
            except Exception as exc:  # noqa: BLE001
                host_health.report_failure(get_netloc(str(url)))
                last = exc
        raise RuntimeError(f"audio download failed: {last}")

    def _write_extras(
        self,
        cli: bilibili_client,
        meta: dict[str, Any],
        *,
        dest: Path,
        emit: emitter | None,
        job_id: str,
        file_id: str,
        player_hint: dict[str, Any] | None = None,
    ) -> None:
        cid = int(meta.get("cid") or 0)
        bvid = str(meta.get("bvid") or "")
        stem = dest.with_suffix("")

        if self.options.danmaku_format and cid:
            try:
                aid = int(meta["aid"]) if meta.get("aid") is not None else None
                duration = meta.get("duration")
                entries = cli.fetch_danmaku_entries(
                    cid, aid=aid, duration=duration
                )
                path = write_danmaku(
                    entries,
                    Path(f"{stem}.danmaku"),
                    fmt=self.options.danmaku_format,
                    cid=cid,
                    title=str(meta.get("part_title") or dest.stem),
                    style=self.options.ass_style,
                )
                if emit:
                    emit.file(
                        job=job_id,
                        id=f"{file_id}:danmaku",
                        name=path.name,
                        path=str(path),
                        role="danmaku",
                    )
                if (
                    self.options.embed_danmaku
                    and self.options.danmaku_format == "ass"
                    and dest.suffix.lower() in {".mkv", ".mp4"}
                ):
                    # re-mux with danmaku as subtitle track
                    try:
                        tmp = dest.with_suffix(dest.suffix + ".tmp")
                        merge_dash(
                            dest,
                            None,
                            tmp,
                            ffmpeg=self.ffmpeg,
                            subtitle_paths=[(path, "chi")],
                        )
                        tmp.replace(dest)
                    except merge_error:
                        pass
            except bilibili_api_error:
                pass

        if self.options.subtitle_format and cid and not self.options.embed_subtitle:
            try:
                player = player_hint or cli.player_v2(
                    cid=cid,
                    bvid=bvid or None,
                    aid=int(meta["aid"]) if meta.get("aid") else None,
                )
                tracks = filter_tracks(
                    tracks_from_player(player),
                    languages=self.options.subtitle_languages or None,
                )
                for track in tracks:
                    raw = cli.get_json_url(track.url)
                    path = write_subtitle(
                        raw,
                        Path(f"{stem}.{safe_filename(track.language)}"),
                        fmt=self.options.subtitle_format,
                        title=track.language_doc,
                        style=self.options.ass_style,
                    )
                    if emit:
                        emit.file(
                            job=job_id,
                            id=f"{file_id}:sub:{track.language}",
                            name=path.name,
                            path=str(path),
                            role="subtitle",
                        )
            except bilibili_api_error:
                pass

    def _download_with_fallbacks(
        self,
        urls: list[str],
        dest: Path,
        *,
        file_id: str,
        name: str,
        opts: download_options,
        job_id: str,
        emit: emitter | None,
        headers: dict[str, str] | None = None,
    ) -> None:
        last: Exception | None = None
        hdrs = headers or stream_headers()
        for url in urls:
            try:
                item = download_item(
                    url=url,
                    dest=dest,
                    file_id=file_id,
                    name=name,
                    headers=hdrs,
                )
                download_file(item, opts, job_id=job_id, emit=emit)
                host_health.report_success(get_netloc(url))
                return
            except InterruptedError:
                raise
            except Exception as exc:  # noqa: BLE001
                host_health.report_failure(get_netloc(url))
                last = exc
                if dest.exists():
                    dest.unlink(missing_ok=True)
                part = dest.with_name(dest.name + ".part")
                if part.exists():
                    part.unlink(missing_ok=True)
        raise RuntimeError(f"all CDN candidates failed for {name}: {last}")

    def _resolve_target(self, url: str) -> tuple[url_target, str]:
        cli = self._cli()
        current = url.strip()
        target = classify_url(current)
        if target.kind == "b23":
            current = cli.resolve_short_link(current)
            target = classify_url(current)
        if target.kind in public_kinds or target.kind in login_kinds:
            if target.kind == "b23":
                raise RuntimeError(f"could not classify after short-link: {current}")
            return target, current
        if target.kind == "unsupported":
            raise RuntimeError(f"unsupported bilibili URL: {url}")
        raise RuntimeError(f"could not classify bilibili URL after redirect: {current}")

    def _expand_probe(
        self,
        cli: bilibili_client,
        probe: probe_info,
        target: url_target,
    ) -> probe_info:
        """paginate lists and expand nested folders/seasons/collections."""
        kind = probe.kind
        self._progress("expand", 0, 1, f"expanding {kind}")
        if kind == "space":
            parts = self._expand_space_pages(cli, target)
        elif kind == "favlist":
            parts = self._expand_favlist_pages(cli, int(target.fid or 0), target.keyword or "")
        elif kind == "collection":
            mid = int(probe.meta.get("mid") or target.mid or 0)
            season_id = int(probe.meta.get("season_id") or target.collection_id or 0)
            parts = self._expand_collection_pages(cli, mid=mid, season_id=season_id)
        elif kind == "series":
            mid = int(probe.meta.get("mid") or target.mid or 0)
            series_id = int(probe.meta.get("series_id") or target.series_id or 0)
            parts = self._expand_series_pages(cli, mid=mid, series_id=series_id)
        elif kind == "watchlater":
            parts = self._expand_watchlater_pages(cli, target.keyword or "")
        elif kind == "history":
            parts = self._expand_history_pages(cli, target.keyword or "")
        elif kind == "followed_bangumi":
            parts = self._expand_followed_bangumi(cli)
        elif kind == "favorite_folders":
            parts = self._expand_favorite_folders(cli, target.keyword or "")
        elif kind == "subscribed_collections":
            parts = self._expand_subscribed_collections(cli)
        else:
            return probe

        # renumber pages then apply existing filters
        for i, p in enumerate(parts, start=1):
            p.page = i
        filtered = apply_filter(
            [_part_dict(p) for p in parts],
            self.options.select,
            checked_pages=set(self.options.checked_pages or []) or None,
        )
        parts = [_dict_to_part(p) for p in filtered]
        for i, p in enumerate(parts, start=1):
            p.page = i
        self._progress("expand", 1, 1, f"expanded to {len(parts)} items")
        probe.parts = [_part_dict(p) for p in parts]
        probe.message = (probe.message or "") + f" — expanded {len(parts)} items"
        probe.pagination = {
            "total_pages": 1,
            "total_items": len(parts),
            "current_page": 1,
            "expanded": True,
        }
        meta = dict(probe.meta or {})
        meta["expanded"] = True
        probe.meta = meta
        return probe

    def _expand_space_pages(
        self, cli: bilibili_client, target: url_target
    ) -> list[part_info]:
        mid = int(target.mid or 0)
        keyword = target.keyword or ""
        parts: list[part_info] = []
        pn = 1
        total_pages = 1
        while pn <= total_pages:
            data = cli.space_videos(mid, pn=pn, keyword=keyword)
            page = data.get("page") or {}
            total = int(page.get("count") or 0)
            ps = int(page.get("ps") or 40) or 40
            total_pages = max(1, (total + ps - 1) // ps) if total else pn
            self._progress("space", pn, total_pages, f"UP space page {pn}/{total_pages}")
            vlist = ((data.get("list") or {}).get("vlist")) or []
            for v in vlist:
                parts.append(_vlist_part(len(parts) + 1, v))
            if not vlist:
                break
            pn += 1
        return parts

    def _expand_favlist_pages(
        self, cli: bilibili_client, fid: int, keyword: str
    ) -> list[part_info]:
        if not fid:
            return []
        parts: list[part_info] = []
        pn = 1
        total_pages = 1
        while pn <= total_pages:
            data = cli.favlist(fid, pn=pn, keyword=keyword)
            info = data.get("info") or {}
            total = int(info.get("media_count") or 0)
            total_pages = max(1, (total + 39) // 40) if total else pn
            self._progress(
                "favlist", pn, total_pages, f"favlist ml{fid} page {pn}/{total_pages}"
            )
            for m in data.get("medias") or []:
                if not isinstance(m, dict):
                    continue
                bvid = str(m.get("bvid") or "")
                if not bvid:
                    continue
                parts.append(
                    part_info(
                        page=len(parts) + 1,
                        cid=int(m.get("cid") or 0),
                        title=str(m.get("title") or bvid),
                        bvid=bvid,
                        aid=int(m["id"]) if m.get("id") is not None else None,
                        duration=_int_or_none(m.get("duration")),
                        upload_date=_pubdate_iso(m.get("fav_time") or m.get("ctime")),
                        needs_expand=not m.get("cid"),
                        kind="video",
                    )
                )
            if not (data.get("medias") or []):
                break
            pn += 1
        return parts

    def _expand_collection_pages(
        self, cli: bilibili_client, *, mid: int, season_id: int
    ) -> list[part_info]:
        if not mid or not season_id:
            return []
        parts: list[part_info] = []
        pn = 1
        total_pages = 1
        while pn <= total_pages:
            data = cli.collection_archives(mid=mid, season_id=season_id, pn=pn)
            page = data.get("page") or {}
            total = int(page.get("total") or 0)
            total_pages = max(1, (total + 29) // 30) if total else pn
            self._progress(
                "collection",
                pn,
                total_pages,
                f"collection {season_id} page {pn}/{total_pages}",
            )
            for a in data.get("archives") or []:
                if not isinstance(a, dict) or not a.get("bvid"):
                    continue
                parts.append(
                    part_info(
                        page=len(parts) + 1,
                        cid=0,
                        title=str(a.get("title") or a.get("bvid")),
                        bvid=str(a.get("bvid") or ""),
                        aid=int(a["aid"]) if a.get("aid") is not None else None,
                        duration=_int_or_none(a.get("duration")),
                        needs_expand=True,
                        kind="video",
                    )
                )
            if not (data.get("archives") or []):
                break
            pn += 1
        return parts

    def _expand_series_pages(
        self, cli: bilibili_client, *, mid: int, series_id: int
    ) -> list[part_info]:
        if not mid or not series_id:
            return []
        parts: list[part_info] = []
        pn = 1
        total_pages = 1
        while pn <= total_pages:
            data = cli.series_archives(mid=mid, series_id=series_id, pn=pn)
            page = data.get("page") or {}
            total = int(page.get("total") or 0)
            total_pages = max(1, (total + 29) // 30) if total else pn
            self._progress(
                "series", pn, total_pages, f"series {series_id} page {pn}/{total_pages}"
            )
            for a in data.get("archives") or []:
                if not isinstance(a, dict) or not a.get("bvid"):
                    continue
                parts.append(
                    part_info(
                        page=len(parts) + 1,
                        cid=0,
                        title=str(a.get("title") or a.get("bvid")),
                        bvid=str(a.get("bvid") or ""),
                        aid=int(a["aid"]) if a.get("aid") is not None else None,
                        duration=_int_or_none(a.get("duration")),
                        needs_expand=True,
                        kind="video",
                    )
                )
            if not (data.get("archives") or []):
                break
            pn += 1
        return parts

    def _expand_watchlater_pages(
        self, cli: bilibili_client, keyword: str
    ) -> list[part_info]:
        parts: list[part_info] = []
        pn = 1
        total_pages = 1
        while pn <= total_pages:
            data = cli.watchlater(pn=pn, keyword=keyword)
            total = int(data.get("count") or 0)
            total_pages = max(1, (total + 19) // 20) if total else pn
            self._progress("watchlater", pn, total_pages, f"watchlater {pn}/{total_pages}")
            for v in data.get("list") or []:
                if not isinstance(v, dict):
                    continue
                bvid = str(v.get("bvid") or "")
                if not bvid:
                    continue
                parts.append(
                    part_info(
                        page=len(parts) + 1,
                        cid=int(v.get("cid") or 0),
                        title=str(v.get("title") or bvid),
                        bvid=bvid,
                        aid=int(v["aid"]) if v.get("aid") is not None else None,
                        duration=_int_or_none(v.get("duration")),
                        needs_expand=not v.get("cid"),
                        kind="video",
                    )
                )
            if not (data.get("list") or []):
                break
            pn += 1
        return parts

    def _expand_history_pages(
        self, cli: bilibili_client, keyword: str
    ) -> list[part_info]:
        parts: list[part_info] = []
        pn = 1
        total_pages = 1
        while pn <= total_pages:
            data = cli.history(pn=pn, keyword=keyword)
            page = data.get("page") or {}
            total = int(page.get("total") or 0)
            ps = int(page.get("ps") or 20) or 20
            total_pages = max(1, (total + ps - 1) // ps) if total else pn
            self._progress("history", pn, total_pages, f"history {pn}/{total_pages}")
            for v in data.get("list") or []:
                if not isinstance(v, dict):
                    continue
                history = v.get("history") or {}
                bvid = str(history.get("bvid") or v.get("bvid") or "")
                if not bvid:
                    continue
                parts.append(
                    part_info(
                        page=len(parts) + 1,
                        cid=int(history.get("cid") or 0),
                        title=str(v.get("title") or bvid),
                        bvid=bvid,
                        aid=int(history["oid"]) if history.get("oid") is not None else None,
                        duration=_int_or_none(v.get("duration")),
                        needs_expand=not history.get("cid"),
                        kind="video",
                    )
                )
            if not (data.get("list") or []):
                break
            pn += 1
        return parts

    def _expand_followed_bangumi(self, cli: bilibili_client) -> list[part_info]:
        seasons: list[dict[str, Any]] = []
        pn = 1
        total_pages = 1
        while pn <= total_pages:
            data = cli.followed_bangumi(pn=pn)
            total = int(data.get("total") or 0)
            total_pages = max(1, (total + 23) // 24) if total else pn
            self._progress(
                "followed_bangumi",
                pn,
                total_pages,
                f"followed bangumi page {pn}/{total_pages}",
            )
            for entry in data.get("list") or []:
                if isinstance(entry, dict) and entry.get("season_id") is not None:
                    seasons.append(entry)
            if not (data.get("list") or []):
                break
            pn += 1

        parts: list[part_info] = []
        for i, entry in enumerate(seasons, start=1):
            season_id = int(entry["season_id"])
            self._progress(
                "followed_bangumi_expand",
                i,
                len(seasons),
                f"expand season ss{season_id}",
            )
            try:
                result = cli.bangumi_season(season_id=season_id)
            except bilibili_api_error:
                continue
            season_title = str(entry.get("title") or result.get("title") or f"ss{season_id}")
            episodes = list(result.get("episodes") or [])
            for section in result.get("section") or []:
                for ep in section.get("episodes") or []:
                    if ep.get("bvid") and ep.get("cid"):
                        episodes.append(ep)
            for ep in episodes:
                badge = str(ep.get("badge") or "").strip()
                bvid = ep.get("bvid")
                cid = ep.get("cid")
                if not bvid or not cid:
                    continue
                if badge in blocked_badges:
                    continue
                if badge in {"预告", "預告"}:
                    continue
                show = str(
                    ep.get("share_copy")
                    or ep.get("long_title")
                    or ep.get("title")
                    or bvid
                )
                parts.append(
                    part_info(
                        page=len(parts) + 1,
                        cid=int(cid),
                        title=f"{season_title} - {show}",
                        bvid=str(bvid),
                        aid=int(ep["aid"]) if ep.get("aid") is not None else None,
                        ep_id=int(ep["id"]) if ep.get("id") is not None else None,
                        badge=badge,
                        duration=int(ep["duration"] // 1000)
                        if isinstance(ep.get("duration"), int)
                        else None,
                        kind="bangumi",
                    )
                )
        return parts

    def _expand_favorite_folders(
        self, cli: bilibili_client, keyword: str
    ) -> list[part_info]:
        data = cli.favorite_folders()
        folders = [e for e in (data.get("list") or []) if isinstance(e, dict)]
        parts: list[part_info] = []
        for i, entry in enumerate(folders, start=1):
            fid = entry.get("id")
            if fid is None:
                continue
            title = str(entry.get("title") or f"ml{fid}")
            self._progress(
                "favorite_folders",
                i,
                len(folders),
                f"expand folder {title}",
            )
            folder_parts = self._expand_favlist_pages(cli, int(fid), keyword)
            for p in folder_parts:
                p.title = f"{title} - {p.title}"
                parts.append(p)
        return parts

    def _expand_subscribed_collections(self, cli: bilibili_client) -> list[part_info]:
        collections: list[dict[str, Any]] = []
        pn = 1
        total_pages = 1
        while pn <= total_pages:
            data = cli.subscribed_collections(pn=pn)
            total = int(data.get("count") or data.get("total") or 0)
            total_pages = max(1, (total + 49) // 50) if total else pn
            self._progress(
                "subscribed_collections",
                pn,
                total_pages,
                f"subscribed collections {pn}/{total_pages}",
            )
            for entry in data.get("list") or []:
                if isinstance(entry, dict):
                    collections.append(entry)
            if not (data.get("list") or []):
                break
            pn += 1

        parts: list[part_info] = []
        for i, entry in enumerate(collections, start=1):
            collection_id = entry.get("id")
            mid = entry.get("mid")
            title = str(entry.get("title") or collection_id or f"item{i}")
            if collection_id is None or mid is None:
                continue
            self._progress(
                "subscribed_collections_expand",
                i,
                len(collections),
                f"expand collection {title}",
            )
            nested = self._expand_collection_pages(
                cli, mid=int(mid), season_id=int(collection_id)
            )
            for p in nested:
                p.title = f"{title} - {p.title}"
                parts.append(p)
        return parts

    def _select_parts(self, probe: probe_info) -> list[part_info]:
        all_parts = [_dict_to_part(p) for p in probe.parts]
        checked = set(self.options.checked_pages or [])
        filtered = apply_filter(
            [_part_dict(p) for p in all_parts],
            self.options.select,
            checked_pages=checked or None,
        )
        if filtered:
            all_parts = [_dict_to_part(p) for p in filtered]
        elif self.options.select and not self.options.select.is_empty():
            return []

        selector = self.part
        if selector == "all":
            return all_parts

        def one(page: int) -> list[part_info]:
            for p in all_parts:
                if p.page == page:
                    return [p]
            raise ValueError(f"part {page} not found (1..{len(all_parts)})")

        if selector is None or selector == "":
            if probe.kind == "bangumi" or probe.kind == "cheese" or probe.meta.get(
                "url_pinned_part"
            ):
                return one(int(probe.default_part))
            if probe.kind in list_kinds:
                return all_parts
            return all_parts
        try:
            page = int(selector)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid --part value: {selector}") from exc
        return one(page)

    @staticmethod
    def _output_name(title: str, part: part_info, multi: bool, *, ext: str = ".mp4") -> str:
        if not ext.startswith("."):
            ext = f".{ext}"
        base = safe_filename(title)
        if multi:
            part_name = safe_filename(part.title, max_len=80)
            return f"P{part.page:02d}_{part_name}{ext}"
        return f"{base}{ext}"

    @staticmethod
    def _part_item_id(part: part_info, kind: str) -> str:
        if part.audio_id:
            return f"au{part.audio_id}"
        if part.bvid:
            return f"{part.bvid}/p{part.page}"
        if part.ep_id:
            return f"{kind}/ep{part.ep_id}"
        return f"{kind}/p{part.page}"

    @staticmethod
    def _part_url(part: part_info, kind: str) -> str:
        if part.audio_id:
            return f"https://www.bilibili.com/audio/au{part.audio_id}"
        if kind == "cheese" and part.ep_id:
            return f"https://www.bilibili.com/cheese/play/ep{part.ep_id}"
        if part.ep_id and kind == "bangumi":
            return f"https://www.bilibili.com/bangumi/play/ep{part.ep_id}"
        if part.bvid:
            return f"https://www.bilibili.com/video/{part.bvid}?p={part.page}"
        return "https://www.bilibili.com/"


def _part_dict(p: part_info) -> dict[str, Any]:
    return {
        "page": p.page,
        "cid": p.cid,
        "title": p.title,
        "bvid": p.bvid,
        "aid": p.aid,
        "ep_id": p.ep_id,
        "badge": p.badge,
        "duration": p.duration,
        "upload_date": p.upload_date,
        "edge_id": p.edge_id,
        "needs_expand": p.needs_expand,
        "kind": p.kind,
        "audio_id": p.audio_id,
    }


def _dict_to_part(p: dict[str, Any]) -> part_info:
    return part_info(
        page=int(p["page"]),
        cid=int(p.get("cid") or 0),
        title=str(p["title"]),
        bvid=str(p.get("bvid") or ""),
        aid=int(p["aid"]) if p.get("aid") is not None else None,
        ep_id=int(p["ep_id"]) if p.get("ep_id") is not None else None,
        badge=str(p.get("badge") or ""),
        duration=int(p["duration"]) if p.get("duration") is not None else None,
        upload_date=_str_or_none(p.get("upload_date")),
        edge_id=int(p["edge_id"]) if p.get("edge_id") is not None else None,
        needs_expand=bool(p.get("needs_expand")),
        kind=str(p.get("kind") or "video"),
        audio_id=int(p["audio_id"]) if p.get("audio_id") is not None else None,
    )


def _vlist_part(index: int, v: Any) -> part_info:
    if not isinstance(v, dict):
        return part_info(page=index, cid=0, title="?", bvid="", needs_expand=True)
    length = v.get("length")
    duration = None
    if isinstance(length, str) and ":" in length:
        try:
            mm, ss = length.split(":", 1)
            duration = int(mm) * 60 + int(ss)
        except ValueError:
            duration = None
    elif isinstance(length, (int, float)):
        duration = int(length)
    return part_info(
        page=index,
        cid=0,
        title=str(v.get("title") or v.get("bvid") or f"P{index}"),
        bvid=str(v.get("bvid") or ""),
        aid=int(v["aid"]) if v.get("aid") is not None else None,
        duration=duration,
        upload_date=_pubdate_iso(v.get("created")),
        needs_expand=True,
        kind="video",
    )


def _extract_tags(data: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for tag in data.get("tag") or []:
        if isinstance(tag, dict) and tag.get("tag_name"):
            tags.append(str(tag["tag_name"]))
        elif isinstance(tag, str):
            tags.append(tag)
    return tags


def _pubdate_iso(value: Any) -> str | None:
    if isinstance(value, int):
        try:
            return datetime.fromtimestamp(value, tz=UTC).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    return _str_or_none(value)


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, requires_login):
        return exc.code
    if isinstance(exc, ffmpeg_missing_error):
        return exc.code
    if isinstance(exc, merge_error):
        return exc.code
    if isinstance(exc, bilibili_api_error):
        if exc.code == -10403:
            return "region_locked"
        if exc.code == 87008:
            return "paywalled"
        if exc.code is not None:
            return f"api_{exc.code}"
        return "resolve_failed"
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return "download_failed"


def matches(url: str) -> bool:
    text = url if "://" in url else f"https://{url}"
    if bilibili_extractor.suitable(text):
        return True
    host = urlparse(text).netloc.lower()
    if (
        "bilibili.com" in host
        or "b23.tv" in host
        or "bili2233.cn" in host
        or host.endswith("biliapi.net")
    ):
        return True
    target = classify_url(url if "://" in url else f"https://www.bilibili.com/{url}")
    return target.kind != "unsupported"
