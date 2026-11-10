"""M2: client contract tests. Request bodies must match BUILD_PROMPT §7 exactly; parsers must tolerate
missing optional fields. Recorded real responses (tests/cassettes/*.json, keys stripped) are replayed when
present."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from recur_deck.cache import CostLedger, DiskCache
from recur_deck.clients import firecrawl as fc_mod
from recur_deck.clients.claude import Claude, LLMError, cost_of, render_blocks
from recur_deck.clients.http import Http
from recur_deck.clients.web import Web, html_to_page, usable
from recur_deck.config import Settings

CASSETTES = Path(__file__).parent / "cassettes"


def cassette(name: str):
    p = CASSETTES / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def mk(tmp_path, handler, **kw):
    s = Settings(runs_dir=tmp_path, firecrawl_api_key="k-fc", anthropic_api_key="k-ant", **kw)
    ledger = CostLedger()
    return s, Http(s, DiskCache(s.cache_dir), ledger, transport=httpx.MockTransport(handler), sleep=lambda x: None), ledger


# ---------------------------------------------------------------- Firecrawl
def test_firecrawl_scrape_map_search_contracts(tmp_path):
    bodies = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        bodies.append((req.url.path, body, req.headers["authorization"]))
        if req.url.path == "/v2/scrape":
            return httpx.Response(200, json=cassette("firecrawl_scrape") or {"success": True, "data": {
                "markdown": "# A2000", "rawHtml": "<html></html>", "links": ["https://a2000software.com/erp"],
                "metadata": {"title": "A2000", "url": "https://a2000software.com/", "statusCode": 200,
                             "og:image": "https://a2000software.com/og.png"}}})
        if req.url.path == "/v2/map":
            return httpx.Response(200, json=cassette("firecrawl_map") or {"success": True, "links": [
                {"url": "https://a2000software.com/erp", "title": "ERP"}, {"url": "https://a2000software.com/about"}]})
        return httpx.Response(200, json=cassette("firecrawl_search") or {"success": True, "data": {"web": [
            {"url": "https://www.capterra.com/p/1/A2000/", "title": "A2000", "description": "Apparel ERP"},
            {"url": "https://x.com"}]}})

    s, http, ledger = mk(tmp_path, handler)
    fc = fc_mod.Firecrawl(http, s.firecrawl_api_key)
    page = fc.scrape("https://a2000software.com", only_main_content=False)
    links = fc.map("https://a2000software.com")
    hits = fc.search('"A2000" vs')
    assert bodies[0] == ("/v2/scrape", {"url": "https://a2000software.com", "formats": ["markdown", "rawHtml", "links"],
                                        "onlyMainContent": False, "waitFor": 1500, "timeout": 60000}, "Bearer k-fc")
    assert bodies[1][1] == {"url": "https://a2000software.com", "limit": 200, "sitemap": "include",
                            "includeSubdomains": False, "ignoreQueryParameters": True}
    assert bodies[2][1] == {"query": '"A2000" vs', "limit": 10, "sources": [{"type": "web"}], "country": "US"}
    assert page.markdown and page.final_url.startswith("https://") and page.main_only is False
    assert links and all(isinstance(l, dict) and l["url"] for l in links)
    assert hits and all(h["url"] for h in hits)
    assert ledger.totals()["firecrawl"]["credits"] >= 4


def test_firecrawl_search_filters_markdown_and_news(tmp_path):
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if body["sources"] == [{"type": "news"}]:
            return httpx.Response(200, json={"success": True, "creditsUsed": 2, "data": {"news": [
                {"title": "GCS Software launches Ai2000", "url": "https://news.example/a", "snippet": "A2000 is now Ai2000",
                 "date": "2026-03-01"}]}})
        return httpx.Response(200, json={"success": True, "creditsUsed": 5, "data": {"web": [
            {"url": "https://www.capterra.com/p/1/a2000/alternatives", "title": "A2000 alternatives", "markdown": "# Top"}]}})

    s, http, ledger = mk(tmp_path, handler)
    fc = fc_mod.Firecrawl(http, s.firecrawl_api_key)
    web = fc.search('"A2000" alternatives', limit=3, scrape_markdown=True, include_domains=["capterra.com", "g2.com"])
    news = fc.search('"Ai2000" ERP', limit=6, source="news")
    fc.search("apparel ERP", exclude_domains=["a2000software.com"])
    assert bodies[0]["includeDomains"] == ["capterra.com", "g2.com"] and "excludeDomains" not in bodies[0]
    assert bodies[0]["scrapeOptions"] == {"formats": ["markdown"], "onlyMainContent": True}
    assert bodies[1]["sources"] == [{"type": "news"}] and bodies[2]["excludeDomains"] == ["a2000software.com"]
    assert web[0]["markdown"] == "# Top" and news[0]["description"] == "A2000 is now Ai2000" and news[0]["date"]
    assert ledger.totals()["firecrawl"]["credits"] == 5 + 2 + 5   # creditsUsed reported by the API wins


def test_firecrawl_parsers_tolerate_missing_fields():
    p = fc_mod.parse_scrape("https://x.io", {"data": {}})
    assert p.markdown == "" and p.final_url == "https://x.io" and p.og_image is None
    assert fc_mod.parse_scrape("https://x.io", {"data": {"metadata": {"ogImage": ["a.png"], "title": ["T"]}}}).og_image == "a.png"
    assert fc_mod.parse_map({"links": ["https://a.io", {"url": "https://b.io"}, {"title": "no url"}]}) == [
        {"url": "https://a.io", "title": "", "description": ""}, {"url": "https://b.io", "title": "", "description": ""}]
    assert fc_mod.parse_search({"data": [{"url": "https://a.io"}]})[0]["url"] == "https://a.io"
    assert fc_mod.parse_search({"data": {"news": None}}, "news") == []


# ---------------------------------------------------------------- web facade: fetch order + fallbacks
PAGE_HTML = b"""<html><head><title>Uphance</title><meta property="og:image" content="/og.png"></head><body>
<nav><a href="/pricing">Pricing</a><a href="/features">Features</a></nav>
<main><h1>Uphance</h1><p>Cloud ERP for fashion brands, from design to delivery.</p>""" + \
    b"<p>" + b"Uphance runs orders, inventory, production and wholesale for growing fashion labels. " * 6 + b"</p>" + \
    b"</main><footer><p>Copyright Uphance Ltd</p></footer></body></html>"


def test_direct_first_uses_plain_fetch_when_usable(tmp_path):
    hosts = []

    def handler(req):
        hosts.append(req.url.host)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=PAGE_HTML)

    s, http, _ = mk(tmp_path, handler)
    web = Web(http, fc_mod.Firecrawl(http, s.firecrawl_api_key), None)
    page = web.fetch("https://uphance.com")
    assert hosts == ["uphance.com"] and page.source == "web" and usable(page)
    assert "Cloud ERP for fashion brands" in page.markdown and "Pricing" not in page.markdown and "Copyright" not in page.markdown
    assert page.og_image == "https://uphance.com/og.png" and "https://uphance.com/pricing" in page.links
    assert "<nav>" in page.raw_html  # full HTML kept for logo scraping


@pytest.mark.parametrize("body", [b"<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>",
                                  b"<html><body><div id='root'></div></body></html>"])
def test_direct_first_falls_back_to_firecrawl_for_bot_walls_and_js_shells(tmp_path, body):
    def handler(req):
        if req.url.host == "api.firecrawl.dev":
            return httpx.Response(200, json={"success": True, "data": {"markdown": "# Real page " + "text " * 100,
                                                                       "rawHtml": "<html></html>", "metadata": {"url": "https://x.io/"}}})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=body)

    s, http, ledger = mk(tmp_path, handler)
    page = Web(http, fc_mod.Firecrawl(http, s.firecrawl_api_key), None).fetch("https://x.io")
    assert page.source == "firecrawl" and ledger.totals()["firecrawl"]["credits"] == 1


@pytest.mark.parametrize("host", ["gone.example", "nope.example"])
def test_404_and_unknown_domains_do_not_spend_firecrawl_or_retry(tmp_path, host):
    calls = []

    def handler(req):
        calls.append(req.url.host)
        if req.url.host == "api.firecrawl.dev":
            pytest.fail("Firecrawl cannot fix a 404 or a domain that does not exist")
        if req.url.host == "nope.example":
            raise httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known")
        return httpx.Response(404, text="not found")

    s, http, _ = mk(tmp_path, handler)
    assert Web(http, fc_mod.Firecrawl(http, s.firecrawl_api_key), None).fetch(f"https://{host}") is None
    assert calls == [host]  # one attempt, no retries


def test_firecrawl_mode_falls_back_to_direct_on_402(tmp_path):
    def handler(req):
        if req.url.host == "api.firecrawl.dev":
            return httpx.Response(402, json={"error": "Payment required"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=PAGE_HTML)

    s, http, _ = mk(tmp_path, handler, fetch_mode="firecrawl")
    web = Web(http, fc_mod.Firecrawl(http, s.firecrawl_api_key), None, fetch_mode="firecrawl")
    page = web.fetch("https://uphance.com")
    assert page and page.source == "web" and not http.is_up("firecrawl")
    web.fetch("https://uphance.com/features")
    assert any("Firecrawl unavailable" in f for f in web.fallbacks)


def test_html_to_page_does_not_duplicate_nested_text():
    p = html_to_page("https://x.io", "<div><p>One line</p></div><ul><li>Two</li></ul>")
    assert p.markdown.splitlines() == ["One line", "Two"]


# ---------------------------------------------------------------- Claude helper (fake SDK client)
class Out(BaseModel):
    answer: str
    n: int


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def msg(text, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage, stop_details=None)


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeStream(r)


def mk_claude(tmp_path, replies):
    s = Settings(runs_dir=tmp_path, anthropic_api_key="k")
    fake = FakeClient(replies)
    return Claude(s, DiskCache(s.cache_dir), CostLedger(), client=fake), fake


def test_claude_request_shape_and_text_block(tmp_path):
    c, fake = mk_claude(tmp_path, [msg('{"answer": "ok", "n": 2}')])
    out = c.ask("claude-sonnet-5-5", "SYSTEM", [("entity", {"a": 1}), ("notes", "hi")], Out, effort="medium", label="t")
    assert out == Out(answer="ok", n=2)
    kw = fake.calls[0]
    assert "temperature" not in kw and "top_p" not in kw and "tool_choice" not in kw
    assert kw["output_config"]["effort"] == "medium" and kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["messages"][-1]["role"] == "user"  # never an assistant prefill
    assert "<entity>" in kw["messages"][0]["content"][-1]["text"]
    assert kw["extra_body"] == {"fallbacks": "default"}
    # cached: a second identical call does not hit the API
    assert c.ask("claude-sonnet-5-5", "SYSTEM", [("entity", {"a": 1}), ("notes", "hi")], Out, label="t") == out
    assert len(fake.calls) == 1


def test_claude_validation_retry_then_error(tmp_path):
    c, fake = mk_claude(tmp_path, [msg('{"answer": "x"}'), msg('{"answer": "x", "n": 3}')])
    assert c.ask("claude-haiku-5-5", "S", [("q", "x")], Out).n == 3
    assert "validation_errors" in fake.calls[1]["messages"][0]["content"][-1]["text"]
    assert "extra_body" not in fake.calls[0]  # Haiku 5.5 has no server-side fallback
    c2, _ = mk_claude(tmp_path / "b", [msg('{"bad": 1}'), msg('{"bad": 2}')])
    with pytest.raises(LLMError):
        c2.ask("claude-haiku-5-5", "S", [("q", "y")], Out)


def test_claude_refusal_falls_back_to_next_model(tmp_path):
    c, fake = mk_claude(tmp_path, [msg("", stop="refusal"), msg('{"answer": "fb", "n": 1}')])
    out = c.ask("claude-sonnet-5-5", "S", [("q", "z")], Out, fallbacks=["claude-haiku-5-5"])
    assert out.answer == "fb" and [k["model"] for k in fake.calls] == ["claude-sonnet-5-5", "claude-haiku-5-5"]


def test_cost_and_blocks():
    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    assert cost_of("claude-opus-5-5", u) == pytest.approx(6.0)
    assert render_blocks([("a", "x")]) == "<a>\nx\n</a>"
