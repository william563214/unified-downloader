"""MEGA public-link extractor via external CLI (optional fallback).

Default MEGA path is the built-in client in `udl.extractors.mega`.
This module remains for `--mega-backend external` / settings.

Preferred tool: megatools (`megatools dl`) — single headless binary with
parseable progress on macOS Windows and Linux GPL-2.0 with OpenSSL exception
so it must never be imported or linked into this process.

Fallback: MEGAcmd (`mega-get`) — mostly BSD-2-Clause CLI wrappers; may need
a background mega-cmd-server. Progress is a TRANSFERRING bar that we parse.

Quota bypass SmartProxy IP rotation and megatools stealth mode are NOT
implemented or advertised.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from typing import IO
from urllib.parse import unquote, urlparse

from udl.events import emitter
from udl.extractors.base import extract_result, extracted_file
from udl.extractors.external.base import external_extractor
from udl.metadata import item_metadata
from udl.network import (
    active_network,
    build_proxy_url,
    effective_job_limit_bps,
    external_env,
    format_speed_kib,
    proxy_mode_custom,
    proxy_mode_none,
)

# lowercase constants per project convention
mega_tool_env = "UDL_MEGA_TOOL"
megatools_names = ("megatools", "megadl")
megacmd_names = ("mega-get",)

# modern + legacy public link shapes
re_file_modern = re.compile(
    r"(?:^|/)(?:file|embed)/(?P<handle>[A-Za-z0-9_-]+)(?:#(?P<key>[A-Za-z0-9_-]+))?",
    re.I,
)
re_folder_modern = re.compile(
    r"(?:^|/)folder/(?P<handle>[A-Za-z0-9_-]+)(?:#(?P<key>[A-Za-z0-9_-]+))?",
    re.I,
)
re_file_legacy = re.compile(
    r"#!?(?P<handle>[A-Za-z0-9_-]+)!(?P<key>[A-Za-z0-9_-]+)",
    re.I,
)
re_folder_legacy = re.compile(
    r"#F!(?P<handle>[A-Za-z0-9_-]+)!(?P<key>[A-Za-z0-9_-]+)",
    re.I,
)

# megatools progress variants:
# "name: 18.74% - 764.8 MiB (801,943,240 bytes) of 4.0 GiB (5.5 MiB/s)"
# "name: 0.00% - 0 bytes of 11 bytes"
re_megatools_progress = re.compile(
    r"^(?P<name>.+?):\s*(?P<pct>[\d.]+)%\s*-\s*"
    r"(?:"
    r".*?\((?P<bytes>[\d,]+)\s*bytes\)\s*of\s+(?P<total_human>.+?)"
    r"(?:\s*\((?P<speed>.+?)\))?"
    r"|"
    r"(?P<done_simple>[\d.]+)\s*(?P<done_unit>[A-Za-z]+)\s+of\s+"
    r"(?P<total_simple>[\d.]+)\s*(?P<total_unit>[A-Za-z]+)"
    r")"
    r"\s*$"
)
re_megatools_downloaded = re.compile(
    r"^Downloaded\s+(?P<name>.+?)\s*$",
    re.I,
)

# mega-get: "TRANSFERRING ||####||(163/163 MB: 100.00 %)"
re_megacmd_progress = re.compile(
    r"TRANSFERRING.*?=\((?P<done>[\d.]+)/(?P<total>[\d.]+)\s*"
    r"(?P<unit>[A-Za-z]+):\s*(?P<pct>[\d.]+)\s*%\s*\)",
    re.I,
)
re_megacmd_finished = re.compile(
    r"Download finished:\s*(?P<path>.+)\s*$",
    re.I,
)

install_guidance = (
    "Install megatools (preferred) or MEGAcmd, then retry. "
    "macOS: brew install megatools  (or brew install --cask megacmd). "
    "Windows: scoop install megatools  or install MEGAcmd from https://mega.io/cmd. "
    "Linux: apt/dnf install megatools  or MEGAcmd packages from mega.io. "
    "Override binary with --mega-tool or UDL_MEGA_TOOL. "
    "First-run auto-download is not implemented yet."
)


class mega_tool_missing_error(RuntimeError):
    """raised when neither megatools nor mega-get is available."""

    code = "mega_tool_missing"


class mega_download_error(RuntimeError):
    """raised when the external MEGA tool fails."""

    code = "download_failed"


@dataclass(frozen=True)
class mega_tool:
    kind: str  # megatools | megacmd
    path: Path


@dataclass(frozen=True)
class link_info:
    kind: str  # file | folder
    handle: str
    key: str | None
    url: str


def matches(url: str) -> bool:
    try:
        return parse_link(url) is not None
    except ValueError:
        return False


def parse_link(url: str) -> link_info | None:
    raw = url.strip()
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"mega.nz", "mega.co.nz", "mega.io"}:
        return None

    path_q = unquote(parsed.path or "")
    fragment = unquote(parsed.fragment or "")
    combined = f"{path_q}#{fragment}" if fragment else path_q

    m = re_folder_modern.search(combined)
    if m:
        return link_info(
            kind="folder",
            handle=m.group("handle"),
            key=m.group("key"),
            url=raw if "://" in raw else f"https://{raw}",
        )
    m = re_file_modern.search(combined)
    if m:
        return link_info(
            kind="file",
            handle=m.group("handle"),
            key=m.group("key"),
            url=raw if "://" in raw else f"https://{raw}",
        )
    m = re_folder_legacy.search(combined)
    if m:
        return link_info(
            kind="folder",
            handle=m.group("handle"),
            key=m.group("key"),
            url=raw if "://" in raw else f"https://{raw}",
        )
    m = re_file_legacy.search(combined)
    if m:
        return link_info(
            kind="file",
            handle=m.group("handle"),
            key=m.group("key"),
            url=raw if "://" in raw else f"https://{raw}",
        )
    return None


def find_mega_tool(explicit: str | Path | None = None) -> mega_tool:
    """prefer megatools then mega-get; raise mega_tool_missing_error."""
    if explicit:
        return _tool_from_path(Path(explicit), required=True)

    env = os.environ.get(mega_tool_env)
    if env:
        return _tool_from_path(Path(env), required=True)

    for name in megatools_names:
        which = shutil.which(name)
        if which:
            tool = _classify_binary(Path(which))
            if tool is not None:
                return tool

    for name in megacmd_names:
        which = shutil.which(name)
        if which:
            return mega_tool(kind="megacmd", path=Path(which).resolve())

    raise mega_tool_missing_error(
        "No MEGA CLI found (tried megatools / megadl / mega-get). " + install_guidance
    )


def _tool_from_path(path: Path, *, required: bool) -> mega_tool:
    if not path.is_file():
        which = shutil.which(str(path))
        if which:
            path = Path(which)
    if not path.is_file() or not os.access(path, os.X_OK):
        if required:
            raise mega_tool_missing_error(
                f"MEGA tool not found at {path}. " + install_guidance
            )
        raise mega_tool_missing_error(install_guidance)
    tool = _classify_binary(path.resolve())
    if tool is None:
        raise mega_tool_missing_error(
            f"{path} is not recognisable as megatools or mega-get. " + install_guidance
        )
    return tool


def _classify_binary(path: Path) -> mega_tool | None:
    name = path.name.lower()
    if name in {"megadl", "megatools"} or "megatools" in name:
        return mega_tool(kind="megatools", path=path)
    if name in {"mega-get", "megaget"} or name.startswith("mega-get"):
        return mega_tool(kind="megacmd", path=path)
    # probe --help for multi-call or renamed binaries
    try:
        proc = subprocess.run(
            [str(path), "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or "") + (proc.stderr or "")
    low = text.lower()
    if "megatools" in low or "download exported files" in low:
        return mega_tool(kind="megatools", path=path)
    if "mega-get" in low or "megacmd" in low:
        return mega_tool(kind="megacmd", path=path)
    return None


def _parse_speed_to_bps(speed: str) -> float | None:
    raw = speed.strip().lower().replace("\xa0", " ")
    if raw in {"", "0", "0 bytes/s", "0 b/s"}:
        return 0.0
    m = re.match(r"^([\d.]+)\s*([a-z]+)/s$", raw)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2)
    mult = {
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "kib": 1024,
        "kb": 1000,
        "mib": 1024**2,
        "mb": 1000**2,
        "gib": 1024**3,
        "gb": 1000**3,
    }.get(unit)
    if mult is None:
        return None
    return value * mult


def _unit_to_bytes(value: float, unit: str) -> int:
    u = unit.strip().lower()
    mult = {
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
    }.get(u, 1)
    return int(value * mult)


def _human_size_to_bytes(text: str) -> int | None:
    m = re.match(r"^([\d.]+)\s*([A-Za-z]+)$", text.strip().replace("\xa0", " "))
    if not m:
        return None
    return _unit_to_bytes(float(m.group(1)), m.group(2))


@dataclass
class progress_update:
    name: str | None
    bytes_done: int
    total: int | None
    bps: float | None
    finished_path: str | None = None


def parse_tool_line(line: str, *, tool_kind: str) -> progress_update | None:
    """parse one progress / completion line from megatools or mega-get."""
    text = line.strip().replace("\x1b[2K", "").replace("\r", "")
    if not text:
        return None

    if tool_kind == "megatools":
        m = re_megatools_downloaded.match(text)
        if m:
            name = m.group("name").strip()
            return progress_update(
                name=name,
                bytes_done=0,
                total=None,
                bps=None,
                finished_path=name,
            )
        m = re_megatools_progress.match(text)
        if m:
            pct = float(m.group("pct"))
            if m.group("bytes"):
                done = int(m.group("bytes").replace(",", ""))
                total_human = _human_size_to_bytes(m.group("total_human") or "")
                speed = m.group("speed")
            else:
                done = _unit_to_bytes(
                    float(m.group("done_simple")),
                    m.group("done_unit"),
                )
                total_human = _unit_to_bytes(
                    float(m.group("total_simple")),
                    m.group("total_unit"),
                )
                speed = None
            if total_human is None and pct > 0 and done > 0:
                total_human = int(done / (pct / 100.0))
            return progress_update(
                name=m.group("name").strip(),
                bytes_done=done,
                total=total_human,
                bps=_parse_speed_to_bps(speed) if speed else None,
            )
        return None

    m = re_megacmd_finished.match(text)
    if m:
        path = m.group("path").strip()
        return progress_update(
            name=Path(path).name,
            bytes_done=0,
            total=None,
            bps=None,
            finished_path=path,
        )
    m = re_megacmd_progress.search(text)
    if m:
        done = _unit_to_bytes(float(m.group("done")), m.group("unit"))
        total = _unit_to_bytes(float(m.group("total")), m.group("unit"))
        return progress_update(
            name=None,
            bytes_done=done,
            total=total,
            bps=None,
        )
    return None


def _build_command(tool: mega_tool, url: str, output_dir: Path) -> list[str]:
    net = active_network()
    limit_kib = format_speed_kib(effective_job_limit_bps())
    if tool.kind == "megatools":
        cmd = [str(tool.path)]
        # multi-call binary needs the dl subcommand; megadl does not
        if tool.path.name.lower() != "megadl":
            cmd.append("dl")
        cmd.extend(
            [
                "--path",
                str(output_dir),
                "--print-names",
                "--no-ask-password",
            ]
        )
        if limit_kib is not None:
            cmd.extend(["--limit-speed", str(limit_kib)])
        mode = (net.proxy_mode or proxy_mode_none).lower()
        if mode == proxy_mode_none:
            cmd.extend(["--proxy", "none"])
        elif mode == proxy_mode_custom:
            proxy = build_proxy_url(net)
            if proxy:
                cmd.extend(["--proxy", proxy])
        cmd.append(url)
        return cmd
    # mega-get <link> <localdir> — proxy/speed via env (see external_env)
    return [str(tool.path), url, str(output_dir)]


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
    except ProcessLookupError:
        return
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    try:
        if sys.platform == "win32":
            proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
    except ProcessLookupError:
        pass


def _reader(stream: IO[str], sink: list[str], on_chunk) -> None:
    buf = ""
    try:
        while True:
            chunk = stream.read(256)
            if not chunk:
                break
            buf += chunk
            while True:
                idx_n = buf.find("\n")
                idx_r = buf.find("\r")
                if idx_n < 0 and idx_r < 0:
                    break
                if idx_n < 0:
                    idx = idx_r
                elif idx_r < 0:
                    idx = idx_n
                else:
                    idx = min(idx_n, idx_r)
                line = buf[:idx]
                buf = buf[idx + 1 :]
                sink.append(line)
                on_chunk(line)
            if len(buf) > 4000:
                # progress bar without terminator — treat as line
                sink.append(buf)
                on_chunk(buf)
                buf = ""
        if buf:
            sink.append(buf)
            on_chunk(buf)
    except OSError:
        return


class mega_extractor(external_extractor):
    site = "mega"
    label = "MEGA"

    def __init__(self, *, mega_tool: str | Path | None = None) -> None:
        self.mega_tool_override = mega_tool

    def configure(self, **kwargs: object) -> None:
        if "mega_tool" in kwargs and kwargs["mega_tool"] is not None:
            self.mega_tool_override = str(kwargs["mega_tool"])  # type: ignore[assignment]

    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        del password  # public links only; folder passwords unsupported here
        info = parse_link(url)
        if info is None:
            raise ValueError(f"not a MEGA public link: {url}")
        # ensure the external tool exists early so GUI/CLI get a clear event
        find_mega_tool(self.mega_tool_override)
        title = f"MEGA {info.kind} {info.handle}"
        # item_id is the MEGA node handle dedupe also requires size see duplicates.py
        meta = item_metadata(
            site=self.site,
            item_id=info.handle,
            canonical_url=info.url,
            title=title,
            collection=title if info.kind == "folder" else None,
            collection_id=info.handle if info.kind == "folder" else None,
            extra={
                "kind": info.kind,
                "tool": "external",
                "node_handle": info.handle,
            },
        )
        placeholder = extracted_file(
            url=info.url,
            name=title,
            relative_dir="",
            size=None,
            meta={
                "handle": info.handle,
                "node_handle": info.handle,
                "kind": info.kind,
                "key": info.key,
            },
            metadata=meta,
        )
        return extract_result(
            site=self.site,
            title=title,
            files=[placeholder],
            kind=info.kind,
            meta={"handle": info.handle, "tool": "external"},
            metadata=meta,
        )

    def download(
        self,
        result: extract_result,
        output_dir: Path,
        *,
        emit: emitter | None = None,
        cancel: Event | None = None,
        workers: int = 3,
        retries: int = 5,
        job_id: str | None = None,
        file_dest_overrides: dict[int, Path] | None = None,
        skip_indexes: set[int] | None = None,
    ) -> tuple[int, int]:
        del workers, retries, file_dest_overrides
        job = job_id or result.site
        meta_payload = (
            result.metadata.to_dict(omit_empty=True) if result.metadata else None
        )
        if skip_indexes and 0 in skip_indexes:
            dest = output_dir / (result.files[0].name if result.files else result.title)
            if emit:
                emit.file(
                    job=job,
                    id=f"{job}:0",
                    name=result.files[0].name if result.files else result.title,
                    path=str(dest),
                    index=0,
                    metadata=meta_payload,
                    already_downloaded=True,
                )
                emit.done(
                    job=job,
                    file=f"{job}:0",
                    path=str(dest),
                    skipped=True,
                    already_downloaded=True,
                    metadata=meta_payload,
                )
            return 1, 0
        output_dir.mkdir(parents=True, exist_ok=True)
        tool = find_mega_tool(self.mega_tool_override)
        url = result.files[0].url if result.files else ""
        if not url:
            raise mega_download_error("missing MEGA URL")

        cmd = _build_command(tool, url, output_dir)
        file_ids: dict[str, str] = {}
        file_totals: dict[str, int | None] = {}
        completed: set[str] = set()
        last_emit = 0.0
        active_name: str | None = None
        stderr_lines: list[str] = []
        stdout_lines: list[str] = []
        state_lock = Lock()

        def ensure_file(name: str, total: int | None = None) -> str:
            nonlocal active_name
            active_name = name
            if name not in file_ids:
                idx = len(file_ids)
                fid = f"{job}:{idx}"
                file_ids[name] = fid
                file_totals[name] = total
                dest = output_dir / name
                if emit:
                    emit.file(
                        job=job,
                        id=fid,
                        name=name,
                        total=total,
                        path=str(dest),
                        index=idx,
                        metadata=meta_payload,
                    )
            elif total is not None and file_totals.get(name) is None:
                file_totals[name] = total
            return file_ids[name]

        # placeholder until the tool prints a real filename
        ensure_file(result.files[0].name if result.files else result.title)

        def handle_line(line: str) -> None:
            nonlocal last_emit
            if cancel is not None and cancel.is_set():
                return
            with state_lock:
                update = parse_tool_line(line, tool_kind=tool.kind)
                if update is None:
                    # megatools --print-names: bare path of completed file
                    stripped = line.strip()
                    if (
                        tool.kind == "megatools"
                        and stripped
                        and not stripped.upper().startswith("ERROR")
                        and not stripped.upper().startswith("WARNING")
                        and (
                            os.path.sep in stripped
                            or (output_dir / stripped).exists()
                        )
                    ):
                        path = Path(stripped)
                        if path.is_absolute() or (output_dir / stripped).exists():
                            name = path.name
                            if name in completed:
                                return
                            fid = ensure_file(name)
                            completed.add(name)
                            if emit:
                                emit.done(
                                    job=job,
                                    file=fid,
                                    path=str(
                                        path
                                        if path.is_absolute()
                                        else output_dir / name
                                    ),
                                )
                    return

                name = update.name or active_name or result.title
                fid = ensure_file(name, update.total)
                if update.finished_path:
                    if name in completed:
                        return
                    completed.add(name)
                    fin = Path(update.finished_path)
                    if not fin.is_absolute():
                        candidate = output_dir / fin.name
                        # folder downloads may nest under subdirs
                        matches = list(output_dir.rglob(fin.name))
                        fin = matches[0] if matches else candidate
                    if emit:
                        emit.done(job=job, file=fid, path=str(fin))
                    return

                now = time.monotonic()
                if emit and (now - last_emit) >= 0.25:
                    last_emit = now
                    emit.progress(
                        job=job,
                        file=fid,
                        bytes=update.bytes_done,
                        total=update.total or file_totals.get(name),
                        bps=update.bps,
                    )

        popen_kwargs: dict[str, object] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "bufsize": 1,
            "env": external_env(),
        }
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[arg-type]
        except OSError as exc:
            raise mega_download_error(f"failed to start {tool.path}: {exc}") from exc

        assert proc.stdout is not None
        assert proc.stderr is not None
        t_out = Thread(
            target=_reader,
            args=(proc.stdout, stdout_lines, handle_line),
            daemon=True,
        )
        t_err = Thread(
            target=_reader,
            args=(proc.stderr, stderr_lines, handle_line),
            daemon=True,
        )
        t_out.start()
        t_err.start()

        try:
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    _terminate_process(proc)
                    t_out.join(timeout=2)
                    t_err.join(timeout=2)
                    raise InterruptedError("cancelled")
                time.sleep(0.05)
        finally:
            t_out.join(timeout=5)
            t_err.join(timeout=5)

        if cancel is not None and cancel.is_set():
            raise InterruptedError("cancelled")

        if proc.returncode != 0:
            detail = "\n".join(stderr_lines[-20:] + stdout_lines[-10:]).strip()
            if len(detail) > 1200:
                detail = detail[-1200:]
            low = detail.lower()
            if "509" in detail or "over quota" in low:
                raise mega_download_error(
                    "MEGA transfer quota exceeded (HTTP 509). "
                    "Wait or use a MEGA account with remaining quota. "
                    "Quota-bypass features are not supported. "
                    f"Detail: {detail or f'exit {proc.returncode}'}"
                )
            raise mega_download_error(
                f"MEGA tool failed (exit {proc.returncode}): "
                f"{detail or 'no output'}"
            )

        # mark remaining active files done if tool succeeded without print-names
        ok = 0
        for name, fid in file_ids.items():
            if name in completed:
                ok += 1
                continue
            dest = output_dir / name
            # folder downloads may create nested paths — scan for any new files
            if dest.is_file():
                if emit:
                    emit.done(job=job, file=fid, path=str(dest))
                ok += 1
                completed.add(name)

        if ok == 0:
            # success with unknown names: count any files under output
            found = [p for p in output_dir.rglob("*") if p.is_file()]
            if found:
                for idx, path in enumerate(found):
                    fid = f"{job}:done:{idx}"
                    if emit:
                        emit.file(
                            job=job,
                            id=fid,
                            name=path.name,
                            total=path.stat().st_size,
                            path=str(path),
                            index=idx,
                        )
                        emit.done(job=job, file=fid, path=str(path))
                ok = len(found)
            else:
                # treat as one ok if tool exited 0 (megatools folder edge cases)
                ok = 1
                if emit and active_name:
                    emit.done(
                        job=job,
                        file=file_ids.get(active_name, f"{job}:0"),
                        path=str(output_dir),
                    )

        failed = 0 if ok > 0 else 1
        return ok, failed
