#!/usr/bin/env python3
"""
Generate app_icon.png, app_icon_64.png, and app_icon.ico
for the Bethesda Strings AI Translator.

Requires: Pillow  (pip install Pillow)
Run from repo root:  python scripts/generate_icon.py
"""

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── constants ───────────────────────────────────────────────────────────────

SIZE = 512
CORNER_R = 88
OUT = Path("resources")

FONT_BOLD    = "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/dejavu/DejaVuSans.ttf"

# Palette
BG_TOP    = (7,  10,  52)   # deep navy
BG_BOT    = (24,  4,  50)   # deep purple
CLR_RU    = (130, 185, 255) # light steel-blue
CLR_ARROW = (0,  210, 255)  # bright cyan
CLR_UK    = (255, 213,  40) # Ukrainian gold
CLR_SUB   = (170, 170, 215) # muted lavender
CLR_LINE  = (100, 100, 175) # separator


# ── helpers ─────────────────────────────────────────────────────────────────

def lerp(a, b, t):
    return a + (b - a) * t

def lerp_color(c1, c2, t):
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(3))

def make_rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255
    )
    return mask

def gradient_bg(size: int, top: tuple, bot: tuple) -> Image.Image:
    img = Image.new("RGBA", (size, size))
    px = img.load()
    assert px is not None
    for y in range(size):
        t = y / (size - 1)
        c = lerp_color(top, bot, t)
        for x in range(size):
            tx = x / (size - 1)
            r = int(lerp(c[0], min(c[0] + 15, 255), tx * 0.4))
            g = int(lerp(c[1], min(c[1] + 10, 255), tx * 0.3))
            b = int(lerp(c[2], min(c[2] + 20, 255), tx * 0.4))
            px[x, y] = (r, g, b, 255)
    return img

def add_stars(draw: "ImageDraw.ImageDraw", size: int, seed: int = 7) -> None:
    rng = random.Random(seed)
    for _ in range(90):
        x = rng.randint(12, size - 12)
        y = rng.randint(12, size - 12)
        br = rng.randint(130, 255)
        alpha = rng.randint(80, 220)
        r = rng.choices([1, 1, 1, 2, 2, 3], k=1)[0]
        draw.ellipse([x - r, y - r, x + r, y + r],
                     fill=(br, br + 10, 255, alpha))

def radial_glow(size: int, cx: int, cy: int,
                max_r: int, color: tuple, blur: int = 30) -> Image.Image:
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for radius in range(max_r, 0, -4):
        t = 1.0 - radius / max_r
        alpha = int(80 * t ** 1.8)
        d.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=color + (alpha,),
        )
    return layer.filter(ImageFilter.GaussianBlur(blur))

def draw_arrow(draw: "ImageDraw.ImageDraw",
               cx: int, cy: int, w: int, h: int,
               color: tuple) -> None:
    """Filled right-pointing arrow centred at (cx, cy)."""
    x0 = cx - w // 2
    y0 = cy - h // 2
    head_w = int(w * 0.46)
    shaft_h = int(h * 0.38)
    sy1 = y0 + (h - shaft_h) // 2
    sy2 = sy1 + shaft_h
    hx = x0 + w - head_w
    pts = [
        (x0,       sy1),
        (hx,       sy1),
        (hx,       y0),
        (x0 + w,   cy),
        (hx,       y0 + h),
        (hx,       sy2),
        (x0,       sy2),
    ]
    draw.polygon(pts, fill=color)

def draw_text_shadow(draw, pos, text, font, fill,
                     shadow=(0, 0, 0, 160), offset=3):
    draw.text((pos[0] + offset, pos[1] + offset), text,
              font=font, fill=shadow)
    draw.text(pos, text, font=font, fill=fill)


# ── icon builder ────────────────────────────────────────────────────────────

