"""
NexusMods Single Sign-On (SSO) client.

Why this exists
---------------
Nexus Mods' API Acceptable Use Policy (https://help.nexusmods.com/article/114)
prohibits public-facing applications from asking users to paste their
*personal* API key.  Public apps must instead obtain a per-user key through the
official SSO flow, which requires the application to be **registered** with
Nexus Mods (they assign an application *slug*).

SSO protocol (v2), as used by Vortex and the official integrations:

  1. Open a WebSocket to ``wss://sso.nexusmods.com``.
  2. Send ``{"id": <uuid4>, "token": <saved_connection_token|null>,
              "protocol": 2}``.
  3. Receive ``{"success": true, "data": {"connection_token": "..."}}`` —
     persist that ``connection_token`` so future sign-ins can be silent.
  4. Open the user's browser at
     ``https://www.nexusmods.com/sso?id=<uuid4>&application=<slug>``.
  5. After the user clicks *Authorise*, the server pushes
     ``{"success": true, "data": {"api_key": "..."}}``.

This module implements a *minimal* RFC-6455 WebSocket client over the stdlib
``socket``/``ssl`` only — no third-party dependency — because SSO must work in
the frozen build without an extra install.  The framing helpers are pure
functions so they can be unit-tested without a network.

No Qt here: the GUI layer (``settings_dialog``) drives this from a worker
thread and supplies the browser-open callback.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import ssl
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# Registered application slug assigned by Nexus Mods at registration time.
# Until the app is registered (support ticket: name + description + logo, see
# https://help.nexusmods.com/article/114-api-acceptable-use-policy) the SSO
# server will not return an api_key for this id and the flow will time out.
# Override per-call via request_api_key(slug=…) if the assigned slug differs.
APPLICATION_SLUG = "bethesda-strings-editor"

_SSO_WS_HOST = "sso.nexusmods.com"
_SSO_WS_PORT = 443
_SSO_WEB_URL = "https://www.nexusmods.com/sso"

# WebSocket opcodes (RFC 6455 §5.2)
_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


# ── Frame codec (pure, unit-tested) ────────────────────────────────────────────

def encode_frame(payload: bytes, opcode: int = _OP_TEXT,
                 mask: Optional[bytes] = None) -> bytes:
    """Encode a single (un-fragmented) client WebSocket frame.

    Clients MUST mask every frame (RFC 6455 §5.3); *mask* is generated when not
    supplied.  Passing a fixed *mask* makes the output deterministic for tests.
    """
    if mask is None:
        mask = os.urandom(4)
    if len(mask) != 4:
        raise ValueError("mask must be exactly 4 bytes")

    header = bytearray()
    header.append(0x80 | (opcode & 0x0F))  # FIN=1 + opcode

    n = len(payload)
    if n < 126:
        header.append(0x80 | n)            # MASK=1 + length
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)

    header += mask
    masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
    return bytes(header) + masked


def decode_frame(data: bytes) -> Tuple[Optional[int], bytes, int]:
    """Decode the first frame from *data*.

    Returns ``(opcode, payload, consumed)``.  If *data* does not yet contain a
    full frame, returns ``(None, b"", 0)`` so the caller can read more bytes.
    Server→client frames are never masked, but a mask bit is honoured anyway
    for robustness.
    """
    if len(data) < 2:
        return None, b"", 0
    b0, b1 = data[0], data[1]
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    idx = 2
    if length == 126:
        if len(data) < idx + 2:
            return None, b"", 0
        length = struct.unpack(">H", data[idx:idx + 2])[0]
        idx += 2
    elif length == 127:
        if len(data) < idx + 8:
            return None, b"", 0
        length = struct.unpack(">Q", data[idx:idx + 8])[0]
        idx += 8

    mask = b""
    if masked:
        if len(data) < idx + 4:
            return None, b"", 0
        mask = data[idx:idx + 4]
        idx += 4

    if len(data) < idx + length:
        return None, b"", 0
    payload = data[idx:idx + length]
    if masked:
        payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
    return opcode, payload, idx + length


def build_sso_url(sso_id: str, slug: str = APPLICATION_SLUG) -> str:
    """Browser URL the user opens to authorise the SSO request."""
    return f"{_SSO_WEB_URL}?id={sso_id}&application={slug}"


# ── Minimal WebSocket client ────────────────────────────────────────────────────

class _WebSocket:
    """Tiny RFC-6455 text-only client (TLS, client-masked, stdlib only)."""

    def __init__(self, host: str, port: int, timeout: float = 30.0):
        raw = socket.create_connection((host, port), timeout=timeout)
        ctx = ssl.create_default_context()
        self._sock = ctx.wrap_socket(raw, server_hostname=host)
        self._buf = bytearray()
        self._handshake(host)

    def _handshake(self, host: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(req.encode("ascii"))
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("SSO server closed during handshake")
            resp += chunk
            if len(resp) > 65536:
                raise ConnectionError("SSO handshake response too large")
        status_line = resp.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line:
            raise ConnectionError(f"SSO handshake failed: {status_line!r}")
        # Stash any bytes that arrived after the header for the frame reader.
        self._buf += resp.split(b"\r\n\r\n", 1)[1]

    def send_text(self, text: str) -> None:
        self._sock.sendall(encode_frame(text.encode("utf-8"), _OP_TEXT))

    def _send_control(self, opcode: int, payload: bytes = b"") -> None:
        self._sock.sendall(encode_frame(payload, opcode))

    def recv_text(self, deadline: float) -> Optional[str]:
        """Return the next text-frame payload, or None on timeout/close.

        Control frames (ping/close) are handled transparently.  *deadline* is
        an absolute ``time.monotonic()`` value.
        """
        while True:
            opcode, payload, consumed = decode_frame(bytes(self._buf))
            if opcode is not None:
                del self._buf[:consumed]
                if opcode == _OP_TEXT:
                    return payload.decode("utf-8", "replace")
                if opcode == _OP_PING:
                    self._send_control(_OP_PONG, payload)
                    continue
                if opcode == _OP_CLOSE:
                    return None
                continue  # ignore binary/pong/continuation

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                self._sock.settimeout(remaining)
                chunk = self._sock.recv(4096)
            except (socket.timeout, ssl.SSLWantReadError):
                return None
            if not chunk:
                return None
            self._buf += chunk

    def close(self) -> None:
        try:
            self._send_control(_OP_CLOSE)
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass


@dataclass
class SSOResult:
    api_key: str
    connection_token: str


def request_api_key(
    on_url: Callable[[str], None],
    slug: str = APPLICATION_SLUG,
    connection_token: Optional[str] = None,
    timeout: float = 180.0,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> SSOResult:
    """Run the SSO flow and return the user's API key.

    *on_url* is called with the authorisation URL once the WebSocket is live —
    the GUI opens it in the user's browser.  *connection_token* (from a prior
    sign-in) lets the server skip the browser step when the grant is still
    valid.  *should_cancel*, if given, is polled to allow early abort.

    Raises ``TimeoutError`` if the user never authorises within *timeout*, or
    ``ConnectionError`` on transport failure.
    """
    sso_id = str(uuid.uuid4())
    ws = _WebSocket(_SSO_WS_HOST, _SSO_WS_PORT, timeout=min(timeout, 30.0))
    token = connection_token or None
    deadline = time.monotonic() + timeout
    opened = False
    try:
        ws.send_text(json.dumps({"id": sso_id, "token": token, "protocol": 2}))
        while time.monotonic() < deadline:
            if should_cancel and should_cancel():
                raise ConnectionError("SSO cancelled")
            # Wake up periodically so cancellation/timeout stay responsive.
            slice_deadline = min(deadline, time.monotonic() + 2.0)
            msg = ws.recv_text(slice_deadline)
            if msg is None:
                continue
            try:
                parsed = json.loads(msg)
            except (ValueError, TypeError):
                continue
            if parsed.get("success") is False:
                raise ConnectionError(
                    f"SSO error: {parsed.get('error') or 'unknown'}"
                )
            data = parsed.get("data") or {}
            if data.get("connection_token"):
                token = data["connection_token"]
            if not opened:
                on_url(build_sso_url(sso_id, slug))
                opened = True
            if data.get("api_key"):
                return SSOResult(api_key=data["api_key"],
                                 connection_token=token or "")
        raise TimeoutError(
            "Timed out waiting for Nexus Mods authorisation. If the browser "
            "page showed an error, this app may not be registered for SSO yet."
        )
    finally:
        ws.close()
