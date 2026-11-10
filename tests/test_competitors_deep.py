"""C3: competitor discovery was shallow on the live Ai2000 run (8 candidates; FDM4, Uphance, Sync and Datatex never
found). Simulated web (tests/fake_world.py, deep=True) + fake Claude; verification rules are unchanged."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from recur_deck.config import Settings
from recur_deck.stages import competitors as C
from recur_deck.stages.base import Ctx
from tests.fake_world import DEEP_HEAD, FakeClaude, World

ENTITY = {"display_name": "Ai2000", "aliases": ["A2000"], "domain": "a2000software.com",
          "vertical": "Apparel, footwear and home goods ERP software",
          "one_liner": "ERP covering PLM, orders, EDI, warehouse and financials for apparel brands"}
PROFILE = {"customer_segments": [], "customer_size_signals": [], "modules": []}


def discover(tmp_path, world: World):
    s = Settings(runs_dir=tmp_path / "runs", firecrawl_rpm=1000, anthropic_api_key="k-ant", firecrawl_api_key="k-fc")
    ctx = Ctx.create(s, s.runs_dir / "deep", transport=httpx.MockTransport(world.handler),
                     claude_client=FakeClaude(world))
    try:
        out = C.discover(ctx, ENTITY, PROFILE)
    finally:
        ctx.http.close()
    return out, json.loads((ctx.run_dir / "candidates.json").read_text())


def test_deep_discovery_finds_at_least_15_candidates(tmp_path):
    world = World(deep=True)
    out, cands = discover(tmp_path, world)
    doms = {c["domain"] for c in cands}
    assert len(cands) >= 15, sorted(doms)
    # the four the live run missed
    assert {"fdm4.com", "uphance.com", "syncapparel.com", "datatex.com"} <= doms
    # the new searches ran: '"<name>" competitors' for the brand and its alias, and the category page
    queries = [b["query"] for b in world.searches]
    assert '"Ai2000" competitors' in queries and '"A2000" competitors' in queries
    cat = [b for b in world.searches if b.get("includeDomains") == ["capterra.com", "getapp.com"]]
    assert [b["query"] for b in cat] == ["Apparel, footwear and home goods ERP software"]
    assert all(b.get("scrapeOptions") for b in cat)
    # name variants merged into their vendor, not separate candidates
    names = {c["name"] for c in cands}
    assert "Aptean Exenta" not in names and "CGS BlueCherry" not in names
    aptean = next(c for c in cands if c["domain"] == "aptean.com")
    assert "Aptean Exenta" in aptean["names"]
    # verification and selection unchanged: at most KEEP kept, each verified on its own site
    assert 5 <= len(out["competitors"]) <= C.KEEP and out["verified_count"] <= C.MAX_VERIFY


def test_old_6000_char_cut_missed_the_tail(tmp_path, monkeypatch):
    """Why chunking matters: with the old single 6000-char window, vendors listed late on a roundup are lost."""
    assert len(DEEP_HEAD) > 6000
    monkeypatch.setattr(C, "MAX_CHUNKS", 1)
    monkeypatch.setattr(C, "CHUNK_CHARS", 6000)
    _, cands = discover(tmp_path, World(deep=True))
    assert "syncapparel.com" not in {c["domain"] for c in cands}  # Sync is named only in the roundup's tail


def test_page_chunks_cover_the_whole_list():
    md = "\n".join(f"line {i} " + "x" * 90 for i in range(250))  # ~25k chars
    chunks = C.page_chunks("Best apparel ERP", "desc", md)
    assert 2 <= len(chunks) <= C.MAX_CHUNKS
    assert all(c.startswith("Best apparel ERP\ndesc\n") and len(c) <= C.CHUNK_CHARS + 40 for c in chunks)
    assert "line 0 " in chunks[0] and "line 240 " in "".join(chunks)
    assert C.page_chunks("T", "", "") == ["T"] and C.page_chunks("", "", "") == []


def _groups(*names):
    return {C.norm(n): [(SimpleNamespace(name=n), None)] for n in names}


def test_merge_variants():
    g = C.merge_variants(_groups("Aptean", "Aptean Exenta", "Aptean Apparel ERP", "BlueCherry", "CGS BlueCherry",
                                 "BlueCherry Suite", "Sync", "Syncron", "Apparel", "Apparel Connect", "SAP", "SAP ECC"))
    assert sorted(m.name for m, _ in g["aptean"]) == ["Aptean", "Aptean Apparel ERP", "Aptean Exenta"]
    assert g["aptean"][0][0].name == "Aptean"                        # the root's own mention first
    assert sorted(m.name for m, _ in g["bluecherry"]) == ["BlueCherry", "BlueCherry Suite", "CGS BlueCherry"]
    assert "syncron" in g and "sync" in g                           # whole words only
    assert "apparelconnect" in g                                    # an all-generic name absorbs nothing
    assert "sapecc" in g                                            # roots need >= 4 letters


def test_category_phrase():
    assert C.category_phrase("Apparel, footwear and home goods ERP software") == "Apparel, footwear and home goods ERP"
    assert C.category_phrase("Pet care platform") == "Pet care"
    assert C.category_phrase("") == "business"
