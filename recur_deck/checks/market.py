"""Market-map plausibility (Agent 3): deterministic checks on slide 3's judgment, run in Layer A.

  axis_issues          axis_not_ordinal       an axis kind missing/unknown, both axes one kind, labels upside down
  placement_issues     placement_unsupported  a placement or "our take" claim with no (or an unknown) quote, or
                                              justified by what a page does not say ("no financials cited")
  diversity_issues     evidence_not_diverse   one domain supplies > 40% of slide 3's evidence, or a competitor is
                                              placed only from a rival's marketing page
  plausibility_issues  target_alone, empty_cells, segment_missing
  consistency_issues   placement_contradicts_history  a placement that contradicts where earlier decks (the market
                                              graph) put that company, with no new quote that supports the change

Every issue is an error with fix `regenerate_market` (re-write the market section; routed by the QA loop) and a
message the writer can act on. The Haiku placement audit (stages/placement.py) is the judgment pass on top.
"""
from __future__ import annotations

import re

from ..axes import AXIS_KINDS, cue_hits, read_side
from ..evidence import normalise
from ..models import DeckContent
from ..qa import Issue
from ..spec import CELLS, TARGET_CELL
from . import register

NEUTRAL_DIRECTORIES = {"capterra.com", "g2.com", "getapp.com", "softwareadvice.com", "softwareconnect.com",
                       "trustradius.com"}
MAX_DOMAIN_SHARE = 0.40
MIN_IDS_FOR_SHARE = 6  # a share of 2 out of 3 ids says nothing
ABSENCE = re.compile(r"\bno\b.{0,40}\b(cited|mentioned|found|listed|named|shown)\b|\bnot (cited|mentioned|listed|found|named)\b"
                     r"|\bwithout (a |an )?[\w-]+ (module|feature|tool)s?\b", re.I)
FIX = "regenerate_market"


def _issue(code: str, field: str, msg: str) -> Issue:
    return Issue(code, "error", 3, field, msg, FIX)


def _registrable(url: str) -> str:
    from ..stages.base import registrable
    return registrable(url)


def _quotes(evidence, ids: list[str]) -> list[str]:
    if evidence is None:
        return []
    return [e.quote for e in (evidence.get(i) for i in ids) if e]


def _axis_ids(c, key: str) -> list[str]:
    """The quotes a competitor's x (or y) placement cites; decks written before per-axis ids fall back to the
    competitor's evidence ids."""
    return list(getattr(c, f"{key}_evidence_ids") or c.evidence_ids)


def _label(text: str) -> str:
    return (text or "").replace("\n", " ")


# ------------------------------------------------------------------ A. ordinal axes
def axis_issues(content: DeckContent) -> list[Issue]:
    m = content.market
    out: list[Issue] = []
    kinds = ", ".join(f"{k} ({v['low']} -> {v['high']})" for k, v in AXIS_KINDS.items())
    for key, ax in (("market.x_axis", m.x_axis), ("market.y_axis", m.y_axis)):
        lo, hi = _label(ax.low_label), _label(ax.high_label)
        if ax.kind not in AXIS_KINDS:
            out.append(_issue("axis_not_ordinal", key,
                              f"axis '{ax.title}' ('{lo}' -> '{hi}') is not a scale a reader can follow: pick one of "
                              f"the ordinal kinds {kinds}"))
            continue
        spec = AXIS_KINDS[ax.kind]
        lo_hi, lo_lo = cue_hits(ax.kind, lo)
        hi_hi, hi_lo = cue_hits(ax.kind, hi)
        if (hi_lo and not hi_hi) or (lo_hi and not lo_lo):
            out.append(_issue("axis_not_ordinal", key,
                              f"axis '{ax.title}' reads upside down for {ax.kind}: the low end ('{lo}') must be "
                              f"{spec['low']} and the high end ('{hi}') {spec['high']}"))
    if m.x_axis.kind and m.x_axis.kind == m.y_axis.kind:
        out.append(_issue("axis_not_ordinal", "market.y_axis",
                          f"both axes measure {m.x_axis.kind}; the map needs two different buying criteria"))
    return out


