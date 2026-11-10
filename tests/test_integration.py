"""Agent 6: the new checks change the output. Fix routing in the QA loop, status semantics, the cost cap and
`recur-deck check`. Simulated web + fake Claude; no network (the conftest guard fails any attempt)."""
from __future__ import annotations

import json
import shutil
import time

import httpx
import pytest

from recur_deck import cli, market_graph, pipeline
from recur_deck.cache import BudgetExceeded, CostLedger
from recur_deck.models import DeckContent
from recur_deck.stages import competitors as s_comp
from recur_deck.stages import logo_sources as s_logos
from recur_deck.stages import write as s_write
from tests.fake_world import FakeClaude, World
from tests.test_known_defects import AI2000, FDM4, UPHANCE
from tests.test_pipeline import fake_render, qa_state, settings


def run(tmp_path, claude, world, **kw):
    return pipeline.run("Ai2000", settings=settings(tmp_path, **kw), out_dir=tmp_path / "out",
                        transport=httpx.MockTransport(world.handler), claude_client=claude, non_interactive=True)


# ---------------------------------------------------------------- routing
def test_fix_routing(tmp_path, monkeypatch):
    """The writer's first market section leaves the target alone -> the market (only) is rewritten; the thesis stays
    byte-identical even though the rewrite answer changed it."""
    fake_render(monkeypatch)
    world = World()
    claude = FakeClaude(world)

    def writer(user):
        d = claude.a_write_deck(user)
        if "<rewrite_only>" not in user:
            for c in d["market"]["competitors"]:
                if (c["x"], c["y"]) == ("right", "top"):
                    c["y"] = "bottom"                                      # nobody next to Ai2000
        else:
            d["thesis"][0]["headline"] = "This line must not reach the deck"
        return d
    claude.overrides["write_deck"] = writer
    sections = []
    orig = s_write.write_deck
    monkeypatch.setattr(s_write, "write_deck", lambda *a, **kw: (sections.append(kw.get("section", "all")), orig(*a, **kw))[1])
    res = run(tmp_path, claude, world)
    rep = res.report
    assert res.exit_code in (0, 1), rep.get("error")
    assert sections == ["all", "market"]
    first = json.loads((res.run_dir / "content.round0.json").read_text())
    final = json.loads((res.run_dir / "content.json").read_text())
    assert json.dumps(final["thesis"]) == json.dumps(first["thesis"]) and final["lever"] == first["lever"]
    assert any((c["x"], c["y"]) == ("right", "top") for c in final["market"]["competitors"])
    fixes = [f for r in rep["rounds"] for f in r.get("fixes_layer_a", [])]
    regen = [f for f in fixes if f["fix"] == "regenerate_market"]
    assert len(regen) == 1 and regen[0]["applied"]                          # target_alone (+ the placement it implied)
    assert rep["status"] in ("pass", "pass_with_warnings")


