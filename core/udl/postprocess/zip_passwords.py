"""ordered ZIP password table stored only in udl.secrets (OS keychain).

Never write passwords into config logs history or SQLite.
Legacy single key zip.password is migrated into the list on first load.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from udl.secrets import delete_secret, get_secret, set_secret, zip_password_key

# lowercase constants per project convention
zip_passwords_key = "zip.passwords"


@dataclass
class password_entry:
    id: str
    label: str
    password: str

    def public_dict(self, *, index: int) -> dict[str, Any]:
        """safe for GUI/CLI list views — never includes the password."""
        return {
            "id": self.id,
            "label": self.label,
            "index": index,
            "length": len(self.password),
        }


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def load_password_entries() -> list[password_entry]:
    """load ordered entries; migrate legacy zip.password when list empty."""
    raw = get_secret(zip_passwords_key)
    entries: list[password_entry] = []
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            items = data.get("entries") or data.get("passwords") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                pwd = item.get("password")
                if pwd is None or str(pwd) == "":
                    continue
                eid = str(item.get("id") or _new_id())
                label = str(item.get("label") or "")
                entries.append(
                    password_entry(id=eid, label=label, password=str(pwd))
                )

    if not entries:
        legacy = get_secret(zip_password_key)
        if legacy:
            entries = [
                password_entry(id=_new_id(), label="", password=str(legacy))
            ]
            save_password_entries(entries)
            # keep legacy key for one release cycle callers may still read it
    return entries


def save_password_entries(entries: list[password_entry]) -> None:
    payload = {
        "entries": [
            {"id": e.id, "label": e.label, "password": e.password} for e in entries
        ]
    }
    set_secret(zip_passwords_key, json.dumps(payload, ensure_ascii=False))


def list_password_public() -> list[dict[str, Any]]:
    return [e.public_dict(index=i + 1) for i, e in enumerate(load_password_entries())]


def add_password(password: str, *, label: str = "") -> dict[str, Any]:
    text = str(password or "")
    if not text:
        raise ValueError("password must be non-empty")
    entries = load_password_entries()
    entry = password_entry(id=_new_id(), label=str(label or ""), password=text)
    entries.append(entry)
    save_password_entries(entries)
    return entry.public_dict(index=len(entries))


def update_password(
    entry_id: str,
    *,
    password: str | None = None,
    label: str | None = None,
) -> dict[str, Any] | None:
    entries = load_password_entries()
    found: password_entry | None = None
    index = 0
    for i, entry in enumerate(entries):
        if entry.id == entry_id:
            found = entry
            index = i + 1
            if password is not None:
                if not str(password):
                    raise ValueError("password must be non-empty")
                entry.password = str(password)
            if label is not None:
                entry.label = str(label)
            break
    if found is None:
        return None
    save_password_entries(entries)
    return found.public_dict(index=index)


def remove_password(entry_id: str) -> bool:
    entries = load_password_entries()
    next_entries = [e for e in entries if e.id != entry_id]
    if len(next_entries) == len(entries):
        return False
    save_password_entries(next_entries)
    return True


def reorder_passwords(ordered_ids: list[str]) -> list[dict[str, Any]]:
    entries = load_password_entries()
    by_id = {e.id: e for e in entries}
    seen: set[str] = set()
    ordered: list[password_entry] = []
    for eid in ordered_ids:
        if eid in by_id and eid not in seen:
            ordered.append(by_id[eid])
            seen.add(eid)
    for entry in entries:
        if entry.id not in seen:
            ordered.append(entry)
    save_password_entries(ordered)
    return [e.public_dict(index=i + 1) for i, e in enumerate(ordered)]


def promote_password(entry_id: str) -> bool:
    """move entry to front of the list. returns False when id missing."""
    entries = load_password_entries()
    match = next((e for e in entries if e.id == entry_id), None)
    if match is None:
        return False
    if entries and entries[0].id == entry_id:
        return True
    rest = [e for e in entries if e.id != entry_id]
    save_password_entries([match, *rest])
    return True


def clear_passwords() -> int:
    entries = load_password_entries()
    count = len(entries)
    save_password_entries([])
    delete_secret(zip_password_key)
    return count


def passwords_for_try() -> list[tuple[str, str]]:
    """return ordered (entry_id, password) pairs for extract attempts."""
    return [(e.id, e.password) for e in load_password_entries() if e.password]
