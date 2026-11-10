"""S5 - strategy + writing (Claude Opus, structured output = models.DeckContent) and single-field rewrites.

The writer chooses words, axes and placements; code enforces identity and evidence afterwards:
- company identity comes from entity.json, never from the model;
- a competitor that is not in the verified list is dropped (no companies from model memory);
- evidence ids that do not exist are removed.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, ValidationError

from .. import fonts, qa
from ..models import DeckContent, _norm_domain
from ..spec import LINE_BUDGETS
from .base import Ctx, StageError, prompt, registrable

log = logging.getLogger("recur_deck.write")

RECUR_LEVERS = [
    {"lever": "AI-native GTM", "proof": "Recur OS -> 40% of August new logos and +50% demos YoY (statii)"},
    {"lever": "AI rebuild", "proof": "Recur FDE rebuilt the product in less than three months (cognidox)"},
    {"lever": "AI modernization", "proof": "Rebuilt legacy form layer in C#, >10x faster and cheaper (Keystone)"},
    {"lever": "Built the bench", "proof": "Recur brought in a new CEO, Head of Sales, VP Eng (collect!)"},
    {"values": ["Stewardship", "Growth", "Partnership", "Ambition", "Speed"],
     "model": "Recur buys and holds vertical software forever, with no fund clock and no exit plan"},
]


class Rewrites(BaseModel):
    candidates: list[str]  # exactly 3, best first


class ShortName(BaseModel):
    short_name: str


def min_competitors(n_verified: int, thin: bool) -> int:
    return max(2, min(5, n_verified)) if thin or n_verified < 5 else 5


def _inputs(ctx: Ctx, entity: dict, profile: dict, comps: list[dict]) -> list[tuple[str, object]]:
    facts = {k: v for k, v in profile.items() if isinstance(v, list) and v and isinstance(v[0], dict)}
    ids = list(entity.get("evidence_ids", []))
    for v in facts.values():
        ids += [f["evidence_id"] for f in v]
    for c in comps:
        ids += c["evidence_ids"]
    comp_view = [{k: c[k] for k in ("name", "domain", "segment", "scope", "channels", "head_to_head", "relevance",
                                    "evidence_ids")} for c in comps]
    ent = {k: entity.get(k) for k in ("display_name", "legal_name", "domain", "aliases", "vertical", "one_liner", "evidence_ids")}
    return [("entity", ent), ("profile", facts), ("competitors", comp_view),
            ("evidence", ctx.evidence.subset(ids)), ("recur_levers", RECUR_LEVERS)]


def enforce(data: dict, entity: dict, comps: list[dict], valid_ids: set[str]) -> tuple[dict, list[str]]:
    """Pin identity, keep only verified competitors, drop unknown evidence ids. Returns (data, notes)."""
    notes = []
    data["company"] = {"display_name": entity["display_name"], "domain": entity["domain"], "aliases": entity.get("aliases", [])}
    by_dom = {_norm_domain(c["domain"]): c for c in comps}
    by_name = {re.sub(r"[^a-z0-9]", "", c["name"].lower()): c for c in comps}
    kept = []
    for c in data.get("market", {}).get("competitors", []):
        v = by_dom.get(_norm_domain(c.get("domain", ""))) or by_dom.get(registrable(c.get("domain", ""))) \
            or by_name.get(re.sub(r"[^a-z0-9]", "", c.get("name", "").lower()))
        if not v:
            notes.append(f"dropped unverified competitor '{c.get('name')}' ({c.get('domain')})")
            continue
        c["name"], c["domain"] = v["name"], v["domain"]
        c["evidence_ids"] = [i for i in c.get("evidence_ids", []) if i in valid_ids] or list(v["evidence_ids"][:3])
        kept.append(c)
    data["market"]["competitors"] = kept
    for t in data.get("thesis", []):
        t["evidence_ids"] = [i for i in t.get("evidence_ids", []) if i in valid_ids]
    m = data.get("market", {})
    m["subtitle_evidence_ids"] = [i for i in m.get("subtitle_evidence_ids", []) if i in valid_ids]
    if "our_take" in m:
        m["our_take"]["evidence_ids"] = [i for i in m["our_take"].get("evidence_ids", []) if i in valid_ids]
    return data, notes


def write_deck(ctx: Ctx, entity: dict, profile: dict, comps_out: dict, problems: list[str] | None = None) -> DeckContent:
    comps = comps_out["competitors"]
    mc = min_competitors(len(comps), comps_out.get("thin_market", False))
    system = prompt("write_deck", STYLE_RULES=qa.STYLE_RULES_FOR_PROMPT)
    blocks = _inputs(ctx, entity, profile, comps)
    if mc < 5:
        blocks.append(("note", f"This is a thin market: place all {len(comps)} competitors you were given "
                               f"(at least {mc}); still use at least 3 of the 4 cells counting the target's."))
    valid = {e.id for e in ctx.evidence.items()}
    for attempt in range(2):
        b = blocks + ([("problems_with_previous_draft", problems)] if problems else [])
        raw = ctx.claude.ask(ctx.settings.model_writer, system, b, DeckContent, effort="high", max_tokens=32000,
                             context={"min_competitors": min(mc, 2)}, label="write_deck")
        data, notes = enforce(raw.model_dump(), entity, comps, valid)
        for n in notes:
            ctx.warn(n)
        try:
            content = DeckContent.model_validate(data, context={"min_competitors": mc})
            ctx.save_json("content.json", content.model_dump())
            return content
        except ValidationError as e:
            problems = [str(err.get("msg")) for err in e.errors()] + notes
            log.info("writer draft invalid after enforcement (%s); asking again", problems)
    raise StageError(f"writer could not produce a valid deck: {problems}")


# ------------------------------------------------------------------ rewrite one field
def get_field(content: DeckContent, path: str) -> str:
    m = re.fullmatch(r"([a-z_.]+)(?:\[(\d+)\])?(?:\.([a-z_]+))?", path)
    node: object = content.model_dump()
    keys = m.group(1).split(".")
    for k in keys:
        node = node[k]  # type: ignore[index]
    if m.group(2) is not None:
        node = node[int(m.group(2))]  # type: ignore[index]
    if m.group(3):
        node = node[m.group(3)]  # type: ignore[index]
    return node  # type: ignore[return-value]


def set_field(content: DeckContent, path: str, value: str) -> DeckContent:
    data = content.model_dump()
    m = re.fullmatch(r"([a-z_.]+)(?:\[(\d+)\])?(?:\.([a-z_]+))?", path)
    keys, idx, leaf = m.group(1).split("."), m.group(2), m.group(3)
    node = data
    for k in keys[:-1]:
        node = node[k]
    if idx is None:
        node[keys[-1]] = value
    elif leaf is None:
        node[keys[-1]][int(idx)] = value
    else:
        node[keys[-1]][int(idx)][leaf] = value
    return DeckContent.model_validate(data, context={"min_competitors": 0})


def _neighbours(c: DeckContent) -> dict:
    return {"thesis": [{"headline": t.headline, "subline": t.subline} for t in c.thesis],
            "market_subtitle": c.market.subtitle, "our_take": c.market.our_take.model_dump(exclude={"evidence_ids"}),
            "axes": {"x": c.market.x_axis.model_dump(), "y": c.market.y_axis.model_dump()}}


def _budget(path: str) -> str:
    if path.endswith("headline") and path.startswith("thesis"):
        f, pt, w = LINE_BUDGETS["thesis_headline"]
        return f"one line, at most {w} in at {pt}pt (about 60 characters); aim for 6-11 words"
    if path.endswith("subline"):
        f, pt, w = LINE_BUDGETS["thesis_subline"]
        return f"one line, at most {w} in at {pt}pt (about 78 characters); aim for 10-14 words"
    if path == "market.subtitle":
        return "one line, at most 8.5 in at 12pt (about 95 characters), <= 15 words"
    if "y_axis" in path and "label" in path:
        return 'at most 2 short lines of <= 12 characters, separated by "\\n" (e.g. "Small &\\nmid brands")'
    if "x_axis" in path and "label" in path:
        return "at most 20 characters"
    if path.endswith("title"):
        return "1-3 words"
    if path == "market.our_take.headline":
        return "<= 14 words, starts with the company name, wraps to <= 4 lines in a 1.86 in column"
    if "our_take.points" in path:
        return "<= 10 words, wraps to <= 2 lines in a 1.86 in column"
    return ""


def rewrite_field(ctx: Ctx, content: DeckContent, path: str, reasons: list[str],
                  extra_protected: list[str]) -> tuple[str | None, dict]:
    """Ask for 3 rewrites of one field; keep the first that passes qa.check_field (errors only). Two calls max.
    Returns (new_text or None, log entry). None means: nothing passed; caller keeps the best-effort text."""
    current = get_field(content, path)
    style = qa.STYLE_RULES_FOR_PROMPT + " Plain declarative sentences, one idea per line, sentence case, " \
                                        "concrete industry nouns, digits for numbers, 'and' instead of '&' (except axis labels)."
    tried: list[dict] = []
    best: tuple[int, str] | None = None
    for call in range(2):
        blocks = [("field", path), ("current_text", current), ("why_it_failed", reasons), ("length_budget", _budget(path)),
                  ("style_rules", style), ("neighbouring_lines", _neighbours(content)),
                  ("company", content.company.display_name)]
        if tried:
            blocks.append(("previous_candidates_that_failed", tried))
        try:
            rw = ctx.claude.ask(ctx.settings.model_writer, prompt("rewrite_field"), blocks, Rewrites, effort="medium",
                                label=f"rewrite:{path}")
        except Exception as e:  # noqa: BLE001
            return None, {"field": path, "error": f"{type(e).__name__}: {e}"}
        for cand in [c.strip() if "y_axis" in path else c.strip().rstrip(".").strip() for c in rw.candidates[:3]]:
            errs = qa.check_field(path, cand, content, extra_protected)
            tried.append({"text": cand, "errors": [i.message for i in errs]})
            if not errs:
                return cand, {"field": path, "from": current, "to": cand, "calls": call + 1, "tried": tried}
            if best is None or len(errs) < best[0]:
                best = (len(errs), cand)
        reasons = sorted({e for t in tried for e in t["errors"]})
    return None, {"field": path, "from": current, "best_effort": best[1] if best else None, "calls": 2, "tried": tried}


# ------------------------------------------------------------------ shorten the display name
def shorten_display_name(ctx: Ctx, entity: dict) -> str | None:
    name = entity["display_name"]
    words = name.split()
    options = [" ".join(words[:i]) for i in range(1, len(words))] + [a for a in entity.get("aliases", []) if len(a) < len(name)]
    fits = lambda n: all(fonts.text_width_in(t, *LINE_BUDGETS[k][:2]) <= LINE_BUDGETS[k][2]  # noqa: E731
                         for t, k in ((f"What we see in {n}", "thesis_title"), (f"The opportunity for {n}", "market_title")))
    options = [o for o in dict.fromkeys(options) if o and fits(o)]
    if not options:
        return None
    page = ctx.pages.get(entity["domain"])
    evidence = {"title": page.title if page else "", "description": page.description if page else "",
                "aliases": entity.get("aliases", []), "one_liner": entity.get("one_liner")}
    try:
        ans = ctx.claude.ask(ctx.settings.model_judge, prompt("short_name"),
                             [("full_name", name), ("options", options), ("evidence", evidence)], ShortName,
                             effort="low", label="short_name", fallbacks=ctx.judge_fallbacks())
        if ans.short_name in options:
            return ans.short_name
    except Exception as e:  # noqa: BLE001
        log.info("short_name call failed: %s", e)
    return options[-1]  # the longest form that fits