def build_icon(size: int) -> Image.Image:
    scale = size / SIZE

    # background
    base = gradient_bg(size, BG_TOP, BG_BOT)
    mask = make_rounded_mask(size, max(4, int(CORNER_R * scale)))
    base.putalpha(mask)

    # stars
    star_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    add_stars(ImageDraw.Draw(star_layer), size)
    base = Image.alpha_composite(base, star_layer)

    # glow
    cx = size // 2
    cy = int(size * 0.44)
    glow = radial_glow(size, cx, cy,
                       max_r=int(140 * scale),
                       color=(0, 160, 255),
                       blur=max(1, int(28 * scale)))
    base = Image.alpha_composite(base, glow)

    draw = ImageDraw.Draw(base)

    # fonts — auto-shrink big font so "Ru → Ук" fits with 32px padding each side
    fs_sub = max(6, int(38 * scale))
    pad = int(32 * scale)
    arrow_w = int(96 * scale)
    arrow_h = int(62 * scale)
    gap     = int(22 * scale)

    def bbox(text, font):
        b = font.getbbox(text)
        return b[2] - b[0], b[3] - b[1], b[0], b[1]

    fs_big = max(8, int(158 * scale))
    font_big = ImageFont.load_default()
    while fs_big > max(8, int(40 * scale)):
        try:
            font_big = ImageFont.truetype(FONT_BOLD, fs_big)
        except OSError:
            font_big = ImageFont.load_default()
            break
        ru_w, ru_h, ru_ox, ru_oy = bbox("Ru", font_big)
        uk_w, uk_h, uk_ox, uk_oy = bbox("Ук", font_big)
        # right edge of "Ук" glyph including any right bearing
        uk_right_extra = font_big.getbbox("Ук")[2] - font_big.getbbox("Ук")[0]
        total_w = ru_w + gap + arrow_w + gap + uk_w
        if total_w + pad * 2 <= size:
            break
        fs_big -= 4

    try:
        font_sub = ImageFont.truetype(FONT_REGULAR, fs_sub)
    except OSError:
        font_sub = font_big

    ru_w,  ru_h,  ru_ox,  ru_oy  = bbox("Ru",      font_big)
    uk_w,  uk_h,  uk_ox,  uk_oy  = bbox("Ук",      font_big)
    sub_w, sub_h, sub_ox, sub_oy = bbox("STRINGS", font_sub)

    total_w = ru_w + gap + arrow_w + gap + uk_w
    x0      = (size - total_w) // 2
    text_cy = int(size * 0.44)

    # "Ru"
    draw_text_shadow(
        draw, (x0 - ru_ox, text_cy - ru_h // 2 - ru_oy),
        "Ru", font_big, fill=CLR_RU + (255,),
        offset=max(1, int(3 * scale)),
    )

    # arrow
    draw_arrow(draw, int(x0 + ru_w + gap + arrow_w // 2), int(text_cy),
               arrow_w, arrow_h, CLR_ARROW)

    # "Ук"
    draw_text_shadow(
        draw, (x0 + ru_w + gap + arrow_w + gap - uk_ox,
               text_cy - uk_h // 2 - uk_oy),
        "Ук", font_big, fill=CLR_UK + (255,),
        offset=max(1, int(3 * scale)),
    )

    # separator line
    line_y = int(size * 0.66)
    hw = int(90 * scale)
    draw.line([(cx - hw, line_y), (cx + hw, line_y)],
              fill=CLR_LINE + (140,), width=max(1, int(2 * scale)))

    # "STRINGS" subtitle
    draw.text((cx - sub_w // 2 - sub_ox, line_y + int(12 * scale)),
              "STRINGS", font=font_sub, fill=CLR_SUB + (200,))

    # inner border
    bdr = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(bdr).rounded_rectangle(
        [1, 1, size - 2, size - 2],
        radius=max(4, int(CORNER_R * scale)),
        outline=(180, 180, 255, 35),
        width=max(1, int(2 * scale)),
    )
    base = Image.alpha_composite(base, bdr)

    return base


# ── entry point ─────────────────────────────────────────────────────────────

def main():
    OUT.mkdir(exist_ok=True)

    print("Generating 512×512 …", end=" ", flush=True)
    icon512 = build_icon(512)
    icon512.save(OUT / "app_icon.png", optimize=True)
    print("done")

    print("Generating 64×64  …", end=" ", flush=True)
    icon64 = icon512.resize((64, 64), Image.LANCZOS)
    icon64.save(OUT / "app_icon_64.png", optimize=True)
    print("done")

    print("Generating .ico   …", end=" ", flush=True)
    sizes = [256, 128, 64, 48, 32, 16]
    frames = [icon512.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[0].save(
        OUT / "app_icon.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print("done")

    print(f"\nSaved to {OUT.resolve()}/")


if __name__ == "__main__":
    main()
