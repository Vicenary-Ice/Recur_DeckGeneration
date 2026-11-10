"""A small simulated web (Firecrawl search/scrape/map + company websites) and a fake Claude, for end-to-end
tests of the pipeline without network or keys. The fake Claude answers each prompt from the documents it is
given, like the real one should, so evidence verification and domain confirmation are exercised for real."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx

FIX = Path(__file__).resolve().parent.parent / "fixtures"
PROMPTS = Path(__file__).resolve().parent.parent / "recur_deck" / "prompts"

TARGET = {"name": "Ai2000", "domain": "a2000software.com"}
COMPS = {  # domain -> (name, homepage sentence, cell, logo fixture)
    "aims360.com": ("AIMS360", "AIMS360 is the apparel ERP for small and mid-size fashion brands, with QuickBooks sync for the books.",
                    ("left", "bottom"), "ai2000/logos/aims360/1_site.png"),
    "apparelmagic.com": ("ApparelMagic", "ApparelMagic is cloud ERP for apparel brands that sell wholesale and online.",
                         ("left", "bottom"), "ai2000/logos/apparelmagic/2_site.jpg"),
    "uphance.com": ("Uphance", "Uphance is the all-in-one ERP for small fashion brands.", ("left", "bottom"),
                    "ai2000/logos/uphance/1_site.png"),
    "cgsinc.com": ("BlueCherry", "BlueCherry by CGS is the enterprise apparel suite for large fashion brands.", ("left", "top"),
                   "ai2000/logos/bluecherry/1_site.jpg"),
    "aptean.com": ("Aptean", "Aptean Apparel ERP gives large brands product, orders and accounting in one system.", ("right", "top"),
                   "ai2000/logos/aptean/1_brandfetch.png"),
    "fdm4.com": ("FDM4", "FDM4 is apparel ERP with financials for mid-size brands.", ("right", "bottom"),
                 "ai2000/logos/fdm4/1_brandfetch.png"),
    "netsuite.com": ("NetSuite", "NetSuite is cloud ERP with financials, configured for apparel brands by partners.",
                     ("right", "bottom"), "ai2000/logos/netsuite/1_site.jpg"),
}
HOME = ("Ai2000 (formerly A2000) is the ERP for apparel, footwear and home goods brands. "
        "Ai2000 covers PLM, retailer orders, EDI, warehouse and financials in one system. "
        "Brands such as large department-store suppliers run their retailer programs on Ai2000. ")
PAGES = {
    "https://a2000software.com/erp": "Our ERP manages product development, orders, EDI compliance and chargebacks. "
                                     "Financials and general ledger are built in, so brands keep their books on Ai2000. ",
    "https://a2000software.com/about": "GCS Software has built apparel software since 1985. "
                                       "We serve brands from $10M to $1B in revenue. ",
    "https://a2000software.com/industries": "Apparel, footwear, accessories and home goods brands. ",
}
FILLER = "Our customers range from emerging labels to established brands across North America and Europe. " * 5


def sentence_with(text: str, name: str) -> str:
    for s in re.split(r"(?<=[.!?])\s+|\n", text):
        if name.lower() in s.lower() and s.strip():
            s = re.sub(r"\[([^\]]+)\]\([^)\s]+\)", r"\1", s)  # quote link text, as a reader would (never cut a link)
            return s.strip()[:190]
    return name


def site_html(domain: str, body: str, title: str, name: str) -> str:
    return (f'<html><head><title>{title}</title><meta property="og:image" content="https://{domain}/og.png"></head>'
            f'<body><header><a href="/"><img src="/assets/logo-{domain}.png" alt="{name} logo" width="200"></a>'
            f'<nav><a href="/products">Products</a><a href="/pricing">Pricing</a></nav></header>'
            f'<main><h1>{name}</h1><p>{body}</p><p>{FILLER}</p></main><footer>Copyright</footer></body></html>')


# Extra vendors for the deep-discovery world (C3): the live Ai2000 run never found FDM4, Uphance, Sync or Datatex.
DEEP_COMPS = {
    "datatex.com": ("Datatex", "Datatex is ERP for textile and apparel manufacturers.", ("right", "top"), "ai2000/logos/aptean/1_brandfetch.png"),
    "syncapparel.com": ("Sync", "Sync is apparel ERP for growing fashion brands.", ("left", "bottom"), "ai2000/logos/uphance/1_site.png"),
    "styleman.com": ("STYLEman", "STYLEman is ERP for fashion brands and retailers.", ("right", "bottom"), "ai2000/logos/fdm4/1_brandfetch.png"),
    "rlmapparel.com": ("RLM Apparel", "RLM Apparel is apparel software for wholesale brands.", ("left", "bottom"), "ai2000/logos/aims360/1_site.png"),
    "fulfil.io": ("Fulfil", "Fulfil is ERP for ecommerce and apparel brands.", ("left", "bottom"), "ai2000/logos/uphance/1_site.png"),
    "instylesoft.com": ("InStyle", "InStyle is apparel software for wholesale distribution.", ("left", "bottom"), "ai2000/logos/aims360/1_site.png"),
    "apparel21.com": ("Apparel21", "Apparel21 is ERP for fashion retailers and wholesalers.", ("right", "bottom"), "ai2000/logos/fdm4/1_brandfetch.png"),
    "fishbowlinventory.com": ("Fishbowl", "Fishbowl is inventory software for manufacturers and apparel brands.", ("left", "bottom"), "ai2000/logos/uphance/1_site.png"),
    "acumatica.com": ("Acumatica", "Acumatica is cloud ERP with an edition for apparel distributors.", ("right", "top"), "ai2000/logos/aptean/1_brandfetch.png"),
    "odoo.com": ("Odoo", "Odoo is open-source ERP used by apparel brands.", ("right", "bottom"), "ai2000/logos/netsuite/1_site.jpg"),
}
DEEP_VARIANTS = ["Aptean Exenta", "CGS BlueCherry"]  # name variants that must merge into Aptean / BlueCherry
# 6000+ characters of list text that names only the early vendors, so the tail is past the old 6000-char cut
DEEP_HEAD = ("# Best apparel ERP software (2026)\n"
             + "".join(f"## {i}. {n}\n{n} is a well-known option for apparel brands that need wholesale, EDI and "
                       f"inventory in one place, with reporting for merchandisers and planners.\n"
                       for i, n in enumerate(["BlueCherry", "Aptean", "AIMS360", "NetSuite"] * 12, 1)))
DEEP_TAIL = ("\n## More vendors to consider\nAlso worth a look: FDM4, Uphance, [Sync](https://syncapparel.com), "
             "[Datatex](https://www.datatex.com), [RLM Apparel](https://rlmapparel.com), "
             "[InStyle](https://www.instylesoft.com) and [Apparel21](https://www.apparel21.com).\n")


# Agent 2 (market memory): the target's own comparison page, a sub-segment's vendors, a vendor only the graph knows.
COMPARE_COMPS = {
    "brightpearl.com": ("Brightpearl", "Brightpearl is retail operations software for wholesale apparel brands.",
                        ("left", "bottom"), "ai2000/logos/uphance/1_site.png"),
}
COMPARE_PAGES = {  # url -> (title, body)
    "https://a2000software.com/compare/ai2000-vs-brightpearl": (
        "Ai2000 vs Brightpearl | Ai2000",
        "Ai2000 vs [Brightpearl](https://www.brightpearl.com): Brightpearl runs orders, while Ai2000 adds PLM, EDI and "
        "financials for apparel brands. "),
}
SEGMENT_COMPS = {
    "deconetwork.com": ("DecoNetwork", "DecoNetwork is business software for apparel decorators and print shops.",
                        ("left", "bottom"), "ai2000/logos/aims360/1_site.png"),
    "printavo.com": ("Printavo", "Printavo is shop management software for screen printers and apparel decorators.",
                     ("left", "bottom"), "ai2000/logos/aims360/1_site.png"),
}
GRAPH_ONLY_COMPS = {"apparel21.com": DEEP_COMPS["apparel21.com"]}  # named by no search result in the default world


class World:
    def __init__(self, bad_target_logo: bool = False, deep: bool = False, compare: bool = False,
                 segments: bool = False, graph_only: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.searches: list[dict] = []
        self.bad_target_logo = bad_target_logo
        self.deep = deep  # C3: long roundup, competitors page, Capterra category page, more vendors, name variants
        self.compare = compare        # the target's site has a comparison page (self_compare channel)
        self.segments = segments      # searches for apparel decorators find decorator software (segment channel)
        self.comps = {**COMPS, **DEEP_COMPS} if deep else COMPS
        if compare or segments or graph_only:
            self.comps = {**self.comps, **(COMPARE_COMPS if compare else {}), **(SEGMENT_COMPS if segments else {}),
                          **(GRAPH_ONLY_COMPS if graph_only else {})}
        self.mention_names = [n for n, *_ in self.comps.values()] + (DEEP_VARIANTS if deep else [])

    # ---------------------------------------------------------------- HTTP
    def handler(self, req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        self.calls.append((host, path))
        body = json.loads(req.content) if req.content else {}
        if host == "api.firecrawl.dev":
            if path == "/v2/scrape":
                return httpx.Response(200, json={"success": True, "data": self.scrape(body["url"])})
            if path == "/v2/map":
                return httpx.Response(200, json={"success": True, "links": [{"url": u} for u in PAGES] +
                                                 [{"url": "https://a2000software.com/privacy"}] +
                                                 ([{"url": u} for u in COMPARE_PAGES] if self.compare else [])})
            if path == "/v2/search":
                self.searches.append(body)
                src = body["sources"][0]["type"]
                return httpx.Response(200, json={"success": True, "data": {src: self.search(body)}})
        dom = host.removeprefix("www.")
        m = re.match(r"/assets/logo-(.+)\.png", path)
        if m:
            d = m.group(1)
            if d != TARGET["domain"] and d not in self.comps:
                return httpx.Response(404, text="not found")  # e.g. an @2x sibling the site does not have
            if d == TARGET["domain"]:
                src = FIX / ("ai2000/logos/apparelmagic/1_photo.jpg" if self.bad_target_logo else "ai2000/logos/ai2000/1_brandfetch.png")
            else:
                src = FIX / self.comps[d][3]
            ct = "image/jpeg" if src.suffix == ".jpg" else "image/png"
            return httpx.Response(200, headers={"content-type": ct}, content=src.read_bytes())
        if path == "/og.png":
            return httpx.Response(200, headers={"content-type": "image/jpeg"},
                                  content=(FIX / "ai2000/logos/apparelmagic/1_photo.jpg").read_bytes())
        html = self.site(dom, str(req.url))
        if html:
            return httpx.Response(200, headers={"content-type": "text/html"}, content=html.encode())
        return httpx.Response(404, text="not found")

    def site(self, dom: str, url: str) -> str | None:
        if dom == TARGET["domain"] and self.compare and url.rstrip("/") in COMPARE_PAGES:
            title, body = COMPARE_PAGES[url.rstrip("/")]
            return site_html(dom, body, title, "Ai2000")
        if dom == TARGET["domain"]:
            body = PAGES.get(url.rstrip("/"), HOME)
            return site_html(dom, body, "Ai2000 ERP | Apparel ERP", "Ai2000")
        if dom in self.comps:
            n, s, *_ = self.comps[dom]
            return site_html(dom, s, f"{n} by CGS | Fashion ERP" if n == "BlueCherry" else f"{n} | Apparel ERP", n)
        if dom == "ai2000.ai":
            return site_html(dom, "AI 2000 is the conference for artificial intelligence.", "AI 2000 Conference", "AI 2000")
        return None

    def search(self, body: dict) -> list[dict]:
        q = body["query"]
        if body["sources"][0]["type"] == "news":
            return [{"title": "GCS Software renames A2000 to Ai2000", "url": "https://news.example.com/ai2000",
                     "snippet": "GCS Software has renamed its A2000 apparel ERP to Ai2000.", "date": "2026-02-01"}]
        if self.segments and "decorator" in q.lower():  # segment channel: decorator software
            if body.get("scrapeOptions"):
                md = ("# Best software for apparel decorators\nShops compare [Printavo](https://www.printavo.com) and "
                      "DecoNetwork for orders, art approval and production.")
                return [{"url": "https://www.example-decorating.com/best-decorator-software",
                         "title": "Best software for apparel decorators", "description": "", "markdown": md}]
            return [{"url": "https://www.deconetwork.com/", "title": "DecoNetwork | Software for decorators",
                     "description": SEGMENT_COMPS["deconetwork.com"][1]}]
        if self.deep and body.get("includeDomains") == ["capterra.com", "getapp.com"]:  # C3 category page
            md = ("# Apparel ERP software\nCompare apparel ERP vendors: [Fishbowl](https://www.fishbowlinventory.com), "
                  "[Acumatica](https://www.acumatica.com), Aptean Exenta, [Odoo](https://www.odoo.com) and "
                  "[Datatex](https://www.datatex.com).")
            return [{"url": "https://www.capterra.com/apparel-erp-software/", "title": "Best Apparel ERP Software 2026",
                     "description": "", "markdown": md}]
        if self.deep and q.endswith(" competitors") and not body.get("includeDomains"):  # C3 competitors page
            md = ("# A2000 competitors\nTop competitors of A2000 include [STYLEman](https://www.styleman.com), "
                  "[Fulfil](https://fulfil.io), CGS BlueCherry and Odoo.")
            return [{"url": "https://www.owler.com/company/a2000/competitors", "title": "A2000 competitors",
                     "description": "", "markdown": md}]
        if body.get("includeDomains"):  # directory channel, with markdown
            md = "# Top A2000 Alternatives\nPopular alternatives: AIMS360, ApparelMagic, BlueCherry and Uphance are used by apparel brands."
            return [{"url": "https://www.capterra.com/p/1/A2000/alternatives", "title": "A2000 alternatives", "description": "", "markdown": md}]
        if q.startswith("best ") and self.deep:  # a long list: the last vendors sit past 6000 characters
            return [{"url": "https://www.example-roundup.com/best-apparel-erp", "title": "Best apparel ERP software (2026)",
                     "description": "", "markdown": DEEP_HEAD + DEEP_TAIL}]
        if q.startswith("best "):  # roundup channel, with markdown and outbound links
            md = ("# Best apparel ERP software\nThe best apparel ERP vendors include BlueCherry, [NetSuite](https://www.netsuite.com), "
                  "FDM4, Aptean, Uphance and AIMS360.")
            return [{"url": "https://www.example-roundup.com/best-apparel-erp", "title": "Best apparel ERP software (2026)",
                     "description": "", "markdown": md}]
        if body.get("excludeDomains") and not q.startswith('"'):  # similar channel: the target's one-liner
            return [{"url": f"https://www.{d}/", "title": f"{self.comps[d][0]} | Apparel ERP", "description": self.comps[d][1]}
                    for d in ("aims360.com", "apparelmagic.com", "aptean.com", "fdm4.com")] + \
                   [{"url": "https://www.erp-blog.example/top-10-apparel-erp", "title": "Top 10 apparel ERP systems",
                     "description": "Uphance and FDM4 lead our list of apparel ERP systems."}]
        if " vs" in q:
            return [{"url": "https://www.apparelmagic.com/apparelmagic-vs-a2000", "title": "ApparelMagic vs A2000",
                     "description": "Compare ApparelMagic vs A2000 for apparel brands."},
                    {"url": "https://www.aims360.com/a2000-vs-aims360", "title": "A2000 vs AIMS360",
                     "description": "A2000 vs AIMS360: which apparel ERP fits your brand?"},
                    {"url": "https://www.fdm4.com/fdm4-vs-a2000", "title": "FDM4 vs A2000",
                     "description": "FDM4 vs A2000 for mid-size apparel brands."}]
        if "alternatives" in q:
            return [{"url": "https://www.g2.com/products/a2000/competitors", "title": "A2000 competitors",
                     "description": "Alternatives to A2000 include NetSuite, Aptean and BlueCherry."}]
        if "formerly" in q:
            return [{"url": "https://www.capterra.com/p/1/A2000/", "title": "A2000 by GCS Software",
                     "description": "Ai2000 (formerly A2000) by GCS Software, a2000software.com"}]
        m = re.match(r'"([^"]+)" ', q)
        if m and m.group(1) not in ("Ai2000", "A2000"):  # name -> domain lookup
            for d, (n, s, *_) in self.comps.items():
                if n.lower() == m.group(1).lower():
                    return [{"url": f"https://{d}/", "title": f"{n} | Apparel ERP", "description": s}]
            return []
        return [{"url": "https://a2000software.com/", "title": "Ai2000 ERP", "description": "Apparel ERP"},
                {"url": "https://ai2000.ai/", "title": "AI 2000 conference", "description": "An AI event"}]

    def scrape(self, url: str) -> dict:
        dom = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        html = self.site(dom, url)
        if not html:
            return {"markdown": "", "metadata": {"statusCode": 404, "url": url}}
        from recur_deck.clients.web import html_to_page
        p = html_to_page(url, html)
        return {"markdown": p.markdown, "rawHtml": html, "links": p.links,
                "metadata": {"title": p.title, "url": f"https://{dom}/", "statusCode": 200, "ogImage": p.og_image}}


# ---------------------------------------------------------------- fake Claude
def _prompt_name(system_text: str) -> str:
    for p in PROMPTS.glob("*.md"):
        if system_text.startswith(p.read_text()[:60]):
            return p.stem
    return "write_deck" if "partner at Recur" in system_text else "?"


def _block(text: str, tag: str):
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return m.group(1)


class FakeClaude:
    """Mimics client.messages.stream(**kw). Override `overrides[prompt_name]` with a callable(user_text) -> dict."""

    def __init__(self, world: World, overrides: dict | None = None):
        self.world = world
        self.overrides = overrides or {}
        self.calls: list[str] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kw):
        name = _prompt_name(kw["system"][0]["text"])
        user = kw["messages"][0]["content"][-1]["text"]
        self.calls.append(name)
        fn = self.overrides.get(name) or getattr(self, f"a_{name}")
        out = fn(user)
        text = out if isinstance(out, str) else json.dumps(out)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=300, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        msg = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=usage,
                              stop_details=None)

        class S:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def get_final_message(s):
                return msg
        return S()

    # ------------------------------------------------------------ answers
    def a_entity_judge(self, user):
        return {"ranked": [
            {"domain": "a2000software.com", "score": 0.93, "display_name": "Ai2000", "legal_name": "GCS Software, LLC",
             "aliases": ["A2000"], "vertical": "ERP for apparel, footwear and home goods brands",
             "one_liner": "ERP covering PLM, orders, EDI, warehouse and financials for apparel brands",
             "evidence_quotes": ["Ai2000 (formerly A2000) is the ERP for apparel, footwear and home goods brands"],
             "why": "site says formerly A2000"},
            {"domain": "ai2000.ai", "score": 0.05, "display_name": "AI 2000", "legal_name": None, "aliases": [],
             "vertical": "events", "one_liner": "AI conference", "evidence_quotes": [], "why": "an event, not software"}],
            "rebrand_detected": True, "notes": ""}

    def a_profile_extract(self, user):
        F = lambda t, q, u: {"text": t, "evidence_quote": q, "source_url": u}  # noqa: E731
        h, erp, about = "https://a2000software.com", "https://a2000software.com/erp", "https://a2000software.com/about"
        return {"modules": [F("PLM, orders, EDI, warehouse, financials", "Ai2000 covers PLM, retailer orders, EDI, warehouse and financials in one system", h),
                            F("Chargebacks and EDI compliance", "Our ERP manages product development, orders, EDI compliance and chargebacks", erp),
                            F("Made up module", "Ai2000 has an AI copilot for buyers", h)],  # not on the page -> dropped
                "customer_segments": [F("Apparel and home goods brands", "Apparel, footwear, accessories and home goods brands", "https://a2000software.com/industries")],
                "customer_size_signals": [F("$10M to $1B brands", "We serve brands from $10M to $1B in revenue", about)],
                "deployment": [], "integrations": [],
                "differentiators": [F("Books on Ai2000", "Financials and general ledger are built in, so brands keep their books on Ai2000", erp),
                                    F("Retailer programs", "Brands such as large department-store suppliers run their retailer programs on Ai2000", h)],
                "recent_news": [F("Renamed to Ai2000", "GCS Software has renamed its A2000 apparel ERP to Ai2000", "https://news.example.com/ai2000")],
                "gaps_or_opportunities": [F("Since 1985", "GCS Software has built apparel software since 1985", about)],
                "tenure": [F("Apparel software since 1985", "GCS Software has built apparel software since 1985", about)],
                "customers": [F("Department-store suppliers", "Brands such as large department-store suppliers run their retailer programs on Ai2000", h)],
                "switching": [F("The books live in Ai2000", "Financials and general ledger are built in, so brands keep their books on Ai2000", erp)],
                "gtm_signals": [], "tech_signals": [], "team_signals": []}

    def a_competitor_extract(self, user):
        docs = _block(user, "documents") or []
        out = []
        for d in docs:
            for n in self.world.mention_names:
                if re.search(rf"(?<![A-Za-z0-9]){re.escape(n)}(?![A-Za-z0-9])", d["text"], re.I):
                    link = re.search(rf"\[{re.escape(n)}\]\((https?://[^)]+)\)", d["text"])
                    out.append({"doc_id": d["doc_id"], "name": n, "domain": link.group(1) if link else None,
                                "quote": sentence_with(d["text"], n), "head_to_head": " vs " in d["title"]})
        return {"mentions": out}

    def a_competitor_segments(self, user):
        return {"segments": [s for s in (_block(user, "segment_facts") or [])][:2]}

    def a_domain_guess(self, user):
        known = {"BlueCherry": "cgsinc.com", "Uphance": "uphance.io", "Aptean": "aptean.com"}  # uphance.io is wrong
        return {"guesses": [{"name": n, "domain": known.get(n)} for n in _block(user, "names") or []]}

    def a_competitor_verify(self, user):
        cand = _block(user, "candidate")
        dom = cand["domain"]
        n, s, cell, _ = self.world.comps[dom]
        return {"is_software_vendor": True, "same_vertical": True, "operating": True, "acquired_or_renamed": None,
                "display_name": n, "segment": "large brands" if cell[1] == "top" else "small and mid brands",
                "scope": "ops + financials" if cell[0] == "right" else "ops only", "head_to_head_with_target": False,
                "evidence_quotes": [s], "relevance": 0.9 if dom != "netsuite.com" else 0.7}

    def a_logo_check(self, user):
        comp = _block(user, "company")
        cands = _block(user, "candidates") or []
        out = []
        for c in cands:
            good = c["source"] in ("header_img", "jsonld", "header_svg")
            out.append({"candidate_id": c["candidate_id"], "is_logo": good, "text_read": comp["name"] if good else "",
                        "matches_company": good, "kind": "wordmark" if good else "banner_or_photo",
                        "legible_on_navy": good, "confidence": 0.9})
        return {"verdicts": out}

    def a_write_deck(self, user):
        d = json.loads((FIX / "ai2000/content.json").read_text())
        ev = _block(user, "evidence") or []
        ids = [e["id"] for e in ev]
        of = lambda *kinds: [e["id"] for e in ev if e["about"] == "target" and e["kind"] in kinds]  # noqa: E731
        d["thesis"][0]["evidence_ids"] = of("news", "segment")[:2] or ids[:2]
        d["thesis"][1]["evidence_ids"] = of("tenure", "customers", "switching")[:2] or ids[:2]
        d["thesis"][2]["evidence_ids"] = of("customers", "gtm")[:2] or ids[:2]
        levers = _block(user, "allowed_levers") or {}
        d["lever"] = next(iter(levers), "ai_native_gtm")
        d["lever_gap_evidence_ids"] = (levers.get(d["lever"]) or {}).get("evidence_ids", [])[:2]
        for c in d["market"]["competitors"]:
            c["evidence_ids"] = [e["id"] for e in ev if e["about"] == c["domain"]][:2]
            own = [e["id"] for e in ev if e["about"] == c["domain"] and c["domain"] in e["url"]]
            c["x_evidence_ids"] = c["y_evidence_ids"] = own[:1] or c["evidence_ids"][:1]
        target = [e["id"] for e in ev if e["about"] == "target"]
        d["market"]["our_take"]["point_evidence_ids"] = [target[:1], target[1:2] or target[:1]]
        d["market"]["competitors"].append({"name": "Imaginary ERP", "domain": "imaginary-erp.com", "x": "left", "y": "top",
                                           "rationale": "from memory", "evidence_ids": []})  # must be dropped
        return d

    def a_rewrite_field(self, user):
        return {"candidates": ["Apparel brands are moving off legacy ERPs", "Brands are leaving legacy ERPs", "Brands leave old ERPs"]}

    def a_short_name(self, user):
        opts = _block(user, "options")
        return {"short_name": opts[0]}

    def a_placement_audit(self, user):
        return {"verdicts": [{"item_id": it["item_id"], "verdict": "supports", "quote": it["quotes"][0]["quote"],
                              "note": "the quote states it"} for it in _block(user, "items") or []]}

    def a_vision_qa(self, user):
        return {"passed": True, "defects": [], "summary": "looks right"}
