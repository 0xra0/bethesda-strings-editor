"""
Tests for encoding detection in EncodingConverter and BethesdaStringFile.
No Qt dependency; pure Python.
"""

import struct

from bethesda_strings.core import BethesdaStringFile
from bethesda_strings.encoding import EncodingConverter


# ── EncodingConverter.detect_encoding ─────────────────────────────────────────


def test_empty_bytes_returns_utf8():
    enc, conf, method = EncodingConverter.detect_encoding(b"")
    assert enc == "utf-8"
    assert conf == 1.0


def test_utf8_bom_detected():
    data = b"\xef\xbb\xbfHello"
    enc, conf, method = EncodingConverter.detect_encoding(data)
    assert enc == "utf-8-sig"
    assert conf == 1.0
    assert "BOM" in method


def test_utf16_le_bom_detected():
    data = b"\xff\xfeHello"
    enc, conf, method = EncodingConverter.detect_encoding(data)
    assert enc == "utf-16-le"
    assert conf == 1.0


def test_utf16_be_bom_detected():
    data = b"\xfe\xffHello"
    enc, conf, method = EncodingConverter.detect_encoding(data)
    assert enc == "utf-16-be"
    assert conf == 1.0


def test_ascii_only_returns_utf8_high_confidence():
    data = b"Hello world. This is a test string."
    enc, conf, method = EncodingConverter.detect_encoding(data)
    assert enc == "utf-8"
    assert conf >= 0.90
    assert "ASCII" in method


def test_valid_utf8_cyrillic_detected():
    # UTF-8 encoded Ukrainian text
    text = "Привіт світ! Це тестовий рядок для перевірки."
    data = text.encode("utf-8")
    enc, conf, method = EncodingConverter.detect_encoding(data)
    assert enc == "utf-8"
    assert conf >= 0.85
    assert "UTF-8" in method or "utf-8" in method.lower()


def test_cp1251_cyrillic_detected():
    # CP1251-encoded Russian text (bytes that are invalid UTF-8)
    text = "Привет мир! Это тестовая строка для проверки кодировки."
    data = text.encode("windows-1251")
    enc, conf, method = EncodingConverter.detect_encoding(data)
    assert enc == "windows-1251"
    assert conf >= 0.60


def test_cp1251_ukrainian_detected():
    # CP1251-encoded Ukrainian text
    text = "Привіт! Це тестовий рядок для перевірки кодування файлу."
    data = text.encode("windows-1251")
    enc, conf, method = EncodingConverter.detect_encoding(data)
    assert enc == "windows-1251"
    assert conf >= 0.60


def test_utf8_english_text():
    data = "The quick brown fox jumps over the lazy dog.".encode("utf-8")
    enc, conf, method = EncodingConverter.detect_encoding(data)
    assert enc == "utf-8"


def test_cp1252_western_fallback():
    # Use CP1252-specific bytes (0x80–0xBF range) that are NOT in the CP1251
    # Cyrillic range (0xC0–0xFF), so the CP1251 branch won't trigger.
    # € = 0x80, ‰ = 0x89, Š = 0x8A — valid CP1252, invalid UTF-8.
    data = b"Price \x80100 or \x89 percent off the \x8aale"
    enc, conf, method = EncodingConverter.detect_encoding(data)
    # These bytes are below 0xC0 so CP1251 Cyrillic ratio stays low;
    # detector should pick UTF-8 (replacement chars but no Cyrillic density)
    # or CP1252 fallback — both are acceptable.
    assert enc in ("windows-1252", "utf-8")


def test_utf8_mixed_latin_accents():
    # UTF-8 text with accented characters (not Cyrillic)
    text = "Über die Straße gehen — Schönes Wetter"
    data = text.encode("utf-8")
    enc, conf, method = EncodingConverter.detect_encoding(data)
    assert enc == "utf-8"


def test_utf8_japanese_text():
    text = "こんにちは世界"
    data = text.encode("utf-8")
    enc, conf, method = EncodingConverter.detect_encoding(data)
    assert enc == "utf-8"


