"""Placement audit (Agent 3): does each cited quote really support where the deck puts a competitor, and does each
"our take" point rest on a quote showing the group really lacks the thing?

One Haiku call per deck (model_fast, structured output, cached like every call). Items without any cited quote
are not sent: checks/market.placement_issues already flags them. `insufficient` and `contradicts` become
`placement_unsupported` issues with fix `regenerate_market`.
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel

from ..axes import AXIS_KINDS
from ..models import DeckContent
from ..qa import Issue
from .base import Ctx, prompt

log = logging.getLogger("recur_deck.placement")


class PlacementVerdict(BaseModel):
    item_id: str
    verdict: Literal["supports", "contradicts", "insufficient"]
    quote: str
    note: str


class PlacementVerdicts(BaseModel):
    verdicts: list[PlacementVerdict]


def _quotes(evidence, ids: list[str]) -> list[dict]:
    out = []
    for i in dict.fromkeys(ids):
        e = evidence.get(i) if evidence is not None else None
        if e:
            out.append({"id": e.id, "quote": e.quote, "url": e.url})
    return out


def audit_items(content: DeckContent, evidence) -> list[dict]:
    m = content.market
    items = []
    for i, c in enumerate(m.competitors):
        for key, ax in (("x", m.x_axis), ("y", m.y_axis)):
            ids = list(getattr(c, f"{key}_evidence_ids") or c.evidence_ids)
            qs = _quotes(evidence, ids)
            if not qs:
                continue
            side = "high" if getattr(c, key) in ("right", "top") else "low"
            spec = AXIS_KINDS.get(ax.kind or "", {})
            items.append({"item_id": f"c{i}.{key}", "company": c.name, "domain": c.domain,
                          "axis": {"kind": ax.kind, "title": ax.title, "low_end": spec.get("low"), "high_end": spec.get("high"),
                                   "low_label": ax.low_label.replace("\n", " "), "high_label": ax.high_label.replace("\n", " ")},
                          "placed_at": f"{side} end: '{(ax.high_label if side == 'high' else ax.low_label).replace(chr(10), ' ')}'",
                          "quotes": qs})
    ot = m.our_take
    for j, point in enumerate(ot.points):
        ids = ot.point_evidence_ids[j] if j < len(ot.point_evidence_ids) else []
        qs = _quotes(evidence, ids)
        if qs:
            items.append({"item_id": f"p{j}", "our_take_point": point, "quotes": qs})
    return items


def to_issues(content: DeckContent, verdicts: PlacementVerdicts, items: list[dict]) -> list[Issue]:
    sent = {it["item_id"]: it for it in items}
    by_field: dict[str, list[str]] = {}
    for v in verdicts.verdicts:
        it = sent.get(v.item_id)
        if it is None or v.verdict == "supports":
            continue
        if v.item_id.startswith("c"):
            idx, key = v.item_id[1:].split(".")
            field = f"market.competitors[{idx}]"
            what = f"{it['company']} on {it['axis']['title']} ({it['placed_at']})"
        else:
            field = f"market.our_take.points[{v.item_id[1:]}]"
            what = f"our take '{it['our_take_point']}'"
        quote = f' ("{v.quote[:140]}")' if v.quote else ""
        by_field.setdefault(field, []).append(f"{what}: the cited quotes {'contradict it' if v.verdict == 'contradicts' else 'do not support it'}"
                                              f"{quote}; {v.note}".rstrip("; "))
    return [Issue("placement_unsupported", "error", 3, f, "placement audit: " + " | ".join(msgs), "regenerate_market")
            for f, msgs in by_field.items()]


def audit(ctx: Ctx, content: DeckContent, evidence=None) -> list[Issue]:
    evidence = evidence if evidence is not None else ctx.evidence
    items = audit_items(content, evidence)
    if not items:
        return []
    verdicts = ctx.claude.ask(ctx.settings.model_fast, prompt("placement_audit"), [("items", items)], PlacementVerdicts,
                              effort="low", max_tokens=8000, label="placement_audit", fallbacks=[ctx.settings.model_judge])
    issues = to_issues(content, verdicts, items)
    log.info("placement audit: %d item(s), %d issue(s)", len(items), len(issues))
    return issues


def audit_after_write(ctx: Ctx, content: DeckContent) -> list[str]:
    """Problem strings for a caller that wants them as text. The QA loop calls `audit` itself, after Layer A and
    before rendering (pipeline.run_audit), so this is no longer registered on after_write."""
    return [f"{i.field}: {i.message}" for i in audit(ctx, content)]
