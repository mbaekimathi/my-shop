"""Dynamic PWA / favicon PNGs branded with the company name."""

from __future__ import annotations

import hashlib
import io
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from . import services

# Bump when the drawn icon design changes so manifests/favicons bust cache.
ICON_ASSET_VERSION = "2026-11-08.3"

# Teal wash matching the chat --wa-* tokens (flat, no gloss).
_BG_TOP = (37, 211, 102)       # --wa-green #25d366
_BG_MID = (0, 168, 132)        # --wa-send  #00a884
_BG_BOTTOM = (7, 94, 84)       # --wa-teal  #075e54
_BUBBLE = (255, 255, 255)
_DOT = (7, 94, 84)
_TEXT = (255, 255, 255)
_SUB = (210, 242, 233)
_SAFE = 0.12                   # maskable safe-zone inset


def icon_label() -> str:
    """Name drawn on the app icon — company name, or MY SHOP if unset."""
    name = " ".join((services.company_name() or "").split())
    if not name or name.upper().replace("-", " ") in {"MY SHOP", "MYSHOP"}:
        return "MY SHOP"
    return name


def icon_cache_bust() -> str:
    """Query token that changes with design version + company label."""
    raw = f"{ICON_ASSET_VERSION}:{icon_label()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def _font_paths() -> list[Path]:
    base = Path(settings.BASE_DIR) / "static" / "fonts"
    return [
        base / "Manrope-Bold.ttf",
        base / "Manrope-SemiBold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ]


@lru_cache(maxsize=8)
def _truetype(size: int):
    from PIL import ImageFont

    for path in _font_paths():
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _paint_background(img) -> None:
    """Diagonal teal→deep teal wash — modern, no glass highlight."""
    w, h = img.size
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = (0.55 * y / max(h - 1, 1)) + (0.45 * x / max(w - 1, 1))
            if t < 0.45:
                color = _lerp(_BG_TOP, _BG_MID, t / 0.45)
            else:
                color = _lerp(_BG_MID, _BG_BOTTOM, (t - 0.45) / 0.55)
            px[x, y] = color


def _draw_bubble(draw, cx: float, cy: float, size: int) -> None:
    """Rounded chat bubble with three typing dots + soft depth."""
    bw = size * 0.40
    bh = size * 0.30
    x0, y0 = cx - bw / 2, cy - bh / 2
    x1, y1 = cx + bw / 2, cy + bh / 2
    radius = max(8, int(size * 0.07))

    # Soft shadow under the bubble (flat depth, not skeuomorphic gloss).
    shadow_shift = size * 0.012
    draw.rounded_rectangle(
        [x0 + shadow_shift, y0 + shadow_shift, x1 + shadow_shift, y1 + shadow_shift],
        radius=radius,
        fill=(4, 55, 50),
    )
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=_BUBBLE)

    # Tail — short triangle under the left side.
    tail = [
        (cx - bw * 0.24, y1 - 2),
        (cx - bw * 0.06, y1 - 2),
        (cx - bw * 0.30, y1 + size * 0.075),
    ]
    draw.polygon(tail, fill=_BUBBLE)

    # Typing dots — vertically centered in the bubble body.
    dot_r = max(2.5, size * 0.030)
    gap = bw * 0.15
    for i in (-1, 0, 1):
        draw.ellipse(
            [
                cx + i * gap - dot_r,
                cy - dot_r,
                cx + i * gap + dot_r,
                cy + dot_r,
            ],
            fill=_DOT,
        )


def _fit_label(draw, text: str, max_width: float, size: int):
    """Shrink the font until the company name fits the safe width."""
    label = text.strip().upper()
    if len(label) > 18:
        label = label[:17] + "…"
    for scale in (0.095, 0.084, 0.074, 0.064, 0.056, 0.048):
        font = _truetype(max(10, int(size * scale)))
        bbox = draw.textbbox((0, 0), label, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return label, font
    font = _truetype(max(10, int(size * 0.042)))
    return label, font


def render_app_icon(size: int = 512, *, rounded: bool = False) -> bytes:
    """Return a PNG app icon. `rounded` bakes a squircle for favicon/"any"."""
    from PIL import Image, ImageDraw

    size = max(48, min(int(size), 1024))
    canvas = Image.new("RGB", (size, size), _BG_BOTTOM)
    _paint_background(canvas)

    inset = size * _SAFE
    content = size - 2 * inset
    cx = size / 2
    bubble_cy = inset + content * 0.36

    draw = ImageDraw.Draw(canvas)
    _draw_bubble(draw, cx, bubble_cy, size)

    label = icon_label()
    max_w = content * 0.90
    text, font = _fit_label(draw, label, max_w, size)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    text_y = inset + content * 0.66
    draw.text((cx - tw / 2, text_y), text, font=font, fill=_TEXT)

    sub_font = _truetype(max(9, int(size * 0.042)))
    sub = "CHAT"
    sb = draw.textbbox((0, 0), sub, font=sub_font)
    sw = sb[2] - sb[0]
    # Light letter-spacing feel via the smaller weight + tracking-ish gap.
    draw.text(
        (cx - sw / 2, text_y + th + size * 0.018),
        sub,
        font=sub_font,
        fill=_SUB,
    )

    if rounded:
        mask = Image.new("L", (size, size), 0)
        mask_draw = ImageDraw.Draw(mask)
        radius = int(size * 0.22)
        mask_draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        img.paste(canvas, (0, 0))
        img.putalpha(mask)
        out = img
    else:
        out = canvas.convert("RGBA")

    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def write_static_fallbacks() -> None:
    """Refresh static/img/icon-*.png used as offline/collectstatic fallbacks."""
    dest = Path(settings.BASE_DIR) / "static" / "img"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "icon-192.png").write_bytes(render_app_icon(192, rounded=True))
    (dest / "icon-512.png").write_bytes(render_app_icon(512, rounded=True))
