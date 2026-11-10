"""Thesis quality (Agent 1): copied language and a real investment case, run in Layer A.

  copied_spans        copied_language          a line shares 5 consecutive words with Recur's own deck
                                               (assets/reference_text.json) or a style example, or uses a stock
                                               phrase from earlier drafts
  repetitive_issues   repetitive_across_decks  a thesis headline is a near copy (token-set similarity > 0.6) of the
                                               same slot in another company's deck (runs/_thesis_history.jsonl)
  why_now_issues      why_now_unsupported      point 01 cites no news / segment / comparison quote, or is about
                                               what vendors do instead of what changed for the customers
  durability_issues   durability_missing       point 02 cites no tenure / customers / switching quote
  lever_issues        lever_unsupported        point 03 has no lever, cites no gap, picks a lever the signals don't
                                               allow, or opens with "Help" (Recur's own template)

All are errors with fix `rewrite_copy`. Company names count as one token, so "Help FDM4 grow" and
"Help Ai2000 grow" are the same words. Recur's reference text is read here and by the critic, never by the writer.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from .. import hooks
from ..models import DeckContent
from ..qa import Issue
from ..spec import KIT_ROOT
from . import register

log = logging.getLogger("recur_deck.checks.copy")
REFERENCE_PATH = KIT_ROOT / "assets" / "reference_text.json"
STYLE_DIR = Path(__file__).resolve().parent.parent / "prompts" / "style_examples"
HISTORY_NAME = "_thesis_history.jsonl"
NGRAM = 5
SIMILAR = 0.6
BANNED_STOCK = ["set to win", "so switching is hard", "to what already works", "grow with outbound sales and AI",
                "sales-led model"]
WHY_NOW_KINDS = {"news", "segment", "comparison"}
DURABILITY_KINDS = {"tenure", "customers", "switching"}
LEVERS = ("ai_native_gtm", "ai_rebuild", "ai_modernization", "built_the_bench")
LEVER_GAP_KINDS = {"ai_native_gtm": {"gtm", "customers", "segment"}, "ai_modernization": {"tech"},
                   "ai_rebuild": {"tech"}, "built_the_bench": {"team"}}
ABOUT_VENDORS = re.compile(r"\b(vendors?|competitors?|rivals?|providers?)\b|"
                           r"\b(erps?|tools|platforms|suites|software|apps) (are|is) (adding|launching|building|shipping)\b", re.I)
PARTNER = re.compile(r"\b(partners?|resellers?|channel|dealers?|distributors? of our software|value[- ]added|var)\b", re.I)
LEGACY = re.compile(r"\bon[- ]?prem|\bdesktop\b|\blegacy\b|\bclient[- ]server|\bas/?400\b|\bibm i\b|\bwindows (pc|server|app)|"
                    r"\binstall(ed|s)? (on|locally)|\bserver[- ]based|\bhosted on your", re.I)
FOUNDER = re.compile(r"\bfounder|\bfounded by\b|\bowner\b|\bfamily[- ]owned", re.I)
SENIOR = re.compile(r"\b(ceo|cto|cfo|coo|cro|cmo|chief|vp|vice president|head of|director of)\b", re.I)


# ------------------------------------------------------------------ tokens
def tokens(text: str, names: list[str] = ()) -> list[str]:
    """Lowercased words; every company name (longest first) becomes the single token <co>."""
    t = (text or "").replace("<Company>", " <co> ").replace("<Rival group>", " <grp> ").replace("&", " and ")
    for n in sorted({n for n in names if n and len(n) > 1}, key=len, reverse=True):
        t = re.sub(rf"(?<![A-Za-z0-9]){re.escape(n)}(?![A-Za-z0-9])", " <co> ", t, flags=re.I)
    return re.findall(r"<co>|<grp>|[a-z0-9]+", t.lower())


def ngrams(toks: list[str], n: int = NGRAM) -> set[tuple[str, ...]]:
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def example_lines(text: str) -> list[str]:
    """The slide lines of a style example (thesis lines, subtitle, our-take headline and points)."""
    out = []
    for ln in text.splitlines():
        m = (re.match(r"^\[\d\] (.+)$", ln) or re.match(r"^ {4}(?![(\s])(.+)$", ln) or re.match(r"^subtitle: (.+)$", ln)
             or re.match(r'^our_take: "(.+)"$', ln) or re.match(r'^ {2}- "(.+)"$', ln))
        if m:
            out.append(m.group(1).strip())
    return out


@lru_cache(maxsize=1)
def reference() -> dict:
    data = json.loads(REFERENCE_PATH.read_text("utf-8"))
    return {"company": data.get("company", ""), "lines": [*data.get("slide2", []), *data.get("slide3", [])]}


def reference_ngrams() -> set[tuple[str, ...]]:
    ref = reference()
    return {g for ln in ref["lines"] for g in ngrams(tokens(ln, [ref["company"]]))}


def style_example_ngrams() -> dict[str, set[tuple[str, ...]]]:
    return {p.stem: {g for ln in example_lines(p.read_text("utf-8")) for g in ngrams(tokens(ln))}
            for p in sorted(STYLE_DIR.glob("*.md"))}


def _names(content: DeckContent) -> list[str]:
    return [content.company.display_name, *content.company.aliases, *(c.name for c in content.market.competitors)]


def copy_fields(content: DeckContent) -> list[tuple[str, int, str]]:
    """(field path, slide, text) for every line the writer chose (titles are generated, axis labels too short)."""
    m = content.market
    out = []
    for i, t in enumerate(content.thesis):
        out += [(f"thesis[{i}].headline", 2, t.headline), (f"thesis[{i}].subline", 2, t.subline)]
    out += [("market.subtitle", 3, m.subtitle), ("market.our_take.headline", 3, m.our_take.headline)]
    out += [(f"market.our_take.points[{j}]", 3, p) for j, p in enumerate(m.our_take.points)]
    return out


# ------------------------------------------------------------------ B. copy lint
def copied_spans(content: DeckContent) -> list[Issue]:
    names = _names(content)
    ref = reference_ngrams()
    examples = style_example_ngrams()
    banned = [(b, tokens(b)) for b in BANNED_STOCK]
    out: list[Issue] = []
    for field, slide, text in copy_fields(content):
        toks = tokens(text, names)
        grams = ngrams(toks)
        why = []
        hit = sorted(grams & ref)
        if hit:
            why.append(f"shares '{' '.join(hit[0])}' with Recur's own deck")
        for ex, g in examples.items():
            h = sorted(grams & g)
            if h:
                why.append(f"shares '{' '.join(h[0])}' with style example {ex}")
        joined = " " + " ".join(toks) + " "
        for phrase, bt in banned:
            if " " + " ".join(bt) + " " in joined:
                why.append(f"stock phrase '{phrase}'")
        if why:
            out.append(Issue("copied_language", "error", slide, field,
                             f"'{text}': " + "; ".join(why) + ". Write it for this company in your own words",
                             "rewrite_copy"))
    return out


# ------------------------------------------------------------------ B. cross-deck similarity
def history_path(run_dir: Path) -> Path:
    return Path(run_dir).parent / HISTORY_NAME


def load_history(runs_dir: Path, exclude_run: str | None = None) -> list[dict]:
    """Earlier decks' theses: runs/_thesis_history.jsonl, plus any sibling run folder's content.json (runs from
    before the history file existed). One entry per run."""
    runs_dir = Path(runs_dir)
    seen: dict[str, dict] = {}
    p = runs_dir / HISTORY_NAME
    if p.exists():
        for line in p.read_text("utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen[e.get("run", "")] = e
    if runs_dir.is_dir():
        for d in sorted(runs_dir.iterdir()):
            if d.name in seen or not (d / "content.json").is_file():
                continue
            try:
                c = DeckContent.model_validate_json((d / "content.json").read_text("utf-8"), context={"min_competitors": 0})
            except Exception:  # noqa: BLE001 - a half-written or foreign folder is not history
                continue
            seen[d.name] = history_entry(d.name, c)
    return [e for k, e in seen.items() if k != exclude_run]


def history_entry(run: str, c: DeckContent) -> dict:
    return {"run": run, "domain": c.company.domain, "company": c.company.display_name, "names": _names(c),
            "thesis": [{"headline": t.headline, "subline": t.subline} for t in c.thesis],
            "at": datetime.now().isoformat(timespec="seconds")}


def similarity(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def repetitive_issues(content: DeckContent, history: list[dict]) -> list[Issue]:
    names = _names(content)
    out: list[Issue] = []
    for i, t in enumerate(content.thesis):
        mine = tokens(t.headline, names)
        for e in history:
            if e.get("domain") == content.company.domain or len(e.get("thesis", [])) <= i:
                continue  # the same company's earlier deck may say the same true things
            other = e["thesis"][i]["headline"]
            s = similarity(mine, tokens(other, e.get("names", [e.get("company", "")])))
            if s > SIMILAR:
                out.append(Issue("repetitive_across_decks", "error", 2, f"thesis[{i}].headline",
                                 f"'{t.headline}' repeats the {e.get('company')} deck's '{other}' (similarity {s:.2f}); "
                                 f"say what is true of this company instead", "rewrite_copy"))
                break
    return out


@hooks.on("after_run")
def record_thesis(ctx, report: dict) -> None:
    """Append the delivered deck's thesis to runs/_thesis_history.jsonl (live runs only)."""
    if ctx.settings.offline or (report.get("input") or {}).get("replay") or not report.get("deliverables"):
        return None
    p = ctx.run_dir / "content.json"
    if not p.exists():
        return None
    c = DeckContent.model_validate_json(p.read_text("utf-8"), context={"min_competitors": 0})
    with history_path(ctx.run_dir).open("a", encoding="utf-8") as f:
        f.write(json.dumps(history_entry(ctx.run_dir.name, c), ensure_ascii=False) + "\n")
    return None


