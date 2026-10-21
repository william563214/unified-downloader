"""ordered postprocessor chain inspired by yt-dlp postprocessor (Unlicense).

Steps (configurable order):
  checksum | av | extract | nfo | cover | move | exec

Default order preserves prior behaviour: checksum -> av -> extract -> nfo -> cover
then move / exec. ZIP extract and classification hooks stay integrated.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

from udl.checksum import (
    checksum_result,
    expected_checksum,
    resolve_expected,
    verify_file,
)
from udl.events import emitter
from udl.extractors.base import extract_result, extracted_file
from udl.metadata import item_metadata
from udl.naming import detect_media_type, resolve_output_path
from udl.postprocess.artifacts import artifact_result, run_postprocess
from udl.postprocess.hook import hook_options, hook_result, run_hook
from udl.postprocess.options import postprocess_options
from udl.postprocess.zip_extract import extract_options, extract_zip
from udl.postprocess.zip_extract import extract_result as zip_extract_result
from udl.rules import apply_rules, classify_rule

# lowercase constants per project convention
known_pp_steps = (
    "checksum",
    "av",
    "extract",
    "nfo",
    "cover",
    "move",
    "exec",
)
default_pp_order = [
    "checksum",
    "av",
    "extract",
    "nfo",
    "cover",
    "move",
    "exec",
]
# aliases accepted in config / CLI
_pp_aliases: dict[str, str] = {
    "hook": "av",
    "scan": "av",
    "antivirus": "av",
    "av_scan": "av",
    "post_cmd": "av",
    "zip": "extract",
    "unzip": "extract",
    "zip_extract": "extract",
    "auto_extract": "extract",
    "write_nfo": "nfo",
    "write_cover": "cover",
    "movefiles": "move",
    "classify": "move",
    "exec_cmd": "exec",
    "execute": "exec",
}


@dataclass
class exec_options:
    """general post-download exec (separate from AV scan hook)."""

    enabled: bool = False
    command: str = ""
    timeout: int = 120

    def resolved_command(self) -> str | None:
        if not self.enabled:
            return None
        text = (self.command or "").strip()
        return text or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "command": self.command,
            "timeout": self.timeout,
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> exec_options:
        if not data:
            return cls()
        try:
            timeout = int(data.get("timeout") or data.get("exec_timeout") or 120)
        except (TypeError, ValueError):
            timeout = 120
        return cls(
            enabled=bool(
                data.get("enabled")
                or data.get("exec_enabled")
                or data.get("execEnabled")
            ),
            command=str(
                data.get("command")
                or data.get("exec_cmd")
                or data.get("execCmd")
                or ""
            ),
            timeout=max(1, timeout),
        )


@dataclass
class chain_options:
    """runtime options for the ordered postprocessor chain."""

    order: list[str] = field(default_factory=lambda: list(default_pp_order))
    checksum_auto: bool = True
    cli_checksum: expected_checksum | None = None
    hook: hook_options = field(default_factory=hook_options)
    extract: extract_options = field(default_factory=extract_options)
    artifacts: postprocess_options = field(default_factory=postprocess_options)
    exec_opts: exec_options = field(default_factory=exec_options)
    move_template: str = ""
    rules: list[classify_rule] = field(default_factory=list)
    name_conflict: str = "rename"
    outtmpl_na_placeholder: str = ""

    def normalized_order(self) -> list[str]:
        return normalize_pp_order(self.order)


@dataclass
class chain_file_state:
    """mutable per-file state as PPs run."""

    index: int
    extracted: extracted_file
    path: Path
    file_id: str
    metadata: item_metadata
    ok: bool = True
    failure: dict[str, Any] | None = None
    artifacts: list[artifact_result] = field(default_factory=list)
    checksum: checksum_result | None = None
    hook: hook_result | None = None
    extract: zip_extract_result | None = None
    moved_from: str | None = None


@dataclass
class chain_result:
    file_failures: dict[int, dict[str, Any]]
    checksum_results: list[checksum_result]
    artifacts: list[artifact_result]
    path_overrides: dict[int, Path]


def normalize_pp_order(raw: list[str] | str | None) -> list[str]:
    """normalize a user order list; append any missing known steps at the end."""
    if raw is None:
        return list(default_pp_order)
    if isinstance(raw, str):
        tokens = [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]
    else:
        tokens = [str(t).strip() for t in raw if str(t).strip()]
    seen: list[str] = []
    for token in tokens:
        key = _pp_aliases.get(token.lower(), token.lower())
        if key not in known_pp_steps:
            continue
        if key not in seen:
            seen.append(key)
    for step in default_pp_order:
        if step not in seen:
            seen.append(step)
    return seen


def parse_pp_order(raw: str | list[str] | None) -> list[str]:
    return normalize_pp_order(raw)


def run_postprocessor_chain(
    result: extract_result,
    output_dir: Path,
    opts: chain_options,
    *,
    job_id: str,
    emit: emitter | None = None,
    conn: sqlite3.Connection | None = None,
    cancel: Event | None = None,
    skip_indexes: set[int] | None = None,
    file_dest_overrides: dict[int, Path] | None = None,
    kind_hint: str | None = None,
) -> chain_result:
    """run enabled postprocessors in configured order for each downloaded file."""
    skip_indexes = skip_indexes or set()
    overrides = dict(file_dest_overrides or {})
    order = opts.normalized_order()
    states = _build_states(
        result,
        output_dir,
        job_id=job_id,
        skip_indexes=skip_indexes,
        overrides=overrides,
    )
    checksum_results: list[checksum_result] = []
    all_artifacts: list[artifact_result] = []

    if emit is not None:
        emit.postprocess(
            job=job_id,
            step="chain",
            message=f"postprocessor order: {','.join(order)}",
            ok=True,
        )

    for step in order:
        if cancel is not None and cancel.is_set():
            break
        if step == "checksum":
            checksum_results.extend(
                _run_checksum_step(states, opts, job_id=job_id, emit=emit)
            )
        elif step == "av":
            _run_av_step(states, opts, job_id=job_id, emit=emit)
        elif step == "extract":
            _run_extract_step(
                states,
                opts,
                result=result,
                output_dir=output_dir,
                job_id=job_id,
                emit=emit,
            )
        elif step == "nfo":
            arts = _run_artifact_step(
                result,
                output_dir,
                opts,
                job_id=job_id,
                emit=emit,
                conn=conn,
                cancel=cancel,
                skip_indexes=skip_indexes,
                overrides=_states_to_overrides(states, overrides),
                kind_hint=kind_hint,
                only="nfo",
                failed_indexes={s.index for s in states if not s.ok},
            )
            all_artifacts.extend(arts)
            _attach_artifacts(states, arts)
        elif step == "cover":
            arts = _run_artifact_step(
                result,
                output_dir,
                opts,
                job_id=job_id,
                emit=emit,
                conn=conn,
                cancel=cancel,
                skip_indexes=skip_indexes,
                overrides=_states_to_overrides(states, overrides),
                kind_hint=kind_hint,
                only="cover",
                failed_indexes={s.index for s in states if not s.ok},
            )
            all_artifacts.extend(arts)
            _attach_artifacts(states, arts)
        elif step == "move":
            _run_move_step(
                states,
                opts,
                output_dir=output_dir,
                job_id=job_id,
                emit=emit,
                source_url=result.url,
            )
        elif step == "exec":
            _run_exec_step(states, opts, job_id=job_id, emit=emit)

    failures: dict[int, dict[str, Any]] = {}
    path_overrides: dict[int, Path] = dict(overrides)
    for state in states:
        path_overrides[state.index] = state.path
        if state.failure is not None:
            failures[state.index] = state.failure
    return chain_result(
        file_failures=failures,
        checksum_results=checksum_results,
        artifacts=all_artifacts,
        path_overrides=path_overrides,
    )


def _build_states(
    result: extract_result,
    output_dir: Path,
    *,
    job_id: str,
    skip_indexes: set[int],
    overrides: dict[int, Path],
) -> list[chain_file_state]:
    states: list[chain_file_state] = []
    base_meta = result.metadata or item_metadata(site=result.site, title=result.title)
    for idx, extracted in enumerate(result.files):
        if idx in skip_indexes:
            continue
        dest_dir = output_dir / extracted.relative_dir if extracted.relative_dir else output_dir
        dest = overrides.get(idx, dest_dir / extracted.name)
        if not dest.is_file():
            continue
        meta = extracted.metadata or base_meta
        states.append(
            chain_file_state(
                index=idx,
                extracted=extracted,
                path=dest,
                file_id=f"{job_id}:{idx}",
                metadata=meta,
            )
        )
    return states


def _states_to_overrides(
    states: list[chain_file_state],
    base: dict[int, Path],
) -> dict[int, Path]:
    out = dict(base)
    for state in states:
        out[state.index] = state.path
    return out


def _mark_failure(state: chain_file_state, payload: dict[str, Any]) -> None:
    state.ok = False
    if state.failure is None:
        state.failure = payload
    else:
        state.failure.update(payload)
        if payload.get("message"):
            state.failure["message"] = payload["message"]


def _run_checksum_step(
    states: list[chain_file_state],
    opts: chain_options,
    *,
    job_id: str,
    emit: emitter | None,
) -> list[checksum_result]:
    results: list[checksum_result] = []
    for state in states:
        if not state.ok:
            continue
        expected = resolve_expected(
            path=state.path,
            metadata=state.metadata,
            cli=opts.cli_checksum,
            auto=opts.checksum_auto,
        )
        if expected is None:
            continue
        checked = verify_file(state.path, expected)
        state.checksum = checked
        results.append(checked)
        if emit is not None:
            emit.checksum(
                job=job_id,
                file=state.file_id,
                ok=checked.ok,
                algo=checked.algo,
                expected=checked.expected,
                actual=checked.actual,
                source=checked.source,
                path=checked.path,
                message=checked.message,
            )
        if not checked.ok:
            _mark_failure(
                state,
                {
                    "algo": checked.algo,
                    "expected": checked.expected,
                    "actual": checked.actual,
                    "source": checked.source,
                    "message": checked.message or "checksum mismatch",
                    "checksum": {
                        "ok": False,
                        "algo": checked.algo,
                        "expected": checked.expected,
                        "actual": checked.actual,
                        "source": checked.source,
                        "message": checked.message or "checksum mismatch",
                    },
                },
            )
            if emit is not None:
                emit.error(
                    job=job_id,
                    file=state.file_id,
                    message=checked.message or "checksum mismatch",
                    code="checksum_mismatch",
                )
    return results


def _run_av_step(
    states: list[chain_file_state],
    opts: chain_options,
    *,
    job_id: str,
    emit: emitter | None,
) -> None:
    for state in states:
        if not state.ok:
            continue
        hook_res = run_hook(
            state.path,
            opts.hook,
            emit=emit,
            job=job_id,
            file=state.file_id,
        )
        state.hook = hook_res
        if hook_res is not None and not hook_res.ok:
            _mark_failure(
                state,
                {
                    "message": hook_res.message or "post-download hook failed",
                    "hook": hook_res.to_dict(),
                },
            )
            if emit is not None:
                emit.error(
                    job=job_id,
                    file=state.file_id,
                    message=hook_res.message or "post-download hook failed",
                    code="hook_failed",
                )


def _run_extract_step(
    states: list[chain_file_state],
    opts: chain_options,
    *,
    result: extract_result,
    output_dir: Path,
    job_id: str,
    emit: emitter | None,
) -> None:
    for state in states:
        if not state.ok:
            continue
        meta_dict = state.metadata.to_dict(omit_empty=True)
        extract_res = extract_zip(
            state.path,
            opts.extract,
            emit=emit,
            job=job_id,
            file=state.file_id,
            source_url=result.url,
            metadata=meta_dict,
            rules=opts.rules or None,
            classify_root=output_dir,
            move_template=opts.move_template or None,
            name_conflict=opts.name_conflict,
            na_placeholder=opts.outtmpl_na_placeholder,
        )
        state.extract = extract_res
        if extract_res is None:
            continue
        if not extract_res.ok:
            _mark_failure(
                state,
                {
                    "message": extract_res.message or "extract failed",
                    "extract": extract_res.to_dict(),
                },
            )
            continue
        state.metadata.extra["extract"] = extract_res.to_dict()


def _run_artifact_step(
    result: extract_result,
    output_dir: Path,
    opts: chain_options,
    *,
    job_id: str,
    emit: emitter | None,
    conn: sqlite3.Connection | None,
    cancel: Event | None,
    skip_indexes: set[int],
    overrides: dict[int, Path],
    kind_hint: str | None,
    only: str,
    failed_indexes: set[int] | None = None,
) -> list[artifact_result]:
    art_opts = opts.artifacts
    if only == "nfo":
        if not art_opts.write_nfo:
            return []
        scoped = postprocess_options(
            write_nfo=True,
            write_cover=False,
            embed_cover=False,
            cover_naming=art_opts.cover_naming,
            nfo_sites=dict(art_opts.nfo_sites),
            cover_sites=dict(art_opts.cover_sites),
            ffmpeg=art_opts.ffmpeg,
        )
    else:
        if not (art_opts.write_cover or art_opts.embed_cover):
            return []
        scoped = postprocess_options(
            write_nfo=False,
            write_cover=art_opts.write_cover,
            embed_cover=art_opts.embed_cover,
            cover_naming=art_opts.cover_naming,
            nfo_sites=dict(art_opts.nfo_sites),
            cover_sites=dict(art_opts.cover_sites),
            ffmpeg=art_opts.ffmpeg,
        )
    return run_postprocess(
        result,
        output_dir,
        scoped,
        job_id=job_id,
        emit=emit,
        conn=conn,
        cancel=cancel,
        skip_indexes=skip_indexes,
        exclude_indexes=set(failed_indexes or ()),
        file_dest_overrides=overrides,
        kind_hint=kind_hint,
    )


def _attach_artifacts(
    states: list[chain_file_state],
    arts: list[artifact_result],
) -> None:
    by_media: dict[str, list[artifact_result]] = {}
    for art in arts:
        if art.media_file_id:
            by_media.setdefault(art.media_file_id, []).append(art)
    for state in states:
        state.artifacts.extend(by_media.get(state.file_id, []))


def _run_move_step(
    states: list[chain_file_state],
    opts: chain_options,
    *,
    output_dir: Path,
    job_id: str,
    emit: emitter | None,
    source_url: str | None = None,
) -> None:
    for state in states:
        if not state.ok:
            continue
        target = _resolve_move_target(
            state,
            opts,
            output_dir=output_dir,
            source_url=source_url,
        )
        if target is None or target.resolve() == state.path.resolve():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if opts.name_conflict == "error":
                    raise FileExistsError(str(target))
                if opts.name_conflict == "rename":
                    from udl.naming import unique_path

                    target = unique_path(target)
                else:
                    target.unlink()
            old = state.path
            shutil.move(str(old), str(target))
            state.moved_from = str(old)
            state.path = target
            # move sidecar artifacts that share the old stem in the same dir
            _move_sidecars(old, target)
            if emit is not None:
                emit.postprocess(
                    job=job_id,
                    step="move",
                    file=state.file_id,
                    path=str(target),
                    role="move",
                    message=f"moved from {old.name}",
                    ok=True,
                )
        except Exception as exc:  # noqa: BLE001
            _mark_failure(
                state,
                {
                    "message": f"move failed: {exc}",
                    "move": {"from": str(state.path), "error": str(exc)},
                },
            )
            if emit is not None:
                emit.postprocess(
                    job=job_id,
                    step="move",
                    file=state.file_id,
                    path=str(state.path),
                    role="move",
                    message=str(exc),
                    ok=False,
                    code="move_failed",
                )
                emit.error(
                    job=job_id,
                    file=state.file_id,
                    message=f"move failed: {exc}",
                    code="move_failed",
                )


def _resolve_move_target(
    state: chain_file_state,
    opts: chain_options,
    *,
    output_dir: Path,
    source_url: str | None = None,
) -> Path | None:
    media_type = detect_media_type(state.path.name)
    url = source_url or state.metadata.canonical_url
    decision = apply_rules(
        opts.rules,
        state.metadata,
        size=state.path.stat().st_size if state.path.is_file() else None,
        media_type=media_type,
        original_name=state.path.name,
        source_url=url,
        download_url=url,
    )
    template = (opts.move_template or "").strip() or (decision.template or "")
    folder = decision.folder
    if not template and not folder:
        return None
    rendered = resolve_output_path(
        output_dir,
        original_name=state.path.name,
        relative_dir="",
        metadata=state.metadata,
        template=template or None,
        folder_override=folder,
        size=state.path.stat().st_size if state.path.is_file() else None,
        media_type=media_type,
        on_conflict=opts.name_conflict,
        na_placeholder=opts.outtmpl_na_placeholder,
    )
    return rendered.absolute


def _move_sidecars(old: Path, new: Path) -> None:
    """move common sidecars that sat next to the media file."""
    stem = old.stem
    parent = old.parent
    new_parent = new.parent
    candidates = [
        parent / f"{stem}.nfo",
        parent / f"{stem}-poster.jpg",
        parent / f"{stem}-poster.png",
        parent / f"{stem}-poster.webp",
    ]
    for src in candidates:
        if not src.is_file():
            continue
        dest = new_parent / src.name
        try:
            if dest.exists():
                continue
            shutil.move(str(src), str(dest))
        except OSError:
            continue


def _run_exec_step(
    states: list[chain_file_state],
    opts: chain_options,
    *,
    job_id: str,
    emit: emitter | None,
) -> None:
    command = opts.exec_opts.resolved_command()
    if not command:
        return
    # reuse hook runner with a synthetic hook_options
    synthetic = hook_options(
        enabled=True,
        command=command,
        preset=None,
        timeout=opts.exec_opts.timeout,
    )
    for state in states:
        if not state.ok:
            continue
        if emit is not None:
            emit.postprocess(
                job=job_id,
                step="exec",
                file=state.file_id,
                path=str(state.path),
                role="exec",
                message="running exec",
            )
        # temporarily tag events as exec via postprocess after hook
        hook_res = run_hook(
            state.path,
            synthetic,
            emit=None,
            job=job_id,
            file=state.file_id,
        )
        if hook_res is None:
            continue
        if emit is not None:
            emit.postprocess(
                job=job_id,
                step="exec",
                file=state.file_id,
                path=str(state.path),
                role="exec",
                message=hook_res.message or ("exec ok" if hook_res.ok else "exec failed"),
                ok=hook_res.ok,
                code=None if hook_res.ok else "exec_failed",
            )
            emit.emit(
                "exec",
                job=job_id,
                file=state.file_id,
                path=str(state.path),
                ok=hook_res.ok,
                command=hook_res.command,
                exit_code=hook_res.exit_code,
                duration_ms=hook_res.duration_ms,
                timed_out=hook_res.timed_out,
                message=hook_res.message,
            )
        if not hook_res.ok:
            _mark_failure(
                state,
                {
                    "message": hook_res.message or "exec failed",
                    "exec": hook_res.to_dict(),
                },
            )
            if emit is not None:
                emit.error(
                    job=job_id,
                    file=state.file_id,
                    message=hook_res.message or "exec failed",
                    code="exec_failed",
                )


def classify_and_move_paths(
    paths: list[Path],
    *,
    metadata: dict[str, Any] | item_metadata | None,
    rules: list[classify_rule] | None,
    move_template: str | None = None,
    output_root: Path | None = None,
    name_conflict: str = "rename",
    na_placeholder: str = "",
    source_url: str | None = None,
) -> list[tuple[Path, Path]]:
    """apply classification / move_template to a list of paths.

    returns list of (old, new) for files that moved.
    """
    if not paths:
        return []
    if not rules and not (move_template or "").strip():
        return []
    meta = (
        metadata
        if isinstance(metadata, item_metadata)
        else item_metadata.from_dict(metadata if isinstance(metadata, dict) else None)
    )
    url = source_url or meta.canonical_url
    moved: list[tuple[Path, Path]] = []
    for path in paths:
        if not path.is_file():
            continue
        root = output_root or path.parent
        media_type = detect_media_type(path.name)
        decision = apply_rules(
            rules or [],
            meta,
            size=path.stat().st_size,
            media_type=media_type,
            original_name=path.name,
            source_url=url,
            download_url=url,
        )
        template = (move_template or "").strip() or (decision.template or "")
        folder = decision.folder
        if not template and not folder:
            continue
        rendered = resolve_output_path(
            root,
            original_name=path.name,
            relative_dir="",
            metadata=meta,
            template=template or None,
            folder_override=folder,
            size=path.stat().st_size,
            media_type=media_type,
            on_conflict=name_conflict,
            na_placeholder=na_placeholder,
        )
        target = rendered.absolute
        if target.resolve() == path.resolve():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and name_conflict == "overwrite":
                target.unlink()
            elif target.exists() and name_conflict == "rename":
                from udl.naming import unique_path

                target = unique_path(target)
            elif target.exists():
                continue
            shutil.move(str(path), str(target))
            moved.append((path, target))
        except OSError:
            continue
    return moved

