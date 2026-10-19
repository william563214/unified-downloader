"""Login-gated API stubs for Bilibili account-only lists.

QR/SMS/Cookie login lands in a later PR once udl.secrets is available.
Session cookies can be injected via bilibili_client.set_session_cookies.
"""

from __future__ import annotations

login_required_kinds = frozenset(
    {
        "watchlater",
        "history",
        "followed_bangumi",
        "subscribed_collections",
        "favorite_folders",  # own folder list (public media_id still works)
    }
)

login_stub_note = (
    "QR/SMS/cookie login is deferred to a follow-up PR. "
    "Account-only lists raise requires_login. "
    "Plug SESSDATA via bilibili_client.set_session_cookies when udl.secrets lands."
)


class requires_login(RuntimeError):
    """raised for account-only endpoints while anonymous."""

    code = "requires_login"

    def __init__(self, kind: str, *, detail: str | None = None) -> None:
        message = (
            f"bilibili '{kind}' requires login "
            "(QR/SMS/cookie not implemented in this PR)"
        )
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.kind = kind


def ensure_not_login_only(kind: str) -> None:
    if kind in login_required_kinds:
        raise requires_login(kind)
