from __future__ import annotations

import zlib


def _parse_png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    # Minimal PNG chunk parser for tests.
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError("Not a PNG")
    pos = 8
    out: list[tuple[bytes, bytes]] = []
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        ctype = data[pos + 4 : pos + 8]
        start = pos + 8
        end = start + length
        chunk = data[start:end]
        out.append((ctype, chunk))
        pos = end + 4  # skip CRC
        if ctype == b"IEND":
            break
    return out


def test_encode_png_rgba_minimal() -> None:
    from devices.adapters.local_desktop import _encode_png_rgba

    # 2x2 RGBA: red, green / blue, white
    rgba = bytes(
        [
            255,
            0,
            0,
            255,
            0,
            255,
            0,
            255,
            0,
            0,
            255,
            255,
            255,
            255,
            255,
            255,
        ]
    )
    png = _encode_png_rgba(2, 2, rgba)
    chunks = _parse_png_chunks(png)
    types = [t for (t, _) in chunks]
    assert b"IHDR" in types
    assert b"IDAT" in types
    assert types[-1] == b"IEND"

    # Decompress IDAT and check scanline sizing: each row has 1 filter byte + 2*4 pixels.
    idat = b"".join(chunk for (t, chunk) in chunks if t == b"IDAT")
    raw = zlib.decompress(idat)
    assert len(raw) == (1 + 2 * 4) * 2
    assert raw[0] == 0
    assert raw[1:9] == rgba[0:8]


def test_windows_screenshot_support_does_not_require_powershell(monkeypatch) -> None:
    import devices.adapters.local_desktop as mod

    monkeypatch.setattr(mod.platform, "system", lambda: "Windows")
    # Even if which() would return None, Windows support should still be True now.
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    ok, reason = mod.LocalDesktopNode._detect_screenshot_support()
    assert ok is True
    assert reason == ""

