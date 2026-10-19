"""load and save udl naming / classification config (json or toml)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from udl.paths import app_data_dir
from udl.rules import classify_rule, parse_rules, rules_to_list

config_filename = "udl.config.json"
udl_config_env = "UDL_CONFIG"
_safe_toml_key = re.compile(r"^[A-Za-z0-9_-]+$")


def _default_type_templates() -> dict[str, str]:
    try:
        from udl.extractors.bilibili.templates import default_type_templates

        return dict(default_type_templates)
    except Exception:  # noqa: BLE001
        return {}


@dataclass
class udl_config:
    name_template: str = ""
    site_templates: dict[str, str] = field(default_factory=dict)
    type_templates: dict[str, str] = field(default_factory=_default_type_templates)
    rules: list[classify_rule] = field(default_factory=list)
    name_conflict: str = "rename"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name_template": self.name_template,
            "site_templates": dict(self.site_templates),
            "type_templates": dict(self.type_templates),
            "name_conflict": self.name_conflict,
            "rules": rules_to_list(self.rules),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> udl_config:
        data = data or {}
        site_templates = data.get("site_templates") or data.get("templates_by_site") or {}
        type_templates = data.get("type_templates") or data.get("templates_by_type") or {}
        if not isinstance(site_templates, dict):
            site_templates = {}
        if not isinstance(type_templates, dict):
            type_templates = {}
        conflict = str(data.get("name_conflict") or "rename").lower()
        if conflict not in {"rename", "overwrite", "error"}:
            conflict = "rename"
        merged_types = _default_type_templates()
        merged_types.update({str(k): str(v) for k, v in type_templates.items()})
        return cls(
            name_template=str(data.get("name_template") or data.get("template") or ""),
            site_templates={str(k): str(v) for k, v in site_templates.items()},
            type_templates=merged_types,
            rules=parse_rules(data),
            name_conflict=conflict,
        )


def default_config_path(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(udl_config_env)
    if env:
        return Path(env).expanduser().resolve()
    return app_data_dir() / config_filename


def load_config(path: str | Path | None = None) -> udl_config:
    cfg_path = default_config_path(path)
    try:
        raw = _load_raw(cfg_path)
    except (OSError, json.JSONDecodeError, ValueError):
        return udl_config()
    return udl_config.from_dict(raw)


def save_config(config: udl_config, path: str | Path | None = None) -> Path:
    cfg_path = default_config_path(path)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.to_dict()
    if cfg_path.suffix.lower() in {".toml", ".tml"}:
        cfg_path.write_text(_dict_to_toml(payload), encoding="utf-8")
    else:
        cfg_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return cfg_path


def merge_config_dict(base: udl_config, overlay: dict[str, Any] | None) -> udl_config:
    """overlay GUI / CLI partial settings onto loaded config."""
    if not overlay:
        return base
    data = base.to_dict()
    for key in ("name_template", "name_conflict"):
        if key in overlay and overlay[key] is not None:
            data[key] = overlay[key]
    if "site_templates" in overlay and isinstance(overlay["site_templates"], dict):
        merged = dict(data.get("site_templates") or {})
        merged.update({str(k): str(v) for k, v in overlay["site_templates"].items()})
        data["site_templates"] = merged
    if "type_templates" in overlay and isinstance(overlay["type_templates"], dict):
        merged = dict(data.get("type_templates") or {})
        merged.update({str(k): str(v) for k, v in overlay["type_templates"].items()})
        data["type_templates"] = merged
    if "rules" in overlay:
        data["rules"] = overlay["rules"]
    return udl_config.from_dict(data)


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".toml", ".tml"}:
        import tomllib

        data = tomllib.loads(text)
        if not isinstance(data, dict):
            return {}
        if "rules_json" in data and "rules" not in data:
            try:
                parsed = json.loads(str(data["rules_json"]))
                if isinstance(parsed, list):
                    data["rules"] = parsed
            except json.JSONDecodeError:
                pass
        return data
    data = json.loads(text) if text.strip() else {}
    return data if isinstance(data, dict) else {}


def _dict_to_toml(data: dict[str, Any]) -> str:
    lines: list[str] = [
        f"name_template = {_toml_str(data.get('name_template') or '')}",
        f"name_conflict = {_toml_str(data.get('name_conflict') or 'rename')}",
    ]
    site_templates = data.get("site_templates") or {}
    if site_templates:
        lines.append("")
        lines.append("[site_templates]")
        for key, value in site_templates.items():
            lines.append(f"{_toml_key(str(key))} = {_toml_str(str(value))}")
    type_templates = data.get("type_templates") or {}
    if type_templates:
        lines.append("")
        lines.append("[type_templates]")
        for key, value in type_templates.items():
            lines.append(f"{_toml_key(str(key))} = {_toml_str(str(value))}")
    rules = data.get("rules") or []
    lines.append("")
    lines.append(f"rules_json = {_toml_str(json.dumps(rules, ensure_ascii=False))}")
    return "\n".join(lines) + "\n"


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_key(key: str) -> str:
    if _safe_toml_key.fullmatch(key):
        return key
    return _toml_str(key)
