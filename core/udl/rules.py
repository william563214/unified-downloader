"""ordered classification rules: match metadata then folder/template/skip."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from udl.metadata import item_metadata
from udl.naming import detect_media_type


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

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> rule_match:
        if not data:
            return cls()
        sites = data.get("sites") or []
        if isinstance(sites, str):
            sites = [s.strip() for s in sites.split(",") if s.strip()]
        tags_any = data.get("tags_any") or data.get("tags") or []
        if isinstance(tags_any, str):
            tags_any = [t.strip() for t in tags_any.split(",") if t.strip()]
        tags_all = data.get("tags_all") or []
        if isinstance(tags_all, str):
            tags_all = [t.strip() for t in tags_all.split(",") if t.strip()]
        media_types = data.get("media_types") or []
        if isinstance(media_types, str):
            media_types = [t.strip() for t in media_types.split(",") if t.strip()]
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
            folder=_str_or_none(data.get("folder") or data.get("target_folder")),
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
    match: rule_match = field(default_factory=rule_match)
    action: rule_action = field(default_factory=rule_action)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> classify_rule:
        data = data or {}
        rid = str(data.get("id") or uuid.uuid4())
        return cls(
            id=rid,
            name=str(data.get("name") or rid),
            enabled=bool(data.get("enabled", True)),
            match=rule_match.from_dict(
                data.get("match") if isinstance(data.get("match"), dict) else data
            ),
            action=rule_action.from_dict(
                data.get("action")
                if isinstance(data.get("action"), dict)
                else data.get("then")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "match": self.match.to_dict(),
            "action": self.action.to_dict(),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "skip": self.skip,
            "folder": self.folder,
            "template": self.template,
            "message": self.message,
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


def _regex_ok(pattern: str | None, text: str | None) -> bool:
    if not pattern:
        return True
    if text is None:
        return False
    try:
        return re.search(pattern, text, re.IGNORECASE) is not None
    except re.error:
        return False


def rule_matches(
    rule: classify_rule,
    meta: item_metadata,
    *,
    size: int | None = None,
    media_type: str | None = None,
    original_name: str | None = None,
) -> bool:
    if not rule.enabled:
        return False
    m = rule.match
    sites = list(m.sites)
    if m.site:
        sites.append(m.site)
    if sites and meta.site not in sites:
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
    resolved_type = media_type or detect_media_type(original_name)
    types = list(m.media_types)
    if m.media_type:
        types.append(m.media_type)
    if types and resolved_type not in types:
        return False
    return True


def apply_rules(
    rules: list[classify_rule],
    meta: item_metadata,
    *,
    size: int | None = None,
    media_type: str | None = None,
    original_name: str | None = None,
) -> rule_decision:
    """first matching enabled rule wins (ordered list)."""
    for rule in rules:
        if not rule_matches(
            rule,
            meta,
            size=size,
            media_type=media_type,
            original_name=original_name,
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
            )
        # prefer explicit folder/template fields; type selects primary intent
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
        )
    return rule_decision(matched=False)


def parse_rules(raw: Any) -> list[classify_rule]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        items = raw.get("rules") or raw.get("classification_rules") or []
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    return [classify_rule.from_dict(item) for item in items if isinstance(item, dict)]


def rules_to_list(rules: list[classify_rule]) -> list[dict[str, Any]]:
    return [r.to_dict() for r in rules]


def test_rules_against_metadata(
    rules: list[classify_rule],
    meta: item_metadata | dict[str, Any],
    *,
    size: int | None = None,
    original_name: str | None = None,
) -> dict[str, Any]:
    if isinstance(meta, dict):
        metadata = item_metadata.from_dict(meta)
    else:
        metadata = meta
    media_type = detect_media_type(original_name)
    decision = apply_rules(
        rules,
        metadata,
        size=size,
        media_type=media_type,
        original_name=original_name,
    )
    evaluated: list[dict[str, Any]] = []
    for rule in rules:
        evaluated.append(
            {
                "id": rule.id,
                "name": rule.name,
                "enabled": rule.enabled,
                "matches": rule_matches(
                    rule,
                    metadata,
                    size=size,
                    media_type=media_type,
                    original_name=original_name,
                ),
            }
        )
    return {
        "decision": decision.to_dict(),
        "evaluated": evaluated,
        "metadata": metadata.to_dict(omit_empty=True),
        "media_type": media_type,
    }
