"""Orchestrator: S1 entity -> S2 profile -> S3 competitors -> S4 logos -> S5 write -> S6 build -> S7 QA loop
-> S8 deliver. Any stage exception is caught here: qa_report.json gets the traceback summary, exit code 2.

Exit codes: 0 pass · 1 pass with warnings / needs_review · 2 error · 3 ambiguous entity (needs user choice).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import build as builder
from . import hooks, market_graph, qa
from .cache import BudgetExceeded
from .clients.http import OfflineMiss
from .config import Settings
from .evidence import EvidenceStore
from .logos import file_stem, slug
from .models import DeckContent
from .spec import (CELLS, MAP_LOGO, MAP_TYPESET_MAX_H, MAX_PER_CELL, MIN_LOGO_H, TARGET_CELL, TYPESET_GROW,
                   TYPESET_SHRINK)
from .stages import competitors as s_comp
from .stages import entity as s_entity
from .stages import logo_sources as s_logos
from .stages import placement as s_placement
from .stages import profile as s_profile
from .stages import vision_qa as s_vision
from .stages import write as s_write
from .stages.base import AmbiguousEntity, Ctx

log = logging.getLogger("recur_deck")
HARD_TIMEOUT_S = 12 * 60
STOPWORDS = {"and", "for", "the", "with", "from", "that", "into", "their", "small", "large", "software", "system",
             "systems", "platform", "companies", "businesses", "business"}

Progress = Callable[[str], None]


class OfflineCacheMiss(Exception):
    """--offline run: a stage needed a response that is not in runs/_cache. The run stops at the end of that
    stage (offline mode never touches the network, so nothing was sent or spent)."""

    def __init__(self, stage: str, call: str, total: int):
        hint = (" Visual QA renders new slide images, which are never cached: add --no-vision."
                if call.startswith("anthropic: vision_qa") else "")
        super().__init__(f"offline run stopped in stage '{stage}': {call} is not in runs/_cache and would have "
                         f"needed the network ({total} uncached call(s) in total, none sent).{hint}")
        self.stage, self.call, self.total = stage, call, total


def offline_gate(ctx: Ctx, stage: str, budget: bool = True) -> None:
    if ctx.strict_offline and ctx.cache.misses:
        raise OfflineCacheMiss(stage, ctx.cache.misses[0], len(ctx.cache.misses))
    if budget:  # research stages stop on the cost cap; the QA stage delivers the best deck instead
        budget_gate(ctx, stage)


class BudgetStop(Exception):
    """A research stage needed a call the cost cap refused: the run stops (exit 2) before a deck is built."""

    def __init__(self, stage: str, hit: str):
        super().__init__(f"cost cap reached in stage '{stage}': {hit}. Raise RECUR_MAX_COST_USD to finish this deck.")
        self.stage = stage


def budget_gate(ctx: Ctx, stage: str) -> None:
    """Research stages: a refused call (even one a fallback swallowed) stops the run."""
    if ctx.ledger.budget_hits:
        raise BudgetStop(stage, ctx.ledger.budget_hits[0])


# ------------------------------------------------------------------ logging
class SecretFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if any(s in msg for s in self.secrets):
            for s in self.secrets:
                msg = msg.replace(s, "***")
            record.msg, record.args = msg, ()
        return True


def _attach_log(run_dir: Path, settings: Settings) -> logging.Handler:
    h = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    h.addFilter(SecretFilter(settings.secrets()))
    root = logging.getLogger("recur_deck")
    root.setLevel(logging.INFO)
    root.addHandler(h)
    return h


# ------------------------------------------------------------------ state
@dataclass
class Result:
    exit_code: int
    run_dir: Path | None
    report: dict
    pptx: Path | None = None
    pdf: Path | None = None
    candidates: list[dict] = field(default_factory=list)


@dataclass
class QAState:
    ctx: Ctx
    entity: dict
    profile: dict
    comps: dict
    content: DeckContent
    logos: dict[str, dict]
    protected: list[str]
    rewrite_attempts: dict[str, int] = field(default_factory=dict)
    relayouts: int = 0
    shortened: bool = False
    rebuilt: bool = False
    review_notes: list[str] = field(default_factory=list)
    seen_hashes: set[str] = field(default_factory=set)
    graph: object | None = None  # market_graph.MarketGraph shared across runs (checks read it through extra)
    regens: dict[str, int] = field(default_factory=lambda: {"thesis": 0, "market": 0})
    added_competitor: bool = False
    budget_stop: str | None = None  # set when the cost cap stopped the QA loop

    @property
    def name(self) -> str:
        return self.content.company.display_name

    def min_competitors(self) -> int:
        return s_write.min_competitors(len(self.comps["competitors"]), self.comps.get("thin_market", False))

    def state_hash(self) -> str:
        logos = sorted((n, hashlib.sha1(Path(l["png"]).read_bytes()).hexdigest(), l.get("typeset_h"))
                       for n, l in self.logos.items() if Path(l["png"]).exists())
        return hashlib.sha256((self.content.model_dump_json() + json.dumps(logos)).encode()).hexdigest()[:16]

    def logo_metrics(self) -> dict[str, dict]:
        names = [self.name, *(c.name for c in self.content.market.competitors)]
        return {n: {"ok": self.logos[n]["ok"], "problems": self.logos[n].get("problems", [])} for n in names if n in self.logos}

    def check_extra(self) -> dict:
        """Run context for the registered checks (recur_deck/checks): see checks/__init__.py."""
        return {"evidence": self.ctx.evidence, "entity": self.entity, "graph": self.graph, "run_dir": self.ctx.run_dir,
                "profile": self.profile, "comps": self.comps, "logos": self.logos}


def extra_protected(entity: dict) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z-]{3,}", entity.get("vertical") or "") if w.lower() not in STOPWORDS]
    return list(dict.fromkeys([*(entity.get("aliases") or []), *words]))


# ------------------------------------------------------------------ build + checks
def build_and_check(st: QAState, k: int) -> tuple[Path, dict, list[qa.Issue]]:
    ctx, c = st.ctx, st.content
    pptx = ctx.run_dir / f"{file_stem(st.name)}.pptx"
    comp_logos = {x.name: Path(st.logos[x.name]["png"]) for x in c.market.competitors}
    typeset = frozenset(n for n, l in st.logos.items() if l.get("typeset"))
    heights = {n: l["typeset_h"] for n, l in st.logos.items() if l.get("typeset") and l.get("typeset_h")}
    manifest = builder.build_deck(c, Path(st.logos[st.name]["png"]), comp_logos, pptx, typeset=typeset,
                                  typeset_h=heights)
    ctx.save_json(f"content.round{k}.json", c.model_dump())
    ctx.save_json("content.json", c.model_dump())
    issues = qa.run_all(pptx, c, manifest, st.logo_metrics(), st.protected, extra=st.check_extra())
    return pptx, manifest, issues


def _cell_counts(c: DeckContent) -> dict[tuple[str, str], int]:
    counts = {k: 0 for k in CELLS}
    counts[TARGET_CELL] += 1
    for x in c.market.competitors:
        counts[(x.x, x.y)] += 1
    return counts


def _relevance(st: QAState, name: str) -> float:
    return next((v["relevance"] for v in st.comps["competitors"] if v["name"] == name), 0.0)


def fix_relayout(st: QAState, names: list[str]) -> dict:
    """Move the lowest-relevance competitor of the crowded cell to the adjacent valid cell with the most room that
    its own quotes don't read against (checks.market.cell_conflicts), else drop it if more than the minimum
    remain."""
    from .checks.market import cell_conflicts
    if st.relayouts >= 2:
        return {"fix": "relayout", "applied": False, "why": "relayout limit reached"}
    c = st.content
    comps = {x.name: x for x in c.market.competitors}
    involved = [comps[n] for n in names if n in comps]
    if not involved:
        return {"fix": "relayout", "applied": False, "why": f"no movable competitor among {names}"}
    cell = (involved[0].x, involved[0].y)
    in_cell = [x for x in c.market.competitors if (x.x, x.y) == cell]
    victim = min(in_cell, key=lambda x: _relevance(st, x.name))
    counts = _cell_counts(c)
    adjacent = [(("left" if cell[0] == "right" else "right"), cell[1]), (cell[0], ("top" if cell[1] == "bottom" else "bottom"))]
    options = sorted((a for a in adjacent if counts[a] < MAX_PER_CELL
                      and not cell_conflicts(c, victim, a, st.ctx.evidence)), key=lambda a: counts[a])
    data = c.model_dump()
    st.relayouts += 1
    for comp in data["market"]["competitors"]:
        if comp["name"] == victim.name:
            if options:
                comp["x"], comp["y"] = options[0]
                st.content = DeckContent.model_validate(data, context={"min_competitors": 0})
                return {"fix": "relayout", "applied": True, "moved": victim.name, "from": cell, "to": options[0]}
    if len(c.market.competitors) > st.min_competitors():
        data["market"]["competitors"] = [x for x in data["market"]["competitors"] if x["name"] != victim.name]
        st.content = DeckContent.model_validate(data, context={"min_competitors": 0})
        return {"fix": "relayout", "applied": True, "dropped": victim.name, "from": cell}
    st.review_notes.append(f"crowded cell {cell}: could not move or drop {victim.name}")
    return {"fix": "relayout", "applied": False, "why": "no valid cell and minimum competitor count reached"}


def fix_swap_logo(st: QAState, name: str, sharper: bool = False) -> dict:
    lg = st.logos.get(name)
    if lg is None:
        return {"fix": "swap_logo", "applied": False, "why": f"unknown logo {name}"}
    if lg.get("typeset"):
        st.review_notes.append(f"logo for {name} is already the typeset fallback")
        return {"fix": "swap_logo", "applied": False, "why": "already typeset"}
    alts = lg.get("alternatives") or []
    old = lg.get("source")
    if sharper:  # logo_low_res: only an alternative with more source pixels (or a vector) helps
        have = lg.get("native_w") or 0
        alts[:] = sorted((a for a in alts if a.get("native_w") is None or (a.get("native_w") or 0) > have),
                         key=lambda a: -(a.get("native_w") or 10 ** 9))
    if alts:
        nxt = alts.pop(0)
        lg.pop("cropped_tagline", None)
        lg.update({"png": nxt["path"], "source": nxt["source"], "aspect": nxt["aspect"], "ok": True, "problems": [],
                   "native_w": nxt.get("native_w"), "native_h": nxt.get("native_h"),
                   **({"cropped_tagline": True} if nxt.get("cropped_tagline") else {})})
    else:
        lg.pop("cropped_tagline", None)
        lg.update(s_logos.typeset(name, st.ctx.run_dir / "logos" / "_typeset", st.entity.get("aliases", [])
                                  if name == st.name else []))
        st.ctx.flag("typeset_logos", sorted({*st.ctx.report["flags"].get("typeset_logos", []), name}))
    return {"fix": "swap_logo", "applied": True, "company": name, "from": old, "to": lg["source"]}


def fix_crop_tagline(st: QAState, name: str) -> dict:
    """logo_tagline_microtext: crop the tagline (one cached vision check of the crop), else swap the logo."""
    lg = st.logos.get(name)
    if lg is None:
        return {"fix": "crop_tagline", "applied": False, "why": f"unknown logo {name}"}
    res = s_logos.crop_logo(st.ctx, name, lg, st.ctx.run_dir / "logos",
                            st.entity.get("aliases", []) if name == st.name else [])
    return res if res["applied"] else {**fix_swap_logo(st, name), "after": res.get("why")}


def fix_regenerate(st: QAState, section: str, problems: list[str]) -> dict:
    """Re-ask the writer for one section (thesis or market) with every problem found, keeping the other section
    byte-identical. At most 2 per section per deck."""
    fix = "regenerate_market" if section == "market" else "regenerate_thesis"
    if st.regens[section] >= 2:
        st.review_notes.append(f"{section}: still failing after 2 rewrites ({'; '.join(problems)[:300]})")
        return {"fix": fix, "applied": False, "why": "2 rewrites used"}
    st.regens[section] += 1
    before = st.content
    try:
        new = s_write.write_deck(st.ctx, st.entity, st.profile, st.comps, problems=list(dict.fromkeys(problems)),
                                 section=section, current=st.content)
    except BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001 - the old content stays; the issues stay open
        return {"fix": fix, "applied": False, "why": f"{type(e).__name__}: {str(e)[:200]}"}
    st.content = new
    changed = new.model_dump_json() != before.model_dump_json()
    return {"fix": fix, "applied": changed, "problems": len(problems), **({} if changed else {"why": "same content"})}


def fix_add_competitor(st: QAState, f: dict) -> tuple[dict, str | None]:
    """Critic says an obvious competitor is missing. If a company the reason names is a candidate this run found
    (verified or not), verify it (cached if done before) and, if it passes, put it on the map via the market
    rewrite. Never adds a company from the critic's memory. Once per deck. Returns (log entry, market problem)."""
    if st.added_competitor:
        return {"fix": "add_competitor", "applied": False, "why": "already used once"}, None
    st.added_competitor = True
    reason = f.get("reason", "")
    p = st.ctx.run_dir / "candidates.json"
    cands = json.loads(p.read_text("utf-8")) if p.exists() else []
    placed = {c.domain for c in st.content.market.competitors}
    hit = next((c for c in cands if c["domain"] not in placed and (
        re.search(rf"(?<![A-Za-z0-9]){re.escape(c['name'])}(?![A-Za-z0-9])", reason, re.I) or c["domain"] in reason)), None)
    if hit is None:
        st.ctx.warn(f"critic: missing competitor not among this run's candidates ({reason[:160]})")
        return {"fix": "add_competitor", "applied": False, "why": "not a candidate this run found"}, None
    known = next((v for v in st.comps["competitors"] if v["domain"] == hit["domain"]), None)
    if known is None:
        c = s_comp.Cand(hit["domain"], names=dict(hit.get("names") or {hit["name"]: 1}), channels=set(hit.get("channels", [])),
                        head_to_head=hit.get("head_to_head", False), mentions=hit.get("mentions", []))
        try:
            v = s_comp._verify(st.ctx, st.entity, st.profile, c)
        except BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001
            return {"fix": "add_competitor", "applied": False, "why": f"verification failed: {type(e).__name__}"}, None
        if not v or not (v["is_software_vendor"] and v["same_vertical"] and v["operating"] and v["relevance"] >= 0.45):
            return {"fix": "add_competitor", "applied": False, "company": hit["name"], "why": "did not pass verification"}, None
        known = s_comp.record_evidence(st.ctx, v)
        st.comps["competitors"].append(known)
        st.logos[known["name"]] = s_logos.resolve_company(st.ctx, known["name"], known["domain"], [], known.get("homepage"),
                                                          st.ctx.run_dir / "logos")
    return ({"fix": "add_competitor", "applied": True, "company": known["name"]},
            f"market.competitors: add {known['name']} ({known['domain']}) to the map: {reason}")