# ------------------------------------------------------------------ B. positive evidence for every placement
def placement_issues(content: DeckContent, evidence=None) -> list[Issue]:
    m = content.market
    out: list[Issue] = []
    known = (lambda i: evidence.get(i) is not None) if evidence is not None else (lambda i: True)
    for i, c in enumerate(m.competitors):
        why = []
        if ABSENCE.search(c.rationale):
            why.append(f"its rationale rests on what a page does not say ('{c.rationale}'); not mentioned is not evidence")
        for key, ax in (("x", m.x_axis), ("y", m.y_axis)):
            ids = getattr(c, f"{key}_evidence_ids")
            side = "high" if getattr(c, key) in ("right", "top") else "low"
            label = _label(ax.high_label if side == "high" else ax.low_label)
            if not ids:
                why.append(f"no quote cited for its {key} placement ({ax.title}: '{label}')")
            elif not all(known(e) for e in ids):
                why.append(f"{key}_evidence_ids cite unknown ids {[e for e in ids if not known(e)]}")
            if ax.kind in AXIS_KINDS and AXIS_KINDS[ax.kind]["derive"]:
                qs = _quotes(evidence, _axis_ids(c, key))
                if read_side(ax.kind, qs) not in (None, side):
                    against = next(q for q in qs if read_side(ax.kind, [q]) not in (None, side))
                    why.append(f"its own cited quote reads the other way on {ax.title} (\"{against[:140]}\"), "
                               f"yet it is placed at '{label}'")
        if why:
            out.append(_issue("placement_unsupported", f"market.competitors[{i}]", f"{c.name}: " + "; ".join(why)))
    ot = m.our_take
    for j, point in enumerate(ot.points):
        ids = ot.point_evidence_ids[j] if j < len(ot.point_evidence_ids) else []
        why = []
        if not ids:
            why.append("cites no quote showing that group really lacks it")
        elif not all(known(e) for e in ids):
            why.append(f"cites unknown ids {[e for e in ids if not known(e)]}")
        if ABSENCE.search(point):
            why.append("rests on what a page does not say")
        if why:
            out.append(_issue("placement_unsupported", f"market.our_take.points[{j}]", f"our take '{point}': " + "; ".join(why)))
    if ABSENCE.search(ot.headline):
        out.append(_issue("placement_unsupported", "market.our_take.headline",
                          f"our take headline rests on what a page does not say: '{ot.headline}'"))
    return out


def slide3_ids(content: DeckContent) -> list[str]:
    m = content.market
    ids = list(m.subtitle_evidence_ids)
    for c in m.competitors:
        ids += [*c.evidence_ids, *c.x_evidence_ids, *c.y_evidence_ids]
    ids += m.our_take.evidence_ids + [e for lst in m.our_take.point_evidence_ids for e in lst]
    return list(dict.fromkeys(ids))


def diversity_issues(content: DeckContent, evidence=None) -> list[Issue]:
    if evidence is None:
        return []
    out: list[Issue] = []
    doms = [_registrable(e.url) for e in (evidence.get(i) for i in slide3_ids(content)) if e]
    if len(doms) >= MIN_IDS_FOR_SHARE:
        top, n = max(((d, doms.count(d)) for d in set(doms)), key=lambda kv: (kv[1], kv[0]))
        if n / len(doms) > MAX_DOMAIN_SHARE:
            out.append(_issue("evidence_not_diverse", "market",
                              f"{top} supplies {n} of the {len(doms)} quotes cited on slide 3 ({n / len(doms):.0%}; max "
                              f"{MAX_DOMAIN_SHARE:.0%}): one company's page describing its rivals is not a fair source"))
    for i, c in enumerate(content.market.competitors):
        ids = list(dict.fromkeys([*c.x_evidence_ids, *c.y_evidence_ids])) or list(c.evidence_ids)
        srcs = [_registrable(e.url) for e in (evidence.get(x) for x in ids) if e]
        own = _registrable(c.domain) or c.domain
        if srcs and not any(d == own or d in NEUTRAL_DIRECTORIES for d in srcs):
            out.append(_issue("evidence_not_diverse", f"market.competitors[{i}]",
                              f"{c.name} is placed only from other sites ({', '.join(sorted(set(srcs)))}); cite a quote "
                              f"from {own} or a neutral directory ({', '.join(sorted(NEUTRAL_DIRECTORIES))})"))
    return out


