"""filename and path template engine with cross-platform sanitization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from udl.metadata import item_metadata

# default keeps extractor-provided relative_dir + name
default_name_template = ""

# windows reserved device names (case-insensitive)
_windows_reserved = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

# illegal on windows + macOS (colon is illegal on both for path components)
_illegal_chars_re = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_whitespace_collapse_re = re.compile(r"\s+")
# legacy simple form kept for reference; render_template uses _brace_field_re
_placeholder_re = re.compile(
    r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::([^}]*))?\}"
)

max_component_len = 200
max_path_len = 240

# field aliases accepted in templates (incl. common yt-dlp OUTPUT TEMPLATE names)
_field_aliases: dict[str, str] = {
    "id": "item_id",
    "itemid": "item_id",
    "url": "canonical_url",
    "webpage_url": "canonical_url",
    "uploader": "author",
    "creator": "author",
    "channel": "author",
    "artist": "author",
    "album": "collection",
    "playlist": "collection",
    "playlist_title": "collection",
    "date": "upload_date",
    "uploaded": "upload_date",
    "upload_date": "upload_date",
    "res": "resolution",
    "quality": "resolution",
    "thumbnail": "thumbnail_url",
}

# yt-dlp-inspired field expression inside {…} or %(…)x
# name[>strftime|:fmt][,alt…][&replacement][|default]
_brace_field_re = re.compile(r"\{([^{}]+)\}")
_percent_field_re = re.compile(
    r"%\(([^)]+)\)([ljhqBUDS]|[diouxXeEfFgGcrs])?"
)
default_outtmpl_na = ""


@dataclass
class rendered_path:
    """result of applying a name template under an output root."""

    relative: str
    absolute: Path
    template: str
    conflict_renamed: bool = False


def detect_media_type(name: str | None, *, mime: str | None = None) -> str:
    """coarse media type from filename extension or mime hint.

    returns video / audio / image / archive / document / program / other
    """
    if mime:
        lower = mime.lower()
        if lower.startswith("video/"):
            return "video"
        if lower.startswith("audio/"):
            return "audio"
        if lower.startswith("image/"):
            return "image"
        if lower in {
            "application/zip",
            "application/x-rar-compressed",
            "application/x-7z-compressed",
            "application/gzip",
            "application/x-tar",
        }:
            return "archive"
        if lower in {
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
        }:
            return "document"
    ext = Path(name or "").suffix.lower().lstrip(".")
    if ext in {"mp4", "mkv", "webm", "avi", "mov", "flv", "m4v", "ts", "mpg", "mpeg"}:
        return "video"
    if ext in {"mp3", "m4a", "flac", "wav", "aac", "ogg", "opus", "wma"}:
        return "audio"
    if ext in {"jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "svg"}:
        return "image"
    if ext in {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz"}:
        return "archive"
    if ext in {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "rtf", "odt"}:
        return "document"
    if ext in {"exe", "msi", "dmg", "pkg", "apk", "appimage", "deb", "rpm"}:
        return "program"
    return "other"


def sanitize_component(value: str | None, *, replacement: str = "_") -> str:
    """sanitize one path component for windows + macOS."""
    text = "" if value is None else str(value)
    text = text.replace("\u0000", "")
    text = _illegal_chars_re.sub(replacement, text)
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = _whitespace_collapse_re.sub(" ", text).strip(" .")
    if not text:
        text = "unnamed"
    stem = text
    suffix = ""
    if "." in text and not text.startswith("."):
        # preserve final extension-like segment when shortening
        parts = text.rsplit(".", 1)
        if len(parts[1]) <= 12 and parts[1].isalnum():
            stem, suffix = parts[0], parts[1]
    stem = stem.rstrip(" .") or "unnamed"
    # windows reserved names (with or without extension)
    check = stem.upper()
    if check in _windows_reserved:
        stem = f"_{stem}"
    if suffix:
        combined = f"{stem}.{suffix}"
    else:
        combined = stem
    if len(combined) > max_component_len:
        if suffix:
            keep = max_component_len - len(suffix) - 1
            combined = f"{stem[: max(1, keep)].rstrip(' .')}.{suffix}"
        else:
            combined = combined[:max_component_len].rstrip(" .") or "unnamed"
    return combined


def sanitize_relative_path(relative: str, *, replacement: str = "_") -> str:
    """sanitize a multi-level relative path using posix separators."""
    raw = relative.replace("\\", "/").strip("/")
    if not raw:
        return "unnamed"
    parts: list[str] = []
    for part in PurePosixPath(raw).parts:
        if part in {".", ""}:
            continue
        if part == "..":
            continue
        parts.append(sanitize_component(part, replacement=replacement))
    return "/".join(parts) if parts else "unnamed"


def _parse_upload_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt, width in (
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d", 10),
        ("%Y/%m/%d", 10),
        ("%Y%m%d", 8),
    ):
        try:
            return datetime.strptime(text[:width], fmt)
        except ValueError:
            continue
    return None


def _lookup_field(
    meta: item_metadata,
    *,
    original_name: str,
    ext: str,
    media_type: str,
    size: int | None,
    key: str,
) -> Any:
    lowered = key.lower()
    if lowered in {"ext", "extension"}:
        return ext
    if lowered in {"name", "original", "original_name"}:
        return Path(original_name).stem if original_name else ""
    if lowered in {"filename", "fullname", "basename"}:
        return original_name
    if lowered in {"type", "media_type", "mediatype"}:
        return media_type
    if lowered == "size":
        return size if size is not None else ""
    if lowered in {"tags", "tag"}:
        return ", ".join(meta.tags) if meta.tags else ""
    field = _field_aliases.get(lowered, lowered)
    if field == "extra":
        return ""
    if hasattr(meta, field):
        return getattr(meta, field)
    if field in meta.extra:
        return meta.extra[field]
    return ""


def _format_value(value: Any, fmt: str | None, *, field_key: str) -> str:
    if value is None:
        return ""
    if field_key.lower() in {"date", "upload_date", "uploaded"} or (
        fmt and "%" in fmt and isinstance(value, (str, datetime))
    ):
        dt = value if isinstance(value, datetime) else _parse_upload_date(str(value))
        if dt is not None:
            return dt.strftime(fmt or "%Y-%m-%d")
        return "" if fmt else str(value)
    if fmt and isinstance(value, (int, float)):
        try:
            return format(value, fmt)
        except (ValueError, TypeError):
            return str(value)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _split_field_expr(expr: str) -> tuple[list[str], str | None, str | None]:
    """split yt-dlp-like field expr into alternatives, replacement, default.

    Grammar (subset): name[>strf|:fmt][,alt…][&replacement][|default]
    """
    text = expr.strip()
    default: str | None = None
    replacement: str | None = None
    if "|" in text:
        # default is after the last unescaped conceptually; use first | at top level
        left, _, right = text.partition("|")
        text = left
        default = right
    if "&" in text:
        left, _, right = text.partition("&")
        text = left
        replacement = right
    alternatives = [part.strip() for part in text.split(",") if part.strip()]
    if not alternatives:
        alternatives = [""]
    return alternatives, replacement, default


def _parse_alt_field(alt: str) -> tuple[str, str | None]:
    """parse one alternative into (field_key, format).

    Supports `date>%Y-%m-%d` (yt-dlp) and legacy `date:%Y-%m-%d`.
    """
    text = alt.strip()
    if ">" in text:
        key, _, fmt = text.partition(">")
        return key.strip(), (fmt or None)
    # colon format only when it looks like strftime / printf (avoid dict keys)
    if ":" in text:
        key, _, fmt = text.partition(":")
        key = key.strip()
        fmt = fmt.strip()
        if key and (
            "%" in fmt
            or re.fullmatch(r"[0-9]*[diouxXeEfFgGcrs]?", fmt)
            or re.fullmatch(r"[<>^]?[0-9]*\.?[0-9]*[diouxXeEfFgGcrs]?", fmt)
        ):
            return key, fmt or None
    return text, None


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def _expand_field_expr(
    expr: str,
    meta: item_metadata,
    *,
    original_name: str,
    ext: str,
    media_type: str,
    size: int | None,
    na_placeholder: str,
    conversion: str | None = None,
) -> str:
    alternatives, replacement, default = _split_field_expr(expr)
    chosen_value: Any = ""
    found = False
    for alt in alternatives:
        key, fmt = _parse_alt_field(alt)
        value = _lookup_field(
            meta,
            original_name=original_name,
            ext=ext,
            media_type=media_type,
            size=size,
            key=key,
        )
        text = _format_value(value, fmt, field_key=key)
        if not _is_empty(text):
            chosen_value = text
            found = True
            break
        chosen_value = text

    if found:
        if replacement is not None:
            # str.format mini-language subset: replacement may contain {}
            try:
                out = replacement.format(chosen_value)
            except (ValueError, IndexError, KeyError):
                out = replacement.replace("{}", str(chosen_value))
                if "{" in out and "}" in out:
                    try:
                        out = replacement.format(value=chosen_value)
                    except (ValueError, IndexError, KeyError):
                        out = replacement
        else:
            out = str(chosen_value)
    else:
        if default is not None:
            out = default
        else:
            out = na_placeholder

    if conversion == "S":
        out = sanitize_component(out)
    elif conversion == "l" and isinstance(chosen_value, list):
        out = ", ".join(str(v) for v in chosen_value)
    # field values must not introduce path separators unless intentionally in template
    return out.replace("/", "_").replace("\\", "_")


def render_template(
    template: str,
    meta: item_metadata,
    *,
    original_name: str = "",
    size: int | None = None,
    media_type: str | None = None,
    na_placeholder: str | None = None,
) -> str:
    """render a template string to a relative path (unsanitized segments joined).

    Supports:
      - `{field}` / `{field:%Y-%m}` (legacy)
      - `{field|default}` / `{a,b|default}` alternatives
      - `{field&replacement|default}` when non-empty
      - `{date>%Y-%m-%d}` yt-dlp-style strftime
      - `%(field)s` / `%(title|NA)s` yt-dlp OUTPUT TEMPLATE form
    """
    if not template or not template.strip():
        return original_name or "unnamed"
    ext = Path(original_name).suffix.lstrip(".") if original_name else ""
    mtype = media_type or detect_media_type(original_name)
    na = default_outtmpl_na if na_placeholder is None else na_placeholder

    def brace_repl(match: re.Match[str]) -> str:
        return _expand_field_expr(
            match.group(1),
            meta,
            original_name=original_name,
            ext=ext,
            media_type=mtype,
            size=size,
            na_placeholder=na,
        )

    def percent_repl(match: re.Match[str]) -> str:
        return _expand_field_expr(
            match.group(1),
            meta,
            original_name=original_name,
            ext=ext,
            media_type=mtype,
            size=size,
            na_placeholder=na,
            conversion=match.group(2),
        )

    # percent form first so "%(title)s" is not partially eaten
    rendered = _percent_field_re.sub(percent_repl, template)
    rendered = _brace_field_re.sub(brace_repl, rendered)
    # collapse accidental empty segments from missing fields
    parts = [p for p in rendered.replace("\\", "/").split("/") if p not in {"", "."}]
    return "/".join(parts) if parts else (original_name or "unnamed")


def unique_path(path: Path) -> Path:
    """return path or path with (n) suffix before extension if taken."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for n in range(1, 10_000):
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"could not find unique name for {path}")


