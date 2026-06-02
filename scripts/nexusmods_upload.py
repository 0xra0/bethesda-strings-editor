#!/usr/bin/env python3
"""Upload release artifacts to NexusMods using the project's own uploader.

Environment variables:
  NEXUSMODS_API_KEY               — NexusMods personal API key (required)
  NEXUSMODS_FILE_GROUP_ID_LINUX   — file group ID for the Linux zip
  NEXUSMODS_FILE_GROUP_ID_WINDOWS — file group ID for the Windows zip
  RELEASE_VERSION                 — git tag, e.g. "v0.2.1" (leading v stripped)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.nexusmods_uploader import NexusModsError, NexusModsUploader


def _progress(done: int, total: int, msg: str) -> None:
    pct = done / total * 100 if total else 0
    print(f"  [{pct:5.1f}%] {msg}", flush=True)


def main() -> None:
    api_key = os.environ.get("NEXUSMODS_API_KEY", "").strip()
    if not api_key:
        print(
            "WARNING: NEXUSMODS_API_KEY secret is not configured — skipping NexusMods upload.\n"
            "To enable uploads, add NEXUSMODS_API_KEY as a repository secret:\n"
            "  GitHub repo → Settings → Secrets and variables → Actions → New repository secret",
            file=sys.stderr,
        )
        sys.exit(0)

    version = os.environ.get("RELEASE_VERSION", "").lstrip("v").strip()
    if not version:
        print("ERROR: RELEASE_VERSION is not set.", file=sys.stderr)
        sys.exit(1)

    uploader = NexusModsUploader(api_key)

    uploads = [
        (
            "bethesda-strings-editor-linux-x64.zip",
            os.environ.get("NEXUSMODS_FILE_GROUP_ID_LINUX", "").strip(),
            f"Bethesda Strings Editor {version} (Linux x64)",
        ),
        (
            "bethesda-strings-editor-windows-x64.zip",
            os.environ.get("NEXUSMODS_FILE_GROUP_ID_WINDOWS", "").strip(),
            f"Bethesda Strings Editor {version} (Windows x64)",
        ),
    ]

    failed = False
    for filename, group_id, display_name in uploads:
        path = Path(filename)
        if not path.exists():
            print(f"WARNING: {filename} not found — skipping.", file=sys.stderr)
            continue
        if not group_id:
            print(f"WARNING: no file group ID configured for {filename} — skipping.", file=sys.stderr)
            continue

        print(f"\nUploading {filename} → '{display_name}'")
        try:
            uid = uploader.upload_file(
                path,
                group_id,
                name=display_name,
                version=version,
                file_category="main",
                archive_existing_file=True,
                progress=_progress,
            )
            print(f"  ✓ Done — NexusMods file UID: {uid}")
        except NexusModsError as exc:
            print(f"  ✗ Upload failed: {exc}", file=sys.stderr)
            failed = True

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
