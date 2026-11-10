"""Agent 2: the market graph shared across runs, and the new competitor channels (graph, self_compare, segment).
Simulated web + fake Claude; graphs live in the test's tmp dir."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from recur_deck import market_graph as MG
from recur_deck.config import Settings
from recur_deck.stages import competitors as C
from recur_deck.stages.base import Ctx
from tests.fake_world import FakeClaude, World
from tests.test_known_defects import AI2000, FDM4, UPHANCE
from tests.test_pipeline import fake_render, settings

ENTITY = {"display_name": "Ai2000", "aliases": ["A2000"], "domain": "a2000software.com",
          "vertical": "Apparel, footwear and home goods ERP software",
          "one_liner": "ERP covering PLM, orders, EDI, warehouse and financials for apparel brands"}
PROFILE = {"customer_segments": [], "customer_size_signals": [], "modules": []}


def discover(tmp_path, world: World, profile=PROFILE, s: Settings | None = None):
    s = s or Settings(runs_dir=tmp_path / "runs", firecrawl_rpm=1000, anthropic_api_key="k-ant", firecrawl_api_key="k-fc")
    claude = FakeClaude(world)
    ctx = Ctx.create(s, s.runs_dir / "disc", transport=httpx.MockTransport(world.handler), claude_client=claude)
    try:
        out = C.discover(ctx, ENTITY, profile)
    finally:
        ctx.http.close()
    return out, {c["domain"]: c for c in json.loads((ctx.run_dir / "candidates.json").read_text())}, claude


# ---------------------------------------------------------------- the graph file
def test_graph_roundtrip(tmp_path, monkeypatch):
    g = MG.from_runs([AI2000, FDM4, UPHANCE])
    assert set(g.runs()) == {AI2000.name, FDM4.name, UPHANCE.name}
    g.path = tmp_path / "g.json"
    g.save()
    loaded = MG.load(Settings(runs_dir=tmp_path / "elsewhere"))     # default path: empty graph, no file needed
    assert loaded.data["companies"] == {} and loaded.path == tmp_path / "elsewhere" / "_market_graph.json"
    monkeypatch.setenv("RECUR_MARKET_GRAPH", str(g.path))
    back = MG.load(Settings(runs_dir=tmp_path))
    assert back.data == json.loads(json.dumps(g.data))           # identical after save + load
    g.save()                                                       # a second save keeps the previous file
    assert (tmp_path / "g.json.bak").exists() and not list(tmp_path.glob("g.json.tmp*"))


def test_graph_load_falls_back_to_bak(tmp_path, monkeypatch):
    monkeypatch.setenv("RECUR_MARKET_GRAPH", str(tmp_path / "g.json"))
    g = MG.load(Settings(runs_dir=tmp_path))
    assert g.add_run(ENTITY, {"competitors": []}, None, run="r1", status="pass")
    g.save()
    g.save()
    (tmp_path / "g.json").write_text("{broken")
    assert "r1" in MG.load(Settings(runs_dir=tmp_path)).runs()


def test_add_run_skips_bad_and_repeated_runs():
    g = MG.MarketGraph(None)
    comps = {"competitors": [{"name": "Aptean", "domain": "aptean.com", "channels": ["roundup"], "evidence_ids": []}]}
    assert not g.add_run(ENTITY, comps, None, run="r1", status="needs_review")         # a bad run can't pollute it
    assert not g.add_run(ENTITY, comps, None, run="r0", status="error")
    assert g.data["companies"] == {}
    assert g.add_run(ENTITY, comps, None, run="r1", status="needs_review", accept_needs_review=True)  # explicit ingest
    assert not g.add_run(ENTITY, comps, None, run="r1", status="pass")                 # already recorded
    assert [n["domain"] for n in g.neighbors("a2000software.com")] == ["aptean.com"]
    assert len(MG.from_runs([AI2000, FDM4, UPHANCE], accept_needs_review=False).runs()) == 1   # only FDM4 passed


def test_graph_from_fixtures_records_quotes_and_placements():
    g = MG.from_runs([AI2000, FDM4, UPHANCE])
    apt = g.company("aptean.com")
    assert set(apt["verified_in_runs"]) == {AI2000.name, FDM4.name, UPHANCE.name}
    assert any("built for fast-moving apparel sectors" in f["quote"] and f["url"].startswith("https://www.aptean.com")
               for f in apt["facts"])                                         # quotes and urls, not just ids
    explicit = [p for p in g.placements("aptean.com") if not p["derived"]]
    kinds = {(p["run"], p["axis_kind"], p["side"]) for p in explicit}
    assert (AI2000.name, "product_scope", "high") in kinds and (AI2000.name, "customer_size", "high") in kinds
    assert (FDM4.name, "industry_depth", "low") in kinds
    assert any(p["axis_kind"] is None for p in explicit if p["run"] == UPHANCE.name)   # "Core buyer" names no scale
    derived = {(p["run"], p["axis_kind"]): p["side"] for p in g.placements("aptean.com") if p["derived"]}
    assert derived[(AI2000.name, "industry_depth")] == "high"                # "industry-specific", "built for apparel"
    # a placement that rests on missing evidence is never history ("with no financials cited")
    assert not [p for p in g.placements("bluecherry.com") if p["run"] == AI2000.name and not p["derived"]]
    # edges: competitors, verified-but-not-kept, rejected, unverified candidates
    types = {(e["b"] if e["a"] == "fdm4.com" else e["a"]): e["type"] for e in g.data["edges"] if "fdm4.com" in (e["a"], e["b"])}
    assert types["aptean.com"] == "competitor" and types["a2000software.com"] == "candidate"
    assert types["ecwid.com"] == "rejected"


def test_related_targets_need_a_distinctive_shared_word():
    g = MG.from_runs([AI2000, UPHANCE])
    fdm4 = json.loads((FDM4 / "entity.json").read_text())
    assert set(g.related_targets(fdm4)) == {"a2000software.com", "uphance.com"}       # "apparel"
    assert g.related_targets({"domain": "x.com", "vertical": "Home health agency software"}) == []
    assert MG.market_tokens("Apparel, footwear and home goods ERP software") == {"apparel", "footwear"}


def test_show_lists_every_placement():
    g = MG.from_runs([AI2000, FDM4, UPHANCE])
    out = MG._show(g, "aptean.com")
    assert out.count(" = ") == len(g.placements("aptean.com")) and "industry_depth = low" in out


def test_offline_runs_neither_read_nor_write_the_graph(tmp_path):
    s = Settings(runs_dir=tmp_path, offline=True)
    g = MG.for_run(s)
    assert g.read_only and not g.add_run(ENTITY, {"competitors": []}, None, run="r", status="pass")
    g.save()
    assert not (tmp_path / "_market_graph.json").exists()


def test_finished_live_run_is_recorded(tmp_path, monkeypatch):
    from recur_deck import pipeline
    fake_render(monkeypatch)
    world = World()
    s = settings(tmp_path)
    res = pipeline.run("Ai2000", settings=s, out_dir=None, transport=httpx.MockTransport(world.handler),
                       claude_client=FakeClaude(world), non_interactive=True)
    assert res.report["status"] in MG.GOOD_STATUSES
    g = MG.load(s)
    assert res.run_dir.name in g.runs() and g.path == s.runs_dir / "_market_graph.json"
    assert {n["domain"] for n in g.neighbors("a2000software.com", depth=1)} >= {"aptean.com", "fdm4.com"}


# ---------------------------------------------------------------- new channels
def test_self_compare_channel(tmp_path):
    out, cands, _ = discover(tmp_path, World(compare=True))
    bp = cands["brightpearl.com"]
    assert "self_compare" in bp["channels"] and bp["head_to_head"]
    assert out["self_compare_chunks"] >= 1
    assert C.self_compare_urls(["https://a.com/integrations/shopify", "https://a.com/switch-from-x",
                                "https://a.com/compare/a-vs-b", "https://b.com/vs", "https://a.com/vsync"], "a.com") == [
        "https://a.com/compare/a-vs-b", "https://a.com/switch-from-x", "https://a.com/integrations/shopify"]


def test_segment_search(tmp_path):
    world = World(segments=True)
    profile = {**PROFILE, "customer_segments": [{"text": "apparel decorators", "evidence_id": "e1"}]}
    out, cands, _ = discover(tmp_path, world, profile)
    queries = [b["query"] for b in world.searches]
    assert "apparel decorators software" in queries and "apparel decorators ERP" in queries
    deco = cands["deconetwork.com"]
    assert deco["channels"] == ["segment"] and deco["segments"] == ["apparel decorators"]
    assert cands["printavo.com"]["segments"] == ["apparel decorators"]       # from the segment roundup's text
    assert out["searches"]["segment"] == 2
    verified = {v["domain"]: v for v in out["competitors"] + out["rejected"]}
    assert "deconetwork.com" in verified


def test_segment_already_in_the_vertical_is_not_searched(tmp_path):
    world = World()
    profile = {**PROFILE, "customer_segments": [{"text": "Apparel and footwear brands", "evidence_id": "e1"}]}
    out, _, claude = discover(tmp_path, world, profile)
    assert "competitor_segments" in claude.calls and "segment" not in out["searches"]


def test_recall_floor(tmp_path):
    """20 vendors across channels (deep roundup, directories, the target's compare page, a sub-segment):
    at least 15 reach verification (the old rule verified at most 12)."""
    world = World(deep=True, compare=True, segments=True)
    assert len(world.comps) == 20
    profile = {**PROFILE, "customer_segments": [{"text": "apparel decorators", "evidence_id": "e1"}]}
    out, cands, claude = discover(tmp_path, world, profile)
    assert len(cands) >= 15 and claude.calls.count("competitor_verify") >= 15
    assert out["verified_count"] >= 15 and C.MAX_VERIFY == 20


def test_graph_seed_is_verified_and_history_attached(tmp_path):
    s = Settings(runs_dir=tmp_path / "runs", firecrawl_rpm=1000, anthropic_api_key="k-ant", firecrawl_api_key="k-fc")
    g = MG.load(s)
    old = {"display_name": "Uphance", "domain": "uphance.com", "vertical": "Apparel ERP for fashion brands"}
    content = {"market": {"x_axis": {"title": "Product scope", "low_label": "Ops only", "high_label": "Full suite"},
                          "y_axis": {"title": "Customer size", "low_label": "Small brands", "high_label": "Large brands"},
                          "competitors": [{"name": "Aptean", "domain": "aptean.com", "x": "right", "y": "top",
                                           "rationale": "Apparel ERP with accounting", "evidence_ids": []}]}}
    comps = {"competitors": [{"name": "Apparel21", "domain": "apparel21.com", "channels": ["roundup"], "evidence_ids": []},
                             {"name": "Aptean", "domain": "aptean.com", "channels": ["roundup"], "evidence_ids": []}]}
    assert g.add_run(old, comps, content, run="uphance-old", status="pass")
    g.save()
    out, cands, claude = discover(tmp_path, World(graph_only=True), s=s)
    a21 = cands["apparel21.com"]
    assert a21["channels"] == ["graph"] and a21["resolved_by"] == "graph" and a21["graph_via"] == ["market:uphance.com"]
    assert "graph" in cands["uphance.com"]["channels"] and "graph" in cands["aptean.com"]["channels"]
    verified = {v["domain"]: v for v in out["competitors"] + out["rejected"]}
    assert "apparel21.com" in verified                                   # still checked on its live site
    assert out["graph_seeds"][:1] == ["uphance.com"]
    apt = next(v for v in out["competitors"] if v["domain"] == "aptean.com")
    assert {(p["axis_kind"], p["side"]) for p in apt["graph_placements"]} >= {("product_scope", "high"), ("customer_size", "high")}


def test_graph_seeds_skip_rejected_and_target():
    g = MG.MarketGraph(None)
    g.add_run(ENTITY, {"competitors": [{"name": "Aptean", "domain": "aptean.com", "channels": [], "evidence_ids": []}],
                       "rejected": [{"name": "Ecwid", "domain": "ecwid.com", "relevance": 0.1, "same_vertical": False,
                                     "channels": []}]}, None, run="r1", status="pass")
    g.add_run({"display_name": "Ecwid", "domain": "ecwid.com", "vertical": "Ecommerce store builder"},
              {"competitors": [{"name": "Volusion", "domain": "volusion.com", "channels": [], "evidence_ids": []}]},
              None, run="r2", status="pass")
    seeds = {c.domain for c in C.graph_seeds(g, ENTITY)}
    assert "aptean.com" in seeds and "ecwid.com" not in seeds and "a2000software.com" not in seeds
    assert C.graph_seeds(None, ENTITY) == []


@pytest.mark.parametrize("seg,skipped", [("apparel decorators", False), ("footwear brands", True),
                                         ("Promotional products distributors", False)])
def test_segment_terms_filter(tmp_path, seg, skipped):
    s = Settings(runs_dir=tmp_path / "runs", anthropic_api_key="k-ant")
    claude = FakeClaude(World(), {"competitor_segments": lambda user: {"segments": [seg]}})
    ctx = Ctx.create(s, s.runs_dir / "seg", claude_client=claude)
    out = C.segment_terms(ctx, ENTITY, {"customer_segments": [{"text": "x"}]})
    assert out == ([] if skipped else [seg])
    assert C.segment_terms(ctx, ENTITY, {"customer_segments": []}) == []
