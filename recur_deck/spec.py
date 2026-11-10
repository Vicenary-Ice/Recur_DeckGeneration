"""Single source of truth for the Recur template: shape identities, geometry and text budgets.

Every number here was measured from the original Recur_x_Ai2000.pptx (16:9, 10in x 5.625in).
Units are inches unless a name says otherwise. 1 inch = 914400 EMU.
Shapes are addressed by their cNvPr id (shape_id) because several shapes share names.
"""
from __future__ import annotations

from pathlib import Path

EMU_PER_IN = 914400
SLIDE_W_IN = 10.0
SLIDE_H_IN = 5.625

KIT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = KIT_ROOT / "assets" / "recur_template.pptx"
FONT_DIR = KIT_ROOT / "assets" / "fonts"

# Font files that reproduce the template's typefaces (verified: measured widths match the
# reference PDF within 0.2% on the reference lines; tests allow 1%).
FONTS = {
    "light": FONT_DIR / "NotoSansArabic-Light.ttf",      # typeface "Noto Sans Arabic Light"
    "regular": FONT_DIR / "NotoSansArabic-Regular.ttf",  # typeface "Noto Sans Arabic"
    "bold": FONT_DIR / "NotoSansArabic-Bold.ttf",        # typeface "Noto Sans Arabic", b=1
    "wordmark": FONT_DIR / "Manrope-Bold.ttf",           # used only for typeset logo fallbacks
}
ALLOWED_TYPEFACES = {"Noto Sans Arabic", "Noto Sans Arabic Light", "Arial", "Roboto", "Manrope", "Manrope Light"}

# Slide indexes are 0-based in python-pptx.
COVER, THESIS, MARKET = 0, 1, 2
FIXED_SLIDES = range(3, 10)  # slides 4..10 must be byte-identical to the template

# ---------------------------------------------------------------- slide 1: cover
COVER_SHAPES = {
    "old_logo": 10,          # "Picture 9" - Ai2000 logo with a PowerPoint brightness effect -> replaced
    "divider": 4,            # vertical white line, x=4.931, y=2.510..3.115
    "recur_wordmark": 72,    # fixed vector wordmark, x=2.441..4.541
}
COVER_LOGO = {
    "left": 5.318,           # logo is left-aligned here (0.387in right of the divider)
    "center_y": 2.8125,      # vertical centre of the divider == slide centre
    "max_w": 2.10,           # mirrors the Recur wordmark's distance from the divider
    "max_h": 0.60,
    "min_h": 0.25,
    "target_area": 0.80,     # sq in; the Ai2000 reference logo is 1.327 x 0.605
    "typeset_h": 0.40,       # typeset fallback text height (cap+descender box), ~ Recur wordmark weight
}

# ---------------------------------------------------------------- slide 2: thesis
THESIS_SHAPES = {
    "title": 14,             # "What we see in {Company}" 20pt Light, white
    "points": [4, 2, 9],     # TextBox 3 / TextBox 1 / TextBox 8: p0 headline 15pt white, p1 subline 11pt #B8C4D6
}
# Lead-in labels "WHY WE'RE HERE / WHY WE'RE EXCITED / HOW WE CAN HELP" and numerals 01-03 are vector
# outlines (shape ids 21-26). They never change. The three points must match those roles.
THESIS_LABELS = ["WHY WE'RE HERE", "WHY WE'RE EXCITED", "HOW WE CAN HELP"]