def fix_resize_typeset(st: QAState, name: str, defect: str) -> dict:
    """A typeset wordmark on the map has no other logo to swap to, so a size defect resizes it instead:
    'logo_too_large' x0.8 (floor MIN_LOGO_H), 'logo_too_small' x1.15 (ceiling MAP_TYPESET_MAX_H). Once per logo;
    if the vision check still objects after that, a person should look."""
    lg = st.logos[name]
    if lg.get("resized"):
        st.review_notes.append(f"typeset logo for {name} still {defect.removeprefix('logo_').replace('_', ' ')} "
                               f"after one resize (now {lg['typeset_h']:.3f}in)")
        return {"fix": "resize_typeset", "applied": False, "company": name, "why": "already resized once"}
    old = lg.get("typeset_h") or MAP_LOGO["typeset_h"]
    new = max(MIN_LOGO_H, old * TYPESET_SHRINK) if defect == "logo_too_large" else min(MAP_TYPESET_MAX_H, old * TYPESET_GROW)
    new = round(new, 4)
    lg.update(typeset_h=new, resized=True)
    if new == old:
        st.review_notes.append(f"typeset logo for {name} is {defect.removeprefix('logo_').replace('_', ' ')} "
                               f"but already at its size limit ({old:.3f}in)")
        return {"fix": "resize_typeset", "applied": False, "company": name, "why": "at size limit"}
    return {"fix": "resize_typeset", "applied": True, "company": name, "from_h": round(old, 4), "to_h": new}


