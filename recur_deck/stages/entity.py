"""S1 - entity resolution: which real company does the user mean?

Candidates come from Firecrawl web searches ("<name>" software, "<name>" formerly, "<name>" <hint> or
company), weighted by rank, plus domains written inside result snippets (how rebrands such as A2000 -> Ai2000
surface). Each candidate homepage is fetched and a Claude judge scores identity.
Gate: accept the top candidate if score >= 0.75 and it leads the next *different* company by >= 0.2.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel

from ..clients.http import ServiceError
from .base import DOMAIN_IN_TEXT, AmbiguousEntity, Ctx, StageError, is_company_domain, prompt, registrable

log = logging.getLogger("recur_deck.entity")
ACCEPT_SCORE, ACCEPT_MARGIN = 0.75, 0.2
MAX_CANDIDATES = 4


class EntityCandidateScore(BaseModel):
    domain: str
    score: float  # 0-1
    display_name: str
    legal_name: str | None
    aliases: list[str]
    vertical: str
    one_liner: str
    evidence_quotes: list[str]
    why: str


class EntityVerdict(BaseModel):
    ranked: list[EntityCandidateScore]
    rebrand_detected: bool
    notes: str


@dataclass
class _Cand:
    domain: str
    votes: float = 0.0
    sources: set[str] = field(default_factory=set)


def _gather(ctx: Ctx, name: str, hint: str | None, queries: list[str] | None = None) -> tuple[dict[str, _Cand], list[dict]]:
    """Candidate domains (weighted votes from search rank) and search snippets, from Firecrawl search."""
    cands: dict[str, _Cand] = {}
    snippets: list[dict] = []

    def vote(domain: str, w: float, src: str) -> None:
        d = registrable(domain)
        if d and is_company_domain(d):
            c = cands.setdefault(d, _Cand(d))
            c.votes += w
            c.sources.add(src)

    if queries is None:
        queries = [f'"{name}" software', f'"{name}" formerly', f'"{name}" {hint}' if hint else f'"{name}" company']
    for q, res in zip(queries, ctx.pmap(lambda q: ctx.web.search(q, limit=10), queries)):
        if isinstance(res, Exception):
            if isinstance(res, ServiceError):
                ctx.fallback(f"entity search failed ({res.service} {res.status}) for {q}")
            continue
        for rank, it in enumerate(res):
            vote(it["url"], 1.0 / (1 + rank * 0.25), q)
            text = f"{it.get('title', '')}\n{it.get('description', '')}"
            snippets.append({"url": it["url"], "title": it.get("title", ""), "text": it.get("description", "")[:700]})
            ctx.evidence.register_doc(it["url"], text)
            for m in DOMAIN_IN_TEXT.findall(text):  # "Ai2000 (formerly A2000) ... a2000software.com"
                vote(m, 0.6, "snippet")
    return cands, snippets


def _judge(ctx: Ctx, name: str, hint: str | None, pages: dict[str, object], snippets: list[dict]) -> EntityVerdict:
    cand_blocks = [{"domain": d, "final_url": p.final_url, "title": p.title, "description": p.description,
                    "homepage_markdown": p.markdown[:6000]} for d, p in pages.items()]
    return ctx.claude.ask(
        ctx.settings.model_judge, prompt("entity_judge"),
        [("user_input", {"name": name, "hint": hint}), ("candidates", cand_blocks), ("search_snippets", snippets[:40])],
        EntityVerdict, effort="medium", label="entity_judge", fallbacks=ctx.judge_fallbacks())


def resolve(ctx: Ctx, name: str, domain: str | None = None, hint: str | None = None,
            accept_low_confidence: bool = False) -> dict:
    """Returns the entity dict (also written to entity.json). Raises AmbiguousEntity when the gate fails
    and accept_low_confidence is False, StageError when nothing plausible was found."""
    name = name.strip()
    supplied = registrable(domain) if domain else None
    if supplied:
        cands = {supplied: _Cand(supplied, 10.0, {"user"})}
        # one search for rebrand / alias snippets; the user has already chosen the company
        snippets = [x for x in _gather(ctx, name, hint, [f'"{name}" formerly'])[1] if registrable(x["url"]) != supplied][:20]
    else:
        cands, snippets = _gather(ctx, name, hint)
    if not cands:
        raise StageError(f"no website found for '{name}'. Re-run with --domain <company domain>.")

    top = sorted(cands.values(), key=lambda c: (-c.votes, c.domain))[:MAX_CANDIDATES]
    fetched = ctx.pmap(lambda c: ctx.web.fetch(f"https://{c.domain}", main_only=True), top)
    pages: dict[str, object] = {}
    for c, p in zip(top, fetched):
        if isinstance(p, Exception) or p is None:
            continue
        final = registrable(p.final_url) or c.domain
        if final in pages:      # two candidate domains that redirect to the same site are one company
            continue
        pages[final] = p
        ctx.evidence.register_doc(p.final_url, f"{p.title}\n{p.description}\n{p.markdown}")
    if not pages:
        raise StageError(f"could not fetch any candidate website for '{name}' (tried {[c.domain for c in top]}).")

    verdict = _judge(ctx, name, hint, pages, snippets)
    ranked = sorted((r for r in verdict.ranked if registrable(r.domain) in pages),
                    key=lambda r: -max(0.0, min(1.0, r.score)))
    if not ranked:
        raise StageError(f"the identity judge rejected every candidate for '{name}'.")
    for r in ranked:
        r.domain, r.score = registrable(r.domain), max(0.0, min(1.0, r.score))

    best = next((r for r in ranked if r.domain == supplied), ranked[0]) if supplied else ranked[0]
    others = [r for r in ranked if r.domain != best.domain and r.display_name.lower() != best.display_name.lower()]
    margin = best.score - (others[0].score if others else 0.0)
    alternatives = [{"domain": r.domain, "score": round(r.score, 3), "display_name": r.display_name,
                     "why_rejected": r.why} for r in ranked if r is not best]
    confident = best.score >= ACCEPT_SCORE and margin >= ACCEPT_MARGIN
    low_conf = False
    if supplied:
        if best.score < 0.5:
            ctx.warn(f"--domain {supplied}: the identity judge scored it {best.score:.2f} ({best.why})")
    elif not confident:
        if not accept_low_confidence:
            raise AmbiguousEntity([{"domain": r.domain, "score": round(r.score, 3), "display_name": r.display_name,
                                    "one_liner": r.one_liner, "why": r.why} for r in ranked[:3]])
        low_conf = True
        ctx.flag("low_confidence_identity")

    page = pages[best.domain]
    eids = []
    for q in best.evidence_quotes:  # homepage first, then any search snippet that contains the quote
        src = ctx.evidence.find_source(q, [page.final_url] + [s["url"] for s in snippets])
        if src:
            own = src == page.final_url
            eid = ctx.evidence.add(src, q, "target", "identity", page.source if own else "web", page.title if own else "")
            if eid:
                eids.append(eid)
    aliases = [a.strip() for a in best.aliases if a.strip() and a.strip().lower() != best.display_name.strip().lower()]
    entity = {
        "display_name": best.display_name.strip(), "legal_name": best.legal_name, "domain": best.domain,
        "homepage": page.final_url, "aliases": list(dict.fromkeys(aliases)), "vertical": best.vertical,
        "one_liner": best.one_liner, "confidence": round(best.score, 3), "margin": round(margin, 3),
        "domain_supplied": bool(supplied), "low_confidence_identity": low_conf,
        "rebrand_detected": verdict.rebrand_detected, "alternatives": alternatives, "evidence_ids": eids,
        "notes": verdict.notes, "typed_name": name, "hint": hint,
    }
    ctx.save_json("entity.json", entity)
    ctx.pages[best.domain] = page  # the profile stage reuses the homepage instead of fetching it again
    return entity