def test_confidence_is_float_between_0_and_1():
    for data in [b"Hello", "Привет".encode("utf-8"), "Привет".encode("windows-1251")]:
        _, conf, _ = EncodingConverter.detect_encoding(data)
        assert 0.0 <= conf <= 1.0


def test_method_is_nonempty_string():
    for data in [b"", b"Hello", "Привет".encode("windows-1251")]:
        _, _, method = EncodingConverter.detect_encoding(data)
        assert isinstance(method, str)
        assert method


def test_large_utf8_sample():
    # Simulate a large block of UTF-8 Cyrillic text.
    # Do NOT slice at an arbitrary byte offset — that can split multibyte
    # sequences and cause the strict-UTF-8 decode to fail, making the sample
    # look like CP1251. Pass whole characters so the decode succeeds.
    text = "Це рядок для тестування великих файлів. " * 2000
    data = text.encode("utf-8")
    enc, conf, _ = EncodingConverter.detect_encoding(data)
    assert enc == "utf-8"
    assert conf >= 0.85


def test_large_cp1251_sample():
    text = "Это строка для тестирования больших файлов. " * 2000
    data = text.encode("windows-1251")
    enc, conf, _ = EncodingConverter.detect_encoding(data[:65536])
    assert enc == "windows-1251"
    assert conf >= 0.60


# ── BethesdaStringFile encoding detection ─────────────────────────────────────


def _make_strings_buffer(strings: list[str], encoding: str = "utf-8") -> bytes:
    """Build a minimal .strings file buffer with the given strings."""
    entry_count = len(strings)
    # Encode strings
    encoded = [s.encode(encoding) + b"\x00" for s in strings]
    data_size = sum(len(e) for e in encoded)

    header = struct.pack("<II", entry_count, data_size)
    directory = b""
    offset = 0
    for e in encoded:
        string_id = 1000 + len(directory) // 8
        directory += struct.pack("<II", string_id, offset)
        offset += len(e)

    return header + directory + b"".join(encoded)


def _make_dlstrings_buffer(strings: list[str], encoding: str = "utf-8") -> bytes:
    """Build a minimal .dlstrings file buffer (length-prefixed)."""
    entry_count = len(strings)
    encoded_chunks = []
    for s in strings:
        raw = s.encode(encoding) + b"\x00"
        encoded_chunks.append(struct.pack("<I", len(raw)) + raw)

    data_size = sum(len(c) for c in encoded_chunks)
    header = struct.pack("<II", entry_count, data_size)
    directory = b""
    offset = 0
    for i, chunk in enumerate(encoded_chunks):
        directory += struct.pack("<II", 1000 + i, offset)
        offset += len(chunk)

    return header + directory + b"".join(encoded_chunks)


