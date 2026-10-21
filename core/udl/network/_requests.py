"""requests library request_handler (default HTTP backend)."""

from __future__ import annotations

import io
from typing import Any

import requests
from requests import exceptions as req_exc

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


class _response_reader(io.IOBase):
    def __init__(self, resp: requests.Response) -> None:
        self._resp = resp
        self._iter = resp.iter_content(chunk_size=64 * 1024)
        self._buf = b""

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            chunks = [self._buf]
            self._buf = b""
            chunks.extend(self._iter)
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
            self._resp.close()
        return super().close()


@register_rh
class RequestsRH(request_handler):
    """HTTP via the requests package (core dependency)."""

    _supported_url_schemes = ("http", "https")
    _supported_proxy_schemes = ("http", "https", "socks", "socks4", "socks4a", "socks5", "socks5h")
    _supported_features = (features.all_proxy, features.no_proxy)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._session = requests.Session()

    def close(self) -> None:
        self._session.close()

    def _send(self, req: request) -> response:
        headers = self._get_headers(req)
        timeout = self._calculate_timeout(req)
        proxies = {k: v for k, v in self._get_proxies(req).items() if v}
        data = req.data
        if isinstance(data, io.IOBase):
            data = data.read()
        try:
            raw = self._session.request(
                method=req.method,
                url=req.url,
                headers=headers,
                data=data,
                timeout=timeout,
                proxies=proxies or None,
                verify=self.verify,
                stream=True,
                allow_redirects=True,
            )
        except req_exc.ProxyError as exc:
            raise proxy_error(cause=exc) from exc
        except req_exc.SSLError as exc:
            text = str(exc).lower()
            if "certificate" in text or "verify" in text:
                raise certificate_verify_error(cause=exc) from exc
            raise ssl_error(cause=exc) from exc
        except req_exc.RequestException as exc:
            raise transport_error(cause=exc) from exc

        resp = response(
            fp=_response_reader(raw),
            url=str(raw.url),
            headers=dict(raw.headers),
            status=int(raw.status_code),
            reason=raw.reason,
        )
        if req.extensions.get("raise_on_status", True) and resp.status >= 400:
            raise http_error(resp)
        return resp


@register_preference(RequestsRH)
def _requests_pref(handler: RequestsRH, req: request) -> int:
    _ = handler, req
    return 500
