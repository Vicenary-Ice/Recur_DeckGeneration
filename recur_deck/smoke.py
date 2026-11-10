"""One real call per service, printed, and recorded as test cassettes (tests/cassettes/*.json, keys stripped).

    python -m recur_deck.smoke            # uses .env; costs ~5 Firecrawl credits and < $0.01 of Claude
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from .cache import CostLedger, DiskCache
from .clients.claude import Claude
from .clients.http import Http
from .config import Settings
from .spec import KIT_ROOT

CASSETTES = KIT_ROOT / "tests" / "cassettes"


class Ping(BaseModel):
    company: str
    sells_software: bool


def _strip(obj, secrets: list[str]):
    s = json.dumps(obj, ensure_ascii=False)
    for k in secrets:
        s = s.replace(k, "***")
    return json.loads(s)


def _shrink(d):
    """Keep cassettes small: long strings truncated."""
    if isinstance(d, dict):
        return {k: _shrink(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_shrink(v) for v in d[:12]]
    if isinstance(d, str) and len(d) > 6000:
        return d[:6000]
    return d


def main() -> int:
    s = Settings.load()
    ledger = CostLedger()
    http = Http(s, DiskCache(s.cache_dir, read=False), ledger)
    CASSETTES.mkdir(parents=True, exist_ok=True)
    ok = True

    def record(name: str, data) -> None:
        (CASSETTES / f"{name}.json").write_text(json.dumps(_shrink(_strip(data, s.secrets())), indent=1, ensure_ascii=False))

    def fc_credits(d) -> tuple[float, float]:  # creditsUsed when Firecrawl reports it, else 1 (scrape/map price)
        used = d.get("creditsUsed") if isinstance(d, dict) else None
        return 0.0, float(used) if isinstance(used, (int, float)) else 1.0

    def raw(service, method, url, **kw):
        if service == "firecrawl":
            kw.setdefault("cost_fn", fc_credits)
        return http.request_json(service, method, url, **kw).data

    checks = []
    if s.has("firecrawl"):
        H = {"Authorization": f"Bearer {s.firecrawl_api_key}"}

        def fc_scrape():
            d = raw("firecrawl", "POST", "https://api.firecrawl.dev/v2/scrape", headers=H, json_body={
                "url": "https://a2000software.com", "formats": ["markdown", "rawHtml", "links"], "onlyMainContent": False,
                "waitFor": 1500, "timeout": 60000})
            record("firecrawl_scrape", d)
            md = (d.get("data") or {}).get("metadata") or {}
            return f"title={md.get('title')!r} final={md.get('url')} markdown={len((d.get('data') or {}).get('markdown') or '')} chars"

        def fc_map():
            d = raw("firecrawl", "POST", "https://api.firecrawl.dev/v2/map", headers=H, json_body={
                "url": "https://a2000software.com", "limit": 20, "sitemap": "include", "includeSubdomains": False,
                "ignoreQueryParameters": True})
            record("firecrawl_map", d)
            return f"{len(d.get('links') or [])} links, first: {(d.get('links') or [{}])[0]}"

        def fc_search():
            d = raw("firecrawl", "POST", "https://api.firecrawl.dev/v2/search", headers=H, json_body={
                "query": '"A2000" vs', "limit": 3, "sources": [{"type": "web"}], "country": "US"})
            record("firecrawl_search", d)
            web = ((d.get("data") or {}).get("web") or []) if isinstance(d.get("data"), dict) else d.get("data") or []
            return f"{len(web)} results, first: {web[0].get('url') if web else '-'}"
        checks += [("Firecrawl /v2/scrape", fc_scrape), ("Firecrawl /v2/map", fc_map), ("Firecrawl /v2/search", fc_search)]
    if s.has("anthropic"):
        def claude():
            c = Claude(s, DiskCache(s.cache_dir, read=False), ledger)
            out = c.ask(s.model_fast, "Answer from the text only.", [("text", "GCS Software makes A2000, an ERP for apparel brands.")],
                        Ping, effort="low", label="smoke")
            return f"{s.model_fast} -> {out.model_dump()}"
        checks.append(("Claude structured output", claude))

    for name, fn in checks:
        try:
            print(f"OK   {name}: {fn()}")
        except Exception as e:  # noqa: BLE001 - report every failure, keep going
            ok = False
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    missing = [k for k, v in s.redacted()["keys"].items() if not v]
    if missing:
        print(f"skipped (no key): {', '.join(missing)}")
    print(json.dumps({"cost_usd": ledger.total_usd(), "by_service": ledger.totals()}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
