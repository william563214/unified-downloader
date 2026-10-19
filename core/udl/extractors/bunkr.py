"""Bunkr extractor.

Ported / adapted from Lysagxra/BunkrDownloader
https://github.com/Lysagxra/BunkrDownloader
commit a58790ff540e8d9b0579c1b1c4d1b49d4fa9b8da
License: GPL-3.0

Album (/a/) and single media (/f/ /v/ /i/) resolution with signing API.
Domains rotate — keep fallback_domain and patterns maintainable.
"""

from __future__ import annotations

import html
import re
import time
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from requests import Session

from udl.extractors.base import extract_result, extracted_file, extractor

# from upstream config
bunkr_api = "https://glb-apisign.cdn.cr/sign"
download_api = "https://dl.bunkr.cr/api/_001_v2"
download_referer = "https://dl.bunkrr.cr/"
fallback_domain = "bunkr.cr"
media_slug_regex = re.compile(r'const\s+slug\s*=\s*"([a-zA-Z0-9_-]+)"')
valid_slug_regex = re.compile(r"^[a-zA-Z0-9_-]+$")
js_vars_comp = re.compile(r'var\s+(\w+)\s*=\s*(".*?"|\'.*?\'|[^;]+);', re.DOTALL)
invalid_chars = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# known bunkr host suffixes (pattern reference — not copied from gallery-dl)
bunkr_host_re = re.compile(
    r"(?:^|\.)bunkr\.(?:"
    r"si|fi|cr|ph|pk|ps|sk|ws|ac|ci|black|red|media|site|by|ru|is|to|ax|sx|pa|"
    r"org|com|net|nu"
    r")$",
    re.I,
)

url_type_album = "album"
url_type_media = "media"
url_type_map = {
    "a": url_type_album,
    "f": url_type_media,
    "i": url_type_media,
    "v": url_type_media,
}

default_headers = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:136.0) Gecko/20100101 Firefox/136.0"
    ),
}
download_headers = {
    **default_headers,
    "Connection": "keep-alive",
    "Referer": download_referer,
}
max_retries = 3
base_delay = 2.0
request_timeout = 20.0


def unescape_js_path(value: str) -> str:
    return value.replace(r"\/", "/").replace(r"\\", "\\")


def extract_page_vars(soup: BeautifulSoup) -> dict[str, str]:
    for script in soup.find_all("script"):
        text = script.string
        if text and "var jsCDN" in text:
            matches = js_vars_comp.findall(text)
            return {key: unescape_js_path(value).strip("'\"") for key, value in matches}
    return {}


def extract_file_id(soup: BeautifulSoup) -> str | None:
    script = soup.find("script")
    if not script:
        return None
    return script.get("data-file-id")


def decrypt_cf_email(cf_email_hex: str) -> str:
    raw = bytes.fromhex(cf_email_hex)
    key = raw[0]
    return bytes(b ^ key for b in raw[1:]).decode("utf-8")


def get_host_page(url: str) -> str:
    return f"https://{urlparse(url).netloc}"


def normalize_url(url: str) -> str:
    if not url.startswith("https://") and not url.startswith("http://"):
        url = f"https://{url}"
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query.pop("page", None)
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def resolve_url_type(url: str) -> str:
    try:
        segment = url.rstrip("/").split("/")[-2]
    except IndexError as exc:
        raise ValueError(f"invalid bunkr URL: {url}") from exc
    if segment not in url_type_map:
        raise ValueError(f"unknown bunkr URL type '{segment}' in {url}")
    return url_type_map[segment]


def replace_domain_with_fallback(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(netloc=fallback_domain))


