#!/usr/bin/env python3
"""
Generate app_icon.png and app_icon_64.png with a polished Starfield-themed design.

Design:
  - Deep space radial gradient background with star field
  - Translation motif: [EN] ──▶ [ΥΚ][DE] / [FR][JA]  (using actual Starfield fonts)
  - Cyan (#3ff0ff) glow effects on all interactive elements
  - Gold (#e8e8ac) accents on secondary language tiles
  - STRINGS wordmark + subtitle in Starfield NB_Architekt font
"""

import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

REPO = Path(__file__).resolve().parent.parent
FONTS = REPO / "data" / "fonts"
OUT = REPO / "resources"

BOLD = str(FONTS / "NB_Architekt.ttf")
LIGHT = str(FONTS / "NB_Architekt_Light.ttf")

# ── Palette ───────────────────────────────────────────────────────────────────
BG_EDGE   = np.array([5,  9, 18],  dtype=np.float32)
BG_MID    = np.array([11, 18, 35], dtype=np.float32)
BG_CENTER = np.array([14, 24, 46], dtype=np.float32)

CYAN    = (63, 240, 255)
GOLD    = (220, 200, 90)
WHITE   = (245, 252, 255)
DIM     = (120, 155, 200)
TILE_BG = (12, 22, 42)


# ── Helpers ───────────────────────────────────────────────────────────────────

def radial_bg(size: int) -> Image.Image:
    cx = cy = size / 2
    y_idx, x_idx = np.mgrid[0:size, 0:size]
    dist = np.sqrt((x_idx - cx) ** 2 + (y_idx - cy) ** 2)
    t1 = np.clip(dist / (size * 0.45), 0, 1)[..., np.newaxis]
    t2 = np.clip(dist / (size * 0.75), 0, 1)[..., np.newaxis]
    col = BG_CENTER * (1 - t1) + BG_MID * t1
    col = col * (1 - t2) + BG_EDGE * t2
    col = np.clip(col, 0, 255).astype(np.uint8)
    alpha = np.full((size, size), 255, dtype=np.uint8)
    return Image.fromarray(np.dstack([col, alpha]), "RGBA")


def scatter_stars(img: Image.Image, rng: random.Random) -> None:
    size = img.width
    star_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(star_layer)
    n = int(size * size * 0.00032)
    for _ in range(n):
        x = rng.randint(0, size - 1)
        y = rng.randint(0, size - 1)
        b = rng.random()
        r = 1 if b < 0.85 else (2 if b < 0.97 else 3)
        a = int(40 + b * 180)
        c = int(200 + b * 55)
        col = (c, c, min(255, c + 20), a)
        if r == 1:
            d.point((x, y), fill=col)
        else:
            d.ellipse((x - r, y - r, x + r, y + r), fill=col)
    # 3 bright hero stars with multi-ring glow
    for pos in [(0.12, 0.18), (0.88, 0.12), (0.78, 0.82)]:
        x, y = int(pos[0] * size), int(pos[1] * size)
        for radius in [7, 4, 2, 1]:
            a = int(25 + (7 - radius) * 28)
            d.ellipse((x - radius, y - radius, x + radius, y + radius),
                      fill=(255, 255, 255, a))
    img.alpha_composite(star_layer)


def glow_composite(img: Image.Image, layer: Image.Image,
                   radius: int, strength: float = 1.2) -> None:
    """Blur *layer* for glow, composite twice for intensity, then sharp layer on top."""
    glow = layer.filter(ImageFilter.GaussianBlur(radius))
    arr = np.array(glow, dtype=np.float32)
    arr[..., 3] = np.clip(arr[..., 3] * strength, 0, 255)
    glow = Image.fromarray(arr.astype(np.uint8), "RGBA")
    img.alpha_composite(glow)
    img.alpha_composite(glow)
    img.alpha_composite(layer)


