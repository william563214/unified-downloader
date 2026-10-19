"""OS keychain helpers for proxy credentials and future login tokens.

Uses the `keyring` package when a backend is available. In headless CI or
environments without a keyring service, falls back to a mode-0600 file under
the platform user data directory so CLI/GUI still work.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Final

service_name: Final[str] = "unified-downloader"

# well-known keys shared across features
proxy_password_key: Final[str] = "proxy.custom"
aria2_secret_key: Final[str] = "aria2.rpc_secret"

_file_backend_warned = False


def _app_data_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / service_name


def _fallback_path() -> Path:
    return _app_data_dir() / "secrets.json"


def _read_fallback() -> dict[str, str]:
    path = _fallback_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _write_fallback(data: dict[str, str]) -> None:
    path = _fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(raw + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _warn_fallback_once() -> None:
    global _file_backend_warned
    if _file_backend_warned:
        return
    _file_backend_warned = True
    print(
        "udl.secrets: no OS keyring backend; using file fallback at "
        f"{_fallback_path()} (mode 0600)",
        file=sys.stderr,
    )


def set_secret(key: str, value: str) -> None:
    """store a secret in the OS keychain under service unified-downloader."""
    if not key or not key.strip():
        raise ValueError("secret key must be non-empty")
    name = key.strip()
    try:
        import keyring
        from keyring.errors import KeyringError, NoKeyringError

        keyring.set_password(service_name, name, value)
        return
    except Exception as exc:  # noqa: BLE001 — fall back for headless / CI
        # only fall back for missing backend / keyring failures
        try:
            from keyring.errors import KeyringError, NoKeyringError

            if not isinstance(exc, (NoKeyringError, KeyringError, ImportError)):
                # unexpected — still try file fallback for robustness
                pass
        except ImportError:
            pass
    _warn_fallback_once()
    data = _read_fallback()
    data[name] = value
    _write_fallback(data)


def get_secret(key: str) -> str | None:
    """return a secret or None when missing / backend unavailable."""
    if not key or not key.strip():
        return None
    name = key.strip()
    try:
        import keyring

        value = keyring.get_password(service_name, name)
        if value is not None:
            return value
    except Exception:  # noqa: BLE001 — keyring backends vary by OS
        pass
    data = _read_fallback()
    return data.get(name)


def delete_secret(key: str) -> bool:
    """delete a secret. returns True when a value was removed."""
    if not key or not key.strip():
        return False
    name = key.strip()
    removed = False
    try:
        import keyring

        existing = keyring.get_password(service_name, name)
        if existing is not None:
            keyring.delete_password(service_name, name)
            removed = True
    except Exception:  # noqa: BLE001
        pass
    data = _read_fallback()
    if name in data:
        data.pop(name, None)
        _write_fallback(data)
        removed = True
    return removed
