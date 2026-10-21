"""Request / Response / RequestHandler / RequestDirector (yt-dlp style).

Interface inspired by yt-dlp ``yt_dlp.networking`` (Unlicense). See NOTICE.
"""

from __future__ import annotations

import abc
import copy
import enum
import io
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from email.message import Message
from http import HTTPStatus
from typing import Any

from udl.network.exceptions import (
    no_supporting_handlers,
    request_error,
    unsupported_request,
)

default_timeout = 20.0

preference = Callable[["request_handler", "request"], int]


class features(enum.Enum):
    all_proxy = enum.auto()
    no_proxy = enum.auto()
    websocket = enum.auto()


class http_headers(dict[str, str]):
    """case-insensitive header mapping that preserves last-write casing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._store: dict[str, tuple[str, str]] = {}
        super().__init__()
        self.update(dict(*args, **kwargs))

    def _key(self, key: str) -> str:
        return key.lower()

    def __setitem__(self, key: str, value: str) -> None:
        self._store[self._key(key)] = (key, str(value))
        super().__setitem__(self._key(key), str(value))

    def __getitem__(self, key: str) -> str:
        return self._store[self._key(key)][1]

    def __delitem__(self, key: str) -> None:
        del self._store[self._key(key)]
        super().__delitem__(self._key(key))

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return self._key(key) in self._store

    def get(self, key: str, default: str | None = None) -> str | None:  # type: ignore[override]
        item = self._store.get(self._key(key))
        return item[1] if item else default

    def update(self, other: Mapping[str, str] | None = None, **kwargs: str) -> None:  # type: ignore[override]
        if other:
            for key, value in other.items():
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def copy(self) -> http_headers:  # type: ignore[override]
        out = http_headers()
        out._store = dict(self._store)
        dict.update(out, {k: v for k, (_, v) in self._store.items()})
        return out

    def sensitive(self) -> dict[str, str]:
        return {orig: value for orig, value in self._store.values()}

    def merged(self, other: Mapping[str, str] | None) -> http_headers:
        out = self.copy()
        if other:
            out.update(other)
        return out


class request:
    """HTTP (or websocket) request to send via a request_handler."""

    def __init__(
        self,
        url: str,
        data: bytes | Iterable[bytes] | io.IOBase | None = None,
        headers: Mapping[str, str] | None = None,
        proxies: dict[str, str | None] | None = None,
        method: str | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        self._headers = http_headers()
        self._data: bytes | Iterable[bytes] | io.IOBase | None = None
        self._method = method.upper() if isinstance(method, str) else None
        self.url = url
        if headers:
            self.headers = headers
        self.data = data
        self.proxies = dict(proxies or {})
        self.extensions = dict(extensions or {})

    @property
    def url(self) -> str:
        return self._url

    @url.setter
    def url(self, url: str) -> None:
        if not isinstance(url, str):
            raise TypeError("url must be a string")
        if url.startswith("//"):
            url = "http:" + url
        self._url = url

    @property
    def method(self) -> str:
        return self._method or ("POST" if self.data is not None else "GET")

    @method.setter
    def method(self, method: str | None) -> None:
        if method is None:
            self._method = None
        elif isinstance(method, str):
            self._method = method.upper()
        else:
            raise TypeError("method must be a string")

    @property
    def data(self) -> bytes | Iterable[bytes] | io.IOBase | None:
        return self._data

    @data.setter
    def data(self, data: bytes | Iterable[bytes] | io.IOBase | None) -> None:
        if data is not None and (
            not isinstance(data, (bytes, io.IOBase, Iterable)) or isinstance(data, (str, Mapping))
        ):
            raise TypeError("data must be bytes, iterable of bytes, or a file-like object")
        if data != self._data:
            if self._data is not None:
                self.headers.pop("Content-Length", None)
            self._data = data
        if self._data is None:
            self.headers.pop("Content-Type", None)
        elif "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "application/x-www-form-urlencoded"

    @property
    def headers(self) -> http_headers:
        return self._headers

    @headers.setter
    def headers(self, new_headers: Mapping[str, str]) -> None:
        if isinstance(new_headers, http_headers):
            self._headers = new_headers
        elif isinstance(new_headers, Mapping):
            self._headers = http_headers(new_headers)
        else:
            raise TypeError("headers must be a mapping")

    def copy(self) -> request:
        return request(
            url=self.url,
            headers=copy.deepcopy(self.headers.sensitive()),
            proxies=copy.deepcopy(self.proxies),
            data=self._data,
            extensions=copy.copy(self.extensions),
            method=self._method,
        )


class response(io.IOBase):
    """file-like HTTP response adapter."""

    def __init__(
        self,
        fp: io.IOBase,
        url: str,
        headers: Mapping[str, str],
        status: int = 200,
        reason: str | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        self.fp = fp
        self.headers = Message()
        for name, value in headers.items():
            self.headers.add_header(name, value)
        self.status = status
        self.url = url
        try:
            self.reason = reason or HTTPStatus(status).phrase
        except ValueError:
            self.reason = reason
        self.extensions = dict(extensions or {})

    def readable(self) -> bool:
        return self.fp.readable()

    def read(self, amt: int | None = None) -> bytes:
        from udl.download.rate import throttle
        from udl.network.exceptions import transport_error

        try:
            chunk = self.fp.read(amt)
            if self.fp.closed:
                self.close()
            if chunk:
                throttle(len(chunk))
            return chunk
        except request_error:
            raise
        except Exception as exc:
            raise transport_error(cause=exc) from exc

    def close(self) -> None:
        if not self.fp.closed:
            self.fp.close()
        return super().close()

    def get_header(self, name: str, default: str | None = None) -> str | None:
        values = self.headers.get_all(name)
        if not values:
            return default
        if name.title() == "Set-Cookie":
            return values[0]
        return ", ".join(values)


_request_handlers: dict[str, type[request_handler]] = {}
_rh_preferences: set[preference] = set()


def register_rh(handler: type[request_handler]) -> type[request_handler]:
    if not issubclass(handler, request_handler):
        raise TypeError(f"{handler} must subclass request_handler")
    key = handler.rh_key
    if key in _request_handlers:
        raise ValueError(f"request_handler {key} already registered")
    _request_handlers[key] = handler
    return handler


def register_preference(*handlers: type[request_handler]) -> Callable[[preference], preference]:
    def outer(pref: preference) -> preference:
        def inner(handler: request_handler, req: request) -> int:
            if not handlers or isinstance(handler, handlers):
                return pref(handler, req)
            return 0

        _rh_preferences.add(inner)
        return pref

    return outer


def registered_handlers() -> dict[str, type[request_handler]]:
    return dict(_request_handlers)


class request_handler(abc.ABC):
    """send a supported request and return a response."""

    _supported_url_schemes: tuple[str, ...] | None = ()
    _supported_proxy_schemes: tuple[str, ...] | None = ()
    _supported_features: tuple[features, ...] | None = ()

    def __init__(
        self,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | int | None = None,
        proxies: dict[str, str | None] | None = None,
        verify: bool = True,
        verbose: bool = False,
        **_: Any,
    ) -> None:
        self.headers = http_headers(headers or {})
        self.timeout = float(timeout if timeout is not None else default_timeout)
        self.proxies = dict(proxies or {})
        self.verify = verify
        self.verbose = verbose

    def _merge_headers(self, request_headers: Mapping[str, str]) -> http_headers:
        return self.headers.merged(request_headers)

    def _get_headers(self, req: request) -> dict[str, str]:
        merged = self._merge_headers(req.headers)
        if req.extensions.get("keep_header_casing"):
            return merged.sensitive()
        return dict(merged)

    def _calculate_timeout(self, req: request) -> float:
        return float(req.extensions.get("timeout") or self.timeout)

    def _get_proxies(self, req: request) -> dict[str, str | None]:
        return dict(req.proxies or self.proxies)

    def _check_url_scheme(self, req: request) -> str:
        scheme = urllib.parse.urlparse(req.url).scheme.lower()
        if self._supported_url_schemes is not None and scheme not in self._supported_url_schemes:
            raise unsupported_request(f'unsupported url scheme: "{scheme}"', handler=self)
        return scheme

    def _check_proxies(self, proxies: dict[str, str | None]) -> None:
        for proxy_key, proxy_url in proxies.items():
            if proxy_url is None:
                continue
            if proxy_key == "no":
                if (
                    self._supported_features is not None
                    and features.no_proxy not in self._supported_features
                ):
                    raise unsupported_request('"no" proxy is not supported', handler=self)
                continue
            if (
                proxy_key == "all"
                and self._supported_features is not None
                and features.all_proxy not in self._supported_features
            ):
                raise unsupported_request('"all" proxy is not supported', handler=self)
            if self._supported_url_schemes is not None and proxy_key not in (
                *self._supported_url_schemes,
                "all",
            ):
                continue
            if self._supported_proxy_schemes is None:
                continue
            parsed = urllib.parse.urlparse(proxy_url)
            scheme = (parsed.scheme or "").lower()
            if not scheme:
                raise unsupported_request(f'proxy "{proxy_url}" missing scheme', handler=self)
            if scheme not in self._supported_proxy_schemes:
                raise unsupported_request(f'unsupported proxy type: "{scheme}"', handler=self)

    def _check_extensions(self, extensions: dict[str, Any]) -> None:
        extensions.pop("timeout", None)
        extensions.pop("keep_header_casing", None)
        extensions.pop("cookiejar", None)
        extensions.pop("raise_on_status", None)

    def _validate(self, req: request) -> None:
        self._check_url_scheme(req)
        self._check_proxies(req.proxies or self.proxies)
        extensions = dict(req.extensions)
        self._check_extensions(extensions)
        if extensions:
            raise unsupported_request(
                f"unsupported extensions: {', '.join(extensions.keys())}",
                handler=self,
            )

    def validate(self, req: request) -> None:
        if not isinstance(req, request):
            raise TypeError("expected an instance of request")
        try:
            self._validate(req)
        except unsupported_request as exc:
            if exc.handler is None:
                exc.handler = self
            raise

    def send(self, req: request) -> response:
        if not isinstance(req, request):
            raise TypeError("expected an instance of request")
        try:
            return self._send(req)
        except request_error as exc:
            if exc.handler is None:
                exc.handler = self
            raise

    @abc.abstractmethod
    def _send(self, req: request) -> response:
        raise NotImplementedError

    def close(self) -> None:
        return None

    def __enter__(self) -> request_handler:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _rh_name(cls: type[request_handler]) -> str:
    name = cls.__name__
    return name[:-2] if name.endswith("RH") else name


def _rh_key(cls: type[request_handler]) -> str:
    name = cls.__name__
    if not name.endswith("RH"):
        raise AssertionError('request_handler class names must end with "RH"')
    return name[:-2]


class _class_str_prop:
    def __init__(self, fn: Callable[[type], str]) -> None:
        self.fn = fn

    def __get__(self, obj: object, owner: type | None = None) -> str:
        cls = owner if owner is not None else type(obj)
        return self.fn(cls)  # type: ignore[arg-type]


request_handler.rh_name = _class_str_prop(_rh_name)  # type: ignore[misc, assignment]
request_handler.rh_key = _class_str_prop(_rh_key)  # type: ignore[misc, assignment]


class request_director:
    """route each request to a supporting request_handler."""

    def __init__(self, *, verbose: bool = False) -> None:
        self.handlers: dict[str, request_handler] = {}
        self.preferences: set[preference] = set(_rh_preferences)
        self.verbose = verbose

    def close(self) -> None:
        for handler in self.handlers.values():
            handler.close()
        self.handlers.clear()

    def add_handler(self, handler: request_handler) -> None:
        if not isinstance(handler, request_handler):
            raise TypeError("handler must be a request_handler")
        self.handlers[handler.rh_key] = handler

    def _get_handlers(self, req: request) -> list[request_handler]:
        scores = {
            rh: sum(pref(rh, req) for pref in self.preferences) for rh in self.handlers.values()
        }
        return sorted(self.handlers.values(), key=lambda rh: scores.get(rh, 0), reverse=True)

    def send(self, req: request) -> response:
        if not self.handlers:
            raise request_error("no request handlers configured")
        if not isinstance(req, request):
            raise TypeError("expected an instance of request")

        unexpected: list[Exception] = []
        unsupported: list[unsupported_request] = []
        for handler in self._get_handlers(req):
            try:
                handler.validate(req)
            except unsupported_request as exc:
                unsupported.append(exc)
                continue
            try:
                return handler.send(req)
            except request_error:
                raise
            except Exception as exc:
                unexpected.append(exc)
                continue
        raise no_supporting_handlers(unsupported, unexpected)

    def __enter__(self) -> request_director:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
