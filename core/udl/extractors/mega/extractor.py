"""MEGA extractor — built-in Python client with optional external CLI fallback."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from typing import Any

from udl.download.rate import configure_global_limiter
from udl.events import emitter
from udl.extractors.base import extract_result, extracted_file, extractor
from udl.extractors.mega import auth as mega_auth
from udl.extractors.mega.api import build_folder_paths, mega_client
from udl.extractors.mega.download import download_mega_file
from udl.extractors.mega.errors import mega_download_error, mega_link_error, mega_quota_error
from udl.extractors.mega.links import matches, parse_link
from udl.metadata import item_metadata
from udl.network import active_network, effective_job_limit_bps

backend_builtin = "builtin"
backend_external = "external"
mega_backend_env = "UDL_MEGA_BACKEND"


class mega_extractor(extractor):
    site = "mega"
    label = "MEGA"

    def __init__(
        self,
        *,
        backend: str | None = None,
        mega_tool: str | Path | None = None,
        selected_handles: list[str] | None = None,
        verify_mac: bool = True,
        workers: int | None = None,
    ) -> None:
        self.backend = _resolve_backend(backend)
        self.mega_tool_override = mega_tool
        self.selected_handles = set(selected_handles or [])
        self.verify_mac = verify_mac
        self.workers_override = workers
        self._client: mega_client | None = None

    def configure(self, **kwargs: object) -> None:
        if "mega_tool" in kwargs and kwargs["mega_tool"] is not None:
            self.mega_tool_override = str(kwargs["mega_tool"])  # type: ignore[assignment]
        if "mega_backend" in kwargs and kwargs["mega_backend"] is not None:
            self.backend = _resolve_backend(str(kwargs["mega_backend"]))
        if "backend" in kwargs and kwargs["backend"] is not None:
            self.backend = _resolve_backend(str(kwargs["backend"]))
        if "selected_handles" in kwargs and kwargs["selected_handles"] is not None:
            raw = kwargs["selected_handles"]
            if isinstance(raw, str):
                self.selected_handles = {h for h in raw.split(",") if h}
            elif isinstance(raw, (list, set, tuple)):
                self.selected_handles = {str(h) for h in raw}
        if "verify_mac" in kwargs and kwargs["verify_mac"] is not None:
            self.verify_mac = bool(kwargs["verify_mac"])

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> mega_client:
        if self._client is None:
            logged = mega_auth.load_session()
            self._client = logged if logged is not None else mega_client()
        return self._client

    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        del password
        if self.backend == backend_external:
            return self._resolve_external(url)

        from udl.extractors.mega.links import parse_cloud_node

        cloud_handle = parse_cloud_node(url)
        if cloud_handle:
            return self._resolve_cloud_node(cloud_handle, url)

        info = parse_link(url)
        if info is None:
            raise mega_link_error(f"not a MEGA public link: {url}")
        if not info.key:
            raise mega_link_error(
                "MEGA link is missing the decryption key (fragment after #)"
            )

        client = self._get_client()
        if info.kind == "file":
            file_info = client.get_public_file(info.handle, info.key)
            meta = item_metadata(
                site=self.site,
                item_id=info.handle,
                canonical_url=info.url,
                title=file_info.name,
                extra={
                    "kind": "file",
                    "backend": backend_builtin,
                    "size": file_info.size,
                },
            )
            f = extracted_file(
                url=info.url,
                name=_safe_name(file_info.name),
                relative_dir="",
                size=file_info.size,
                meta={
                    "handle": info.handle,
                    "kind": "file",
                    "key": info.key,
                    "public": True,
                },
                metadata=meta,
            )
            return extract_result(
                site=self.site,
                title=file_info.name,
                files=[f],
                kind="file",
                meta={"handle": info.handle, "backend": backend_builtin, "tree": []},
                metadata=meta,
            )

        nodes = client.list_public_folder(
            info.handle, info.key, sub_handle=info.sub_handle
        )
        paths = build_folder_paths(nodes)
        files: list[extracted_file] = []
        tree: list[dict[str, Any]] = []
        folder_title = info.handle
        for n in nodes:
            if n.kind == 1 and (n.parent is None or n.handle == info.handle):
                folder_title = n.name or folder_title
            rel = paths.get(n.handle, n.name)
            tree.append(
                {
                    "handle": n.handle,
                    "parent": n.parent,
                    "name": n.name,
                    "kind": "file" if n.is_file else "folder",
                    "size": n.size if n.is_file else None,
                    "path": rel,
                }
            )
            if not n.is_file or n.key_a32 is None:
                continue
            if self.selected_handles and n.handle not in self.selected_handles:
                continue
            parent_dir = str(Path(rel).parent) if "/" in rel else ""
            if parent_dir == ".":
                parent_dir = ""
            file_meta = item_metadata(
                site=self.site,
                item_id=n.handle,
                canonical_url=info.url,
                title=n.name,
                collection=folder_title,
                collection_id=info.handle,
                extra={"kind": "file", "folder": True, "size": n.size},
            )
            files.append(
                extracted_file(
                    url=info.url,
                    name=_safe_name(n.name),
                    relative_dir=parent_dir,
                    size=n.size,
                    meta={
                        "handle": n.handle,
                        "kind": "file",
                        "key_a32": list(n.key_a32),
                        "public": True,
                        "folder_handle": info.handle,
                        "folder_key": info.key,
                    },
                    metadata=file_meta,
                )
            )

        meta = item_metadata(
            site=self.site,
            item_id=info.handle,
            canonical_url=info.url,
            title=folder_title,
            collection=folder_title,
            collection_id=info.handle,
            extra={"kind": "folder", "backend": backend_builtin, "files": len(files)},
        )
        return extract_result(
            site=self.site,
            title=folder_title,
            files=files,
            kind="folder",
            meta={
                "handle": info.handle,
                "backend": backend_builtin,
                "tree": tree,
            },
            metadata=meta,
        )

    def _resolve_cloud_node(self, handle: str, url: str) -> extract_result:
        client = self._get_client()
        if not client.sid or not client.master_key:
            raise mega_link_error("cloud drive node requires MEGA login")
        nodes = {n.handle: n for n in client.list_cloud_drive()}
        node = nodes.get(handle)
        if node is None or not node.is_file or node.key_a32 is None:
            raise mega_link_error(f"cloud file not found: {handle}")
        meta = item_metadata(
            site=self.site,
            item_id=handle,
            canonical_url=url,
            title=node.name,
            extra={"kind": "file", "backend": backend_builtin, "cloud": True},
        )
        f = extracted_file(
            url=url,
            name=_safe_name(node.name),
            relative_dir="",
            size=node.size,
            meta={
                "handle": handle,
                "kind": "file",
                "key_a32": list(node.key_a32),
                "public": False,
            },
            metadata=meta,
        )
        return extract_result(
            site=self.site,
            title=node.name,
            files=[f],
            kind="file",
            meta={"handle": handle, "backend": backend_builtin, "cloud": True},
            metadata=meta,
        )

    def probe(self, url: str) -> dict[str, Any]:
        result = self.resolve(url)
        tree = result.meta.get("tree") or []
        return {
            "site": self.site,
            "kind": result.kind,
            "title": result.title,
            "url": url,
            "backend": self.backend,
            "tree": tree,
            "files": [
                {
                    "index": i,
                    "name": f.name,
                    "size": f.size,
                    "path": f.relative_dir,
                    "handle": (f.meta or {}).get("handle"),
                }
                for i, f in enumerate(result.files)
            ],
            "parts": [
                {
                    "page": i + 1,
                    "title": f.name,
                    "size": f.size,
                    "handle": (f.meta or {}).get("handle"),
                    "path": f.relative_dir,
                }
                for i, f in enumerate(result.files)
            ],
            "message": None,
            "metadata": result.metadata.to_dict(omit_empty=True) if result.metadata else None,
        }

    def download(
        self,
        result: extract_result,
        output_dir: Path,
        *,
        emit: emitter | None = None,
        cancel: Event | None = None,
        workers: int = 3,
        retries: int = 5,
        job_id: str | None = None,
        file_dest_overrides: dict[int, Path] | None = None,
        skip_indexes: set[int] | None = None,
    ) -> tuple[int, int]:
        if self.backend == backend_external or (
            result.meta.get("backend") == backend_external
        ):
            return self._download_external(
                result,
                output_dir,
                emit=emit,
                cancel=cancel,
                workers=workers,
                retries=retries,
                job_id=job_id,
                file_dest_overrides=file_dest_overrides,
                skip_indexes=skip_indexes,
            )

        job = job_id or result.site
        net = active_network()
        configure_global_limiter(net.speed_limit_bps)
        speed = effective_job_limit_bps()
        client = self._get_client()
        workers_n = self.workers_override or max(workers, 4)
        ok = 0
        failed = 0

        for idx, f in enumerate(result.files):
            if skip_indexes and idx in skip_indexes:
                dest = (
                    output_dir / f.relative_dir / f.name
                    if f.relative_dir
                    else output_dir / f.name
                )
                if file_dest_overrides and idx in file_dest_overrides:
                    dest = file_dest_overrides[idx]
                meta_payload = (
                    (f.metadata or result.metadata).to_dict(omit_empty=True)
                    if (f.metadata or result.metadata)
                    else None
                )
                if emit:
                    emit.file(
                        job=job,
                        id=f"{job}:{idx}",
                        name=f.name,
                        total=f.size,
                        path=str(dest),
                        index=idx,
                        metadata=meta_payload,
                        already_downloaded=True,
                    )
                    emit.done(
                        job=job,
                        file=f"{job}:{idx}",
                        path=str(dest),
                        skipped=True,
                        already_downloaded=True,
                        metadata=meta_payload,
                    )
                ok += 1
                continue

            dest_dir = output_dir / f.relative_dir if f.relative_dir else output_dir
            dest = dest_dir / f.name
            if file_dest_overrides and idx in file_dest_overrides:
                dest = file_dest_overrides[idx]
            file_id = f"{job}:{idx}"
            meta_payload = (
                (f.metadata or result.metadata).to_dict(omit_empty=True)
                if (f.metadata or result.metadata)
                else None
            )
            if emit:
                emit.file(
                    job=job,
                    id=file_id,
                    name=f.name,
                    total=f.size,
                    path=str(dest),
                    index=idx,
                    metadata=meta_payload,
                )

            try:
                info = self._file_info_for(client, f, result)
                download_mega_file(
                    client,
                    info,
                    dest,
                    public=bool((f.meta or {}).get("public", True)),
                    workers=workers_n,
                    retries=retries,
                    cancel=cancel,
                    emit=emit,
                    job_id=job,
                    file_id=file_id,
                    verify_mac=self.verify_mac,
                    speed_limit_bps=speed,
                )
                ok += 1
            except InterruptedError:
                raise
            except mega_quota_error:
                raise
            except Exception as exc:  # noqa: BLE001
                failed += 1
                if emit:
                    emit.error(
                        job=job,
                        file=file_id,
                        message=str(exc),
                        code=getattr(exc, "code", None) or "download_failed",
                    )

        return ok, failed

    def _file_info_for(self, client: mega_client, f: extracted_file, result: extract_result):
        meta = f.meta or {}
        handle = str(meta.get("handle") or "")
        if meta.get("key"):
            return client.get_public_file(handle, str(meta["key"]))
        key_a32 = meta.get("key_a32")
        if key_a32:
            # folder child: need CDN URL via public folder parent + node g request
            # For public folder files MEGA uses 'n' with folder context — use 'g'+'n'
            # after establishing folder. Simpler: use get with public folder handle
            # via temporary approach — request g with n=handle after f listing.
            from udl.extractors.mega.api import public_file_info
            from udl.extractors.mega.crypto import derive_file_key_parts

            key_t = tuple(int(x) for x in key_a32)
            k, iv, mac = derive_file_key_parts(key_t)
            # public folder children still use node handle with 'n'
            data = client.request({"a": "g", "g": 1, "n": handle, "ssl": 1})
            if not isinstance(data, dict) or "g" not in data:
                # fallback: try as public file id (rare)
                folder_handle = meta.get("folder_handle")
                if folder_handle:
                    data = client.request(
                        {"a": "g", "g": 1, "n": handle, "p": folder_handle}
                    )
            if not isinstance(data, dict) or "g" not in data:
                raise mega_download_error(f"cannot get download URL for {handle}")
            return public_file_info(
                handle=handle,
                name=f.name,
                size=int(data.get("s") or f.size or 0),
                key_a32=k,
                iv_a32=iv,
                meta_mac=mac,
                download_url=str(data["g"]),
                attrs={},
            )
        raise mega_download_error(f"missing key for {f.name}")

    def _resolve_external(self, url: str) -> extract_result:
        from udl.extractors.external import mega as external_mega

        ext = external_mega.mega_extractor(mega_tool=self.mega_tool_override)
        result = ext.resolve(url)
        if result.meta is not None:
            result.meta["backend"] = backend_external
        return result

    def _download_external(self, result, output_dir, **kwargs):
        from udl.extractors.external import mega as external_mega

        ext = external_mega.mega_extractor(mega_tool=self.mega_tool_override)
        return ext.download(result, output_dir, **kwargs)


def _resolve_backend(value: str | None) -> str:
    raw = (value or os.environ.get(mega_backend_env) or backend_builtin).strip().lower()
    if raw in {"external", "megatools", "megacmd", "cli"}:
        return backend_external
    return backend_builtin


def _safe_name(name: str) -> str:
    cleaned = name.replace("\0", "").replace("/", "_").replace("\\", "_").strip()
    return cleaned or "mega-file"


__all__ = ["mega_extractor", "matches", "parse_link"]
