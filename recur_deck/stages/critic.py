"""Founder-eyes critic (Agent 5): the judgment layer for what no rule catches.

One Opus call per round (settings.model_writer, effort high, structured output) reads the rendered slides 2 and 3,
the deck's words, the quotes they cite, the verified competitors, where earlier decks placed them (market graph)
and Recur's own reference text, then answers as two people at once: the target's founder and the Recur partner who
signs the letter. Runs only after the vision check passes (hooks.after_vision), at most MAX_CALLS times per deck.

`review(...) -> CriticVerdict`; `to_fixes(verdict) -> list[dict]` maps findings to the QA loop's fix vocabulary:
  copied_language / generic_thesis / negative_tone, and unsupported claims in the thesis  -> rewrite_copy
  disputed_placement / axis_not_ordinal / target_isolated, and market claims             -> regenerate_market
  missing_competitor                                                                       -> add_competitor
  illegible_logo                                                                           -> swap_logo
A finding is major only when a founder would notice it on paper and it would lower trust; `mail_ready` is forced
false whenever a major finding exists.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .. import hooks
from ..spec import KIT_ROOT
from .base import Ctx, prompt, registrable

log = logging.getLogger("recur_deck.critic")
MAX_CALLS = 2
REFERENCE_PATH = KIT_ROOT / "assets" / "reference_text.json"
Grade = Literal["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"]


class Finding(BaseModel):
    persona: Literal["founder", "partner"]
    slide: Literal[2, 3]
    target: str = Field(description='Content field path, e.g. "thesis[2].subline", "market.competitors[3]", '
                                    '"market.y_axis", or "logo:<company name>"')
    category: Literal["unsupported_claim", "disputed_placement", "missing_competitor", "axis_not_ordinal",
                      "target_isolated", "copied_language", "generic_thesis", "illegible_logo", "negative_tone", "other"]
    severity: Literal["major", "minor"]
    explanation: str = Field(description="One or two sentences; cite evidence ids where relevant")
    suggested_fix: Literal["rewrite_copy", "rewrite_shorter", "regenerate_market", "relayout", "swap_logo",
                           "add_competitor", "none"]


class SlideGrades(BaseModel):
    """Fixed keys: structured outputs turn a free-form dict into an object that allows no keys at all."""
    slide2: Grade
    slide3: Grade


class CriticVerdict(BaseModel):
    findings: list[Finding]
    slide_grades: SlideGrades
    mail_ready: bool = Field(description="true only if there is no major finding")

    def majors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "major"]


# ------------------------------------------------------------------ inputs
def reference_text() -> dict:
    return json.loads(REFERENCE_PATH.read_text("utf-8"))


def cited_ids(content) -> list[str]:
    from ..checks.market import slide3_ids
    ids = [i for t in content.thesis for i in t.evidence_ids] + list(content.lever_gap_evidence_ids) + slide3_ids(content)
    return list(dict.fromkeys(ids))


def _evidence_view(evidence, ids: list[str]) -> list[dict]:
    out = []
    for i in ids:
        e = evidence.get(i) if evidence is not None else None
        if e:
            out.append({"id": e.id, "quote": e.quote, "url": e.url, "domain": registrable(e.url), "about": e.about,
                        "kind": e.kind})
    return out


def _competitor_view(comps: dict | None) -> dict:
    comps = comps or {}
    keep = ("name", "domain", "segment", "scope", "relevance", "channels", "segments")
    return {"verified": [{k: v.get(k) for k in keep if v.get(k) is not None} for v in comps.get("competitors", [])],
            "verified_but_not_kept": [{"name": v.get("name"), "domain": v.get("domain"), "relevance": v.get("relevance"),
                                       "same_vertical": v.get("same_vertical")} for v in comps.get("rejected", [])]}


def _history_view(graph, content) -> list[dict]:
    out = []
    for c in content.market.competitors:
        ps = graph.placements(c.domain) if graph is not None else []
        for p in ps[:4]:
            out.append({"company": c.name, "run": p.get("run"), "axis_kind": p.get("axis_kind"), "side": p.get("side"),
                        **({"label": p["label"]} if p.get("label") else {}),
                        **({"quote": p["quotes"][0][:200]} if p.get("quotes") else {})})
    return out


def _logo_view(manifest: dict, logos: dict | None) -> list[dict]:
    out = []
    for e in manifest.get("elements", []):
        if e.get("kind") != "logo":
            continue
        lg = (logos or {}).get(e.get("label"), {})
        out.append({"id": e["id"], "company": e.get("label"), "slide": e["slide"], "printed_in": [round(e["w"], 2), round(e["h"], 2)],
                    "typeset": bool(lg.get("typeset")),
                    **({"dpi": round(lg["native_w"] / e["w"])} if lg.get("native_w") and e.get("w") else {})})
    return out


def _comps_from_run(ctx: Ctx) -> dict | None:
    p = Path(ctx.run_dir) / "competitors.json"
    try:
        return json.loads(p.read_text("utf-8")) if p.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def review(ctx: Ctx, content, pngs: list, manifest: dict, evidence, graph, comps: dict | None = None,
           logos: dict | None = None, label: str = "critic") -> CriticVerdict:
    """One critic call. `pngs` are the rendered slides 1-3 (slides 2 and 3 are sent, <= 2576 px long edge)."""
    pngs = [Path(p) for p in pngs]
    images = [(f"Slide {n} as printed", pngs[n - 1].read_bytes()) for n in (2, 3) if len(pngs) >= n]
    blocks = [("deck_content", content.model_dump()),
              ("cited_evidence", _evidence_view(evidence, cited_ids(content))),
              ("competitors", _competitor_view(comps if comps is not None else _comps_from_run(ctx))),
              ("earlier_placements", _history_view(graph, content)),
              ("logos", _logo_view(manifest, logos)),
              ("recur_reference_text", reference_text())]
    v = ctx.claude.ask(ctx.settings.model_writer, prompt("critic"), blocks, CriticVerdict, images=images, effort="high",
                       max_tokens=16000, label=label)
    v.mail_ready = v.mail_ready and not v.majors()  # a major finding always blocks mailing, whatever the model said
    return v


# ------------------------------------------------------------------ findings -> fixes
THESIS_FIELD = re.compile(r"^thesis\[(\d)\](?:\.(headline|subline))?$")


def _field(f: Finding) -> str:
    t = f.target.strip()
    m = THESIS_FIELD.match(t)
    if m:
        return f"thesis[{m.group(1)}].{m.group(2) or 'headline'}"
    return t


def to_fixes(verdict: CriticVerdict) -> list[dict]:
    """Every finding as {fix, field, severity, category, persona, reason} in the QA loop's vocabulary."""
    out = []
    for f in verdict.findings:
        field = _field(f)
        if f.category == "illegible_logo" or field.startswith("logo:"):
            fix = "swap_logo"
            if not field.startswith("logo:"):
                field = f"logo:{field}"
        elif f.category == "missing_competitor":
            fix = "add_competitor"
        elif f.category in ("copied_language", "generic_thesis", "negative_tone"):
            fix = "rewrite_copy" if not field.startswith("market.competitors") else "regenerate_market"
        elif f.category in ("disputed_placement", "axis_not_ordinal", "target_isolated"):
            fix = "regenerate_market"
        elif f.category == "unsupported_claim":
            fix = "rewrite_copy" if field.startswith("thesis") or field == "market.subtitle" else "regenerate_market"
        else:
            fix = f.suggested_fix if f.suggested_fix != "none" else "needs_review"
        out.append({"fix": fix, "field": field, "severity": f.severity, "category": f.category, "persona": f.persona,
                    "slide": f.slide, "reason": f"critic ({f.persona}, {f.category}): {f.explanation}"})
    return out


# ------------------------------------------------------------------ hook
@hooks.on("after_vision")
def critic_after_vision(ctx: Ctx, content, pngs, manifest) -> list[dict]:
    """Called when the vision check passes. Records each verdict in ctx.report["critic"]; at most MAX_CALLS calls."""
    from .. import market_graph
    state = ctx.report.setdefault("critic", {"calls": 0, "verdicts": []})
    if state["calls"] >= MAX_CALLS:
        log.info("critic: %d calls used for this deck; not called again", state["calls"])
        return []
    state["calls"] += 1
    v = review(ctx, content, pngs, manifest, ctx.evidence, market_graph.for_run(ctx.settings),
               label=f"critic:r{state['calls']}")
    state["verdicts"].append(v.model_dump())
    log.info("critic: grades %s/%s, %d major finding(s), mail_ready=%s", v.slide_grades.slide2, v.slide_grades.slide3,
             len(v.majors()), v.mail_ready)
    return to_fixes(v)
