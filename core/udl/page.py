"""scan a web page for downloadable candidate links (Download All).

Fetches HTML via the shared HTTP engine (proxy / UA) and lists:
  - anchor href files by extension
  - video / audio / source media
  - optional img sources
  - URLs matched by existing site extractors

Only unencrypted direct media and supported sites — no DRM or encrypted HLS.
"""

from __future__ import annotations

import fnmatch
import mimetypes
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from requests import Session

from udl.network import active_network, apply_to_session, default_user_agent
from udl.router import match as match_site
from udl.router import normalize_url

# lowercase constants per project convention
default_timeout = 30.0
default_file_extensions = frozenset(
    {
        "7z",
        "aac",
        "avi",
        "bin",
        "bz2",
        "csv",
        "doc",
        "docx",
        "epub",
        "flac",
        "flv",
        "gif",
        "gz",
        "iso",
        "jpeg",
        "jpg",
        "json",
        "m4a",
        "m4v",
        "mkv",
        "mov",
        "mp3",
        "mp4",
        "mpeg",
        "mpg",
        "ogg",
        "ogv",
        "opus",
        "pdf",
        "png",
        "rar",
        "srt",
        "tar",
        "tgz",
        "ts",
        "txt",
        "vtt",
        "wav",
        "webm",
        "webp",
        "wma",
        "wmv",
        "xls",
        "xlsx",
        "xz",
        "zip",
    }
)
video_extensions = frozenset(
    {"mp4", "mkv", "webm", "avi", "mov", "flv", "m4v", "mpeg", "mpg", "wmv", "ts", "ogv"}
)
audio_extensions = frozenset({"mp3", "m4a", "flac", "aac", "ogg", "opus", "wav", "wma"})
image_extensions = frozenset({"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "avif"})
archive_extensions = frozenset({"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "iso"})
skip_schemes = frozenset({"javascript", "mailto", "data", "blob", "about", "tel"})
skip_extensions = frozenset({"html", "htm", "php", "asp", "aspx", "jsp", "css", "js", "map"})


@dataclass
class page_filter:
    """filters applied after candidate discovery."""

    extensions: set[str] | None = None
    types: set[str] | None = None
    size_min: int | None = None
    size_max: int | None = None
    regex: str | None = None
    glob: str | None = None
    same_domain: bool = False
    include_images: bool = False
    head_sizes: bool = True
    head_workers: int = 8


@dataclass
class page_candidate:
    """one downloadable or site-matched URL found on a page."""

    url: str
    name: str
    source: str
    kind: str
    extension: str | None = None
    size: int | None = None
    mime: str | None = None
    site: str | None = None
    site_label: str | None = None
    text: str | None = None
    selected: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "name": self.name,
            "source": self.source,
            "kind": self.kind,
            "extension": self.extension,
            "size": self.size,
            "mime": self.mime,
            "site": self.site,
            "site_label": self.site_label,
            "text": self.text,
            "selected": self.selected,
        }


@dataclass
class page_scan_result:
    """result of scanning a page URL for candidates."""

    page_url: str
    title: str | None = None
    candidates: list[page_candidate] = field(default_factory=list)
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ev": "page",
            "url": self.page_url,
            "title": self.title,
            "count": len(self.candidates),
            "candidates": [c.to_dict() for c in self.candidates],
            "message": self.message,
        }


def _make_session() -> Session:
    session = Session()
    net = active_network()
    apply_to_session(session, net)
    if not session.headers.get("User-Agent"):
        session.headers["User-Agent"] = net.user_agent or default_user_agent
    return session


def _basename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1] if path else ""
    if not name or name in {".", ".."}:
        return "download"
    return name


def _extension_of(url: str, name: str | None = None) -> str | None:
    target = name or _basename_from_url(url)
    if "." not in target:
        path = urlparse(url).path
        if "." in path:
            target = path.rsplit("/", 1)[-1]
    if "." not in target:
        return None
    ext = target.rsplit(".", 1)[-1].lower()
    if not ext or len(ext) > 8 or "/" in ext:
        return None
    return ext


def _guess_kind(ext: str | None, source: str, site: str | None) -> str:
    if site:
        return "site"
    if source == "img":
        return "image"
    if source in {"video", "audio", "source"}:
        if ext in audio_extensions or source == "audio":
            return "audio"
        return "video"
    if ext in video_extensions:
        return "video"
    if ext in audio_extensions:
        return "audio"
    if ext in image_extensions:
        return "image"
    if ext in archive_extensions:
        return "archive"
    if ext in {"pdf", "txt", "csv", "doc", "docx", "xls", "xlsx", "epub", "json", "srt", "vtt"}:
        return "document"
    return "file"


def _clean_url(base: str, raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip()
    if not text or text.startswith("#"):
        return None
    lower = text.lower()
    for scheme in skip_schemes:
        if lower.startswith(f"{scheme}:"):
            return None
    absolute = urljoin(base, text)
    try:
        absolute = normalize_url(absolute)
    except ValueError:
        return None
    parsed = urlparse(absolute)
    # drop fragment
    absolute = urlunparse(parsed._replace(fragment=""))
    return absolute


def _host_key(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        return host[4:]
    return host


def _looks_like_file(url: str, ext: str | None) -> bool:
    if ext and ext in skip_extensions:
        return False
    if ext and ext in default_file_extensions:
        return True
    path = urlparse(url).path.lower()
    if any(path.endswith(f".{e}") for e in default_file_extensions):
        return True
    return False


def _collect_raw(html: str, page_url: str, *, include_images: bool) -> list[page_candidate]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[page_candidate] = []

    def add(
        url: str | None,
        *,
        source: str,
        text: str | None = None,
        force: bool = False,
    ) -> None:
        if not url or url in seen:
            return
        site_entry = match_site(url)
        site = site_entry.site if site_entry else None
        site_label = site_entry.label if site_entry else None
        name = _basename_from_url(url)
        ext = _extension_of(url, name)
        if site:
            kind = "site"
        elif not force and not _looks_like_file(url, ext):
            return
        else:
            kind = _guess_kind(ext, source, None)
        seen.add(url)
        out.append(
            page_candidate(
                url=url,
                name=name,
                source=source,
                kind=kind,
                extension=ext,
                site=site,
                site_label=site_label,
                text=text,
            )
        )

    for tag in soup.find_all("a", href=True):
        href = _clean_url(page_url, tag.get("href"))
        label = tag.get_text(" ", strip=True) or None
        add(href, source="a", text=label)

    for tag_name in ("video", "audio"):
        for tag in soup.find_all(tag_name):
            src = _clean_url(page_url, tag.get("src"))
            add(src, source=tag_name, force=True)
            for source in tag.find_all("source"):
                src = _clean_url(page_url, source.get("src"))
                add(src, source="source", force=True)

    for tag in soup.find_all("source"):
        if tag.parent and tag.parent.name in {"video", "audio"}:
            continue
        src = _clean_url(page_url, tag.get("src"))
        add(src, source="source", force=True)

    if include_images:
        for tag in soup.find_all("img"):
            src = _clean_url(page_url, tag.get("src") or tag.get("data-src"))
            add(src, source="img", force=True, text=tag.get("alt"))

    return out


def _match_filters(candidate: page_candidate, filt: page_filter, page_host: str) -> bool:
    if filt.same_domain and _host_key(candidate.url) != page_host:
        return False
    if filt.extensions is not None:
        ext = (candidate.extension or "").lower()
        if candidate.kind != "site" and ext not in filt.extensions:
            return False
    if filt.types is not None:
        if candidate.kind not in filt.types and not (
            candidate.kind == "site" and "site" in filt.types
        ):
            return False
    haystack = f"{candidate.url} {candidate.name} {candidate.text or ''}"
    if filt.regex:
        try:
            if not re.search(filt.regex, haystack, re.I):
                return False
        except re.error:
            return False
    if filt.glob:
        patterns = [p.strip() for p in filt.glob.split("|") if p.strip()]
        if patterns and not any(
            fnmatch.fnmatch(candidate.name, p) or fnmatch.fnmatch(candidate.url, p)
            for p in patterns
        ):
            return False
    if filt.size_min is not None and candidate.size is not None:
        if candidate.size < filt.size_min:
            return False
    if filt.size_max is not None and candidate.size is not None:
        if candidate.size > filt.size_max:
            return False
    return True


def _head_size(session: Session, url: str, timeout: float) -> tuple[int | None, str | None]:
    try:
        resp = session.head(url, allow_redirects=True, timeout=timeout)
        if resp.status_code >= 400:
            return None, None
        length = resp.headers.get("Content-Length")
        size = int(length) if length and length.isdigit() else None
        mime = resp.headers.get("Content-Type")
        if mime and ";" in mime:
            mime = mime.split(";", 1)[0].strip()
        return size, mime
    except Exception:  # noqa: BLE001
        return None, None


def _fill_sizes(
    session: Session,
    candidates: list[page_candidate],
    *,
    timeout: float,
    workers: int,
) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    pending = [c for c in candidates if c.kind != "site"]
    if not pending:
        return
    workers = max(1, min(workers, len(pending)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_head_size, session, c.url, timeout): c for c in pending
        }
        for fut in as_completed(futures):
            candidate = futures[fut]
            try:
                size, mime = fut.result()
            except Exception:  # noqa: BLE001
                continue
            if size is not None:
                candidate.size = size
            if mime:
                candidate.mime = mime
                if not candidate.extension:
                    guessed = mimetypes.guess_extension(mime)
                    if guessed:
                        candidate.extension = guessed.lstrip(".").lower()


def parse_extensions(raw: str | None) -> set[str] | None:
    if raw is None or not str(raw).strip():
        return None
    parts = {p.strip().lower().lstrip(".") for p in str(raw).split(",") if p.strip()}
    return parts or None


def parse_types(raw: str | None) -> set[str] | None:
    if raw is None or not str(raw).strip():
        return None
    allowed = {
        "video",
        "audio",
        "image",
        "archive",
        "document",
        "file",
        "site",
    }
    parts = {p.strip().lower() for p in str(raw).split(",") if p.strip()}
    return {p for p in parts if p in allowed} or None


def parse_size_bound(raw: str | int | None) -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    text = str(raw).strip().lower()
    if not text:
        return None
    mult = 1
    if text[-1] in "kmgt":
        mult = {"k": 1000, "m": 1000**2, "g": 1000**3, "t": 1000**4}[text[-1]]
        text = text[:-1].strip()
    elif text.endswith("kb"):
        mult = 1000
        text = text[:-2].strip()
    elif text.endswith("mb"):
        mult = 1000**2
        text = text[:-2].strip()
    elif text.endswith("gb"):
        mult = 1000**3
        text = text[:-2].strip()
    try:
        return int(float(text) * mult)
    except ValueError as exc:
        raise ValueError(f"invalid size: {raw}") from exc


def scan_page(
    url: str,
    *,
    filters: page_filter | None = None,
    timeout: float = default_timeout,
    session: Session | None = None,
) -> page_scan_result:
    """fetch page HTML and return filtered candidate links."""
    page_url = normalize_url(url)
    filt = filters or page_filter()
    own_session = session is None
    sess = session or _make_session()
    try:
        resp = sess.get(page_url, timeout=timeout)
        resp.raise_for_status()
        # prefer declared encoding then apparent
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else None
        candidates = _collect_raw(html, page_url, include_images=filt.include_images)
        if filt.head_sizes:
            _fill_sizes(
                sess,
                candidates,
                timeout=timeout,
                workers=filt.head_workers,
            )
        page_host = _host_key(page_url)
        filtered = [c for c in candidates if _match_filters(c, filt, page_host)]
        return page_scan_result(page_url=page_url, title=title, candidates=filtered)
    except Exception as exc:  # noqa: BLE001
        return page_scan_result(
            page_url=page_url,
            title=None,
            candidates=[],
            message=str(exc),
        )
    finally:
        if own_session:
            sess.close()
