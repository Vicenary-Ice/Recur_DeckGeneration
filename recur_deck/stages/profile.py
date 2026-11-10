"""S2 - target profile: what the company does and for whom, every fact backed by a verbatim quote.

One Firecrawl /v2/map call lists the site; the most informative pages (product, features, solutions, about,
customers, pricing, industries ...) are ranked locally, <= 8 are scraped, and Claude extracts facts with
quotes. Facts whose quote is not found verbatim in the fetched page are dropped.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from pydantic import BaseModel

from ..clients.http import ServiceError
from .base import Ctx, prompt, registrable

log = logging.getLogger("recur_deck.profile")
MAX_PAGES = 8
KEYWORDS = [  # (pattern in URL path, weight)
    ("product", 5), ("feature", 5), ("solution", 5), ("platform", 4), ("module", 4), ("about", 4),
    ("customer", 4), ("case-stud", 3), ("pricing", 3), ("industr", 4), ("who-we-serve", 4), ("integrat", 3),
    ("erp", 2), ("software", 2), ("why", 2), ("overview", 2), ("services", 1), ("company", 2), ("news", 1),
    ("press", 1), ("partners", 1),
]
SKIP = re.compile(r"(blog/.+/.+|/tag/|/category/|/author/|/page/\d|privacy|terms|cookie|legal|login|signin|sign-in|"
                  r"careers|jobs|/cart|checkout|\.pdf$|\.jpg$|\.png$|/feed|wp-json|/search)", re.I)

FIELDS = {  # profile field -> evidence kind
    "modules": "product", "customer_segments": "segment", "customer_size_signals": "segment",
    "deployment": "product", "integrations": "product", "differentiators": "product",
    "recent_news": "news", "gaps_or_opportunities": "product",
}


class Fact(BaseModel):
    text: str
    evidence_quote: str
    source_url: str


class Profile(BaseModel):
    modules: list[Fact]
    customer_segments: list[Fact]
    customer_size_signals: list[Fact]
    deployment: list[Fact]
    integrations: list[Fact]
    differentiators: list[Fact]
    recent_news: list[Fact]
    gaps_or_opportunities: list[Fact]


def rank_urls(urls: list[str], domain: str, limit: int) -> list[str]:
    scored = []
    for u in dict.fromkeys(urls):
        if registrable(u) != domain or SKIP.search(u):
            continue
        path = urlsplit(u).path.lower().rstrip("/")
        if not path:
            continue
        depth = path.count("/")
        score = sum(w for k, w in KEYWORDS if k in path) - 1.5 * max(0, depth - 2)
        if score > 0:
            scored.append((score, -len(path), u))
    scored.sort(reverse=True)
    picked, sections = [], set()
    for _, _, u in scored:  # prefer distinct top-level sections
        sec = urlsplit(u).path.strip("/").split("/")[0]
        if sec in sections and len(picked) < limit // 2:
            continue
        sections.add(sec)
        picked.append(u)
        if len(picked) == limit:
            break
    return picked


def build(ctx: Ctx, entity: dict) -> dict:
    domain = entity["domain"]
    home = ctx.pages.get(domain) or ctx.web.fetch(entity.get("homepage") or f"https://{domain}")
    pages = [home] if home else []
    urls: list[str] = []
    if ctx.firecrawl.available:
        try:
            urls = [l["url"] for l in ctx.firecrawl.map(f"https://{domain}", limit=200)]
        except ServiceError as e:
            ctx.fallback(f"Firecrawl map failed ({e.status}); used homepage links to find product pages")
    if not urls and home:
        urls = home.links
    picked = rank_urls(urls, domain, MAX_PAGES - len(pages))
    for p in ctx.pmap(lambda u: ctx.web.fetch(u, main_only=True), picked):
        if p is not None and not isinstance(p, Exception) and p.markdown:
            pages.append(p)
    # Recent news about the company: one Firecrawl news search (titles + snippets, 2 credits).
    news_docs = []
    names = [entity["display_name"], *entity.get("aliases", [])]
    try:
        topic = " ".join((entity.get("vertical") or "software").split()[:3])
        for r in ctx.web.search(f'"{entity["display_name"]}" {topic}', limit=6, source="news"):
            text = f"{r['title']}\n{r['description']}"
            if any(n.lower() in text.lower() for n in names):
                news_docs.append({"url": r["url"], "title": r["title"], "markdown": text + (f"\n({r['date']})" if r.get("date") else "")})
                ctx.evidence.register_doc(r["url"], text)
    except ServiceError as e:
        log.info("news search failed: %s", e)
    if not pages:
        raise RuntimeError(f"could not fetch any page from {domain}")
    for p in pages:
        ctx.evidence.register_doc(p.final_url, f"{p.title}\n{p.description}\n{p.markdown}")

    page_blocks = [{"url": p.final_url, "title": p.title, "markdown": p.markdown[:9000]} for p in pages] + news_docs
    prof = ctx.claude.ask(
        ctx.settings.model_judge, prompt("profile_extract"),
        [("company", {k: entity[k] for k in ("display_name", "aliases", "domain", "vertical", "one_liner")}),
         ("pages", page_blocks)],
        Profile, effort="medium", max_tokens=16000, label="profile_extract", fallbacks=ctx.judge_fallbacks())

    urls_all = [b["url"] for b in page_blocks]
    titles = {b["url"]: b["title"] for b in page_blocks}
    out: dict = {"pages": urls_all, "facts_total": 0, "facts_kept": 0}
    for f, kind in FIELDS.items():
        kept = []
        for fact in getattr(prof, f):
            out["facts_total"] += 1
            order = [fact.source_url] + [u for u in urls_all if u != fact.source_url]
            src = ctx.evidence.find_source(fact.evidence_quote, [u for u in order if u in urls_all])
            via = "firecrawl" if any(n["url"] == src for n in news_docs) else next(
                (p.source for p in pages if p.final_url == src), "web")
            eid = ctx.evidence.add(src, fact.evidence_quote, "target", kind, via, titles.get(src, "")) if src else None
            if eid:
                kept.append({"text": fact.text, "evidence_id": eid})
            else:
                log.info("dropped profile fact (quote not found): %s", fact.text)
        out[f] = kept
        out["facts_kept"] += len(kept)
    if out["facts_kept"] < 4:
        ctx.warn(f"thin profile: only {out['facts_kept']} verified facts about {entity['display_name']}")
    ctx.save_json("profile.json", out)
    ctx.evidence.save()
    return out
