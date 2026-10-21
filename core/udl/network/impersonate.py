"""Browser impersonation helpers for optional curl_cffi handler.

Impersonation is opt-in and off by default (same policy as Cloudflare bypass).
"""

from __future__ import annotations

import re
from abc import ABC
from dataclasses import dataclass
from typing import Any

from udl.network.common import request, request_handler
from udl.network.exceptions import unsupported_request

re_impersonate = re.compile(
    r"^(?:(?P<client>[^:-]+)(?:-(?P<version>[^:-]+))?)?"
    r"(?::(?:(?P<os>[^:-]+)(?:-(?P<os_version>[^:-]+))?)?)?$"
)


@dataclass(order=True, frozen=True)
class impersonate_target:
    """browser impersonation target (client / version / os)."""

    client: str | None = None
    version: str | None = None
    os: str | None = None
    os_version: str | None = None

    def __post_init__(self) -> None:
        if self.version and not self.client:
            raise ValueError("client is required if version is set")
        if self.os_version and not self.os:
            raise ValueError("os is required if os_version is set")

    def __contains__(self, target: object) -> bool:
        if not isinstance(target, impersonate_target):
            return False
        return (
            (self.client is None or target.client is None or self.client == target.client)
            and (self.version is None or target.version is None or self.version == target.version)
            and (self.os is None or target.os is None or self.os == target.os)
            and (
                self.os_version is None
                or target.os_version is None
                or self.os_version == target.os_version
            )
        )

    def __str__(self) -> str:
        left = "-".join(p for p in (self.client, self.version) if p)
        right = "-".join(p for p in (self.os, self.os_version) if p)
        if left and right:
            return f"{left}:{right}"
        return left or right

    @classmethod
    def from_str(cls, target: str) -> impersonate_target:
        text = (target or "").strip()
        if not text:
            raise ValueError("empty impersonate target")
        # accept curl_cffi short forms like chrome / chrome131 / safari17_0
        if re.fullmatch(r"[A-Za-z0-9_]+", text):
            m = re.fullmatch(r"(?P<client>[A-Za-z]+)(?P<version>[0-9].*)?", text)
            if m:
                return cls(client=m.group("client").lower(), version=m.group("version"))
        mobj = re_impersonate.fullmatch(text)
        if not mobj:
            raise ValueError(f'invalid impersonate target "{target}"')
        return cls(**mobj.groupdict())


class impersonate_request_handler(request_handler, ABC):
    """request_handler that may apply browser TLS/HTTP fingerprint impersonation."""

    _supported_impersonate_targets: tuple[impersonate_target, ...] | None = ()

    def __init__(self, *, impersonate: impersonate_target | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.impersonate = impersonate

    def _resolve_impersonate(self, req: request) -> impersonate_target | None:
        raw = req.extensions.get("impersonate", self.impersonate)
        if raw is None or raw is False:
            return None
        if isinstance(raw, impersonate_target):
            return raw
        if isinstance(raw, str):
            return impersonate_target.from_str(raw)
        raise unsupported_request(f"invalid impersonate extension: {raw!r}", handler=self)

    def _check_extensions(self, extensions: dict[str, Any]) -> None:
        if "impersonate" in extensions:
            extensions.pop("impersonate", None)
        super()._check_extensions(extensions)

    def _validate(self, req: request) -> None:
        target = self._resolve_impersonate(req)
        supported = self._supported_impersonate_targets
        if target is not None and supported is not None:
            matched = any(item.__contains__(target) for item in supported)
            allowed = {str(t) for t in supported}
            clients = {t.client for t in supported if t.client}
            if not matched and str(target) not in allowed and target.client not in clients:
                raise unsupported_request(
                    f'impersonate target "{target}" is not supported',
                    handler=self,
                )
        super()._validate(req)