def test_thesis_problems_regenerate_the_thesis_only(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    st = qa_state(tmp_path, FakeClaude(World()), {"thesis.1.evidence_ids": ["e1"]})   # a scope quote: not durability
    seen = []

    def regen(ctx, entity, profile, comps, problems=None, section="all", current=None):
        seen.append((section, problems))
        data = current.model_dump()
        data["thesis"][1]["evidence_ids"] = ["e11"]
        return DeckContent.model_validate(data, context={"min_competitors": 0})
    monkeypatch.setattr(s_write, "write_deck", regen)
    market_before = st.content.market.model_dump_json()
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    assert [s for s, _ in seen] == ["thesis"] and "durability" in seen[0][1][0]
    assert st.content.market.model_dump_json() == market_before
    assert {"fix": "regenerate_thesis", "applied": True, "problems": 1} in out["rounds"][0]["fixes_layer_a"]
    assert out["verdict"].passed and pipeline.final_status(st, out, st.ctx) != "needs_review"


def test_tagline_micro_text_is_cropped_first(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    st = qa_state(tmp_path, FakeClaude(World()))
    png = st.ctx.run_dir / "logos" / "aims360-lockup.png"
    shutil.copy(UPHANCE / "logos" / "aims360.png", png)
    st.logos["AIMS360"].update(png=str(png), source="jsonld", aspect=4.367, native_w=1145, native_h=262)
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    fix = out["rounds"][0]["fixes_layer_a"][0]
    assert fix["fix"] == "crop_tagline" and fix["applied"] and st.logos["AIMS360"]["cropped_tagline"]
    assert not st.logos["AIMS360"]["typeset"] and out["verdict"].passed


def test_critic_add_competitor_verifies_a_found_candidate(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    n = {"i": 0}

    def critic(user):
        n["i"] += 1
        findings = [] if n["i"] > 1 else [{"persona": "founder", "slide": 3, "target": "market.competitors",
                                          "category": "missing_competitor", "severity": "major", "suggested_fix": "add_competitor",
                                          "explanation": "Brightpearl, which this run found, is missing from the map."}]
        return {"findings": findings, "slide_grades": {"slide2": "B", "slide3": "B"}, "mail_ready": not findings}
    st = qa_state(tmp_path, FakeClaude(World(), {"critic": critic}))
    (st.ctx.run_dir / "candidates.json").write_text(json.dumps([{"name": "Brightpearl", "domain": "brightpearl.com",
                                                                 "channels": ["roundup"], "mentions": []}]))
    verified = {"name": "Brightpearl", "domain": "brightpearl.com", "is_software_vendor": True, "same_vertical": True,
                "operating": True, "relevance": 0.8, "channels": ["roundup"], "evidence_ids": [], "_pending_evidence": [],
                "independent_sources": 0, "homepage": "https://brightpearl.com/"}
    monkeypatch.setattr(s_comp, "_verify", lambda ctx, entity, profile, c: dict(verified))
    monkeypatch.setattr(s_logos, "resolve_company", lambda ctx, name, *a, **kw: {"name": name, **s_logos.typeset(name, tmp_path)})
    problems = []
    monkeypatch.setattr(pipeline, "fix_regenerate", lambda st, section, p: (problems.append((section, p)),
                                                                            {"fix": "regenerate_market", "applied": True})[1])
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    assert out["rounds"][0]["fixes_critic"][0] == {"fix": "add_competitor", "applied": True, "company": "Brightpearl"}
    assert problems[0][0] == "market" and "add Brightpearl (brightpearl.com) to the map" in problems[0][1][0]
    assert "Brightpearl" in st.logos and any(v["domain"] == "brightpearl.com" for v in st.comps["competitors"])


# ---------------------------------------------------------------- status
def test_pass_requires_critic(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    major = {"findings": [{"persona": "partner", "slide": 3, "target": "market.competitors", "category": "other",
                           "severity": "major", "explanation": "The map would not survive a call with the founder.",
                           "suggested_fix": "none"}],
             "slide_grades": {"slide2": "B", "slide3": "C"}, "mail_ready": True}
    st = qa_state(tmp_path, FakeClaude(World(), {"critic": lambda user: major}))
    rep = pipeline._qa_and_deliver(st.ctx, st, {}, None, lambda s: None, time.time(), {}).report
    assert rep["final_vision"]["passed"] is True                           # vision passed ...
    assert rep["status"] == "needs_review"                                 # ... the critic did not
    assert any("would not survive a call with the founder" in n for n in rep["needs_review"])
    assert rep["critic"]["grades"] == {"slide2": "B", "slide3": "C"} and rep["critic"]["mail_ready"] is False


def test_minor_critic_findings_give_pass_with_warnings(tmp_path, monkeypatch):
    fake_render(monkeypatch)
    minor = {"findings": [{"persona": "founder", "slide": 2, "target": "thesis[0].headline", "category": "other",
                           "severity": "minor", "explanation": "Could be sharper.", "suggested_fix": "none"}],
             "slide_grades": {"slide2": "A-", "slide3": "A"}, "mail_ready": True}
    st = qa_state(tmp_path, FakeClaude(World(), {"critic": lambda user: minor}))
    out = pipeline.qa_loop(st, lambda s: None, deadline=1e12)
    assert pipeline.final_status(st, out, st.ctx) == "pass_with_warnings"


# ---------------------------------------------------------------- the cost cap
def test_budget_guard(tmp_path, monkeypatch):
    """A ledger near the cap: the next Claude call is refused; the deck is still delivered, as needs_review."""
    fake_render(monkeypatch)
    claude = FakeClaude(World())
    st = qa_state(tmp_path, claude, {"thesis.0.headline": "Apparel and footwear brands everywhere are replacing their "
                                                          "aging legacy ERP systems with AI"})
    st.ctx.ledger.cap_usd = 0.05
    st.ctx.ledger.record("anthropic", "earlier stages", 200, 0, cost_usd=0.049)
    res = pipeline._qa_and_deliver(st.ctx, st, {}, tmp_path / "out", lambda s: None, time.time(), {})
    rep = res.report
    assert "rewrite_field" not in claude.calls                             # refused before it was sent
    assert rep["status"] == "needs_review" and any(n.startswith("budget reached") for n in rep["needs_review"])
    assert rep["budget_reached"] and (tmp_path / "out" / "Recur_x_Ai2000.pptx").exists()


def test_budget_stops_a_research_stage(tmp_path):
    world = World()
    res = run(tmp_path, FakeClaude(world), world, max_cost_usd=0.001)
    assert res.exit_code == 2 and res.report["status"] == "budget_exceeded"
    assert "RECUR_MAX_COST_USD" in res.report["error"] and not list(res.run_dir.glob("*.pptx"))


def test_ledger_guard_and_firecrawl_credits():
    led = CostLedger(cap_usd=1.0, usd_per_credit=0.01)
    led.record("firecrawl", "/search", 200, 0, credits=60)                # $0.60 counted toward the cap
    led.record("anthropic", "x", 200, 0, cost_usd=0.30)
    assert led.spend_usd() == 0.9 and led.total_usd() == 0.3
    led.guard("anthropic", "ok", 0.05)
    with pytest.raises(BudgetExceeded):
        led.guard("anthropic", "too much", 0.2)
    assert len(led.budget_hits) == 1 and "RECUR_MAX_COST_USD" in led.budget_hits[0]
    CostLedger(cap_usd=None).guard("anthropic", "no cap", 1e6)


# ---------------------------------------------------------------- recur-deck check
def test_check_command_offline(tmp_path, monkeypatch, capsys, net_guard):
    monkeypatch.setenv("RECUR_MARKET_GRAPH", str(tmp_path / "graph.json"))
    assert market_graph.main(["ingest", str(AI2000)]) == 0                # what the earlier Ai2000 run knew
    capsys.readouterr()
    code = cli.main(["check", str(FDM4)])
    out = capsys.readouterr().out
    assert code == 1 and "no network, no API calls" in out
    assert "target_alone" in out                                           # D5
    assert "placement_contradicts_history market.competitors[0]" in out    # D7: Aptean
    assert not net_guard.attempts


def test_check_command_across_runs_and_critic_needs_yes(capsys):
    code = cli.main(["check", str(FDM4), str(UPHANCE)])
    out = capsys.readouterr().out
    assert code == 1 and "repetitive_across_decks" in out                  # the other run on the command line counts
    assert cli.main(["check", str(FDM4), "--critic"]) == 2                 # live calls need --yes


def test_scorecard_fills_the_live_test_plan_table(capsys):
    assert cli.main(["check", "--scorecard", str(AI2000), str(FDM4), str(UPHANCE)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("| Deck | Run | Status |")
    assert "| Uphance | uphance-20261109-215758 | needs_review |" in out and "106 cover" in out
    assert "FDM4: no/no" in out                                            # the old Ai2000 and FDM4 decks missed each other
