"""Firecrawl v2 over raw HTTP (https://docs.firecrawl.dev/api-reference/endpoint/scrape.md).

scrape: 1 credit/page · map: 1 credit/call (links are objects) · search: 2 credits per 10 results
(+1 credit per scraped result when scrapeOptions is set).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .http import Http, ServiceUnavailable

BASE = "https://api.firecrawl.dev/v2"


@dataclass
class Page:
    url: str
    final_url: str = ""
    status_code: int | None = None
    title: str = ""
    description: str = ""
    markdown: str = ""
    raw_html: str = ""
    links: list[str] = field(default_factory=list)
    og_image: str | None = None
    source: str = "firecrawl"
    main_only: bool = False  # True when fetched with onlyMainContent (header/nav may be missing)


def parse_scrape(url: str, data: dict) -> Page:
    d = (data or {}).get("data") or {}
    md = d.get("metadata") or {}
    og = md.get("ogImage") or md.get("og:image")
    if isinstance(og, list):
        og = og[0] if og else None
    title = md.get("title") or md.get("og:title") or ""
    if isinstance(title, list):
        title = title[0] if title else ""
    desc = md.get("description") or md.get("og:description") or ""
    if isinstance(desc, list):
        desc = desc[0] if desc else ""
    links = [l for l in (d.get("links") or []) if isinstance(l, str)]
    return Page(url=url, final_url=md.get("url") or md.get("sourceURL") or url, status_code=md.get("statusCode"),
                title=str(title), description=str(desc), markdown=d.get("markdown") or "",
                raw_html=d.get("rawHtml") or "", links=links, og_image=og)


def parse_map(data: dict) -> list[dict]:
    out = []
    for l in (data or {}).get("links") or []:
        if isinstance(l, str):
            out.append({"url": l, "title": "", "description": ""})
        elif isinstance(l, dict) and l.get("url"):
            out.append({"url": l["url"], "title": l.get("title") or "", "description": l.get("description") or ""})
    return out


def parse_search(data: dict, source: str = "web") -> list[dict]:
    d = (data or {}).get("data") or {}
    items = d.get(source) if isinstance(d, dict) else d  # v2: data.web[] / data.news[]; tolerate a bare list
    out = []
    for it in items or []:
        if isinstance(it, dict) and it.get("url"):
            md = it.get("metadata") or {}
            out.append({"url": it["url"], "title": it.get("title") or md.get("title") or "",
                        "description": it.get("description") or it.get("snippet") or md.get("description") or "",
                        "markdown": it.get("markdown") or "", "date": it.get("date") or ""})
    return out


class Firecrawl:
    def __init__(self, http: Http, api_key: str | None):
        self.http, self.key = http, api_key

    @property
    def available(self) -> bool:
        # offline: served from the cache, which never needs (or sends) a key
        return (bool(self.key) or self.http.settings.offline) and self.http.is_up("firecrawl")

    def _post(self, path: str, body: dict, credits) -> dict:
        if not self.key and not self.http.settings.offline:
            raise ServiceUnavailable("firecrawl", "no-key", "FIRECRAWL_API_KEY not set")
        r = self.http.request_json("firecrawl", "POST", f"{BASE}{path}", json_body=body,
                                   headers={"Authorization": f"Bearer {self.key}"} if self.key else None,
                                   cost_fn=lambda d: (0.0, float(credits(d))), endpoint=path)
        return r.data

    def scrape(self, url: str, *, only_main_content: bool = True, formats: tuple[str, ...] = ("markdown", "rawHtml", "links"),
               wait_for: int = 1500, timeout_ms: int = 60000) -> Page:
        body = {"url": url, "formats": list(formats), "onlyMainContent": only_main_content,
                "waitFor": wait_for, "timeout": min(timeout_ms, 300000)}
        page = parse_scrape(url, self._post("/scrape", body, lambda d: 1))
        page.main_only = only_main_content
        return page

    def map(self, url: str, *, search: str | None = None, limit: int = 200) -> list[dict]:
        body = {"url": url, "limit": limit, "sitemap": "include", "includeSubdomains": False,
                "ignoreQueryParameters": True}
        if search:
            body["search"] = search
        return parse_map(self._post("/map", body, lambda d: 1))

    def search(self, query: str, *, limit: int = 10, country: str = "US", scrape_markdown: bool = False,
               include_domains: list[str] | None = None, exclude_domains: list[str] | None = None,
               source: str = "web") -> list[dict]:
        """Web (or news) search. include/exclude domains are mutually exclusive (Firecrawl adds site: / -site:)."""
        body: dict = {"query": query, "limit": limit, "sources": [{"type": source}], "country": country}
        if include_domains:
            body["includeDomains"] = include_domains
        elif exclude_domains:
            body["excludeDomains"] = exclude_domains
        if scrape_markdown:
            body["scrapeOptions"] = {"formats": ["markdown"], "onlyMainContent": True}

        def credits(d) -> float:
            if isinstance(d, dict) and isinstance(d.get("creditsUsed"), (int, float)):
                return float(d["creditsUsed"])
            n = len(parse_search(d, source))
            return 2 * math.ceil(max(n, 1) / 10) + (n if scrape_markdown else 0)

        return parse_search(self._post("/search", body, credits), source)
