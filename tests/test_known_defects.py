"""Known defects D1-D12 of the three live decks (table in docs/agents/README.md), as tests on the frozen runs in
tests/fixtures_live/. Each starts as xfail(strict=True); the owning agent removes the marker when the fix lands.

Functions under test are imported inside each test, so collection works before they exist.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from recur_deck.evidence import EvidenceStore
from recur_deck.models import DeckContent

LIVE = Path(__file__).parent / "fixtures_live"
AI2000 = LIVE / "ai2000-20261109-163637-deck"   # the reviewed Ai2000 deck (the briefs call it ai2000-212951)
FDM4 = LIVE / "fdm4-20261109-214123"
UPHANCE = LIVE / "uphance-20261109-215758"
DECKS = [AI2000, FDM4, UPHANCE]
BASELINE = Path(__file__).resolve().parent.parent / "assets" / "baseline"


def content(run: Path) -> DeckContent:
    return DeckContent.model_validate_json((run / "content.json").read_text(), context={"min_competitors": 0})


def evidence(run: Path) -> EvidenceStore:
    return EvidenceStore.load(run / "evidence.jsonl")


def jload(run: Path, name: str):
    return json.loads((run / name).read_text())


def manifest(run: Path) -> dict:
    return json.loads(next(run.glob("Recur_x_*.manifest.json")).read_text())


def comp_index(c: DeckContent, name: str) -> int:
    return next(i for i, x in enumerate(c.market.competitors) if x.name == name)


# ---------------------------------------------------------------- Agent 1: thesis
@pytest.mark.parametrize("run", DECKS, ids=lambda p: p.name)
def test_d1_copied_recur_phrases(run):
    from recur_deck.checks.copy import copied_spans
    issues = copied_spans(content(run))
    assert issues and all(i.code == "copied_language" for i in issues)


@pytest.mark.parametrize("run", DECKS, ids=lambda p: p.name)
def test_d2_point_03_cites_no_gap(run):
    from recur_deck.checks.copy import lever_issues
    issues = lever_issues(content(run), evidence(run))
    assert any(i.code == "lever_unsupported" and i.field.startswith("thesis[2]") for i in issues)


@pytest.mark.parametrize("run", DECKS, ids=lambda p: p.name)
def test_d3_point_02_has_no_durability_evidence(run):
    from recur_deck.checks.copy import durability_issues
    issues = durability_issues(content(run), evidence(run))
    assert any(i.code == "durability_missing" and i.field.startswith("thesis[1]") for i in issues)


# ---------------------------------------------------------------- Agent 3: market map
def test_d4_placement_from_missing_evidence():
    from recur_deck.checks.market import placement_issues
    c = content(AI2000)
    issues = [i for i in placement_issues(c, evidence(AI2000)) if i.code == "placement_unsupported"]
    fields = {i.field for i in issues}
    assert f"market.competitors[{comp_index(c, 'BlueCherry')}]" in fields          # "no financials cited"
    assert "market.our_take.points[1]" in fields                                    # "lack financials"


def test_d5_target_alone_in_its_cell():
    from recur_deck.checks.market import plausibility_issues
    assert any(i.code == "target_alone" for i in plausibility_issues(content(FDM4), extra={}))


def test_d6_core_buyer_axis_is_not_ordinal():
    from recur_deck.checks.market import axis_issues
    issues = axis_issues(content(UPHANCE))
    assert any(i.code == "axis_not_ordinal" and i.field.startswith("market.y_axis") for i in issues)


def test_d7_aptean_contradicts_earlier_run():
    from recur_deck import market_graph
    from recur_deck.checks.market import consistency_issues
    graph = market_graph.from_runs([AI2000])
    c = content(FDM4)
    issues = [i for i in consistency_issues(c, graph) if i.code == "placement_contradicts_history"]
    assert any(i.field == f"market.competitors[{comp_index(c, 'Aptean')}]" for i in issues)


# ---------------------------------------------------------------- Agent 2: recall
def test_d8_fdm4_discovery_seeds_from_the_graph():
    from recur_deck import market_graph
    from recur_deck.stages.competitors import graph_seeds
    graph = market_graph.from_runs([AI2000, UPHANCE])
    seeds = {c.domain for c in graph_seeds(graph, jload(FDM4, "entity.json"))}
    assert {"a2000software.com", "apparelmagic.com", "bluecherry.com"} <= seeds


# ---------------------------------------------------------------- Agent 4: logos
@pytest.mark.xfail(strict=True, reason="D9 — owner Agent 4")
def test_d9_typeset_name_has_no_brackets():
    from recur_deck.names import brand_short_name
    assert brand_short_name("Sync (iSync Solutions)") == "Sync"


@pytest.mark.xfail(strict=True, reason="D10 — owner Agent 4")
def test_d10_uphance_cover_logo_under_300_dpi():
    from recur_deck.checks.logo_print import print_dpi_issues
    issues = print_dpi_issues(manifest(UPHANCE), jload(UPHANCE, "logo_metrics.json"))
    assert any(i.code == "logo_low_res" and i.field == "logo:Uphance" and i.slide == 1 for i in issues)


@pytest.mark.xfail(strict=True, reason="D11 — owner Agent 4")
def test_d11_aims360_tagline_prints_under_5pt():
    from recur_deck.stages.logo_sources import tagline_issue
    el = next(e for e in manifest(UPHANCE)["elements"] if e["id"] == "logo:AIMS360")
    issue = tagline_issue(UPHANCE / "logos" / "aims360.png", el["h"])
    assert issue is not None and issue.code == "logo_tagline_microtext"


# ---------------------------------------------------------------- Agent 5: critic
CRITIC_ROUTES = {  # deck -> (fix, field) pairs the scripted verdict's findings must route to
    AI2000.name: {("rewrite_copy", "thesis[1].headline"), ("regenerate_market", "market.our_take.points[1]")},
    FDM4.name: {("rewrite_copy", "thesis[1].headline"), ("regenerate_market", "market.competitors")},
    UPHANCE.name: {("rewrite_copy", "thesis[1].headline"), ("regenerate_market", "market.y_axis"),
                   ("swap_logo", "logo:Sync (iSync Solutions)")},
}


@pytest.mark.xfail(strict=True, reason="D12 — owner Agent 5")
@pytest.mark.parametrize("run", DECKS, ids=lambda p: p.name)
def test_d12_critic_findings_route_to_fixes(run, tmp_path):
    from recur_deck import market_graph
    from recur_deck.config import Settings
    from recur_deck.stages import critic
    from recur_deck.stages.base import Ctx
    from tests.fake_world import FakeClaude, World

    scripted = json.loads((LIVE / "critic" / f"{run.name}.json").read_text())
    s = Settings(runs_dir=tmp_path / "runs")
    ctx = Ctx.create(s, s.runs_dir / "critic-test", claude_client=FakeClaude(World(), {"critic": lambda user: scripted}))
    pngs = [BASELINE / f"slide{i}.png" for i in (1, 2, 3)]
    verdict = critic.review(ctx, content(run), pngs, manifest(run), evidence(run), market_graph.MarketGraph(None))
    assert verdict.mail_ready is False
    routes = {(f["fix"], f["field"]) for f in critic.to_fixes(verdict)}
    assert CRITIC_ROUTES[run.name] <= routes