def truncate_path(path: Path, *, root: Path) -> Path:
    """ensure absolute path string stays under max_path_len when possible."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path
    text = str(path)
    if len(text) <= max_path_len:
        return path
    parts = list(relative.parts)
    if not parts:
        return path
    # shorten the final component first
    final = parts[-1]
    budget = max_path_len - len(str(root)) - 1 - sum(len(p) + 1 for p in parts[:-1])
    budget = max(16, budget)
    if len(final) > budget:
        p = Path(final)
        if p.suffix and len(p.suffix) < 16:
            keep = budget - len(p.suffix)
            final = f"{p.stem[: max(1, keep)].rstrip(' .')}{p.suffix}"
        else:
            final = final[:budget].rstrip(" .") or "unnamed"
        parts[-1] = final
    return root.joinpath(*parts)


def resolve_output_path(
    output_dir: Path,
    *,
    original_name: str,
    relative_dir: str = "",
    metadata: item_metadata | None = None,
    template: str | None = None,
    folder_override: str | None = None,
    size: int | None = None,
    media_type: str | None = None,
    on_conflict: str = "rename",
    na_placeholder: str | None = None,
) -> rendered_path:
    """compute final absolute path under output_dir.

    on_conflict: rename | overwrite | error
    empty template keeps extractor relative_dir + original_name
    """
    meta = metadata or item_metadata(site="unknown", title=original_name)
    tpl = (template or "").strip()
    if tpl:
        relative = render_template(
            tpl,
            meta,
            original_name=original_name,
            size=size,
            media_type=media_type,
            na_placeholder=na_placeholder,
        )
    else:
        base = original_name or "unnamed"
        cleaned_dir = relative_dir.strip("/").strip("\\") if relative_dir else ""
        relative = f"{cleaned_dir}/{base}" if cleaned_dir else base

    if folder_override:
        folder = sanitize_relative_path(folder_override)
        # if template already includes directories keep filename only under override
        filename = PurePosixPath(sanitize_relative_path(relative)).name
        relative = f"{folder}/{filename}"
    else:
        relative = sanitize_relative_path(relative)

    # ensure we have an extension when original had one and template dropped it
    orig_ext = Path(original_name).suffix
    if orig_ext and not Path(relative).suffix:
        relative = f"{relative}{orig_ext}"
        relative = sanitize_relative_path(relative)

    absolute = truncate_path(output_dir.joinpath(*PurePosixPath(relative).parts), root=output_dir)
    conflict_renamed = False
    if absolute.exists() and on_conflict == "rename":
        absolute = unique_path(absolute)
        conflict_renamed = True
        try:
            relative = str(absolute.relative_to(output_dir)).replace("\\", "/")
        except ValueError:
            relative = absolute.name
    elif absolute.exists() and on_conflict == "error":
        raise FileExistsError(str(absolute))
    return rendered_path(
        relative=relative,
        absolute=absolute,
        template=tpl,
        conflict_renamed=conflict_renamed,
    )


def preview_template(
    template: str,
    meta: item_metadata | dict[str, Any],
    *,
    original_name: str = "example.mp4",
    size: int | None = None,
    na_placeholder: str | None = None,
) -> dict[str, Any]:
    """preview helper for CLI / GUI live preview."""
    if isinstance(meta, dict):
        metadata = item_metadata.from_dict(meta)
    else:
        metadata = meta
    media_type = detect_media_type(original_name)
    relative = sanitize_relative_path(
        render_template(
            template,
            metadata,
            original_name=original_name,
            size=size,
            media_type=media_type,
            na_placeholder=na_placeholder,
        )
    )
    return {
        "template": template,
        "relative": relative,
        "media_type": media_type,
        "original_name": original_name,
        "metadata": metadata.to_dict(omit_empty=True),
    }


def pick_template(
    *,
    cli_template: str | None,
    site: str | None,
    media_type: str | None,
    config_default: str | None = None,
    site_templates: dict[str, str] | None = None,
    type_templates: dict[str, str] | None = None,
) -> str:
    """resolve template precedence: cli > rule (caller) > site > type > config default."""
    if cli_template is not None and cli_template.strip() != "":
        return cli_template.strip()
    site_templates = site_templates or {}
    type_templates = type_templates or {}
    if site and site in site_templates and site_templates[site].strip():
        return site_templates[site].strip()
    if media_type and media_type in type_templates and type_templates[media_type].strip():
        return type_templates[media_type].strip()
    if config_default and config_default.strip():
        return config_default.strip()
    return default_name_template