def fix_rewrite(st: QAState, path: str, reasons: list[str]) -> dict:
    n = st.rewrite_attempts.get(path, 0)
    if n >= 2:
        st.review_notes.append(f"{path}: still failing after 2 rewrite attempts ({'; '.join(reasons)[:200]})")
        return {"fix": "rewrite", "field": path, "applied": False, "why": "2 attempts used"}
    st.rewrite_attempts[path] = n + 1
    new, entry = s_write.rewrite_field(st.ctx, st.content, path, reasons, st.protected)
    if new is None and entry.get("best_effort"):
        new = entry["best_effort"]
        st.review_notes.append(f"{path}: no rewrite passed every check; used the closest one ('{new}')")
    if new is None:
        return {"fix": "rewrite", "field": path, "applied": False, **entry}
    try:
        st.content = s_write.set_field(st.content, path, new)
    except Exception as e:  # noqa: BLE001 - e.g. a stray line break the schema rejects
        return {"fix": "rewrite", "field": path, "applied": False, "why": str(e)[:200]}
    return {"fix": "rewrite", "applied": True, **entry}


def fix_shorten_name(st: QAState) -> dict:
    if st.shortened:
        return {"fix": "shorten_display_name", "applied": False, "why": "already shortened once"}
    st.shortened = True
    old = st.name
    short = s_write.shorten_display_name(st.ctx, st.entity)
    if not short:
        st.review_notes.append(f"company name '{old}' is too long for the titles and has no shorter brand form")
        return {"fix": "shorten_display_name", "applied": False, "why": "no shorter form fits"}
    data = json.loads(st.content.model_dump_json().replace(json.dumps(old)[1:-1], json.dumps(short)[1:-1]))
    data["company"]["display_name"] = short
    data["company"]["aliases"] = list(dict.fromkeys([old, *data["company"].get("aliases", [])]))
    st.content = DeckContent.model_validate(data, context={"min_competitors": 0})
    st.logos[short] = st.logos.pop(old)
    st.entity.update(display_name=short, aliases=data["company"]["aliases"])
    st.ctx.save_json("entity.json", st.entity)
    return {"fix": "shorten_display_name", "applied": True, "from": old, "to": short}


def _subfield(st: QAState, element: str, description: str) -> str:
    """Map a vision element id to the content field to rewrite."""
    d = description.lower()
    m = re.fullmatch(r"thesis\[(\d)\]", element)
    if m:
        t = st.content.thesis[int(m.group(1))]
        sub_hit = "subline" in d or "second line" in d or t.subline.lower()[:25] in d
        return f"thesis[{m.group(1)}].{'subline' if sub_hit else 'headline'}"
    if element == "market.our_take":
        for i, p in enumerate(st.content.market.our_take.points):
            if p.lower()[:25] in d or f"point {i + 1}" in d:
                return f"market.our_take.points[{i}]"
        return "market.our_take.headline"
    return element


THESIS_REGEN_CODES = {"lever_unsupported", "durability_missing", "why_now_unsupported"}


