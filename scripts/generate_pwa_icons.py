"""Generate colorful MY-SHOP PWA icons."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "static" / "icons"
OUT.mkdir(parents=True, exist_ok=True)


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return tuple(int(lerp(a, b, t)) for a, b in zip(c1, c2))


def draw_icon(size, maskable=False):
    bg = Image.new("RGBA", (size, size), (6, 18, 22, 255))
    bg_draw = ImageDraw.Draw(bg)

    cx = cy = size / 2
    for r in range(int(size * 0.72), 0, -2):
        t = 1 - r / (size * 0.72)
        color = mix((8, 40, 48), (14, 120, 110), t * 0.55)
        alpha = int(90 * (1 - t))
        bg_draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, alpha))

    orb_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    od = ImageDraw.Draw(orb_layer)
    orbs = [
        ((0.22, 0.24), 0.34, (242, 176, 69)),
        ((0.78, 0.28), 0.30, (62, 207, 191)),
        ((0.30, 0.78), 0.28, (255, 120, 90)),
        ((0.72, 0.74), 0.26, (120, 150, 255)),
    ]
    for (ox, oy), rad, rgb in orbs:
        r = size * rad
        x, y = size * ox, size * oy
        for i in range(8, 0, -1):
            rr = r * (i / 8)
            a = int(55 * (i / 8))
            od.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(*rgb, a))
    orb_layer = orb_layer.filter(ImageFilter.GaussianBlur(radius=max(2, size // 40)))
    bg = Image.alpha_composite(bg, orb_layer)

    pad = int(size * (0.08 if maskable else 0.12))
    radius = int(size * 0.22)
    tile_fill = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tfd = ImageDraw.Draw(tile_fill)
    for y in range(pad, size - pad):
        t = (y - pad) / max(1, (size - 2 * pad))
        col = mix((18, 92, 98), (12, 160, 140), t)
        tfd.line([(pad, y), (size - pad, y)], fill=(*col, 255))
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, fill=255)
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tile.paste(tile_fill, (0, 0), mask)

    rim = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rim)
    inset = pad + max(1, size // 80)
    rd.rounded_rectangle(
        [inset, inset, size - inset, size - inset],
        radius=max(1, radius - size // 80),
        outline=(255, 255, 255, 55),
        width=max(1, size // 90),
    )
    tile = Image.alpha_composite(tile, rim)

    glyph = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glyph)
    gpad = int(size * 0.30)
    box = [gpad, int(size * 0.34), size - gpad, int(size * 0.70)]
    gd.rounded_rectangle(box, radius=int(size * 0.08), fill=(246, 250, 250, 235))
    hx0, hx1 = int(size * 0.38), int(size * 0.62)
    hy0, hy1 = int(size * 0.24), int(size * 0.40)
    gd.arc(
        [hx0, hy0, hx1, hy1],
        start=200,
        end=340,
        fill=(246, 250, 250, 235),
        width=max(2, size // 28),
    )
    bar_y = int(size * 0.48)
    gd.rounded_rectangle(
        [int(size * 0.36), bar_y, int(size * 0.64), bar_y + max(3, size // 22)],
        radius=max(2, size // 40),
        fill=(242, 176, 69, 255),
    )
    d = max(4, size // 18)
    gd.ellipse(
        [int(size * 0.56), int(size * 0.56), int(size * 0.56) + d, int(size * 0.56) + d],
        fill=(62, 207, 191, 255),
    )

    composed = Image.alpha_composite(bg, tile)
    composed = Image.alpha_composite(composed, glyph)

    if not maskable:
        edge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ed = ImageDraw.Draw(edge)
        ed.rounded_rectangle(
            [pad - 1, pad - 1, size - pad + 1, size - pad + 1],
            radius=radius + 1,
            outline=(0, 0, 0, 40),
            width=max(1, size // 100),
        )
        composed = Image.alpha_composite(composed, edge)

    return composed.convert("RGBA")


def save(img, name):
    path = OUT / name
    img.save(path, "PNG", optimize=True)
    print(f"{path.name}: {img.size[0]}x{img.size[1]} ({path.stat().st_size} bytes)")


def main():
    for size, name in (
        (192, "myshop-192.png"),
        (512, "myshop-512.png"),
        (180, "myshop-apple-180.png"),
    ):
        save(draw_icon(size, maskable=False), name)
    save(draw_icon(512, maskable=True), "myshop-maskable-512.png")


if __name__ == "__main__":
    main()
