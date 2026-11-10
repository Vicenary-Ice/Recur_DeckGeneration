"""Agent 3: market-map plausibility (ordinal axes, positive evidence, source diversity, plausibility, history)
and the batched Haiku placement audit. Synthetic decks + fake Claude; no network."""
from __future__ import annotations

import json

import anthropic
import pytest

from recur_deck.axes import AXIS_KINDS, infer_kind, read_side
from recur_deck.checks import market as M
from recur_deck.config import Settings
from recur_deck.evidence import EvidenceStore
from recur_deck.models import DeckContent
from recur_deck.stages import placement as P
from recur_deck.stages import write as s_write
from recur_deck.stages.base import Ctx
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


def store(*quotes: tuple[str, str, str]) -> EvidenceStore:
    """(url, about, quote) -> ids e1, e2, ... in order."""
    ev = EvidenceStore()
    for url, about, q in quotes:
        assert ev.add(url, q, about, "scope", "web", text=q)
    return ev


# ---------------------------------------------------------------- A. ordinal axes
def test_axis_kinds():
    for kind, spec in AXIS_KINDS.items():
        assert spec["low"] and spec["high"] and spec["titles"], kind      # every kind has a defined direction
        if spec["derive"]:
            assert spec["high_cues"] and spec["low_cues"], kind
    assert infer_kind("Customer size") == "customer_size" and infer_kind("Agency size") == "customer_size"
    assert infer_kind("Industry focus") == "industry_depth" and infer_kind("Core buyer") is None
    assert M.axis_issues(content(deck())) == []
    upside_down = deck(**{"market.y_axis.low_label": "Large\nbrands", "market.y_axis.high_label": "Small\nbrands"})
    issues = M.axis_issues(content(upside_down))
    assert [(i.code, i.field) for i in issues] == [("axis_not_ordinal", "market.y_axis")] and "upside down" in issues[0].message
    same = deck(**{"market.x_axis.kind": "customer_size"})
    assert any("both axes measure customer_size" in i.message for i in M.axis_issues(content(same)))
    unknown = deck(**{"market.x_axis.kind": None})
    assert M.axis_issues(content(unknown))[0].field == "market.x_axis"
    assert all(i.fix == "regenerate_market" and i.severity == "error" for i in M.axis_issues(content(unknown)))


def test_writer_schema_requires_the_new_fields_and_old_content_still_loads():
    s = anthropic.transform_schema(DeckContent)["$defs"]
    assert "kind" in s["Axis"]["required"]
    assert {"x_evidence_ids", "y_evidence_ids"} <= set(s["Competitor"]["required"])
    assert "point_evidence_ids" in s["OurTake"]["required"]
    old = json.loads((FIX / "ai2000/content.json").read_text())
    for ax in ("x_axis", "y_axis"):
        old["market"][ax].pop("kind")
    for c in old["market"]["competitors"]:
        c.pop("x_evidence_ids"), c.pop("y_evidence_ids")
    old["market"]["our_take"].pop("point_evidence_ids")
    c = content(old)                                                       # a content.json from before Agent 3
    assert (c.market.x_axis.kind, c.market.y_axis.kind) == ("product_scope", "customer_size")
    assert c.market.competitors[0].x_evidence_ids == [] and c.market.our_take.point_evidence_ids == []


def test_writer_prompt_has_the_market_rules():
    p = s_write.writer_system_prompt()
    assert all(k in p for k in AXIS_KINDS) and "Not mentioned is not evidence" in p
    assert "x_evidence_ids" in p and "point_evidence_ids" in p and "40%" in p


# ---------------------------------------------------------------- B. positive evidence
def test_placement_needs_positive_evidence():
    ev = store(*[(f"https://{c['domain']}/", c["domain"], f"{c['name']} is apparel software.")
                 for c in deck()["market"]["competitors"]], ("https://x.com/a", "x.com", "Tools for small labels."),
               ("https://x.com/b", "x.com", "Books in QuickBooks."))
    assert M.placement_issues(content(deck()), ev) == []
    bad = deck(**{"market.competitors.0.rationale": "Enterprise suite, with no financials cited",
                  "market.competitors.1.x_evidence_ids": [], "market.competitors.2.y_evidence_ids": ["e99"],
                  "market.our_take.point_evidence_ids": [["e8"]]})
    issues = {i.field: i.message for i in M.placement_issues(content(bad), ev)}
    assert "not mentioned is not evidence" in issues["market.competitors[0]"]
    assert "no quote cited for its x placement" in issues["market.competitors[1]"]
    assert "unknown ids ['e99']" in issues["market.competitors[2]"]
    assert "market.our_take.points[1]" in issues and "market.our_take.points[0]" not in issues


