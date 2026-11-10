"""`recur-deck check runs/<dir> [...]`: re-run every deterministic check on finished runs' saved files.

Free and offline: no Claude or Firecrawl call, no network. It runs the built-in Layer-A text and layout checks and
every registered check (recur_deck/checks: copy, market, logo print) with the run's own evidence, profile,
competitors and logos. History for the cross-run checks is the shared market graph and thesis history, plus every
other run named on the same command line, so `check A B C` judges each deck against the other two.

`--critic --yes` also asks the founder-eyes critic about each run (one live Opus call per run, ~$0.09-0.17); it
needs the run's slide renders (png/slide2.png, png/slide3.png).
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

from . import checks, market_graph, qa
from .checks import copy as copy_checks
from .config import Settings
from .evidence import EvidenceStore
from .models import DeckContent

CRITIC_COST = "~$0.09-0.17"


def _j(p: Path):
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def load_run(run_dir: Path) -> dict:
    rd = Path(run_dir)
    if not (rd / "content.json").exists():
        raise FileNotFoundError(f"{rd} has no content.json (the run stopped before writing a deck)")
    man = next(iter(sorted(rd.glob("Recur_x_*.manifest.json"))), None)
    return {"dir": rd, "content": DeckContent.model_validate_json((rd / "content.json").read_text("utf-8"),
                                                                  context={"min_competitors": 0}),
            "evidence": EvidenceStore.load(rd / "evidence.jsonl"), "entity": _j(rd / "entity.json") or {},
            "profile": _j(rd / "profile.json") or {}, "comps": _j(rd / "competitors.json") or {},
            "logos": _j(rd / "logo_metrics.json") or {}, "manifest": _j(man) if man else None,
            "pptx": next(iter(sorted(rd.glob("Recur_x_*.pptx"))), None), "report": _j(rd / "qa_report.json") or {}}


def graph_with(base: market_graph.MarketGraph, run_dirs: list[Path]) -> market_graph.MarketGraph:
    """The shared graph plus the given runs (in memory; nothing is saved)."""
    g = market_graph.MarketGraph(None, copy.deepcopy(base.data))
    for d in run_dirs:
        market_graph.add_run_dir(g, Path(d), accept_needs_review=True)
    return g


def check_one(run: dict, graph, history: list[dict]) -> list[qa.Issue]:
    from .pipeline import extra_protected
    c, man = run["content"], run["manifest"]
    small = {n: {"ok": v.get("ok", True), "problems": v.get("problems", [])} for n, v in run["logos"].items()}
    issues = qa.check_text(c, extra_protected(run["entity"]))
    if man:
        issues += qa.check_layout(man, {n: small[n] for n in [c.company.display_name, *(x.name for x in c.market.competitors)]
                                        if n in small})
    if run["pptx"] is not None:
        issues += qa.check_integrity(run["pptx"], c)
    extra = {"evidence": run["evidence"], "entity": run["entity"], "graph": graph, "run_dir": run["dir"],
             "profile": run["profile"], "comps": run["comps"], "logos": run["logos"], "thesis_history": history}
    return issues + checks.run_registered(c, man or {"elements": []}, small, extra)


def check_runs(run_dirs: list[Path], settings: Settings | None = None, out=print) -> tuple[int, dict[str, list[qa.Issue]]]:
    """Exit code (1 if any run has an error, 2 if a folder can't be read, else 0) and the issues per run."""
    settings = replace(settings or Settings.load(), offline=True, anthropic_api_key=None, firecrawl_api_key=None)
    dirs = [Path(d) for d in run_dirs]
    runs, code = [], 0
    for d in dirs:
        try:
            runs.append(load_run(d))
        except (FileNotFoundError, ValueError) as e:
            out(f"== {d}: cannot check: {e}")
            code = 2
    graph = graph_with(market_graph.load(settings), [r["dir"] for r in runs])
    result: dict[str, list[qa.Issue]] = {}
    for r in runs:
        history = copy_checks.load_history(r["dir"].parent, r["dir"].name)
        seen = {e["run"] for e in history}
        history += [copy_checks.history_entry(o["dir"].name, o["content"]) for o in runs
                    if o is not r and o["dir"].name not in seen]
        issues = check_one(r, graph, history)
        result[str(r["dir"])] = issues
        errs = [i for i in issues if i.severity == "error"]
        warns = [i for i in issues if i.severity == "warn"]
        was = r["report"].get("status", "?")
        out(f"== {r['dir']} ({r['content'].company.display_name}; status then: {was}): {len(errs)} error(s), "
            f"{len(warns)} warning(s)")
        for i in sorted(issues, key=lambda i: (i.severity != "error", i.slide, i.code, i.field)):
            out(f"  [{i.severity.upper():5}] slide {i.slide} {i.code} {i.field}: {i.message} -> {i.fix}")
        if errs:
            code = max(code, 1)
    return code, result


def critic_runs(run_dirs: list[Path], out=print) -> int:
    """One live critic call per run (needs keys and the run's slide renders)."""
    from .stages import critic
    from .stages.base import Ctx
    settings = Settings.load()
    if not settings.has("anthropic"):
        out("ANTHROPIC_API_KEY is missing: the critic needs it")
        return 2
    runs = [load_run(Path(d)) for d in run_dirs]
    graph = graph_with(market_graph.load(settings), [r["dir"] for r in runs])
    code = 0
    for r in runs:
        pngs = [r["dir"] / "png" / f"slide{i}.png" for i in (1, 2, 3)]
        if not all(p.exists() for p in pngs[1:]):
            out(f"== {r['dir']}: no slide renders in png/ (run without --no-vision to get them); critic skipped")
            code = 2
            continue
        ctx = Ctx.create(settings, r["dir"] / "_critic_check")
        v = critic.review(ctx, r["content"], pngs, r["manifest"] or {"elements": []}, r["evidence"], graph,
                          comps=r["comps"], logos=r["logos"])
        out(f"== {r['dir']}: grades slide 2 {v.slide_grades.slide2}, slide 3 {v.slide_grades.slide3}; "
            f"mail_ready={v.mail_ready}; cost ${ctx.ledger.total_usd()}")
        for f in v.findings:
            out(f"  [{f.severity.upper():5}] {f.persona} slide {f.slide} {f.category} {f.target}: {f.explanation}")
        if v.majors():
            code = max(code, 1)
    return code


# ------------------------------------------------------------------ scorecard (docs/LIVE_TEST_PLAN.md)
def _min_dpi(run: dict) -> tuple[int | None, int | None]:
    """(cover dpi, lowest map dpi) of the placed raster logos; None = vector or typeset only."""
    man, logos = run["manifest"] or {"elements": []}, run["logos"]
    cover, maps = None, []
    for e in man["elements"]:
        lg = logos.get(e.get("label")) or {}
        if e.get("kind") != "logo" or lg.get("typeset") or not lg.get("native_w") or not e.get("w"):
            continue
        dpi = round(lg["native_w"] / e["w"])
        if e["id"] == "cover_logo":
            cover = dpi
        else:
            maps.append(dpi)
    return cover, (min(maps) if maps else None)


def scorecard(run_dirs: list[Path], out=print) -> list[dict]:
    """One row per run for the live test plan's scorecard (fills every column the files can answer)."""
    _, issues = check_runs(run_dirs, out=lambda *_: None)
    runs = [load_run(Path(d)) for d in run_dirs if (Path(d) / "content.json").exists()]
    on_map = {r["content"].company.domain: {c.domain for c in r["content"].market.competitors} for r in runs}
    rows = []
    for r in runs:
        rep, c = r["report"], r["content"]
        grades = (rep.get("critic") or {}).get("grades") or {}
        cover, mp = _min_dpi(r)
        others = [o for o in runs if o is not r]
        cross = ", ".join(f"{o['content'].company.display_name}: {'yes' if o['content'].company.domain in on_map[c.company.domain] else 'no'}"
                          f"/{'yes' if c.company.domain in on_map[o['content'].company.domain] else 'no'}" for o in others)
        iss = issues.get(str(r["dir"]), [])
        rows.append({"deck": c.company.display_name, "run": r["dir"].name, "status": rep.get("status"),
                     "critic": f"{grades.get('slide2', '-')}/{grades.get('slide3', '-')}",
                     "competitors": len(c.market.competitors), "cross": cross or "-",
                     "copied": sum(1 for i in iss if i.code in ("copied_language", "repetitive_across_decks")),
                     "errors_now": sum(1 for i in iss if i.severity == "error"),
                     "dpi": f"{cover or 'vector/typeset'} cover, {mp or 'vector/typeset'} map min",
                     "cost": rep.get("cost_usd"), "time_s": rep.get("seconds_total")})
    out("| Deck | Run | Status | Critic s2/s3 | Competitors | On the other's map (this deck/theirs) | Copied spans | "
        "Errors now | Logo dpi | Cost $ | Time s |")
    out("|---|---|---|---|---|---|---|---|---|---|---|")
    for x in rows:
        out(f"| {x['deck']} | {x['run']} | {x['status']} | {x['critic']} | {x['competitors']} | {x['cross']} | "
            f"{x['copied']} | {x['errors_now']} | {x['dpi']} | {x['cost']} | {x['time_s']} |")
    return rows
