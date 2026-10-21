"""load and save udl naming / classification config (json or toml)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from udl.paths import app_data_dir
from udl.postprocess.chain import default_pp_order, normalize_pp_order
from udl.rules import (
    classify_rule,
    extension_rules_to_list,
    flatten_rules,
    parse_extension_rules,
    parse_rules,
    parse_site_rules,
    rules_to_list,
    site_rules_to_list,
)

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
    # layered classification (download_classify-compatible)
    rules: list[classify_rule] = field(default_factory=list)
    site_rules: list[classify_rule] = field(default_factory=list)
    extension_rules: list[classify_rule] = field(default_factory=list)
    name_conflict: str = "rename"
    # yt-dlp-inspired NA placeholder for empty template fields
    outtmpl_na_placeholder: str = ""
    # v0.9-D post-download hook / checksum defaults
    checksum_auto: bool = True
    post_cmd_enabled: bool = False
    post_cmd: str = ""
    post_cmd_preset: str = ""
    post_cmd_timeout: int = 120
    # auto-extract ZIP after download (default off)
    auto_extract_zip: bool = False
    extract_to: str = "subfolder"
    extract_delete_zip: bool = False
    extract_max_uncompressed: int = 4 * 1024 * 1024 * 1024
    extract_max_ratio: float = 100.0
    extract_promote_password: bool = True
    # v0.10 ordered postprocessor chain
    pp_order: list[str] = field(default_factory=lambda: list(default_pp_order))
    move_template: str = ""
    exec_enabled: bool = False
    exec_cmd: str = ""
    exec_timeout: int = 120

    def effective_rules(self) -> list[classify_rule]:
        """priority-ordered site + custom + extension rules."""
        return flatten_rules(
            rules=self.rules,
            site_rules=self.site_rules,
            extension_rules=self.extension_rules,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name_template": self.name_template,
            "site_templates": dict(self.site_templates),
            "type_templates": dict(self.type_templates),
            "name_conflict": self.name_conflict,
            "outtmpl_na_placeholder": self.outtmpl_na_placeholder,
            "rules": rules_to_list(self.rules),
            "site_rules": site_rules_to_list(self.site_rules),
            "extension_rules": extension_rules_to_list(self.extension_rules),
            "checksum_auto": self.checksum_auto,
            "post_cmd_enabled": self.post_cmd_enabled,
            "post_cmd": self.post_cmd,
            "post_cmd_preset": self.post_cmd_preset,
            "post_cmd_timeout": self.post_cmd_timeout,
            "auto_extract_zip": self.auto_extract_zip,
            "extract_to": self.extract_to,
            "extract_delete_zip": self.extract_delete_zip,
            "extract_max_uncompressed": self.extract_max_uncompressed,
            "extract_max_ratio": self.extract_max_ratio,
            "extract_promote_password": self.extract_promote_password,
            "pp_order": list(self.pp_order),
            "move_template": self.move_template,
            "exec_enabled": self.exec_enabled,
            "exec_cmd": self.exec_cmd,
            "exec_timeout": self.exec_timeout,
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
        try:
            timeout = int(data.get("post_cmd_timeout") or 120)
        except (TypeError, ValueError):
            timeout = 120
        checksum_auto = data.get("checksum_auto")
        if checksum_auto is None:
            checksum_auto = True
        extract_to = str(
            data.get("extract_to") or data.get("extractTo") or "subfolder"
        ).strip().lower()
        if extract_to in {"same", "same_folder", "flat"}:
            extract_to = "same_folder"
        else:
            extract_to = "subfolder"
        try:
            max_uncompressed = int(
                data.get("extract_max_uncompressed")
                or data.get("extractMaxUncompressed")
                or 4 * 1024 * 1024 * 1024
            )
        except (TypeError, ValueError):
            max_uncompressed = 4 * 1024 * 1024 * 1024
        try:
            max_ratio = float(
                data.get("extract_max_ratio") or data.get("extractMaxRatio") or 100.0
            )
        except (TypeError, ValueError):
            max_ratio = 100.0
        try:
            exec_timeout = int(
                data.get("exec_timeout") or data.get("execTimeout") or 120
            )
        except (TypeError, ValueError):
            exec_timeout = 120
        pp_raw = (
            data.get("pp_order")
            or data.get("ppOrder")
            or data.get("postprocessor_order")
            or data.get("postprocessorOrder")
        )
        return cls(
            name_template=str(data.get("name_template") or data.get("template") or ""),
            site_templates={str(k): str(v) for k, v in site_templates.items()},
            type_templates=merged_types,
            rules=parse_rules(data),
            site_rules=parse_site_rules(
                data.get("site_rules") or data.get("siteRules")
            ),
            extension_rules=parse_extension_rules(
                data.get("extension_rules") or data.get("extensionRules")
            ),
            name_conflict=conflict,
            outtmpl_na_placeholder=str(
                data.get("outtmpl_na_placeholder")
                if "outtmpl_na_placeholder" in data
                else data.get("outtmplNaPlaceholder")
                if "outtmplNaPlaceholder" in data
                else ""
            ),
            checksum_auto=bool(checksum_auto),
            post_cmd_enabled=bool(data.get("post_cmd_enabled")),
            post_cmd=str(data.get("post_cmd") or ""),
            post_cmd_preset=str(data.get("post_cmd_preset") or ""),
            post_cmd_timeout=max(1, timeout),
            auto_extract_zip=bool(
                data.get("auto_extract_zip")
                if "auto_extract_zip" in data
                else data.get("autoExtractZip")
            ),
            extract_to=extract_to,
            extract_delete_zip=bool(
                data.get("extract_delete_zip")
                or data.get("extractDeleteZip")
            ),
            extract_max_uncompressed=max(1, max_uncompressed),
            extract_max_ratio=max(1.0, max_ratio),
            extract_promote_password=(
                True
                if data.get("extract_promote_password") is None
                and data.get("extractPromotePassword") is None
                else bool(
                    data.get("extract_promote_password")
                    if "extract_promote_password" in data
                    else data.get("extractPromotePassword")
                )
            ),
            pp_order=normalize_pp_order(pp_raw),
            move_template=str(
                data.get("move_template") or data.get("moveTemplate") or ""
            ),
            exec_enabled=bool(
                data.get("exec_enabled")
                if "exec_enabled" in data
                else data.get("execEnabled")
            ),
            exec_cmd=str(data.get("exec_cmd") or data.get("execCmd") or ""),
            exec_timeout=max(1, exec_timeout),
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
    for key in (
        "name_template",
        "name_conflict",
        "outtmpl_na_placeholder",
        "checksum_auto",
        "post_cmd_enabled",
        "post_cmd",
        "post_cmd_preset",
        "post_cmd_timeout",
        "auto_extract_zip",
        "extract_to",
        "extract_delete_zip",
        "extract_max_uncompressed",
        "extract_max_ratio",
        "extract_promote_password",
        "pp_order",
        "move_template",
        "exec_enabled",
        "exec_cmd",
        "exec_timeout",
    ):
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
    if "site_rules" in overlay:
        data["site_rules"] = overlay["site_rules"]
    if "extension_rules" in overlay:
        data["extension_rules"] = overlay["extension_rules"]
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
