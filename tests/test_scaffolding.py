"""Agent 0 scaffolding: the split writer prompt renders exactly as before, the check registry, the hooks and the
market-graph interface."""
from __future__ import annotations

from pathlib import Path

from recur_deck import checks, hooks, market_graph, qa
from recur_deck.config import Settings
from recur_deck.models import DeckContent
from recur_deck.stages import write as s_write
from tests.fake_world import FIX

SNAPSHOT = Path(__file__).parent / "snapshots" / "write_deck_system.txt"


def test_writer_prompt_matches_snapshot():
    """The rendered writer system prompt is pinned. When the prompt is changed on purpose, regenerate the
    snapshot from writer_system_prompt() in the same commit and say why in the commit message."""
    assert s_write.writer_system_prompt() == SNAPSHOT.read_text("utf-8")


def test_style_examples_are_read_in_name_order(tmp_path, monkeypatch):
    d = tmp_path / "style_examples"
    d.mkdir()
    (d / "10_b.md").write_text("B\n")
    (d / "02_a.md").write_text("A\n")
    monkeypatch.setattr(s_write, "PROMPTS", tmp_path)
    assert s_write.style_examples() == "A\n\nB\n"


def _content() -> DeckContent:
    return DeckContent.model_validate_json((FIX / "ai2000/content.json").read_text())


def test_registered_checks_run_and_a_broken_check_is_contained(monkeypatch):
    monkeypatch.setattr(checks, "_REGISTRY", [])
    seen = {}

    @checks.register
    def good(content, manifest, logo_metrics, extra):
        seen.update(extra)
        return [qa.Issue("demo", "warn", 3, "market.subtitle", "demo issue", "rewrite_copy")]

    @checks.register
    def broken(content, manifest, logo_metrics, extra):
        raise ValueError("boom")

    out = checks.run_registered(_content(), {"elements": []}, {}, {"run_dir": "x"})
    assert [i.code for i in out] == ["demo", "check_failed"] and seen == {"run_dir": "x"}
    assert out[1].severity == "warn" and "boom" in out[1].message


def test_qa_run_all_appends_registered_checks(monkeypatch):
    monkeypatch.setattr(checks, "_REGISTRY", [])
    monkeypatch.setattr(qa, "check_text", lambda c, p=None: [])
    monkeypatch.setattr(qa, "check_layout", lambda m, l: [])
    monkeypatch.setattr(qa, "check_integrity", lambda p, c: [])
    checks.register(lambda c, m, l, e: [qa.Issue("x", "error", 2, "thesis[0].headline", "m", "rewrite_copy")])
    assert [i.code for i in qa.run_all(Path("unused.pptx"), _content(), {}, {})] == ["x"]


def test_hooks_flatten_results_and_skip_failures(monkeypatch):
    monkeypatch.setattr(hooks, "_handlers", {n: [] for n in hooks.NAMES})
    monkeypatch.setattr(hooks, "_plugins_loaded", True)
    hooks.on("after_write")(lambda ctx, content: ["too long"])
    hooks.on("after_write")(lambda ctx, content: None)

    @hooks.on("after_write")
    def bad(ctx, content):
        raise RuntimeError("nope")
    hooks.on("after_write")(lambda ctx, content: ["no evidence", "copied"])
    assert hooks.fire("after_write", ctx=None, content=None) == ["too long", "no evidence", "copied"]
    assert hooks.fire("after_run", ctx=None, report={}) == []


def test_market_graph_path(tmp_path, monkeypatch):
    s = Settings(runs_dir=tmp_path / "runs")
    monkeypatch.delenv("RECUR_MARKET_GRAPH", raising=False)
    assert market_graph.load(s).path == tmp_path / "runs" / "_market_graph.json"
    monkeypatch.setenv("RECUR_MARKET_GRAPH", str(tmp_path / "g.json"))
    assert market_graph.load(s).path == tmp_path / "g.json"
    g = market_graph.from_runs([])
    assert g.neighbors("a2000software.com") == [] and g.placements("aptean.com") == []
