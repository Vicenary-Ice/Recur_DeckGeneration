"""Agent 1: thesis quality. Recur's own deck is out of the writer prompt, copied and stock language is caught,
and the thesis must make an investment case (why-now quote, durability quote, a lever the gap signals allow)."""
from __future__ import annotations

import json

import httpx
import pytest

from recur_deck.checks import copy as K
from recur_deck.config import Settings
from recur_deck.evidence import EvidenceStore
from recur_deck.models import DeckContent
from recur_deck.stages import profile as s_profile
from recur_deck.stages import write as s_write
from recur_deck.stages.base import PROMPTS, Ctx
from tests.fake_world import FIX, FakeClaude, World


def deck(**patch) -> dict:
    d = json.loads((FIX / "ai2000/content.json").read_text())
    for path, val in patch.items():
        node = d
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[int(k)] if k.isdigit() else node[k]
        node[int(keys[-1]) if keys[-1].isdigit() else keys[-1]] = val
    return d


def content(d: dict) -> DeckContent:
    return DeckContent.model_validate(d, context={"min_competitors": 0})


def store(*items: tuple[str, str]) -> EvidenceStore:
    """(kind, quote) about the target -> e1, e2, ..."""
    ev = EvidenceStore()
    for i, (kind, q) in enumerate(items):
        assert ev.add(f"https://target.example/{i}", q, "target", kind, "web", text=q)
    return ev


# ---------------------------------------------------------------- A. Recur's deck is out of the prompt
def test_prompt_has_no_reference_text():
    p = s_write.writer_system_prompt()
    grams = K.ngrams(K.tokens(p, [K.reference()["company"]]))
    shared = grams & K.reference_ngrams()
    assert not shared, sorted(shared)[:5]
    assert "GOLD EXAMPLE" not in p and not (PROMPTS / "style_examples" / "00_recur_ai2000.md").exists()
    assert "**Match the register of the style examples, never their sentences.**" in p
    assert len(sorted((PROMPTS / "style_examples").glob("*.md"))) == 3


def test_style_examples_distinct():
    lines = {p.stem: K.example_lines(p.read_text()) for p in sorted((PROMPTS / "style_examples").glob("*.md"))}
    assert len(lines) == 3 and all(len(v) == 10 for v in lines.values())   # 6 thesis lines, subtitle, take + 2
    names = list(lines)
    for i, a in enumerate(names):
        ga = {g for ln in lines[a] for g in K.ngrams(K.tokens(ln))}
        oa = {tuple(K.tokens(ln)[:2]) for ln in lines[a]}
        assert not ga & K.reference_ngrams(), a
        assert not lines[a][4].lower().startswith("help"), a                # point 03 never opens with "Help"
        for b in names[i + 1:]:
            gb = {g for ln in lines[b] for g in K.ngrams(K.tokens(ln))}
            ob = {tuple(K.tokens(ln)[:2]) for ln in lines[b]}
            assert not ga & gb, (a, b, sorted(ga & gb)[:3])                 # no shared 5-word sequence
            assert not oa & ob, (a, b, oa & ob)                             # no shared opening two words
    levers = {p.stem: p.read_text() for p in (PROMPTS / "style_examples").glob("*.md")}
    assert {"ai_native_gtm", "ai_modernization", "built_the_bench"} <= {
        lv for t in levers.values() for lv in K.LEVERS if f"lever {lv}" in t}  # three different levers shown


# ---------------------------------------------------------------- B. copy lint
def test_copy_lint_counts_company_names_as_one_token():
    c = content(deck(**{"thesis.2.headline": "Help Acme grow with outbound sales and AI"}))
    hits = {i.field: i.message for i in K.copied_spans(c)}
    assert "thesis[2].headline" in hits and "Recur's own deck" in hits["thesis[2].headline"]
    assert "stock phrase 'grow with outbound sales and AI'" in hits["thesis[2].headline"]
    assert K.copied_spans(content(deck())) == []                            # the fixture deck is its own words
    ex = content(deck(**{"thesis.1.subline": "Batch records, kegs and tax filings share the ledger"}))
    assert "style example b_brewery_erp" in K.copied_spans(ex)[0].message
    assert K.tokens("Help FDM4 America grow", ["FDM4", "FDM4 America"]) == ["help", "<co>", "grow"]