def apply_fixes(st: QAState, issues: list[qa.Issue], defects: list, critic_fixes: list[dict] | tuple = ()) -> list[dict]:
    """Route Layer-A errors, major vision defects and major critic findings through the fix menu (§9).

    rewrite_copy / rewrite_shorter   one field rewritten (2 per field); lever, durability and why-now problems
                                     re-ask the writer for the thesis only (2 per deck)
    regenerate_market                re-ask the writer for the market section only, the thesis frozen (2 per deck)
    add_competitor                   a candidate the critic named is verified and added via the market rewrite (1)
    swap_logo                        tagline micro-text: crop, then the next logo, then typeset; low resolution:
                                     a sharper alternative, then typeset; others: the next logo, then typeset
    relayout, shorten_display_name, rebuild  as before
    """
    fixes: list[dict] = []
    rewrites: dict[str, list[str]] = {}
    swaps: list[str] = []
    crops: list[str] = []
    sharper: list[str] = []
    resizes: dict[str, str] = {}
    relayouts: list[list[str]] = []
    market: list[str] = []
    thesis: list[str] = []
    add: list[dict] = []
    shorten = rebuild = False
    target_ids = {"cover_logo", "target_logo", f"logo:{st.name}"}

    for i in issues:
        if i.severity != "error":
            continue
        if i.fix == "regenerate_market":
            market.append(f"{i.field}: {i.message}")
        elif i.fix == "rewrite_copy" and i.code in THESIS_REGEN_CODES:
            thesis.append(f"{i.field}: {i.message}")
        elif i.fix in ("rewrite_shorter", "rewrite_copy"):
            rewrites.setdefault(i.field, []).append(i.message)
        elif i.fix == "swap_logo":
            name = i.field.removeprefix("logo:")
            (crops if i.code == "logo_tagline_microtext" else sharper if i.code == "logo_low_res" else swaps).append(name)
        elif i.fix == "relayout":
            relayouts.append([p.removeprefix("logo:") for p in i.field.split("|")])
        elif i.fix == "shorten_display_name":
            shorten = True
        elif i.fix == "rebuild":
            rebuild = True
        else:
            st.review_notes.append(f"{i.field}: {i.message}")
    for d in defects:
        if d.severity != "major":
            continue
        el = d.element_id or ""
        reason = f"vision check ({d.type}): {d.description}"
        if el in target_ids or el.startswith("logo:"):
            name = st.name if el in target_ids else el.removeprefix("logo:")
            on_map = el != "cover_logo"  # typeset_h only sizes map logos
            if on_map and d.type in ("logo_too_large", "logo_too_small") and (st.logos.get(name) or {}).get("typeset"):
                resizes.setdefault(name, d.type)
            elif d.type in ("logo_overlap", "logo_misaligned", "visual_imbalance") or d.suggested_fix == "relayout":
                relayouts.append([name])
            else:
                swaps.append(name)
        elif el in ("thesis.title", "market.title"):
            shorten = True
        elif re.fullmatch(r"thesis\[\d\]|market\.[a-z_.\[\]0-9]+", el):
            rewrites.setdefault(_subfield(st, el, d.description), []).append(reason)
        else:
            st.review_notes.append(f"slide {d.slide}: {d.description}")
    for f in critic_fixes:
        if f.get("severity") != "major":
            continue
        field, reason = f["field"], f["reason"]
        if f["fix"] == "regenerate_market":
            market.append(f"{field}: {reason}")
        elif f["fix"] == "rewrite_copy" and (f.get("category") == "generic_thesis" or not re.fullmatch(
                r"thesis\[\d\]\.(headline|subline)|market\.(subtitle|our_take\.headline|our_take\.points\[\d\])", field)):
            (thesis if field.startswith("thesis") or f.get("category") == "generic_thesis" else market).append(f"{field}: {reason}")
        elif f["fix"] in ("rewrite_copy", "rewrite_shorter"):
            rewrites.setdefault(field, []).append(reason)
        elif f["fix"] == "swap_logo":
            swaps.append(field.removeprefix("logo:"))
        elif f["fix"] == "relayout":
            relayouts.append([field.removeprefix("logo:")])
        elif f["fix"] == "add_competitor":
            add.append(f)
        else:
            st.review_notes.append(f"slide {f.get('slide')}: {reason}")

    if rebuild:
        if st.rebuilt:
            raise RuntimeError("template integrity check failed twice after a fresh rebuild (bug): "
                               + "; ".join(f"{i.field}: {i.message}" for i in issues if i.fix == "rebuild"))
        st.rebuilt = True
        fixes.append({"fix": "rebuild", "applied": True})
    if shorten:
        fixes.append(fix_shorten_name(st))
    for f in add[:1]:
        entry, problem = fix_add_competitor(st, f)
        fixes.append(entry)
        if problem:
            market.append(problem)
    regenerated: set[str] = set()
    if thesis:
        r = fix_regenerate(st, "thesis", thesis)
        fixes.append(r)
        if r["applied"]:
            regenerated.add("thesis")
    if market:
        r = fix_regenerate(st, "market", market)
        fixes.append(r)
        if r["applied"]:
            regenerated.add("market")
    for path, reasons in rewrites.items():
        if path in ("thesis.title", "market.title") or path.split("[")[0].split(".")[0] in regenerated:
            continue  # the section was rewritten as a whole; its old field problems no longer apply
        fixes.append(fix_rewrite(st, path, reasons))
    for name in dict.fromkeys(crops):
        if name in st.logos:
            fixes.append(fix_crop_tagline(st, name))
    for name in dict.fromkeys(sharper):
        if name in st.logos and name not in crops:
            fixes.append(fix_swap_logo(st, name, sharper=True))
    for name in dict.fromkeys(swaps):
        if name in st.logos and name not in resizes and name not in crops and name not in sharper:
            fixes.append(fix_swap_logo(st, name))
    for name, defect in resizes.items():
        fixes.append(fix_resize_typeset(st, name, defect))
    done_cells = set()
    for names in relayouts:
        movable = [n for n in names if n != st.name]
        key = tuple(sorted(movable))
        if movable and key not in done_cells:
            done_cells.add(key)
            fixes.append(fix_relayout(st, movable))
    return fixes


def run_audit(st: QAState) -> list[qa.Issue]:
    """The Haiku placement audit (one call per distinct deck; cached). A failure leaves the rules' verdict."""
    try:
        return s_placement.audit(st.ctx, st.content, st.ctx.evidence)
    except BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001 - e.g. an offline replay without the cached audit
        st.ctx.warn(f"placement audit unavailable ({type(e).__name__}); placements were checked by the rules only")
        return []