# ------------------------------------------------------------------ C. plausibility
SEGMENT_NOUN = re.compile(r"\b([a-z]{4,}(?:ors|ers|ists|ians))\b")
GENERIC_SEGMENT_NOUNS = {"manufacturers", "distributors", "wholesalers", "retailers", "customers", "suppliers",
                         "partners", "others", "members", "users", "buyers", "sellers", "owners", "teams", "leaders",
                         "providers", "vendors", "makers", "designers", "workers", "orders", "numbers", "markets"}


def _stem(w: str) -> str:
    return w.lower()[:6]


def _segments(extra: dict) -> list[str]:
    """Customer sub-segments this deck could lead with: the ones competitor discovery searched (Agent 2), else
    distinctive customer nouns in the profile's segment facts ('apparel decorators' -> 'decorators')."""
    comps = extra.get("comps") or {}
    segs = list(comps.get("segments_searched") or [])
    for v in comps.get("competitors", []):
        segs += v.get("segments", [])
    if segs:
        return list(dict.fromkeys(segs))
    words: list[str] = []
    for f in (extra.get("profile") or {}).get("customer_segments", []):
        words += [w for w in SEGMENT_NOUN.findall((f.get("text") or "").lower()) if w not in GENERIC_SEGMENT_NOUNS]
    return list(dict.fromkeys(words))


def _has(stems: set[str], texts: list[str]) -> bool:
    hay = " ".join(texts).lower()
    return bool(stems) and any(re.search(rf"\b{re.escape(s)}", hay) for s in stems)


def _segment_stems(seg: str, vertical: str, comp_texts: list[list[str]]) -> set[str]:
    """The words that make a segment distinct: not the vertical's own words, not generic customer nouns, and not
    words most competitors on the map use anyway ('apparel' in 'apparel decorators')."""
    from ..market_graph import market_tokens
    skip = {_stem(w) for w in market_tokens(vertical)}
    stems = {_stem(w) for w in re.findall(r"[a-z]{4,}", seg.lower())
             if w not in GENERIC_SEGMENT_NOUNS and _stem(w) not in skip}
    if comp_texts:
        stems = {s for s in stems if sum(_has({s}, t) for t in comp_texts) * 2 < len(comp_texts)}
    return stems


def plausibility_issues(content: DeckContent, extra: dict | None = None) -> list[Issue]:
    extra = extra or {}
    m = content.market
    evidence = extra.get("evidence")
    out: list[Issue] = []
    cells = {k: [c for c in m.competitors if (c.x, c.y) == k] for k in CELLS}
    if not cells[TARGET_CELL]:
        # The target alone in its cell tells the founder "you have no competition". Only evidence that every
        # competitor sits low on one of the two axes would make that believable.
        all_low = evidence is not None and m.x_axis.kind and m.y_axis.kind and all(
            read_side(m.x_axis.kind, _quotes(evidence, _axis_ids(c, "x"))) == "low"
            or read_side(m.y_axis.kind, _quotes(evidence, _axis_ids(c, "y"))) == "low" for c in m.competitors)
        if not all_low:
            out.append(_issue("target_alone", "market.competitors",
                              f"{content.company.display_name} sits alone in its cell, which tells the founder they "
                              f"have no competition: place the strongest direct rival next to the target"))
    empty = [k for k, v in cells.items() if not v]
    if len(empty) > 1:
        out.append(_issue("empty_cells", "market.competitors",
                          f"{len(empty)} of the 4 cells hold no competitor ({', '.join('-'.join(k[::-1]) for k in empty)}); "
                          f"spread the competitors so at most one cell is empty"))
    lead = " ".join([m.subtitle, *(f"{t.headline} {t.subline}" for t in content.thesis[:2])])
    verified = {v.get("domain"): v for v in (extra.get("comps") or {}).get("competitors", [])}
    comp_texts = []
    for c in m.competitors:
        v = verified.get(c.domain, {})
        comp_texts.append([c.name, c.rationale, v.get("segment") or "", v.get("scope") or "", *v.get("segments", []),
                           *_quotes(evidence, [*c.evidence_ids, *c.x_evidence_ids, *c.y_evidence_ids])])
    vertical = (extra.get("entity") or {}).get("vertical") or ""
    for seg in _segments(extra):
        stems = _segment_stems(seg, vertical, comp_texts)
        if not _has(stems, [lead]):
            continue
        served = any(seg in verified.get(c.domain, {}).get("segments", []) or _has(stems, texts)
                     for c, texts in zip(m.competitors, comp_texts))
        if not served:
            out.append(_issue("segment_missing", "market.competitors",
                              f"the thesis or subtitle leads with {seg}, but no competitor on the map serves {seg}: "
                              f"either add a competitor from that segment or don't lead with it"))
    return out


