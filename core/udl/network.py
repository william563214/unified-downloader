"""Global networking settings: proxy user-agent speed limits aria2.

Process-wide config is set by the CLI (or GUI-spawned sidecar) and consumed
by the shared HTTP engine extractors and external tool wrappers.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from requests import Session

from udl.secrets import aria2_secret_key, get_secret, proxy_password_key

# lowercase constants per project convention
default_user_agent = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
proxy_mode_none = "none"
proxy_mode_system = "system"
proxy_mode_custom = "custom"

re_speed = re.compile(
    r"^\s*(?P<num>[\d.]+)\s*(?P<unit>[kmgt]i?b?|b)?(?:/?s)?\s*$",
    re.I,
)


@dataclass
class network_settings:
    """process-wide / per-invocation network options."""

    user_agent: str = default_user_agent
    proxy_mode: str = proxy_mode_none
    # custom proxy base URL without credentials e.g. http://host:8080 socks5://127.0.0.1:1080
    proxy_url: str | None = None
    proxy_username: str | None = None
    # password normally loaded from keyring; may be set for one-shot CLI
    proxy_password: str | None = None
    # overall process speed cap in bytes/sec; None = unlimited
    speed_limit_bps: int | None = None
    # optional per-job cap in bytes/sec
    job_speed_limit_bps: int | None = None
    aria2_enabled: bool = False
    aria2_rpc_url: str = "http://127.0.0.1:6800/jsonrpc"
    aria2_rpc_secret: str | None = None
    # multi-connection segmented downloads (v0.9-A); aria2 still optional backend
    segmented_enabled: bool = True
    # max connections per file (IDM-style dynamic segmentation)
    connections: int = 8
    # hard cap per host across segments of one file
    per_host_connections: int = 8
    # when True also use segmented for site extractors that yield plain HTTP
    # (gofile bunkr iwara mega always excluded)
    segmented_for_sites: bool = False
    # bilibili CDN / Host (shared networking settings; used by bili stream download)
    # preferred CDN hostnames e.g. upos-sz-mirrorcos.bilivideo.com
    cdn_hosts: list[str] = field(default_factory=list)
    # when True put replaced CDN URLs ahead of original playurl hosts
    prefer_cdn_replace: bool = False
    # optional Host header override for media downloads (empty = unset)
    custom_host: str | None = None


_active: network_settings = network_settings()


def active_network() -> network_settings:
    return _active


def configure_network(settings: network_settings) -> None:
    """replace the process-wide network settings."""
    global _active
    _active = settings


def update_network(**kwargs: Any) -> network_settings:
    """patch and return the active network settings."""
    global _active
    _active = replace(_active, **kwargs)
    return _active


def parse_speed_limit(raw: str | int | float | None) -> int | None:
    """parse human speed like 1M 512K 1024 into bytes/sec. None/0/empty = unlimited."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        value = int(raw)
        return value if value > 0 else None
    text = str(raw).strip()
    if not text or text.lower() in {"0", "off", "none", "unlimited", "-"}:
        return None
    m = re_speed.match(text)
    if not m:
        raise ValueError(f"invalid speed limit: {raw}")
    num = float(m.group("num"))
    unit = (m.group("unit") or "b").lower()
    # accept 1M / 1MB / 1MiB / 512K etc
    if unit in {"ki", "mi", "gi", "ti"}:
        unit = unit + "b"
    mult = {
        "b": 1,
        "k": 1000,
        "kb": 1000,
        "kib": 1024,
        "m": 1000**2,
        "mb": 1000**2,
        "mib": 1024**2,
        "g": 1000**3,
        "gb": 1000**3,
        "gib": 1024**3,
        "t": 1000**4,
        "tb": 1000**4,
        "tib": 1024**4,
    }.get(unit)
    if mult is None:
        raise ValueError(f"invalid speed unit in: {raw}")
    bps = int(num * mult)
    return bps if bps > 0 else None


def format_speed_kib(bps: int | None) -> int | None:
    """megatools --limit-speed expects KiB/s integer."""
    if bps is None or bps <= 0:
        return None
    return max(1, int(round(bps / 1024.0)))


def resolve_proxy_password(settings: network_settings | None = None) -> str | None:
    cfg = settings or _active
    if cfg.proxy_password:
        return cfg.proxy_password
    return get_secret(proxy_password_key)


def resolve_aria2_secret(settings: network_settings | None = None) -> str | None:
    cfg = settings or _active
    if cfg.aria2_rpc_secret:
        return cfg.aria2_rpc_secret
    return get_secret(aria2_secret_key)


