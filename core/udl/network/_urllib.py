"""stdlib urllib request_handler."""

from __future__ import annotations

import io
import ssl
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from udl.network.common import (
    features,
    register_preference,
    register_rh,
    request,
    request_handler,
    response,
)
from udl.network.exceptions import (
    certificate_verify_error,
    http_error,
    proxy_error,
    ssl_error,
    transport_error,
)


@register_rh
class UrllibRH(request_handler):
    """HTTP via urllib.request (always available)."""

    _supported_url_schemes = ("http", "https")
    _supported_proxy_schemes = ("http", "https", "socks", "socks4", "socks4a", "socks5", "socks5h")
    _supported_features = (features.all_proxy, features.no_proxy)

    def _send(self, req: request) -> response:
        headers = self._get_headers(req)
        timeout = self._calculate_timeout(req)
        proxies = self._get_proxies(req)
        data = req.data
        if isinstance(data, io.IOBase):
            data = data.read()
        elif data is not None and not isinstance(data, bytes):
            data = b"".join(data)

        proxy_handler = urllib.request.ProxyHandler(_urllib_proxies(proxies))
        https_ctx = ssl.create_default_context()
        if not self.verify:
            https_ctx.check_hostname = False
            https_ctx.verify_mode = ssl.CERT_NONE
        handlers: list[Any] = [
            proxy_handler,
            urllib.request.HTTPSHandler(context=https_ctx),
            urllib.request.HTTPHandler(),
        ]
        opener = urllib.request.build_opener(*handlers)
        ureq = urllib.request.Request(req.url, data=data, headers=headers, method=req.method)
        try:
            raw = opener.open(ureq, timeout=timeout)
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                body = b""
            resp = response(
                fp=io.BytesIO(body),
                url=getattr(exc, "url", None) or req.url,
                headers=dict(exc.headers.items()) if exc.headers else {},
                status=int(exc.code),
                reason=str(exc.reason),
            )
            if req.extensions.get("raise_on_status", True):
                raise http_error(resp) from exc
            return resp
        except urllib.error.URLError as exc:
            text = str(exc.reason).lower()
            if "proxy" in text:
                raise proxy_error(cause=exc) from exc
            if "ssl" in text or "certificate" in text:
                if "certificate" in text or "verify" in text:
                    raise certificate_verify_error(cause=exc) from exc
                raise ssl_error(cause=exc) from exc
            raise transport_error(cause=exc) from exc

        hdrs = dict(raw.headers.items()) if raw.headers else {}
        resp = response(
            fp=raw,
            url=raw.geturl(),
            headers=hdrs,
            status=getattr(raw, "status", None) or raw.getcode(),
            reason=getattr(raw, "reason", None),
        )
        if req.extensions.get("raise_on_status", True) and resp.status >= 400:
            raise http_error(resp)
        return resp


def _urllib_proxies(proxies: dict[str, str | None]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in proxies.items():
        if value is None or key == "no":
            continue
        if key == "all":
            out.setdefault("http", value)
            out.setdefault("https", value)
            scheme = urlparse(value).scheme or "http"
            out.setdefault(scheme, value)
        else:
            out[key] = value
    return out


@register_preference(UrllibRH)
def _urllib_pref(handler: UrllibRH, req: request) -> int:
    _ = handler, req
    return 100
