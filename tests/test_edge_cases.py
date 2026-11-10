"""Regression tests for edge cases found in the independent review (2026-11-07)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from recur_deck import qa
from recur_deck.logos import file_stem, process_logo, slug, typeset_wordmark
from recur_deck.models import DeckContent
from recur_deck.offline import run

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def _content(**patch) -> dict:
    d = json.loads((FIX / "ai2000/content.json").read_text())
    for path, val in patch.items():
        node = d
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[int(k)] if k.isdigit() else node[k]
        node[int(keys[-1]) if keys[-1].isdigit() else keys[-1]] = val
    return d


# ---------------------------------------------------------------- A2: names are not style violations
@pytest.mark.parametrize("brand", ["Elevate", "Streamline Health", "Yahoo!", "Acme Co.", "Landscape Pro"])
def test_company_names_are_exempt_from_style_lint(brand):
    d = _content(**{"company.display_name": brand, "company.domain": "brand.example",
                    "market.our_take.headline": f"{brand} runs orders and financials for large brands in one system"})
    c = DeckContent.model_validate(d)
    lint = [i for i in qa.check_text(c) if i.code == "style_lint"]
    assert lint == [], lint


def test_real_style_violation_still_caught_next_to_a_protected_name():
    d = _content(**{"company.display_name": "Elevate", "company.domain": "elevate.example",
                    "thesis.1.subline": "Elevate helps brands leverage one platform for orders and books"})
    c = DeckContent.model_validate(d)
    assert any(i.field == "thesis[1].subline" and i.code == "style_lint" for i in qa.check_text(c))


# ---------------------------------------------------------------- A3: newlines never crash
def test_newline_in_x_label_is_rejected_by_schema():
    with pytest.raises(Exception):
        DeckContent.model_validate(_content(**{"market.x_axis.high_label": "Ops +\nfinancials"}))


def test_newline_in_line_check_is_an_issue_not_a_crash():
    issues: list = []
    qa._line(issues, 3, "market.x_axis.high_label", "Ops +\nfinancials", "x_label")
    assert any(i.code == "newline" for i in issues)


# ---------------------------------------------------------------- B5: single-field check for the rewrite loop
def test_check_field_picks_fitting_candidate():
    c = DeckContent.model_validate(_content())
    long = "Apparel and footwear brands everywhere are replacing their aging legacy ERP systems with AI"
    assert qa.check_field("thesis[0].headline", long, c)
    assert qa.check_field("thesis[0].headline", "Apparel brands are moving off legacy ERPs", c) == []
    assert qa.check_field("market.our_take.points[1]", "Ops-only suites push the books elsewhere", c) == []
    assert qa.check_field("market.subtitle", "Brands want one system\nfor everything", c)  # newline -> error


# ---------------------------------------------------------------- B1: logos designed for dark backgrounds
def _white_wordmark_with_orange_dot() -> bytes:
    im = Image.new("RGBA", (900, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([10, 10, 190, 190], fill=(240, 120, 20, 255))           # big coloured symbol
    d.rectangle([230, 85, 700, 115], fill=(255, 255, 255, 255))        # thin white "wordmark" (< half the ink)
    import io
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def test_designed_for_dark_keeps_white_wordmark():
    data = _white_wordmark_with_orange_dot()
    kept, _ = process_logo(data, designed_for_dark=True)
    erased, _ = process_logo(data, designed_for_dark=False)
    assert kept.width > 3 * kept.height          # dot + wordmark survive
    assert erased.width < 1.5 * erased.height    # default treats white as knockout (only the dot remains)


# ---------------------------------------------------------------- B2: SVG detection
@pytest.mark.parametrize("prefix", ["﻿", "<!-- made by hand -->\n", '<?xml version="1.0"?>\n<!DOCTYPE svg>\n'])
def test_svg_variants_are_recognised(prefix):
    svg = prefix + '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 60"><rect x="5" y="5" width="120" height="50" fill="#123"/><rect x="150" y="20" width="145" height="20" fill="#e45"/></svg>'
    _, m = process_logo(svg.encode("utf-8"))
    assert m.source_kind == "svg" and m.ok, m.problems


# ---------------------------------------------------------------- B3: collisions
def test_slug_collisions_and_target_as_competitor_rejected():
    with pytest.raises(Exception):
        DeckContent.model_validate(_content(**{"market.competitors.0.name": "Zoho-Recruit",
                                               "market.competitors.1.name": "Zoho Recruit"}))
    with pytest.raises(Exception):
        DeckContent.model_validate(_content(**{"market.competitors.0.name": "Ai2000"}))


# ---------------------------------------------------------------- B4: tiny logos
def test_long_typeset_names_wrap_to_two_lines():
    img, m = typeset_wordmark("Very Long Competitor Name Systems")
    assert m.aspect < 8.5


def test_tiny_logo_is_flagged():
    man = {"elements": [{"slide": 3, "id": "logo:X", "kind": "logo", "label": "X", "cell": ["left", "bottom"],
                         "x": 3.0, "y": 4.0, "w": 1.0, "h": 0.06}]}
    assert any(i.code == "logo_too_small" for i in qa.check_layout(man, {}))


# ---------------------------------------------------------------- B12: names and files
def test_unicode_slug_and_file_stem():
    assert slug("Café & Müller") == "cafe-muller"
    assert slug("株式会社") .startswith("co-")
    assert file_stem("Ai2000") == "Recur_x_Ai2000"


# ---------------------------------------------------------------- B6: text elements in the manifest
def test_manifest_lists_text_fields(tmp_path):
    rep = run(FIX / "ai2000", tmp_path / "a", render_pngs=False)
    man = json.loads(Path(rep["pptx"]).with_suffix(".manifest.json").read_text())
    fields = {e["field"] for e in man["elements"] if e["kind"] == "text"}
    assert {"thesis[0]", "thesis[2]", "market.subtitle", "market.our_take", "market.x_axis.high_label"} <= fields
    assert Path(rep["pptx"]).name == "Recur_x_Ai2000.pptx"


def test_three_line_y_label_rejected_by_schema():
    with pytest.raises(Exception):
        DeckContent.model_validate(_content(**{"market.y_axis.high_label": "Multi-site\nhospital\ngroups"}))