# ------------------------------------------------------------------ C. the investment case
def _kinds(evidence, ids: list[str]) -> set[str]:
    return {e.kind for e in (evidence.get(i) for i in ids) if e}


def why_now_issues(content: DeckContent, evidence=None) -> list[Issue]:
    t = content.thesis[0]
    out = []
    if ABOUT_VENDORS.search(f"{t.headline} {t.subline}"):
        out.append(Issue("why_now_unsupported", "error", 2, "thesis[0].subline",
                         f"point 01 is about what vendors do ('{t.subline}'); say what changed in the customers' world",
                         "rewrite_copy"))
    if not t.evidence_ids or (evidence is not None and not _kinds(evidence, t.evidence_ids) & WHY_NOW_KINDS):
        out.append(Issue("why_now_unsupported", "error", 2, "thesis[0].headline",
                         "point 01 cites no news, segment or comparison quote showing a change in the customers' world",
                         "rewrite_copy"))
    return out


def durability_issues(content: DeckContent, evidence=None) -> list[Issue]:
    t = content.thesis[1]
    if t.evidence_ids and (evidence is None or _kinds(evidence, t.evidence_ids) & DURABILITY_KINDS):
        return []
    return [Issue("durability_missing", "error", 2, "thesis[1].subline",
                  "point 02 cites no durability fact (a tenure, customers or switching quote): say why this business "
                  "lasts, from the evidence", "rewrite_copy")]