def test_repetitive_across_decks(tmp_path):
    a = content(deck(**{"thesis.0.headline": "Decorators are moving to one system for orders"}))
    other = K.history_entry("fdm4-run", content(deck(**{"company.display_name": "Acme", "company.domain": "acme.example",
                                                        "thesis.0.headline": "Decorators are moving to one system for orders",
                                                        "thesis.1.headline": "Acme keeps twenty years of shop records",
                                                        "thesis.2.headline": "Resellers bring in every new Acme customer"})))
    issues = K.repetitive_issues(a, [other])
    assert [(i.code, i.field) for i in issues] == [("repetitive_across_decks", "thesis[0].headline")]
    same_company = K.history_entry("ai2000-old", a)
    assert K.repetitive_issues(a, [same_company]) == []                     # the same company may repeat itself
    # history: the jsonl file plus older run folders' content.json, never the run itself
    runs = tmp_path / "runs"
    (runs / "fdm4-run").mkdir(parents=True)
    (runs / "fdm4-run" / "content.json").write_text(json.dumps(content(deck(**{
        "company.display_name": "Acme", "company.domain": "acme.example"})).model_dump()))
    (runs / "now").mkdir()
    (runs / "now" / "content.json").write_text(json.dumps(a.model_dump()))
    (runs / K.HISTORY_NAME).write_text(json.dumps(K.history_entry("x-run", a)) + "\n")
    assert sorted(e["run"] for e in K.load_history(runs, "now")) == ["fdm4-run", "x-run"]


def test_thesis_history_is_appended_after_a_live_run(tmp_path, monkeypatch):
    from recur_deck import pipeline
    from tests.test_pipeline import fake_render, settings
    fake_render(monkeypatch)
    world = World()
    s = settings(tmp_path)
    res = pipeline.run("Ai2000", settings=s, out_dir=None, transport=httpx.MockTransport(world.handler),
                       claude_client=FakeClaude(world), non_interactive=True)
    rows = [json.loads(x) for x in (s.runs_dir / K.HISTORY_NAME).read_text().splitlines()]
    assert [r["run"] for r in rows] == [res.run_dir.name] and rows[0]["domain"] == "a2000software.com"


# ---------------------------------------------------------------- C. the investment case
def test_why_now_must_be_about_the_customers():
    ev = store(("news", "Retailers now fine late EDI shipments."))
    assert K.why_now_issues(content(deck(**{"thesis.0.evidence_ids": ["e1"]})), ev) == []
    bad = content(deck(**{"thesis.0.subline": "Fashion ERP vendors are adding AI to daily orders",
                          "thesis.0.evidence_ids": ["e1"]}))
    assert [i.field for i in K.why_now_issues(bad, ev)] == ["thesis[0].subline"]
    product_only = store(("product", "Ai2000 covers PLM."))
    assert [i.field for i in K.why_now_issues(content(deck(**{"thesis.0.evidence_ids": ["e1"]})), product_only)] == [
        "thesis[0].headline"]


def test_durability_needs_a_tenure_customers_or_switching_quote():
    ev = store(("product", "Ai2000 covers PLM, orders and EDI."), ("tenure", "Serving apparel brands since 1985."))
    assert [i.code for i in K.durability_issues(content(deck(**{"thesis.1.evidence_ids": ["e1"]})), ev)] == ["durability_missing"]
    assert K.durability_issues(content(deck(**{"thesis.1.evidence_ids": ["e1", "e2"]})), ev) == []


def test_lever_mapping():
    legacy_with_sales = store(("tech", "The software installs on your Windows server on-premise."),
                              ("gtm", "Talk to our sales team to book a demo."))
    assert set(K.allowed_levers(legacy_with_sales)) == {"ai_modernization", "ai_rebuild"}
    assert "ai_native_gtm" in K.allowed_levers(store(("tech", "On-premise ERP for brewers.")))   # no gtm signals
    assert set(K.allowed_levers(store(("gtm", "Sold through our network of certified resellers.")))) == {"ai_native_gtm"}
    founder = store(("team", "Founded by Jane Doe, who still leads product."), ("gtm", "Request a demo"))
    assert set(K.allowed_levers(founder)) == {"built_the_bench"}
    hired = store(("team", "Founded by Jane Doe."), ("team", "John Roe, VP of Sales"), ("gtm", "Request a demo"))
    assert K.allowed_levers(hired) == {"ai_native_gtm": {"why": "no clear gap: add to the sales motion that exists and say what it is",
                                                         "evidence_ids": ["e3"], "default": True}}
    # lever_issues: a lever the signals don't allow, or gap evidence of the wrong kind
    d = deck(lever="ai_native_gtm", lever_gap_evidence_ids=["e2"])
    issues = K.lever_issues(content(d), legacy_with_sales)
    assert issues and "the signals don't support ai_native_gtm" in issues[0].message
    d = deck(lever="ai_modernization", lever_gap_evidence_ids=["e2"])
    assert "must be a tech quote" in K.lever_issues(content(d), legacy_with_sales)[0].message
    assert K.lever_issues(content(deck(lever="ai_modernization", lever_gap_evidence_ids=["e1"])), legacy_with_sales) == []
    help_ = deck(**{"thesis.2.headline": "Help Ai2000 reach more brands"})
    assert K.lever_issues(content(help_), None)[0].field == "thesis[2].headline"


