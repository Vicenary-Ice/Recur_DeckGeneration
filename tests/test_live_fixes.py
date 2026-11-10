"""Fixes for runs/ai2000-20261109-163637 (needs_review), tested on that run's real files where possible.

C1  logo name match: AIMS360's real lockup ("AIMS360 FASHION TECHNOLOGY") was rejected, typeset used instead.
C2  typeset map logos: smaller default height, and the QA loop resizes them instead of a no-op swap.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from recur_deck import layout
from recur_deck import logos as L
from recur_deck import pipeline
from recur_deck.spec import MAP_LOGO, MAP_TYPESET_MAX_H, MIN_LOGO_H
from recur_deck.stages.logo_sources import LogoVerdict, accept, first_distinctive_token, fuzzy_ratio
from tests.fake_world import FakeClaude, World
from tests.test_pipeline import fake_render, qa_state

LIVE = Path(__file__).parent / "fixtures_live" / "ai2000-20261109-163637"


def recorded() -> dict:
    return json.loads((LIVE / "logo_metrics.json").read_text())["companies"]


def verdict(company: str, cid: str) -> LogoVerdict:
    t = next(t for t in recorded()[company]["tried"] if t["id"] == cid)
    return LogoVerdict(**t["verdict"])


# ---------------------------------------------------------------- C1: accept()
def test_aims360_real_lockup_is_accepted():
    v = verdict("AIMS360", "c1")
    assert v.text_read == "AIMS360 FASHION TECHNOLOGY"
    assert fuzzy_ratio(v.text_read, "AIMS360") < 0.6     # why the old rule rejected it
    assert accept(v, ["AIMS360"])


def test_instyle_lockup_is_accepted():
    v = verdict("InStyle Apparel Software", "c1")
    assert v.text_read == "InStyle! In-Style Software"
    assert first_distinctive_token("InStyle Apparel Software") == "instyle"
    assert accept(v, ["InStyle Apparel Software"])


def test_bluecherry_by_cgs_is_accepted():
    v = verdict("BlueCherry", "c1")
    assert v.text_read == "BlueCherry by CGS"
    assert accept(v, ["BlueCherry"])


def test_another_companys_wordmark_is_rejected():
    v = verdict("Aptean", "c1")                          # the real Aptean wordmark verdict
    assert v.text_read.lower() == "aptean"
    assert not accept(v, ["AIMS360"])
    assert not accept(LogoVerdict(**{**v.model_dump(), "text_read": "Aptean"}), ["AIMS360", "AIMS 360"])


def test_alias_and_reverse_containment():
    base = verdict("AIMS360", "c1").model_dump()
    assert accept(LogoVerdict(**{**base, "text_read": "A2K"}), ["Ai2000", "A2K"])            # alias contained
    assert accept(LogoVerdict(**{**base, "text_read": "Ai2000"}), ["Ai2000 Software"])       # text in name
    assert not accept(LogoVerdict(**{**base, "text_read": "Fashion Technology"}), ["AIMS360"])
    assert first_distinctive_token("CGS Fashion") is None                                    # short first word


def test_non_logo_verdicts_still_rejected():
    for cid in ("c2", "c4", "c5"):                       # generic icon, banner, og screenshot
        assert not accept(verdict("AIMS360", cid), ["AIMS360"])


def test_aims360_gets_its_real_logo_from_the_live_files():
    """Same order as resolve_company: process each real raw file, then the recorded verdict decides."""
    passed = []
    for t in recorded()["AIMS360"]["tried"]:
        raw = LIVE / t["raw"]
        _, met = L.process_logo(raw.read_bytes(), designed_for_dark=t["white_as_ink"])
        if met.ok and accept(LogoVerdict(**t["verdict"]), ["AIMS360"]):
            passed.append(t)
    assert [t["id"] for t in passed] == ["c1", "c3"]     # jsonld lockup first, the header lockup as alternative
    assert passed[0]["source"] == "jsonld"


# ---------------------------------------------------------------- C2: typeset map logos
def test_typeset_default_height_and_override():
    assert MAP_LOGO["typeset_h"] == 0.13 and MAP_TYPESET_MAX_H == 0.17
    comps = [("AIMS360", "right", "bottom", 5.661), ("Uphance", "left", "bottom", 4.0)]
    boxes, _, _ = layout.market_layout(3.0, comps, typeset=frozenset({"AIMS360", "Uphance"}))
    assert boxes["AIMS360"].h == pytest.approx(0.13) and boxes["Uphance"].h == pytest.approx(0.13)
    boxes, _, _ = layout.market_layout(3.0, comps, typeset=frozenset({"AIMS360", "Uphance"}),
                                       typeset_h={"AIMS360": 0.11})
    assert boxes["AIMS360"].h == pytest.approx(0.11) and boxes["Uphance"].h == pytest.approx(0.13)


def _typeset_state(tmp_path, claude, name="Uphance"):
    st = qa_state(tmp_path, claude)
    st.logos[name].update(pipeline.s_logos.typeset(name, st.ctx.run_dir / "logos" / "_typeset"))
    return st


def _size_defect(kind: str, name="Uphance") -> dict:
    return {"slide": 3, "element_id": f"logo:{name}", "type": kind, "severity": "major",
            "description": f"the {name} wordmark is {kind}", "suggested_fix": "swap_logo"}


def _map_h(out, name="Uphance") -> float:
    return next(e["h"] for e in out["manifest"]["elements"] if e["id"] == f"logo:{name}")


def test_typeset_logo_too_large_is_shrunk_and_rechecked(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    n = {"i": 0}

    def vision(user):
        n["i"] += 1
        return {"passed": n["i"] > 1, "summary": "", "defects": [] if n["i"] > 1 else [_size_defect("logo_too_large")]}

    claude = FakeClaude(World(), {"vision_qa": vision})
    st = _typeset_state(tmp_path, claude)
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    fix = out["rounds"][0]["fixes_vision"][0]
    assert fix == {"fix": "resize_typeset", "applied": True, "company": "Uphance", "from_h": 0.13, "to_h": 0.11}
    assert _map_h(out) == pytest.approx(0.11)            # 0.13 * 0.8 = 0.104, floored at MIN_LOGO_H
    assert MIN_LOGO_H == 0.11
    assert out["verdict"].passed and not st.review_notes and claude.calls.count("vision_qa") == 2
    assert pipeline.final_status(st, out, st.ctx) != "needs_review"


def test_typeset_logo_too_small_is_grown(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    n = {"i": 0}

    def vision(user):
        n["i"] += 1
        return {"passed": n["i"] > 1, "summary": "", "defects": [] if n["i"] > 1 else [_size_defect("logo_too_small")]}

    st = _typeset_state(tmp_path, FakeClaude(World(), {"vision_qa": vision}))
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    assert out["rounds"][0]["fixes_vision"][0]["to_h"] == pytest.approx(0.1495)   # 0.13 * 1.15, under 0.17
    assert not st.review_notes


def test_typeset_resize_needs_review_only_after_one_resize(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    claude = FakeClaude(World(), {"vision_qa": lambda user: {"passed": False, "summary": "",
                                                             "defects": [_size_defect("logo_too_large")]}})
    st = _typeset_state(tmp_path, claude)
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    fixes = [f for r in out["rounds"] for f in r.get("fixes_vision", [])]
    assert fixes[0]["applied"] is True and fixes[1] == {"fix": "resize_typeset", "applied": False,
                                                        "company": "Uphance", "why": "already resized once"}
    assert any("still too large after one resize" in n for n in st.review_notes)
    assert not any("already the typeset fallback" in n for n in st.review_notes)
    assert pipeline.final_status(st, out, st.ctx) == "needs_review"


def test_non_typeset_size_defect_still_swaps(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    n = {"i": 0}

    def vision(user):
        n["i"] += 1
        return {"passed": n["i"] > 1, "summary": "", "defects": [] if n["i"] > 1 else [_size_defect("logo_too_large")]}

    st = qa_state(tmp_path, FakeClaude(World(), {"vision_qa": vision}))
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    assert out["rounds"][0]["fixes_vision"][0]["fix"] == "swap_logo"