def allowed_levers(evidence) -> dict[str, dict]:
    """Levers the target's gap signals allow, each with why and the evidence ids behind it (Agent 1 table):
    no outbound/sales signal or partner-only selling -> ai_native_gtm; legacy, desktop or on-prem tech ->
    ai_modernization / ai_rebuild; founder-led with no senior hire found -> built_the_bench; no clear gap ->
    ai_native_gtm as the default, written as adding to what exists."""
    items = [e for e in (evidence.items() if evidence is not None else []) if e.about == "target"]
    by = lambda k: [e for e in items if e.kind == k]  # noqa: E731
    gtm, tech, team = by("gtm"), by("tech"), by("team")
    reach = [e.id for e in items if e.kind in ("customers", "segment")][:3]
    out: dict[str, dict] = {}
    partner_only = bool(gtm) and all(PARTNER.search(e.quote) for e in gtm)
    if not gtm or partner_only:
        out["ai_native_gtm"] = {"why": "sales come only through partners" if partner_only else
                                "no outbound or sales-team signal found on the site",
                                "evidence_ids": [e.id for e in gtm] or reach}
    legacy = [e for e in tech if LEGACY.search(e.quote)]
    if legacy:
        for lv in ("ai_modernization", "ai_rebuild"):
            out[lv] = {"why": "legacy, desktop or on-prem technology", "evidence_ids": [e.id for e in legacy]}
    founder = [e for e in team if FOUNDER.search(e.quote)]
    if founder and not any(SENIOR.search(e.quote) and not FOUNDER.search(e.quote) for e in team):
        out["built_the_bench"] = {"why": "founder-led, no senior hire found", "evidence_ids": [e.id for e in founder]}
    if not out:
        out["ai_native_gtm"] = {"why": "no clear gap: add to the sales motion that exists and say what it is",
                                "evidence_ids": [e.id for e in gtm] or reach, "default": True}
    return out