def draw_tile(img: Image.Image, x1: int, y1: int, x2: int, y2: int,
              text: str, border: tuple, font: ImageFont.FreeTypeFont,
              corner: int = 10) -> None:
    s = img.size
    # Border glow
    gl = Image.new("RGBA", s, (0, 0, 0, 0))
    ImageDraw.Draw(gl).rounded_rectangle(
        [x1, y1, x2, y2], radius=corner, outline=border + (200,), width=3)
    img.alpha_composite(gl.filter(ImageFilter.GaussianBlur(6)))

    # Fill + sharp border
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x1, y1, x2, y2], radius=corner,
                        fill=TILE_BG + (255,), outline=border + (195,), width=2)

    # Centred text
    bb = font.getbbox(text)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    tx = x1 + ((x2 - x1) - tw) // 2 - bb[0]
    ty = y1 + ((y2 - y1) - th) // 2 - bb[1]

    tg = Image.new("RGBA", s, (0, 0, 0, 0))
    ImageDraw.Draw(tg).text((tx, ty), text, font=font, fill=border + (255,))
    glow_composite(img, tg, radius=5, strength=0.9)
    ImageDraw.Draw(img).text((tx, ty), text, font=font, fill=WHITE + (255,))


def draw_en_block(img: Image.Image, x1: int, y1: int, x2: int, y2: int,
                  font: ImageFont.FreeTypeFont, corner: int = 14) -> None:
    s = img.size
    # Outer glow ring
    gl = Image.new("RGBA", s, (0, 0, 0, 0))
    ImageDraw.Draw(gl).rounded_rectangle(
        [x1 - 4, y1 - 4, x2 + 4, y2 + 4], radius=corner + 4,
        outline=CYAN + (140,), width=5)
    img.alpha_composite(gl.filter(ImageFilter.GaussianBlur(12)))

    # Fill
    ImageDraw.Draw(img).rounded_rectangle(
        [x1, y1, x2, y2], radius=corner,
        fill=(10, 20, 44, 255), outline=CYAN + (230,), width=3)

    text = "EN"
    bb = font.getbbox(text)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    tx = x1 + ((x2 - x1) - tw) // 2 - bb[0]
    ty = y1 + ((y2 - y1) - th) // 2 - bb[1]

    tg = Image.new("RGBA", s, (0, 0, 0, 0))
    ImageDraw.Draw(tg).text((tx, ty), text, font=font, fill=CYAN + (255,))
    glow_composite(img, tg, radius=8, strength=1.0)
    ImageDraw.Draw(img).text((tx, ty), text, font=font, fill=WHITE + (255,))