# ------------------------------------------------------------------ D. consistency with earlier decks
def consistency_issues(content: DeckContent, graph, evidence=None, run: str | None = None) -> list[Issue]:
    if graph is None:
        return []
    m = content.market
    out: list[Issue] = []
    for i, c in enumerate(m.competitors):
        # History is what other runs said. An explicit placement whose own quotes read the other way is not
        # history: it was wrong when it was made.
        history = [p for p in graph.placements(c.domain) if p.get("run") != run
                   and not (not p.get("derived") and p.get("axis_kind") in AXIS_KINDS
                            and read_side(p["axis_kind"], p.get("quotes", [])) not in (None, p.get("side")))]
        if not history:
            continue
        known_quotes = {normalise(q) for p in history for q in p.get("quotes", [])}
        company = graph.company(c.domain) or {}
        known_quotes |= {normalise(f.get("quote") or "") for f in company.get("facts", []) if f.get("run") != run}
        why = []
        for key, ax in (("x", m.x_axis), ("y", m.y_axis)):
            if ax.kind not in AXIS_KINDS:
                continue
            side = "high" if getattr(c, key) in ("right", "top") else "low"
            against = [p for p in history if p.get("axis_kind") == ax.kind and p.get("side") != side]
            if not against:
                continue
            new = [q for q in _quotes(evidence, _axis_ids(c, key)) if normalise(q) not in known_quotes]
            if any(read_side(ax.kind, [q]) == side for q in new) or (not AXIS_KINDS[ax.kind]["derive"] and new):
                continue  # a contradiction needs new evidence, and this placement has it
            p = against[0]
            said = f"'{p['label']}'" if p.get("label") else "from its quotes"
            quote = f" (\"{p['quotes'][0][:140]}\")" if p.get("quotes") else ""
            why.append(f"{ax.title} ({ax.kind}) puts it {side} ('{_label(ax.high_label if side == 'high' else ax.low_label)}'), "
                       f"but run {p['run']} read it {p['side']} {said}{quote}; cite a new quote that supports the change "
                       f"or move it")
        if why:
            out.append(_issue("placement_contradicts_history", f"market.competitors[{i}]", f"{c.name}: " + "; ".join(why)))
    return out


def cell_conflicts(content: DeckContent, comp, cell: tuple[str, str], evidence) -> bool:
    """Would moving `comp` to `cell` put it on a side its own quotes read against? (For the relayout fix, which
    moves logos for space and must not change what the map says.)"""
    m = content.market
    for key, ax, pos in (("x", m.x_axis, cell[0]), ("y", m.y_axis, cell[1])):
        if ax.kind in AXIS_KINDS and AXIS_KINDS[ax.kind]["derive"]:
            side = "high" if pos in ("right", "top") else "low"
            if read_side(ax.kind, _quotes(evidence, _axis_ids(comp, key))) not in (None, side):
                return True
    return False


# ------------------------------------------------------------------ registry
@register
def market_checks(content: DeckContent, manifest: dict, logo_metrics: dict, extra: dict) -> list[Issue]:
    ev = extra.get("evidence")
    run = getattr(extra.get("run_dir"), "name", None)
    return (axis_issues(content) + placement_issues(content, ev) + diversity_issues(content, ev)
            + plausibility_issues(content, extra) + consistency_issues(content, extra.get("graph"), ev, run))
