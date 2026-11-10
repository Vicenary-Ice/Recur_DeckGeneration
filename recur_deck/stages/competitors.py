"""S3 - competitor discovery and verification (Firecrawl search + Claude). Never adds a company from model
memory: every candidate comes from a fetched document, every domain is confirmed by fetching that site and
finding the product name on it, and every kept competitor is verified against its own live homepage.

Channels (parallel Firecrawl searches):
  vs_page    '"<name>" vs' for the brand and its alias, '"<name>" alternatives'   (titles + descriptions)
  directory  '"<name>" alternatives competitors' on capterra/g2/getapp/softwareadvice  (+ page markdown)
             '"<name>" competitors' for the brand and its alias, open web               (+ page markdown)
  similar    the target's one-liner, target domain excluded -> vendor homepages      (titles + descriptions)
  roundup    'best <vertical> software', target domain excluded                       (+ page markdown)
             '<vertical> software' category pages on capterra/getapp                  (+ page markdown)
  segment    for up to 2 customer sub-segments the vertical does not already name ("apparel decorators"):
             '<segment> software' (+ markdown, 2 results) and '<segment> ERP' / '<segment> management software'
  self_compare  the target's own compare / vs / alternative / switch / migrate / integration pages
             (from its site map or homepage links; direct fetches, no search)
  graph      companies the market graph links to the target within 2 edges, or that earlier targets in the same
             market competed with (runs/_market_graph.json). One channel, never independent evidence: every
             seed is still verified on its live site
Directory and roundup pages are read in full (in chunks), so every vendor they list becomes a mention.
-> mentions (name, verbatim quote, head-to-head?) -> name variants merged ('Aptean Exenta' -> 'Aptean') ->
name -> domain (confirmed on the live site) -> merged by registrable domain -> candidates.json (aim: >= 15)
-> Claude verifier -> selection rule (§8.3) -> competitors.json
"""
from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from pydantic import BaseModel

from .. import market_graph
from ..axes import AXIS_KINDS
from ..clients.firecrawl import Page
from ..clients.http import ServiceError
from ..evidence import quote_in
from .base import Ctx, StageError, is_company_domain, prompt, registrable

log = logging.getLogger("recur_deck.competitors")
DIRECTORIES = ["capterra.com", "g2.com", "getapp.com", "softwareadvice.com"]
CATEGORY_DIRECTORIES = ["capterra.com", "getapp.com"]  # their category pages list every vendor in a category
LISTICLE = re.compile(r"\b(best|top \d+|top|alternatives?|vs\.?|versus|compare|comparison|reviews?|list of|guide|"
                      r"competitors?|ranked|rankings?)\b", re.I)
MAX_VERIFY = 20          # candidates verified per run (strong ones first, then the best-mentioned weak ones)
MAX_GRAPH_SEEDS = 12     # market-graph seeds per run (closest and most-verified first)
MAX_SELF_COMPARE = 4     # pages of the target's own site read for comparisons
MAX_SEGMENTS = 2         # customer sub-segments searched (2 searches each)
SELF_COMPARE = [re.compile(r"compare|comparison|(?<![a-z])vs(?![a-z])|versus|alternative", re.I),
                re.compile(r"switch|migrat|replac", re.I), re.compile(r"integrat|partner", re.I)]
MAX_RESOLVE = 30          # mention groups resolved to a website (most confirmations are free direct fetches)
MAX_SEARCH_LOOKUPS = 8    # Firecrawl name lookups for groups no cheaper route resolved (2 credits each)
KEEP = 8
CHUNK_CHARS = 8000        # directory / roundup pages are read in chunks of this size ...
MAX_CHUNKS = 4            # ... up to this many per page (the old 6000-char cut missed most of long lists)


class Mention(BaseModel):
    doc_id: int
    name: str
    domain: str | None
    quote: str
    head_to_head: bool


class Mentions(BaseModel):
    mentions: list[Mention]


class DomainGuess(BaseModel):
    name: str
    domain: str | None


class DomainGuesses(BaseModel):
    guesses: list[DomainGuess]


class SegmentTerms(BaseModel):
    segments: list[str]  # short search phrases for customer sub-segments, most important first


class CompetitorCheck(BaseModel):
    is_software_vendor: bool
    same_vertical: bool
    operating: bool
    acquired_or_renamed: str | None
    display_name: str
    segment: str
    scope: str
    head_to_head_with_target: bool
    evidence_quotes: list[str]
    relevance: float  # 0-1 how directly it competes for the same buyer


