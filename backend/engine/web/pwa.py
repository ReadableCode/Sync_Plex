"""Add-to-home-screen support: manifest, icons, and the install banner.

iOS has no install-prompt API — a page can never trigger "Add to Home
Screen" itself, so on iPhones the banner shows share-sheet instructions
instead. Chromium browsers (Android and desktop) do have one: we catch
`beforeinstallprompt` and show a real install button. Either way the
manifest + apple-touch-icon below make the resulting shortcut launch
standalone with the syncplex name and icon.

Icons are rasterized in-process (navy tile, green ❯ chevron — the brand
mark from the web theme) so the repo carries no binary assets and no
image dependency; a few-ms pure-python render per size, cached.
"""

import json
import struct
import zlib
from functools import lru_cache

_BG = (0x0D, 0x14, 0x20)  # --bg
_FG = (0x56, 0xD3, 0x64)  # --green-bright

# ❯ chevron as a two-segment polyline in unit coordinates, rounded caps.
# Kept inside the central 60% so the icon survives Android maskable crops.
_GLYPH = ((0.38, 0.30), (0.63, 0.50), (0.38, 0.70))
_HALF_STROKE = 0.045

MANIFEST = {
    "name": "syncplex media",
    "short_name": "syncplex",
    "description": "search, request, and add media across your servers",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#0d1420",
    "theme_color": "#0d1420",
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

ICON_SIZES = (180, 192, 512)  # 180 = apple-touch-icon, 192/512 = manifest


def _segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


@lru_cache(maxsize=None)
def icon_png(size: int) -> bytes:
    """Render the app icon at `size`×`size` as an 8-bit RGB PNG."""
    (ax, ay), (bx, by), (cx, cy) = _GLYPH
    # bounding box of the stroked glyph plus one pixel of antialias apron —
    # pixels outside it are plain background, skipping the distance math
    apron = _HALF_STROKE + 1.5 / size
    x_lo, x_hi = min(ax, bx, cx) - apron, max(ax, bx, cx) + apron
    y_lo, y_hi = min(ay, by, cy) - apron, max(ay, by, cy) + apron

    rows = []
    background = bytes(_BG) * size
    for y in range(size):
        py = (y + 0.5) / size
        if not y_lo <= py <= y_hi:
            rows.append(b"\x00" + background)
            continue
        row = bytearray(background)
        for x in range(size):
            px = (x + 0.5) / size
            if not x_lo <= px <= x_hi:
                continue
            dist = min(
                _segment_distance(px, py, ax, ay, bx, by),
                _segment_distance(px, py, bx, by, cx, cy),
            )
            coverage = max(0.0, min(1.0, (_HALF_STROKE - dist) * size + 0.5))
            if coverage <= 0.0:
                continue
            for channel in range(3):
                blended = _BG[channel] + (_FG[channel] - _BG[channel]) * coverage
                row[x * 3 + channel] = round(blended)
        rows.append(b"\x00" + bytes(row))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + _png_chunk(b"IEND", b"")
    )


HEAD_HTML = """
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#0d1420">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="syncplex">
"""

# Fixed bottom banner. Hidden until the script decides which variant applies;
# dismissing it sticks (localStorage), and it never shows once installed.
BANNER_HTML = """
<style>
#a2hs-banner {
  position: fixed;
  left: 50%;
  bottom: 16px;
  transform: translateX(-50%);
  z-index: 5000;
  display: flex;
  align-items: center;
  gap: 12px;
  width: calc(100% - 24px);
  max-width: 640px;
  padding: 10px 12px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 13px;
}
#a2hs-banner[hidden] { display: none; }
#a2hs-banner .a2hs-text { flex: 1 1 auto; min-width: 0; }
#a2hs-banner .a2hs-title { font-weight: 700; }
#a2hs-banner .a2hs-title .brand-prompt { color: var(--green-bright); }
#a2hs-banner .a2hs-help { display: block; color: var(--ink-2); font-size: 12px; margin-top: 2px; }
#a2hs-banner .a2hs-help svg { vertical-align: -2px; }
#a2hs-banner button {
  flex: 0 0 auto;
  background: none;
  border: none;
  font-family: var(--font-mono);
  cursor: pointer;
}
#a2hs-install {
  background: var(--green);
  color: var(--bg);
  font-weight: 700;
  padding: 6px 14px;
  border-radius: var(--radius);
}
#a2hs-close { color: var(--muted); font-size: 14px; padding: 4px; }
</style>
<div id="a2hs-banner" hidden>
  <div class="a2hs-text">
    <span class="a2hs-title"><span class="brand-prompt">&#10095;</span> add syncplex to your home screen</span>
    <span class="a2hs-help" id="a2hs-ios-help" hidden>
      tap
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round" aria-label="share">
        <path d="M12 3v12"/><path d="M8 6.5 12 3l4 3.5"/><path d="M6 11H4v10h16V11h-2"/>
      </svg>
      below, then &#8220;Add to Home Screen&#8221;
    </span>
  </div>
  <button id="a2hs-install" hidden>install</button>
  <button id="a2hs-close" aria-label="dismiss">&#10005;</button>
</div>
<script>
(function () {
  const KEY = "syncplex-a2hs-dismissed";
  const banner = document.getElementById("a2hs-banner");
  const installBtn = document.getElementById("a2hs-install");
  const standalone =
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  if (standalone || localStorage.getItem(KEY)) return;
  const dismiss = () => { localStorage.setItem(KEY, "1"); banner.hidden = true; };
  document.getElementById("a2hs-close").addEventListener("click", dismiss);

  // iPadOS Safari reports itself as MacIntel — the touch-point check catches it
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (ios) {
    // No install API on iOS — all we can do is point at the share sheet
    document.getElementById("a2hs-ios-help").hidden = false;
    banner.hidden = false;
    return;
  }

  let deferred = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferred = event;
    installBtn.hidden = false;
    banner.hidden = false;
  });
  installBtn.addEventListener("click", async () => {
    if (!deferred) return;
    deferred.prompt();
    const choice = await deferred.userChoice;
    deferred = null;
    if (choice.outcome === "accepted") banner.hidden = true;
    else dismiss();
  });
})();
</script>
"""


def register(app) -> None:
    """Mount the manifest and icon routes on the (FastAPI) app.

    These stay outside AuthMiddleware's page gate on purpose: browsers fetch
    the manifest and touch icon without session context during install.
    """
    from fastapi import HTTPException, Response

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def _manifest() -> Response:
        return Response(json.dumps(MANIFEST), media_type="application/manifest+json")

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def _apple_touch_icon() -> Response:
        return Response(icon_png(180), media_type="image/png")

    @app.get("/icons/icon-{size}.png", include_in_schema=False)
    def _icon(size: int) -> Response:
        if size not in ICON_SIZES:
            raise HTTPException(status_code=404)
        return Response(icon_png(size), media_type="image/png")