def lever_issues(content: DeckContent, evidence=None) -> list[Issue]:
    t = content.thesis[2]
    why = []
    if t.headline.strip().lower().startswith("help"):
        why.append(f"the headline opens with 'Help' ('{t.headline}'), Recur's own template: name the gap or the "
                   f"opportunity instead")
    lever, ids = content.lever, list(content.lever_gap_evidence_ids)
    if lever is None:
        why.append(f"point 03 names no lever (one of {', '.join(LEVERS)})")
    if not ids:
        why.append("point 03 cites no evidence of the gap it fixes (lever_gap_evidence_ids)")
    elif evidence is not None:
        missing = [i for i in ids if evidence.get(i) is None]
        if missing:
            why.append(f"lever_gap_evidence_ids cite unknown ids {missing}")
        allowed = allowed_levers(evidence)
        if lever and lever not in allowed:
            why.append(f"the signals don't support {lever}; allowed: "
                       + "; ".join(f"{k} ({v['why']})" for k, v in allowed.items()))
        elif lever and not missing:
            ok_kinds = LEVER_GAP_KINDS[lever]
            if not _kinds(evidence, ids) & ok_kinds:
                why.append(f"the gap evidence for {lever} must be a {' / '.join(sorted(ok_kinds))} quote")
    if not why:
        return []
    return [Issue("lever_unsupported", "error", 2, "thesis[2].headline" if why[0].startswith("the headline") else "thesis[2].subline",
                  "; ".join(why), "rewrite_copy")]


def thesis_problems(content: DeckContent, evidence=None) -> list[str]:
    """Problem strings for re-asking the writer (copy, why-now, durability, lever)."""
    issues = (copied_spans(content) + why_now_issues(content, evidence) + durability_issues(content, evidence)
              + lever_issues(content, evidence))
    return [f"{i.field}: {i.message}" for i in issues]


# ------------------------------------------------------------------ registry
@register
def copy_checks(content: DeckContent, manifest: dict, logo_metrics: dict, extra: dict) -> list[Issue]:
    ev = extra.get("evidence")
    issues = copied_spans(content) + why_now_issues(content, ev) + durability_issues(content, ev) + lever_issues(content, ev)
    run_dir = extra.get("run_dir")
    if run_dir is not None:
        issues += repetitive_issues(content, extra.get("thesis_history")
                                    if extra.get("thesis_history") is not None
                                    else load_history(Path(run_dir).parent, Path(run_dir).name))
    return issues