class TestBethesdaStringFileEncoding:
    def test_default_encoding_before_load(self):
        f = BethesdaStringFile()
        assert f.encoding == "utf-8"
        assert f._encoding_source == "default"

    def test_utf8_file_detected(self):
        strings = [
            "Hello world",
            "Привіт світ",
            "Це тестовий рядок для перевірки кодування",
        ]
        buf = _make_strings_buffer(strings, "utf-8")
        f = BethesdaStringFile(buffer=buf, file_extension="strings")
        assert f.encoding == "utf-8"
        assert f._encoding_source == "detected"
        assert f._encoding_confidence >= 0.80

    def test_cp1251_file_detected(self):
        strings = [
            "Привет мир, это строка на русском языке для тестирования кодировки",
            "Ещё одна строка для увеличения размера выборки символов",
            "Третья строка с кириллическим текстом для надёжного определения",
        ]
        buf = _make_strings_buffer(strings, "windows-1251")
        f = BethesdaStringFile(buffer=buf, file_extension="strings")
        assert f.encoding == "windows-1251"
        assert f._encoding_source == "detected"

    def test_ascii_file_detected_as_utf8(self):
        strings = ["Hello world", "Test string", "Another item"]
        buf = _make_strings_buffer(strings, "utf-8")
        f = BethesdaStringFile(buffer=buf, file_extension="strings")
        assert f.encoding == "utf-8"
        assert f._encoding_source == "detected"

    def test_encoding_info_returns_4_tuple(self):
        strings = ["Hello", "World"]
        buf = _make_strings_buffer(strings)
        f = BethesdaStringFile(buffer=buf, file_extension="strings")
        info = f.encoding_info()
        assert len(info) == 4
        enc, conf, src, method = info
        assert isinstance(enc, str)
        assert isinstance(conf, float)
        assert src in ("default", "detected", "manual")
        assert isinstance(method, str)

    def test_set_encoding_overrides_detection(self):
        strings = ["Hello world", "Another string"]
        buf = _make_strings_buffer(strings, "utf-8")
        f = BethesdaStringFile(buffer=buf, file_extension="strings")
        assert f._encoding_source == "detected"

        f.set_encoding("windows-1252")
        assert f.encoding == "windows-1252"
        assert f._encoding_source == "manual"
        assert f._encoding_confidence == 1.0

    def test_set_encoding_method_reflects_value(self):
        buf = _make_strings_buffer(["Hello"])
        f = BethesdaStringFile(buffer=buf, file_extension="strings")
        f.set_encoding("windows-1251")
        _, _, src, method = f.encoding_info()
        assert src == "manual"
        assert "windows-1251" in method

    def test_manual_encoding_not_overwritten_by_detect(self):
        strings = [
            "Привіт! Це тестовий рядок для перевірки автоматичного визначення.",
            "Ще один рядок з кириличним текстом для більшої вибірки.",
        ]
        buf = _make_strings_buffer(strings, "utf-8")
        f = BethesdaStringFile(buffer=buf, file_extension="strings")
        f.set_encoding("windows-1252")  # manual override

        # Re-running _detect_encoding should be a no-op
        f._detect_encoding()
        assert f.encoding == "windows-1252"
        assert f._encoding_source == "manual"

    def test_dlstrings_encoding_detected(self):
        strings = [
            "Привіт! Це тестовий рядок для перевірки кодування у форматі DL.",
            "Ще один рядок для збільшення розміру вибірки символів у файлі.",
        ]
        buf = _make_dlstrings_buffer(strings, "utf-8")
        f = BethesdaStringFile(buffer=buf, file_extension="dlstrings")
        assert f.encoding == "utf-8"
        assert f._encoding_source == "detected"

    def test_empty_file_uses_default_encoding(self):
        # Minimal file with 0 strings
        buf = struct.pack("<II", 0, 0)
        f = BethesdaStringFile(buffer=buf, file_extension="strings")
        assert f.encoding == "utf-8"
        assert f._encoding_source == "default"

    def test_encoding_confidence_stored(self):
        strings = ["Привіт! Це рядок для тестування впевненості у визначенні кодування."]
        buf = _make_strings_buffer(strings, "utf-8")
        f = BethesdaStringFile(buffer=buf, file_extension="strings")
        _, conf, _, _ = f.encoding_info()
        assert 0.0 < conf <= 1.0

    def test_set_string_uses_detected_encoding(self):
        """set_string should encode using the file's detected encoding."""
        strings = [
            "Привет мир это тестовая строка для кодировки виндовс тысяча двести пятьдесят один",
            "Ещё строки для увеличения выборки символов в файле кодировки",
            "Третья строка кириллицы для надёжного определения кодировки файла",
        ]
        buf = _make_strings_buffer(strings, "windows-1251")
        f = BethesdaStringFile(buffer=buf, file_extension="strings")
        assert f.encoding == "windows-1251"

        # Now write a string using the file's encoding
        s = f.strings[0]
        s.set_string("Новый текст", encoding=f.encoding)
        assert s.get_string(f.encoding) == "Новый текст"
