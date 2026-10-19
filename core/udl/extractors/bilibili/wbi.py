"""WBI request signing adapted from ScottSloan/Bili23-Downloader.

Upstream: util/parse/parser/base.py (enc_wbi / mixinKeyEncTab)
License: GPL-3.0 — see NOTICE for commit SHA.
"""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from functools import reduce
from threading import Lock

# mixin table from bilibili-API-collect / Bili23
mixin_key_enc_tab = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]

wbi_key_length = max(mixin_key_enc_tab) + 1

_img_key = ""
_sub_key = ""
_lock = Lock()


def store_wbi_keys(img_key: str, sub_key: str) -> bool:
    """store session-scoped WBI keys (not persisted to disk)."""
    global _img_key, _sub_key
    if not img_key or not sub_key:
        return False
    if len(img_key) + len(sub_key) < wbi_key_length:
        return False
    with _lock:
        _img_key = img_key
        _sub_key = sub_key
    return True


def keys_ready() -> bool:
    return len(_img_key) + len(_sub_key) >= wbi_key_length


def clear_keys() -> None:
    global _img_key, _sub_key
    with _lock:
        _img_key = ""
        _sub_key = ""


def _mixin_key(orig: str) -> str:
    return reduce(lambda s, i: s + orig[i], mixin_key_enc_tab, "")[:32]


def enc_wbi(params: dict[str, object]) -> str:
    """return urlencoded query with wts + w_rid."""
    if not keys_ready():
        raise RuntimeError("WBI keys unavailable — call client.refresh_wbi() first")
    with _lock:
        mixin = _mixin_key(_img_key + _sub_key)

    signed: dict[str, object] = dict(params)
    signed["wts"] = round(time.time())
    signed = dict(sorted(signed.items()))
    cleaned = {
        k: "".join(c for c in str(v) if c not in "!'()*") for k, v in signed.items()
    }
    query = urllib.parse.urlencode(cleaned)
    cleaned["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return urllib.parse.urlencode(cleaned)
