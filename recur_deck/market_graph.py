"""Market graph shared across runs: which companies compete with which, what was verified about them, and where
earlier decks placed them. Stored at runs/_market_graph.json (or the path in env RECUR_MARKET_GRAPH).

    {"version": 1,
     "companies": {"aptean.com": {"names": {"Aptean": 2}, "aliases": [], "facts": [{kind, quote, url, run}],
                                  "summaries": [{run, target, segment, scope, relevance, segments}],
                                  "placements": [{run, target, axis_kind, axis_title, side, label, evidence_ids,
                                                  quotes, urls, rationale}],
                                  "verified_in_runs": [...], "targets_in_runs": [...], "markets": [{run, vertical,
                                  segments}]}},
     "edges": [{"a": "a2000software.com", "b": "aptean.com", "type": "competitor", "runs": [...], "channels": [...]}],
     "runs": {"ai2000-20261109-163637": {"target": "a2000software.com", "status": "needs_review", "added": "..."}}}

Edge types: competitor (verified and on the map), verified (verified for the same buyer but not kept),
rejected (verified as another market), candidate (found in a fetched page, never verified).

Quotes and URLs are stored, not just evidence ids: ids are only unique within one run.

Recording: the after_run hook adds every finished live run whose status is pass or pass_with_warnings, so a bad
run can't pollute the graph. `python -m recur_deck.market_graph ingest runs/<dir> ...` adds earlier runs by hand
(needs_review runs too: their competitor research was verified even when the deck needed a person's look;
placements whose rationale rests on missing evidence are never recorded). Offline runs (--offline, --replay)
neither read nor write the graph, so a cached run replays exactly as it ran.

Writes are atomic (temp file + rename) and keep the previous version as <name>.bak.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import hooks
from .axes import AXIS_KINDS, infer_kind, read_side

log = logging.getLogger("recur_deck.market_graph")
VERSION = 1
GOOD_STATUSES = ("pass", "pass_with_warnings")
DECK_STATUSES = GOOD_STATUSES + ("needs_review",)
NEIGHBOR_TYPES = ("competitor", "verified")
MAX_FACTS_PER_RUN = 8
# A placement justified by what a page does NOT say is not evidence (P6); it is never recorded as history.
ABSENCE = re.compile(r"\bno\b.{0,40}\b(cited|mentioned|found|listed|named|shown)\b|\bnot (cited|mentioned|listed|found|named)\b|"
                     r"\bwithout (a |an )?[\w-]+ (module|feature|tool)s?\b", re.I)
# Words that never tie two markets together on their own (functions, generic industries, filler).
_STOP = {"and", "or", "the", "a", "an", "for", "of", "to", "with", "in", "on", "by", "software", "erp", "erps",
         "platform", "platforms", "system", "systems", "solution", "solutions", "management", "manager", "tool",
         "tools", "app", "apps", "cloud", "saas", "business", "businesses", "company", "companies", "brand", "brands",
         "firm", "firms", "customer", "customers", "team", "teams", "operations", "operation", "inventory", "order",
         "orders", "warehouse", "warehousing", "edi", "accounting", "plm", "wholesale", "wholesalers", "retail",
         "retailers", "distribution", "distributors", "manufacturing", "manufacturers", "ecommerce", "commerce",
         "products", "product", "goods", "services", "service", "supply", "chain", "financials", "crm", "pos", "home",
         "small", "mid", "large", "size", "industry", "industries", "vertical", "suite", "all", "one", "online"}


def market_tokens(text: str) -> set[str]:
    """Distinctive words of a vertical ('Apparel, footwear and home goods ERP software' -> {apparel, footwear})."""
    return {w for w in re.findall(r"[a-z][a-z0-9]{3,}", (text or "").lower()) if w not in _STOP}


def default_path(settings) -> Path:
    env = (os.environ.get("RECUR_MARKET_GRAPH") or "").strip()
    return Path(env) if env else Path(settings.runs_dir) / "_market_graph.json"


def _empty() -> dict:
    return {"version": VERSION, "companies": {}, "edges": [], "runs": {}}


def _dom(d: str) -> str:
    from .stages.base import registrable
    return registrable(d or "") or (d or "").strip().lower()


class MarketGraph:
    def __init__(self, path: Path | None = None, data: dict | None = None, read_only: bool = False):
        self.path = Path(path) if path else None
        self.data = data or _empty()
        self.read_only = read_only
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ queries
    def company(self, domain: str) -> dict | None:
        return self.data["companies"].get(_dom(domain))

    def name(self, domain: str) -> str:
        c = self.company(domain) or {}
        names = c.get("names") or {}
        return max(names.items(), key=lambda kv: (kv[1], -len(kv[0])))[0] if names else _dom(domain).split(".")[0]

    def _adjacent(self, domain: str, types: tuple[str, ...]) -> dict[str, dict]:
        d = _dom(domain)
        out: dict[str, dict] = {}
        for e in self.data["edges"]:
            if e["type"] not in types or d not in (e["a"], e["b"]):
                continue
            other = e["b"] if e["a"] == d else e["a"]
            rec = out.setdefault(other, {"runs": set(), "types": set()})
            rec["runs"].update(e["runs"])
            rec["types"].add(e["type"])
        return out

    def neighbors(self, domain: str, depth: int = 2, types: tuple[str, ...] = NEIGHBOR_TYPES) -> list[dict]:
        """[{domain, name, via, runs, depth, types}] for companies within `depth` edges of `domain` (breadth first).
        `via` is the company the path came through (the start itself at depth 1)."""
        start = _dom(domain)
        seen = {start}
        frontier = [start]
        out: list[dict] = []
        for level in range(1, depth + 1):
            nxt = []
            for node in frontier:
                for other, rec in sorted(self._adjacent(node, types).items()):
                    if other in seen:
                        continue
                    seen.add(other)
                    nxt.append(other)
                    out.append({"domain": other, "name": self.name(other), "via": node, "runs": sorted(rec["runs"]),
                                "depth": level, "types": sorted(rec["types"])})
            frontier = nxt
        return out

    def placements(self, domain: str, derived: bool = True) -> list[dict]:
        """Where earlier decks put `domain`: [{run, target, axis_kind, axis_title, side, label, evidence_ids,
        quotes, ...}]. With `derived`, also one reading per (run, kind) for kinds that run's map did not use,
        taken from that run's rationale and quotes (axes.read_side; e.g. "built for the fashion industry" ->
        industry_depth high). Derived records have derived=True and no label."""
        c = self.company(domain)
        if not c:
            return []
        out = [dict(p, derived=False) for p in c.get("placements", [])]
        if not derived:
            return out
        by_run: dict[str, dict] = {}
        for p in c.get("placements", []):
            r = by_run.setdefault(p["run"], {"target": p.get("target"), "kinds": set(), "texts": []})
            r["kinds"].add(p.get("axis_kind"))
            r["texts"] += [p.get("rationale") or "", *p.get("quotes", [])]
        for f in c.get("facts", []):
            r = by_run.setdefault(f["run"], {"target": f.get("target"), "kinds": set(), "texts": []})
            r["texts"].append(f.get("quote") or "")
        for run, r in by_run.items():
            texts = list(dict.fromkeys(t for t in r["texts"] if t))
            for kind, spec in AXIS_KINDS.items():
                if not spec["derive"] or kind in r["kinds"]:
                    continue
                side = read_side(kind, texts)
                if side:
                    support = [t for t in texts if read_side(kind, [t]) == side]
                    out.append({"run": run, "target": r["target"], "axis_kind": kind, "axis_title": None, "side": side,
                                "label": None, "evidence_ids": [], "quotes": support[:3], "derived": True})
        return out

    def related_targets(self, entity: dict) -> list[str]:
        """Targets of earlier runs in the same market: their vertical shares a distinctive word with this one's.
        Most shared words first."""
        mine = market_tokens(f"{entity.get('vertical') or ''}")
        tdom = _dom(entity.get("domain", ""))
        scored: dict[str, int] = {}
        for dom, c in self.data["companies"].items():
            if dom == tdom:
                continue
            for m in c.get("markets", []):
                n = len(mine & market_tokens(m.get("vertical") or ""))
                if n:
                    scored[dom] = max(scored.get(dom, 0), n)
        return [d for d, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))]

    def runs(self) -> dict:
        return self.data["runs"]

    # ------------------------------------------------------------------ writes
    def _node(self, domain: str) -> dict:
        return self.data["companies"].setdefault(_dom(domain), {
            "names": {}, "aliases": [], "facts": [], "summaries": [], "placements": [], "verified_in_runs": [],
            "targets_in_runs": [], "markets": []})

    def _edge(self, a: str, b: str, typ: str, run: str, channels=()) -> None:
        a, b = sorted((_dom(a), _dom(b)))
        if not a or not b or a == b:
            return
        for e in self.data["edges"]:
            if e["a"] == a and e["b"] == b and e["type"] == typ:
                e["runs"] = sorted({*e["runs"], run})
                e["channels"] = sorted({*e.get("channels", []), *channels})
                return
        self.data["edges"].append({"a": a, "b": b, "type": typ, "runs": [run], "channels": sorted(set(channels))})

    def add_run(self, entity: dict, comps_out: dict, content: dict | None, *, run: str | None = None,
                status: str | None = None, evidence=None, candidates: list[dict] | None = None,
                profile: dict | None = None, accept_needs_review: bool = False) -> bool:
        """Record one finished run. Returns False (and records nothing) when the run is already in the graph or
        its status is not good enough: pass / pass_with_warnings, plus needs_review if `accept_needs_review`."""
        if self.read_only:
            return False
        allowed = DECK_STATUSES if accept_needs_review else GOOD_STATUSES
        if status is not None and status not in allowed:
            log.info("market graph: not adding run %s (status %s)", run, status)
            return False
        if hasattr(content, "model_dump"):
            content = content.model_dump()
        tdom = _dom(entity["domain"])
        run = run or f"{tdom}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        with self._lock:
            if run in self.data["runs"]:
                return False

            def quote(eid: str) -> tuple[str, str, str] | None:
                e = evidence.get(eid) if evidence is not None else None
                return (e.quote, e.url, e.kind) if e else None

            t = self._node(tdom)
            for n in [entity.get("display_name"), *(entity.get("aliases") or [])]:
                if n and n == entity.get("display_name"):
                    t["names"][n] = t["names"].get(n, 0) + 1
                elif n and n not in t["aliases"]:
                    t["aliases"].append(n)
            t["targets_in_runs"].append(run)
            t["markets"].append({"run": run, "vertical": entity.get("vertical") or "",
                                 "segments": [f.get("text") for f in (profile or {}).get("customer_segments", [])][:6]})
            chosen = {_dom(v["domain"]) for v in comps_out.get("competitors", [])}
            for v in comps_out.get("competitors", []):
                c = self._node(v["domain"])
                c["names"][v["name"]] = c["names"].get(v["name"], 0) + 1
                c["verified_in_runs"].append(run)
                c["summaries"].append({"run": run, "target": tdom, "segment": v.get("segment"), "scope": v.get("scope"),
                                       "relevance": v.get("relevance"), "segments": v.get("segments", [])})
                for eid in v.get("evidence_ids", [])[:MAX_FACTS_PER_RUN]:
                    q = quote(eid)
                    if q:
                        c["facts"].append({"kind": q[2], "quote": q[0], "url": q[1], "run": run, "target": tdom})
                self._edge(tdom, v["domain"], "competitor", run, v.get("channels", []))
            for r in comps_out.get("rejected", []):
                if _dom(r["domain"]) in chosen:
                    continue
                c = self._node(r["domain"])
                c["names"][r["name"]] = c["names"].get(r["name"], 0) + 1
                typ = "verified" if r.get("same_vertical") and (r.get("relevance") or 0) >= 0.45 else "rejected"
                self._edge(tdom, r["domain"], typ, run, r.get("channels", []))
            known = chosen | {_dom(r["domain"]) for r in comps_out.get("rejected", [])}
            for cand in candidates or []:
                d = _dom(cand.get("domain", ""))
                if not d or d in known or d == tdom or not any(m.get("quote") for m in cand.get("mentions", [])):
                    continue
                c = self._node(d)
                c["names"][cand["name"]] = c["names"].get(cand["name"], 0) + 1
                self._edge(tdom, d, "candidate", run, cand.get("channels", []))
            if content:
                self._add_placements(content, tdom, run, quote)
            self.data["runs"][run] = {"target": tdom, "status": status, "added": datetime.now().isoformat(timespec="seconds")}
        return True

    def _add_placements(self, content: dict, tdom: str, run: str, quote) -> None:
        m = content.get("market") or {}
        for comp in m.get("competitors", []):
            if ABSENCE.search(comp.get("rationale") or ""):
                log.info("market graph: %s placement in %s rests on missing evidence; not recorded", comp.get("name"), run)
                continue
            node = self._node(comp["domain"])
            for axis_key, pos_key in (("x_axis", "x"), ("y_axis", "y")):
                ax = m.get(axis_key) or {}
                side = "high" if comp.get(pos_key) in ("right", "top") else "low"
                eids = comp.get(f"{pos_key}_evidence_ids") or comp.get("evidence_ids") or []
                qs = [q for q in (quote(e) for e in eids) if q]
                node["placements"].append({
                    "run": run, "target": tdom,
                    "axis_kind": ax.get("kind") or infer_kind(ax.get("title", ""), ax.get("low_label", ""), ax.get("high_label", "")),
                    "axis_title": ax.get("title"), "side": side,
                    "label": (ax.get("high_label" if side == "high" else "low_label") or "").replace("\n", " "),
                    "evidence_ids": list(eids), "quotes": [q[0] for q in qs], "urls": [q[1] for q in qs],
                    "rationale": comp.get("rationale")})

    def save(self) -> None:
        if self.read_only or self.path is None:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
            tmp = self.path.with_name(f"{self.path.name}.tmp{os.getpid()}-{threading.get_ident()}")
            tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False, sort_keys=True), "utf-8")
            tmp.replace(self.path)


# ------------------------------------------------------------------ loading
def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("version") == VERSION else None


def load(settings) -> MarketGraph:
    """The graph file (a damaged file falls back to its .bak, then to an empty graph)."""
    path = default_path(settings)
    data = _read(path) if path.exists() else None
    if data is None and path.exists():
        bak = path.with_name(path.name + ".bak")
        data = _read(bak) if bak.exists() else None
        log.warning("market graph %s unreadable; %s", path, "using its .bak" if data else "starting empty")
    return MarketGraph(path, data)


def for_run(settings) -> MarketGraph:
    """The graph a pipeline run should use: the shared file, or an empty read-only graph for offline runs."""
    if getattr(settings, "offline", False):
        return MarketGraph(None, read_only=True)
    return load(settings)


def _run_files(run_dir: Path) -> dict | None:
    from .evidence import EvidenceStore
    rd = Path(run_dir)
    need = ("entity.json", "competitors.json")
    if not all((rd / f).exists() for f in need):
        return None
    j = lambda f: json.loads((rd / f).read_text("utf-8")) if (rd / f).exists() else None  # noqa: E731
    rep = j("qa_report.json") or {}
    return {"entity": j("entity.json"), "comps_out": j("competitors.json"), "content": j("content.json"),
            "candidates": j("candidates.json") or [], "profile": j("profile.json") or {},
            "evidence": EvidenceStore.load(rd / "evidence.jsonl"), "status": rep.get("status"),
            "replay": bool((rep.get("input") or {}).get("replay"))}


def add_run_dir(graph: MarketGraph, run_dir: Path, accept_needs_review: bool = True) -> tuple[bool, str]:
    f = _run_files(run_dir)
    if f is None:
        return False, "missing entity.json or competitors.json"
    if f["replay"]:
        return False, "a replay of another run"
    if f["status"] not in (DECK_STATUSES if accept_needs_review else GOOD_STATUSES):
        return False, f"status {f['status']}"
    ok = graph.add_run(f["entity"], f["comps_out"], f["content"], run=Path(run_dir).name, status=f["status"],
                       evidence=f["evidence"], candidates=f["candidates"], profile=f["profile"],
                       accept_needs_review=accept_needs_review)
    return ok, "added" if ok else "already in the graph"


def from_runs(run_dirs: list[Path], accept_needs_review: bool = True) -> MarketGraph:
    """An in-memory graph built from run folders (needs_review runs included unless told otherwise)."""
    g = MarketGraph(None)
    for d in run_dirs:
        add_run_dir(g, Path(d), accept_needs_review)
    return g


# ------------------------------------------------------------------ recording finished runs
@hooks.on("after_run")
def record_run(ctx, report: dict) -> None:
    status = report.get("status")
    if ctx.settings.offline or (report.get("input") or {}).get("replay") or status not in GOOD_STATUSES:
        log.info("market graph: run %s not recorded (status %s%s)", ctx.run_dir.name, status,
                 ", offline" if ctx.settings.offline else "")
        return None
    graph = load(ctx.settings)  # re-read just before writing: another run may have saved meanwhile
    f = _run_files(ctx.run_dir)
    if f is None:
        return None
    if graph.add_run(f["entity"], f["comps_out"], f["content"], run=ctx.run_dir.name, status=status,
                     evidence=ctx.evidence, candidates=f["candidates"], profile=f["profile"]):
        graph.save()
        log.info("market graph: recorded run %s in %s", ctx.run_dir.name, graph.path)
    return None


# ------------------------------------------------------------------ CLI
def _show(graph: MarketGraph, domain: str) -> str:
    d = _dom(domain)
    c = graph.company(d)
    if not c:
        return f"{d}: not in the market graph ({graph.path})"
    L = [f"{graph.name(d)} ({d})",
         f"  names: {', '.join(f'{n} x{k}' for n, k in Counter(c['names']).most_common())}"
         + (f"; aliases: {', '.join(c['aliases'])}" if c.get("aliases") else ""),
         f"  target in runs: {', '.join(c['targets_in_runs']) or '-'}",
         f"  verified as a competitor in runs: {', '.join(c['verified_in_runs']) or '-'}"]
    for s in c.get("summaries", []):
        L.append(f"  [{s['run']} for {s['target']}] segment: {s.get('segment')} | scope: {s.get('scope')}")
    nb = graph.neighbors(d, depth=1, types=("competitor", "verified", "candidate", "rejected"))
    if nb:
        L.append("  edges: " + ", ".join(f"{n['name']} ({n['domain']}, {'/'.join(n['types'])})" for n in nb))
    ps = graph.placements(d)
    L.append(f"  placements on record: {len(ps)}")
    for p in ps:
        how = "derived from prose" if p["derived"] else f"\"{p['axis_title']}\" -> {p['label']!r}"
        L.append(f"    - {p['run']} (target {p['target']}): {p['axis_kind'] or 'UNKNOWN KIND'} = {p['side']}  [{how}]")
        for q in p.get("quotes", [])[:3]:
            L.append(f"        \"{q[:160]}\"")
        if p.get("rationale"):
            L.append(f"        rationale: {p['rationale'][:160]}")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    from .config import Settings
    ap = argparse.ArgumentParser(prog="python -m recur_deck.market_graph",
                                 description="Inspect or extend the market graph shared across runs (local, free).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="add finished run folders to the graph")
    ing.add_argument("run_dirs", nargs="+", type=Path)
    ing.add_argument("--strict", action="store_true", help="only pass / pass_with_warnings runs (default: needs_review too)")
    sh = sub.add_parser("show", help="print what the graph knows about a company")
    sh.add_argument("domain")
    a = ap.parse_args(argv)
    settings = Settings.load()
    graph = load(settings)
    if a.cmd == "show":
        print(_show(graph, a.domain))
        return 0
    added = 0
    for d in a.run_dirs:
        ok, why = add_run_dir(graph, d, accept_needs_review=not a.strict)
        added += ok
        print(f"{d}: {why}")
    if added:
        graph.save()
    print(f"{added} run(s) added; graph at {graph.path} has {len(graph.data['companies'])} companies, "
          f"{len(graph.data['edges'])} edges, {len(graph.data['runs'])} runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
