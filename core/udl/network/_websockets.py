"""optional websockets request_handler for ws / wss."""

from __future__ import annotations

import io
from typing import Any

from udl.network.common import (
    features,
    register_preference,
    register_rh,
    request,
    request_handler,
    response,
)
from udl.network.exceptions import (
    proxy_error,
    ssl_error,
    transport_error,
    unsupported_request,
)

try:
    import websockets
    from websockets.sync import client as ws_client
except ImportError as exc:  # pragma: no cover - optional dependency
    raise ImportError("websockets is not installed") from exc


class websocket_response(response):
    """response that also exposes a sync websocket connection."""

    def __init__(self, ws: Any, url: str, headers: dict[str, str], status: int = 101) -> None:
        body = b""
        try:
            raw_resp = getattr(ws, "response", None)
            if raw_resp is not None and getattr(raw_resp, "body", None):
                body = raw_resp.body or b""
            if raw_resp is not None and getattr(raw_resp, "headers", None):
                headers = dict(raw_resp.headers)
            if raw_resp is not None and getattr(raw_resp, "status_code", None):
                status = int(raw_resp.status_code)
        except Exception:
            pass
        super().__init__(
            fp=io.BytesIO(body),
            url=url,
            headers=headers,
            status=status,
            reason="Switching Protocols",
            extensions={"websocket": ws},
        )
        self.ws = ws

    def send(self, data: bytes | str) -> None:
        self.ws.send(data)

    def recv(self, *args: Any, **kwargs: Any) -> Any:
        return self.ws.recv(*args, **kwargs)

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass
        return super().close()


@register_rh
class WebsocketsRH(request_handler):
    """WebSocket via the websockets package (optional dependency)."""

    _supported_url_schemes = ("ws", "wss")
    _supported_proxy_schemes = ("http", "https", "socks5", "socks5h")
    _supported_features = (features.websocket, features.all_proxy)

    def _check_extensions(self, extensions: dict[str, Any]) -> None:
        extensions.pop("websocket", None)
        super()._check_extensions(extensions)

    def _send(self, req: request) -> response:
        if req.method not in {"GET", "WS", "WEBSOCKET"}:
            raise unsupported_request(
                f"websocket handler does not support method {req.method}",
                handler=self,
            )
        headers = self._get_headers(req)
        timeout = self._calculate_timeout(req)
        proxies = self._get_proxies(req)
        proxy = proxies.get("all") or proxies.get("https") or proxies.get("http")
        try:
            connect_kwargs: dict[str, Any] = {
                "additional_headers": headers,
                "open_timeout": timeout,
            }
            # websockets sync client proxy support varies by version
            if proxy:
                connect_kwargs["proxy"] = proxy
            ws = ws_client.connect(req.url, **connect_kwargs)
        except TypeError:
            # older / newer API without proxy kw
            try:
                ws = ws_client.connect(
                    req.url,
                    additional_headers=headers,
                    open_timeout=timeout,
                )
            except Exception as exc:
                raise transport_error(cause=exc) from exc
        except Exception as exc:
            text = str(exc).lower()
            if "proxy" in text:
                raise proxy_error(cause=exc) from exc
            if "ssl" in text or "certificate" in text:
                raise ssl_error(cause=exc) from exc
            raise transport_error(cause=exc) from exc

        return websocket_response(ws, url=req.url, headers=headers)


@register_preference(WebsocketsRH)
def _ws_pref(handler: WebsocketsRH, req: request) -> int:
    _ = handler
    scheme = req.url.split(":", 1)[0].lower()
    return 2000 if scheme in {"ws", "wss"} else 0


# silence unused import warning for version introspection
_ = websockets
