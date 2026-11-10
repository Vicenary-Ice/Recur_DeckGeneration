"""Fallback-aware facade over page fetching and search (BUILD_PROMPT §10, adapted to Firecrawl + Anthropic).

fetch(url):  direct-first (default): plain HTTPS GET + HTML text extraction; Firecrawl scrape when the page is
             blocked, a bot wall, or too thin (JS-rendered). RECUR_FETCH_MODE=firecrawl reverses the order.
search(q):   Firecrawl search -> Claude server web search tool (when Firecrawl is unavailable)
Every degradation is recorded in `self.fallbacks` so the run report can say exactly what happened.
"""
from __future__ import annotations

import copy
import logging
import re
import threading
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .claude import Claude
from .firecrawl import Firecrawl, Page
from .http import Http, OfflineMiss, ServiceError

log = logging.getLogger("recur_deck.web")
MIN_TEXT = 400  # a page with less visible text than this is treated as JS-rendered / empty
BOT_WALL = re.compile(r"just a moment|attention required|access denied|are you a robot|verify you are human|"
                      r"captcha|request blocked|403 forbidden|enable javascript and cookies", re.I)
_BLOCKS = ["h1", "h2", "h3", "h4", "p", "li", "td", "th", "blockquote", "figcaption", "dt", "dd"]


def _text_lines(root) -> list[str]:
    """Text of every leaf block (a block or div that contains no other block or div), in document order."""
    lines: list[str] = []
    for el in root.find_all(_BLOCKS + ["div"]):
        if el.find(_BLOCKS + ["div"]):
            continue
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if txt and (not lines or lines[-1].lstrip("# ") != txt):
            lines.append(("# " if el.name in ("h1", "h2") else "") + txt)
    return lines


def html_to_page(url: str, html: str, final_url: str | None = None) -> Page:
    """Page from raw HTML: title, description, og:image, links, and markdown-ish main text
    (<main> when it has real content; navigation, footers and cookie banners removed)."""
    base = final_url or url
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    og = soup.find("meta", attrs={"property": "og:image"}) or soup.find("meta", attrs={"name": "og:image"})
    links = list(dict.fromkeys(h for h in (urljoin(base, a["href"]) for a in soup.find_all("a", href=True))
                               if h.startswith("http")))
    body = copy.copy(soup)
    for t in body(["script", "style", "noscript", "svg", "template", "iframe", "nav", "footer", "form"]):
        t.decompose()
    for t in body.select('[role="navigation"], [role="contentinfo"], [id*="cookie" i], [class*="cookie" i]'):
        t.decompose()
    main = body.find("main") or body.find(attrs={"role": "main"})
    lines = _text_lines(main) if main else []
    if sum(len(x) for x in lines) < MIN_TEXT:
        lines = _text_lines(body.body or body)
    return Page(url=url, final_url=base, status_code=200, title=title,
                description=(desc_tag.get("content") or "").strip() if desc_tag else "",
                markdown="\n".join(lines), raw_html=html, links=links,
                og_image=urljoin(base, og["content"]) if og and og.get("content") else None, source="web")


def usable(p: Page | None) -> bool:
    return bool(p) and len(p.markdown) >= MIN_TEXT and not BOT_WALL.search(f"{p.title}\n{p.markdown[:600]}")


class Web:
    def __init__(self, http: Http, firecrawl: Firecrawl, claude: Claude | None, fetch_mode: str = "direct-first"):
        self.http, self.fc, self.claude, self.fetch_mode = http, firecrawl, claude, fetch_mode
        self.fallbacks: list[str] = []
        self._lock = threading.Lock()

    def note(self, msg: str) -> None:
        with self._lock:
            if msg not in self.fallbacks:
                self.fallbacks.append(msg)
                log.warning("fallback: %s", msg)

    # ---------------------------------------------------------------- pages
    def _direct(self, url: str) -> tuple[Page | None, bool]:
        """(page, firecrawl_could_help). A 404/410 or a domain that does not exist is final; a bot wall,
        403/429/5xx, timeout or JS-only shell may still work through Firecrawl."""
        try:
            data, _, final = self.http.get_bytes(url, kind="html", max_bytes=8_000_000,
                                                 headers={"Accept": "text/html,application/xhtml+xml"})
        except ServiceError as e:
            log.info("direct fetch failed for %s: %s", url, e)
            return None, e.status not in (404, 410, "dns", "content-type")
        return html_to_page(url, data.decode("utf-8", errors="replace"), final), True

    def _firecrawl(self, url: str, main_only: bool) -> Page | None:
        if not self.fc.available:
            if self.fc.key:
                self.note("Firecrawl unavailable (credits or auth): pages fetched directly only")
            return None
        try:
            p = self.fc.scrape(url, only_main_content=main_only)
            return p if (p.markdown or p.raw_html) else None
        except OfflineMiss:
            return None
        except ServiceError as e:
            log.info("firecrawl scrape failed for %s: %s", url, e)
            return None

    def fetch(self, url: str, *, main_only: bool = True) -> Page | None:
        """One page as text (+ raw HTML). Returns None if every method fails. `main_only` applies to Firecrawl;
        direct fetches always keep the full raw HTML (logos) and extract the main text."""
        if self.fetch_mode == "firecrawl":
            p = self._firecrawl(url, main_only)
            return p if p is not None else self._direct(url)[0]
        p, could_help = self._direct(url)
        if usable(p) or not could_help:
            return p
        log.info("direct fetch of %s unusable (%s); trying Firecrawl", url,
                 "no response" if p is None else f"{len(p.markdown)} chars of text")
        q = self._firecrawl(url, main_only)
        return q if q is not None else p

    # ---------------------------------------------------------------- search
    def search(self, query: str, *, limit: int = 10, with_markdown: bool = False, include_domains: list[str] | None = None,
               exclude_domains: list[str] | None = None, source: str = "web") -> list[dict]:
        """[{url, title, description, markdown, via}] from Firecrawl search, else Claude web search."""
        if self.fc.available:
            try:
                return [{**r, "via": "firecrawl"} for r in self.fc.search(
                    query, limit=limit, scrape_markdown=with_markdown, include_domains=include_domains,
                    exclude_domains=exclude_domains, source=source)]
            except OfflineMiss:
                return []
            except ServiceError as e:
                log.info("firecrawl search failed: %s", e)
        if source != "web":
            return []
        if self.claude is not None and self.claude.settings.anthropic_api_key and not self.claude.settings.offline:
            self.note("Firecrawl search unavailable: used Claude web search (titles and URLs only)")
            try:
                q = query + (" " + " ".join(f"site:{d}" for d in include_domains[:1]) if include_domains else "")
                hits = self.claude.web_search(q)
                if exclude_domains:
                    hits = [h for h in hits if not any(d in h["url"] for d in exclude_domains)]
                return [{**r, "markdown": "", "via": "claude_web_search"} for r in hits][:limit]
            except Exception as e:  # noqa: BLE001 - last resort, never fatal
                log.info("claude web search failed: %s", e)
        return []
