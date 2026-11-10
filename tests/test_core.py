"""Regression tests for the deterministic core. Run: pytest -q
These pin down facts that were verified against Recur's reference deck; if one fails, something about
the template, fonts or geometry changed and decks will look wrong."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from recur_deck import fonts, qa
from recur_deck.logos import process_logo, typeset_wordmark
from recur_deck.models import DeckContent
from recur_deck.offline import run
from recur_deck.spec import TEMPLATE_PATH

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"


# ---------------------------------------------------------------- fonts match the reference PDF
@pytest.mark.parametrize("text,font,pt,pdf_width_in", [
    ("What we see in Ai2000", "light", 20, 2.842),
    ("Apparel brands are trading legacy ERPs for systems with AI", "light", 15, 5.551),
    ("Many run generic ERPs or ops tools that keep financials in another system", "light", 11, 5.114),
])
def test_font_widths_match_reference_pdf(text, font, pt, pdf_width_in):
    assert fonts.text_width_in(text, font, pt) == pytest.approx(pdf_width_in, rel=0.01)


def test_our_take_reference_wraps_like_the_deck():
    lines = fonts.wrap("Ai2000 runs product, retailer orders and financials for big brands in one system", "bold", 10.5, 1.86)
    assert len(lines) == 4
    h, counts = qa.our_take_height(
        "Ai2000 runs product, retailer orders and financials for big brands in one system",
        ["Small-brand tools lack the depth big retailer programs need", "Ops-only suites push the books into a second system"])
    assert counts == [4, 2, 2] and h < 1.95


# ---------------------------------------------------------------- logo pipeline
@pytest.mark.parametrize("path,ok", [
    ("ai2000/logos/ai2000/1_brandfetch.png", True),      # colour on transparency
    ("ai2000/logos/bluecherry/1_site.jpg", True),        # colour on white JPG
    ("ai2000/logos/fdm4/1_brandfetch.png", True),        # already light
    ("ai2000/logos/aims360/1_site.png", True),           # badge with knocked-out text
    ("ai2000/logos/apparelmagic/1_photo.jpg", False),    # photo, not a logo
    ("pcrecruiter/logos/bullhorn/1_favicon.png", False), # 32px favicon
    ("pcrecruiter/logos/jobadder/1_brandfetch.svg", True),  # SVG with white background rect
])
def test_logo_acceptance(path, ok):
    _, m = process_logo((FIX / path).read_bytes())
    assert m.ok is ok, m.problems


def test_badge_knockout_is_not_a_solid_block():
    _, m = process_logo((FIX / "ai2000/logos/aims360/1_site.png").read_bytes())
    assert m.ink_coverage < 0.6


def test_typeset_always_succeeds():
    img, m = typeset_wordmark("Some Very Long Company Name Inc")
    assert m.ok and img.width > img.height


# ---------------------------------------------------------------- end to end (offline, no render)
@pytest.mark.parametrize("case", ["ai2000", "pcrecruiter"])
def test_good_cases_have_no_errors(case, tmp_path):
    rep = run(FIX / case, tmp_path / case, render_pngs=False)
    errors = [i for i in rep["issues"] if i["severity"] == "error"]
    assert errors == []


def test_stress_case_fails_in_the_expected_ways(tmp_path):
    rep = run(FIX / "stress", tmp_path / "stress", render_pngs=False)
    codes = {i["code"] for i in rep["issues"]}
    assert {"text_overflow", "style_lint", "our_take_lines", "our_take_height"} <= codes
    assert any(i["field"] == "market.y_axis.high_label" and i["code"] == "text_overflow" for i in rep["issues"])
    assert rep["logos"]["Lighthouse Veterinary Practice Cloud"]["source"] == "typeset"


def test_fixed_slides_untouched_and_orphans_removed(tmp_path):
    rep = run(FIX / "ai2000", tmp_path / "a", render_pngs=False)
    out = Path(rep["pptx"])
    content = DeckContent.model_validate_json((FIX / "ai2000/content.json").read_text())
    assert qa.check_integrity(out, content) == []
    with zipfile.ZipFile(TEMPLATE_PATH) as zt, zipfile.ZipFile(out) as zo:
        for n in range(4, 11):
            assert zt.read(f"ppt/slides/slide{n}.xml") == zo.read(f"ppt/slides/slide{n}.xml")


def test_template_leak_detected(tmp_path):
    """Building PCRecruiter must not leave any Ai2000 / apparel text behind."""
    rep = run(FIX / "pcrecruiter", tmp_path / "p", render_pngs=False)
    assert not [i for i in rep["issues"] if i["code"] == "template_leak"]