def run_critic(st: QAState, pngs: list, manifest: dict, h: str, critic: dict) -> list[dict]:
    """Fire after_vision (the founder-eyes critic, capped at 2 calls a deck); keep the newest verdict with the
    state it judged. Returns the critic's fix dicts."""
    before = len((st.ctx.report.get("critic") or {}).get("verdicts", []))
    out = hooks.fire("after_vision", ctx=st.ctx, content=st.content, pngs=pngs, manifest=manifest)
    verdicts = (st.ctx.report.get("critic") or {}).get("verdicts", [])
    if len(verdicts) > before:
        critic.update(verdict=verdicts[-1], hash=h)
    return [f for f in out if isinstance(f, dict) and "fix" in f]


def _critic_summary(v: dict | None) -> dict | None:
    if not v:
        return None
    return {"grades": v["slide_grades"], "mail_ready": v["mail_ready"],
            "major": [f"{f['target']}: {f['explanation']}" for f in v["findings"] if f["severity"] == "major"],
            "minor": len([f for f in v["findings"] if f["severity"] == "minor"])}


def qa_loop(st: QAState, progress: Progress, deadline: float) -> dict:
    """Each round: 1. Layer A (built-in + registered checks) and its fixes; 2. the placement audit (one Haiku call)
    and its market rewrite; 3. render + vision; 4. the critic, only after vision passes, and its fixes. A round
    that changes the deck is checked again in the next one; after the last round, one more render + vision (+ the
    critic, within its cap) verifies the final deck. The cost cap stops the fixing, never the delivery."""
    ctx, max_rounds = st.ctx, ctx_rounds(st.ctx)
    vision = ctx.settings.vision_qa
    rounds: list[dict] = []
    state = {"pptx": None, "manifest": None, "issues": []}

    def rebuild(k: int) -> None:
        state["pptx"], state["manifest"], state["issues"] = build_and_check(st, k)

    def errs() -> list[qa.Issue]:
        return [i for i in state["issues"] if i.severity == "error"]

    def budget() -> bool:
        if ctx.ledger.budget_hits and not st.budget_stop:
            st.budget_stop = ctx.ledger.budget_hits[0]
            st.review_notes.append(f"budget reached: {st.budget_stop}")
            ctx.warn("cost cap reached during QA: stopped fixing and delivered the best deck so far")
        return bool(st.budget_stop)

    def applied(rec: dict, key: str) -> bool:
        return any(f.get("applied") for f in rec.get(key, []))

    rebuild(0)
    can_render = vision  # --no-vision: no render, no PDF, layer A only
    if not vision:
        ctx.warn("visual QA skipped (--no-vision): layer A checks only, no PDF rendered")
    verdict, pdf, pngs, rendered_hash = None, None, None, None
    critic: dict = {"verdict": None, "hash": None}
    try:
        for rnd in range(1, max_rounds + 1):
            if budget():
                break
            rec: dict = {"round": rnd, "layer_a_before": [i.as_dict() for i in errs()]}
            if errs():
                progress(f"QA round {rnd}: fixing {len(errs())} layout/copy issue(s)")
                rec["fixes_layer_a"] = apply_fixes(st, errs(), [])
                rebuild(rnd)
                rec["layer_a_after"] = [i.as_dict() for i in errs()]
            if errs():
                rec.setdefault("vision", "skipped (--no-vision)" if not vision else None)
                rounds.append(rec)
                if budget() or not applied(rec, "fixes_layer_a"):
                    break
                continue
            audit = run_audit(st)
            if audit:
                rec["placement_audit"] = [i.as_dict() for i in audit]
                if not budget():
                    progress(f"QA round {rnd}: rewriting the market map ({len(audit)} placement issue(s))")
                    rec["fixes_audit"] = apply_fixes(st, audit, [])
                    if applied(rec, "fixes_audit"):
                        rebuild(rnd)
                        rounds.append(rec)
                        continue
            if not can_render:
                rec.setdefault("vision", "skipped (--no-vision)" if not vision else None)
                rounds.append(rec)
                break
            if time.time() > deadline:
                ctx.warn("time budget reached: skipped further visual QA rounds")
                rounds.append(rec)
                break
            h = st.state_hash()
            if h in st.seen_hashes:
                rec["stopped"] = "same content and logos as an earlier round (no progress)"
                rounds.append(rec)
                break
            st.seen_hashes.add(h)
            try:
                progress(f"QA round {rnd}: rendering and visual check")
                pdf, pngs, annotated = s_vision.render_slides(state["pptx"], ctx.run_dir, state["manifest"])
                rendered_hash = h
                verdict = s_vision.check(ctx, pngs, annotated, state["manifest"], st.content.model_dump(), f"vision_qa:r{rnd}")
            except OfflineMiss:
                ctx.warn("replay: visual check not in cache, skipped")
                rounds.append(rec)
                break
            except (RuntimeError, FileNotFoundError, OSError) as e:
                if "LibreOffice" in str(e) or isinstance(e, FileNotFoundError):
                    can_render = False
                    ctx.warn(f"rendering unavailable ({e}); delivered the .pptx without PDF or visual QA. "
                             "Install LibreOffice: macOS `brew install --cask libreoffice`, Linux `apt install libreoffice-impress`")
                    rounds.append(rec)
                    break
                raise
            rec["vision"] = verdict.model_dump()
            if verdict.passed:
                progress(f"QA round {rnd}: founder-eyes review")
                cfix = run_critic(st, pngs, state["manifest"], h, critic)
                if critic["hash"] == h:
                    rec["critic"] = _critic_summary(critic["verdict"])
                majors = [f for f in cfix if f.get("severity") == "major"]
                if majors and not budget():
                    progress(f"QA round {rnd}: fixing {len(majors)} finding(s) from the review")
                    rec["fixes_critic"] = apply_fixes(st, [], [], critic_fixes=majors)
                    rounds.append(rec)
                    if not applied(rec, "fixes_critic"):
                        break
                    rebuild(rnd)
                    continue
                rounds.append(rec)
                break
            majors = [d for d in verdict.defects if d.severity == "major"]
            progress(f"QA round {rnd}: fixing {len(majors)} visual defect(s)")
            rec["fixes_vision"] = apply_fixes(st, [], majors)
            rounds.append(rec)
            if not applied(rec, "fixes_vision"):
                break
            rebuild(rnd)
        # verify the last round's fixes (verification, not another fix round)
        h = st.state_hash()
        if (can_render and rendered_hash is not None and h not in st.seen_hashes and not errs() and not budget()
                and time.time() < deadline):
            st.seen_hashes.add(h)
            try:
                final: dict = {"round": "final-check"}
                audit = run_audit(st)
                if audit:
                    final["placement_audit"] = [i.as_dict() for i in audit]
                pdf, pngs, annotated = s_vision.render_slides(state["pptx"], ctx.run_dir, state["manifest"])
                rendered_hash = h
                verdict = s_vision.check(ctx, pngs, annotated, state["manifest"], st.content.model_dump(), "vision_qa:final")
                final["vision"] = verdict.model_dump()
                if verdict.passed:
                    run_critic(st, pngs, state["manifest"], h, critic)
                    if critic["hash"] == h:
                        final["critic"] = _critic_summary(critic["verdict"])
                rounds.append(final)
            except OfflineMiss:
                verdict = None
    except BudgetExceeded:
        budget()
        if st.state_hash() != (rendered_hash or "") and state["pptx"] is not None:
            rebuild(len(rounds) + 1)  # the deck the fixes left behind, not a stale build
    audit_final = [] if st.budget_stop else run_audit(st)  # cached when this deck was audited already
    # the delivered PDF must be the delivered PPTX
    if can_render and rendered_hash != st.state_hash():
        try:
            pdf, _, _ = s_vision.render_slides(state["pptx"], ctx.run_dir, state["manifest"])
            if verdict is not None and rendered_hash is not None:
                ctx.warn("final deck changed after the last visual check")
                verdict = None
        except (RuntimeError, FileNotFoundError, OSError) as e:
            can_render, pdf = False, None
            ctx.warn(f"rendering unavailable ({e}); delivered the .pptx only")
    return {"pptx": state["pptx"], "pdf": pdf, "manifest": state["manifest"], "issues": state["issues"],
            "verdict": verdict, "rounds": rounds, "can_render": can_render, "vision": vision, "audit": audit_final,
            "critic": {**critic, "current": critic["hash"] is not None and critic["hash"] == st.state_hash(),
                       "calls": (ctx.report.get("critic") or {}).get("calls", 0)}}


