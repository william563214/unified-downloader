"""MEGA account login with OS keychain session storage."""

from __future__ import annotations

import json
from typing import Any

from udl.extractors.mega.api import mega_client, session_from_secrets, session_to_secrets
from udl.extractors.mega.errors import mega_auth_error
from udl.secrets import delete_secret, get_secret, set_secret

mega_session_key = "mega.session"


def save_session(client: mega_client) -> None:
    payload = session_to_secrets(client)
    set_secret(mega_session_key, json.dumps(payload, ensure_ascii=False))


def load_session() -> mega_client | None:
    raw = get_secret(mega_session_key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("sid"):
        return None
    return session_from_secrets(data)


def clear_session() -> bool:
    return delete_secret(mega_session_key)


def login(email: str, password: str) -> dict[str, Any]:
    client = mega_client()
    try:
        info = client.login(email, password)
        save_session(client)
        return {"ok": True, "email": info.get("email"), "logged_in": True}
    finally:
        client.close()


def logout() -> dict[str, Any]:
    client = load_session()
    if client is not None:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass
        finally:
            client.close()
    cleared = clear_session()
    return {"ok": True, "logged_in": False, "cleared": cleared}


def whoami() -> dict[str, Any]:
    client = load_session()
    if client is None:
        return {"logged_in": False, "email": None}
    try:
        try:
            info = client.whoami()
            return {
                "logged_in": True,
                "email": info.get("email"),
                "name": info.get("name"),
            }
        except Exception as exc:  # noqa: BLE001
            raise mega_auth_error(f"session invalid: {exc}") from exc
    finally:
        client.close()


def client_from_keychain() -> mega_client | None:
    return load_session()