# ---------------------------------------------------------------- slide 3: market map
MARKET_SHAPES = {
    "title": 12,             # "The opportunity for {Company}" 20pt Light
    "subtitle": 238,         # one line, 12pt Light, #6FA8DC
    "y_top_label": 240,      # e.g. "Large brands"   12pt bold, right-aligned, wrap none
    "y_bottom_label": 242,   # e.g. "Small &\nmid brands" (2 paragraphs allowed)
    "x_left_label": 245,     # e.g. "Ops only"        12pt bold, centred
    "x_right_label": 7,      # e.g. "Ops + financials"
    "y_title": 2,            # e.g. "Customer size"   8pt
    "x_title": 3,            # e.g. "Product scope"   8pt
    "our_take": 256,         # p0 "OUR TAKE" (fixed), p1 headline 10.5pt bold, p2/p3 points 9pt Light
    "glow": 1043,            # radial-gradient ellipse 1.247 x 0.815 centred on the target logo
    "old_logos": [1041, 1042, 1044, 1045, 1046, 1047, 1048, 1049],  # removed and re-added
}
PLOT = {"x0": 2.115, "x1": 7.200, "y0": 1.995, "y1": 4.704}  # axis lines
GLOW_SIZE = (1.247, 0.815)
# Logo cells. key = (x, y) with x in {"left","right"}, y in {"bottom","top"}.
CELLS = {
    ("left", "top"): {"cx": 3.45, "cy": 2.62, "x0": 2.30, "x1": 4.60, "y0": 2.10, "y1": 3.15},
    ("right", "top"): {"cx": 6.05, "cy": 2.62, "x0": 4.85, "x1": 7.25, "y0": 2.10, "y1": 3.15},
    ("left", "bottom"): {"cx": 3.45, "cy": 4.02, "x0": 2.30, "x1": 4.60, "y0": 3.40, "y1": 4.62},
    ("right", "bottom"): {"cx": 6.05, "cy": 4.02, "x0": 4.85, "x1": 7.25, "y0": 3.40, "y1": 4.62},
}
TARGET_CELL = ("right", "top")
SLOT_DX, SLOT_DY = 0.56, 0.29
MAX_PER_CELL = 3            # including the target in its cell
MIN_COMPETITORS, MAX_COMPETITORS = 5, 8
MIN_LOGO_GAP = 0.08
MIN_LOGO_H = 0.11           # below this a map logo is unreadable in print
MAP_LOGO = {"target_area": 0.17, "max_w": 1.00, "max_h": 0.30, "min_h": 0.13,
            "typeset_h": 0.13}  # typeset fallback height on the map (0.17 read heavier than real logos)
MAP_TYPESET_MAX_H = 0.17    # ceiling when the QA loop enlarges a typeset logo ("logo_too_small")
TYPESET_SHRINK, TYPESET_GROW = 0.8, 1.15  # QA-loop resize factors for typeset map logos

# ---------------------------------------------------------------- text budgets
# (shape role) -> font key, size pt, max single-line width in inches. Lines never wrap in these boxes.
LINE_BUDGETS = {
    "thesis_title": ("light", 20.0, 8.30),
    "thesis_headline": ("light", 15.0, 6.20),
    "thesis_subline": ("light", 11.0, 6.20),
    "market_title": ("light", 20.0, 8.30),
    "market_subtitle": ("light", 12.0, 8.50),
    "y_label_line": ("bold", 12.0, 1.43),      # per line, max 2 lines
    "x_label": ("bold", 12.0, 1.80),
    "axis_title": ("regular", 8.0, 1.40),
}
# Soft targets the writer should aim for (the reference deck sits here).
LINE_TARGETS = {"thesis_headline": 5.70, "thesis_subline": 5.60, "market_subtitle": 8.0}
OUR_TAKE = {
    "width": 1.86, "max_height": 1.95,   # reference block measures 1.83in with this model
    "label": ("bold", 8.0, 1.00, 8.0),         # font, pt, line spacing factor, space after pt
    "headline": ("bold", 10.5, 1.00, 8.0),
    "point": ("light", 9.0, 1.15, 6.0),
    "headline_max_lines": 4, "point_max_lines": 2,
}

# Strings that only belong to the reference deck. If any appear in slides 1-3 for another company,
# the template leaked.
TEMPLATE_LEAK_TOKENS = ["Ai2000", "apparel", "Apparel", "BlueCherry", "Aptean", "NetSuite", "FDM4",
                        "AIMS360", "ApparelMagic", "Uphance", "retailer", "Ops only", "Ops + financials"]


def emu(inches: float) -> int:
    return int(round(inches * EMU_PER_IN))


def inches(e: int) -> float:
    return e / EMU_PER_IN