def draw_arrow(img: Image.Image, x1: int, y: int, x2: int) -> None:
    s = img.size
    hs = max(16, (x2 - x1) // 4)    # head size proportional to arrow length
    thick = max(5, (x2 - x1) // 14)

    arrow = Image.new("RGBA", s, (0, 0, 0, 0))
    d = ImageDraw.Draw(arrow)
    # Shaft
    d.rectangle([x1, y - thick // 2, x2 - hs, y + thick // 2],
                fill=CYAN + (255,))
    # Arrowhead
    d.polygon([
        (x2 - hs, y - hs // 2),
        (x2,      y),
        (x2 - hs, y + hs // 2),
    ], fill=CYAN + (255,))
    # Circuit dots on shaft
    dot_r = max(2, thick - 1)
    step = max(16, (x2 - x1) // 8)
    for dx in range(x1 + step, x2 - hs - step // 2, step):
        d.ellipse([dx - dot_r, y - dot_r, dx + dot_r, y + dot_r],
                  fill=CYAN + (210,))

    glow_composite(img, arrow, radius=10, strength=1.3)


def centered_text_glow(img: Image.Image, y: int, text: str,
                        font: ImageFont.FreeTypeFont, color: tuple,
                        glow_r: int = 8, glow_s: float = 0.55) -> None:
    W = img.width
    bb = font.getbbox(text)
    tw = bb[2] - bb[0]
    x = (W - tw) // 2 - bb[0]
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((x, y), text, font=font, fill=color + (255,))
    glow = layer.filter(ImageFilter.GaussianBlur(glow_r))
    arr = np.array(glow, dtype=np.float32)
    arr[..., 3] = np.clip(arr[..., 3] * glow_s, 0, 255)
    img.alpha_composite(Image.fromarray(arr.astype(np.uint8), "RGBA"))
    img.alpha_composite(layer)


def add_scan_lines(img: Image.Image, alpha: int = 7) -> None:
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for y in range(0, img.height, 4):
        d.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha))
    img.alpha_composite(ov)


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1],
                                        radius=radius, fill=255)
    return m


# ── Generator ─────────────────────────────────────────────────────────────────

def generate(size: int = 512) -> Image.Image:
    rng = random.Random(42)
    sc = size / 512

    def s(v: float) -> int:
        return max(1, int(v * sc))

    # ── Background ────────────────────────────────────────────────────────────
    img = radial_bg(size)
    scatter_stars(img, rng)

    # Nebula band behind the graphic
    nb = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(nb).ellipse(
        [s(-50), s(95), s(562), s(215)], fill=(28, 75, 115, 16))
    img.alpha_composite(nb.filter(ImageFilter.GaussianBlur(s(28))))

    # ── Fonts ─────────────────────────────────────────────────────────────────
    fnt_en   = ImageFont.truetype(BOLD,  s(76))
    fnt_tile = ImageFont.truetype(BOLD,  s(39))
    fnt_cyr  = ImageFont.truetype(str(FONTS / "RF_55_SB.ttf"), s(39))  # for УК tile
    fnt_str  = ImageFont.truetype(BOLD,  s(54))
    fnt_sub  = ImageFont.truetype(LIGHT, s(22))

    # ── Layout: balanced centering ─────────────────────────────────────────────
    # EN block (150px) + arrow gap (62px) + 2×(91px cell + 5px gap) - last gap
    # = 150 + 62 + 187 = 399px → left margin = (512-399)/2 = 56px
    en_x1, en_x2 = s(56), s(206)
    en_y1, en_y2 = s(80), s(228)

    # ── EN block ──────────────────────────────────────────────────────────────
    draw_en_block(img, en_x1, en_y1, en_x2, en_y2, fnt_en, corner=s(14))

    # ── Arrow ─────────────────────────────────────────────────────────────────
    arrow_y = (en_y1 + en_y2) // 2
    draw_arrow(img, s(216), arrow_y, s(284))

    # ── 2×2 language tile grid ────────────────────────────────────────────────
    gx, gy = s(290), en_y1
    cw, ch, gap = s(91), s(69), s(5)
    tiles = [
        ("УК", CYAN,  fnt_cyr),   # Cyrillic via RF_55_SB
        ("DE", CYAN,  fnt_tile),
        ("FR", GOLD,  fnt_tile),
        ("JA", GOLD,  fnt_tile),
    ]
    for i, (lang, border, fnt) in enumerate(tiles):
        ci, ri = i % 2, i // 2
        tx1 = gx + ci * (cw + gap)
        ty1 = gy + ri * (ch + gap)
        draw_tile(img, tx1, ty1, tx1 + cw, ty1 + ch, lang, border, fnt, s(10))

    # ── Divider line ──────────────────────────────────────────────────────────
    div_y = s(258)
    dl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(dl).line([(s(44), div_y), (s(468), div_y)],
                            fill=CYAN + (110,), width=max(1, s(1)))
    img.alpha_composite(dl.filter(ImageFilter.GaussianBlur(s(2))))
    img.alpha_composite(dl)

    # ── STRINGS wordmark ──────────────────────────────────────────────────────
    centered_text_glow(img, s(270), "STRINGS", fnt_str,
                       WHITE, glow_r=s(10), glow_s=0.45)

    # ── Subtitle ──────────────────────────────────────────────────────────────
    centered_text_glow(img, s(340), "BETHESDA LOCALIZATION EDITOR", fnt_sub,
                       DIM, glow_r=s(4), glow_s=0.35)

    # ── Scan-lines texture ────────────────────────────────────────────────────
    if size >= 200:
        add_scan_lines(img, alpha=6)

    # ── Rounded-corner mask ───────────────────────────────────────────────────
    mask = rounded_mask(size, radius=s(54))
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, mask=mask)
    return result


def make_ico(png: Path, ico: Path) -> None:
    src = Image.open(png)
    sizes = [256, 128, 64, 48, 32, 16]
    frames = [src.resize((sz, sz), Image.LANCZOS) for sz in sizes]
    frames[0].save(ico, format="ICO", append_images=frames[1:],
                   sizes=[(sz, sz) for sz in sizes])
    print(f"  wrote {ico}")


if __name__ == "__main__":
    print("Generating 512 px icon…")
    icon = generate(512)
    icon.save(OUT / "app_icon.png", optimize=True)
    print(f"  wrote {OUT / 'app_icon.png'}")

    print("Generating 64 px icon…")
    icon.resize((64, 64), Image.LANCZOS).save(OUT / "app_icon_64.png", optimize=True)
    print(f"  wrote {OUT / 'app_icon_64.png'}")

    print("Generating .ico…")
    make_ico(OUT / "app_icon.png", OUT / "app_icon.ico")

    print("Done.")
