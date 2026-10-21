"""RequestHandler error hierarchy (yt-dlp networking style)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from udl.network.common import request_handler, response


class request_error(Exception):
    """base error raised by request handlers."""

    def __init__(
        self,
        msg: str | None = None,
        *,
        cause: Exception | str | None = None,
        handler: request_handler | None = None,
    ) -> None:
        self.handler = handler
        self.cause = cause
        if not msg and cause is not None:
            msg = str(cause)
        super().__init__(msg or "request error")


class unsupported_request(request_error):
    """handler cannot process this request."""


class no_supporting_handlers(request_error):
    """no registered handler accepted the request."""

    def __init__(
        self,
        unsupported_errors: list[unsupported_request],
        unexpected_errors: list[Exception],
    ) -> None:
        self.unsupported_errors = unsupported_errors or []
        self.unexpected_errors = unexpected_errors or []
        groups: dict[str, list[str]] = {}
        for err in self.unsupported_errors:
            name = err.handler.rh_name if err.handler is not None else "?"
            groups.setdefault(str(err), []).append(name)
        parts = [f"{msg} ({', '.join(names)})" for msg, names in groups.items()]
        if self.unexpected_errors:
            parts.append(f"{len(self.unexpected_errors)} unexpected error(s)")
        detail = "; ".join(parts)
        if detail:
            super().__init__(f"unable to handle request: {detail}")
        else:
            super().__init__("unable to handle request")


class transport_error(request_error):
    """network / transport failure."""


class http_error(request_error):
    """non-success HTTP status (when raise_on_status is enabled)."""

    def __init__(self, resp: response, *, redirect_loop: bool = False) -> None:
        self.response = resp
        self.status = resp.status
        self.reason = resp.reason
        self.redirect_loop = redirect_loop
        msg = f"HTTP Error {resp.status}: {resp.reason}"
        if redirect_loop:
            msg += " (redirect loop detected)"
        super().__init__(msg)

    def close(self) -> None:
        self.response.close()


class incomplete_read(transport_error):
    def __init__(
        self,
        partial: int,
        expected: int | None = None,
        *,
        cause: Exception | str | None = None,
        handler: request_handler | None = None,
    ) -> None:
        self.partial = partial
        self.expected = expected
        msg = f"{partial} bytes read"
        if expected is not None:
            msg += f", {expected} more expected"
        super().__init__(msg, cause=cause, handler=handler)


class ssl_error(transport_error):
    pass


class certificate_verify_error(ssl_error):
    pass


class proxy_error(transport_error):
    pass