def ctx_rounds(ctx: Ctx) -> int:
    return ctx.settings.max_qa_rounds


# ------------------------------------------------------------------ sources.md
def write_sources(ctx: Ctx, entity: dict, content: DeckContent, comps: dict, logos: dict) -> Path:
    ev = ctx.evidence

    def cite(ids: list[str]) -> list[str]:
        out = []
        for i in ids:
            e = ev.get(i)
            if e:
                out.append(f'  - [{e.id}] "{e.quote}" — [{e.title or e.url}]({e.url})')
        return out or ["  - (no evidence id attached)"]

    L = [f"# Sources — Recur x {content.company.display_name}", "",
         f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}. Every quote below was found verbatim on the linked page.", "",
         "## Company", "",
         f"- **{content.company.display_name}** — [{entity['domain']}](https://{entity['domain']})"
         + (f" · also known as {', '.join(entity.get('aliases') or [])}" if entity.get("aliases") else ""),
         f"- Identity confidence {entity.get('confidence')} (margin {entity.get('margin')})"
         + (" · domain supplied by the user" if entity.get("domain_supplied") else "")
         + (" · LOW CONFIDENCE" if entity.get("low_confidence_identity") else ""),
         *cite(entity.get("evidence_ids", [])), "", "## Thesis (slide 2)", ""]
    for label, t in zip(("01 Why we're here", "02 Why we're excited", "03 How we can help"), content.thesis):
        L += [f"**{label}** — {t.headline} / {t.subline}", *cite(t.evidence_ids), ""]
    m = content.market
    L += ["## Market map (slide 3)", "", f"**Subtitle** — {m.subtitle}", *cite(m.subtitle_evidence_ids), "",
          f"**Our take** — {m.our_take.headline} · {' · '.join(m.our_take.points)}", *cite(m.our_take.evidence_ids), "",
          f"Axes: y = {m.y_axis.title} ({m.y_axis.low_label.replace(chr(10), ' ')} → {m.y_axis.high_label.replace(chr(10), ' ')}), "
          f"x = {m.x_axis.title} ({m.x_axis.low_label} → {m.x_axis.high_label})", "", "## Competitors", ""]
    verified = {v["name"]: v for v in comps["competitors"]}
    for c in m.competitors:
        v = verified.get(c.name, {})
        lg = logos.get(c.name, {})
        L += [f"### {c.name} — [{c.domain}](https://{c.domain})",
              f"- Placed {c.y}-{c.x}: {c.rationale}",
              f"- Segment: {v.get('segment', '?')} · Scope: {v.get('scope', '?')}",
              f"- Found via: {', '.join(v.get('channels', []))}" + (" · head-to-head comparison" if v.get("head_to_head") else "")
              + f" · relevance {v.get('relevance')} · {v.get('independent_sources', 0)} independent source(s)",
              f"- Logo: {lg.get('source', '?')}",
              *cite(list(dict.fromkeys([*c.evidence_ids, *v.get("evidence_ids", [])]))), ""]
    if ctx.report["fallbacks"] or ctx.report["flags"] or ctx.report["warnings"]:
        L += ["## Fallbacks, flags and warnings", ""]
        L += [f"- Fallback: {f}" for f in ctx.report["fallbacks"]]
        L += [f"- Flag: {k} = {v}" for k, v in ctx.report["flags"].items()]
        L += [f"- Warning: {w}" for w in ctx.report["warnings"]]
    p = ctx.run_dir / "sources.md"
    p.write_text("\n".join(L) + "\n", "utf-8")
    return p


# ------------------------------------------------------------------ report + delivery
def critic_findings(qa_out: dict, severity: str) -> list[dict]:
    """The critic's findings of one severity in its latest verdict. If the deck changed after that verdict (a fix
    was tried and the 2-call cap left no review of the result), its major findings still count as unresolved."""
    c = qa_out.get("critic") or {}
    if not c.get("verdict"):
        return []
    note = "" if c.get("current") else " (a fix was tried; the critic did not see the result)"
    return [{**f, "explanation": f["explanation"] + note} for f in c["verdict"]["findings"] if f["severity"] == severity]


def final_status(st: QAState | None, qa_out: dict | None, ctx: Ctx) -> str:
    """pass: no Layer-A error, the placement audit clean, vision passed and the critic found the final deck
    mail-ready. pass_with_warnings: only minor findings, warnings, thin market, typeset logos, or a check that could
    not run (e.g. --no-vision). needs_review: any unresolved error or major finding, or the cost cap was reached."""
    if st is None or qa_out is None:
        return "error"
    errors = [i for i in qa_out["issues"] if i.severity == "error"]
    v = qa_out["verdict"]
    vision = qa_out.get("vision", True)
    if (errors or st.review_notes or st.budget_stop or qa_out.get("audit") or critic_findings(qa_out, "major")
            or (vision and (not qa_out["can_render"] or v is None or not v.passed))):
        return "needs_review"
    warns = [i for i in qa_out["issues"] if i.severity == "warn"]
    minor = [d for d in v.defects if d.severity == "minor"] if v else []
    unreviewed = vision and not (qa_out.get("critic") or {}).get("current")
    if (warns or minor or critic_findings(qa_out, "minor") or unreviewed or ctx.report["fallbacks"]
            or ctx.report["flags"] or ctx.report["warnings"]):
        return "pass_with_warnings"
    return "pass"


