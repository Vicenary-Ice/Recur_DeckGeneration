"""recur-deck "Ai2000" [--domain a2000software.com] [--hint "apparel ERP"] [--non-interactive]
                     [--accept-low-confidence] [--out ./output] [--replay RUN_DIR] [--offline] [--no-vision]
recur-deck check runs/<dir> [runs/<dir> ...] [--critic --yes] [--scorecard]

check       re-run every deterministic check on finished runs' saved files: no network, no cost. Exit 1 if any
            run has an error. --critic also asks the founder-eyes critic (one live Opus call per run, ~$0.09-0.17);
            it needs --yes.

--offline   run the whole pipeline from runs/_cache only: no network, no keys used, no credits spent. The first
            call that is not cached stops the run (exit 2) and is named with its stage.
--no-vision skip rendering and the vision check (layer A checks only, no PDF). New slide images are never
            cached, so an --offline run usually needs it.

Cost cap: RECUR_MAX_COST_USD (default 2.00) per deck. A research stage that would pass it stops the run (exit 2);
the QA loop stops fixing and delivers the best deck so far as needs_review.

Exit codes: 0 pass · 1 pass with warnings / needs review · 2 error · 3 ambiguous company (choose a --domain).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import pipeline
from .config import Settings

console = Console(stderr=True)


def _choose(cands: list[dict]) -> str | None:
    t = Table(title="Which company do you mean?")
    for col in ("#", "domain", "name", "score", "what it does"):
        t.add_column(col)
    for i, c in enumerate(cands, 1):
        t.add_row(str(i), c["domain"], c.get("display_name", ""), str(c["score"]), (c.get("one_liner") or "")[:70])
    console.print(t)
    ans = console.input("Pick 1-3, type a domain, or press Enter to stop: ").strip()
    if not ans:
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(cands):
        return cands[int(ans) - 1]["domain"]
    return ans


def check_main(argv: list[str]) -> int:
    from . import check_run
    ap = argparse.ArgumentParser(prog="recur-deck check",
                                 description="Re-run every deterministic check on finished runs (offline, free).")
    ap.add_argument("run_dirs", nargs="+", type=Path)
    ap.add_argument("--critic", action="store_true",
                    help=f"also ask the founder-eyes critic: one LIVE Opus call per run ({check_run.CRITIC_COST})")
    ap.add_argument("--yes", action="store_true", help="confirm the live --critic calls")
    ap.add_argument("--scorecard", action="store_true", help="print the live test plan's scorecard table instead")
    a = ap.parse_args(argv)
    if a.scorecard:
        check_run.scorecard(a.run_dirs)
        return 0
    print("Checking saved files only: no network, no API calls.")
    code, _ = check_run.check_runs(a.run_dirs)
    if a.critic:
        n = len(a.run_dirs)
        if not a.yes:
            console.print(f"[yellow]--critic makes {n} live Opus call(s) ({check_run.CRITIC_COST} each). "
                          f"Re-run with --yes to spend that.[/yellow]")
            return max(code, 2)
        code = max(code, check_run.critic_runs(a.run_dirs))
    return code


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["check"]:
        return check_main(argv[1:])
    ap = argparse.ArgumentParser(prog="recur-deck", description="Build a Recur outreach deck for a company.")
    ap.add_argument("name", nargs="?", help='company name, e.g. "Ai2000"')
    ap.add_argument("--domain", help="the company's website domain (skips name disambiguation)")
    ap.add_argument("--hint", help='industry hint, e.g. "apparel ERP"')
    ap.add_argument("--non-interactive", action="store_true", help="never prompt; ambiguous names exit with code 3")
    ap.add_argument("--accept-low-confidence", action="store_true",
                    help="with --non-interactive: proceed with the best candidate and flag it")
    ap.add_argument("--out", type=Path, default=Path("output"), help="folder for the deliverables (default ./output)")
    ap.add_argument("--replay", type=Path, help="rebuild a previous run folder from cache, without network")
    ap.add_argument("--offline", action="store_true",
                    help="serve every web and Claude call from runs/_cache (no network, no credits); stop on a miss")
    ap.add_argument("--no-vision", action="store_true", help="skip render + vision QA (layer A checks only, no PDF)")
    a = ap.parse_args(argv)
    if not a.name and not a.replay:
        ap.error("give a company name (or --replay RUN_DIR)")

    settings = Settings.load()
    if a.offline:  # keys are dropped too, so nothing could authenticate even if a call slipped past the cache
        settings = replace(settings, offline=True, anthropic_api_key=None, firecrawl_api_key=None)
    if a.no_vision:
        settings = replace(settings, vision_qa=False)
    progress = lambda s: console.print(f"[cyan]›[/cyan] {s}")  # noqa: E731
    if a.replay:
        res = pipeline.replay(a.replay, out_dir=a.out, settings=settings, progress=progress)
    else:
        if a.offline:
            console.print("[cyan]Offline run:[/cyan] every call is served from runs/_cache; nothing is sent.")
        elif not settings.has("anthropic"):
            console.print("[red]ANTHROPIC_API_KEY is missing.[/red] Copy .env.example to .env and add your keys.")
            return 2
        if not settings.has("firecrawl") and not a.offline:
            console.print("[yellow]FIRECRAWL_API_KEY is missing: searches fall back to Claude web search and pages are "
                          "fetched directly (slower, fewer competitors). Add the key to .env for normal runs.[/yellow]")
        interactive = not a.non_interactive and sys.stdin.isatty()
        res = pipeline.run(a.name, domain=a.domain, hint=a.hint, non_interactive=not interactive,
                           accept_low_confidence=a.accept_low_confidence, out_dir=a.out, settings=settings,
                           choose=_choose if interactive else None, progress=progress)

    rep = res.report
    if res.exit_code == 3:
        print(json.dumps({"status": "ambiguous_entity", "candidates": res.candidates,
                          "next": f'recur-deck "{a.name}" --domain <domain>'}, indent=2))
        return 3
    if res.exit_code == 2 and rep.get("status") == "budget_exceeded":
        console.print(f"[red]{rep.get('error')}[/red]")
        console.print(f"Spent ${rep.get('cost_usd')} (cap ${rep.get('cost_cap_usd')}). Report: {res.run_dir / 'qa_report.json'}")
        return 2
    if res.exit_code == 2 and rep.get("status") == "offline_cache_miss":
        console.print(f"[red]{rep.get('error')}[/red]")
        if res.run_dir:
            console.print(f"Uncached calls: {res.run_dir / 'qa_report.json'} (offline_misses)")
        return 2
    if res.exit_code == 2:
        console.print(f"[red]Failed in stage '{rep.get('failed_stage')}':[/red] {rep.get('error')}")
        console.print(f"Report: {res.run_dir / 'qa_report.json' if res.run_dir else '-'}")
        return 2
    colour = {"pass": "green", "pass_with_warnings": "yellow", "needs_review": "red"}.get(rep["status"], "white")
    cap = rep.get("cost_cap_usd")
    console.print(f"\n[bold {colour}]QA status: {rep['status']}[/bold {colour}]   "
                  f"time {rep.get('seconds_total')}s · cost ${rep.get('cost_usd')}"
                  + (f" (cap ${cap:.2f})" if cap else " (no cap)"))
    crit = rep.get("critic") or {}
    if crit.get("grades"):
        console.print(f"  critic: slide 2 {crit['grades']['slide2']}, slide 3 {crit['grades']['slide3']}, "
                      f"mail_ready={crit.get('mail_ready')}" + ("" if crit.get("judged_final_deck") else " (an earlier version)"))
    if rep.get("budget_reached"):
        console.print(f"  [red]cost cap reached:[/red] {rep['budget_reached']}")
    for f in rep.get("delivered_to") or [rep["deliverables"]["pptx"], rep["deliverables"].get("pdf")]:
        if f:
            console.print(f"  {f}")
    for k in ("fallbacks", "warnings", "needs_review"):
        for item in rep.get(k) or []:
            console.print(f"  [yellow]{k[:-1] if k.endswith('s') else k}:[/yellow] {item}")
    for k, v in (rep.get("flags") or {}).items():
        console.print(f"  [yellow]flag:[/yellow] {k} = {v}")
    print(json.dumps({"status": rep["status"], "pptx": rep["deliverables"]["pptx"], "pdf": rep["deliverables"].get("pdf"),
                      "run_dir": str(res.run_dir)}, indent=2))
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())