def build_proxy_url(settings: network_settings | None = None) -> str | None:
    """return a requests/curl-compatible proxy URL for custom mode else None."""
    cfg = settings or _active
    if cfg.proxy_mode != proxy_mode_custom:
        return None
    raw = (cfg.proxy_url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    username = (cfg.proxy_username or "").strip() or (parsed.username or "")
    password = resolve_proxy_password(cfg) or (parsed.password or "")
    if not username and not password:
        return urlunparse(parsed._replace(netloc=parsed.hostname or parsed.netloc))

    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    user = quote(username, safe="")
    if password:
        auth = f"{user}:{quote(password, safe='')}@"
    else:
        auth = f"{user}@"
    netloc = f"{auth}{host}{port}"
    return urlunparse(parsed._replace(netloc=netloc))


def proxies_for_requests(settings: network_settings | None = None) -> dict[str, str | None] | None:
    """map for Session.proxies. None means leave requests defaults."""
    cfg = settings or _active
    mode = (cfg.proxy_mode or proxy_mode_none).lower()
    if mode == proxy_mode_none:
        return {"http": None, "https": None}
    if mode == proxy_mode_system:
        return None
    url = build_proxy_url(cfg)
    if not url:
        return {"http": None, "https": None}
    return {"http": url, "https": url, "all": url}


def apply_to_session(session: Session, settings: network_settings | None = None) -> Session:
    """apply proxy + User-Agent to an existing requests Session."""
    cfg = settings or _active
    if cfg.user_agent:
        session.headers["User-Agent"] = cfg.user_agent
    mode = (cfg.proxy_mode or proxy_mode_none).lower()
    if mode == proxy_mode_system:
        session.trust_env = True
        # clear any previous explicit proxies so env / system apply
        session.proxies.clear()
    elif mode == proxy_mode_custom:
        session.trust_env = False
        proxies = proxies_for_requests(cfg) or {}
        session.proxies.clear()
        session.proxies.update({k: v for k, v in proxies.items() if v})
    else:
        session.trust_env = False
        session.proxies.clear()
        session.proxies.update({"http": None, "https": None})  # type: ignore[dict-item]
    return session


def external_env(settings: network_settings | None = None) -> dict[str, str]:
    """environment variables for child processes that honour HTTP(S)_PROXY."""
    cfg = settings or _active
    env = dict(os.environ)
    mode = (cfg.proxy_mode or proxy_mode_none).lower()
    if mode == proxy_mode_none:
        for key in (
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "all_proxy",
            "ALL_PROXY",
        ):
            env.pop(key, None)
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
    elif mode == proxy_mode_custom:
        url = build_proxy_url(cfg)
        if url:
            env["http_proxy"] = url
            env["https_proxy"] = url
            env["HTTP_PROXY"] = url
            env["HTTPS_PROXY"] = url
            env["all_proxy"] = url
            env["ALL_PROXY"] = url
    # system mode: leave parent env as-is
    if cfg.user_agent:
        env["UDL_USER_AGENT"] = cfg.user_agent
    return env


def effective_job_limit_bps(job_limit: int | None = None) -> int | None:
    """combine global and optional job limits into the tighter cap."""
    global_bps = _active.speed_limit_bps
    job_bps = job_limit if job_limit is not None else _active.job_speed_limit_bps
    limits = [b for b in (global_bps, job_bps) if b is not None and b > 0]
    if not limits:
        return None
    return min(limits)


@dataclass
class network_cli_bundle:
    """parsed CLI networking flags ready for configure_network."""

    settings: network_settings = field(default_factory=network_settings)


def settings_from_cli_args(args: Any) -> network_settings:
    """build network_settings from an argparse namespace."""
    ua = getattr(args, "user_agent", None) or default_user_agent
    proxy_mode = (getattr(args, "proxy_mode", None) or proxy_mode_none).lower()
    proxy_url = getattr(args, "proxy", None)
    # allow --proxy URL shorthand to imply custom mode
    if proxy_url and proxy_mode == proxy_mode_none:
        low = str(proxy_url).strip().lower()
        if low in {proxy_mode_none, proxy_mode_system}:
            proxy_mode = low
            proxy_url = None
        else:
            proxy_mode = proxy_mode_custom
    speed = parse_speed_limit(getattr(args, "speed_limit", None))
    job_speed = parse_speed_limit(getattr(args, "job_speed_limit", None))
    aria2_enabled = bool(getattr(args, "aria2", False))
    aria2_url = getattr(args, "aria2_rpc", None) or "http://127.0.0.1:6800/jsonrpc"
    aria2_secret = getattr(args, "aria2_secret", None)
    connections = getattr(args, "connections", None)
    if connections is None:
        connections = 8
    else:
        connections = max(1, min(int(connections), 32))
    per_host = getattr(args, "per_host_connections", None)
    if per_host is None:
        per_host = connections
    else:
        per_host = max(1, min(int(per_host), 32))
    segmented_enabled = not bool(getattr(args, "no_segmented", False))
    if getattr(args, "segmented", None) is False:
        segmented_enabled = False
    segmented_for_sites = bool(getattr(args, "segmented_for_sites", False))
    cdn_raw = getattr(args, "cdn_hosts", None) or getattr(args, "bili_cdn", None)
    cdn_hosts: list[str] = []
    if cdn_raw:
        cdn_hosts = [p.strip() for p in str(cdn_raw).split(",") if p.strip()]
    custom_host = getattr(args, "custom_host", None) or getattr(args, "bili_host", None)
    prefer_cdn = bool(
        getattr(args, "prefer_cdn", False) or getattr(args, "bili_prefer_cdn", False)
    )
    return network_settings(
        user_agent=str(ua),
        proxy_mode=proxy_mode,
        proxy_url=str(proxy_url).strip() if proxy_url else None,
        proxy_username=getattr(args, "proxy_user", None) or None,
        proxy_password=getattr(args, "proxy_password", None) or None,
        speed_limit_bps=speed,
        job_speed_limit_bps=job_speed,
        aria2_enabled=aria2_enabled,
        aria2_rpc_url=str(aria2_url),
        aria2_rpc_secret=str(aria2_secret) if aria2_secret else None,
        segmented_enabled=segmented_enabled,
        connections=connections,
        per_host_connections=per_host,
        segmented_for_sites=segmented_for_sites,
        cdn_hosts=cdn_hosts,
        prefer_cdn_replace=prefer_cdn or bool(cdn_hosts),
        custom_host=str(custom_host).strip() if custom_host else None,
    )
