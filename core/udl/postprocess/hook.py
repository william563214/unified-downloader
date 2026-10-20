"""optional post-download command hook (antivirus scan presets).

Off by default. Templates may include {path} placeholder for the local file.
Presets:
  - defender: Windows Defender MpCmdRun.exe -Scan -ScanType 3 -File {path}
  - clamav: clamscan {path}
"""

from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from udl.events import emitter

# lowercase constants per project convention
default_hook_timeout = 120
preset_defender = (
    "MpCmdRun.exe -Scan -ScanType 3 -File {path}"
)
preset_clamav = "clamscan {path}"
hook_presets: dict[str, str] = {
    "defender": preset_defender,
    "windows_defender": preset_defender,
    "clamav": preset_clamav,
    "clamscan": preset_clamav,
}


@dataclass
class hook_options:
    """post-download command hook settings."""

    enabled: bool = False
    command: str = ""
    preset: str | None = None
    timeout: int = default_hook_timeout

    def resolved_command(self) -> str | None:
        if not self.enabled:
            return None
        preset = (self.preset or "").strip().lower()
        if preset and preset in hook_presets:
            return hook_presets[preset]
        text = (self.command or "").strip()
        return text or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "command": self.command,
            "preset": self.preset,
            "timeout": self.timeout,
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> hook_options:
        if not data:
            return cls()
        timeout = data.get("timeout") or data.get("post_cmd_timeout") or default_hook_timeout
        try:
            timeout_i = int(timeout)
        except (TypeError, ValueError):
            timeout_i = default_hook_timeout
        preset_raw = data.get("preset") or data.get("avHookPreset") or data.get("av_hook_preset")
        preset = str(preset_raw).strip() if preset_raw else None
        return cls(
            enabled=bool(
                data.get("enabled")
                or data.get("post_cmd_enabled")
                or data.get("avHookEnabled")
                or data.get("av_hook_enabled")
            ),
            command=str(
                data.get("command")
                or data.get("post_cmd")
                or data.get("avHookCommand")
                or data.get("av_hook_command")
                or ""
            ),
            preset=preset or None,
            timeout=max(1, timeout_i),
        )


@dataclass
class hook_result:
    ok: bool
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "message": self.message,
        }


def render_command(template: str, path: str | Path) -> str:
    target = str(Path(path).resolve())
    # support both {path} and quoted forms without double-expanding
    if "{path}" in template:
        return template.replace("{path}", target)
    return f"{template.rstrip()} {shlex.quote(target)}"


def run_hook(
    path: str | Path,
    options: hook_options,
    *,
    emit: emitter | None = None,
    job: str | None = None,
    file: str | None = None,
) -> hook_result | None:
    """run configured post-download command. returns None when disabled."""
    template = options.resolved_command()
    if not template:
        return None
    target = Path(path)
    if not target.is_file():
        result = hook_result(
            ok=False,
            command=template,
            exit_code=None,
            stdout="",
            stderr="",
            duration_ms=0,
            message="file missing for post-download hook",
        )
        _emit(emit, job=job, file=file, path=str(target), result=result)
        return result

    rendered = render_command(template, target)
    argv = shlex.split(rendered, posix=True)
    if not argv:
        result = hook_result(
            ok=False,
            command=rendered,
            exit_code=None,
            stdout="",
            stderr="",
            duration_ms=0,
            message="empty post-download command",
        )
        _emit(emit, job=job, file=file, path=str(target), result=result)
        return result

    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=max(1, int(options.timeout)),
            check=False,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        ok = proc.returncode == 0
        result = hook_result(
            ok=ok,
            command=rendered,
            exit_code=proc.returncode,
            stdout=(proc.stdout or "")[-4000:],
            stderr=(proc.stderr or "")[-4000:],
            duration_ms=duration_ms,
            message=None if ok else f"post-download command exit {proc.returncode}",
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        result = hook_result(
            ok=False,
            command=rendered,
            exit_code=None,
            stdout=((exc.stdout or "") if isinstance(exc.stdout, str) else "")[-4000:],
            stderr=((exc.stderr or "") if isinstance(exc.stderr, str) else "")[-4000:],
            duration_ms=duration_ms,
            timed_out=True,
            message=f"post-download command timed out after {options.timeout}s",
        )
    except FileNotFoundError:
        duration_ms = int((time.monotonic() - started) * 1000)
        result = hook_result(
            ok=False,
            command=rendered,
            exit_code=None,
            stdout="",
            stderr="",
            duration_ms=duration_ms,
            message=f"post-download command not found: {argv[0]}",
        )
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.monotonic() - started) * 1000)
        result = hook_result(
            ok=False,
            command=rendered,
            exit_code=None,
            stdout="",
            stderr="",
            duration_ms=duration_ms,
            message=str(exc),
        )

    _emit(emit, job=job, file=file, path=str(target), result=result)
    return result


def _emit(
    emit: emitter | None,
    *,
    job: str | None,
    file: str | None,
    path: str,
    result: hook_result,
) -> None:
    if emit is None:
        return
    emit.postprocess(
        job=job or "",
        step="hook",
        file=file,
        path=path,
        role="hook",
        message=result.message or ("hook ok" if result.ok else "hook failed"),
        ok=result.ok,
        code=None if result.ok else ("hook_timeout" if result.timed_out else "hook_failed"),
    )
    emit.emit(
        "hook",
        job=job,
        file=file,
        path=path,
        ok=result.ok,
        command=result.command,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        timed_out=result.timed_out,
        message=result.message,
        stdout=result.stdout,
        stderr=result.stderr,
    )