# ------------------------------------------------------------------ name helpers
def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def fuzzy(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def domain_label(domain: str) -> str:
    return registrable(domain).split(".")[0]


GENERIC_TITLE = {"home", "homepage", "home page", "welcome", "official site", "index", "main"}


def name_from_title(title: str, domain: str) -> str:
    """'AIMS360 | Apparel ERP Software' -> 'AIMS360': the title segment closest to the domain label, else the
    first short non-generic segment ('BlueCherry | Fashion ERP' on cgsinc.com -> 'BlueCherry')."""
    parts = [p.strip() for p in re.split(r"\s[|\-–—:·]\s|\s[|–—·]|[|–—·]\s", title or "") if p.strip()]
    parts = [p for p in parts if p.lower() not in GENERIC_TITLE]
    label = domain_label(domain)
    if not parts:
        return label.capitalize()
    best = max(parts, key=lambda p: (fuzzy(p, label), -len(p)))
    if fuzzy(best, label) >= 0.5 or norm(label) in norm(best):
        return best
    return parts[0] if len(parts[0].split()) <= 4 else label.capitalize()


def label_matches(name: str, domain: str) -> bool:
    label, n = norm(domain_label(domain)), norm(name)
    return bool(n and label) and (fuzzy(name, label) >= 0.6 or label in n or n in label)


def name_on_page(name: str, page: Page | None) -> bool:
    """Does the fetched site present itself as `name`? (word match in title/description/text)"""
    if page is None:
        return False
    hay = f"{page.title}\n{page.description}\n{page.markdown[:30000]}"
    words = re.escape(name.strip()).replace(r"\ ", r"[\s\-]*")
    if re.search(rf"(?<![A-Za-z0-9]){words}(?![A-Za-z0-9])", hay, re.I):
        return True
    return len(norm(name)) >= 6 and norm(name) in norm(page.title + page.description)


@dataclass
class Doc:
    id: int
    url: str
    title: str
    text: str
    channel: str
    source: str  # firecrawl | claude_web_search
    segment: str = ""  # segment channel: the sub-segment whose search found this page


@dataclass
class Vendor:
    """A vendor homepage that a search returned directly (similar and segment channels)."""
    domain: str
    name: str
    url: str
    channel: str = "similar"
    segment: str = ""


@dataclass
class Cand:
    domain: str
    names: dict[str, int] = field(default_factory=dict)
    channels: set[str] = field(default_factory=set)
    head_to_head: bool = False
    mentions: list[dict] = field(default_factory=list)
    resolved_by: str = ""
    segments: set[str] = field(default_factory=set)   # sub-segments whose searches found it
    via: list[str] = field(default_factory=list)      # graph channel: the companies that link it to the target

    @property
    def name(self) -> str:
        return max(self.names.items(), key=lambda kv: (kv[1], -len(kv[0])))[0] if self.names else domain_label(self.domain)


# ------------------------------------------------------------------ channel collection
def segment_terms(ctx: Ctx, entity: dict, profile: dict | None) -> list[str]:
    """Up to MAX_SEGMENTS customer sub-segments worth their own search ("apparel decorators"), from the profile's
    segment facts. A sub-segment whose words the vertical already contains is skipped (the vertical searches
    cover it). One Haiku call; no call when the profile names no segment."""
    facts = [f.get("text") for f in (profile or {}).get("customer_segments", []) if f.get("text")][:8]
    if not facts:
        return []
    try:
        ans = ctx.claude.ask(ctx.settings.model_fast, prompt("competitor_segments"),
                             [("company", {k: entity.get(k) for k in ("display_name", "vertical", "one_liner")}),
                              ("segment_facts", facts)], SegmentTerms, effort="low", label="competitor_segments",
                             fallbacks=[ctx.settings.model_judge])
    except Exception as e:  # noqa: BLE001 - the other channels still run
        log.info("segment extraction failed: %s", e)
        return []
    covered = {w[:6] for w in market_graph.market_tokens(f"{entity.get('vertical') or ''} {entity.get('one_liner') or ''}")}
    out = []
    for s in ans.segments:
        s = re.sub(r"\s+", " ", s).strip(" .,'\"")
        words = {w[:6] for w in market_graph.market_tokens(s)}
        if not s or len(s) > 60 or not words or words <= covered or s.lower() in (o.lower() for o in out):
            continue
        out.append(s)
    return out[:MAX_SEGMENTS]


def _collect(ctx: Ctx, entity: dict, profile: dict | None = None) -> tuple[list[Doc], list[Vendor], dict[str, int]]:
    """Documents to read for mentions, vendor homepages from the similar and segment channels, and the number
    of searches per channel."""
    names = list(dict.fromkeys([entity["display_name"], *entity.get("aliases", [])]))[:2]
    vertical = entity.get("vertical") or entity.get("one_liner") or ""
    tdom = entity["domain"]
    jobs: list[tuple[str, str, dict]] = [("vs_page", f'"{n}" vs', {}) for n in names]
    jobs += [("vs_page", f'"{names[0]}" alternatives', {})]
    jobs += [("directory", f'"{n}" alternatives competitors', {"include_domains": DIRECTORIES, "limit": 2,
                                                                 "with_markdown": True}) for n in names]
    jobs += [("directory", f'"{n}" competitors', {"exclude_domains": [tdom], "limit": 3, "with_markdown": True})
             for n in names]
    jobs += [("similar", entity.get("one_liner") or f"{vertical} software", {"exclude_domains": [tdom]})]
    jobs += [("roundup", f"best {vertical} software", {"exclude_domains": [tdom], "limit": 4, "with_markdown": True})]
    jobs += [("roundup", f"{category_phrase(vertical)} software", {"include_domains": CATEGORY_DIRECTORIES, "limit": 3,
                                                                   "with_markdown": True})]
    noun = "ERP" if re.search(r"\berp\b", vertical, re.I) else "management software"
    for seg in segment_terms(ctx, entity, profile):
        jobs += [("segment", f"{seg} software", {"exclude_domains": [tdom], "limit": 2, "with_markdown": True, "segment": seg}),
                 ("segment", f"{seg} {noun}", {"exclude_domains": [tdom], "limit": 8, "segment": seg})]
    counts: dict[str, int] = {}
    for ch, *_ in jobs:
        counts[ch] = counts.get(ch, 0) + 1

    def run(job):
        ch, q, kw = job
        seg = kw.pop("segment", "")
        return ch, seg, ctx.web.search(q, limit=kw.pop("limit", 10), **kw)

    docs: list[Doc] = []
    vendors: list[Vendor] = []
    seen: set[tuple[str, str, bool]] = set()  # one page found by two searches is read once per channel
    for res in ctx.pmap(run, [(c, q, dict(k)) for c, q, k in jobs]):
        if isinstance(res, Exception):
            ctx.warn(f"competitor search failed: {type(res).__name__}: {res}")
            continue
        ch, seg, items = res
        for r in items:
            dom = registrable(r["url"])
            if dom == tdom:
                continue
            title, desc, md = r.get("title", ""), r.get("description", ""), r.get("markdown", "")
            if ch in ("similar", "segment"):
                path = urlsplit(r["url"]).path.strip("/")
                if is_company_domain(dom) and path.count("/") <= 1 and not LISTICLE.search(title):
                    vendors.append(Vendor(dom, name_from_title(title, dom), r["url"], ch, seg))
                    continue
                ch_doc = "roundup" if ch == "similar" else "segment"  # a listicle that ranked for the query
            else:
                ch_doc = ch
            if (r["url"], ch_doc, bool(md)) in seen:
                continue
            seen.add((r["url"], ch_doc, bool(md)))
            for chunk in page_chunks(title, desc, md):
                docs.append(Doc(len(docs) + 1, r["url"], title, chunk, ch_doc, r.get("via", "firecrawl"), seg))
    return docs, vendors, counts


def self_compare_urls(urls: list[str], domain: str, limit: int = MAX_SELF_COMPARE) -> list[str]:
    """The target's own pages that name rivals: compare / vs / alternative first, then switch / migrate / replace,
    then integration / partner pages."""
    picked: list[str] = []
    for pat in SELF_COMPARE:
        for u in dict.fromkeys(urls):
            path = urlsplit(u).path.lower()
            if registrable(u) == domain and path.strip("/") and pat.search(path) and u not in picked:
                picked.append(u)
    return picked[:limit]


def _self_compare_docs(ctx: Ctx, entity: dict, profile: dict | None, start_id: int) -> list[Doc]:
    tdom = entity["domain"]
    urls = list((profile or {}).get("site_urls") or [])
    if not urls and ctx.firecrawl.available:
        try:  # the profile stage made this exact call, so it is served from the cache
            urls = [l["url"] for l in ctx.firecrawl.map(f"https://{tdom}", limit=200)]
        except ServiceError as e:
            log.info("site map for self-compare pages failed: %s", e)
    home = ctx.pages.get(tdom)
    urls += list(home.links) if home is not None else []
    docs: list[Doc] = []
    picked = self_compare_urls(urls, tdom)
    for u, page in zip(picked, ctx.pmap(lambda u: ctx.web.fetch(u, main_only=True), picked)):
        if page is None or isinstance(page, Exception) or not page.markdown:
            continue
        for chunk in page_chunks(page.title, page.description, page.markdown):
            docs.append(Doc(start_id + len(docs), page.final_url or u, page.title, chunk, "self_compare", page.source))
    if picked:
        log.info("self-compare pages read: %s", picked)
    return docs


def graph_seeds(graph, entity: dict, limit: int = MAX_GRAPH_SEEDS) -> list[Cand]:
    """Candidates from the market graph (channel `graph`): competitors within 2 edges of the target, the target's
    unverified candidates from earlier runs, and earlier targets of the same market (shared distinctive word in
    the vertical) with their competitors. Companies an earlier run rejected for this same target are skipped.
    Closest and most-verified first, at most `limit`."""
    if graph is None:
        return []
    tdom = registrable(entity["domain"]) or entity["domain"]
    tnames = {norm(n) for n in [entity.get("display_name", ""), *entity.get("aliases", [])]}
    found: dict[str, dict] = {}

    def add(dom: str, name: str, via: str, runs: list[str], depth: int) -> None:
        if not dom or dom == tdom or not is_company_domain(dom) or norm(name) in tnames:
            return
        rec = found.setdefault(dom, {"name": name, "via": [], "runs": set(), "depth": depth})
        rec["via"].append(via)
        rec["runs"].update(runs)
        rec["depth"] = min(rec["depth"], depth)

    for n in graph.neighbors(tdom, depth=2):
        add(n["domain"], n["name"], n["via"], n["runs"], n["depth"])
    for n in graph.neighbors(tdom, depth=1, types=("candidate",)):
        add(n["domain"], n["name"], n["via"], n["runs"], 2)
    for t in graph.related_targets(entity):
        c = graph.company(t) or {}
        add(t, graph.name(t), f"market:{t}", c.get("targets_in_runs", []), 1)
        for n in graph.neighbors(t, depth=1):
            add(n["domain"], n["name"], f"market:{t}", n["runs"], 2)
    rejected = {n["domain"] for n in graph.neighbors(tdom, depth=1, types=("rejected",))}
    ranked = sorted((d for d in found if d not in rejected), key=lambda d: (found[d]["depth"], -len(found[d]["runs"]), d))
    return [Cand(d, names={found[d]["name"]: 1}, channels={"graph"}, resolved_by="graph",
                 via=list(dict.fromkeys(found[d]["via"]))) for d in ranked[:limit]]


def category_phrase(vertical: str) -> str:
    """'Apparel, footwear and home goods ERP software' -> 'Apparel, footwear and home goods ERP' (the search adds
    'software' back), so the category query reads like a directory category name."""
    return re.sub(r"\s+(software|platform|solutions?)\s*$", "", (vertical or "").strip(), flags=re.I) or "business"


def page_chunks(title: str, desc: str, md: str) -> list[str]:
    """A search result as one or more documents: title + description + the page markdown in CHUNK_CHARS pieces
    (cut at line breaks where possible), at most MAX_CHUNKS. Every chunk repeats the title so a mention's
    quote and context stay on the same document."""
    head = f"{title}\n{desc}".strip()
    md = (md or "").strip()
    if not md:
        return [head] if head else []
    out, rest = [], md
    while rest and len(out) < MAX_CHUNKS:
        cut = len(rest) if len(rest) <= CHUNK_CHARS else (rest.rfind("\n", CHUNK_CHARS // 2, CHUNK_CHARS) + 1 or CHUNK_CHARS)
        out.append(f"{head}\n{rest[:cut]}".strip())
        rest = rest[cut:].lstrip()
    return out


GENERIC_NAME_WORDS = {"apparel", "fashion", "software", "systems", "system", "erp", "cloud", "suite", "solutions",
                      "business", "retail", "wholesale", "inventory", "management", "the", "and", "for"}


def merge_variants(groups: dict[str, list]) -> dict[str, list]:
    """Merge mention groups that are one vendor's name plus a product or descriptor: 'Aptean Exenta' and 'Aptean
    Apparel ERP' join 'Aptean'; 'CGS BlueCherry' and 'BlueCherry Suite' join 'BlueCherry'. A group joins the
    shortest other group whose name starts or ends it as whole words (>= 4 letters, not just generic words), so
    'Syncron' never joins 'Sync' and 'Apparel Connect' never joins 'Apparel'. Keys are normalised names; values
    are lists of (Mention, Doc), the root's own mentions first."""
    names = {k: v[0][0].name.strip() for k, v in groups.items()}
    by_len = sorted(groups, key=lambda k: (len(k), k))

    def usable_root(k: str) -> bool:
        words = re.findall(r"[a-z0-9]+", names[k].lower())
        return len(k) >= 4 and any(w not in GENERIC_NAME_WORDS for w in words)

    parent: dict[str, str] = {}
    for k in by_len:
        for short in by_len:
            if len(short) >= len(k) or short in parent or not usable_root(short):
                continue
            w = re.escape(names[short]).replace(r"\ ", r"[\s\-]+")
            if re.match(rf"{w}(?![A-Za-z0-9])", names[k], re.I) or re.search(rf"(?<![A-Za-z0-9]){w}$", names[k], re.I):
                parent[k] = short
                break
    out = {k: list(groups[k]) for k in by_len if k not in parent}
    for k in by_len:
        if k in parent:
            out[parent[k]].extend(groups[k])
    return out


def _extract_mentions(ctx: Ctx, entity: dict, docs: list[Doc]) -> list[tuple[Mention, Doc]]:
    if not docs:
        return []
    target = {k: entity[k] for k in ("display_name", "aliases", "domain", "vertical", "one_liner")}
    by_id = {d.id: d for d in docs}

    def run(batch: list[Doc]) -> Mentions:
        blocks = [{"doc_id": d.id, "url": d.url, "title": d.title, "text": d.text} for d in batch]
        return ctx.claude.ask(ctx.settings.model_fast, prompt("competitor_extract"),
                              [("target", target), ("documents", blocks)], Mentions, effort="medium",
                              label="competitor_extract", fallbacks=[ctx.settings.model_judge])

    targets = {norm(n) for n in [entity["display_name"], *entity.get("aliases", [])]}
    out: list[tuple[Mention, Doc]] = []
    for res in ctx.pmap(run, [docs[i:i + 8] for i in range(0, len(docs), 8)], workers=4):
        if isinstance(res, Exception):
            ctx.warn(f"mention extraction failed for one batch: {res}")
            continue
        for m in res.mentions:
            d = by_id.get(m.doc_id)
            if not d or not m.name.strip() or norm(m.name) in targets or any(fuzzy(m.name, t) >= 0.85 for t in targets):
                continue
            if not quote_in(m.quote, d.text) or norm(m.name) not in norm(d.text):
                log.info("dropped mention %s (quote/name not in %s)", m.name, d.url)
                continue
            ctx.evidence.register_doc(d.url, d.text)  # so the quote can later be stored as verified evidence
            out.append((m, d))
    return out


# ------------------------------------------------------------------ name -> domain, confirmed on the live site
def _links_by_text(docs: list[Doc]) -> dict[str, set[str]]:
    """Markdown links in fetched pages: normalised link text -> domains ('[AIMS360](https://aims360.com)')."""
    out: dict[str, set[str]] = {}
    for d in docs:
        for text, url in re.findall(r"\[([^\]]{2,60})\]\((https?://[^)\s]+)\)", d.text):
            dom = registrable(url)
            if dom and is_company_domain(dom) and dom != registrable(d.url):
                out.setdefault(norm(text), set()).add(dom)
    return out


def _resolve(ctx: Ctx, entity: dict, mentions: list[tuple[Mention, Doc]], docs: list[Doc],
             vendors: list[Vendor]) -> dict[str, Cand]:
    tdom = entity["domain"]
    cands: dict[str, Cand] = {}
    for v in vendors:  # similar / segment vendor sites: domain is known, name from the title
        c = cands.setdefault(v.domain, Cand(v.domain, resolved_by=v.channel))
        c.names[v.name] = c.names.get(v.name, 0) + 1
        c.channels.add(v.channel)
        if v.segment:
            c.segments.add(v.segment)
        c.mentions.append({"url": v.url, "quote": "", "channel": v.channel})

    groups: dict[str, list[tuple[Mention, Doc]]] = {}
    for m, d in mentions:
        groups.setdefault(norm(m.name), []).append((m, d))
    groups = merge_variants(groups)
    order = sorted(groups, key=lambda k: (-len({d.channel for _, d in groups[k]}), -len(groups[k]), k))[:MAX_RESOLVE]
    links = _links_by_text(docs)
    vendor_by_name = {norm(v.name): v.domain for v in vendors}

    def cheap_guesses(key: str) -> list[tuple[str, str]]:
        ms = groups[key]
        name = ms[0][0].name
        g: list[tuple[str, str]] = []
        if key in vendor_by_name:
            g.append((vendor_by_name[key], "similar"))
        for m, d in ms:
            if m.domain:
                g.append((registrable(m.domain), "stated"))
            if label_matches(name, d.url):
                g.append((registrable(d.url), "own_page"))
        g += [(dom, "link") for dom in sorted(links.get(key, ()))]
        g += [(v.domain, "name_match") for v in vendors if fuzzy(v.name, name) >= 0.9]
        return [(dom, how) for dom, how in dict.fromkeys(g) if dom and dom != tdom and is_company_domain(dom)]

    guesses = {k: cheap_guesses(k) for k in order}
    unknown = [groups[k][0][0].name for k in order if not guesses[k]]
    if unknown:  # one batched call; every answer is confirmed below by fetching the site
        try:
            ans = ctx.claude.ask(ctx.settings.model_fast, prompt("domain_guess"),
                                 [("category", entity.get("vertical") or ""), ("names", unknown)], DomainGuesses,
                                 effort="low", label="domain_guess", fallbacks=[ctx.settings.model_judge])
            for gs in ans.guesses:
                k, dom = norm(gs.name), registrable(gs.domain or "")
                if k in guesses and dom and dom != tdom and is_company_domain(dom):
                    guesses[k].append((dom, "model_guess_confirmed"))
        except Exception as e:  # noqa: BLE001 - lookups below still run
            log.info("domain_guess failed: %s", e)

    def confirm(key: str) -> tuple[str, str] | None:
        name = groups[key][0][0].name
        for dom, how in guesses[key][:4]:
            page = ctx.pages.get(dom) or ctx.web.fetch(f"https://{dom}", main_only=False)
            if page is not None and registrable(page.final_url) not in (dom, ""):
                dom = registrable(page.final_url)  # followed a redirect (e.g. acquired or renamed)
            if name_on_page(name, page):
                ctx.pages.setdefault(dom, page)
                return dom, how
        return None

    resolved: dict[str, tuple[str, str]] = {}
    for key, res in zip(order, ctx.pmap(confirm, order)):
        if res and not isinstance(res, Exception):
            resolved[key] = res
    vertical = " ".join((entity.get("vertical") or "software").split()[:4])
    left = [k for k in order if k not in resolved][:MAX_SEARCH_LOOKUPS]

    def lookup(key: str) -> tuple[str, str] | None:
        name = groups[key][0][0].name
        for r in ctx.web.search(f'"{name}" {vertical}', limit=5, exclude_domains=[tdom]):
            dom = registrable(r["url"])
            if is_company_domain(dom) and (label_matches(name, dom) or norm(name) in norm(r.get("title", ""))):
                page = ctx.pages.get(dom) or ctx.web.fetch(f"https://{dom}", main_only=False)
                if name_on_page(name, page):
                    ctx.pages.setdefault(dom, page)
                    return dom, "search_lookup"
        return None

    for key, res in zip(left, ctx.pmap(lookup, left)):
        if res and not isinstance(res, Exception):
            resolved[key] = res

    for key, (dom, how) in resolved.items():
        if dom == tdom:
            continue
        c = cands.setdefault(dom, Cand(dom, resolved_by=how))
        for m, d in groups[key]:
            c.names[m.name] = c.names.get(m.name, 0) + 1
            c.channels.add(d.channel)
            if d.segment:
                c.segments.add(d.segment)
            c.head_to_head |= m.head_to_head
            c.mentions.append({"url": d.url, "quote": m.quote, "channel": d.channel, "head_to_head": m.head_to_head,
                               "title": d.title, "source": d.source})
    unresolved = [groups[k][0][0].name for k in groups if k not in resolved]
    if unresolved:
        log.info("competitor names without a confirmed website: %s", unresolved[:25])
    return cands


# ------------------------------------------------------------------ verification
def _verify(ctx: Ctx, entity: dict, profile: dict, c: Cand) -> dict | None:
    page = ctx.pages.get(c.domain) or ctx.web.fetch(f"https://{c.domain}", main_only=False)
    if page is None:
        return None
    ctx.pages[c.domain] = page
    final = registrable(page.final_url)
    if final == entity["domain"]:
        return None  # redirects to the target itself
    url, title = page.final_url, page.title
    text = f"{page.title}\n{page.description}\n{page.markdown}"
    ctx.evidence.register_doc(url, text)
    tprof = {"display_name": entity["display_name"], "aliases": entity.get("aliases", []), "vertical": entity.get("vertical"),
             "one_liner": entity.get("one_liner"),
             "segments": [f["text"] for f in profile.get("customer_segments", [])][:8],
             "size_signals": [f["text"] for f in profile.get("customer_size_signals", [])][:6],
             "modules": [f["text"] for f in profile.get("modules", [])][:10]}
    snippets = [{"url": m["url"], "text": m["quote"]} for m in c.mentions if m.get("quote")][:12]
    chk = ctx.claude.ask(
        ctx.settings.model_judge, prompt("competitor_verify"),
        [("target_profile", tprof),
         ("candidate", {"name": c.name, "domain": c.domain, "homepage_url": url, "title": title,
                        "homepage_markdown": text[:8000]}),
         ("snippets", snippets)],
        CompetitorCheck, effort="medium", label=f"competitor_verify:{c.domain}", fallbacks=ctx.judge_fallbacks())
    # Evidence is only collected here; record_evidence() adds it after the parallel map, in candidate order, so
    # evidence ids (and with them the writer's input and its cache key) are the same on every run.
    pending = []
    for q in chk.evidence_quotes:
        src = url if quote_in(q, ctx.evidence.doc(url)) else ctx.evidence.find_source(q, [m["url"] for m in c.mentions])
        if src:
            pending.append((src, q, c.domain, "scope" if src == url else "comparison",
                            page.source if src == url else "firecrawl", title if src == url else ""))
    for m in c.mentions:  # third-party mentions (vs pages, directories, roundups), verified against their page
        if m.get("quote"):
            pending.append((m["url"], m["quote"], c.domain, "comparison", m.get("source", "firecrawl"), m.get("title", "")))
    return {"_pending_evidence": pending, "name": chk.display_name.strip() or c.name, "domain": c.domain, "channels": sorted(c.channels),
            "same_vertical": chk.same_vertical, "operating": chk.operating, "is_software_vendor": chk.is_software_vendor,
            "acquired_or_renamed": chk.acquired_or_renamed, "segment": chk.segment, "scope": chk.scope,
            "head_to_head": chk.head_to_head_with_target or c.head_to_head,
            "relevance": round(max(0.0, min(1.0, chk.relevance)), 3), "evidence_ids": [],
            "independent_sources": 0, "homepage": url, "resolved_by": c.resolved_by, "mentions": len(c.mentions),
            "segments": sorted(c.segments), **({"graph_via": c.via} if c.via else {})}


def record_evidence(ctx: Ctx, v: dict) -> dict:
    """Add a verified candidate's collected quotes to the evidence store (sequentially, see _verify)."""
    eids = []
    for args in v.pop("_pending_evidence", []):
        eid = ctx.evidence.add(*args)
        if eid:
            eids.append(eid)
    eids = list(dict.fromkeys(eids))
    v["evidence_ids"] = eids
    v["independent_sources"] = len({registrable(ctx.evidence.get(e).url) for e in eids})
    return v


def select(verified: list[dict], keep: int = KEEP) -> tuple[list[dict], bool]:
    """§8.3 selection rule (+ >= 2 independent sources in the strict pass, criterion A5).
    Returns (competitors, thin_market)."""
    def ok(v, rel, strict):
        if not (v["is_software_vendor"] and v["same_vertical"] and v["operating"] and v["relevance"] >= rel):
            return False
        if strict:
            return (len(v["channels"]) >= 2 or v["head_to_head"]) and v["independent_sources"] >= 2
        return len(v["channels"]) >= 1

    rank = lambda v: (-v["relevance"], -len(v["channels"]), -v["independent_sources"], v["name"].lower())  # noqa: E731
    strict = sorted((v for v in verified if ok(v, 0.6, True)), key=rank)
    if len(strict) >= 5:
        return _dedupe(strict)[:keep], False
    relaxed = sorted((v for v in verified if ok(v, 0.45, False)), key=lambda v: (v not in strict, *rank(v)))
    return _dedupe(relaxed)[:keep], True


def _dedupe(vs: list[dict]) -> list[dict]:
    out, names = [], set()
    for v in vs:
        k = norm(v["name"])
        if k and k not in names:
            names.add(k)
            out.append(v)
    return out


def discover(ctx: Ctx, entity: dict, profile: dict) -> dict:
    graph = market_graph.for_run(ctx.settings)
    docs, vendors, searches = _collect(ctx, entity, profile)
    own = _self_compare_docs(ctx, entity, profile, len(docs) + 1)
    mentions = _extract_mentions(ctx, entity, docs + own)
    cands = _resolve(ctx, entity, mentions, docs + own, vendors)
    seeds = graph_seeds(graph, entity)
    for s in seeds:  # a seed already found by a search gains the graph channel; a new one is verified like any other
        c = cands.setdefault(s.domain, Cand(s.domain, resolved_by="graph"))
        for n, k in s.names.items():
            c.names.setdefault(n, k)
        c.channels.add("graph")
        c.via = s.via
    tnames = {norm(n) for n in [entity["display_name"], *entity.get("aliases", [])]}
    cands = {d: c for d, c in cands.items() if d != entity["domain"] and norm(c.name) not in tnames}
    log.info("competitor channels: searches %s, %d self-compare page chunks, %d graph seeds, %d candidates",
             searches, len(own), len(seeds), len(cands))
    ctx.save_json("candidates.json", sorted(
        [{"name": c.name, "domain": d, "channels": sorted(c.channels), "head_to_head": c.head_to_head,
          "names": c.names, "resolved_by": c.resolved_by, "mentions": c.mentions, "segments": sorted(c.segments),
          **({"graph_via": c.via} if c.via else {})} for d, c in cands.items()],
        key=lambda x: (-len(x["channels"]), -len(x["mentions"]))))

    key = lambda c: (-len(c.channels), -int(c.head_to_head), -len(c.mentions), c.domain)  # noqa: E731
    strong = sorted((c for c in cands.values() if len(c.channels) >= 2 or c.head_to_head), key=key)
    weak = sorted((c for c in cands.values() if not (len(c.channels) >= 2 or c.head_to_head)), key=key)
    to_verify = strong[:MAX_VERIFY]
    to_verify += weak[:max(0, MAX_VERIFY - len(to_verify))]
    verified = []
    for c, r in zip(to_verify, ctx.pmap(lambda c: _verify(ctx, entity, profile, c), to_verify)):
        if isinstance(r, Exception):
            ctx.warn(f"could not verify competitor {c.domain}: {type(r).__name__}")
        elif r:
            verified.append(record_evidence(ctx, r))
    chosen, thin = select(verified)
    if thin:
        ctx.flag("thin_market")
        ctx.warn(f"thin market: only {len(chosen)} competitors passed the relaxed rule "
                 f"(strict rule needs >= 5 with 2+ channels or a head-to-head page, and 2+ independent sources)")
    if len(chosen) < 2:
        raise StageError(f"only {len(chosen)} verified competitor(s) found for {entity['display_name']}; "
                         "a market map needs at least 2 (try --hint with the industry)")
    weak_sources = [v["name"] for v in chosen if v["independent_sources"] < 2]
    if weak_sources:
        ctx.warn(f"competitors with fewer than 2 independent sources: {weak_sources}")
    for v in chosen:  # earlier decks' placements of this company, for the map's consistency check
        v["graph_placements"] = [p for p in graph.placements(v["domain"]) if p.get("axis_kind") in AXIS_KINDS]
    out = {"competitors": chosen, "thin_market": thin, "verified_count": len(verified), "candidates": len(cands),
           "searches": searches, "self_compare_chunks": len(own), "graph_seeds": [s.domain for s in seeds],
           "rejected": [{"name": v["name"], "domain": v["domain"], "relevance": v["relevance"], "same_vertical": v["same_vertical"],
                         "operating": v["operating"], "channels": v["channels"]} for v in verified if v not in chosen]}
    ctx.save_json("competitors.json", out)
    ctx.evidence.save()
    return out
