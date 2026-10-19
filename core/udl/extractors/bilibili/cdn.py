"""Bilibili CDN candidate ordering and Host preference.

Adapted from ScottSloan/Bili23-Downloader util/network/cdn.py under GPL-3.0.
Uses udl.network settings for preferred CDN hosts and optional Host header.
"""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import ClassVar
from urllib.parse import urlparse, urlunparse

from udl.network import active_network

logger = logging.getLogger(__name__)

host_failure_threshold = 2
host_cooldown_sec = 180

# low-quality / P2P-ish nodes to deprioritize (match hostname substring)
cdn_blacklist = (
    "mcdn",
    "pcdn",
    "szbdyd.com",
    "mountaintoys.cn",
    "upos-sz-mirror14b.bilivideo.com",
    "nexusedgeio.com",
    "ahdohpiechei.com",
)

# sensible CN defaults when user enables prefer_cdn_replace without a list
default_cn_cdn_hosts = (
    "upos-sz-mirrorcos.bilivideo.com",
    "upos-sz-mirrorcosov.bilivideo.com",
    "upos-sz-mirrorcosb1.bilivideo.com",
    "upos-sz-mirrorhw.bilivideo.com",
    "upos-sz-mirrorali.bilivideo.com",
    "upos-hz-mirrorakam.akamaized.net",
)


class host_health:
    """shared per-process CDN host health (cooldown after probe failures)."""

    _lock = Lock()
    _failures: ClassVar[dict[str, int]] = {}
    _cooldown_until: ClassVar[dict[str, float]] = {}

    @classmethod
    def report_success(cls, host: str) -> None:
        if not host:
            return
        with cls._lock:
            cls._failures.pop(host, None)
            cls._cooldown_until.pop(host, None)

    @classmethod
    def report_failure(cls, host: str) -> None:
        if not host:
            return
        with cls._lock:
            count = cls._failures.get(host, 0) + 1
            cls._failures[host] = count
            if count >= host_failure_threshold:
                cls._cooldown_until[host] = time.monotonic() + host_cooldown_sec
                logger.warning(
                    "CDN host %s failed %s times; cooling %ss",
                    host,
                    count,
                    host_cooldown_sec,
                )

    @classmethod
    def is_cooling(cls, host: str) -> bool:
        if not host:
            return False
        with cls._lock:
            until = cls._cooldown_until.get(host)
            if until is None:
                return False
            if time.monotonic() >= until:
                cls._cooldown_until.pop(host, None)
                cls._failures.pop(host, None)
                return False
            return True


def get_netloc(url: str) -> str:
    try:
        return urlparse(url).netloc
    except ValueError:
        return ""


def replace_netloc(url: str, new_netloc: str) -> str:
    parsed = urlparse(url)
    if not new_netloc or new_netloc == parsed.netloc:
        return url
    return urlunparse(parsed._replace(netloc=new_netloc))


def is_mall_course_url(url: str) -> bool:
    return "/mallxcodeboss/" in url or "gen=playurlv2_itemsvideo" in url


def filter_urls(url_list: list[str]) -> list[str]:
    out: list[str] = []
    for url in url_list:
        if not url:
            continue
        netloc = get_netloc(url)
        if any(domain in netloc for domain in cdn_blacklist):
            continue
        out.append(url)
    return out


def preferred_cdn_hosts() -> list[str]:
    cfg = active_network()
    hosts = [h.strip() for h in (cfg.cdn_hosts or []) if h and str(h).strip()]
    if hosts:
        return hosts
    if cfg.prefer_cdn_replace:
        return list(default_cn_cdn_hosts)
    return []


def replace_with_preferred(url_list: list[str]) -> list[str]:
    hosts = preferred_cdn_hosts()
    if not hosts:
        return []
    out: list[str] = []
    for url in url_list:
        if not url or is_mall_course_url(url):
            continue
        for host in hosts:
            out.append(replace_netloc(url, host))
    return out


def arrange(url_list: list[str], *, exclude: set[str] | None = None) -> list[str]:
    healthy: list[str] = []
    cooling: list[str] = []
    seen: set[str] = set()
    for url in url_list:
        if not url or url in seen or (exclude and url in exclude):
            continue
        seen.add(url)
        if host_health.is_cooling(get_netloc(url)):
            cooling.append(url)
        else:
            healthy.append(url)
    return healthy + cooling


def candidate_urls(url_list: list[str]) -> list[str]:
    """ordered stream URL candidates: preferred CDN then originals as fallback."""
    filtered = filter_urls(url_list)
    if not filtered:
        filtered = [u for u in url_list if u]
    replaced = replace_with_preferred(filtered)
    cfg = active_network()
    if cfg.prefer_cdn_replace and replaced:
        primary, fallback = replaced, filtered
    elif replaced:
        primary, fallback = filtered, replaced
    else:
        return arrange(filtered)
    primary_ordered = arrange(primary)
    return primary_ordered + arrange(fallback, exclude=set(primary_ordered))


def stream_headers() -> dict[str, str]:
    """extra headers for bilibili media downloads (Referer + optional Host)."""
    headers = {"Referer": "https://www.bilibili.com/"}
    host = (active_network().custom_host or "").strip()
    if host:
        headers["Host"] = host
    return headers
