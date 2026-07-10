#!/usr/bin/env python3
"""
Generate resources/nexusmods_header.png — the 1300x300 banner used on the
NexusMods page and at the top of README.md.

The space artwork is supplied as a separate background image (Ideogram 4.0
output); this script only crops it to 13:3 and composites the text layer on
top:

  - "Bethesda Strings Editor" wordmark + subtitle + format/version line
  - 4x2 grid of feature chips (flat fill, 1px border, accent-coloured label)
  - 2x2 grid of translucent stat cards over the gas giant

Everything below is measured from the original header, so re-running this
reproduces it on a new background.

    python scripts/generate_header.py --bg path/to/space.png
"""

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "resources"
BUNDLED = REPO / "data" / "fonts"

W, H = 1300, 300

# ── Palette ───────────────────────────────────────────────────────────────────
TITLE_FG    = "#D7EEFF"
SUBTITLE_FG = "#6EB4FF"
META_FG     = "#8CAAD2"
DIVIDER_FG  = "#2864C8"

CHIP_BORDER = "#466496"
CARD_FILL   = (6, 10, 20, 156)      # measured alpha 0.61
CARD_BORDER = (33, 60, 101, 204)    # measured alpha 0.80
LABEL_FG    = "#A0CDF0"

# The original artwork was near-black under the wordmark. On brighter art the
# version line drops to 1.9:1 against a lit nebula; this gradient restores it.
# Held flat past the longest text run (the version line ends at x=416), then eased out.
SCRIM_RGB, SCRIM_FLAT, SCRIM_X = (4, 8, 20), 440, 720

# ── Content ───────────────────────────────────────────────────────────────────
TITLE    = "Bethesda Strings Editor"
SUBTITLE = "AI-powered localization studio for Bethesda games"
FORMATS  = ".strings .dlstrings .ilstrings · BA2 archives · ESP/ESM plugins"

# (label, fill, text colour) in reading order, laid out column-major left→right
CHIPS = [
    ("Ollama  Translation",        "#120A2A", "#AF82FF"),
    ("Claude  API",                "#261032", "#C896FF"),
    ("Quality Checker · 16 codes", "#082612", "#5AD782"),
    ("Glossary Manager",           "#261C08", "#FFC646"),
    ("ESP/ESM  Parser",            "#0A1C30", "#5AC3FF"),
    ("BA2  Archive Support",       "#1E0F2A", "#B982FF"),
    ("Batch  Translation",         "#23080F", "#FF735F"),
    ("NexusMods  Integration",     "#231908", "#D7AA4B"),
]

# These track the app; update alongside the QC checker / glossary / cache.
STATS = [
    ("16",  "QC Checks",       "#00DCFF"),
    ("8K+", "Protected Terms", "#AF82FF"),
    ("12",  "Languages",       "#5AD782"),
    ("50K", "Cache Entries",   "#FFC646"),
]

# ── Layout (all measured from the original PNG) ───────────────────────────────
TITLE_PEN    = (28, 60)     # left / baseline
SUBTITLE_PEN = (28, 86)
META_PEN     = (28, 108)

DIVIDER_Y, DIVIDER_X1, DIVIDER_X2 = 118, 28, 590

CHIP_COLS   = (28, 224)
CHIP_ROWS   = (128, 155, 182, 209)
CHIP_W, CHIP_H, CHIP_R = 188, 22, 3
CHIP_PAD_X, CHIP_BASE = 9, 16          # text left pad, baseline offset from top

CARD_COLS   = (860, 1069)
CARD_ROWS   = (34, 143)
CARD_W, CARD_H, CARD_R = 180, 78, 6
CARD_PAD_X  = 11
CARD_NUM_BASE, CARD_LBL_BASE = 37, 57  # baselines relative to card top


# ── Fonts ─────────────────────────────────────────────────────────────────────
# The original header was composited with Ubuntu (display) + Roboto (UI text).
# Ship-anywhere fallback: the bundled Starfield faces in data/fonts.
FONT_CANDIDATES = {
    "ubuntu-bold": [
        "/usr/share/fonts/TTF/Ubuntu-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/TTF/UbuntuNerdFont-Bold.ttf",
        BUNDLED / "RF_55_SB.ttf",
    ],
    "ubuntu-regular": [
        "/usr/share/fonts/TTF/Ubuntu-Regular.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        "/usr/share/fonts/TTF/UbuntuNerdFont-Regular.ttf",
        BUNDLED / "RF_35_M.ttf",
    ],
    "roboto-medium": [
        Path.home() / ".fonts" / "Roboto-Medium.ttf",
        "/usr/share/fonts/TTF/Roboto-Medium.ttf",
        "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Medium.ttf",
        BUNDLED / "RF_55_M.ttf",
    ],
    "roboto-regular": [
        Path.home() / ".fonts" / "Roboto-Regular.ttf",
        "/usr/share/fonts/TTF/Roboto-Regular.ttf",
        "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Regular.ttf",
        BUNDLED / "RF_35_M.ttf",
    ],
}


_warned: set[str] = set()


def load_font(key: str, size: int) -> ImageFont.FreeTypeFont:
    for cand in FONT_CANDIDATES[key]:
        p = Path(cand)
        if p.exists():
            # The bundled faces are a different design; silently substituting them
            # would reshape the banner without anyone noticing.
            if BUNDLED in p.parents and key not in _warned:
                _warned.add(key)
                print(f"warning: {key} not installed, falling back to {p.name} "
                      f"— header will not match the reference", file=sys.stderr)
            return ImageFont.truetype(str(p), size)
    raise FileNotFoundError(f"no font found for {key!r}; tried {FONT_CANDIDATES[key]}")