def get_album_id(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def get_album_name(soup: BeautifulSoup) -> str | None:
    container = soup.find(
        "div",
        {"class": "text-subs font-semibold flex text-base sm:text-lg"},
    )
    if not container:
        return None
    h1 = container.find("h1")
    if not h1:
        return None
    raw = html.unescape(h1.get_text(strip=True))
    try:
        fixed = raw.encode("latin1").decode("utf-8")
        if fixed != raw:
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return raw


def extract_item_pages(soup: BeautifulSoup, host_page: str) -> list[str]:
    items = soup.find_all(
        "a",
        {"class": "after:absolute after:z-10 after:inset-0", "href": True},
    )
    return [f"{host_page}{item.get('href')}" for item in items]


def extract_next_album_pages(soup: BeautifulSoup, url: str) -> list[str]:
    nav = soup.find("nav", {"class": "pagination"})
    if nav is None:
        return []
    page_ids = re.findall(r"\d+", nav.get_text())
    if not page_ids:
        return []
    num_pages = max(int(p) for p in page_ids)
    return [f"{url}?page={page}" for page in range(2, num_pages + 1)]


def get_item_filename(soup: BeautifulSoup) -> str:
    container = soup.find(
        "h1",
        {"class": "text-subs font-semibold text-base sm:text-lg truncate"},
    )
    if not container:
        return "download.bin"
    cf = container.find(class_="__cf_email__")
    if cf and cf.get("data-cfemail"):
        cf.replace_with(decrypt_cf_email(cf["data-cfemail"]))
    name = container.get_text(strip=True)
    try:
        return name.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def sanitize_filename(name: str) -> str:
    cleaned = invalid_chars.sub("_", name).strip().rstrip(".")
    return cleaned or "download.bin"


class bunkr_extractor(extractor):
    site = "bunkr"
    label = "Bunkr"

    def __init__(self) -> None:
        self._session = Session()
        self._session.headers.update(default_headers)

    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        del password  # bunkr public albums do not use password in upstream CLI
        url = normalize_url(url)
        kind = resolve_url_type(url)
        soup = self._fetch_page(url)
        if soup is None:
            raise RuntimeError(f"failed to fetch bunkr page: {url}")

        if kind == url_type_album:
            return self._resolve_album(url, soup)
        return self._resolve_media(url, soup)

    def _fetch_page(self, url: str) -> BeautifulSoup | None:
        tried_fallback = False
        current = url
        for _ in range(max_retries):
            try:
                resp = self._session.get(current, timeout=request_timeout)
                if resp.status_code == 403 and not tried_fallback:
                    tried_fallback = True
                    current = replace_domain_with_fallback(current)
                    continue
                resp.raise_for_status()
                return BeautifulSoup(resp.content, "html.parser")
            except requests.RequestException:
                time.sleep(base_delay)
        return None

    def _request_json(
        self,
        method: str,
        api_url: str,
        *,
        json_body: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        headers = {"Accept-Encoding": "gzip, deflate"} if method.upper() == "POST" else None
        for attempt in range(1, max_retries + 1):
            try:
                resp = self._session.request(
                    method,
                    api_url,
                    json=json_body,
                    params=params,
                    headers=headers,
                    timeout=request_timeout,
                )
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError):
                if attempt < max_retries:
                    time.sleep(base_delay * (2 ** (attempt - 1)))
        return None

    def _get_download_response(self, file_id: str) -> str | None:
        data = self._request_json("POST", download_api, json_body={"id": file_id})
        if not data:
            return None
        base_url = data.get("mediafiles")
        path = data.get("path")
        if not base_url or not path:
            return None
        parsed = urlparse(str(base_url))
        return urlunparse(parsed._replace(path=str(path)))

    def _sign_url(self, item_url: str, soup: BeautifulSoup) -> str | None:
        """resolve and sign a media URL (upstream get_api_response)."""
        page_vars = extract_page_vars(soup)
        cdn_url = page_vars.get("jsCDN")
        file_id = extract_file_id(soup) if not page_vars else None
        unsigned = self._get_download_response(file_id) if file_id else None

        if not cdn_url and not unsigned:
            return None

        media_slug = PurePosixPath(urlparse(unsigned or item_url).path).name
        media_path = urlparse(cdn_url).path if cdn_url else f"/storage/media/{media_slug}"

        data = self._request_json("GET", bunkr_api, params={"path": media_path})
        if not data:
            return None

        token = data.get("token")
        expires_at = data.get("ex")
        base_url = cdn_url or unsigned
        if token and expires_at and base_url:
            return f"{base_url}?token={token}&ex={expires_at}"
        return cdn_url

    def _resolve_media(self, url: str, soup: BeautifulSoup) -> extract_result:
        link = self._sign_url(url, soup)
        if not link:
            raise RuntimeError(f"could not resolve download URL for {url}")
        name = sanitize_filename(get_item_filename(soup))
        album_id = get_album_id(url)
        return extract_result(
            site=self.site,
            title=name,
            files=[
                extracted_file(
                    url=link,
                    name=name,
                    relative_dir=album_id,
                    headers=dict(download_headers),
                )
            ],
            kind="file",
            meta={"url": url},
        )

    def _resolve_album(self, url: str, soup: BeautifulSoup) -> extract_result:
        host = get_host_page(url)
        album_id = get_album_id(url)
        album_name = get_album_name(soup) or album_id
        item_pages = extract_item_pages(soup, host)
        for next_page in extract_next_album_pages(soup, url):
            next_soup = self._fetch_page(next_page)
            if next_soup is None:
                raise RuntimeError(f"failed paginated album page: {next_page}")
            item_pages.extend(extract_item_pages(next_soup, host))

        if not item_pages:
            raise RuntimeError(f"no items in album {url}")

        folder = sanitize_filename(f"{album_name} ({album_id})")
        files: list[extracted_file] = []
        reserved: set[str] = set()
        for page in item_pages:
            item_soup = self._fetch_page(page)
            if item_soup is None:
                continue
            link = self._sign_url(page, item_soup)
            if not link:
                continue
            raw_name = sanitize_filename(get_item_filename(item_soup))
            name = raw_name
            n = 1
            while name.lower() in reserved:
                stem = PurePosixPath(raw_name).stem
                suffix = PurePosixPath(raw_name).suffix
                name = f"{stem}_{n}{suffix}"
                n += 1
            reserved.add(name.lower())
            files.append(
                extracted_file(
                    url=link,
                    name=name,
                    relative_dir=folder,
                    headers=dict(download_headers),
                    meta={"page": page},
                )
            )

        if not files:
            raise RuntimeError(f"could not resolve any files in album {url}")

        return extract_result(
            site=self.site,
            title=album_name,
            files=files,
            kind="album",
            meta={"album_id": album_id, "items": len(item_pages)},
        )


def matches(url: str) -> bool:
    host = urlparse(url).netloc.lower().split(":")[0]
    if not bunkr_host_re.search(host) and "bunkr" not in host:
        return False
    try:
        resolve_url_type(normalize_url(url))
        return True
    except ValueError:
        return False