def test_own_quote_that_reads_the_other_way_is_flagged():
    d = deck()
    big = next(i for i, c in enumerate(d["market"]["competitors"]) if c["name"] == "AIMS360")
    d["market"]["competitors"][big]["y"] = "top"                          # "Large brands"
    ev = store(*[(f"https://{c['domain']}/", c["domain"],
                  "AIMS360 is ERP for small and growing fashion brands." if c["name"] == "AIMS360" else f"{c['name']} ERP.")
                 for c in d["market"]["competitors"]], ("https://x.com/a", "x.com", "a"), ("https://x.com/b", "x.com", "b"))
    d["market"]["competitors"][big]["y_evidence_ids"] = [f"e{big + 1}"]
    issues = M.placement_issues(content(d), ev)
    assert any(i.field == f"market.competitors[{big}]" and "reads the other way" in i.message for i in issues)


# ---------------------------------------------------------------- B. source diversity
def test_diversity():
    d = deck()
    comps = d["market"]["competitors"]
    quotes = []
    for i, c in enumerate(comps):  # 6 of the 7 competitors cited from one rival's blog (60%+ of slide 3)
        url = f"https://{c['domain']}/" if i == 0 else f"https://www.aims360.com/blog/best-erp#{i}"
        quotes.append((url, c["domain"], f"{c['name']} is listed here for apparel brands {i}."))
    quotes += [("https://capterra.com/a", "x", "Point tools a."), ("https://capterra.com/b", "x", "Point tools b.")]
    issues = M.diversity_issues(content(d), store(*quotes))
    share = [i for i in issues if i.field == "market"]
    assert share and "aims360.com supplies" in share[0].message and share[0].code == "evidence_not_diverse"
    rival_only = {i.field for i in issues if i.field.startswith("market.competitors")}
    aims = next(i for i, c in enumerate(comps) if c["domain"] == "aims360.com")
    assert f"market.competitors[{aims}]" not in rival_only                 # AIMS360 quoted from its own site
    assert len(rival_only) == len(comps) - 2                               # everyone else only from a rival
    assert M.diversity_issues(content(d), None) == []


# ---------------------------------------------------------------- C. plausibility
def test_target_alone_and_empty_cells():
    d = deck()
    for c in d["market"]["competitors"]:
        if (c["x"], c["y"]) == ("right", "top"):
            c["x"] = "left"
    codes = [i.code for i in M.plausibility_issues(content(d), {})]
    assert codes == ["target_alone"]
    # every competitor's own quote puts it low on customer size: alone at the top is then believable
    ev = store(*[(f"https://{c['domain']}/", c["domain"], f"{c['name']} is for small brands.") for c in d["market"]["competitors"]])
    assert M.plausibility_issues(content(d), {"evidence": ev}) == []
    two_empty = deck()  # 3 competitors top-left, 2 bottom-left: the whole right half holds only the target
    two_empty["market"]["competitors"] = two_empty["market"]["competitors"][:5]
    for i, c in enumerate(two_empty["market"]["competitors"]):
        c["x"], c["y"] = "left", ("top" if i < 3 else "bottom")
    assert {i.code for i in M.plausibility_issues(content(two_empty), {})} == {"target_alone", "empty_cells"}


def test_segment_missing():
    d = deck(**{"thesis.0.headline": "Apparel decorators want one system for decorated orders"})
    comps = {"segments_searched": ["apparel decorators"], "competitors": []}
    issues = M.plausibility_issues(content(d), {"comps": comps})
    assert [i.code for i in issues] == ["segment_missing"] and "add a competitor from that segment" in issues[0].message
    comps["competitors"] = [{"domain": "fdm4.com", "segments": ["apparel decorators"]}]
    assert M.plausibility_issues(content(d), {"comps": comps}) == []
    assert M.plausibility_issues(content(deck()), {"comps": {"segments_searched": ["apparel decorators"]}}) == []


# ---------------------------------------------------------------- D. consistency with history
class FakeGraph:
    """Hand-built: one earlier placement of Aptean, as Agent 2's graph would return it."""

    def __init__(self, side="high", quotes=("Aptean Apparel ERP is built for the fashion industry.",), facts=()):
        self.side, self.quotes, self.facts = side, list(quotes), list(facts)

    def placements(self, domain):
        if domain != "aptean.com":
            return []
        return [{"run": "earlier", "target": "x.com", "axis_kind": "customer_size", "axis_title": "Customer size",
                 "side": self.side, "label": "Large brands", "evidence_ids": ["e3"], "quotes": self.quotes,
                 "derived": False}]

    def company(self, domain):
        return {"facts": [{"quote": q, "run": "earlier"} for q in self.quotes + self.facts]}