def app_version() -> str:
    ns: dict = {}
    exec((REPO / "_version.py").read_text(), ns)          # noqa: S102
    v = ns.get("__version__", "dev")
    if v != "dev":
        return v
    try:
        return subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout.strip().lstrip("v")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "dev"


# ── Drawing ───────────────────────────────────────────────────────────────────

def background(path: Path, y_offset: int) -> Image.Image:
    src = Image.open(path).convert("RGB")
    crop_h = round(src.width * H / W)
    if crop_h > src.height:
        raise ValueError(f"{path.name} is too short to crop to {W}:{H}")
    y = max(0, min(y_offset, src.height - crop_h))
    return src.crop((0, y, src.width, y + crop_h)).resize((W, H), Image.Resampling.LANCZOS)


def draw_scrim(img: Image.Image, strength: float) -> None:
    """Left-to-right darkening ramp so the wordmark block stays legible."""
    if strength <= 0:
        return
    x = np.arange(W, dtype=np.float32)
    t = np.clip((x - SCRIM_FLAT) / (SCRIM_X - SCRIM_FLAT), 0.0, 1.0)
    ramp = (1.0 - t) ** 1.6
    alpha = (ramp * strength * 255).astype(np.uint8)
    layer = np.zeros((H, W, 4), dtype=np.uint8)
    layer[..., :3] = SCRIM_RGB
    layer[..., 3] = np.broadcast_to(alpha, (H, W))
    img.alpha_composite(Image.fromarray(layer, "RGBA"))


def draw_chips(img: Image.Image) -> None:
    font = load_font("roboto-medium", 11)
    d = ImageDraw.Draw(img)
    for i, (label, fill, fg) in enumerate(CHIPS):
        x = CHIP_COLS[i % 2]
        y = CHIP_ROWS[i // 2]
        d.rounded_rectangle([x, y, x + CHIP_W, y + CHIP_H], radius=CHIP_R,
                            fill=fill, outline=CHIP_BORDER, width=1)
        d.text((x + CHIP_PAD_X, y + CHIP_BASE), label,
               font=font, fill=fg, anchor="ls")


def draw_cards(img: Image.Image) -> None:
    num_font = load_font("ubuntu-bold", 30)
    lbl_font = load_font("roboto-regular", 11)
    # Translucent panels need their own layer so alpha composites onto the art.
    panel = Image.new("RGBA", img.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    for i in range(len(STATS)):
        x, y = CARD_COLS[i % 2], CARD_ROWS[i // 2]
        pd.rounded_rectangle([x, y, x + CARD_W, y + CARD_H], radius=CARD_R,
                             fill=CARD_FILL, outline=CARD_BORDER, width=1)
    img.alpha_composite(panel)

    d = ImageDraw.Draw(img)
    for i, (num, label, fg) in enumerate(STATS):
        x, y = CARD_COLS[i % 2], CARD_ROWS[i // 2]
        d.text((x + CARD_PAD_X, y + CARD_NUM_BASE), num,
               font=num_font, fill=fg, anchor="ls")
        d.text((x + CARD_PAD_X, y + CARD_LBL_BASE), label,
               font=lbl_font, fill=LABEL_FG, anchor="ls")


def build(bg_path: Path, y_offset: int, version: str, scrim: float) -> Image.Image:
    img = background(bg_path, y_offset).convert("RGBA")
    draw_scrim(img, scrim)
    d = ImageDraw.Draw(img)

    d.text(TITLE_PEN, TITLE, font=load_font("ubuntu-bold", 40),
           fill=TITLE_FG, anchor="ls")
    d.text(SUBTITLE_PEN, SUBTITLE, font=load_font("roboto-medium", 15),
           fill=SUBTITLE_FG, anchor="ls")
    d.text(META_PEN, f"v{version} · {FORMATS}", font=load_font("ubuntu-regular", 13),
           fill=META_FG, anchor="ls")

    d.line([(DIVIDER_X1, DIVIDER_Y), (DIVIDER_X2, DIVIDER_Y)],
           fill=DIVIDER_FG, width=1)

    draw_chips(img)
    draw_cards(img)
    return img.convert("RGB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bg", type=Path, required=True,
                    help="space artwork; cropped to 13:3 from --y-offset")
    ap.add_argument("--out", type=Path, default=OUT / "nexusmods_header.png")
    ap.add_argument("--y-offset", type=int, default=0,
                    help="top of the crop window in source pixels (default 0)")
    ap.add_argument("--version", default=None,
                    help="override the version string (default: from _version.py / git tag)")
    ap.add_argument("--scrim", type=float, default=0.55, metavar="A",
                    help="strength of the left darkening ramp, 0 disables (default 0.55)")
    args = ap.parse_args()

    version = args.version or app_version()
    if version == "dev":
        print("warning: version resolved to 'dev' — pass --version for a release build",
              file=sys.stderr)

    img = build(args.bg, args.y_offset, version, args.scrim)
    img.save(args.out, optimize=True)
    print(f"wrote {args.out}  ({img.width}x{img.height}, v{version})")
