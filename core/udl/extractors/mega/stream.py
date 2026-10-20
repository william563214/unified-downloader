"""Localhost-only HTTP stream server that decrypts MEGA content on the fly."""

from __future__ import annotations

import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import requests

from udl.extractors.mega.api import mega_client, public_file_info
from udl.extractors.mega.crypto import make_aes_ctr
from udl.network import apply_to_session, default_user_agent

default_chunk = 256 * 1024


class mega_stream_server:
    """serve one MEGA file over http://127.0.0.1:<port>/<token>/..."""

    def __init__(
        self,
        client: mega_client,
        info: public_file_info,
        *,
        public: bool = True,
    ) -> None:
        self.client = client
        self.info = info
        self.public = public
        self.token = secrets.token_urlsafe(24)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url: str | None = None

    def start(self) -> str:
        info = self.info
        token = self.token
        client = self.client
        public = self.public

        class handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                parts = [p for p in parsed.path.split("/") if p]
                if not parts or parts[0] != token:
                    self.send_error(403, "forbidden")
                    return
                range_header = self.headers.get("Range")
                start = 0
                end = info.size - 1
                if range_header and range_header.startswith("bytes="):
                    spec = range_header[6:].split(",")[0].strip()
                    if "-" in spec:
                        a, b = spec.split("-", 1)
                        if a:
                            start = int(a)
                        if b:
                            end = int(b)
                if start < 0 or end >= info.size or start > end:
                    self.send_error(416, "invalid range")
                    return
                length = end - start + 1
                self.send_response(206 if range_header else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                if range_header:
                    self.send_header(
                        "Content-Range", f"bytes {start}-{end}/{info.size}"
                    )
                self.send_header("Content-Disposition", f'inline; filename="{info.name}"')
                self.end_headers()

                session = requests.Session()
                try:
                    apply_to_session(session)
                    session.headers["User-Agent"] = (
                        client.session.headers.get("User-Agent") or default_user_agent
                    )
                    session.headers["Connection"] = "close"
                    url = info.download_url
                    if end + 1 >= info.size:
                        range_url = f"{url}/{start}"
                    else:
                        range_url = f"{url}/{start}-{end}"
                    resp = session.get(range_url, stream=True, timeout=60)
                    if resp.status_code == 509:
                        self.send_error(509, "mega quota exceeded")
                        return
                    if resp.status_code not in (200, 206):
                        # refresh once
                        try:
                            url = client.refresh_download_url(info, public=public)
                            if end + 1 >= info.size:
                                range_url = f"{url}/{start}"
                            else:
                                range_url = f"{url}/{start}-{end}"
                            resp = session.get(range_url, stream=True, timeout=60)
                        except Exception:  # noqa: BLE001
                            self.send_error(502, "cdn unavailable")
                            return
                    cipher = make_aes_ctr(info.key_a32, info.iv_a32, offset=start)
                    remaining = length
                    for chunk in resp.iter_content(chunk_size=default_chunk):
                        if not chunk or remaining <= 0:
                            break
                        if len(chunk) > remaining:
                            chunk = chunk[:remaining]
                        plain = cipher.decrypt(chunk)
                        try:
                            self.wfile.write(plain)
                        except BrokenPipeError:
                            return
                        remaining -= len(plain)
                finally:
                    session.close()

            def do_HEAD(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                parts = [p for p in parsed.path.split("/") if p]
                if not parts or parts[0] != token:
                    self.send_error(403, "forbidden")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(info.size))
                self.end_headers()

        # bind localhost only
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._httpd = httpd
        port = httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}/{self.token}/{info.name}"
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None
        self.url = None


def start_stream_for_link(
    url: str,
    *,
    client: mega_client | None = None,
) -> dict[str, Any]:
    """resolve a public file link and start a localhost decrypting stream."""
    from udl.extractors.mega.links import parse_link

    info_link = parse_link(url)
    if info_link is None or info_link.kind != "file" or not info_link.key:
        raise ValueError("streaming requires a MEGA public file link with key")
    own = client is None
    client = client or mega_client()
    try:
        file_info = client.get_public_file(info_link.handle, info_link.key)
        server = mega_stream_server(client, file_info, public=True)
        stream_url = server.start()
        return {
            "url": stream_url,
            "token": server.token,
            "name": file_info.name,
            "size": file_info.size,
            "handle": file_info.handle,
            "server": server,
            "client": client if not own else client,
            "owns_client": own,
        }
    except Exception:
        if own:
            client.close()
        raise
