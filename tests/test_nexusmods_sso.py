"""Tests for the pure (no-network) parts of the NexusMods SSO client.

Covers the RFC-6455 frame codec and the SSO URL builder. The WebSocket
transport and the live handshake are intentionally not exercised here — they
need a network and a registered application slug.
"""

import json

import pytest

from gui.nexusmods_sso import (
    APPLICATION_SLUG,
    build_sso_url,
    decode_frame,
    encode_frame,
    _OP_CLOSE,
    _OP_PING,
    _OP_TEXT,
)


def test_encode_sets_fin_and_mask_bits():
    frame = encode_frame(b"hi", _OP_TEXT, mask=b"\x00\x00\x00\x00")
    # FIN(0x80) + text opcode(0x1) = 0x81; MASK(0x80) + len 2 = 0x82
    assert frame == b"\x81\x82\x00\x00\x00\x00hi"


def test_encode_rejects_bad_mask_length():
    with pytest.raises(ValueError):
        encode_frame(b"x", _OP_TEXT, mask=b"\x01\x02")


def test_round_trip_short_payload():
    payload = json.dumps({"id": "abc", "token": None, "protocol": 2}).encode()
    op, out, consumed = decode_frame(encode_frame(payload, _OP_TEXT,
                                                  mask=b"\x01\x02\x03\x04"))
    assert op == _OP_TEXT
    assert out == payload
    assert consumed == len(encode_frame(payload, _OP_TEXT, mask=b"\x01\x02\x03\x04"))


def test_round_trip_extended_16bit_length():
    payload = b"x" * 200  # forces the 126 + 2-byte length form
    op, out, _ = decode_frame(encode_frame(payload, _OP_TEXT,
                                           mask=b"\xaa\xbb\xcc\xdd"))
    assert op == _OP_TEXT and out == payload


def test_round_trip_extended_64bit_length():
    payload = b"y" * 70000  # forces the 127 + 8-byte length form
    op, out, _ = decode_frame(encode_frame(payload, _OP_TEXT,
                                           mask=b"\x10\x20\x30\x40"))
    assert op == _OP_TEXT and out == payload


def test_decode_partial_frame_needs_more_bytes():
    frame = encode_frame(b"hello world", _OP_TEXT, mask=b"\x01\x02\x03\x04")
    assert decode_frame(frame[:1]) == (None, b"", 0)
    assert decode_frame(frame[:5]) == (None, b"", 0)
    # full frame decodes
    op, out, consumed = decode_frame(frame)
    assert op == _OP_TEXT and out == b"hello world" and consumed == len(frame)


def test_decode_unmasked_server_frame():
    # Servers send unmasked frames: 0x81, len, payload (no mask key).
    raw = bytes([0x81, 0x03]) + b"abc"
    op, out, consumed = decode_frame(raw)
    assert op == _OP_TEXT and out == b"abc" and consumed == 5


def test_decode_reports_consumed_for_trailing_frame():
    a = encode_frame(b"one", _OP_TEXT, mask=b"\x01\x01\x01\x01")
    b = encode_frame(b"two", _OP_TEXT, mask=b"\x02\x02\x02\x02")
    op, out, consumed = decode_frame(a + b)
    assert op == _OP_TEXT and out == b"one" and consumed == len(a)
    # remaining bytes decode to the second frame
    op2, out2, _ = decode_frame((a + b)[consumed:])
    assert op2 == _OP_TEXT and out2 == b"two"


def test_decode_control_opcodes():
    op, _, _ = decode_frame(encode_frame(b"", _OP_PING, mask=b"\x00\x00\x00\x00"))
    assert op == _OP_PING
    op, _, _ = decode_frame(encode_frame(b"", _OP_CLOSE, mask=b"\x00\x00\x00\x00"))
    assert op == _OP_CLOSE


def test_build_sso_url_default_slug():
    url = build_sso_url("11111111-2222-3333-4444-555555555555")
    assert url == (
        "https://www.nexusmods.com/sso?"
        "id=11111111-2222-3333-4444-555555555555&"
        f"application={APPLICATION_SLUG}"
    )


def test_build_sso_url_custom_slug():
    assert build_sso_url("the-id", slug="my-app") == (
        "https://www.nexusmods.com/sso?id=the-id&application=my-app"
    )
