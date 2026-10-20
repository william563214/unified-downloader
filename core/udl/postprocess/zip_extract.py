"""safe ZIP auto-extract postprocessor (stdlib zipfile only).

Runs after download completes and checksum / AV hook (if configured) pass.
Guards against zip-slip absolute paths symlinks and zip bombs.
Filename encoding: UTF-8 flag then cp437 with cp932/gbk detection for CJK names.
Encrypted archives are not cracked; status needs_password when password required.
"""

from __future__ import annotations

import stat
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from udl.events import emitter
from udl.secrets import get_secret, zip_password_key

# lowercase constants per project convention
zip_magic = b"PK\x03\x04"
zip_password_secret_key = zip_password_key
default_max_uncompressed = 4 * 1024 * 1024 * 1024
default_max_ratio = 100.0
extract_destination = Literal["subfolder", "same_folder"]
extract_override = Literal["inherit", "on", "off"]

# unix symlink file type in zip external_attr high word
_unix_ifmt = 0o170000
_unix_iflnk = 0o120000


@dataclass
class extract_options:
    """auto-extract settings (global + per-job override)."""

    enabled: bool = False
    override: extract_override = "inherit"
    destination: extract_destination = "subfolder"
    delete_zip: bool = False
    max_uncompressed: int = default_max_uncompressed
    max_ratio: float = default_max_ratio
    password: str | None = None
    use_secret_password: bool = True

    def resolved_enabled(self) -> bool:
        if self.override == "on":
            return True
        if self.override == "off":
            return False
        return bool(self.enabled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "override": self.override,
            "destination": self.destination,
            "delete_zip": self.delete_zip,
            "max_uncompressed": self.max_uncompressed,
            "max_ratio": self.max_ratio,
            "use_secret_password": self.use_secret_password,
            # never serialize password material
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> extract_options:
        if not data:
            return cls()
        dest = str(
            data.get("destination")
            or data.get("extract_to")
            or data.get("extractTo")
            or "subfolder"
        ).strip().lower()
        if dest in {"same", "same_folder", "flat"}:
            dest = "same_folder"
        else:
            dest = "subfolder"
        override = str(
            data.get("override")
            or data.get("auto_extract_override")
            or data.get("autoExtractOverride")
            or "inherit"
        ).strip().lower()
        if override not in {"inherit", "on", "off"}:
            override = "inherit"
        try:
            max_bytes = int(
                data.get("max_uncompressed")
                or data.get("extract_max_uncompressed")
                or default_max_uncompressed
            )
        except (TypeError, ValueError):
            max_bytes = default_max_uncompressed
        try:
            max_ratio = float(
                data.get("max_ratio")
                or data.get("extract_max_ratio")
                or default_max_ratio
            )
        except (TypeError, ValueError):
            max_ratio = default_max_ratio
        password = data.get("password") or data.get("extract_password")
        password_s = str(password) if password else None
        use_secret = data.get("use_secret_password")
        if use_secret is None:
            use_secret = data.get("useSecretPassword")
        if use_secret is None:
            use_secret = True
        enabled = bool(
            data.get("enabled")
            if "enabled" in data
            else data.get("auto_extract_zip")
            if "auto_extract_zip" in data
            else data.get("autoExtractZip")
        )
        return cls(
            enabled=enabled,
            override=override,  # type: ignore[arg-type]
            destination=dest,  # type: ignore[arg-type]
            delete_zip=bool(
                data.get("delete_zip")
                or data.get("extract_delete_zip")
                or data.get("extractDeleteZip")
            ),
            max_uncompressed=max(1, max_bytes),
            max_ratio=max(1.0, max_ratio),
            password=password_s,
            use_secret_password=bool(use_secret),
        )


@dataclass
class extract_result:
    ok: bool
    path: str
    dest: str | None = None
    members: int = 0
    deleted_archive: bool = False
    code: str | None = None
    message: str | None = None
    extracted_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "path": self.path,
            "dest": self.dest,
            "members": self.members,
            "deleted_archive": self.deleted_archive,
            "code": self.code,
            "message": self.message,
            "extracted_paths": list(self.extracted_paths),
        }