def deliver(ctx: Ctx, out_dir: Path | None, files: list[Path | None]) -> list[str]:
    copied = []
    if out_dir is None:
        return copied
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        if f and Path(f).exists():
            dst = out_dir / Path(f).name
            shutil.copy2(f, dst)
            copied.append(str(dst))
    return copied


def _new_run_dir(settings: Settings, name: str, suffix: str = "") -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = settings.runs_dir / f"{slug(name)}-{ts}{suffix}"
    n = 1
    while d.exists():
        n += 1
        d = settings.runs_dir / f"{slug(name)}-{ts}{suffix}-{n}"
    d.mkdir(parents=True)
    return d


def _finish(ctx: Ctx, report: dict, t0: float) -> dict:
    report["seconds_total"] = round(time.time() - t0, 1)
    report["cost_usd"] = ctx.ledger.total_usd()
    report["cost_by_service"] = ctx.ledger.totals()
    report.update(fallbacks=ctx.report["fallbacks"] + [f for f in ctx.web.fallbacks if f not in ctx.report["fallbacks"]],
                  flags=ctx.report["flags"], warnings=ctx.report["warnings"])
    ctx.ledger.write(ctx.run_dir / "cost.json")
    ctx.evidence.save()
    ctx.save_json("qa_report.json", report)
    return report


def _qa_and_deliver(ctx: Ctx, st: QAState, report: dict, out_dir: Path | None, progress: Progress,
                    t0: float, timings: dict) -> Result:
    ts = time.time()
    qa_out = qa_loop(st, progress, t0 + HARD_TIMEOUT_S - 60)
    offline_gate(ctx, "qa", budget=False)
    timings["qa"] = round(time.time() - ts, 1)
    sources = write_sources(ctx, st.entity, st.content, st.comps, st.logos)
    status = final_status(st, qa_out, ctx)
    v = qa_out["verdict"]
    errors = [i.as_dict() for i in qa_out["issues"] if i.severity == "error"]
    cq = qa_out.get("critic") or {}
    review = list(st.review_notes)
    review += [f"placement audit: {i.field}: {i.message}" for i in qa_out.get("audit") or []]
    review += [f"critic ({f['persona']}, slide {f['slide']}) {f['target']}: {f['explanation']}"
               for f in critic_findings(qa_out, "major")]
    report.update(
        status=status, company=st.name, domain=st.entity["domain"], timings=timings,
        deliverables={"pptx": str(qa_out["pptx"]), "pdf": str(qa_out["pdf"]) if qa_out["pdf"] else None,
                      "sources": str(sources)},
        identity={k: st.entity.get(k) for k in ("display_name", "domain", "aliases", "confidence", "margin",
                                                 "domain_supplied", "low_confidence_identity", "rebrand_detected")},
        competitors=[{"name": c.name, "domain": c.domain, "cell": f"{c.y}-{c.x}",
                      "independent_sources": next((v2["independent_sources"] for v2 in st.comps["competitors"] if v2["name"] == c.name), None),
                      "channels": next((v2["channels"] for v2 in st.comps["competitors"] if v2["name"] == c.name), None)}
                     for c in st.content.market.competitors],
        logos={n: {k: l.get(k) for k in ("source", "typeset", "aspect")} for n, l in st.logos.items()
               if n == st.name or n in {c.name for c in st.content.market.competitors}},
        rounds=qa_out["rounds"], final_layer_a_errors=errors,
        final_layer_a_warnings=[i.as_dict() for i in qa_out["issues"] if i.severity == "warn"],
        final_vision=v.model_dump() if v else None, needs_review=review,
        placement_audit=[i.as_dict() for i in qa_out.get("audit") or []],
        critic={"calls": cq.get("calls", 0), "judged_final_deck": bool(cq.get("current")),
                **(_critic_summary(cq.get("verdict")) or {}),
                "findings": (cq.get("verdict") or {}).get("findings", [])},
        cost_cap_usd=ctx.settings.max_cost_usd, budget_reached=st.budget_stop,
    )
    stem = file_stem(st.name)
    extra = []
    for src, name in ((sources, f"{stem}_sources.md"), (ctx.run_dir / "qa_report.json", f"{stem}_qa_report.json")):
        extra.append((src, name))
    _finish(ctx, report, t0)
    copied = deliver(ctx, out_dir, [qa_out["pptx"], qa_out["pdf"]])
    if out_dir is not None:
        for src, name in extra:
            if Path(src).exists():
                shutil.copy2(src, out_dir / name)
                copied.append(str(out_dir / name))
    report["delivered_to"] = copied
    ctx.save_json("qa_report.json", report)
    hooks.fire("after_run", ctx=ctx, report=report)
    code = {"pass": 0, "pass_with_warnings": 1, "needs_review": 1}.get(status, 2)
    return Result(code, ctx.run_dir, report, qa_out["pptx"], qa_out["pdf"])


