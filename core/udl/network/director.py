"""Build and use a process-wide request_director from network_settings."""

from __future__ import annotations

from typing import Any

from udl.network.common import request, request_director, response
from udl.network.impersonate import impersonate_target
from udl.network.settings import (
    active_network,
    default_user_agent,
    network_settings,
    proxies_for_requests,
)

_director: request_director | None = None


def _handler_kwargs(settings: network_settings) -> dict[str, Any]:
    headers = {}
    ua = settings.user_agent or default_user_agent
    if ua:
        headers["User-Agent"] = ua
    proxies = proxies_for_requests(settings)
    return {
        "headers": headers,
        "proxies": proxies or {},
        "timeout": 20.0,
        "verify": True,
    }


def _maybe_impersonate(settings: network_settings) -> impersonate_target | None:
    if not settings.impersonate:
        return None
    raw = settings.impersonate_target or "chrome"
    try:
        return impersonate_target.from_str(raw)
    except ValueError:
        return impersonate_target(client="chrome")


def build_director(settings: network_settings | None = None) -> request_director:
    """create a request_director with urllib / requests / optional backends."""
    # ensure handlers register themselves
    from udl.network import _requests as _requests_mod  # noqa: F401
    from udl.network import _urllib as _urllib_mod  # noqa: F401

    cfg = settings or active_network()
    kwargs = _handler_kwargs(cfg)
    preferred = (cfg.request_handler or "auto").lower()
    director = request_director()

    from udl.network._requests import RequestsRH
    from udl.network._urllib import UrllibRH

    curl_cls = None
    try:
        from udl.network._curlcffi import CurlCffiRH

        curl_cls = CurlCffiRH
    except ImportError:
        curl_cls = None

    ws_cls = None
    try:
        from udl.network._websockets import WebsocketsRH

        ws_cls = WebsocketsRH
    except ImportError:
        ws_cls = None

    impersonate = _maybe_impersonate(cfg)

    def add(name: str, cls: type, **extra: Any) -> None:
        director.add_handler(cls(**kwargs, **extra))

    # websockets always registered when available (scheme-gated by preference)
    if ws_cls is not None:
        add("websockets", ws_cls)

    if preferred == "urllib":
        add("urllib", UrllibRH)
    elif preferred == "requests":
        add("requests", RequestsRH)
    elif preferred == "curl_cffi":
        if curl_cls is None:
            raise RuntimeError(
                "request_handler=curl_cffi requires optional dependency curl_cffi "
                '(install udl[curl-cffi] or pip install "curl_cffi")'
            )
        add("curl_cffi", curl_cls, impersonate=impersonate)
    else:
        # auto: requests + urllib fallback; curl_cffi only elevated when impersonate on
        add("requests", RequestsRH)
        add("urllib", UrllibRH)
        if curl_cls is not None:
            add("curl_cffi", curl_cls, impersonate=impersonate)

    return director


def active_director(*, refresh: bool = False) -> request_director:
    """process-wide director rebuilt when refresh=True or missing."""
    global _director
    if _director is None or refresh:
        if _director is not None:
            _director.close()
        _director = build_director()
    return _director


def reset_director() -> None:
    global _director
    if _director is not None:
        _director.close()
    _director = None


def send(req: request | str, **kwargs: Any) -> response:
    """send via the active director; apply impersonate extension from settings."""
    if isinstance(req, str):
        req = request(req, **kwargs)
    elif kwargs:
        raise TypeError("kwargs only allowed when req is a URL string")

    cfg = active_network()
    if cfg.impersonate and "impersonate" not in req.extensions:
        target = _maybe_impersonate(cfg)
        if target is not None:
            req.extensions["impersonate"] = target

    # keep UA centralized even if caller omitted it
    ua = cfg.user_agent or default_user_agent
    if ua and "User-Agent" not in req.headers and "user-agent" not in req.headers:
        req.headers["User-Agent"] = ua

    # proxy from settings when request carries none
    if not req.proxies:
        proxies = proxies_for_requests(cfg)
        if proxies is not None:
            req.proxies = dict(proxies)

    return active_director().send(req)


def available_handlers() -> dict[str, bool]:
    """report which backends can be imported in this environment."""
    out = {"urllib": True, "requests": True, "curl_cffi": False, "websockets": False}
    try:
        import curl_cffi  # noqa: F401

        out["curl_cffi"] = True
    except ImportError:
        pass
    try:
        import websockets  # noqa: F401

        out["websockets"] = True
    except ImportError:
        pass
    return out
