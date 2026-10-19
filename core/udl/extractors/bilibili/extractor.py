"""Bilibili extractor — video / b23 / bangumi subset.

Ported / adapted from ScottSloan/Bili23-Downloader (GPL-3.0).
Decoupled from Qt. No paywall, VIP-only, or region-lock bypass.
QR-code login is intentionally stubbed (see login_stub note in meta).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urlparse

from udl.download.http import download_file, download_item, download_options
from udl.events import emitter
from udl.extractors.base import extract_result, extracted_file, extractor
from udl.extractors.bilibili.client import bilibili_api_error, bilibili_client
from udl.extractors.bilibili.ids import classify_url, safe_filename, url_target
from udl.extractors.bilibili.quality import (
    available_qualities,
    pick_audio_stream,
    pick_video_stream,
    stream_urls,
)
from udl.metadata import item_metadata
from udl.postprocess.ffmpeg import ffmpeg_missing_error, find_ffmpeg, merge_dash, merge_error

# vip / paid badges we refuse without attempting a bypass
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

login_stub_note = (
    "QR-code / cookie login is designed but not required for v0.3; "
    "anonymous best-available quality is used. Credentials would go to OS keychain."
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

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "site": self.site,
            "kind": self.kind,
            "title": self.title,
            "url": self.url,
            "parts": self.parts,
            "qualities": self.qualities,
            "default_part": self.default_part,
            "default_quality": self.default_quality,
            "message": self.message,
            "meta": self.meta,
        }
        metadata = self.meta.get("metadata") if isinstance(self.meta, dict) else None
        if metadata is not None:
            payload["metadata"] = metadata
        return payload


class bilibili_extractor(extractor):
    site = "bilibili"
    label = "Bilibili"

    def __init__(
        self,
        *,
        quality: int | None = None,
        part: int | str | None = None,
        ffmpeg: str | Path | None = None,
        client: bilibili_client | None = None,
    ) -> None:
        self.quality = quality
        # part: None/"all" → all parts; int → single page/ep index (1-based)
        self.part = part
        self.ffmpeg = ffmpeg
        self._client = client
        self._own_client = client is None

    def _cli(self) -> bilibili_client:
        if self._client is None:
            self._client = bilibili_client()
            self._own_client = True
        return self._client

    def close(self) -> None:
        if self._own_client and self._client is not None:
            self._client.close()
            self._client = None

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

        multi = len(probe.parts) > 1
        files: list[extracted_file] = []
        job_meta = self._job_metadata(probe)
        for part in parts:
            part_id = f"{part.bvid}/p{part.page}"
            part_meta = item_metadata(
                site=self.site,
                item_id=part_id,
                canonical_url=f"https://www.bilibili.com/video/{part.bvid}?p={part.page}",
                title=part.title or probe.title,
                author=job_meta.author,
                upload_date=job_meta.upload_date,
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
                collection=probe.title if multi else None,
                collection_id=part.bvid if multi else None,
                extra={
                    "bvid": part.bvid,
                    "cid": part.cid,
                    "page": part.page,
                    "aid": part.aid,
                    "ep_id": part.ep_id,
                    "kind": probe.kind,
                },
            )
            files.append(
                extracted_file(
                    url=f"bilibili://{part.bvid}/p{part.page}/cid{part.cid}",
                    name=self._output_name(probe.title, part, multi),
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
                        "kind": probe.kind,
                        "quality": self.quality or probe.default_quality,
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
                "default_part": probe.default_part,
                "default_quality": probe.default_quality,
                "login_stub": login_stub_note,
                "source_url": probe.url,
            },
            metadata=job_meta,
        )

    def probe(self, url: str) -> probe_info:
        """resolve metadata + available qualities for the default / selected part."""
        cli = self._cli()
        cli.bootstrap()
        target, final_url = self._resolve_target(url)
        if target.kind == "video":
            return self._probe_video(cli, target, final_url)
        if target.kind == "bangumi":
            return self._probe_bangumi(cli, target, final_url)
        raise RuntimeError(f"unsupported bilibili URL kind: {target.kind}")

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
        del workers  # sequential per part; DASH uses two HTTP fetches each
        job = job_id or result.site
        # fail fast before any network download if FFmpeg is missing
        find_ffmpeg(self.ffmpeg)

        cli = self._cli()
        opts = download_options(
            output_dir=output_dir,
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
            extra={"kind": probe.kind, "login_stub": login_stub_note},
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

        meta = f.meta
        bvid = str(meta["bvid"])
        cid = int(meta["cid"])
        kind = str(meta.get("kind") or "video")
        qn = meta.get("quality")
        qn_int = int(qn) if qn is not None else None

        if kind == "bangumi":
            play = cli.playurl_bangumi(bvid=bvid, cid=cid, qn=qn_int or 80)
        else:
            play = cli.playurl_video(bvid=bvid, cid=cid, qn=127)

        dash = play.get("dash")
        if not isinstance(dash, dict):
            # MP4 durl fallback (rare for modern uploads)
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
                headers={"Referer": "https://www.bilibili.com/"},
            )
            return download_file(item, opts, job_id=job_id, emit=emit)

        video_stream, used_qn = pick_video_stream(dash, quality=qn_int)
        audio_stream = pick_audio_stream(dash)
        v_urls = stream_urls(video_stream)
        if not v_urls:
            raise RuntimeError("DASH video entry has no URL")
        a_urls = stream_urls(audio_stream) if audio_stream else []

        work = dest.parent / f".udl-bili-{file_id.replace(':', '_')}"
        work.mkdir(parents=True, exist_ok=True)
        video_part = work / f"video_q{used_qn}.m4s"
        audio_part = work / "audio.m4s" if a_urls else None

        try:
            self._download_with_fallbacks(
                v_urls,
                video_part,
                file_id=f"{file_id}:v",
                name=video_part.name,
                opts=opts,
                job_id=job_id,
                emit=emit,
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
                )
            else:
                audio_part = None

            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")

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
                )
            except ffmpeg_missing_error:
                raise
            except merge_error:
                raise
        finally:
            shutil.rmtree(work, ignore_errors=True)

        return dest

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
    ) -> None:
        last: Exception | None = None
        for url in urls:
            try:
                item = download_item(
                    url=url,
                    dest=dest,
                    file_id=file_id,
                    name=name,
                    headers={"Referer": "https://www.bilibili.com/"},
                )
                download_file(item, opts, job_id=job_id, emit=emit)
                return
            except InterruptedError:
                raise
            except Exception as exc:  # noqa: BLE001
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
        if target.kind in {"video", "bangumi"}:
            return target, current
        if target.kind == "unsupported":
            raise RuntimeError(f"unsupported bilibili URL: {url}")
        raise RuntimeError(f"could not classify bilibili URL after redirect: {current}")

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
                code=None,
            )
        redirect = data.get("redirect_url")
        if redirect:
            # UGC that redirects to bangumi
            return self.probe(str(redirect))

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
                        duration=int(p["duration"]) if p.get("duration") is not None else None,
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
                    duration=int(data["duration"]) if data.get("duration") is not None else None,
                )
            )

        pinned = target.page or data.get("current_page_number")
        default_part = int(pinned) if pinned is not None else 1
        if default_part < 1 or default_part > len(parts):
            default_part = 1

        sample = parts[default_part - 1]
        play = cli.playurl_video(bvid=sample.bvid, cid=sample.cid, qn=127)
        dash = play.get("dash") or {}
        qualities = available_qualities(dash) if isinstance(dash, dict) else []
        default_quality = qualities[0]["qn"] if qualities else None

        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        stat = data.get("stat") if isinstance(data.get("stat"), dict) else {}
        tags = []
        for tag in data.get("tag") or []:
            if isinstance(tag, dict) and tag.get("tag_name"):
                tags.append(str(tag["tag_name"]))
            elif isinstance(tag, str):
                tags.append(tag)
        pubdate = data.get("pubdate")
        upload_date = None
        if isinstance(pubdate, int):
            upload_date = datetime.fromtimestamp(pubdate, tz=UTC).date().isoformat()
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
            extra={
                "aid": data.get("aid"),
                "bvid": bvid,
                "owner_mid": owner.get("mid"),
            },
        )
        return probe_info(
            site=self.site,
            kind="video",
            title=title,
            url=final_url,
            parts=[
                {
                    "page": p.page,
                    "cid": p.cid,
                    "title": p.title,
                    "bvid": p.bvid,
                    "aid": p.aid,
                    "duration": p.duration,
                }
                for p in parts
            ],
            qualities=qualities,
            default_part=default_part,
            default_quality=default_quality,
            message=None,
            meta={
                "login_stub": login_stub_note,
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
        # include section extras that have bvid+cid
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
            if badge == "预告" or badge == "預告":
                continue
            show = (
                str(ep.get("share_copy") or ep.get("long_title") or ep.get("title") or f"P{i}")
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
        default_quality: int | None = None
        message: str | None = None
        try:
            play = cli.playurl_bangumi(bvid=sample.bvid, cid=sample.cid, qn=80)
            dash = play.get("dash") or {}
            if isinstance(dash, dict):
                qualities = available_qualities(dash)
                default_quality = qualities[0]["qn"] if qualities else None
        except bilibili_api_error as exc:
            # keep part list so UI can show titles; download will fail clearly
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
            },
        )
        return probe_info(
            site=self.site,
            kind="bangumi",
            title=title,
            url=final_url,
            parts=[
                {
                    "page": p.page,
                    "cid": p.cid,
                    "title": p.title,
                    "bvid": p.bvid,
                    "aid": p.aid,
                    "ep_id": p.ep_id,
                    "badge": p.badge,
                    "duration": p.duration,
                }
                for p in parts
            ],
            qualities=qualities,
            default_part=default_part,
            default_quality=default_quality,
            message=message,
            meta={
                "login_stub": login_stub_note,
                "season_id": season_id,
                "url_pinned_part": target.ep_id is not None,
                "cover": cover,
                "cover_url": cover,
                "thumbnail_url": cover,
                "metadata": metadata.to_dict(omit_empty=True),
            },
        )

    def _select_parts(self, probe: probe_info) -> list[part_info]:
        all_parts = [
            part_info(
                page=int(p["page"]),
                cid=int(p["cid"]),
                title=str(p["title"]),
                bvid=str(p["bvid"]),
                aid=int(p["aid"]) if p.get("aid") is not None else None,
                ep_id=int(p["ep_id"]) if p.get("ep_id") is not None else None,
                badge=str(p.get("badge") or ""),
                duration=int(p["duration"]) if p.get("duration") is not None else None,
            )
            for p in probe.parts
        ]
        selector = self.part
        if selector == "all":
            return all_parts

        def one(page: int) -> list[part_info]:
            for p in all_parts:
                if p.page == page:
                    return [p]
            raise ValueError(f"part {page} not found (1..{len(all_parts)})")

        if selector is None or selector == "":
            # video multi-P without ?p= → all parts
            # bangumi season / pinned URL → single default part
            if probe.kind == "bangumi" or probe.meta.get("url_pinned_part"):
                return one(int(probe.default_part))
            return all_parts
        try:
            page = int(selector)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid --part value: {selector}") from exc
        return one(page)

    @staticmethod
    def _output_name(title: str, part: part_info, multi: bool) -> str:
        base = safe_filename(title)
        if multi:
            part_name = safe_filename(part.title, max_len=80)
            return f"P{part.page:02d}_{part_name}.mp4"
        return f"{base}.mp4"


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
    host = urlparse(url).netloc.lower()
    if (
        "bilibili.com" in host
        or "b23.tv" in host
        or "bili2233.cn" in host
        or host.endswith("biliapi.net")
    ):
        return True
    # bare BV / av / ep / ss paste
    target = classify_url(url if "://" in url else f"https://www.bilibili.com/{url}")
    return target.kind in {"video", "bangumi", "b23"}
