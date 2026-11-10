"""B: `recur-deck "<name>" --offline [--no-vision]` runs the whole pipeline from runs/_cache. A cache miss stops
the run and names the stage and the call. The 'live' leg here is the simulated web, never the real one."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx

from recur_deck import cli, pipeline
from recur_deck.cache import cache_key
from recur_deck.config import Settings
from tests.fake_world import FakeClaude, World
from tests.test_pipeline import fake_render, settings


def simulated_live_run(tmp_path, monkeypatch) -> tuple[pipeline.Result, World, Settings]:
    fake_render(monkeypatch)
    world = World()
    s = settings(tmp_path)
    res = pipeline.run("Ai2000", settings=s, out_dir=None, transport=httpx.MockTransport(world.handler),
                       claude_client=FakeClaude(world), non_interactive=True)
    assert res.exit_code in (0, 1), res.report.get("error")
    return res, world, s


def offline_settings(s: Settings, **kw) -> Settings:
    """What the CLI builds for --offline: no keys at all, cache only."""
    return replace(s, offline=True, anthropic_api_key=None, firecrawl_api_key=None, **kw)


def offline_run(s: Settings, out_dir=None) -> pipeline.Result:
    # no transport and no fake Claude: a call that slipped past the cache would hit the (blocked) real network
    return pipeline.run("Ai2000", settings=s, out_dir=out_dir, non_interactive=True)


def test_offline_full_run_from_cache_no_vision(tmp_path, monkeypatch, net_guard):
    live, _, s = simulated_live_run(tmp_path, monkeypatch)
    res = offline_run(offline_settings(s, vision_qa=False), out_dir=tmp_path / "out")
    rep = res.report
    assert res.exit_code in (0, 1) and rep["status"] in ("pass", "pass_with_warnings"), rep.get("error")
    assert rep["deliverables"]["pdf"] is None and (tmp_path / "out" / "Recur_x_Ai2000.pptx").exists()
    assert any("--no-vision" in w for w in rep["warnings"])
    assert rep["cost_usd"] == 0 and all(t["calls"] == t["cached"] for t in rep["cost_by_service"].values())
    assert [c["name"] for c in rep["competitors"]] == [c["name"] for c in live.report["competitors"]]
    assert rep["logos"] == live.report["logos"]
    assert not net_guard.attempts


def test_recorded_failures_replay_offline(tmp_path, monkeypatch):
    """The live run's wrong domain guess (uphance.io -> 404) is a recorded failure, not an offline miss."""
    _, world, s = simulated_live_run(tmp_path, monkeypatch)
    assert ("uphance.io", "/") in world.calls
    assert list((s.cache_dir / "download_err").rglob("*.json"))
    res = offline_run(offline_settings(s, vision_qa=False))
    assert res.report["status"] != "offline_cache_miss", res.report.get("error")


def test_offline_empty_cache_stops_at_entity(tmp_path, net_guard):
    s = offline_settings(settings(tmp_path))
    res = offline_run(s)
    rep = res.report
    assert res.exit_code == 2 and rep["status"] == "offline_cache_miss" and rep["failed_stage"] == "entity"
    assert "offline run stopped in stage 'entity'" in rep["error"]
    assert "firecrawl: POST https://api.firecrawl.dev/v2/search" in rep["error"] and "none sent" in rep["error"]
    assert rep["offline_misses"] and not net_guard.attempts
    assert not (Path(rep["run_dir"]) / "Recur_x_Ai2000.pptx").exists()


def test_offline_miss_in_competitors_names_stage_and_call(tmp_path, monkeypatch):
    _, world, s = simulated_live_run(tmp_path, monkeypatch)
    body = next(b for b in world.searches if b["query"] == '"Ai2000" competitors')
    key = cache_key("POST", "https://api.firecrawl.dev/v2/search", body)
    (s.cache_dir / "firecrawl" / key[:2] / f"{key}.json").unlink()   # as if the live run never made this search
    res = offline_run(offline_settings(s, vision_qa=False))
    rep = res.report
    assert rep["status"] == "offline_cache_miss" and rep["failed_stage"] == "competitors"
    assert "query='\"Ai2000\" competitors'" in rep["error"]
    assert "fc-" not in rep["error"] and "SECRET" not in rep["error"]


def test_offline_vision_miss_suggests_no_vision(tmp_path, monkeypatch):
    _, _, s = simulated_live_run(tmp_path, monkeypatch)
    dropped = 0
    for p in (s.cache_dir / "anthropic").rglob("*.json"):           # drop the cached vision verdicts
        if '"passed"' in json.loads(p.read_text())["text"]:
            p.unlink()
            dropped += 1
    assert dropped
    res = offline_run(offline_settings(s))
    rep = res.report
    assert rep["status"] == "offline_cache_miss" and rep["failed_stage"] == "qa"
    assert "vision_qa" in rep["error"] and "--no-vision" in rep["error"]
    assert not rep.get("delivered_to")


def test_cli_offline_drops_keys_and_sets_flags(monkeypatch):
    seen = {}

    def fake_run(name, **kw):
        seen.update(kw)
        return pipeline.Result(2, None, {"status": "offline_cache_miss", "error": "stop"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-used")
    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["Ai2000", "--offline", "--no-vision", "--non-interactive"]) == 2
    s = seen["settings"]
    assert s.offline and not s.vision_qa and s.anthropic_api_key is None and s.firecrawl_api_key is None
