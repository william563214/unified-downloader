"""MEGA-specific errors with stable codes for CLI/GUI."""

from __future__ import annotations


class mega_error(RuntimeError):
    """base MEGA error."""

    code = "mega_error"


class mega_api_error(mega_error):
    """MEGA API returned an error code."""

    code = "mega_api_error"

    def __init__(self, api_code: int, message: str | None = None) -> None:
        self.api_code = api_code
        super().__init__(message or f"MEGA API error {api_code}")


class mega_quota_error(mega_error):
    """HTTP 509 / transfer quota exceeded — no bypass is offered."""

    code = "mega_quota"

    def __init__(self, message: str, *, wait_seconds: int | None = None) -> None:
        self.wait_seconds = wait_seconds
        super().__init__(message)


class mega_integrity_error(mega_error):
    """CBC-MAC integrity check failed."""

    code = "mega_integrity"


class mega_auth_error(mega_error):
    """login / session failure."""

    code = "mega_auth"


class mega_link_error(mega_error):
    """invalid or incomplete public link."""

    code = "mega_link"


class mega_download_error(mega_error):
    """download failed."""

    code = "download_failed"
