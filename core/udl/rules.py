"""ordered classification rules: match metadata then folder/template/skip.

Rule JSON is compatible with william563214/download_classify export shape
(``rules`` / ``site_rules`` / ``extension_rules``) so a future browser
extension can share the same files. Legacy udl match fields remain supported.
"""

from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from udl.metadata import item_metadata
from udl.naming import detect_media_type, sanitize_component

# download_classify layers: site → custom → extension
priority_site_default = 10
priority_custom_default = 50
priority_extension_base = 900


@dataclass
class rule_match:
    site: str | None = None
    sites: list[str] = field(default_factory=list)
    author: str | None = None
    author_regex: str | None = None
    tags_any: list[str] = field(default_factory=list)
    tags_all: list[str] = field(default_factory=list)
    title_regex: str | None = None
    duration_min: float | None = None
    duration_max: float | None = None
    size_min: int | None = None
    size_max: int | None = None
    media_type: str | None = None
    media_types: list[str] = field(default_factory=list)
    collection_regex: str | None = None
    # file type / download_classify matchers
    extensions: list[str] = field(default_factory=list)
    mime_types: list[str] = field(default_factory=list)
    filename: list[str] = field(default_factory=list)
    # source URL / host (download_classify site + source_page / source_site)
    hosts: list[str] = field(default_factory=list)
    source_hosts: list[str] = field(default_factory=list)
    source_page: list[str] = field(default_factory=list)
    url_patterns: list[str] = field(default_factory=list)
    # site_rules match_target: source | download | either
    match_target: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> rule_match:
        if not data:
            return cls()
        sites = _as_str_list(data.get("sites"))
        tags_any = _as_str_list(data.get("tags_any") or data.get("tags"))
        tags_all = _as_str_list(data.get("tags_all"))
        media_types = _as_str_list(data.get("media_types"))
        extensions = _as_str_list(
            data.get("extensions") or data.get("extension") or data.get("ext")
        )
        extensions = [e.lstrip(".").lower() for e in extensions if e]
        mime_types = _as_str_list(data.get("mime_types") or data.get("mime"))
        filename = _as_str_list(data.get("filename") or data.get("filenames"))
        hosts = _as_str_list(
            data.get("hosts")
            or data.get("host")
            or data.get("download_site")
            or data.get("site_hosts")
        )
        source_hosts = _as_str_list(
            data.get("source_hosts")
            or data.get("source_host")
            or data.get("source_site")
            or data.get("source_sites")
        )
        source_page = _as_str_list(
            data.get("source_page") or data.get("source_pages") or data.get("page")
        )
        url_patterns = _as_str_list(
            data.get("url_patterns")
            or data.get("url_pattern")
            or data.get("urls")
            or data.get("url")
        )
        match_target = _str_or_none(data.get("match_target"))
        if match_target:
            match_target = match_target.lower()
            if match_target not in {"source", "download", "either"}:
                match_target = "either"
        return cls(
            site=_str_or_none(data.get("site")),
            sites=[str(s) for s in sites],
            author=_str_or_none(data.get("author")),
            author_regex=_str_or_none(data.get("author_regex")),
            tags_any=[str(t) for t in tags_any],
            tags_all=[str(t) for t in tags_all],
            title_regex=_str_or_none(data.get("title_regex")),
            duration_min=_float_or_none(data.get("duration_min")),
            duration_max=_float_or_none(data.get("duration_max")),
            size_min=_int_or_none(data.get("size_min")),
            size_max=_int_or_none(data.get("size_max")),
            media_type=_str_or_none(data.get("media_type")),
            media_types=[str(t) for t in media_types],
            collection_regex=_str_or_none(data.get("collection_regex")),
            extensions=extensions,
            mime_types=[str(m).lower() for m in mime_types],
            filename=[str(f) for f in filename],
            hosts=[str(h) for h in hosts],
            source_hosts=[str(h) for h in source_hosts],
            source_page=[str(p) for p in source_page],
            url_patterns=[str(u) for u in url_patterns],
            match_target=match_target,
        )

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], "")}


