"""MEGA crypto helpers ported from MegaBasterd (GPL-3.0) protocol logic.

Key / IV / attribute / CBC-MAC algorithms follow tonikelope/megabasterd
CryptTools + Download.verifyFileCBCMAC and the public MEGA protocol.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
from collections.abc import Iterable
from typing import Any

from Crypto.Cipher import AES
from Crypto.PublicKey import RSA
from Crypto.Util import Counter

# lowercase constants
aes_block = 16
zero_iv = b"\0" * 16


def base64_url_decode(data: str) -> bytes:
    text = data.replace("-", "+").replace("_", "/").replace(",", "")
    pad = (4 - len(text) % 4) % 4
    return base64.b64decode(text + "=" * pad)


def base64_url_encode(data: bytes) -> str:
    text = base64.b64encode(data).decode("ascii")
    return text.replace("+", "-").replace("/", "_").rstrip("=")


def a32_to_bytes(a: Iterable[int]) -> bytes:
    values = list(a)
    return struct.pack(f">{len(values)}I", *values)


def bytes_to_a32(data: bytes) -> tuple[int, ...]:
    if len(data) % 4:
        data = data + b"\0" * (4 - len(data) % 4)
    return struct.unpack(f">{len(data) // 4}I", data)


def a32_to_base64(a: Iterable[int]) -> str:
    return base64_url_encode(a32_to_bytes(a))


def base64_to_a32(s: str) -> tuple[int, ...]:
    return bytes_to_a32(base64_url_decode(s))


def aes_cbc_encrypt(data: bytes, key: bytes, iv: bytes = zero_iv) -> bytes:
    return AES.new(key, AES.MODE_CBC, iv).encrypt(data)


def aes_cbc_decrypt(data: bytes, key: bytes, iv: bytes = zero_iv) -> bytes:
    return AES.new(key, AES.MODE_CBC, iv).decrypt(data)


def aes_cbc_encrypt_a32(data: Iterable[int], key: Iterable[int]) -> tuple[int, ...]:
    return bytes_to_a32(aes_cbc_encrypt(a32_to_bytes(data), a32_to_bytes(key)))


def aes_cbc_decrypt_a32(data: Iterable[int], key: Iterable[int]) -> tuple[int, ...]:
    return bytes_to_a32(aes_cbc_decrypt(a32_to_bytes(data), a32_to_bytes(key)))


def stringhash(email: str, aeskey: Iterable[int]) -> str:
    s32 = bytes_to_a32(email.encode("utf-8"))
    h32 = [0, 0, 0, 0]
    for i, value in enumerate(s32):
        h32[i % 4] ^= value
    key = tuple(aeskey)
    for _ in range(0x4000):
        h32 = list(aes_cbc_encrypt_a32(h32, key))
    return a32_to_base64((h32[0], h32[2]))


def prepare_key(arr: Iterable[int]) -> tuple[int, ...]:
    pkey = [0x93C467E3, 0x7DB0C7A4, 0xD1BE3F81, 0x0152CB56]
    values = list(arr)
    for _ in range(0x10000):
        for j in range(0, len(values), 4):
            key = [0, 0, 0, 0]
            for i in range(4):
                if i + j < len(values):
                    key[i] = values[i + j]
            pkey = list(aes_cbc_encrypt_a32(pkey, key))
    return tuple(pkey)


def encrypt_key(a: Iterable[int], key: Iterable[int]) -> tuple[int, ...]:
    values = list(a)
    out: list[int] = []
    for i in range(0, len(values), 4):
        out.extend(aes_cbc_encrypt_a32(values[i : i + 4], key))
    return tuple(out)


def decrypt_key(a: Iterable[int], key: Iterable[int]) -> tuple[int, ...]:
    values = list(a)
    out: list[int] = []
    for i in range(0, len(values), 4):
        out.extend(aes_cbc_decrypt_a32(values[i : i + 4], key))
    return tuple(out)


def decrypt_attr(attr: bytes, key: Iterable[int]) -> dict[str, Any] | None:
    raw = aes_cbc_decrypt(attr, a32_to_bytes(key))
    text = raw.rstrip(b"\0").decode("utf-8", errors="ignore")
    if not text.startswith("MEGA"):
        return None
    payload = text[4:]
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def encrypt_attr(attr: dict[str, Any], key: Iterable[int]) -> bytes:
    raw = ("MEGA" + json.dumps(attr, separators=(",", ":"))).encode("utf-8")
    if len(raw) % 16:
        raw += b"\0" * (16 - len(raw) % 16)
    return aes_cbc_encrypt(raw, a32_to_bytes(key))


def mpi_to_int(data: bytes) -> int:
    return int.from_bytes(data[2:], "big")


def modular_inverse(a: int, m: int) -> int:
    def egcd(x: int, y: int) -> tuple[int, int, int]:
        if x == 0:
            return y, 0, 1
        g, yy, xx = egcd(y % x, x)
        return g, xx - (y // x) * yy, yy

    g, x, _ = egcd(a, m)
    if g != 1:
        raise ValueError("modular inverse does not exist")
    return x % m


def derive_file_key_parts(file_key: tuple[int, ...] | str) -> tuple[
    tuple[int, int, int, int],
    tuple[int, int, int, int],
    tuple[int, int],
]:
    """return (aes_key_a32, ctr_iv_a32, meta_mac)."""
    if isinstance(file_key, str):
        key = base64_to_a32(file_key)
    else:
        key = tuple(file_key)
    if len(key) < 8:
        raise ValueError("MEGA file key must be 8 ints (256-bit link key)")
    k = (
        key[0] ^ key[4],
        key[1] ^ key[5],
        key[2] ^ key[6],
        key[3] ^ key[7],
    )
    iv = (key[4], key[5], 0, 0)
    meta_mac = (key[6], key[7])
    return k, iv, meta_mac


def make_aes_ctr(key_a32: Iterable[int], iv_a32: Iterable[int], *, offset: int = 0):
    """AES-CTR cipher positioned at byte offset (chunk-aligned decryption)."""
    iv = list(iv_a32)
    initial = ((iv[0] << 32) + iv[1]) << 64
    # advance by whole AES blocks already consumed
    initial += offset // aes_block
    counter = Counter.new(128, initial_value=initial)
    return AES.new(a32_to_bytes(key_a32), AES.MODE_CTR, counter=counter)


def decrypt_ctr_chunk(
    ciphertext: bytes,
    key_a32: Iterable[int],
    iv_a32: Iterable[int],
    *,
    offset: int,
) -> bytes:
    cipher = make_aes_ctr(key_a32, iv_a32, offset=offset)
    return cipher.decrypt(ciphertext)


def get_mac_chunks(size: int) -> list[tuple[int, int]]:
    """MEGA protocol chunk layout used for CBC-MAC (size_multi=1)."""
    chunks: list[tuple[int, int]] = []
    p = 0
    s = 0x20000
    while p + s < size:
        chunks.append((p, s))
        p += s
        if s < 0x100000:
            s += 0x20000
    if p < size:
        chunks.append((p, size - p))
    return chunks


def verify_cbc_mac(
    path_or_data: str | bytes,
    *,
    file_size: int,
    key_a32: Iterable[int],
    iv_a32: Iterable[int],
    meta_mac: tuple[int, int],
) -> bool:
    """CBC-MAC integrity check (MegaBasterd verifyFileCBCMAC / mega.py)."""
    k_bytes = a32_to_bytes(key_a32)
    iv = list(iv_a32)
    iv_str = a32_to_bytes([iv[0], iv[1], iv[0], iv[1]])
    mac_encryptor = AES.new(k_bytes, AES.MODE_CBC, zero_iv)
    mac = zero_iv

    def feed(chunk: bytes) -> None:
        nonlocal mac
        encryptor = AES.new(k_bytes, AES.MODE_CBC, iv_str)
        i = 0
        for i in range(0, max(len(chunk) - 16, 0), 16):
            encryptor.encrypt(chunk[i : i + 16])
        if file_size > 16:
            i += 16
        else:
            i = 0
        block = chunk[i : i + 16]
        if len(block) % 16:
            block += b"\0" * (16 - len(block) % 16)
        if not block:
            block = b"\0" * 16
        mac = mac_encryptor.encrypt(encryptor.encrypt(block))

    if isinstance(path_or_data, bytes):
        data = path_or_data
        for start, length in get_mac_chunks(file_size):
            feed(data[start : start + length])
    else:
        with open(path_or_data, "rb") as fh:
            for _start, length in get_mac_chunks(file_size):
                chunk = fh.read(length)
                if len(chunk) < length:
                    chunk += b"\0" * (length - len(chunk))
                feed(chunk)

    file_mac = bytes_to_a32(mac)
    computed = (file_mac[0] ^ file_mac[1], file_mac[2] ^ file_mac[3])
    return computed == meta_mac


def pbkdf2_v2_password(password: str, salt_a32: Iterable[int]) -> tuple[tuple[int, ...], str]:
    """v2 account: return (password_aes_a32, user_hash_b64)."""
    salt = a32_to_bytes(salt_a32)
    derived = hashlib.pbkdf2_hmac(
        "sha512",
        password.encode("utf-8"),
        salt,
        100000,
        dklen=32,
    )
    password_aes = bytes_to_a32(derived[:16])
    user_hash = base64_url_encode(derived[-16:])
    return password_aes, user_hash


def decrypt_rsa_sid(csid_b64: str, privk_a32: Iterable[int], master_key: Iterable[int]) -> str:
    """decrypt session id from csid using RSA private key blob."""
    rsa_private_key = decrypt_key(privk_a32, master_key)
    private_key = a32_to_bytes(rsa_private_key)
    parts: list[int] = []
    for _ in range(4):
        bitlength = (private_key[0] << 8) + private_key[1]
        bytelength = math.ceil(bitlength / 8) + 2
        parts.append(mpi_to_int(private_key[:bytelength]))
        private_key = private_key[bytelength:]
    p, q, d = parts[0], parts[1], parts[2]
    n = p * q
    phi = (p - 1) * (q - 1)
    e = modular_inverse(d, phi)
    # validate RSA components constructible (MegaBasterd / mega.py parity)
    RSA.construct((n, e, d, p, q))
    encrypted_sid = mpi_to_int(base64_url_decode(csid_b64))
    sid_int = pow(encrypted_sid, d, n)
    sid_hex = f"{sid_int:x}"
    if len(sid_hex) % 2:
        sid_hex = "0" + sid_hex
    sid = bytes.fromhex(sid_hex)
    return base64_url_encode(sid[:43])
