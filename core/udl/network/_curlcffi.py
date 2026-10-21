"""optional curl_cffi request_handler with opt-in browser impersonation."""

from __future__ import annotations

import io
from typing import Any

from udl.network.common import (
    features,
    register_preference,
    register_rh,
    request,
    response,
)
from udl.network.exceptions import (
    certificate_verify_error,
    http_error,
    proxy_error,
    ssl_error,
    transport_error,
    unsupported_request,
)
from udl.network.impersonate import impersonate_request_handler, impersonate_target

try:
    import curl_cffi
    from curl_cffi import requests as curl_requests
except ImportError as exc:  # pragma: no cover - optional dependency
    raise ImportError("curl_cffi is not installed") from exc


class _curl_reader(io.IOBase):
    def __init__(self, resp: Any) -> None:
        self._resp = resp
        self._iter = resp.iter_content()
        self._buf = b""

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            chunks = [self._buf]
            self._buf = b""
            for chunk in self._iter:
                chunks.append(chunk)
            return b"".join(chunks)
        while len(self._buf) < size:
            try:
                chunk = next(self._iter)
            except StopIteration:
                break
            if not chunk:
                break
            self._buf += chunk
        data = self._buf[:size]
        self._buf = self._buf[size:]
        return data

    def close(self) -> None:
        if not self.closed:
            try:
                self._resp.close()
            except Exception:
                pass
        return super().close()


@register_rh
class CurlCffiRH(impersonate_request_handler):
    """HTTP via curl_cffi. Impersonation only when explicitly enabled."""

    _supported_url_schemes = ("http", "https")
    _supported_proxy_schemes = ("http", "https", "socks", "socks4", "socks5", "socks5h")
    _supported_features = (features.all_proxy,)
    # broad client family targets; concrete versions mapped at send time
    _supported_impersonate_targets = (
        impersonate_target(client="chrome"),
        impersonate_target(client="edge"),
        impersonate_target(client="safari"),
        impersonate_target(client="firefox"),
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._session = curl_requests.Session()

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    def _impersonate_arg(self, target: impersonate_target | None) -> str | None:
        if target is None:
            return None
        # curl_cffi accepts names like chrome, chrome131, safari17_0
        if target.version:
            return f"{target.client}{target.version}"
        return target.client

    def _send(self, req: request) -> response:
        target = self._resolve_impersonate(req)
        # policy: curl_cffi without impersonate is allowed as a transport
        # but director preference only elevates it when impersonate is on
        headers = self._get_headers(req)
        timeout = self._calculate_timeout(req)
        proxies = {k: v for k, v in self._get_proxies(req).items() if v}
        proxy = proxies.get("https") or proxies.get("http") or proxies.get("all")
        data = req.data
        if isinstance(data, io.IOBase):
            data = data.read()
        impersonate = self._impersonate_arg(target)
        try:
            kwargs: dict[str, Any] = {
                "method": req.method,
                "url": req.url,
                "headers": headers,
                "data": data,
                "timeout": timeout,
                "verify": self.verify,
                "stream": True,
                "allow_redirects": True,
            }
            if proxy:
                kwargs["proxies"] = {"http": proxy, "https": proxy}
            if impersonate:
                kwargs["impersonate"] = impersonate
            raw = self._session.request(**kwargs)
        except Exception as exc:
            text = str(exc).lower()
            if "proxy" in text:
                raise proxy_error(cause=exc) from exc
            if "certificate" in text or "ssl" in text:
                if "certificate" in text or "verify" in text:
                    raise certificate_verify_error(cause=exc) from exc
                raise ssl_error(cause=exc) from exc
            if "impersonate" in text:
                raise unsupported_request(str(exc), handler=self) from exc
            raise transport_error(cause=exc) from exc

        resp = response(
            fp=_curl_reader(raw),
            url=str(getattr(raw, "url", req.url)),
            headers=dict(getattr(raw, "headers", {}) or {}),
            status=int(getattr(raw, "status_code", 200)),
            reason=getattr(raw, "reason", None),
            extensions={"impersonate": impersonate, "curl_cffi": curl_cffi.__version__},
        )
        if req.extensions.get("raise_on_status", True) and resp.status >= 400:
            raise http_error(resp)
        return resp


@register_preference(CurlCffiRH)
def _curl_pref(handler: CurlCffiRH, req: request) -> int:
    _ = handler
    # only prefer curl_cffi when impersonation is explicitly requested
    raw = req.extensions.get("impersonate")
    if raw is None or raw is False:
        return 50
    return 1000