@dataclass
class rule_action:
    # folder | template | skip | folder_and_template
    type: str = "folder"
    folder: str | None = None
    template: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> rule_action:
        if not data:
            return cls()
        action_type = str(data.get("type") or data.get("action") or "folder").lower()
        if action_type in {"folder_template", "both"}:
            action_type = "folder_and_template"
        return cls(
            type=action_type,
            folder=_str_or_none(
                data.get("folder")
                or data.get("target_folder")
                or data.get("targetFolder")
            ),
            template=_str_or_none(data.get("template") or data.get("name_template")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.folder:
            out["folder"] = self.folder
        if self.template:
            out["template"] = self.template
        return out


@dataclass
class classify_rule:
    id: str
    name: str = ""
    enabled: bool = True
    priority: int = priority_custom_default
    kind: str = "custom"  # custom | site | extension
    match: rule_match = field(default_factory=rule_match)
    action: rule_action = field(default_factory=rule_action)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> classify_rule:
        data = data or {}
        rid = str(data.get("id") or uuid.uuid4())
        # download_classify ClassificationRule uses matchers + target_folder
        match_raw = data.get("match")
        if not isinstance(match_raw, dict):
            matchers = data.get("matchers")
            if isinstance(matchers, dict):
                match_raw = dict(matchers)
                # map download_classify matcher keys
                if "site" in matchers and "hosts" not in match_raw:
                    match_raw["hosts"] = matchers.get("site")
                if "source_site" in matchers:
                    match_raw["source_hosts"] = matchers.get("source_site")
                if "extension" in matchers:
                    match_raw["extensions"] = matchers.get("extension")
            elif "host" in data and not any(
                k in data for k in ("site", "sites", "author", "title_regex")
            ):
                # SiteClassificationRule-shaped object
                match_raw = {
                    "hosts": [data.get("host")],
                    "match_target": data.get("match_target") or "either",
                }
            elif "extension" in data and "target_folder" in data and "match" not in data:
                match_raw = {"extensions": [data.get("extension")]}
            else:
                match_raw = data
        action_raw = data.get("action")
        if not isinstance(action_raw, dict):
            action_raw = data.get("then")
        if not isinstance(action_raw, dict):
            folder = data.get("target_folder") or data.get("folder")
            if folder:
                action_raw = {"type": "folder", "folder": folder}
        priority = _int_or_none(data.get("priority"))
        kind = str(data.get("kind") or "custom").lower()
        if kind not in {"custom", "site", "extension"}:
            kind = "custom"
        if priority is None:
            if kind == "site":
                priority = priority_site_default
            elif kind == "extension":
                priority = priority_extension_base
            else:
                priority = priority_custom_default
        return cls(
            id=rid,
            name=str(data.get("name") or rid),
            enabled=bool(data.get("enabled", True)),
            priority=int(priority),
            kind=kind,
            match=rule_match.from_dict(match_raw if isinstance(match_raw, dict) else {}),
            action=rule_action.from_dict(
                action_raw if isinstance(action_raw, dict) else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "priority": self.priority,
            "kind": self.kind,
            "match": self.match.to_dict(),
            "action": self.action.to_dict(),
        }

    def to_download_classify_dict(self) -> dict[str, Any]:
        """serialize one rule in download_classify ClassificationRule shape."""
        matchers: dict[str, Any] = {}
        m = self.match
        if m.hosts:
            matchers["site"] = list(m.hosts)
        if m.source_hosts:
            matchers["source_site"] = list(m.source_hosts)
        if m.source_page:
            matchers["source_page"] = list(m.source_page)
        if m.filename:
            matchers["filename"] = list(m.filename)
        if m.extensions:
            matchers["extension"] = list(m.extensions)
        return {
            "id": self.id,
            "name": self.name,
            "target_folder": self.action.folder or "Others",
            "priority": self.priority,
            "enabled": self.enabled,
            "matchers": matchers,
        }


@dataclass
class rule_decision:
    matched: bool
    rule_id: str | None = None
    rule_name: str | None = None
    skip: bool = False
    folder: str | None = None
    template: str | None = None
    message: str | None = None
    priority: int | None = None
    kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "skip": self.skip,
            "folder": self.folder,
            "template": self.template,
            "message": self.message,
            "priority": self.priority,
            "kind": self.kind,
        }


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


def _as_str_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _regex_ok(pattern: str | None, text: str | None) -> bool:
    if not pattern:
        return True
    if text is None:
        return False
    try:
        return re.search(pattern, text, re.IGNORECASE) is not None
    except re.error:
        return False


def pattern_to_regex(pattern: str) -> re.Pattern[str]:
    normalized = pattern.strip().lower()
    if not normalized:
        return re.compile(r"^$")
    parts = normalized.split("*")
    body = ".*".join(re.escape(p) for p in parts)
    return re.compile(f"^{body}$", re.IGNORECASE)


def matches_pattern(value: str | None, pattern: str | None) -> bool:
    if not value or not pattern:
        return False
    return pattern_to_regex(pattern).match(value.strip()) is not None


def matches_any_pattern(value: str | None, patterns: list[str] | None) -> bool:
    if not patterns or not value:
        return False
    return any(matches_pattern(value, p) for p in patterns)


def extract_hostname(url_or_host: str | None) -> str:
    if not url_or_host:
        return ""
    text = url_or_host.strip()
    if not text:
        return ""
    if "://" not in text:
        # bare host or host/path
        return text.split("/")[0].split(":")[0].lower()
    try:
        return (urlparse(text).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def host_matches_site(hostname: str | None, pattern: str | None) -> bool:
    if not hostname or not pattern:
        return False
    lower = hostname.lower().strip()
    normalized = pattern.lower().strip()
    if not normalized:
        return False
    if matches_pattern(lower, normalized):
        return True
    bare = normalized[2:] if normalized.startswith("*.") else normalized
    return lower == bare or lower.endswith(f".{bare}")


def hosts_match_any(hostname: str | None, patterns: list[str] | None) -> bool:
    if not patterns or not hostname:
        return False
    return any(host_matches_site(hostname, p) for p in patterns)


def extract_extension(filename: str | None) -> str:
    if not filename:
        return ""
    basename = Path(filename).name
    if "." not in basename or basename.startswith("."):
        return ""
    return basename.rsplit(".", 1)[-1].lower()


def default_extension_rules() -> list[dict[str, Any]]:
    """mirrors download_classify get_default_extension_rules (zh-TW names)."""
    return [
        {"name": "文件", "extension": "pdf", "target_folder": "Documents"},
        {"name": "文件", "extension": "doc", "target_folder": "Documents"},
        {"name": "文件", "extension": "docx", "target_folder": "Documents"},
        {"name": "文件", "extension": "xlsx", "target_folder": "Documents"},
        {"name": "文件", "extension": "pptx", "target_folder": "Documents"},
        {"name": "文件", "extension": "txt", "target_folder": "Documents"},
        {"name": "壓縮檔", "extension": "zip", "target_folder": "Archives"},
        {"name": "壓縮檔", "extension": "rar", "target_folder": "Archives"},
        {"name": "壓縮檔", "extension": "7z", "target_folder": "Archives"},
        {"name": "壓縮檔", "extension": "tar", "target_folder": "Archives"},
        {"name": "壓縮檔", "extension": "gz", "target_folder": "Archives"},
        {"name": "圖片", "extension": "png", "target_folder": "Media/Images"},
        {"name": "圖片", "extension": "jpg", "target_folder": "Media/Images"},
        {"name": "圖片", "extension": "jpeg", "target_folder": "Media/Images"},
        {"name": "圖片", "extension": "gif", "target_folder": "Media/Images"},
        {"name": "圖片", "extension": "webp", "target_folder": "Media/Images"},
        {"name": "圖片", "extension": "svg", "target_folder": "Media/Images"},
        {"name": "影片", "extension": "mp4", "target_folder": "Media/Video"},
        {"name": "影片", "extension": "mkv", "target_folder": "Media/Video"},
        {"name": "影片", "extension": "avi", "target_folder": "Media/Video"},
        {"name": "影片", "extension": "mov", "target_folder": "Media/Video"},
        {"name": "影片", "extension": "webm", "target_folder": "Media/Video"},
        {"name": "音樂", "extension": "mp3", "target_folder": "Media/Audio"},
        {"name": "音樂", "extension": "flac", "target_folder": "Media/Audio"},
        {"name": "音樂", "extension": "wav", "target_folder": "Media/Audio"},
        {"name": "音樂", "extension": "aac", "target_folder": "Media/Audio"},
        {"name": "程式", "extension": "exe", "target_folder": "Programs"},
        {"name": "程式", "extension": "msi", "target_folder": "Programs"},
        {"name": "程式", "extension": "dmg", "target_folder": "Programs"},
        {"name": "程式", "extension": "pkg", "target_folder": "Programs"},
        {"name": "程式", "extension": "apk", "target_folder": "Programs"},
    ]


def site_rule_from_dict(data: dict[str, Any]) -> classify_rule:
    host = _str_or_none(data.get("host")) or ""
    priority = _int_or_none(data.get("priority"))
    if priority is None:
        priority = priority_site_default
    return classify_rule(
        id=str(data.get("id") or uuid.uuid4()),
        name=str(data.get("name") or host or "site"),
        enabled=bool(data.get("enabled", True)),
        priority=int(priority),
        kind="site",
        match=rule_match(
            hosts=[host] if host else [],
            match_target=(
                str(data.get("match_target") or "either").lower()
                if str(data.get("match_target") or "either").lower()
                in {"source", "download", "either"}
                else "either"
            ),
        ),
        action=rule_action(
            type="folder",
            folder=_str_or_none(data.get("target_folder") or data.get("folder")),
        ),
    )


def extension_rule_from_dict(
    data: dict[str, Any], *, index: int = 0
) -> classify_rule:
    ext = str(data.get("extension") or "").lstrip(".").lower()
    priority = _int_or_none(data.get("priority"))
    if priority is None:
        priority = priority_extension_base + index
    return classify_rule(
        id=str(data.get("id") or f"ext:{ext or index}"),
        name=str(data.get("name") or ext or "extension"),
        enabled=bool(data.get("enabled", True)),
        priority=int(priority),
        kind="extension",
        match=rule_match(extensions=[ext] if ext else []),
        action=rule_action(
            type="folder",
            folder=_str_or_none(data.get("target_folder") or data.get("folder")),
        ),
    )


def parse_site_rules(raw: Any) -> list[classify_rule]:
    if not isinstance(raw, list):
        return []
    return [
        site_rule_from_dict(item) for item in raw if isinstance(item, dict)
    ]


def parse_extension_rules(raw: Any) -> list[classify_rule]:
    if not isinstance(raw, list):
        return []
    return [
        extension_rule_from_dict(item, index=i)
        for i, item in enumerate(raw)
        if isinstance(item, dict)
    ]


def parse_rules(raw: Any) -> list[classify_rule]:
    """parse custom/legacy rules list (not site/extension layers)."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        items = raw.get("rules") or raw.get("classification_rules") or []
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[classify_rule] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        rule = classify_rule.from_dict(item)
        # legacy list order: if priority omitted from payload keep relative order
        if "priority" not in item and rule.kind == "custom":
            rule.priority = priority_custom_default + index
        out.append(rule)
    return out


def flatten_rules(
    *,
    rules: list[classify_rule] | None = None,
    site_rules: list[classify_rule] | None = None,
    extension_rules: list[classify_rule] | None = None,
) -> list[classify_rule]:
    """combine layers and sort by priority then stable index.

    Order mirrors download_classify: site → custom → extension when using
    default priorities.
    """
    combined: list[tuple[int, int, classify_rule]] = []
    for i, rule in enumerate(site_rules or []):
        combined.append((rule.priority, i, rule))
    offset = 1000
    for i, rule in enumerate(rules or []):
        combined.append((rule.priority, offset + i, rule))
    offset = 2000
    for i, rule in enumerate(extension_rules or []):
        combined.append((rule.priority, offset + i, rule))
    combined.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in combined]


def parse_classification_config(data: dict[str, Any] | list[Any] | None) -> list[classify_rule]:
    """load rules + site_rules + extension_rules from config / export JSON."""
    if data is None:
        return []
    if isinstance(data, list):
        return flatten_rules(rules=parse_rules(data))
    if not isinstance(data, dict):
        return []
    custom = parse_rules(data)
    sites = parse_site_rules(data.get("site_rules") or data.get("siteRules"))
    extensions = parse_extension_rules(
        data.get("extension_rules") or data.get("extensionRules")
    )
    return flatten_rules(
        rules=custom, site_rules=sites, extension_rules=extensions
    )


def rules_to_list(rules: list[classify_rule]) -> list[dict[str, Any]]:
    return [r.to_dict() for r in rules]


def site_rules_to_list(rules: list[classify_rule]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rule in rules:
        host = rule.match.hosts[0] if rule.match.hosts else ""
        out.append(
            {
                "id": rule.id,
                "name": rule.name,
                "host": host,
                "target_folder": rule.action.folder or "",
                "match_target": rule.match.match_target or "either",
                "priority": rule.priority,
                "enabled": rule.enabled,
            }
        )
    return out


def extension_rules_to_list(rules: list[classify_rule]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rule in rules:
        ext = rule.match.extensions[0] if rule.match.extensions else ""
        out.append(
            {
                "id": rule.id,
                "name": rule.name,
                "extension": ext,
                "target_folder": rule.action.folder or "",
                "priority": rule.priority,
                "enabled": rule.enabled,
            }
        )
    return out


def to_download_classify_export(
    *,
    rules: list[classify_rule],
    site_rules: list[classify_rule] | None = None,
    extension_rules: list[classify_rule] | None = None,
) -> dict[str, Any]:
    """JSON shape shared with download_classify options export."""
    custom = [r for r in rules if r.kind == "custom"]
    sites = site_rules if site_rules is not None else [r for r in rules if r.kind == "site"]
    exts = (
        extension_rules
        if extension_rules is not None
        else [r for r in rules if r.kind == "extension"]
    )
    return {
        "rules": [r.to_download_classify_dict() for r in custom],
        "site_rules": site_rules_to_list(sites),
        "extension_rules": extension_rules_to_list(exts),
    }


def import_download_classify_export(
    payload: dict[str, Any] | None,
) -> dict[str, list[classify_rule]]:
    payload = payload or {}
    return {
        "rules": parse_rules(payload.get("rules") or []),
        "site_rules": parse_site_rules(payload.get("site_rules") or []),
        "extension_rules": parse_extension_rules(
            payload.get("extension_rules") or []
        ),
    }


def rule_matches(
    rule: classify_rule,
    meta: item_metadata,
    *,
    size: int | None = None,
    media_type: str | None = None,
    original_name: str | None = None,
    source_url: str | None = None,
    download_url: str | None = None,
    mime: str | None = None,
) -> bool:
    if not rule.enabled:
        return False
    m = rule.match
    sites = list(m.sites)
    if m.site:
        sites.append(m.site)
    # udl site id match (iwara / bilibili / …); host patterns handled separately
    if sites:
        site_ok = meta.site in sites
        # also allow host-style patterns stored historically in site field
        if not site_ok:
            source_host = extract_hostname(source_url or meta.canonical_url)
            download_host = extract_hostname(download_url or meta.canonical_url)
            site_ok = any(
                host_matches_site(source_host, s) or host_matches_site(download_host, s)
                for s in sites
            )
        if not site_ok:
            return False
    if m.author and (meta.author or "").lower() != m.author.lower():
        return False
    if not _regex_ok(m.author_regex, meta.author):
        return False
    if not _regex_ok(m.title_regex, meta.title):
        return False
    if not _regex_ok(m.collection_regex, meta.collection):
        return False
    tags_lower = {t.lower() for t in (meta.tags or [])}
    if m.tags_any and not any(t.lower() in tags_lower for t in m.tags_any):
        return False
    if m.tags_all and not all(t.lower() in tags_lower for t in m.tags_all):
        return False
    if m.duration_min is not None:
        if meta.duration is None or meta.duration < m.duration_min:
            return False
    if m.duration_max is not None:
        if meta.duration is None or meta.duration > m.duration_max:
            return False
    if m.size_min is not None:
        if size is None or size < m.size_min:
            return False
    if m.size_max is not None:
        if size is None or size > m.size_max:
            return False
    resolved_type = media_type or detect_media_type(original_name, mime=mime)
    types = list(m.media_types)
    if m.media_type:
        types.append(m.media_type)
    if types and resolved_type not in types:
        return False

    # download_classify-style OR checks among present matcher groups
    or_checks: list[bool] = []
    source_host = extract_hostname(source_url or meta.canonical_url)
    download_host = extract_hostname(
        download_url or meta.canonical_url or source_url
    )
    page_url = source_url or meta.canonical_url or ""

    if m.hosts:
        target = (m.match_target or "either").lower()
        source_hit = hosts_match_any(source_host, m.hosts)
        download_hit = hosts_match_any(download_host, m.hosts)
        if target == "source":
            or_checks.append(source_hit)
        elif target == "download":
            or_checks.append(download_hit)
        else:
            or_checks.append(source_hit or download_hit)

    if m.source_hosts:
        or_checks.append(hosts_match_any(source_host, m.source_hosts))

    if m.source_page:
        or_checks.append(matches_any_pattern(page_url.lower(), m.source_page))

    if m.url_patterns:
        urls_to_try = [
            u
            for u in (source_url, download_url, meta.canonical_url)
            if u
        ]
        or_checks.append(
            any(matches_any_pattern(u.lower(), m.url_patterns) for u in urls_to_try)
        )

    if m.filename:
        basename = Path(original_name or "").name
        or_checks.append(matches_any_pattern(basename, m.filename))

    if m.extensions:
        ext = extract_extension(original_name)
        or_checks.append(ext in {e.lower().lstrip(".") for e in m.extensions})

    if m.mime_types:
        mime_l = (mime or "").lower()
        or_checks.append(
            bool(mime_l)
            and any(
                mime_l == mt or mime_l.startswith(mt.rstrip("*"))
                for mt in m.mime_types
            )
        )

    # if only host/url/ext matchers were set (no classic metadata matchers beyond
    # empty defaults), require at least one OR group to pass — same as
    # download_classify custom rules. classic-only rules keep AND semantics above.
    if or_checks and not any(or_checks):
        return False

    # site-kind rules with only host matcher: require the host check succeeded
    if rule.kind == "site" and m.hosts and or_checks and not any(or_checks):
        return False

    return True


def apply_rules(
    rules: list[classify_rule],
    meta: item_metadata,
    *,
    size: int | None = None,
    media_type: str | None = None,
    original_name: str | None = None,
    source_url: str | None = None,
    download_url: str | None = None,
    mime: str | None = None,
) -> rule_decision:
    """first matching enabled rule wins (priority-ordered list)."""
    ordered = sorted(
        enumerate(rules),
        key=lambda pair: (pair[1].priority, pair[0]),
    )
    for _, rule in ordered:
        if not rule_matches(
            rule,
            meta,
            size=size,
            media_type=media_type,
            original_name=original_name,
            source_url=source_url,
            download_url=download_url,
            mime=mime,
        ):
            continue
        action = rule.action
        if action.type == "skip":
            return rule_decision(
                matched=True,
                rule_id=rule.id,
                rule_name=rule.name,
                skip=True,
                message=f"skipped by rule {rule.name or rule.id}",
                priority=rule.priority,
                kind=rule.kind,
            )
        folder = action.folder
        template = action.template
        if action.type == "folder":
            folder = action.folder
        elif action.type == "template":
            template = action.template
        return rule_decision(
            matched=True,
            rule_id=rule.id,
            rule_name=rule.name,
            folder=folder,
            template=template,
            message=f"matched rule {rule.name or rule.id}",
            priority=rule.priority,
            kind=rule.kind,
        )
    return rule_decision(matched=False)


def test_rules_against_metadata(
    rules: list[classify_rule],
    meta: item_metadata | dict[str, Any],
    *,
    size: int | None = None,
    original_name: str | None = None,
    source_url: str | None = None,
    download_url: str | None = None,
    mime: str | None = None,
) -> dict[str, Any]:
    if isinstance(meta, dict):
        metadata = item_metadata.from_dict(meta)
    else:
        metadata = meta
    resolved_source = source_url or metadata.canonical_url
    media_type = detect_media_type(original_name, mime=mime)
    decision = apply_rules(
        rules,
        metadata,
        size=size,
        media_type=media_type,
        original_name=original_name,
        source_url=resolved_source,
        download_url=download_url or resolved_source,
        mime=mime,
    )
    evaluated: list[dict[str, Any]] = []
    for rule in sorted(rules, key=lambda r: (r.priority, r.id)):
        evaluated.append(
            {
                "id": rule.id,
                "name": rule.name,
                "enabled": rule.enabled,
                "priority": rule.priority,
                "kind": rule.kind,
                "matches": rule_matches(
                    rule,
                    metadata,
                    size=size,
                    media_type=media_type,
                    original_name=original_name,
                    source_url=resolved_source,
                    download_url=download_url or resolved_source,
                    mime=mime,
                ),
            }
        )
    preview_folder = None
    if decision.matched and decision.folder and original_name:
        parts = [
            sanitize_component(p)
            for p in decision.folder.replace("\\", "/").split("/")
            if p and p not in {".", ".."}
        ]
        preview_folder = str(Path(*parts) / Path(original_name).name) if parts else None
    return {
        "decision": decision.to_dict(),
        "evaluated": evaluated,
        "metadata": metadata.to_dict(omit_empty=True),
        "media_type": media_type,
        "source_url": resolved_source,
        "preview_path": preview_folder,
    }


def place_file_by_decision(
    path: Path,
    decision: rule_decision,
    *,
    root: Path | None = None,
) -> Path | None:
    """move ``path`` into decision.folder under root (or path.parent).

    returns new path when moved, None when nothing to do / skip / no folder.
    """
    if not decision.matched or decision.skip or not decision.folder:
        return None
    if not path.is_file():
        return None
    base = root if root is not None else path.parent
    parts = [
        sanitize_component(p)
        for p in decision.folder.replace("\\", "/").split("/")
        if p and p not in {".", ".."}
    ]
    if not parts:
        return None
    dest_dir = base.joinpath(*parts)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if dest.resolve() == path.resolve():
        return path
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        n = 1
        while dest.exists():
            dest = dest_dir / f"{stem} ({n}){suffix}"
            n += 1
    shutil.move(str(path), str(dest))
    return dest


def classify_paths(
    paths: list[Path],
    rules: list[classify_rule],
    *,
    source_url: str | None = None,
    metadata: item_metadata | dict[str, Any] | None = None,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """apply rules to each path and move into target folders under root."""
    if isinstance(metadata, dict):
        meta = item_metadata.from_dict(metadata)
    elif metadata is None:
        meta = item_metadata(site="direct", canonical_url=source_url)
    else:
        meta = metadata
    if meta.canonical_url is None and source_url:
        meta.canonical_url = source_url
    results: list[dict[str, Any]] = []
    for path in paths:
        media_type = detect_media_type(path.name)
        try:
            size = path.stat().st_size if path.is_file() else None
        except OSError:
            size = None
        decision = apply_rules(
            rules,
            meta,
            size=size,
            media_type=media_type,
            original_name=path.name,
            source_url=source_url or meta.canonical_url,
            download_url=source_url or meta.canonical_url,
        )
        new_path = place_file_by_decision(path, decision, root=root)
        results.append(
            {
                "from": str(path),
                "to": str(new_path) if new_path else str(path),
                "moved": new_path is not None and new_path != path,
                "decision": decision.to_dict(),
            }
        )
    return results