# ------------------------------------------------------------------ entry points
def run(name: str, *, domain: str | None = None, hint: str | None = None, non_interactive: bool = False,
        accept_low_confidence: bool = False, out_dir: Path | None = Path("output"), settings: Settings | None = None,
        choose: Callable[[list[dict]], str | None] | None = None, progress: Progress = lambda s: None,
        transport=None, claude_client=None) -> Result:
    settings = settings or Settings.load()
    t0 = time.time()
    run_dir = _new_run_dir(settings, name)
    handler = _attach_log(run_dir, settings)
    ctx = Ctx.create(settings, run_dir, transport=transport, claude_client=claude_client)
    ctx.strict_offline = settings.offline  # --offline: every call from cache; the first miss stops the run
    report: dict = {"input": {"name": name, "domain": domain, "hint": hint}, "run_dir": str(run_dir),
                    "settings": settings.redacted(), "started": datetime.now().isoformat(timespec="seconds")}
    timings: dict = {}
    stage = "setup"
    try:
        log.info("run %s: settings %s", name, json.dumps(settings.redacted(), default=str))
        try:
            from . import render
            render.install_fonts()
        except OSError as e:
            ctx.warn(f"could not install fonts ({e})")

        stage = "entity"
        progress(f"Identifying '{name}'")
        ts = time.time()
        try:
            entity = s_entity.resolve(ctx, name, domain, hint, accept_low_confidence=accept_low_confidence)
        except AmbiguousEntity as amb:
            pick = None if (non_interactive or choose is None) else choose(amb.candidates)
            if not pick:
                report.update(status="ambiguous_entity", candidates=amb.candidates)
                _finish(ctx, report, t0)
                return Result(3, run_dir, report, candidates=amb.candidates)
            entity = s_entity.resolve(ctx, name, pick, hint)
        offline_gate(ctx, stage)
        timings["entity"] = round(time.time() - ts, 1)
        progress(f"Company: {entity['display_name']} ({entity['domain']}), confidence {entity['confidence']}"
                 + (f", aliases {', '.join(entity['aliases'])}" if entity.get("aliases") else ""))

        stage = "profile"
        progress("Reading the company's website")
        ts = time.time()
        profile = s_profile.build(ctx, entity)
        offline_gate(ctx, stage)
        hooks.fire("after_profile", ctx=ctx, entity=entity, profile=profile)
        timings["profile"] = round(time.time() - ts, 1)
        progress(f"Profile: {profile['facts_kept']} verified facts from {len(profile['pages'])} pages")

        stage = "competitors"
        progress("Finding and verifying competitors")
        ts = time.time()
        comps = s_comp.discover(ctx, entity, profile)
        offline_gate(ctx, stage)
        hooks.fire("after_competitors", ctx=ctx, entity=entity, comps_out=comps)
        timings["competitors"] = round(time.time() - ts, 1)
        progress(f"Competitors: {', '.join(c['name'] for c in comps['competitors'])}"
                 + (" (thin market)" if comps["thin_market"] else ""))

        stage = "logos"
        progress("Sourcing and checking logos")
        ts = time.time()
        logos = s_logos.resolve_all(ctx, entity, comps["competitors"])
        offline_gate(ctx, stage)
        timings["logos"] = round(time.time() - ts, 1)

        stage = "write"
        progress("Writing the thesis and market map")
        ts = time.time()
        content = s_write.write_deck(ctx, entity, profile, comps)
        offline_gate(ctx, stage)
        hooks.fire("after_write", ctx=ctx, content=content)
        timings["write"] = round(time.time() - ts, 1)

        stage = "qa"
        st = QAState(ctx, entity, profile, comps, content, logos, extra_protected(entity),
                     graph=market_graph.for_run(settings))
        return _qa_and_deliver(ctx, st, report, out_dir, progress, t0, timings)
    except Exception as e:  # noqa: BLE001 - any stage failure: report it, exit 2
        if isinstance(e, (BudgetStop, BudgetExceeded)) or (ctx.ledger.budget_hits and not isinstance(e, OfflineCacheMiss)):
            msg = str(e) if isinstance(e, BudgetStop) else str(BudgetStop(stage, ctx.ledger.budget_hits[0] if ctx.ledger.budget_hits else str(e)))
            log.error("%s", msg)
            report.update(status="budget_exceeded", failed_stage=stage, error=msg, budget_hits=ctx.ledger.budget_hits[:20],
                          timings=timings, cost_cap_usd=ctx.settings.max_cost_usd)
            _finish(ctx, report, t0)
            return Result(2, run_dir, report)
        if ctx.strict_offline and ctx.cache.misses:  # the real cause, even if a fallback hid the miss
            miss = e if isinstance(e, OfflineCacheMiss) else OfflineCacheMiss(stage, ctx.cache.misses[0], len(ctx.cache.misses))
            log.error("%s", miss)
            report.update(status="offline_cache_miss", failed_stage=miss.stage, error=str(miss),
                          offline_misses=ctx.cache.misses[:50], timings=timings)
            _finish(ctx, report, t0)
            return Result(2, run_dir, report)
        log.exception("stage %s failed", stage)
        report.update(status="error", failed_stage=stage, error=f"{type(e).__name__}: {e}",
                      traceback=traceback.format_exc(limit=6).splitlines()[-12:], timings=timings)
        _finish(ctx, report, t0)
        return Result(2, run_dir, report)
    finally:
        ctx.http.close()
        logging.getLogger("recur_deck").removeHandler(handler)
        handler.close()


def replay(src_dir: Path, *, out_dir: Path | None = None, settings: Settings | None = None,
           progress: Progress = lambda s: None) -> Result:
    """Rebuild a finished run from its JSON and the response cache, without network (offline mode)."""
    from dataclasses import replace as dc_replace
    settings = dc_replace(settings or Settings.load(), offline=True)
    src_dir = Path(src_dir)
    entity = json.loads((src_dir / "entity.json").read_text())
    t0 = time.time()
    run_dir = _new_run_dir(settings, entity["display_name"], "-replay")
    handler = _attach_log(run_dir, settings)
    ctx = Ctx.create(settings, run_dir)
    report: dict = {"input": {"replay": str(src_dir)}, "run_dir": str(run_dir), "settings": settings.redacted()}
    try:
        ctx.evidence = EvidenceStore.load(src_dir / "evidence.jsonl")
        ctx.evidence.path = run_dir / "evidence.jsonl"
        profile = json.loads((src_dir / "profile.json").read_text())
        comps = json.loads((src_dir / "competitors.json").read_text())
        logos = json.loads((src_dir / "logo_metrics.json").read_text())
        content = DeckContent.model_validate_json((src_dir / "content.json").read_text(), context={"min_competitors": 0})
        for f in ("entity.json", "profile.json", "competitors.json", "candidates.json", "logo_metrics.json"):
            if (src_dir / f).exists():
                shutil.copy2(src_dir / f, run_dir / f)
        if (src_dir / "qa_report.json").exists():  # research-stage flags, fallbacks and warnings still apply
            orig = json.loads((src_dir / "qa_report.json").read_text())
            ctx.report["flags"].update(orig.get("flags") or {})
            for k in ("fallbacks", "warnings"):
                ctx.report[k] += [x for x in orig.get(k) or [] if x not in ctx.report[k]]
        progress(f"Replaying {entity['display_name']} from {src_dir} (offline)")
        st = QAState(ctx, entity, profile, comps, content, logos, extra_protected(entity),
                     graph=market_graph.for_run(settings))
        return _qa_and_deliver(ctx, st, report, out_dir, progress, t0, {})
    except Exception as e:  # noqa: BLE001
        log.exception("replay failed")
        report.update(status="error", failed_stage="replay", error=f"{type(e).__name__}: {e}",
                      traceback=traceback.format_exc(limit=6).splitlines()[-12:])
        _finish(ctx, report, t0)
        return Result(2, run_dir, report)
    finally:
        logging.getLogger("recur_deck").removeHandler(handler)
        handler.close()
