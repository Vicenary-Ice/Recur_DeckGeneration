"""Exact text measurement with the template's real fonts (Pillow + fontTools).

Uses Pillow's BASIC layout engine on purpose: it ignores kerning, so widths are identical on every machine
(macOS Pillow wheels may lack raqm) and are slightly conservative (never narrower than PowerPoint).
Verified against the reference PDF: within 0.2% on the reference lines.
"""
from __future__ import annotations

from functools import lru_cache

from fontTools.ttLib import TTFont
from PIL import ImageFont

from .spec import FONTS

_REF_SIZE = 1000  # measure at 1000px and scale; avoids hinting/rounding error

# PowerPoint "single" spacing = 1.2 x point size. Measured on the LibreOffice render of the reference
# deck: 10.5pt lines sit 12.6pt apart (1.20x); 9pt @115% sit 12.6pt apart (1.22x). Do NOT use the font's
# hhea metrics: Noto Sans Arabic carries tall Arabic metrics (2.11x) that PowerPoint ignores here.
PPT_SINGLE_LINE = 1.2


@lru_cache(maxsize=None)
def _pil(font_key: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS[font_key]), _REF_SIZE, layout_engine=ImageFont.Layout.BASIC)


def text_width_in(text: str, font_key: str, pt: float, letter_spacing_pt: float = 0.0) -> float:
    """Advance width of a single line in inches. Multi-line input is measured line by line (widest wins)."""
    lines = text.split("\n")
    w = max(_pil(font_key).getlength(ln) for ln in lines) / _REF_SIZE * pt / 72.0
    if letter_spacing_pt:
        w += letter_spacing_pt * max(max(len(ln) for ln in lines) - 1, 0) / 72.0
    return w


def line_height_in(font_key: str, pt: float, spacing: float = 1.0) -> float:
    return PPT_SINGLE_LINE * pt * spacing / 72.0


def wrap(text: str, font_key: str, pt: float, max_w_in: float) -> list[str]:
    """Greedy word wrap like PowerPoint's (breaks on spaces only)."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if not cur or text_width_in(trial, font_key, pt) <= max_w_in:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def missing_glyphs(text: str, font_key: str) -> set[str]:
    cmap = _cmap(font_key)
    return {ch for ch in text if not ch.isspace() and ord(ch) not in cmap}


@lru_cache(maxsize=None)
def _cmap(font_key: str) -> dict:
    return TTFont(str(FONTS[font_key])).getBestCmap()
