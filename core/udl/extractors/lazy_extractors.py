"""AUTO-GENERATED lazy extractor stubs — do not edit by hand.

Regenerate:
  python scripts/make_lazy_extractors.py

Inspired by yt-dlp make_lazy_extractors (Unlicense, commit 8973412).
"""

from __future__ import annotations

from udl.extractors.base import extractor
from udl.extractors.lazy_load import lazy_load_extractor


class gofile_extractor(lazy_load_extractor):
    _module = 'udl.extractors.gofile'
    site = 'gofile'
    label = 'Gofile'
    status = 'implemented'
    kind_hint = 'folder|file'
    _VALID_URL = '(?i)https?://(?:www\\.)?gofile\\.io/d/[A-Za-z0-9]+(?:[/?#]|$)'


class bunkr_extractor(lazy_load_extractor):
    _module = 'udl.extractors.bunkr'
    site = 'bunkr'
    label = 'Bunkr'
    status = 'implemented'
    kind_hint = 'album|file'
    _VALID_URL = (
        '(?i)https?://(?:[^/]*\\.)?bunkr\\.(?:si|fi|cr|ph|pk|ps|sk|ws|ac|ci|black|red|media|site|by|ru|is|to|ax|sx|pa|org|com|net|nu)/(?:a|f|i|v)/[A-Za-z0-9_-]+',
        '(?i)https?://[^/]*bunkr[^/]*/(?:a|f|i|v)/[A-Za-z0-9_-]+',
    )


class iwara_extractor(lazy_load_extractor):
    _module = 'udl.extractors.iwara'
    site = 'iwara'
    label = 'Iwara'
    status = 'implemented'
    kind_hint = 'video|playlist|profile'
    _VALID_URL = '(?i)https?://(?:[^/]+\\.)?(?:iwara\\.tv|iwara\\.ai)(?:[/?#]|$|/.*)'


class bilibili_extractor(lazy_load_extractor):
    _module = 'udl.extractors.bilibili.extractor'
    site = 'bilibili'
    label = 'Bilibili'
    status = 'implemented'
    kind_hint = 'video|bangumi|cheese|audio|space|favlist|popular|collection'
    _VALID_URL = '(?i)https?://(?:[^/]+\\.)?(?:bilibili\\.com|b23\\.tv|bili2233\\.cn|biliapi\\.net)(?:[/?#]|$|/.*)'


class mega_extractor(lazy_load_extractor):
    _module = 'udl.extractors.mega.extractor'
    site = 'mega'
    label = 'MEGA'
    status = 'implemented'
    kind_hint = 'file|folder'
    _VALID_URL = (
        '(?i)https?://(?:www\\.)?mega(?:\\.co)?\\.(?:nz|io)/(?:file|folder|embed)/[A-Za-z0-9_-]+(?:#.*)?',
        '(?i)https?://(?:www\\.)?mega(?:\\.co)?\\.(?:nz|io)/#!?[A-Za-z0-9_-]+![A-Za-z0-9_-]+.*',
        '(?i)https?://(?:www\\.)?mega(?:\\.co)?\\.(?:nz|io)/#F![A-Za-z0-9_-]+![A-Za-z0-9_-]+.*',
        '(?i)mega://node/[A-Za-z0-9_-]+/?$',
        '(?i)mega:node:[A-Za-z0-9_-]+/?$',
    )


class gallerydl_extractor(lazy_load_extractor):
    _module = 'udl.extractors.external.gallerydl'
    site = 'gallerydl'
    label = 'gallery-dl'
    status = 'external'
    kind_hint = 'gallery|file'
    _VALID_URL = False

    @classmethod
    def suitable(cls, url: str) -> bool:
        if cls._match_valid_url(url) is not None:
            return True
        from udl.extractors.external.gallerydl import matches
        return matches(url)


class direct_extractor(lazy_load_extractor):
    _module = 'udl.extractors.direct'
    site = 'direct'
    label = 'Direct'
    status = 'implemented'
    kind_hint = 'file'
    _VALID_URL = '(?i)https?://[^/\\s]+(?:[/\\?#].*)?$'


extractors: list[type[extractor]] = [
    gofile_extractor,
    bunkr_extractor,
    iwara_extractor,
    bilibili_extractor,
    mega_extractor,
    gallerydl_extractor,
    direct_extractor,
]

__all__ = [
    'extractors',
    'gofile_extractor',
    'bunkr_extractor',
    'iwara_extractor',
    'bilibili_extractor',
    'mega_extractor',
    'gallerydl_extractor',
    'direct_extractor',
]