def test_consistency_with_a_fake_graph():
    d = deck()
    apt = next(i for i, c in enumerate(d["market"]["competitors"]) if c["name"] == "Aptean")
    assert M.consistency_issues(content(d), FakeGraph()) == []             # same side as before
    d["market"]["competitors"][apt]["y"] = "bottom"                       # "Small & mid brands"
    issues = M.consistency_issues(content(d), FakeGraph())
    assert [(i.code, i.field) for i in issues] == [("placement_contradicts_history", f"market.competitors[{apt}]")]
    assert "built for the fashion industry" in issues[0].message           # the earlier quote is the hint
    # a NEW quote that supports the change resolves it; an old one does not
    ev = store(*[(f"https://{c['domain']}/", c["domain"],
                  "Aptean Starter is ERP for small brands." if c["name"] == "Aptean" else f"{c['name']} ERP.")
                 for c in d["market"]["competitors"]])
    d["market"]["competitors"][apt]["y_evidence_ids"] = [f"e{apt + 1}"]
    assert M.consistency_issues(content(d), FakeGraph(), ev) == []
    assert M.consistency_issues(content(d), FakeGraph(facts=("Aptean Starter is ERP for small brands.",)), ev)
    # an earlier placement contradicted by its own quotes is not history
    assert M.consistency_issues(content(d), FakeGraph(quotes=("Aptean is for small brands.",))) == []
    assert M.consistency_issues(content(d), None) == []


def test_relayout_never_moves_a_logo_against_its_evidence():
    c = content(deck())
    up = next(x for x in c.market.competitors if x.name == "Uphance")
    ev = store(*[(f"https://{x.domain}/", x.domain, "Uphance is ERP for small fashion brands." if x.name == "Uphance"
                  else f"{x.name} ERP.") for x in c.market.competitors])
    assert M.cell_conflicts(c, up, ("left", "top"), ev) is True          # "Large brands"
    assert M.cell_conflicts(c, up, ("right", "bottom"), ev) is False


# ---------------------------------------------------------------- the Haiku audit
def test_audit_batching(tmp_path):
    d = deck()
    n = len(d["market"]["competitors"])
    ev = store(*[(f"https://{c['domain']}/", c["domain"], f"{c['name']} is ERP for apparel brands.")
                 for c in d["market"]["competitors"]], ("https://x.com/a", "x.com", "Point tools stop at orders."),
               ("https://x.com/b", "x.com", "Books in QuickBooks."))
    d["market"]["competitors"][1]["x_evidence_ids"] = []                    # no quote: never sent, the check flags it
    seen = {}

    def answer(user):
        items = json.loads(user.split("<items>\n", 1)[1].split("\n</items>", 1)[0])
        seen["ids"] = [it["item_id"] for it in items]
        verdict = {"c0.x": "contradicts", "c2.y": "insufficient", "p1": "insufficient"}
        return {"verdicts": [{"item_id": it["item_id"], "verdict": verdict.get(it["item_id"], "supports"),
                              "quote": it["quotes"][0]["quote"] if it["item_id"] == "c0.x" else "", "note": "n"}
                             for it in items]}

    claude = FakeClaude(World(), {"placement_audit": answer})
    ctx = Ctx.create(Settings(runs_dir=tmp_path), tmp_path / "audit", claude_client=claude)
    issues = P.audit(ctx, content(d), ev)
    assert claude.calls == ["placement_audit"]                              # one call for the whole deck
    assert len(seen["ids"]) == 2 * n - 1 + 2 and "c1.x" not in seen["ids"]
    by = {i.field: i.message for i in issues}
    assert set(by) == {"market.competitors[0]", "market.competitors[2]", "market.our_take.points[1]"}
    assert "contradict it" in by["market.competitors[0]"] and "do not support it" in by["market.competitors[2]"]
    assert all(i.code == "placement_unsupported" and i.fix == "regenerate_market" for i in issues)
    assert P.audit(ctx, content(d), ev) and claude.calls == ["placement_audit"]   # second time: from the cache


def test_audit_hook_returns_problem_strings(tmp_path):
    from recur_deck import hooks
    ctx = Ctx.create(Settings(runs_dir=tmp_path), tmp_path / "h", claude_client=FakeClaude(World()))
    assert P.audit_after_write in hooks.handlers("after_write")
    assert P.audit_after_write(ctx, content(deck())) == []                  # nothing cited in an empty store
