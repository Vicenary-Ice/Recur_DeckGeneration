"""Turn any logo file (SVG / PNG / JPG / WebP / ICO) into a flat white silhouette on transparency,
score it, and provide a typeset fallback that always succeeds.

Pipeline: load -> rasterise at >= 600px tall -> build alpha mask -> paint white -> trim -> metrics.
Never use PowerPoint's brightness trick: it turns opaque backgrounds into white rectangles.

Print grade (Agent 4): metrics record `native_w` / `native_h`, the trimmed logo's size in *source* pixels (before the
upscale to 600 px; None for vector sources), so dpi can be judged after layout. `ink_bands` / `crop_tagline` find
and remove a tagline line that would print as micro-text.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .spec import FONTS, MAP_LOGO

TARGET_RASTER_H = 600
TYPESET_MIN_H = 0.12                                         # in: typeset text on the map never prints smaller
TYPESET_MAX_ASPECT = round(MAP_LOGO["max_w"] / TYPESET_MIN_H, 2)  # one line wider than this goes onto 2 lines
TAGLINE_MAX_SHARE = 0.22                                     # a lower ink band under 22% of the height is a tagline
BAND_GAP = 0.015                                             # gaps under 1.5% of the height are not band breaks


@dataclass
class LogoMetrics:
    width_px: int
    height_px: int
    aspect: float            # w / h after trimming
    ink_coverage: float      # share of bbox pixels that are >50% opaque
    source_kind: str         # svg | raster-alpha | raster-opaque | typeset
    knocked_out_white: bool  # near-white interior pixels were removed (logo designed for light bg)
    ok: bool
    problems: list[str]
    native_w: int | None = None  # trimmed size in source pixels (before upscaling); None = vector / typeset
    native_h: int | None = None
    lines: int = 1               # typeset wordmarks: 1 or 2 lines of text

    def as_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ loading
def _is_svg(data: bytes) -> bool:
    """True for SVG text, including files that start with a BOM, an XML declaration, a comment or a DOCTYPE."""
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head[:1] != b"<":
        return False
    return b"<svg" in head


def load_rgba(data: bytes) -> tuple[Image.Image, str]:
    """Return an RGBA image at least TARGET_RASTER_H tall (SVGs are rendered at that height)."""
    if _is_svg(data):
        import resvg_py
        svg = data.decode("utf-8-sig", errors="ignore")
        # Render at a fixed height; resvg keeps the aspect ratio when only height is given.
        png = resvg_py.svg_to_bytes(svg_string=svg, height=TARGET_RASTER_H)
        img = Image.open(io.BytesIO(bytes(png))).convert("RGBA")
        return img, "svg"
    img = Image.open(io.BytesIO(data))
    if img.format == "ICO":
        sizes = sorted(img.info.get("sizes", [img.size]), key=lambda s: s[0] * s[1])
        img.size = sizes[-1]
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    img = img.convert("RGBA")
    a = np.asarray(img)[..., 3]
    kind = "raster-alpha" if (a < 250).mean() > 0.01 else "raster-opaque"
    return img, kind


# ------------------------------------------------------------------ whitening
def _border(arr: np.ndarray, k: int = 2) -> np.ndarray:
    return np.concatenate([arr[:k].reshape(-1, arr.shape[-1]), arr[-k:].reshape(-1, arr.shape[-1]),
                           arr[:, :k].reshape(-1, arr.shape[-1]), arr[:, -k:].reshape(-1, arr.shape[-1])])


def _smoothstep(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def whiten(img: Image.Image, source_kind: str, designed_for_dark: bool = False) -> tuple[Image.Image, bool]:
    """Return (white RGBA silhouette, knocked_out_white).

    designed_for_dark=True (e.g. Brandfetch theme "light"): the logo's white parts are real ink, not
    knockouts, so they are kept. Otherwise white areas inside a mostly-coloured logo are treated as
    knockouts (white text in a coloured badge) and removed."""
    arr = np.asarray(img.convert("RGBA")).astype(np.float32)
    rgb, alpha = arr[..., :3], arr[..., 3] / 255.0
    knocked = False
    border = _border(arr)
    border_transparent = (border[:, 3] < 128).mean() > 0.6

    if border_transparent:
        mask = alpha.copy()
        opaque = alpha > 0.5
        if opaque.any():
            min_c = rgb.min(axis=-1)
            near_white = (min_c > 215) & opaque
            share_white = near_white.sum() / opaque.sum()
            # Mostly-coloured logo with some white parts: the white parts are knockouts meant to
            # show the page through (e.g. white text inside a coloured badge). Remove them.
            if 0.0 < share_white < 0.5 and not designed_for_dark:
                mask *= 1.0 - _smoothstep(min_c, 200, 235)
                knocked = True
    else:
        # Opaque image: background = median border colour; alpha from colour distance to it.
        bg = np.median(border[:, :3], axis=0)
        dist = np.sqrt(((rgb - bg) ** 2).sum(axis=-1))
        mask = _smoothstep(dist, 18.0, 70.0) * alpha
        knocked = bool(bg.min() > 200)  # white page bg: interior white areas become transparent too

    mask[mask < 0.08] = 0.0
    out = np.zeros(arr.shape, dtype=np.uint8)
    out[..., :3] = 255
    out[..., 3] = np.clip(mask * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA"), knocked


def trim(img: Image.Image, pad_px: int = 0) -> Image.Image:
    a = img.getchannel("A").point(lambda v: 255 if v > 20 else 0)
    box = a.getbbox()
    if not box:
        return img
    l, t, r, b = box
    return img.crop((max(l - pad_px, 0), max(t - pad_px, 0), min(r + pad_px, img.width), min(b + pad_px, img.height)))


# ------------------------------------------------------------------ scoring
def measure(img: Image.Image, source_kind: str, knocked: bool, src_size: tuple[int, int],
            scale: float = 1.0) -> LogoMetrics:
    a = np.asarray(img.getchannel("A"))
    h, w = a.shape
    cov = float((a > 127).mean()) if a.size else 0.0
    aspect = w / h if h else 0.0
    problems = []
    if w < 8 or h < 8 or cov == 0:
        problems.append("blank_after_whitening")
    if cov > 0.90:
        problems.append("solid_block (background not removed)")
    if 0 < cov < 0.03:
        problems.append("too_faint")
    if source_kind != "svg" and src_size[1] < 40:
        problems.append("source_too_small (<40px tall)")
    if aspect > 12:
        problems.append("aspect_extreme (>12:1, probably a banner or tagline strip)")
    native = (None, None) if source_kind in ("svg", "typeset") else (round(w / scale), round(h / scale))
    return LogoMetrics(w, h, round(aspect, 3), round(cov, 3), source_kind, knocked, not problems, problems, *native)


def process_logo(data: bytes, designed_for_dark: bool = False) -> tuple[Image.Image, LogoMetrics]:
    """bytes of any logo file -> (white trimmed RGBA, metrics). Raises nothing for bad input:
    returns metrics.ok=False with problems so the caller can try the next source.
    Pass designed_for_dark=True for logos made for dark backgrounds (Brandfetch theme == "light")."""
    try:
        img, kind = load_rgba(data)
    except Exception as e:  # noqa: BLE001 - any decoder failure means "try next source"
        blank = Image.new("RGBA", (1, 1))
        return blank, LogoMetrics(1, 1, 1.0, 0.0, "unknown", False, False, [f"decode_failed: {e}"])
    src_size = img.size
    scale = 1.0
    if kind != "svg" and img.height < TARGET_RASTER_H:
        scale = TARGET_RASTER_H / img.height
        img = img.resize((max(1, round(img.width * scale)), TARGET_RASTER_H), Image.LANCZOS)
    white, knocked = whiten(img, kind, designed_for_dark)
    white = trim(white)
    return white, measure(white, kind, knocked, src_size, scale)


# ------------------------------------------------------------------ taglines
def ink_bands(img: Image.Image, alpha_min: int = 40) -> list[tuple[int, int]]:
    """Horizontal bands of ink (row projection of the alpha channel), top to bottom: [(y0, y1), ...].
    Gaps under BAND_GAP of the height (anti-aliasing, a dot over an i) do not split a band."""
    a = np.asarray(img.convert("RGBA").getchannel("A")) > alpha_min
    rows = a.any(axis=1)
    bands: list[list[int]] = []
    for y, ink in enumerate(rows):
        if ink and (not bands or bands[-1][1] != y):
            if bands and y - bands[-1][1] < max(1, BAND_GAP * len(rows)):
                bands[-1][1] = y + 1
                continue
            bands.append([y, y + 1])
        elif ink:
            bands[-1][1] = y + 1
    return [(a0, a1) for a0, a1 in bands]


def tagline_band(img: Image.Image) -> dict | None:
    """The lowest ink band, if it is a separate line under TAGLINE_MAX_SHARE of the logo's ink height:
    {share, bands, croppable}. Croppable only with exactly 2 bands (mark + tagline)."""
    bands = ink_bands(img)
    if len(bands) < 2:
        return None
    total = bands[-1][1] - bands[0][0]
    low = bands[-1]
    share = (low[1] - low[0]) / total if total else 0.0
    if share >= TAGLINE_MAX_SHARE:
        return None
    return {"share": round(share, 4), "bands": bands, "croppable": len(bands) == 2}


def tagline_pt(img: Image.Image, printed_height_in: float) -> float | None:
    """Printed height in points of a tagline line when the logo prints `printed_height_in` tall, else None."""
    tb = tagline_band(img)
    return round(tb["share"] * printed_height_in * 72, 2) if tb else None


def crop_tagline(img: Image.Image) -> Image.Image | None:
    """The logo without its tagline band (re-trimmed), or None when that is not safe (no tagline, bands touch,
    or more than 2 bands)."""
    tb = tagline_band(img)
    if not tb or not tb["croppable"]:
        return None
    top = tb["bands"][0]
    return trim(img.crop((0, top[0], img.width, top[1])))


# ------------------------------------------------------------------ fallback
def _render_text(text: str, font: ImageFont.FreeTypeFont, spacing: int) -> Image.Image:
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    l, t, r, b = (int(round(v)) for v in probe.multiline_textbbox((0, 0), text, font=font, align="center",
                                                                   spacing=spacing))
    img = Image.new("RGBA", (r - l + 20, b - t + 20), (0, 0, 0, 0))
    ImageDraw.Draw(img).multiline_text((10 - l, 10 - t), text, font=font, fill=(255, 255, 255, 255),
                                       align="center", spacing=spacing)
    return trim(img)


def typeset_wordmark(name: str, px: int = 240, max_aspect: float = TYPESET_MAX_ASPECT) -> tuple[Image.Image, LogoMetrics]:
    """Company name set in Manrope Bold, white on transparency. Always succeeds.
    If the one-line wordmark is wider than `max_aspect`:1 (it would print < 0.12in tall in a 1in map slot),
    it is split onto two centred lines at the most balanced space (metrics.lines == 2). Callers pass the short
    brand name (names.brand_short_name), never a bracketed full name."""
    font = ImageFont.truetype(str(FONTS["wordmark"]), px, layout_engine=ImageFont.Layout.BASIC)
    img = _render_text(name, font, px // 6)
    words = name.split()
    if img.width / img.height > max_aspect and len(words) > 1:
        splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
        a, z = min(splits, key=lambda ab: abs(font.getlength(ab[0]) - font.getlength(ab[1])))
        img = _render_text(f"{a}\n{z}", font, px // 6)
        lines = 2
    else:
        lines = 1
    m = measure(img, "typeset", False, img.size)
    m.ok, m.problems, m.lines = True, [], lines
    return img, m


def slug(name: str) -> str:
    """ASCII slug for file names: 'Café & Müller' -> 'cafe-muller'. Never empty."""
    import hashlib
    import unicodedata
    ascii_ = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    return s or "co-" + hashlib.sha1(name.encode()).hexdigest()[:8]


def file_stem(display_name: str) -> str:
    """Deliverable name keeps the brand's casing: 'Recur_x_Ai2000'."""
    import unicodedata
    ascii_ = unicodedata.normalize("NFKD", display_name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", ascii_).strip("-")
    return f"Recur_x_{s or slug(display_name)}"


def save(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return path