def _writer_ctx(tmp_path, claude, kinds=(("segment", "Apparel and footwear brands."), ("tenure", "Since 1985."))):
    s = Settings(runs_dir=tmp_path, anthropic_api_key="k")
    ctx = Ctx.create(s, tmp_path / "w", claude_client=claude)
    for kind, q in kinds:
        ctx.evidence.add("https://a2000software.com/about", q, "target", kind, "web", text=q)
    return ctx


def test_enforce_rejects_uncited_durability(tmp_path):
    good = json.loads((FIX / "ai2000/content.json").read_text())
    good["thesis"][0]["evidence_ids"], good["thesis"][1]["evidence_ids"] = ["e1"], ["e2"]
    good["thesis"][2]["evidence_ids"], good["lever_gap_evidence_ids"] = ["e1"], ["e1"]
    first = json.loads(json.dumps(good))
    first["thesis"][1]["evidence_ids"] = ["e1"]                            # a segment quote, not durability
    seen = []

    def writer(user):
        seen.append(user)
        return first if len(seen) == 1 else good

    claude = FakeClaude(World(), {"write_deck": writer})
    ctx = _writer_ctx(tmp_path, claude)
    comps = {"competitors": [{"name": c["name"], "domain": c["domain"], "segment": "", "scope": "", "channels": ["a"],
                              "head_to_head": False, "relevance": 0.9, "evidence_ids": []} for c in good["market"]["competitors"]]}
    entity = {"display_name": "Ai2000", "domain": "a2000software.com", "aliases": ["A2000", "GCS Software"]}
    out = s_write.write_deck(ctx, entity, {}, comps)
    assert claude.calls.count("write_deck") == 2
    assert "<problems_with_previous_draft>" in seen[1] and "point 02 cites no durability fact" in seen[1]
    assert out.thesis[1].evidence_ids == ["e2"]
    assert "<allowed_levers>" in seen[0] and "ai_native_gtm" in seen[0]


def test_profile_records_durability_and_gap_kinds(tmp_path):
    world = World()
    s = Settings(runs_dir=tmp_path, firecrawl_rpm=1000, anthropic_api_key="k", firecrawl_api_key="f")
    ctx = Ctx.create(s, tmp_path / "p", transport=httpx.MockTransport(world.handler), claude_client=FakeClaude(world))
    entity = {"display_name": "Ai2000", "aliases": ["A2000"], "domain": "a2000software.com",
              "vertical": "Apparel ERP software", "one_liner": "Apparel ERP"}
    try:
        prof = s_profile.build(ctx, entity)
    finally:
        ctx.http.close()
    kinds = {ctx.evidence.get(f["evidence_id"]).kind for k in ("tenure", "customers", "switching") for f in prof[k]}
    assert kinds == {"tenure", "customers", "switching"}
    assert prof["site_urls"] and all(u.startswith("https://a2000software.com") for u in prof["site_urls"])
    assert prof["gtm_signals"] == [] and prof["facts_total"] == prof["facts_kept"] + 1


@pytest.mark.parametrize("run", ["ai2000-20261109-163637-deck", "fdm4-20261109-214123", "uphance-20261109-215758"])
def test_live_fixture_decks_are_flagged(run):
    """What the three live decks would be flagged for now (the report lists the messages)."""
    from tests.test_known_defects import LIVE
    d = LIVE / run
    c = DeckContent.model_validate_json((d / "content.json").read_text(), context={"min_competitors": 0})
    ev = EvidenceStore.load(d / "evidence.jsonl")
    codes = {i.code for i in K.copy_checks(c, {}, {}, {"evidence": ev})}
    assert {"copied_language", "durability_missing", "lever_unsupported"} <= codes
