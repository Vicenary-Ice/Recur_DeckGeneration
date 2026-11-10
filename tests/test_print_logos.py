"""Agent 4: print-grade logos. Native resolution, dpi after layout, tagline micro-text, SVG-first ranking, @2x
siblings and short typeset names. Fixture files + a tiny simulated site; no network."""
from __future__ import annotations

import io
import json

import httpx
import pytest
from PIL import Image, ImageDraw

from recur_deck import logos as L
from recur_deck.checks import logo_print as LP
from recur_deck.config import Settings
from recur_deck.names import brand_short_name
from recur_deck.stages import logo_sources as S
from recur_deck.stages.base import Ctx
from tests.fake_world import FakeClaude, World
from tests.test_known_defects import UPHANCE, manifest


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def wordmark(w: int, h: int, tagline: bool = False, pad: int = 0) -> Image.Image:
    """Dark letter-like strokes on transparency (a 'name' row from top to bottom, or the top 62% when there is a
    tagline), optionally a thin tagline row well below it; `pad` px of margin all round, like a real logo file."""
    im = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    name_h = int(h * 0.62) if tagline else h - 1
    bw = max(w // 24, 1)
    for x in range(bw, w - bw, max(w // 8, 2)):
        d.rectangle([pad + x, pad, pad + x + bw, pad + name_h], fill=(20, 30, 60, 255))
    if tagline:
        d.rectangle([pad + int(w * 0.2), pad + int(h * 0.86), pad + int(w * 0.8), pad + h - 1], fill=(200, 40, 120, 255))
    return im


# ---------------------------------------------------------------- A. native resolution
def test_native_px_recorded():
    img, met = L.process_logo(png_bytes(wordmark(400, 120)))
    assert img.height == 600 and met.height_px == 600            # enlarged for whitening ...
    assert met.native_h == 120 and met.native_w == round(met.width_px / 5)   # ... but the source resolution is kept
    assert met.native_w < 400                                     # (the ink's width: the margins are trimmed)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="100"><rect x="0" y="0" width="300" height="100" fill="#123"/>' \
          b'<rect x="10" y="10" width="40" height="80" fill="#fff"/></svg>'
    assert L.process_logo(svg)[1].native_w is None               # vector: no pixel limit


# ---------------------------------------------------------------- B. dpi after layout
def test_print_dpi_rules(monkeypatch):
    man = {"elements": [{"slide": 1, "id": "cover_logo", "kind": "logo", "label": "T", "w": 1.32, "h": 0.6},
                        {"slide": 3, "id": "logo:A", "kind": "logo", "label": "A", "w": 0.9, "h": 0.2},
                        {"slide": 3, "id": "logo:B", "kind": "logo", "label": "B", "w": 0.9, "h": 0.2},
                        {"slide": 3, "id": "logo:C", "kind": "logo", "label": "C", "w": 0.9, "h": 0.2}]}
    metrics = {"T": {"native_w": 338}, "A": {"native_w": 150}, "B": {"native_w": None}, "C": {"typeset": True, "native_w": 10}}
    issues = {i.field: i for i in LP.print_dpi_issues(man, metrics)}
    assert set(issues) == {"logo:T", "logo:A"}                   # vector and typeset logos are exempt
    assert issues["logo:A"].severity == "error" and issues["logo:A"].measured == 167 and issues["logo:A"].allowed == 200
    assert issues["logo:T"].severity == "warn" and issues["logo:T"].slide == 1   # 256 dpi target cover: kept
    metrics["T"]["native_w"] = 219
    assert {i.field: i.severity for i in LP.print_dpi_issues(man, metrics)}["logo:T"] == "error"   # 166 dpi
    monkeypatch.setenv("RECUR_TARGET_COVER_MIN_DPI", "300")
    metrics["T"]["native_w"] = 338
    assert {i.field: i.severity for i in LP.print_dpi_issues(man, metrics)}["logo:T"] == "error"
    assert all(i.fix == "swap_logo" and i.code == "logo_low_res" for i in issues.values())


# ---------------------------------------------------------------- C. tagline micro-text
def test_crop_tagline():
    img = Image.open(UPHANCE / "logos" / "aims360.png")
    tb = L.tagline_band(img)
    assert tb["croppable"] and tb["share"] < L.TAGLINE_MAX_SHARE
    cropped = L.crop_tagline(img)
    assert len(L.ink_bands(cropped)) == 1 and L.tagline_band(cropped) is None   # no lower band left
    assert cropped.width / cropped.height == pytest.approx(2341 / 414, rel=0.01)  # 4.37:1 -> 5.65:1
    assert img.width / img.height == pytest.approx(4.367, rel=0.01)
    assert S.tagline_issue(cropped, 0.2) is None and S.tagline_issue(img, 0.6) is None   # 1.7pt on the map, 5.3pt big
    bc = Image.open(UPHANCE / "logos" / "bluecherry.png")       # "by CGS" is 24% of the height: part of the mark
    assert L.tagline_band(bc) is None


def test_three_bands_are_not_cropped():
    im = wordmark(600, 200, tagline=True)
    ImageDraw.Draw(im).rectangle([100, 145, 499, 150], fill=(255, 255, 255, 255))  # a third thin band
    assert L.crop_tagline(im) is None
    issue = S.tagline_issue(im, 0.2)
    assert issue is not None and "use another logo" in issue.message


# ---------------------------------------------------------------- D. short names
@pytest.mark.parametrize("name,short", [
    ("Sync (iSync Solutions)", "Sync"), ("Aptean Apparel ERP", "Aptean"), ("InStyle Apparel Software", "InStyle"),
    ("Recruit CRM", "Recruit CRM"), ("AIMS360", "AIMS360"), ("Bravo Store Systems", "Bravo Store"),
    ("GCS Software, LLC", "GCS"), ("Acme Inc.", "Acme"), ("Foo Technologies Corporation", "Foo"),
    ("Apparel Software", "Apparel Software"), ("NetSuite", "NetSuite"), ("Ai2000", "Ai2000"),
    ("Zoho Recruit", "Zoho Recruit"), ("Cin7 Core", "Cin7 Core"), ("ERP Software Ltd", "ERP Software Ltd")])
def test_short_names(name, short):
    assert brand_short_name(name) == short


def test_short_name_aliases_and_typeset(tmp_path):
    assert brand_short_name("Oracle NetSuite", ["NetSuite"]) == "Oracle NetSuite"   # alias shares no first word
    assert brand_short_name("Uphance Apparel ERP", ["Uphance"]) == "Uphance"
    ts = S.typeset("Sync (iSync Solutions)", tmp_path)
    assert ts["typeset_name"] == "Sync" and ts["lines"] == 1 and "typeset_h" not in ts
    long = S.typeset("Lighthouse Veterinary Practice Cloud", tmp_path)
    assert long["lines"] == 2 and long["typeset_h"] >= 2 * L.TYPESET_MIN_H     # each line still >= 0.12in
    one_line_max = L.typeset_wordmark("NetSuite")[1].aspect
    assert one_line_max <= L.TYPESET_MAX_ASPECT and L.MAP_LOGO["max_w"] / L.TYPESET_MAX_ASPECT >= L.TYPESET_MIN_H


# ---------------------------------------------------------------- ranking and sources (a tiny site)
SVG = ('<svg viewBox="0 0 300 100" width="300" height="100" class="site-logo" aria-label="Svgco logo">'
       + "".join(f'<rect x="{x}" y="10" width="30" height="80" fill="#1a2b4c"/>' for x in range(0, 270, 45))
       + "</svg>")


class Site:
    """svgco.com: a JSON-LD PNG logo (first in source order) and an inline header SVG; optional @2x sibling."""

    def __init__(self, png: bytes, svg: bool = True, hires: bytes | None = None):
        self.png, self.svg, self.hires = png, svg, hires

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/img/logo.png":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=self.png)
        if req.url.path == "/img/logo@2x.png" and self.hires:
            return httpx.Response(200, headers={"content-type": "image/png"}, content=self.hires)
        if req.url.path in ("/", ""):
            ld = json.dumps({"@type": "Organization", "logo": "https://svgco.com/img/logo.png"})
            html = (f'<html><head><title>Svgco</title><script type="application/ld+json">{ld}</script></head>'
                    f'<body><header>{SVG if self.svg else ""}</header><main><p>Svgco software. ' + "x " * 300
                    + '</p></main></body></html>')
            return httpx.Response(200, headers={"content-type": "text/html"}, content=html.encode())
        return httpx.Response(404, text="nope")


def resolve(tmp_path, site: Site, claude=None, is_target=False):
    s = Settings(runs_dir=tmp_path, anthropic_api_key="k")
    claude = claude or FakeClaude(World())
    ctx = Ctx.create(s, tmp_path / "r", transport=httpx.MockTransport(site.handler), claude_client=claude)
    try:
        return S.resolve_company(ctx, "Svgco", "svgco.com", [], None, ctx.run_dir / "logos", is_target=is_target), claude
    finally:
        ctx.http.close()


def test_svg_preferred(tmp_path):
    out, _ = resolve(tmp_path, Site(png_bytes(wordmark(1200, 400))))
    assert [t["source"] for t in out["tried"][:2]] == ["jsonld", "header_svg"]     # PNG first in source order
    assert out["source"] == "header_svg" and out["native_w"] is None               # ... but the SVG wins
    assert out["alternatives"][0]["source"] == "jsonld"


def test_highest_native_resolution_and_hires_sibling(tmp_path):
    small, big = wordmark(240, 80), wordmark(480, 160)
    low, _ = resolve(tmp_path / "a", Site(png_bytes(small), svg=False), is_target=True)
    assert low["source"] == "jsonld" and low["native_w"] < 240                     # no sibling: stays low-res
    hi, _ = resolve(tmp_path / "b", Site(png_bytes(small), svg=False, hires=png_bytes(big)), is_target=True)
    assert hi["native_w"] == pytest.approx(2 * low["native_w"], abs=2)             # @2x sibling used


def test_tagline_crop_variant_wins(tmp_path):
    def logo_check(user):
        cands = json.loads(user.split("<candidates>\n", 1)[1].split("\n</candidates>", 1)[0])
        return {"verdicts": [{"candidate_id": c["candidate_id"], "is_logo": True, "text_read": "Svgco",
                              "matches_company": True, "kind": "lockup", "legible_on_navy": True, "confidence": 0.9,
                              "tagline_present": not c["candidate_id"].endswith("t")} for c in cands]}
    claude = FakeClaude(World(), {"logo_check": logo_check})
    out, claude = resolve(tmp_path, Site(png_bytes(wordmark(1200, 300, tagline=True, pad=24)), svg=False), claude)
    assert claude.calls == ["logo_check"]                                         # the crop rode in the same call
    assert out["cropped_tagline"] and out["chosen_id"] == "c1t"
    assert L.tagline_band(Image.open(out["png"])) is None
    assert out["alternatives"][0]["id"] == "c1"                                   # the full lockup kept as a fallback


def test_crop_logo_fix_checks_the_crop(tmp_path):
    src = L.save(L.process_logo(png_bytes(wordmark(1200, 300, tagline=True, pad=24)))[0], tmp_path / "x.png")
    lg = {"png": str(src), "source": "jsonld", "typeset": False, "native_w": 1200, "native_h": 300, "aspect": 4.0}
    s = Settings(runs_dir=tmp_path, anthropic_api_key="k")
    reads = {"text": "Svgco"}

    def logo_check(user):
        return {"verdicts": [{"candidate_id": "crop", "is_logo": True, "text_read": reads["text"], "matches_company": True,
                              "kind": "wordmark", "legible_on_navy": True, "confidence": 0.9}]}
    ctx = Ctx.create(s, tmp_path / "q", claude_client=FakeClaude(World(), {"logo_check": logo_check}))
    reads["text"] = "Tagline Words"
    bad = S.crop_logo(ctx, "Svgco", dict(lg), tmp_path)
    assert bad["applied"] is False and "not read as the logo" in bad["why"]
    reads["text"] = "Svgco"
    s2 = Settings(runs_dir=tmp_path / "fresh-cache", anthropic_api_key="k")   # the first answer is cached per image
    ctx2 = Ctx.create(s2, tmp_path / "q2", claude_client=FakeClaude(World(), {"logo_check": logo_check}))
    ok_lg = dict(lg)
    res = S.crop_logo(ctx2, "Svgco", ok_lg, tmp_path)
    assert res["applied"] and ok_lg["cropped_tagline"] and ok_lg["aspect"] > lg["aspect"]
    assert ok_lg["native_h"] < 300 and S.crop_logo(ctx2, "Svgco", ok_lg, tmp_path)["applied"] is False


def test_layer_a_logo_print_check_on_the_uphance_run():
    """D10 + D11 through the registered check, resolving the run's png paths inside the fixture folder."""
    lm = json.loads((UPHANCE / "logo_metrics.json").read_text())
    issues = LP.logo_print_checks(None, manifest(UPHANCE), {}, {"logos": lm, "run_dir": UPHANCE})
    by = {(i.code, i.field): i for i in issues}
    assert ("logo_low_res", "logo:Uphance") in by and by[("logo_low_res", "logo:Uphance")].severity == "error"
    assert ("logo_tagline_microtext", "logo:AIMS360") in by


def test_corner_specks_are_not_bands():
    """A lockup on a rounded box: whitening leaves the box's corners as specks above and below the mark. They are not
    lines of text, so the real tagline ("CONSUMER BRANDS ERP") is still found and can be cropped."""
    from tests.fake_world import FIX
    img, _ = L.process_logo((FIX / "ai2000/logos/aims360/1_site.png").read_bytes())
    tb = L.tagline_band(img)
    assert tb is not None and tb["croppable"] and len(tb["bands"]) == 2
    assert L.tagline_band(L.crop_tagline(img)) is None
