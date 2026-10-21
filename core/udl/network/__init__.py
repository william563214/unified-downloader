"""udl.network — settings plus yt-dlp-style RequestHandler stack.

Architecture inspired by yt-dlp ``yt_dlp.networking`` (Unlicense). See NOTICE.
Proxy, User-Agent, and rate limits are centralized here. curl_cffi browser
impersonation is opt-in and off by default (same policy as Cloudflare bypass).
"""

from __future__ import annotations

from typing import Any

from udl.network import settings as _settings
from udl.network.common import (
    features,
    http_headers,
    register_preference,
    register_rh,
    registered_handlers,
    request,
    request_director,
    request_handler,
    response,
)
from udl.network.director import (
    active_director,
    available_handlers,
    build_director,
    reset_director,
    send,
)
from udl.network.exceptions import (
    certificate_verify_error,
    http_error,
    incomplete_read,
    no_supporting_handlers,
    proxy_error,
    request_error,
    ssl_error,
    transport_error,
    unsupported_request,
)
from udl.network.impersonate import impersonate_request_handler, impersonate_target
from udl.network.settings import (
    active_network,
    apply_to_session,
    build_proxy_url,
    default_user_agent,
    effective_job_limit_bps,
    external_env,
    format_speed_kib,
    network_cli_bundle,
    network_settings,
    parse_speed_limit,
    proxies_for_requests,
    proxy_mode_custom,
    proxy_mode_none,
    proxy_mode_system,
    resolve_aria2_secret,
    resolve_proxy_password,
    settings_from_cli_args,
)


def configure_network(settings: network_settings) -> None:
    """replace process-wide network settings and rebuild the request director."""
    _settings.configure_network(settings)
    reset_director()


def update_network(**kwargs: Any) -> network_settings:
    """patch active network settings and rebuild the request director."""
    result = _settings.update_network(**kwargs)
    reset_director()
    return result


__all__ = [
    "active_director",
    "active_network",
    "apply_to_session",
    "available_handlers",
    "build_director",
    "build_proxy_url",
    "certificate_verify_error",
    "configure_network",
    "default_user_agent",
    "effective_job_limit_bps",
    "external_env",
    "features",
    "format_speed_kib",
    "http_error",
    "http_headers",
    "impersonate_request_handler",
    "impersonate_target",
    "incomplete_read",
    "network_cli_bundle",
    "network_settings",
    "no_supporting_handlers",
    "parse_speed_limit",
    "proxy_error",
    "proxy_mode_custom",
    "proxy_mode_none",
    "proxy_mode_system",
    "proxies_for_requests",
    "register_preference",
    "register_rh",
    "registered_handlers",
    "request",
    "request_director",
    "request_error",
    "request_handler",
    "reset_director",
    "resolve_aria2_secret",
    "resolve_proxy_password",
    "response",
    "send",
    "settings_from_cli_args",
    "ssl_error",
    "transport_error",
    "unsupported_request",
    "update_network",
]
