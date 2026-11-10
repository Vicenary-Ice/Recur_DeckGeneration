"""M3-M6: pipeline stages end to end on a simulated web + fake Claude, the entity gate, the QA fix loop,
and offline replay. No network, no keys. Tests that render need LibreOffice and are skipped without it."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest

from recur_deck import pipeline, qa
from recur_deck.config import Settings
from recur_deck.models import DeckContent
from recur_deck.render import soffice_bin
from recur_deck.stages import vision_qa as s_vision
from recur_deck.stages.base import Ctx
from tests.fake_world import FIX, FakeClaude, World

try:
    soffice_bin()
    HAVE_LO = True
except RuntimeError:
    HAVE_LO = False
BASELINE = Path(__file__).resolve().parent.parent / "assets" / "baseline"
SECRETS = dict(anthropic_api_key="sk-ant-SECRET-123456", firecrawl_api_key="fc-SECRET-123456")


def settings(tmp_path: Path, **kw) -> Settings:
    return Settings(runs_dir=tmp_path / "runs", firecrawl_rpm=1000, **{**SECRETS, **kw})


def fake_render(monkeypatch):
    """Skip LibreOffice: 'render' the baseline PNGs, stamped with a hash of the deck so that a changed deck
    gives changed images (as a real render does)."""
    import hashlib
    import zipfile

    from PIL import Image

    def render_slides(pptx, run_dir, manifest):
        out = run_dir / "png"
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pptx) as z:
            h = hashlib.sha1(b"".join(z.read(n) for n in sorted(z.namelist()) if n.startswith("ppt/"))).digest()
        pngs = []
        for i in (1, 2, 3):
            im = Image.open(BASELINE / f"slide{i}.png").convert("RGB")
            im.putpixel((0, 0), tuple(h[:3]))
            im.save(out / f"slide{i}.png")
            pngs.append(out / f"slide{i}.png")
        pdf = run_dir / (pptx.stem + ".pdf")
        pdf.write_bytes(b"%PDF-1.4 fake")
        return pdf, pngs, pngs
    monkeypatch.setattr(s_vision, "render_slides", render_slides)


def run(tmp_path, world=None, claude=None, **kw):
    world = world or World()
    claude = claude or FakeClaude(world)
    res = pipeline.run(kw.pop("name", "Ai2000"), settings=settings(tmp_path), out_dir=tmp_path / "output",
                       transport=httpx.MockTransport(world.handler), claude_client=claude, non_interactive=True, **kw)
    return res, world, claude


# ---------------------------------------------------------------- end to end
@pytest.mark.skipif(not HAVE_LO, reason="LibreOffice not installed")
def test_end_to_end_simulated_ai2000(tmp_path):
    res, world, claude = run(tmp_path)
    rep = res.report
    assert res.exit_code in (0, 1), rep.get("error")
    rd = res.run_dir
    ent = json.loads((rd / "entity.json").read_text())
    assert ent["domain"] == "a2000software.com" and "A2000" in ent["aliases"] and ent["confidence"] >= 0.75
    prof = json.loads((rd / "profile.json").read_text())
    assert prof["facts_kept"] >= 7 and prof["facts_total"] == prof["facts_kept"] + 1   # invented fact dropped
    assert prof["recent_news"]                                                        # Firecrawl news source
    comps = json.loads((rd / "competitors.json").read_text())["competitors"]
    assert len(comps) >= 5 and all(c["independent_sources"] >= 2 for c in comps)
    assert {c["name"] for c in comps} >= {"AIMS360", "ApparelMagic", "Uphance", "BlueCherry", "FDM4", "NetSuite"}
    how = {c["name"]: c["resolved_by"] for c in json.loads((rd / "candidates.json").read_text())}
    assert how["NetSuite"] in ("stated", "link") and how["BlueCherry"] == "model_guess_confirmed"
    assert how["Uphance"] == "search_lookup"        # the model's wrong guess (uphance.io) was not confirmed
    # plain pages came from direct fetches: Firecrawl was only used for search and map
    assert {p for h, p in world.calls if h == "api.firecrawl.dev"} == {"/v2/search", "/v2/map"}
    # C3 added three markdown searches ('"<name>" competitors' x2, a Capterra/GetApp category page): ~ +9 here
    assert rep["cost_by_service"]["firecrawl"]["credits"] <= 40
    content = DeckContent.model_validate_json((rd / "content.json").read_text(), context={"min_competitors": 0})
    assert "Imaginary ERP" not in {c.name for c in content.market.competitors}                # never from memory
    assert any("Imaginary ERP" in w for w in rep["warnings"])
    for f in ("Recur_x_Ai2000.pptx", "Recur_x_Ai2000.pdf", "sources.md", "qa_report.json", "cost.json", "run.log",
              "evidence.jsonl", "candidates.json", "logo_metrics.json"):
        assert (rd / f).exists(), f
    assert qa.check_integrity(rd / "Recur_x_Ai2000.pptx", content) == []
    src = (rd / "sources.md").read_text()
    assert all(f"(https://{c.domain})" in src for c in content.market.competitors)
    out = tmp_path / "output"
    assert (out / "Recur_x_Ai2000.pptx").exists() and (out / "Recur_x_Ai2000_sources.md").exists()
    for p in rd.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".jsonl", ".log", ".md"):
            assert "SECRET-123456" not in p.read_text(errors="ignore"), p
    assert rep["final_vision"]["passed"] is True and rep["status"] in ("pass", "pass_with_warnings")
    # the target's og:image photo was rejected; logos came from the site header
    logos = json.loads((rd / "logo_metrics.json").read_text())
    assert logos["Ai2000"]["source"] == "header_img" and not logos["Ai2000"]["typeset"]

    # replay: same run rebuilt from cache, offline (no transport -> any network call would fail)
    res2 = pipeline.replay(rd, settings=settings(tmp_path), out_dir=None)
    assert res2.exit_code == res.exit_code, res2.report.get("error")
    assert res2.report["status"] == rep["status"]


# ---------------------------------------------------------------- entity gate
def _ambiguous_judge(user):
    return {"ranked": [
        {"domain": "a2000software.com", "score": 0.6, "display_name": "Ai2000", "legal_name": None, "aliases": [],
         "vertical": "apparel ERP", "one_liner": "Apparel ERP", "evidence_quotes": [], "why": "plausible"},
        {"domain": "ai2000.ai", "score": 0.5, "display_name": "AI 2000", "legal_name": None, "aliases": [],
         "vertical": "events", "one_liner": "AI conference", "evidence_quotes": [], "why": "plausible"}],
        "rebrand_detected": False, "notes": ""}


def test_ambiguous_name_exits_3_non_interactive(tmp_path):
    world = World()
    res, _, claude = run(tmp_path, world, FakeClaude(world, {"entity_judge": _ambiguous_judge}))
    assert res.exit_code == 3 and res.report["status"] == "ambiguous_entity"
    assert [c["domain"] for c in res.candidates] == ["a2000software.com", "ai2000.ai"]
    assert claude.calls == ["entity_judge"]           # stopped before any further work


def test_accept_low_confidence_proceeds_and_flags(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    world = World()
    res, _, _ = run(tmp_path, world, FakeClaude(world, {"entity_judge": _ambiguous_judge}), accept_low_confidence=True)
    assert res.exit_code == 1 and res.report["flags"].get("low_confidence_identity") is True


def test_supplied_domain_skips_the_gate(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    world = World()
    res, _, _ = run(tmp_path, world, FakeClaude(world, {"entity_judge": _ambiguous_judge}), domain="a2000software.com")
    assert res.exit_code in (0, 1) and res.report["identity"]["domain_supplied"] is True


def test_stage_failure_writes_report_and_exits_2(tmp_path):
    world = World()

    def boom(user):
        raise RuntimeError("writer exploded")
    res, _, _ = run(tmp_path, world, FakeClaude(world, {"write_deck": boom}))
    assert res.exit_code == 2 and res.report["failed_stage"] == "write"
    rep = json.loads((res.run_dir / "qa_report.json").read_text())
    assert rep["status"] == "error" and "writer exploded" in rep["error"] and rep["traceback"]


# ---------------------------------------------------------------- QA fix loop (unit level)
def qa_state(tmp_path, claude, content_patch=None, logo_overrides=None) -> pipeline.QAState:
    s = settings(tmp_path)
    ctx = Ctx.create(s, s.runs_dir / "qa-test", claude_client=claude)
    d = json.loads((FIX / "ai2000/content.json").read_text())
    for k, v in (content_patch or {}).items():
        node = d
        keys = k.split(".")
        for kk in keys[:-1]:
            node = node[int(kk)] if kk.isdigit() else node[kk]
        node[int(keys[-1]) if keys[-1].isdigit() else keys[-1]] = v
    content = DeckContent.model_validate(d)
    from recur_deck import logos as L
    logos = {}
    for name in [content.company.display_name, *(c.name for c in content.market.competitors)]:
        cands = sorted((FIX / "ai2000/logos" / L.slug(name)).glob("*"))
        img, met = L.process_logo(cands[-1].read_bytes()) if cands else L.typeset_wordmark(name)
        if not met.ok:
            img, met = L.typeset_wordmark(name)
        p = L.save(img, ctx.run_dir / "logos" / f"{L.slug(name)}.png")
        logos[name] = {"png": str(p), "source": "fixture", "typeset": False, "ok": True, "problems": [],
                       "aspect": met.aspect, "ink_coverage": met.ink_coverage, "alternatives": []}
    logos.update(logo_overrides or {})
    comps = {"competitors": [{"name": c.name, "domain": c.domain, "relevance": 0.9 - i * 0.05, "channels": ["a", "b"],
                              "independent_sources": 2, "evidence_ids": []} for i, c in enumerate(content.market.competitors)],
             "thin_market": False}
    entity = {"display_name": "Ai2000", "domain": "a2000software.com", "aliases": ["A2000"], "vertical": "apparel ERP"}
    return pipeline.QAState(ctx, entity, {}, comps, content, logos, pipeline.extra_protected(entity))


def test_overlong_headline_is_rewritten_in_one_round(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    world = World()
    claude = FakeClaude(world)
    long = "Apparel and footwear brands everywhere are replacing their aging legacy ERP systems with AI"
    st = qa_state(tmp_path, claude, {"thesis.0.headline": long})
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    assert st.content.thesis[0].headline == "Apparel brands are moving off legacy ERPs"
    assert not [i for i in out["issues"] if i.severity == "error"]
    assert out["verdict"].passed and len(out["rounds"]) == 1
    assert out["rounds"][0]["fixes_layer_a"][0]["fix"] == "rewrite" and claude.calls.count("rewrite_field") == 1


def test_wrong_logo_from_vision_is_swapped_to_typeset(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    world = World()
    n = {"i": 0}

    def vision(user):
        n["i"] += 1
        if n["i"] == 1:
            return {"passed": False, "summary": "", "defects": [
                {"slide": 3, "element_id": "logo:Uphance", "type": "logo_wrong_company", "severity": "major",
                 "description": "this is not the Uphance logo", "suggested_fix": "swap_logo"}]}
        return {"passed": True, "defects": [], "summary": "ok"}

    claude = FakeClaude(world, {"vision_qa": vision})
    st = qa_state(tmp_path, claude)
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    assert st.logos["Uphance"]["typeset"] is True and out["verdict"].passed
    assert out["rounds"][0]["fixes_vision"][0] == {"fix": "swap_logo", "applied": True, "company": "Uphance",
                                                   "from": "fixture", "to": "typeset"}


def test_same_hash_guard_stops_unfixable_loop(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    world = World()

    def always_fail(user):
        return {"passed": False, "summary": "", "defects": [
            {"slide": 3, "element_id": "logo:Uphance", "type": "logo_illegible", "severity": "major",
             "description": "unreadable", "suggested_fix": "swap_logo"}]}

    claude = FakeClaude(world, {"vision_qa": always_fail})
    st = qa_state(tmp_path, claude)
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    assert claude.calls.count("vision_qa") <= st.ctx.settings.max_qa_rounds + 1
    assert pipeline.final_status(st, out, st.ctx) == "needs_review"
    assert any("already the typeset fallback" in n for n in st.review_notes)


def test_crowded_cell_relayout_moves_lowest_relevance(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    world = World()
    n = {"i": 0}

    def vision(user):
        n["i"] += 1
        if n["i"] == 1:
            return {"passed": False, "summary": "", "defects": [
                {"slide": 3, "element_id": "logo:ApparelMagic", "type": "logo_overlap", "severity": "major",
                 "description": "AIMS360 and ApparelMagic touch", "suggested_fix": "relayout"}]}
        return {"passed": True, "defects": [], "summary": "ok"}

    st = qa_state(tmp_path, FakeClaude(world, {"vision_qa": vision}))
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    fix = out["rounds"][0]["fixes_vision"][0]
    assert fix["fix"] == "relayout" and fix["applied"] and fix["moved"] == "Uphance"   # lowest relevance in that cell
    assert out["verdict"].passed


def test_title_overflow_shortens_display_name(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    world = World()
    long = "Lighthouse Veterinary Practice Cloud Platform Systems International"
    patch = {"company.display_name": long, "market.our_take.headline": f"{long} runs it all"}
    st = qa_state(tmp_path, FakeClaude(world), patch)
    st.entity.update(display_name=long, domain="a2000software.com")
    pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    assert st.content.company.display_name == "Lighthouse" and long in st.content.company.aliases
    assert st.content.market.our_take.headline.startswith("Lighthouse")
