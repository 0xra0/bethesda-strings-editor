"""NexusMods v3 multipart upload client.

Six-step flow:
  1. POST /uploads/multipart          → upload_id + presigned part URLs
  2. PUT  <presigned URLs>            → upload chunks, collect ETags
  3. POST <complete_presigned_url>    → S3 XML manifest to assemble parts
  4. POST /uploads/{id}/finalise      → tell NexusMods the upload is done
  5. GET  /uploads/{id}  (poll)       → wait for state == "available"
  6. POST /mod-file-update-groups/{group_id}/versions → attach metadata, get file UID
"""

import logging
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.nexusmods.com/v3"
_USER_AGENT = "bethesda-strings-editor"
_MAX_CONCURRENT_PARTS = 6
_POLL_INITIAL_DELAY = 2.0
_POLL_MAX_DELAY = 30.0
_POLL_MAX_ATTEMPTS = 60

ProgressCallback = Callable[[int, int, str], None]  # (bytes_done, bytes_total, message)


class NexusModsError(Exception):
    """Raised for API errors during the upload flow."""


class NexusModsUploader:
    """Upload a local file to a NexusMods mod page via the v3 API."""

    def __init__(self, api_key: str, base_url: str = _BASE_URL) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._base = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({
            "apikey": api_key,
            "User-Agent": _USER_AGENT,
        })

    def upload_file(
        self,
        file_path: Path,
        group_id: str,
        *,
        name: str,
        version: str,
        description: str = "",
        file_category: str = "main",
        archive_existing_file: bool = True,
        primary_mod_manager_download: bool = False,
        allow_mod_manager_download: bool = True,
        show_requirements_pop_up: bool = False,
        progress: Optional[ProgressCallback] = None,
    ) -> str:
        """Upload *file_path* and return the new NexusMods file UID string."""
        file_path = Path(file_path)
        total = file_path.stat().st_size
        filename = file_path.name

        def _p(done: int, total_b: int, msg: str) -> None:
            if progress:
                progress(done, total_b, msg)

        _p(0, total, "Initiating multipart upload…")
        upload_id, part_urls, part_size, complete_url = self._initiate(filename, total)

        _p(0, total, f"Uploading {len(part_urls)} chunk(s)…")
        etags = self._upload_parts(file_path, part_urls, part_size, total, progress)

        _p(total, total, "Assembling parts on server…")
        self._complete_multipart(complete_url, etags)
        self._finalise(upload_id)

        _p(total, total, "Waiting for NexusMods to process the upload…")
        self._poll_until_available(upload_id, total, progress)

        _p(total, total, "Creating file version entry…")
        file_uid = self._create_version(
            group_id,
            upload_id=upload_id,
            name=name,
            version=version,
            description=description,
            file_category=file_category,
            archive_existing_file=archive_existing_file,
            primary_mod_manager_download=primary_mod_manager_download,
            allow_mod_manager_download=allow_mod_manager_download,
            show_requirements_pop_up=show_requirements_pop_up,
        )
        _p(total, total, f"Upload complete — file UID: {file_uid}")
        return file_uid

    # ── private helpers ────────────────────────────────────────────────────

    def _api(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self._base}{path}"
        resp = self._session.request(method, url, timeout=30, **kwargs)
        if not resp.ok:
            raise NexusModsError(
                f"{method} {path} returned {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json()

    def _initiate(self, filename: str, size: int):
        data = self._api(
            "POST", "/uploads/multipart",
            json={"filename": filename, "size_bytes": str(size)},
        )["data"]
        return (
            data["id"],
            data["part_presigned_urls"],
            data["part_size_bytes"],
            data["complete_presigned_url"],
        )

    def _upload_parts(
        self,
        file_path: Path,
        presigned_urls: list,
        part_size: int,
        total: int,
        progress: Optional[ProgressCallback],
    ) -> list:
        etags: list = []
        bytes_done = 0
        lock = threading.Lock()

        def _upload_one(idx_url):
            idx, url = idx_url
            offset = idx * part_size
            with open(file_path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read(part_size)
            resp = requests.put(
                url,
                data=chunk,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(chunk)),
                },
                timeout=300,
            )
            if not resp.ok:
                raise NexusModsError(
                    f"Part {idx + 1} upload failed with {resp.status_code}"
                )
            etag = resp.headers.get("ETag", "").strip('"')
            return idx + 1, etag, len(chunk)

        n = len(presigned_urls)
        with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_PARTS) as ex:
            futures = {
                ex.submit(_upload_one, (i, url)): i
                for i, url in enumerate(presigned_urls)
            }
            for fut in as_completed(futures):
                part_num, etag, chunk_len = fut.result()
                etags.append((part_num, etag))
                with lock:
                    bytes_done += chunk_len
                if progress:
                    progress(bytes_done, total, f"Uploaded part {part_num}/{n}")

        etags.sort(key=lambda x: x[0])
        return etags

    def _complete_multipart(self, complete_url: str, etags: list) -> None:
        root = ET.Element("CompleteMultipartUpload")
        for part_num, etag in etags:
            part = ET.SubElement(root, "Part")
            ET.SubElement(part, "PartNumber").text = str(part_num)
            ET.SubElement(part, "ETag").text = etag
        body = ET.tostring(root, encoding="unicode")
        resp = requests.post(
            complete_url,
            data=body,
            headers={"Content-Type": "application/xml"},
            timeout=60,
        )
        if not resp.ok:
            raise NexusModsError(
                f"Multipart completion failed: {resp.status_code}: {resp.text[:200]}"
            )

    def _finalise(self, upload_id: str) -> None:
        self._api("POST", f"/uploads/{upload_id}/finalise")

    def _poll_until_available(
        self,
        upload_id: str,
        total: int,
        progress: Optional[ProgressCallback],
    ) -> None:
        delay = _POLL_INITIAL_DELAY
        for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
            time.sleep(delay)
            data = self._api("GET", f"/uploads/{upload_id}")["data"]
            state = data.get("state", "unknown")
            logger.debug("Upload %s state=%s (attempt %d)", upload_id, state, attempt)
            if progress:
                progress(total, total, f"Processing… (state: {state})")
            if state == "available":
                return
            if state in ("error", "failed"):
                raise NexusModsError(f"NexusMods processing failed (state={state!r})")
            delay = min(delay * 1.5, _POLL_MAX_DELAY)
        raise NexusModsError("Timed out waiting for upload to become available")

    def _create_version(
        self,
        group_id: str,
        *,
        upload_id: str,
        name: str,
        version: str,
        description: str,
        file_category: str,
        archive_existing_file: bool,
        primary_mod_manager_download: bool,
        allow_mod_manager_download: bool,
        show_requirements_pop_up: bool,
    ) -> str:
        data = self._api(
            "POST", f"/mod-file-update-groups/{group_id}/versions",
            json={
                "upload_id": upload_id,
                "name": name,
                "description": description,
                "version": version,
                "file_category": file_category,
                "archive_existing_file": archive_existing_file,
                "primary_mod_manager_download": primary_mod_manager_download,
                "allow_mod_manager_download": allow_mod_manager_download,
                "show_requirements_pop_up": show_requirements_pop_up,
            },
        )["data"]
        return str(data["id"])
