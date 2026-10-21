"""download session helpers: history queue duplicates naming rules around extractors."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from udl.checksum import (
    checksum_result,
    expected_checksum,
    resolve_expected,
    verify_file,
)
from udl.config import load_config, udl_config
from udl.events import emitter
from udl.extractors.base import extract_result
from udl.metadata import item_metadata
from udl.naming import detect_media_type, pick_template, resolve_output_path
from udl.postprocess.hook import hook_options, run_hook
from udl.postprocess.zip_extract import extract_options, extract_zip
from udl.rules import apply_rules
from udl.store.db import connect
from udl.store.duplicates import duplicate_mode, resolve_duplicate
from udl.store.hashutil import hash_file_for_history
from udl.store.history import record_file_done, record_job_finished, record_job_started
from udl.store.queue import queue_job, save_queue_job, update_queue_status


def open_store(db_path: str | Path | None = None) -> sqlite3.Connection:
    return connect(db_path)


def plan_file_destinations(
    result: extract_result,
    output_dir: Path,
    *,
    config: udl_config | None = None,
    name_template: str | None = None,
    on_duplicate: duplicate_mode = "skip",
    source_url: str | None = None,
) -> tuple[set[int], dict[int, Path], dict[int, Any]]:
    """apply classification rules + naming templates without touching the DB.

    returns (rule_skip_indexes, dest_overrides, per_index_decision_dict)
    """
    cfg = config if config is not None else load_config()
    meta = result.metadata or item_metadata(site=result.site, title=result.title)
    job_url = source_url or getattr(result, "url", None) or meta.canonical_url
    rules = cfg.effective_rules()
    skip_indexes: set[int] = set()
    overrides: dict[int, Path] = {}
    decisions: dict[int, Any] = {}
    for idx, f in enumerate(result.files):
        file_meta = f.metadata or meta
        media_type = detect_media_type(f.name)
        file_url = getattr(f, "url", None) or job_url
        decision = apply_rules(
            rules,
            file_meta,
            size=f.size,
            media_type=media_type,
            original_name=f.name,
            source_url=job_url,
            download_url=file_url,
        )
        decisions[idx] = decision
        if decision.skip:
            skip_indexes.add(idx)
            continue
        template = decision.template or pick_template(
            cli_template=name_template,
            site=result.site,
            media_type=media_type,
            config_default=cfg.name_template,
            site_templates=cfg.site_templates,
            type_templates=cfg.type_templates,
        )
        conflict = "overwrite" if on_duplicate == "overwrite" else cfg.name_conflict
        rendered = resolve_output_path(
            output_dir,
            original_name=f.name,
            relative_dir=f.relative_dir,
            metadata=file_meta,
            template=template or None,
            folder_override=decision.folder,
            size=f.size,
            media_type=media_type,
            on_conflict=conflict,
        )
        if template or decision.folder or rendered.conflict_renamed:
            overrides[idx] = rendered.absolute
    return skip_indexes, overrides, decisions


def prepare_download(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    url: str,
    output_dir: Path,
    result: extract_result,
    on_duplicate: duplicate_mode = "skip",
    emit: emitter | None = None,
    quality: str | None = None,
    part: str | None = None,
    cloudflare_bypass: bool = False,
    workers: int | None = None,
    ffmpeg: str | None = None,
    config: udl_config | None = None,
    name_template: str | None = None,
) -> tuple[set[int], dict[int, Path], bool]:
    """record job start apply rules naming duplicate policy.

    returns skip set dest overrides all-skipped.
    """
    cfg = config if config is not None else load_config()
    meta = result.metadata or item_metadata(site=result.site, title=result.title)
    if meta.canonical_url is None:
        meta.canonical_url = url
    if meta.title is None:
        meta.title = result.title

    save_queue_job(
        conn,
        queue_job(
            id=job_id,
            url=url,
            output_dir=str(output_dir),
            status="running",
            site=result.site,
            title=result.title,
            quality=quality,
            part=part,
            cloudflare_bypass=cloudflare_bypass,
            workers=workers,
            ffmpeg=ffmpeg,
            on_duplicate=on_duplicate,
            metadata=meta.to_dict(omit_empty=True),
            options={
                "name_template": name_template or cfg.name_template,
            },
        ),
        metadata=meta,
    )
    record_job_started(
        conn,
        job_id=job_id,
        site=result.site,
        source_url=url,
        output_dir=output_dir,
        metadata=meta,
        status="running",
    )

    rule_skips, overrides, decisions = plan_file_destinations(
        result,
        output_dir,
        config=cfg,
        name_template=name_template,
        on_duplicate=on_duplicate,
        source_url=url,
    )
    skip_indexes: set[int] = set(rule_skips)

    for idx, f in enumerate(result.files):
        file_meta = f.metadata or meta
        decision = decisions.get(idx)
        if idx in rule_skips:
            if emit and decision is not None:
                emit.duplicate(
                    job=job_id,
                    file=f"{job_id}:{idx}",
                    action="skip",
                    message=decision.message or "skipped by classification rule",
                    path=None,
                    matched_by=f"rule:{decision.rule_id}",
                    already_downloaded=False,
                    metadata=file_meta.to_dict(omit_empty=True) if file_meta else None,
                )
            record_file_done(
                conn,
                job_id=job_id,
                site=result.site,
                file_id=f"{job_id}:{idx}",
                name=f.name,
                path=None,
                status="skipped",
                item_id=file_meta.item_id if file_meta else None,
                canonical_url=file_meta.canonical_url if file_meta else None,
                size=f.size,
                metadata=file_meta,
                compute_hash=False,
            )
            continue

        dest_dir = output_dir / f.relative_dir if f.relative_dir else output_dir
        dest = overrides.get(idx, dest_dir / f.name)

        action = resolve_duplicate(
            conn,
            site=result.site,
            dest=dest,
            mode=on_duplicate,
            metadata=file_meta,
            item_id=file_meta.item_id if file_meta else None,
            canonical_url=file_meta.canonical_url if file_meta else None,
            size=f.size,
        )
        if action.dest is not None and action.dest != dest:
            overrides[idx] = action.dest
            dest = action.dest
        matched_rule = decision.matched if decision is not None else False
        should_emit = (
            action.hit is not None
            or action.already_downloaded
            or action.action == "rename"
            or matched_rule
            or idx in overrides
        )
        if emit and should_emit:
            message = action.message
            if decision is not None and decision.matched and decision.message:
                message = f"{decision.message}; {message}" if message else decision.message
            emit.duplicate(
                job=job_id,
                file=f"{job_id}:{idx}",
                action=action.action,
                message=message,
                path=str(dest),
                matched_by=action.hit.matched_by if action.hit else (
                    f"rule:{decision.rule_id}" if matched_rule and decision else None
                ),
                already_downloaded=action.already_downloaded,
                metadata=file_meta.to_dict(omit_empty=True) if file_meta else None,
            )
        if action.action == "skip" and action.already_downloaded:
            skip_indexes.add(idx)
            record_file_done(
                conn,
                job_id=job_id,
                site=result.site,
                file_id=f"{job_id}:{idx}",
                name=f.name,
                path=action.hit.path if action.hit and action.hit.path else dest,
                status="skipped",
                item_id=file_meta.item_id if file_meta else None,
                canonical_url=file_meta.canonical_url if file_meta else None,
                size=f.size,
                metadata=file_meta,
                compute_hash=False,
            )

    all_skipped = bool(result.files) and len(skip_indexes) == len(result.files)
    return skip_indexes, overrides, all_skipped


def finalize_download(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    result: extract_result,
    output_dir: Path,
    ok: int,
    failed: int,
    cancelled: bool = False,
    error_message: str | None = None,
    skip_indexes: set[int] | None = None,
    file_dest_overrides: dict[int, Path] | None = None,
    file_failures: dict[int, dict[str, Any]] | None = None,
) -> None:
    skip_indexes = skip_indexes or set()
    file_dest_overrides = file_dest_overrides or {}
    file_failures = file_failures or {}
    for idx, f in enumerate(result.files):
        if idx in skip_indexes:
            continue
        dest_dir = output_dir / f.relative_dir if f.relative_dir else output_dir
        dest = file_dest_overrides.get(idx, dest_dir / f.name)
        file_meta = f.metadata or result.metadata
        failure = file_failures.get(idx)
        if failure:
            # store expected digest in sha256 so repair verify_hash surfaces mismatch
            expected_hex = failure.get("expected")
            algo = failure.get("algo") or "sha256"
            meta_payload: dict[str, Any]
            if file_meta is not None:
                meta_payload = file_meta.to_dict(omit_empty=True)
            else:
                meta_payload = {"site": result.site}
            extra = dict(meta_payload.get("extra") or {})
            if failure.get("checksum") or failure.get("expected") is not None:
                extra["checksum"] = failure.get("checksum") or {
                    "ok": False,
                    "algo": algo,
                    "expected": expected_hex,
                    "actual": failure.get("actual"),
                    "source": failure.get("source"),
                    "message": failure.get("message"),
                }
            if failure.get("hook"):
                extra["hook"] = failure["hook"]
            if failure.get("extract"):
                extra["extract"] = failure["extract"]
            meta_payload["extra"] = extra
            record_file_done(
                conn,
                job_id=job_id,
                site=result.site,
                file_id=f"{job_id}:{idx}",
                name=f.name,
                path=dest,
                status="error",
                item_id=file_meta.item_id if file_meta else None,
                canonical_url=file_meta.canonical_url if file_meta else None,
                sha256=expected_hex if algo == "sha256" else None,
                metadata=meta_payload,
                compute_hash=False,
            )
        elif dest.exists() and dest.is_file():
            record_file_done(
                conn,
                job_id=job_id,
                site=result.site,
                file_id=f"{job_id}:{idx}",
                name=f.name,
                path=dest,
                status="done",
                item_id=file_meta.item_id if file_meta else None,
                canonical_url=file_meta.canonical_url if file_meta else None,
                metadata=file_meta,
                compute_hash=True,
            )
        elif failed > 0:
            record_file_done(
                conn,
                job_id=job_id,
                site=result.site,
                file_id=f"{job_id}:{idx}",
                name=f.name,
                path=dest,
                status="error",
                item_id=file_meta.item_id if file_meta else None,
                canonical_url=file_meta.canonical_url if file_meta else None,
                metadata=file_meta,
                compute_hash=False,
            )

    if cancelled:
        status = "cancelled"
    elif failed == 0:
        status = "done" if ok > 0 else "skipped"
    else:
        status = "error"
    record_job_finished(
        conn,
        job_id=job_id,
        status=status,
        ok=ok,
        failed=failed,
        error_message=error_message,
    )
    update_queue_status(
        conn,
        job_id,
        status,
        error_message=error_message,
        title=result.title,
        site=result.site,
    )


def post_download_verify(
    result: extract_result,
    output_dir: Path,
    *,
    job_id: str,
    skip_indexes: set[int] | None = None,
    file_dest_overrides: dict[int, Path] | None = None,
    cli_checksum: expected_checksum | None = None,
    checksum_auto: bool = True,
    hook: hook_options | None = None,
    extract: extract_options | None = None,
    emit: emitter | None = None,
) -> tuple[dict[int, dict[str, Any]], list[checksum_result]]:
    """verify checksums run optional AV hook then optional ZIP extract.

    extract runs only when checksum and hook (if configured) pass for the file.
    returns (file_failures, checksum_results). failures keyed by file index.
    """
    skip_indexes = skip_indexes or set()
    file_dest_overrides = file_dest_overrides or {}
    hook = hook or hook_options()
    extract = extract or extract_options()
    failures: dict[int, dict[str, Any]] = {}
    results: list[checksum_result] = []
    classify_rules = load_config().effective_rules()

    for idx, f in enumerate(result.files):
        if idx in skip_indexes:
            continue
        dest_dir = output_dir / f.relative_dir if f.relative_dir else output_dir
        dest = file_dest_overrides.get(idx, dest_dir / f.name)
        if not dest.is_file():
            continue
        file_id = f"{job_id}:{idx}"
        file_meta = f.metadata or result.metadata
        file_ok = True
        expected = resolve_expected(
            path=dest,
            metadata=file_meta,
            cli=cli_checksum,
            auto=checksum_auto,
        )
        if expected is not None:
            checked = verify_file(dest, expected)
            results.append(checked)
            if emit is not None:
                emit.checksum(
                    job=job_id,
                    file=file_id,
                    ok=checked.ok,
                    algo=checked.algo,
                    expected=checked.expected,
                    actual=checked.actual,
                    source=checked.source,
                    path=checked.path,
                    message=checked.message,
                )
            if not checked.ok:
                file_ok = False
                failures[idx] = {
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
                }
                if emit is not None:
                    emit.error(
                        job=job_id,
                        file=file_id,
                        message=checked.message or "checksum mismatch",
                        code="checksum_mismatch",
                    )

        hook_res = run_hook(
            dest,
            hook,
            emit=emit,
            job=job_id,
            file=file_id,
        )
        if hook_res is not None and not hook_res.ok:
            file_ok = False
            entry = failures.setdefault(
                idx,
                {
                    "algo": expected.algo if expected else "sha256",
                    "expected": expected.hex if expected else None,
                    "actual": None,
                    "source": expected.source if expected else None,
                    "message": hook_res.message or "post-download hook failed",
                },
            )
            entry["hook"] = hook_res.to_dict()
            entry["message"] = hook_res.message or entry.get("message")
            if emit is not None:
                emit.error(
                    job=job_id,
                    file=file_id,
                    message=hook_res.message or "post-download hook failed",
                    code="hook_failed",
                )

        if not file_ok:
            continue

        meta_dict = file_meta.to_dict(omit_empty=True) if file_meta is not None else None
        extract_res = extract_zip(
            dest,
            extract,
            emit=emit,
            job=job_id,
            file=file_id,
            source_url=result.url,
            metadata=meta_dict,
            rules=classify_rules,
            classify_root=output_dir,
        )
        if extract_res is not None and not extract_res.ok:
            entry = failures.setdefault(
                idx,
                {
                    "algo": expected.algo if expected else "sha256",
                    "expected": expected.hex if expected else None,
                    "actual": None,
                    "source": expected.source if expected else None,
                    "message": extract_res.message or "extract failed",
                },
            )
            entry["extract"] = extract_res.to_dict()
            entry["message"] = extract_res.message or entry.get("message")
        elif extract_res is not None and extract_res.ok:
            if file_meta is not None:
                file_meta.extra["extract"] = extract_res.to_dict()
            elif f.metadata is None and result.metadata is not None:
                result.metadata.extra.setdefault("extract_by_file", {})[
                    file_id
                ] = extract_res.to_dict()

    return failures, results


def file_digest(path: Path) -> tuple[int | None, str | None]:
    if not path.is_file():
        return None, None
    return path.stat().st_size, hash_file_for_history(path)


def metadata_payload(result: extract_result) -> dict[str, Any] | None:
    if result.metadata is None:
        return None
    return result.metadata.to_dict(omit_empty=True)
