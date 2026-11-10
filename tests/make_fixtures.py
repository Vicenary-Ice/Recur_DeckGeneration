"""Generate raw logo candidates that mimic what the live pipeline downloads, so every branch of the
logo code is exercised offline: colour-on-white JPG, colour-on-transparent PNG, light-on-transparent
PNG, coloured badge with knocked-out text, SVG, a too-small favicon, and a photo (non-logo).

The white silhouettes shipped inside the template are used as shapes and recoloured.
Run:  python tests/make_fixtures.py
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "assets" / "recur_template.pptx"
FIX = ROOT / "fixtures"
FONT = ROOT / "assets" / "fonts" / "NotoSansArabic-Bold.ttf"


def template_logo(name: str) -> Image.Image:
    with zipfile.ZipFile(TEMPLATE) as z:
        return Image.open(io.BytesIO(z.read(f"ppt/media/{name}"))).convert("RGBA")


def recolor(img: Image.Image, rgb: tuple[int, int, int]) -> Image.Image:
    a = np.asarray(img).copy()
    a[..., 0], a[..., 1], a[..., 2] = rgb
    return Image.fromarray(a, "RGBA")


def on_bg(img: Image.Image, bg: tuple[int, int, int], pad: int = 40) -> Image.Image:
    canvas = Image.new("RGB", (img.width + 2 * pad, img.height + 2 * pad), bg)
    canvas.paste(img, (pad, pad), img)
    return canvas


def badge(img: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """Coloured rounded badge with the logo knocked out in white (designed for light pages)."""
    pad = 30
    out = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(out).rounded_rectangle([0, 0, out.width - 1, out.height - 1], radius=24, fill=(*color, 255))
    out.alpha_composite(recolor(img, (255, 255, 255)), (pad, pad))
    return out


def text_logo(text: str, color, bg=None, h=160) -> Image.Image:
    f = ImageFont.truetype(str(FONT), h)
    l, t, r, b = f.getbbox(text)
    im = Image.new("RGBA" if bg is None else "RGB", (r - l + 60, b - t + 60), (0, 0, 0, 0) if bg is None else bg)
    d = ImageDraw.Draw(im)
    d.ellipse([10, 10, 10 + (b - t) // 2, 10 + (b - t) // 2], fill=color)  # little brand mark
    d.text((30 - l + (b - t) // 3, 30 - t), text, font=f, fill=color)
    return im


def photo() -> Image.Image:
    rng = np.random.default_rng(7)
    a = (rng.random((300, 400, 3)) * 255).astype(np.uint8)
    return Image.fromarray(a).filter(ImageFilter.GaussianBlur(6))


def put(case: str, company_slug: str, fname: str, img_or_bytes) -> None:
    d = FIX / case / "logos" / company_slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / fname
    if isinstance(img_or_bytes, bytes):
        p.write_bytes(img_or_bytes)
    elif p.suffix == ".jpg":
        img_or_bytes.convert("RGB").save(p, quality=88)
    else:
        img_or_bytes.save(p)


SVG_ON_WHITE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 100">
<rect width="420" height="100" fill="#ffffff"/>
<circle cx="50" cy="50" r="34" fill="#E4572E"/><circle cx="50" cy="50" r="16" fill="#ffffff"/>
<rect x="105" y="30" width="290" height="14" rx="7" fill="#1B2A49"/>
<rect x="105" y="56" width="200" height="14" rx="7" fill="#E4572E"/></svg>"""


def main() -> None:
    # ---------------- case 1: Ai2000 (mirrors the reference deck)
    put("ai2000", "ai2000", "1_brandfetch.png", recolor(template_logo("image3.png"), (20, 52, 110)))       # colour on alpha
    put("ai2000", "bluecherry", "1_site.jpg", on_bg(recolor(template_logo("image39.png"), (10, 92, 171)), (255, 255, 255)))
    put("ai2000", "aptean", "1_brandfetch.png", recolor(template_logo("image40.png"), (236, 112, 37)))
    put("ai2000", "netsuite", "1_site.jpg", on_bg(recolor(template_logo("image41.png"), (30, 30, 30)), (255, 255, 255)))
    put("ai2000", "fdm4", "1_brandfetch.png", template_logo("image42.png"))                               # already light
    put("ai2000", "aims360", "1_site.png", badge(template_logo("image43.png"), (0, 120, 140)))              # knockout
    put("ai2000", "apparelmagic", "1_photo.jpg", photo())                                                  # must be rejected
    put("ai2000", "apparelmagic", "2_site.jpg", on_bg(recolor(template_logo("image44.png"), (40, 40, 40)), (244, 244, 244)))
    put("ai2000", "uphance", "1_site.png", recolor(template_logo("image45.png"), (90, 60, 200)))           # small source

    # ---------------- case 2: PCRecruiter (unseen company; mostly typeset fallbacks)
    put("pcrecruiter", "pcrecruiter", "1_site.jpg", text_logo("PCRecruiter", (0, 84, 166), bg=(255, 255, 255)))
    put("pcrecruiter", "bullhorn", "1_favicon.png", text_logo("B", (232, 93, 4)).resize((32, 32)))       # too small -> typeset
    # zoho-recruit/1_brandfetch.svg is a committed copy of the Simple Icons "zoho" glyph (CC0); kept as-is.
    put("pcrecruiter", "jobadder", "1_brandfetch.svg", SVG_ON_WHITE.encode())                              # SVG with white bg

    # ---------------- case 3: stress (must FAIL QA in specific, predictable ways)
    put("stress", "lighthouse-veterinary-practice-cloud", "1_photo.jpg", photo())
    print("fixtures written to", FIX)


if __name__ == "__main__":
    main()
