"""M1: config, cache, cost ledger, evidence store, shared HTTP client (retry policy), render font fix."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
import pytest

from recur_deck.cache import CostLedger, DiskCache, cache_key
from recur_deck.clients.http import (Http, OfflineMiss, RateLimiter, RetryableError, ServiceError,
                                     ServiceUnavailable, redact_url)
from recur_deck.config import Settings
from recur_deck.evidence import EvidenceStore, normalise, quote_in


def make_http(tmp_path: Path, handler, **settings) -> tuple[Http, list[float], CostLedger]:
    sleeps: list[float] = []
    ledger = CostLedger()
    s = Settings(runs_dir=tmp_path, **settings)
    http = Http(s, DiskCache(s.cache_dir), ledger, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    return http, sleeps, ledger


# ---------------------------------------------------------------- config
def test_settings_never_expose_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret-123")
    monkeypatch.setenv("RECUR_FETCH_MODE", "bogus")
    s = Settings.load(env_file=tmp_path / "missing.env")
    assert s.has("firecrawl") and "fc-secret-123" not in repr(s) and "fc-secret-123" not in json.dumps(s.redacted(), default=str)
    assert s.fetch_mode == "direct-first" and set(s.redacted()["keys"]) == {"anthropic", "firecrawl"}


# ---------------------------------------------------------------- cache
def test_cache_hit_skips_network(tmp_path):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        return httpx.Response(200, json={"ok": 1})

    http, _, ledger = make_http(tmp_path, handler)
    a = http.request_json("exa", "POST", "https://api.exa.ai/search", json_body={"query": "x"})
    b = http.request_json("exa", "POST", "https://api.exa.ai/search", json_body={"query": "x"})
    c = http.request_json("exa", "POST", "https://api.exa.ai/search", json_body={"query": "y"})
    assert a.data == b.data == c.data == {"ok": 1}
    assert len(calls) == 2 and b.cached and not a.cached
    assert ledger.totals()["exa"]["cached"] == 1


def test_cache_key_ignores_secret_params(tmp_path):
    def handler(req):
        return httpx.Response(200, json=[{"domain": "a.com"}])

    http, _, _ = make_http(tmp_path, handler)
    r = http.request_json("someapi", "GET", "https://api.example.com/v2/search/ai2000",
                          params={"c": "CLIENT-SECRET"}, secret_params=("c",))
    assert "CLIENT-SECRET" not in r.url
    stored = list((tmp_path / "_cache").rglob("*.json"))
    assert stored and all("CLIENT-SECRET" not in p.read_text() for p in stored)
    assert redact_url("https://x.io/a?c=1&q=2", ("c",)) == "https://x.io/a?q=2"


def test_offline_mode_serves_only_cache(tmp_path):
    http, _, _ = make_http(tmp_path, lambda r: httpx.Response(200, json={}), offline=True)
    with pytest.raises(OfflineMiss):
        http.request_json("exa", "POST", "https://api.exa.ai/search", json_body={"q": 1})


# ---------------------------------------------------------------- retry policy
def test_429_is_retried_with_retry_after(tmp_path):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "slow down"})
        return httpx.Response(200, json={"results": []})

    http, sleeps, _ = make_http(tmp_path, handler)
    r = http.request_json("exa", "POST", "https://api.exa.ai/search", json_body={})
    assert r.status == 200 and n["i"] == 2 and sleeps == [2.0]


def test_5xx_retried_three_times_then_raises(tmp_path):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(503, json={"error": "down"})

    http, sleeps, _ = make_http(tmp_path, handler)
    with pytest.raises(RetryableError):
        http.request_json("firecrawl", "POST", "https://api.firecrawl.dev/v2/scrape", json_body={})
    assert n["i"] == 3 and len(sleeps) == 2 and all(1 <= s <= 8 for s in sleeps)


@pytest.mark.parametrize("status,exc", [(400, ServiceError), (402, ServiceUnavailable), (401, ServiceUnavailable)])
def test_4xx_not_retried(tmp_path, status, exc):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(status, json={"error": "nope"})

    http, sleeps, _ = make_http(tmp_path, handler)
    with pytest.raises(exc) as ei:
        http.request_json("exa", "POST", "https://api.exa.ai/search", json_body={})
    assert n["i"] == 1 and sleeps == [] and not isinstance(ei.value, RetryableError)
    if status in (401, 402):
        assert not http.is_up("exa")
        with pytest.raises(ServiceUnavailable):  # later calls fail fast, no network
            http.request_json("exa", "POST", "https://api.exa.ai/search", json_body={"other": 1})
        assert n["i"] == 1


def test_ok_status_404_is_returned_and_cached(tmp_path):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(404, json={"message": "not found"})

    http, _, ledger = make_http(tmp_path, handler)
    for _ in range(2):
        r = http.request_json("someapi", "GET", "https://api.example.com/v2/item/x", ok_statuses=(404,))
        assert r.status == 404
    assert n["i"] == 1 and ledger.totals()["someapi"]["cached"] == 1


def test_download_rejects_html_and_oversize(tmp_path):
    def handler(req):
        if "page" in str(req.url):
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")
        if "big" in str(req.url):
            return httpx.Response(200, headers={"content-type": "image/png"}, content=b"x" * 2000)
        return httpx.Response(200, headers={"content-type": "image/svg+xml"}, content=b"<svg/>")

    http, _, _ = make_http(tmp_path, handler)
    assert http.get_bytes("https://x.io/logo.svg")[0] == b"<svg/>"
    with pytest.raises(ServiceError):
        http.get_bytes("https://x.io/page")
    with pytest.raises(ServiceError):
        http.get_bytes("https://x.io/big.png", max_bytes=1000)


def test_rate_limiter_waits_for_window():
    t = {"now": 0.0}
    waits: list[float] = []

    def sleep(s):
        waits.append(s)
        t["now"] += s

    rl = RateLimiter(2, clock=lambda: t["now"], sleep=sleep)
    for _ in range(3):
        rl.acquire()
    assert len(waits) == 1 and 59 < waits[0] <= 60.1


# ---------------------------------------------------------------- cost ledger
def test_cost_ledger_totals(tmp_path):
    led = CostLedger()
    led.record("exa", "/search", 200, 120, cost_usd=0.007)
    led.record("exa", "/search", 200, 0, cached=True)
    led.record("anthropic", "writer", 200, 9000, cost_usd=0.4123)
    led.record("firecrawl", "/v2/scrape", 500, 300)
    t = led.totals()
    assert t["exa"] == {"calls": 2, "cached": 1, "errors": 0, "cost_usd": 0.007, "credits": 0.0}
    assert t["firecrawl"]["errors"] == 1 and led.total_usd() == pytest.approx(0.4193)
    led.write(tmp_path / "cost.json")
    assert json.loads((tmp_path / "cost.json").read_text())["total_usd"] == pytest.approx(0.4193)


# ---------------------------------------------------------------- evidence
PAGE = """# A2000 by GCS Software
**Ai2000** (formerly A2000) is the ERP for [apparel brands](https://x.io).
It covers PLM, EDI, warehouse and financials – all in one system."""


def test_quotes_must_be_verbatim():
    assert quote_in("Ai2000 (formerly A2000) is the ERP for apparel brands", PAGE)
    assert quote_in("warehouse and financials - all in one system", PAGE)          # dash style ignored
    assert quote_in("It covers PLM, EDI ... financials – all in one system", PAGE)  # elision, fragments in order
    assert not quote_in("Ai2000 is the leading ERP for apparel brands", PAGE)
    assert not quote_in("PLM ... ERP", PAGE)                                        # fragments too short
    assert normalise("“Smart”  quotes here") == '"smart" quotes here'


def test_evidence_store_drops_unverified_and_dedupes(tmp_path):
    st = EvidenceStore(tmp_path / "evidence.jsonl")
    st.register_doc("https://a2000software.com", PAGE)
    e1 = st.add("https://a2000software.com", "Ai2000 (formerly A2000)", "target", "identity", "firecrawl")
    assert e1 == "e1"
    assert st.add("https://a2000software.com", "Ai2000 (formerly A2000)", "target", "identity", "firecrawl") == "e1"
    assert st.add("https://a2000software.com", "Founded in 1985", "target", "product", "firecrawl") is None
    assert st.add_any("covers PLM, EDI, warehouse", "target", "product", "firecrawl") == "e2"
    assert st.valid_ids(["e2", "e9", "e2"]) == ["e2"]
    st.save()
    again = EvidenceStore.load(tmp_path / "evidence.jsonl")
    assert [e.id for e in again.items()] == ["e1", "e2"] and again.get("e2").url == "https://a2000software.com"


def test_long_quotes_are_trimmed_but_stay_verbatim():
    text = "word " * 200
    st = EvidenceStore()
    eid = st.add("https://x.io", text.strip(), "target", "product", "exa", text=text)
    q = st.get(eid).quote
    assert len(q) <= 300 and quote_in(q, text)


# ---------------------------------------------------------------- render font fix (no LibreOffice needed)
def test_render_copy_embeds_full_fonts_under_unique_names(tmp_path):
    from recur_deck import render
    from recur_deck.spec import TEMPLATE_PATH
    out = render._render_copy(TEMPLATE_PATH, tmp_path / "r.pptx")
    with zipfile.ZipFile(TEMPLATE_PATH) as zt, zipfile.ZipFile(out) as zo:
        assert set(zt.namelist()) == set(zo.namelist())
        slide = zo.read("ppt/slides/slide3.xml").decode()
        assert 'typeface="Noto Sans Arabic' not in slide and 'typeface="RecurRender Noto Light"' in slide
        f7 = zo.read("ppt/fonts/font7.fntdata")
        assert f7[34:36] == b"LP" and len(f7) > 150_000          # uncompressed full font, EOT magic
        assert zt.read("ppt/slides/slide5.xml") != zo.read("ppt/slides/slide5.xml")  # aliased in the render copy only
    # the deliverable is never modified
    with zipfile.ZipFile(TEMPLATE_PATH) as zt:
        assert b'typeface="Noto Sans Arabic Light"' in zt.read("ppt/slides/slide3.xml")
