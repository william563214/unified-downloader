"""checksum verification after download (md5 / sha1 / sha256).

Sources for expected digests (first match wins unless CLI overrides):
  1. explicit CLI / job options (--checksum / --md5 / --sha1 / --sha256)
  2. site metadata.extra (md5 / sha1 / sha256 / hash)
  3. sibling sidecar files (.sha256 / .md5 / .sha1) next to the download
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from udl.events import emitter
from udl.metadata import item_metadata
from udl.store.hashutil import (
    detect_algo_from_hex,
    hash_file,
    normalize_hex,
    supported_algos,
)

# lowercase constants per project convention
sidecar_suffixes = (".sha256", ".md5", ".sha1", ".sha256sum", ".md5sum")
checksum_re = re.compile(
    r"^(?:(?P<algo>md5|sha1|sha256|sha-1|sha-256):)?(?P<hex>[0-9a-fA-F]{32,64})$"
)


@dataclass
class expected_checksum:
    algo: str
    hex: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {"algo": self.algo, "hex": self.hex, "source": self.source}


@dataclass
class checksum_result:
    ok: bool
    algo: str
    expected: str
    actual: str | None
    source: str
    path: str
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "algo": self.algo,
            "expected": self.expected,
            "actual": self.actual,
            "source": self.source,
            "path": self.path,
            "message": self.message,
        }


def parse_checksum_spec(raw: str | None) -> expected_checksum | None:
    """parse 'sha256:deadbeef' or bare hex."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    m = checksum_re.match(text)
    if not m:
        raise ValueError(f"invalid checksum spec: {raw}")
    algo_raw = (m.group("algo") or "").lower().replace("-", "")
    hex_value = normalize_hex(m.group("hex"))
    if not hex_value:
        raise ValueError(f"invalid checksum hex: {raw}")
    algo = algo_raw or detect_algo_from_hex(hex_value)
    if algo not in supported_algos:
        raise ValueError(f"unsupported checksum algorithm: {algo_raw or 'unknown'}")
    return expected_checksum(algo=algo, hex=hex_value, source="cli")


def expected_from_cli(
    *,
    checksum: str | None = None,
    md5: str | None = None,
    sha1: str | None = None,
    sha256: str | None = None,
) -> expected_checksum | None:
    if checksum:
        return parse_checksum_spec(checksum)
    for algo, value in (("sha256", sha256), ("sha1", sha1), ("md5", md5)):
        if value:
            hex_value = normalize_hex(value)
            if not hex_value:
                raise ValueError(f"invalid {algo} hex: {value}")
            return expected_checksum(algo=algo, hex=hex_value, source="cli")
    return None


def expected_from_metadata(meta: item_metadata | dict[str, Any] | None) -> expected_checksum | None:
    if meta is None:
        return None
    if isinstance(meta, item_metadata):
        extra = meta.extra or {}
    else:
        extra = meta.get("extra") if isinstance(meta.get("extra"), dict) else {}
        if not extra:
            # flat dict may carry hash keys directly
            extra = meta
    for key, algo in (
        ("sha256", "sha256"),
        ("sha_256", "sha256"),
        ("sha1", "sha1"),
        ("sha_1", "sha1"),
        ("md5", "md5"),
        ("hash", None),
    ):
        raw = extra.get(key)
        if not raw:
            continue
        hex_value = normalize_hex(str(raw))
        if not hex_value:
            continue
        resolved = algo or detect_algo_from_hex(hex_value)
        if resolved in supported_algos:
            return expected_checksum(algo=resolved, hex=hex_value, source="metadata")
    return None


def _parse_sidecar_text(text: str, filename: str | None) -> expected_checksum | None:
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # GNU coreutils: "hash  name" or "hash *name"
        parts = line.split()
        if not parts:
            continue
        hex_value = normalize_hex(parts[0])
        if not hex_value:
            continue
        if len(parts) >= 2 and filename:
            listed = parts[1].lstrip("*")
            if listed and Path(listed).name != Path(filename).name:
                continue
        algo = detect_algo_from_hex(hex_value)
        if algo:
            return expected_checksum(algo=algo, hex=hex_value, source="sidecar")
    return None


def expected_from_sidecar(path: str | Path) -> expected_checksum | None:
    target = Path(path)
    if not target.is_file():
        return None
    candidates = [
        target.with_suffix(target.suffix + ".sha256"),
        target.with_suffix(target.suffix + ".md5"),
        target.with_suffix(target.suffix + ".sha1"),
        Path(str(target) + ".sha256"),
        Path(str(target) + ".md5"),
        Path(str(target) + ".sha1"),
        target.parent / f"{target.name}.sha256",
        target.parent / f"{target.name}.md5",
        target.parent / f"{target.name}.sha1",
    ]
    for side in candidates:
        if not side.is_file():
            continue
        try:
            text = side.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # prefer algo from filename
        lower = side.name.lower()
        parsed = _parse_sidecar_text(text, target.name)
        if parsed:
            if ".md5" in lower:
                parsed.algo = "md5"
            elif ".sha1" in lower:
                parsed.algo = "sha1"
            elif ".sha256" in lower:
                parsed.algo = "sha256"
            parsed.source = f"sidecar:{side.name}"
            return parsed
    return None


def resolve_expected(
    *,
    path: str | Path | None = None,
    metadata: item_metadata | dict[str, Any] | None = None,
    cli: expected_checksum | None = None,
    auto: bool = True,
) -> expected_checksum | None:
    if cli is not None:
        return cli
    if not auto:
        return None
    from_meta = expected_from_metadata(metadata)
    if from_meta is not None:
        return from_meta
    if path is not None:
        return expected_from_sidecar(path)
    return None


def verify_file(
    path: str | Path,
    expected: expected_checksum,
) -> checksum_result:
    target = Path(path)
    actual = hash_file(target, expected.algo)
    if actual is None:
        return checksum_result(
            ok=False,
            algo=expected.algo,
            expected=expected.hex,
            actual=None,
            source=expected.source,
            path=str(target),
            message="file missing for checksum verify",
        )
    ok = actual.lower() == expected.hex.lower()
    return checksum_result(
        ok=ok,
        algo=expected.algo,
        expected=expected.hex,
        actual=actual,
        source=expected.source,
        path=str(target),
        message=None if ok else f"{expected.algo} mismatch",
    )


def emit_checksum(
    emit: emitter | None,
    *,
    job: str,
    file: str | None,
    result: checksum_result,
) -> None:
    if emit is None:
        return
    emit.emit(
        "checksum",
        job=job,
        file=file,
        ok=result.ok,
        algo=result.algo,
        expected=result.expected,
        actual=result.actual,
        source=result.source,
        path=result.path,
        message=result.message,
    )
