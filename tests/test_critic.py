"""Agent 5: the founder-eyes critic. Inputs, mail_ready semantics, the round cap and finding -> fix routing.
Fake Claude only."""
from __future__ import annotations

import json

import httpx

from recur_deck import hooks, market_graph
from recur_deck.config import Settings
from recur_deck.stages import critic as C
from recur_deck.stages import write as s_write
from recur_deck.stages.base import Ctx
from tests.fake_world import FakeClaude, World
from tests.test_known_defects import BASELINE, LIVE, UPHANCE, content, evidence, manifest

PNGS = [BASELINE / f"slide{i}.png" for i in (1, 2, 3)]
SECRET = "sk-ant-SECRET-critic-123456"


def ctx_with(tmp_path, answer=None):
    claude = FakeClaude(World(), {"critic": answer} if answer else None)
    seen = []
    orig = claude.messages.stream
    claude.messages.stream = lambda **kw: (seen.append(kw), orig(**kw))[1]
    s = Settings(runs_dir=tmp_path / "runs", anthropic_api_key=SECRET)
    return Ctx.create(s, s.runs_dir / "crit", claude_client=claude), claude, seen


def finding(**kw) -> dict:
    return {"persona": "founder", "slide": 3, "target": "market.competitors", "category": "other", "severity": "minor",
            "explanation": "x", "suggested_fix": "none", **kw}


def test_critic_inputs(tmp_path):
    ctx, claude, seen = ctx_with(tmp_path)
    comps = json.loads((UPHANCE / "competitors.json").read_text())
    C.review(ctx, content(UPHANCE), PNGS, manifest(UPHANCE), evidence(UPHANCE),
             market_graph.from_runs([LIVE / "ai2000-20261109-163637-deck"]), comps=comps,
             logos=json.loads((UPHANCE / "logo_metrics.json").read_text()))
    assert claude.calls == ["critic"]
    kw = seen[0]
    assert kw["model"] == ctx.settings.model_writer and kw["output_config"]["effort"] == "high"
    parts = kw["messages"][0]["content"]
    assert sum(p["type"] == "image" for p in parts) == 2                 # slides 2 and 3 only
    text = parts[-1]["text"]
    assert "<cited_evidence>" in text and "Aptean Distribution ERP integrates supply chain management" in text
    assert "<recur_reference_text>" in text and "is set to win as apparel brands replace legacy ERPs" in text
    assert "<earlier_placements>" in text and "<competitors>" in text and '"dpi": 106' in text
    assert SECRET not in json.dumps(kw, default=str)                      # no API key anywhere in the request
    writer = s_write.writer_system_prompt()                               # ... and the writer never sees Recur's lines
    assert not any(line in writer for line in C.reference_text()["slide2"] + C.reference_text()["slide3"])


def test_mail_ready_semantics(tmp_path):
    major = {"findings": [finding(severity="major")], "slide_grades": {"slide2": "B", "slide3": "C"}, "mail_ready": True}
    ctx, _, _ = ctx_with(tmp_path / "a", lambda user: major)
    v = C.review(ctx, content(UPHANCE), PNGS, manifest(UPHANCE), evidence(UPHANCE), None)
    assert v.mail_ready is False and len(v.majors()) == 1                  # forced, whatever the model said
    minor = {"findings": [finding()], "slide_grades": {"slide2": "A-", "slide3": "B+"}, "mail_ready": True}
    ctx, _, _ = ctx_with(tmp_path / "b", lambda user: minor)
    assert C.review(ctx, content(UPHANCE), PNGS, manifest(UPHANCE), evidence(UPHANCE), None).mail_ready is True


def test_round_cap(tmp_path):
    ctx, claude, _ = ctx_with(tmp_path)
    for i in range(3):  # each QA round has a changed deck (an unchanged one would be served from the cache)
        c = content(UPHANCE)
        c.thesis[0].headline = f"Round {i} headline"
        hooks.fire("after_vision", ctx=ctx, content=c, pngs=PNGS, manifest=manifest(UPHANCE))
    assert claude.calls.count("critic") == C.MAX_CALLS == 2
    assert ctx.report["critic"]["calls"] == 2 and len(ctx.report["critic"]["verdicts"]) == 2


def test_to_fixes_routing():
    v = C.CriticVerdict.model_validate({"findings": [
        finding(target="thesis[1]", category="copied_language", slide=2),
        finding(target="market.subtitle", category="unsupported_claim"),
        finding(target="market.our_take.points[0]", category="unsupported_claim"),
        finding(target="market.competitors[2]", category="disputed_placement"),
        finding(target="market.competitors", category="missing_competitor", explanation="Ai2000 is missing"),
        finding(target="Sync", category="illegible_logo"),
        finding(target="thesis[2].subline", category="negative_tone", slide=2),
        finding(target="market.x_axis", category="other", suggested_fix="relayout")],
        "slide_grades": {"slide2": "B", "slide3": "C"}, "mail_ready": False})
    routes = [(f["fix"], f["field"]) for f in C.to_fixes(v)]
    assert routes == [("rewrite_copy", "thesis[1].headline"), ("rewrite_copy", "market.subtitle"),
                      ("regenerate_market", "market.our_take.points[0]"), ("regenerate_market", "market.competitors[2]"),
                      ("add_competitor", "market.competitors"), ("swap_logo", "logo:Sync"),
                      ("rewrite_copy", "thesis[2].subline"), ("relayout", "market.x_axis")]
    assert C.to_fixes(v)[4]["reason"].startswith("critic (founder, missing_competitor): Ai2000 is missing")


def test_critic_runs_once_after_vision_passes(tmp_path, monkeypatch):
    from recur_deck import pipeline
    from tests.test_pipeline import fake_render, settings
    fake_render(monkeypatch)
    world = World()
    claude = FakeClaude(world)
    res = pipeline.run("Ai2000", settings=settings(tmp_path), out_dir=None, transport=httpx.MockTransport(world.handler),
                       claude_client=claude, non_interactive=True)
    assert res.exit_code in (0, 1), res.report.get("error")
    assert claude.calls.count("critic") == 1 and claude.calls.index("critic") > claude.calls.index("vision_qa")