def is_zip_candidate(path: str | Path) -> bool:
    """true when filename ends with .zip or file starts with ZIP local-file magic."""
    target = Path(path)
    if not target.is_file():
        return False
    if target.suffix.lower() == ".zip":
        return True
    try:
        with target.open("rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    return head == zip_magic


def resolve_password(options: extract_options) -> str | None:
    if options.password:
        return options.password
    if options.use_secret_password:
        secret = get_secret(zip_password_secret_key)
        if secret:
            return secret
    return None


def decode_zip_filename(info: zipfile.ZipInfo) -> str:
    """decode member name: UTF-8 flag else cp437 bytes with cp932/gbk fallback."""
    if info.flag_bits & 0x800:
        return info.filename.replace("\\", "/")
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename.replace("\\", "/")
    # prefer utf-8 if the bytes happen to be utf-8
    try:
        return raw.decode("utf-8").replace("\\", "/")
    except UnicodeDecodeError:
        pass
    best = raw.decode("cp437", errors="replace").replace("\\", "/")
    best_score = _cjk_score(best)
    for enc in ("cp932", "gbk"):
        try:
            text = raw.decode(enc).replace("\\", "/")
        except UnicodeDecodeError:
            continue
        score = _cjk_score(text)
        if score > best_score:
            best = text
            best_score = score
    return best


def _cjk_score(text: str) -> int:
    score = 0
    for ch in text:
        code = ord(ch)
        if ch == "\ufffd":
            score -= 8
            continue
        if (
            0x3040 <= code <= 0x30FF
            or 0x3400 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
            or 0xFF66 <= code <= 0xFF9D
        ):
            score += 2
        elif code < 32 and ch not in "\t\n\r":
            score -= 4
    return score


def _is_symlink_member(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and (mode & _unix_ifmt) == _unix_iflnk:
        return True
    # some creators set ms-dos directory bit oddly ignore unless unix mode says link
    name = info.filename
    return name.endswith("/") and bool(mode and stat.S_ISLNK(mode))


def _is_unsafe_member_name(name: str) -> bool:
    if not name or name.strip() == "":
        return True
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//"):
        return True
    # windows drive / unc
    if len(normalized) >= 2 and normalized[1] == ":":
        return True
    parts = normalized.split("/")
    if any(p == ".." for p in parts):
        return True
    return False


def destination_dir(archive: Path, destination: extract_destination) -> Path:
    if destination == "same_folder":
        return archive.parent
    # subfolder named after archive stem next to it
    return archive.parent / archive.stem


def after_extract_classify_hook(
    extracted_paths: list[Path],
    *,
    archive: Path,
    source_url: str | None = None,
    metadata: dict[str, Any] | None = None,
    rules: list[Any] | None = None,
) -> None:
    """hook for folder classification rules (issue #27).

    When type/url classification is present callers may move extracted members.
    Currently a no-op placeholder so extract remains independent of #27.
    """
    _ = (extracted_paths, archive, source_url, metadata, rules)


def extract_zip(
    path: str | Path,
    options: extract_options,
    *,
    emit: emitter | None = None,
    job: str | None = None,
    file: str | None = None,
    source_url: str | None = None,
    metadata: dict[str, Any] | None = None,
    classify_hook: Callable[..., None] | None = None,
) -> extract_result | None:
    """extract one archive when enabled and recognized. returns None when skipped."""
    if not options.resolved_enabled():
        return None
    archive = Path(path)
    if not is_zip_candidate(archive):
        return None

    dest_root = destination_dir(archive, options.destination)
    if emit is not None:
        emit.extracting(
            job=job or "",
            file=file,
            path=str(archive),
            dest=str(dest_root),
        )
        emit.postprocess(
            job=job or "",
            step="extracting",
            file=file,
            path=str(archive),
            role="extract",
            message="extracting zip",
            ok=None,
        )

    password = resolve_password(options)
    try:
        result = _extract_safe(
            archive,
            dest_root,
            options=options,
            password=password,
        )
    except Exception as exc:  # noqa: BLE001
        result = extract_result(
            ok=False,
            path=str(archive),
            dest=str(dest_root),
            code="extract_failed",
            message=str(exc),
        )

    if result.ok:
        hook = classify_hook or after_extract_classify_hook
        try:
            hook(
                [Path(p) for p in result.extracted_paths],
                archive=archive,
                source_url=source_url,
                metadata=metadata,
                rules=None,
            )
        except Exception:  # noqa: BLE001
            # classification is best-effort never fail extract
            pass
        if emit is not None:
            emit.extracted(
                job=job or "",
                file=file,
                path=str(archive),
                dest=result.dest,
                members=result.members,
                deleted_archive=result.deleted_archive,
                message=result.message,
            )
            emit.postprocess(
                job=job or "",
                step="extracted",
                file=file,
                path=result.dest or str(archive),
                role="extract",
                message=result.message or "extracted",
                ok=True,
            )
        return result

    if emit is not None:
        emit.failed(
            job=job or "",
            file=file,
            path=str(archive),
            dest=result.dest,
            code=result.code,
            message=result.message,
            phase="extract",
        )
        emit.postprocess(
            job=job or "",
            step="failed",
            file=file,
            path=str(archive),
            role="extract",
            message=result.message or "extract failed",
            ok=False,
            code=result.code,
        )
        emit.error(
            job=job,
            file=file,
            message=result.message or "extract failed",
            code=result.code or "extract_failed",
        )
    return result


def _extract_safe(
    archive: Path,
    dest_root: Path,
    *,
    options: extract_options,
    password: str | None,
) -> extract_result:
    if not archive.is_file():
        return extract_result(
            ok=False,
            path=str(archive),
            dest=str(dest_root),
            code="extract_failed",
            message="archive missing",
        )

    try:
        zf = zipfile.ZipFile(archive, "r")
    except zipfile.BadZipFile:
        return extract_result(
            ok=False,
            path=str(archive),
            dest=str(dest_root),
            code="extract_failed",
            message="not a valid zip archive",
        )

    pwd_bytes = password.encode("utf-8") if password else None
    extracted: list[str] = []
    total_uncompressed = 0
    total_compressed = 0

    with zf:
        infos = zf.infolist()
        # pre-scan bomb / slip / symlink / encryption
        encrypted = False
        for info in infos:
            name = decode_zip_filename(info)
            if _is_unsafe_member_name(name):
                return extract_result(
                    ok=False,
                    path=str(archive),
                    dest=str(dest_root),
                    code="zip_slip",
                    message=f"unsafe zip member path: {name}",
                )
            if _is_symlink_member(info):
                return extract_result(
                    ok=False,
                    path=str(archive),
                    dest=str(dest_root),
                    code="zip_symlink",
                    message=f"symlink members are not allowed: {name}",
                )
            if info.flag_bits & 0x1:
                encrypted = True
            total_uncompressed += max(0, int(info.file_size))
            total_compressed += max(0, int(info.compress_size))
            if info.compress_size > 0:
                ratio = float(info.file_size) / float(info.compress_size)
                if ratio > options.max_ratio:
                    return extract_result(
                        ok=False,
                        path=str(archive),
                        dest=str(dest_root),
                        code="zip_bomb",
                        message=(
                            f"member compression ratio {ratio:.1f} exceeds "
                            f"limit {options.max_ratio}"
                        ),
                    )

        if total_uncompressed > options.max_uncompressed:
            return extract_result(
                ok=False,
                path=str(archive),
                dest=str(dest_root),
                code="zip_bomb",
                message=(
                    f"total uncompressed size {total_uncompressed} exceeds "
                    f"limit {options.max_uncompressed}"
                ),
            )
        if total_compressed > 0:
            overall = float(total_uncompressed) / float(total_compressed)
            if overall > options.max_ratio:
                return extract_result(
                    ok=False,
                    path=str(archive),
                    dest=str(dest_root),
                    code="zip_bomb",
                    message=(
                        f"archive compression ratio {overall:.1f} exceeds "
                        f"limit {options.max_ratio}"
                    ),
                )

        if encrypted and not pwd_bytes:
            return extract_result(
                ok=False,
                path=str(archive),
                dest=str(dest_root),
                code="needs_password",
                message="zip is password-protected",
            )

        dest_root.mkdir(parents=True, exist_ok=True)
        dest_resolved = dest_root.resolve()

        for info in infos:
            name = decode_zip_filename(info)
            if name.endswith("/"):
                target = dest_resolved / name.rstrip("/")
                if not _is_within_dir(dest_resolved, target):
                    return extract_result(
                        ok=False,
                        path=str(archive),
                        dest=str(dest_root),
                        code="zip_slip",
                        message=f"zip-slip blocked: {name}",
                    )
                target.mkdir(parents=True, exist_ok=True)
                continue

            target = dest_resolved / name
            if not _is_within_dir(dest_resolved, target):
                return extract_result(
                    ok=False,
                    path=str(archive),
                    dest=str(dest_root),
                    code="zip_slip",
                    message=f"zip-slip blocked: {name}",
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info, "r", pwd=pwd_bytes) as src, target.open("wb") as out:
                    written = 0
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > options.max_uncompressed:
                            return extract_result(
                                ok=False,
                                path=str(archive),
                                dest=str(dest_root),
                                code="zip_bomb",
                                message="uncompressed write exceeded limit",
                            )
                        out.write(chunk)
            except RuntimeError as exc:
                # zipfile raises RuntimeError for bad password
                msg = str(exc).lower()
                if "password" in msg or "encrypted" in msg:
                    return extract_result(
                        ok=False,
                        path=str(archive),
                        dest=str(dest_root),
                        code="needs_password",
                        message="zip password required or incorrect",
                    )
                return extract_result(
                    ok=False,
                    path=str(archive),
                    dest=str(dest_root),
                    code="extract_failed",
                    message=str(exc),
                )
            except zipfile.BadZipFile as exc:
                return extract_result(
                    ok=False,
                    path=str(archive),
                    dest=str(dest_root),
                    code="extract_failed",
                    message=str(exc),
                )
            extracted.append(str(target))

    deleted = False
    if options.delete_zip and extracted:
        try:
            archive.unlink()
            deleted = True
        except OSError:
            deleted = False

    return extract_result(
        ok=True,
        path=str(archive),
        dest=str(dest_root),
        members=len(extracted),
        deleted_archive=deleted,
        message=f"extracted {len(extracted)} files",
        extracted_paths=extracted,
    )


def _is_within_dir(root: Path, target: Path) -> bool:
    try:
        root_r = root.resolve()
        target_r = target.resolve()
    except OSError:
        return False
    try:
        target_r.relative_to(root_r)
        return True
    except ValueError:
        return False


def retry_extract_path(
    archive: str | Path,
    options: extract_options | None = None,
    *,
    password: str | None = None,
    emit: emitter | None = None,
    job: str | None = None,
    file: str | None = None,
) -> extract_result:
    """re-run extraction for repair center / GUI retry."""
    opts = options or extract_options(enabled=True, override="on")
    opts = extract_options(
        enabled=True,
        override="on",
        destination=opts.destination,
        delete_zip=opts.delete_zip,
        max_uncompressed=opts.max_uncompressed,
        max_ratio=opts.max_ratio,
        password=password if password is not None else opts.password,
        use_secret_password=opts.use_secret_password if password is None else False,
    )
    result = extract_zip(
        archive,
        opts,
        emit=emit,
        job=job,
        file=file,
    )
    if result is None:
        return extract_result(
            ok=False,
            path=str(archive),
            code="extract_failed",
            message="not a zip archive",
        )
    return result
