import json
import struct
import zlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.web.pwa import BANNER_HTML, HEAD_HTML, ICON_SIZES, MANIFEST, icon_png, register

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _decode_pixels(png: bytes) -> tuple[int, int, bytes]:
    """Parse our own PNG output: chunk walk, then unfilter (all rows filter 0)."""
    assert png.startswith(PNG_SIGNATURE)
    offset, idat = len(PNG_SIGNATURE), b""
    width = height = 0
    while offset < len(png):
        (length,) = struct.unpack(">I", png[offset : offset + 4])
        tag = png[offset + 4 : offset + 8]
        data = png[offset + 8 : offset + 8 + length]
        if tag == b"IHDR":
            width, height = struct.unpack(">II", data[:8])
        elif tag == b"IDAT":
            idat += data
        offset += 12 + length
    raw = zlib.decompress(idat)
    stride = 1 + 3 * width
    assert len(raw) == stride * height
    pixels = b"".join(raw[row * stride + 1 : (row + 1) * stride] for row in range(height))
    return width, height, pixels


def test_icon_dimensions():
    for size in ICON_SIZES:
        width, height, pixels = _decode_pixels(icon_png(size))
        assert (width, height) == (size, size)
        assert len(pixels) == size * size * 3


def test_icon_draws_glyph_on_background():
    _, _, pixels = _decode_pixels(icon_png(192))
    colors = {pixels[i : i + 3] for i in range(0, len(pixels), 3)}
    assert b"\x0d\x14\x20" in colors  # navy background
    assert b"\x56\xd3\x64" in colors  # green chevron core
    assert len(colors) > 2  # antialiased edge between them


def test_manifest_is_installable():
    json.dumps(MANIFEST)  # must be plain serializable data
    assert MANIFEST["display"] == "standalone"
    assert MANIFEST["start_url"] == "/"
    sizes = {icon["sizes"] for icon in MANIFEST["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon.get("purpose") == "maskable" for icon in MANIFEST["icons"])


def test_head_html_declares_manifest_and_touch_icon():
    assert 'rel="manifest"' in HEAD_HTML
    assert 'rel="apple-touch-icon"' in HEAD_HTML
    assert 'name="apple-mobile-web-app-capable"' in HEAD_HTML


def test_banner_covers_both_install_paths():
    assert "beforeinstallprompt" in BANNER_HTML  # Android/Chromium one-tap install
    assert "Add to Home Screen" in BANNER_HTML  # iOS share-sheet instructions
    assert "syncplex-a2hs-dismissed" in BANNER_HTML  # dismissal sticks


def test_routes_serve_manifest_and_icons():
    app = FastAPI()
    register(app)
    client = TestClient(app)

    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    served_icons = {icon["src"] for icon in manifest.json()["icons"]}

    for path in served_icons | {"/apple-touch-icon.png"}:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(PNG_SIGNATURE)

    assert client.get("/icons/icon-999.png").status_code == 404
