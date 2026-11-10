# BUILD PROMPT — Recur "build a deck for X" pipeline

> **How to use this file.** Open this repository in your IDE and give this entire file to your coding
> agent (Claude Code, Cursor, etc.) as its task, e.g. *"Read BUILD_PROMPT.md and execute it milestone by
> milestone. Do not skip the verification steps."* Everything the agent needs is in this file or in the
> repo. Facts marked **[VERIFIED]** were tested in this repo or checked against official docs on
> 2026-11-07; do not "fix" them from memory.

---

## 0. Your role and the rules of engagement

You are a senior engineer building a production-quality, unattended pipeline. You will:

1. **Keep the verified core.** `recur_deck/` already contains a tested deterministic core (template editing,
   exact text measurement, logo whitening, market-map layout, rendering, deterministic QA). It passes 37 tests
   (`pytest -q`), including edge cases found by an independent review (protected brand names, newlines,
   SVG variants, dark-theme logos, slug collisions, tiny logos, unicode names). Build the research + AI layers **around** it. Do not rewrite these modules; extend
   them only when a milestone says so, and keep all existing tests passing.
2. **Work milestone by milestone (§14).** At the end of each milestone, run its "definition of done"
   checks and show the output before moving on.
3. **Never guess an API.** Use the exact contracts in §7. If a call fails in a way that contradicts them,
   read the official docs URL given there and adapt — then note the change in `docs/DECISIONS.md`.
4. **Never invent facts.** Every company, competitor and claim in a deck must trace to fetched evidence
   (§6.3). When evidence is missing, the code drops the claim or the competitor; it never fills the gap.
5. **Secrets.** Keys come from `.env` only. Never print, log or write keys to run folders.

---

## 1. The assignment (from Recur — this is the acceptance spec)

> One part of our sourcing strategy is a physical mailing campaign. Attached is an example we made for a
> company called Ai2000. We built it entirely by AI in one go — so to invoke it, all you have to say is
> "build a deck for Ai2000".
> Build a workflow that can make this deck for any company in one go. The output should have these three
> pages, with no human intervention, right on the first try for any company:
> 1. **Cover page:** the Recur logo | the company's logo on the Recur background
> 2. **Thesis page ("What we see in [Company]"):** three sections on our investment case. Fully AI
>    generated, but make sense and sound like a person wrote it
> 3. **Market map ("The opportunity for [Company]"):** real competitors and a compelling, human-sounding
>    case on the market
> All other pages (4 to 10) can be copied straight from the example. Your version won't match ours word for
> word. What we care about is a directionally similar, usable deck. Send us your output and however you've
> packaged the workflow so we can try it ourselves. I'll test it on Ai2000 and another company.

### 1.1 Acceptance criteria (what "done" means)

| # | Criterion | How it is verified |
|---|---|---|
| A1 | One command / one sentence produces the deck: `recur-deck "Ai2000"` or, in Claude Code, "build a deck for Ai2000" | Manual run |
| A2 | Output = 10-slide `.pptx` + print-ready `.pdf` + `sources.md` + `qa_report.json` | File check |
| A3 | Slides 4–10 are byte-identical to the template | `qa.check_integrity` |
| A4 | Right company (rebrands handled: Ai2000 = formerly A2000, a2000software.com) | Entity stage + report |
| A5 | 5–8 **real, currently operating** competitors, each with ≥2 independent evidence sources | Competitor stage + `sources.md` |
| A6 | Copy reads human, follows the style guide, passes lint, fits every text box (measured, not estimated) | `qa.check_text` = 0 errors |
| A7 | Every logo is the correct company's, white on transparent, legible, consistently sized; else a clean typeset wordmark | Logo verification + vision QA |
| A8 | Rendered slides pass the vision QA (no major defects) within ≤3 fix rounds | `qa_report.json` |
| A9 | Never crashes for an unknown/obscure company: degrades gracefully, reports what it fell back on | Test matrix §12.3 |
| A10 | Recur can install and run it from the README in < 10 minutes | Fresh-machine test |

---

## 2. Context you must understand

### 2.1 Who Recur is (for tone and for thesis point 3)
Recur Software (Chicago/NYC) acquires and grows mission-critical **vertical software** businesses and holds
them long term ("we buy and hold forever, with no fund clock and no exit plan"). Founders previously at
Accel, General Atlantic and others. The deck's fixed slides state their levers — reuse these, do not invent
new ones:

- **AI-native GTM** — "Recur OS → 40% of August new logos and +50% demos YoY" (statii)
- **AI rebuild** — "Recur FDE rebuilt the product in less than three months" (cognidox)
- **AI modernization** — "Rebuilt legacy form layer in C#, >10x faster and cheaper" (Keystone)
- **Built the bench** — "Recur brought in a new CEO, Head of Sales, VP Eng" (collect!)
- Values: Stewardship, Growth, Partnership, Ambition, Speed. Portfolio includes PCRecruiter, Cognidox,
  FLAGS Software, Statii, Innova Zones and others.

The deck is a **first touch mailed to a founder**. Tone: respectful, specific, confident, warm. It should
read like an investor who did their homework, never like marketing copy, never negative about the founder's
company.

### 2.2 The reference deck (gold standard — `assets/reference_Ai2000.pdf`)

**Slide 2 — "What we see in Ai2000"** (labels are fixed vector art; only headline + subline change)

| Label | Headline (15pt) | Subline (11pt) |
|---|---|---|
| 01 WHY WE'RE HERE | Apparel brands are trading legacy ERPs for systems with AI | Many run generic ERPs or ops tools that keep financials in another system |
| 02 WHY WE'RE EXCITED | Ai2000 is set to win as apparel brands replace legacy ERPs | Brands run retailer orders and their books on Ai2000, so switching is hard |
| 03 HOW WE CAN HELP | Help Ai2000 grow with outbound sales and AI | We add outbound sales to what already works and build AI into the product |

**Slide 3 — "The opportunity for Ai2000"**
- Subtitle: *Apparel and home goods brands need one system for product, orders, retailers and books*
- Y axis "Customer size": top **Large brands**, bottom **Small & / mid brands**
- X axis "Product scope": left **Ops only**, right **Ops + financials**
- Logos: top-left BlueCherry · top-right Aptean + **Ai2000 (glow)** · bottom-left AIMS360, ApparelMagic,
  Uphance · bottom-right NetSuite, FDM4
- OUR TAKE: **Ai2000 runs product, retailer orders and financials for big brands in one system** ·
  *Small-brand tools lack the depth big retailer programs need* · *Ops-only suites push the books into a
  second system*

What makes it good: one idea per line, concrete nouns from the industry (retailer orders, books, EDI),
causal sublines ("so switching is hard"), the market map's axes encode the thesis, and the "our take" points
explain why each *group* of competitors falls short — never naming or disparaging a competitor.

### 2.3 Verified facts that will bite you if ignored  **[VERIFIED]**

1. **Rebrands are common.** Ai2000 is the renamed **A2000** from **GCS Software** (`a2000software.com`).
   Capterra/G2/GetApp still list "A2000". A bare search for "Ai2000" returns unrelated AI companies.
2. **Template text boxes do not wrap or shrink** (`wrap="none"`, `noAutofit`). Overlong text silently runs
   off the slide. Fit is checked by measuring with the real font (`recur_deck/fonts.py`: Pillow BASIC layout,
   identical on macOS/Linux, matches the reference PDF within 0.2% on the reference lines; tests allow 1%).
3. **The template font is "Noto Sans Arabic (Light)"** (it contains Latin glyphs). Bundled static instances
   in `assets/fonts/` reproduce it exactly. Line height = 1.2 × pt × spacing (not the font's hhea, which is 2.11×).
4. **The template only subset-embeds fonts.** New characters may be missing on machines without the font →
   always ship the PDF (rendered with the bundled fonts) alongside the PPTX.
5. **LibreOffice cannot render the template's elliptical glow** (path gradient → rectangular seams). The core
   replaces it with an identical PNG glow (`build.make_glow_png`). The vision QA must not flag glow seams on
   *template* renders; on our output they no longer exist.
6. **Slide 3 of the template still packages leftover logos from another deck** (Surgence, Palantir) via
   orphaned relationships. The core strips them (`build.drop_orphan_media`); QA verifies by hash.
7. **Every logo in the reference is a flat white silhouette on transparency.** Recur's own cover used
   PowerPoint's brightness trick; that turns JPG backgrounds into white rectangles — never use it. The core
   bakes white PNGs (`logos.process_logo`).
8. **Exa `/findSimilar` is deprecated (2026).** Use `/search` with a description of the target and
   `excludeDomains=[target_domain]`. `type` values: `auto | lite | deep-lite | deep`; `neural`/`keyword` are
   not valid. Contents must be nested under `contents`.
9. **Brandfetch Logo CDN (`cdn.brandfetch.io`) forbids programmatic download/caching.** Use the **Brand API**
   (`api.brandfetch.io/v2/brands/...`, Bearer key) and its `logos[].formats[].src`. Free tier = **100
   lifetime requests**, and a 404 still costs a credit → cache by domain on disk.
10. **Claude Opus 5.5 / Sonnet 5.5:** forced `tool_choice` (`any`/`tool`) returns 400; no `temperature`/
    `top_p`; no assistant prefill on Opus 5.5. Use **structured outputs** (GA). Verified in the installed SDK
    (`anthropic==2.6.0`): `client.messages.parse(model=..., output_format=PydanticModel, ...)` transforms the
    schema itself and returns `.parsed_output`. Raw fallback: `output_config={"format": {"type": "json_schema",
    "schema": anthropic.transform_schema(PydanticModel)}}` — never the raw `model_json_schema()` (the API rejects
    some keywords). Thinking is adaptive; find the `type=="text"` block, never assume `content[0]`.

---

## 3. Architecture

```
recur-deck "Ai2000" [--domain a2000software.com] [--hint "apparel ERP"]
│
├─ S1 ENTITY RESOLUTION ──────────────── who exactly is this?
│    Brandfetch Brand Search (name→domains) + Exa company search + Firecrawl search ("<name> software",
│    "<name> formerly", "<name> <hint>") → candidate domains → Firecrawl scrape each homepage
│    → Claude judge (scores identity match, finds aliases/rebrands) → GATE (confidence ≥ 0.75, margin ≥ 0.2)
│    out: entity.json {display_name, legal_name, domain, aliases[], vertical, one_liner, confidence}
│
├─ S2 TARGET PROFILE ─────────────────── what do they do, for whom?
│    Firecrawl /v2/map (search: product|features|solutions|about|customers|pricing|industries)
│    → scrape ≤8 best pages (markdown) → Claude extracts facts with quotes → evidence.jsonl
│    out: profile.json {modules[], customer_segments[], customer_size_signals[], deployment, integrations[],
│         differentiators[], recent_news[], gaps_or_opportunities[]} each fact → evidence ids
│
├─ S3 COMPETITOR DISCOVERY ───────────── who do they actually compete with?
│    Channels (run in parallel):
│      C1 "vs/alternative" pages:  Firecrawl search  "<name OR alias> vs", "<alias> alternatives"
│      C2 directories:             Exa search include_domains [capterra, getapp, softwareadvice, g2,
│                                  softwareconnect, trustradius] "<alias> alternatives"
│      C3 similar companies:       Exa search category=company, query=<one-liner of target>,
│                                  excludeDomains=[target domain]
│      C4 category roundups:       Exa/Firecrawl search "best <vertical> software", "<vertical> ERP vendors"
│    → normalise to {name, domain}, merge by registrable domain → candidates.json (with channel hits)
│    → keep candidates with ≥2 channels (or 1 channel + explicit head-to-head comparison page)
│    → Firecrawl scrape each survivor's homepage → Claude verifier: same vertical? still operating?
│      acquired/renamed? segment & scope evidence?  → competitors.json (verified, with evidence ids)
│
├─ S4 LOGOS ───────────────────────────── for target + each verified competitor (parallel)
│    sources in order: Brandfetch Brand API (logo type, svg>png>webp) → site scrape (JSON-LD logo,
│    header/nav inline SVG, header <img> 'logo') → og:image (only if verified) → typeset fallback
│    each candidate → logos.process_logo (white silhouette + metrics) → Claude vision logo check
│    out: logos/<slug>.png + logo_metrics.json
│
├─ S5 STRATEGY + WRITING ─────────────── Claude (Opus) with structured output = models.DeckContent
│    inputs: entity, profile, verified competitors (+evidence), Recur levers, gold example, style guide,
│    measured text budgets. Picks axes, places competitors (≤3 per cell incl. target, ≥3 quadrants used),
│    writes thesis + market copy, cites evidence ids.
│
├─ S6 BUILD ───────────────────────────── recur_deck.build.build_deck (verified, deterministic)
│
├─ S7 QA LOOP (max 3 rounds) ──────────── Layer A: recur_deck.qa.run_all (exact, instant)
│    Layer B: render (LibreOffice→PDF→PNG) → annotated element map → Claude vision verdict
│    → route each defect through the fix menu (§9) → rebuild → recheck
│
└─ S8 DELIVER ─────────────────────────── runs/<slug>-<ts>/ Recur_x_<Company>.pptx, .pdf, sources.md,
                                          qa_report.json, cost.json, run.log  (+ copy to ./output/)
```

**Design principles**
- **AI decides, code executes.** The LLM chooses words, axes and placements; code does geometry, fitting,
  file editing and checking. No LLM ever writes PPTX XML.
- **Evidence first.** Every claim and every competitor carries `evidence_ids` pointing to stored quotes+URLs.
- **Graceful degradation.** Every external service has a fallback (§10). A missing key or a 5xx never kills
  the run; it lowers confidence and is reported.
- **Idempotent, cached runs.** Every HTTP response is cached under `runs/_cache/` keyed by request hash, so
  re-runs (and the fix loop) cost nothing and are reproducible.

---

## 4. Repository layout (target)

```
.
├── BUILD_PROMPT.md                 # this file
├── README.md                       # for Recur: install + run (write in M7)
├── pyproject.toml / requirements.txt
├── .env.example
├── assets/
│   ├── recur_template.pptx         # original deck (do not edit)
│   ├── reference_Ai2000.pdf        # gold example
│   └── fonts/                      # Noto Sans Arabic Light/Regular/Bold, Manrope Bold (+ OFL licences)
├── recur_deck/
│   ├── spec.py        [VERIFIED]   # template geometry, shape ids, budgets — single source of truth
│   ├── fonts.py       [VERIFIED]   # exact width / wrap / line height
│   ├── logos.py       [VERIFIED]   # load → whiten → trim → metrics; typeset fallback
│   ├── layout.py      [VERIFIED]   # cover box, market-map slots, glow box
│   ├── models.py      [VERIFIED]   # DeckContent schema (LLM contract)
│   ├── build.py       [VERIFIED]   # fills slides 1–3 in place
│   ├── render.py      [VERIFIED]   # pptx→pdf→png, annotated element map
│   ├── qa.py          [VERIFIED]   # Layer A checks + fix codes
│   ├── offline.py     [VERIFIED]   # fixture runner (no network, no AI)
│   ├── config.py                   # NEW: settings from env (models, keys, limits, modes)
│   ├── cache.py                    # NEW: disk cache + cost ledger
│   ├── evidence.py                 # NEW: Evidence store (jsonl) + ids
│   ├── clients/                    # NEW
│   │   ├── http.py                 #   shared httpx client, retries, timeouts, UA
│   │   ├── exa.py  firecrawl.py  brandfetch.py  claude.py
│   ├── stages/                     # NEW
│   │   ├── entity.py  profile.py  competitors.py  logo_sources.py  write.py  vision_qa.py
│   ├── prompts/                    # NEW: one .md per LLM call (copy verbatim from §8)
│   ├── pipeline.py                 # NEW: orchestrator + fix loop
│   └── cli.py                      # NEW: `recur-deck` entry point
├── .claude/skills/build-deck/SKILL.md   # NEW (M7): "build a deck for X"
├── fixtures/  (ai2000, pcrecruiter, stress)   # offline cases [VERIFIED]
├── tests/     (test_core.py [VERIFIED], make_fixtures.py, + new tests)
└── runs/      (git-ignored) per-run folders + _cache/
```

---

## 5. Environment

- Python **3.11+** (the `anthropic` SDK needs ≥3.10). Use `uv` or `venv`.
- macOS: `brew install --cask libreoffice` (binary: `/Applications/LibreOffice.app/Contents/MacOS/soffice`;
  `render.soffice_bin()` finds it, or set `SOFFICE=`). Linux: `apt install libreoffice-impress`.
- Fonts: `render.install_fonts()` copies `assets/fonts/*.ttf` to `~/Library/Fonts` (macOS) or `~/.fonts`.
  Call it at the start of every run (idempotent).
- Python deps (already used by the core): `python-pptx==1.0.2`, `pillow`, `numpy`, `fonttools`, `resvg-py`,
  `pymupdf`, `pydantic>=2.7`, `pytest`. Add: `anthropic>=2.6`, `httpx>=0.27`, `exa-py>=2.28` (optional —
  raw HTTP is fine), `beautifulsoup4`, `lxml`, `python-dotenv`, `tenacity`, `tldextract`, `rich`.
- `.env` (create `.env.example` with these names, empty values):
  ```
  ANTHROPIC_API_KEY=
  EXA_API_KEY=
  FIRECRAWL_API_KEY=
  BRANDFETCH_API_KEY=          # Brand API (Bearer)
  BRANDFETCH_CLIENT_ID=        # Brand Search (query param c=)
  RECUR_MODEL_WRITER=claude-opus-5-5
  RECUR_MODEL_JUDGE=claude-sonnet-5-5
  RECUR_MODEL_FAST=claude-haiku-5-5
  RECUR_MODEL_VISION=claude-sonnet-5-5
  BRANDFETCH_MODE=first        # first | fallback | off   (free tier = 100 lifetime calls)
  RECUR_MAX_QA_ROUNDS=3
  ```

---

## 6. Data contracts

### 6.1 Run folder (`runs/<slug>-<YYYYmmdd-HHMMSS>/`)
```
entity.json  profile.json  evidence.jsonl  candidates.json  competitors.json
logos/<slug>.png  logos/_raw/<slug>/<n>_<source>.<ext>  logo_metrics.json
content.json  content.round<k>.json
Recur_x_<Company>.pptx  Recur_x_<Company>.pdf  png/slide{1,2,3}.png  png/slide{n}_annotated.png
qa_report.json  sources.md  cost.json  run.log
```

### 6.2 Entity (`entity.json`)
```json
{"display_name": "Ai2000", "legal_name": "GCS Software, LLC", "domain": "a2000software.com",
 "aliases": ["A2000"], "vertical": "ERP for apparel, footwear and home goods brands",
 "one_liner": "ERP covering PLM, orders, EDI, warehouse and financials for apparel brands",
 "confidence": 0.92, "alternatives": [{"domain": "...", "score": 0.1, "why_rejected": "..."}],
 "evidence_ids": ["e3", "e7"]}
```
`display_name` = how the company writes its own brand (from its site/logo), used verbatim in titles.

### 6.3 Evidence (`evidence.jsonl`, one per line) — the backbone of trust
```json
{"id": "e12", "url": "https://...", "title": "...", "fetched_at": "2026-11-07T18:03:00Z",
 "source": "firecrawl|exa|brandfetch", "quote": "exact sentence copied from the page (≤300 chars)",
 "about": "target|<competitor domain>", "kind": "identity|product|segment|scope|news|comparison"}
```
Rules: quotes must be **verbatim substrings** of the fetched markdown (validate in code with a normalised
`in` check; drop facts whose quote is not found). IDs are stable within a run.

### 6.4 Verified competitor (`competitors.json`)
```json
{"name": "AIMS360", "domain": "aims360.com", "channels": ["vs_page", "directory", "exa_similar"],
 "same_vertical": true, "operating": true, "acquired_or_renamed": null,
 "segment": "small-mid brands ($1M–$500M)", "scope": "ops ERP, syncs to QuickBooks/Sage/NetSuite for books",
 "evidence_ids": ["e21", "e22"], "relevance": 0.9}
```

### 6.5 Deck content — `recur_deck/models.py::DeckContent` **[VERIFIED schema]**
Pass `DeckContent` as the structured-output model. Pydantic validators enforce: 5–8 competitors (override
with `DeckContent.model_validate(data, context={"min_competitors": 3})` for thin markets), unique names
(after slugging) and unique domains, no competitor equal to the target (name, alias or domain), ≤3 logos per
quadrant including the target, ≥3 quadrants used, exactly 3 thesis points, exactly 2 "our take" points, and
no line breaks anywhere except y-axis labels (max 2 lines). Evidence fields: `thesis[i].evidence_ids`,
`market.subtitle_evidence_ids`, `market.our_take.evidence_ids`, `competitors[i].evidence_ids`.
Text-fit and style are enforced afterwards by `qa.check_text` (whole deck) and `qa.check_field` (one field).
Deliverable names come from `logos.file_stem(display_name)` → `Recur_x_Ai2000.pptx`.

---

## 7. External services — exact contracts **[VERIFIED against official docs, 2026-11-07]**

Implement all clients on one shared `httpx.Client` (timeout 30s connect/60s read, `User-Agent:
RecurDeckBuilder/1.0`), with `tenacity` retries (3 attempts, exponential backoff 1–8s, honour `Retry-After`)
on 429/5xx/timeouts only. Never retry 400/401/402/403. Log status, latency and cost per call to `cost.json`.
Cache every successful response on disk keyed by `sha256(method+url+body)`.

### 7.1 Exa — https://exa.ai/docs/reference/search.md
- `POST https://api.exa.ai/search`, header `x-api-key: $EXA_API_KEY`.
- Body:
  ```json
  {"query": "...", "type": "auto", "category": "company", "numResults": 10,
   "includeDomains": [], "excludeDomains": ["a2000software.com"],
   "contents": {"text": {"maxCharacters": 4000}, "highlights": true,
                "summary": {"query": "What does this company sell and to whom?"}}}
  ```
  - `category` ∈ `company | people | news | publication | personal site | financial report`. With
    `category:"company"` do **not** send date filters (400).
  - Omit `category` for directory/roundup searches; use `includeDomains` instead.
- Response: `results[]` → `url`, `title`, `text?`, `highlights?` (list[str]), `summary?`; `costDollars.total`.
- Errors: body `{requestId, error, tag}`; 402 = out of credits → mark Exa unavailable for the run.
- Limits: 10 QPS. Price ≈ $7 / 1k `auto` searches (contents for first 10 results included).
- **Do not use** `/findSimilar` (deprecated) or `/research` (retired).

### 7.2 Firecrawl v2 — https://docs.firecrawl.dev/api-reference/endpoint/scrape.md
Use raw HTTP (field names below are camelCase as in the API reference). Header
`Authorization: Bearer $FIRECRAWL_API_KEY`.
- **Scrape** `POST https://api.firecrawl.dev/v2/scrape`
  ```json
  {"url": "https://a2000software.com", "formats": ["markdown", "rawHtml", "links"],
   "onlyMainContent": true, "waitFor": 1500, "timeout": 60000}
  ```
  Read `data.markdown`, `data.rawHtml`, `data.links` (list[str]), `data.metadata.title`,
  `data.metadata.description`, `data.metadata.url` (final URL after redirects), `data.metadata.statusCode`,
  and `data.metadata.get("ogImage") or data.metadata.get("og:image")`.
  For **logo scraping** send `"onlyMainContent": false` (headers/nav are stripped otherwise).
  Cost: 1 credit/page. Timeout max 300000 ms.
- **Map** `POST https://api.firecrawl.dev/v2/map`
  `{"url": "...", "search": "product", "limit": 200, "sitemap": "include", "includeSubdomains": false,
  "ignoreQueryParameters": true}` → `links[]` are **objects** `{url, title?, description?}`. Always set
  `limit` explicitly. 1 credit/call.
- **Search** `POST https://api.firecrawl.dev/v2/search`
  `{"query": "\"A2000\" vs", "limit": 10, "sources": [{"type": "web"}], "country": "US"}` →
  `data.web[]` `{url, title, description}`. 2 credits per 10 results (+scrape cost if `scrapeOptions` set).
- Errors: 402 no credits, 429 rate limit (Free plan: 10 req/min — throttle accordingly), 500.

### 7.3 Brandfetch — https://docs.brandfetch.com/reference/brand-api.md
- **Brand Search (name → domains):** `GET https://api.brandfetch.io/v2/search/{urlencoded name}?c={CLIENT_ID}`
  (client id as query param, not the API key). Response: array of
  `{name|null, domain, claimed: bool, icon|null, brandId}`; at most 5 results. Use only as a *candidate
  generator* for S1; do not persist icon URLs (they expire in 24h).
- **Brand API (logos):** `GET https://api.brandfetch.io/v2/brands/domain/{domain}` with
  `Authorization: Bearer $BRANDFETCH_API_KEY`.
  - `logos[]`: `type` ∈ `logo | symbol | icon | other`; `theme` ∈ `light | dark | null` where
    **`light` = light-coloured logo for DARK backgrounds**; `tags[]` (skip `placeholder`);
    `formats[]`: `{src, format: svg|webp|png|jpeg, width?, height?, size, background: "transparent"|null}`.
  - Selection: `type=="logo"` → prefer `theme=="light"`, then `"dark"`, then null → format
    `svg > png > webp > jpeg` → largest. Ignore `symbol`/`icon` for wordmark slots.
  - **Call `logos.process_logo(data, designed_for_dark=(theme == "light"))`.** Light-theme logos are white
    wordmarks; without the flag the whitening step treats their white parts as knockouts and erases them.
  - Errors: 404 = unknown brand (**still billed**), 403 `no_api_credits`, 429 `quota_exceeded`.
  - Quota: free = 100 **lifetime** requests. Cache per domain forever in `runs/_cache/brandfetch/`.
  - Download `formats[].src` with httpx (≤5 MB, check content-type), store under `logos/_raw/`.
- **Never** call `cdn.brandfetch.io` from code (terms: hotlink only; programmatic download gets blocked).

### 7.4 Anthropic — https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- SDK `anthropic>=2.6`. Models from config (§5). Defaults: writer `claude-opus-5-5`, judge/vision
  `claude-sonnet-5-5`, fast classification `claude-haiku-5-5`.
- **Structured output** (preferred, verified in `anthropic==2.6.0`): `client.messages.parse(model=...,
  max_tokens=..., system=..., messages=[...], output_format=SomePydanticModel)` → `resp.parsed_output`
  (the SDK runs `transform_schema` for you). Fallback: `messages.create(..., output_config={"format":
  {"type": "json_schema", "schema": anthropic.transform_schema(Model)}})`, take the block with
  `type=="text"`, then `Model.model_validate_json(text)`. Always re-validate with Pydantic (the API schema is
  looser than our validators). On `ValidationError`: one retry that includes the error text; then fail the
  stage with a clear message.
- **Do not** send `temperature`, `top_p`, `top_k`, assistant prefill, or forced `tool_choice` to 5.5 models.
- Effort: `output_config={"effort": "medium"}` for judges, `"high"` for the writer.
- **Images:** `{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}`.
  Downscale slides to ≤2576 px long edge (≤10 MB each).
- Prompt caching: put the long, static parts (style guide, gold example, Recur levers) in the system prompt
  with `cache_control: {"type": "ephemeral"}` — the fix loop re-calls the writer.

### 7.5 LibreOffice / PyMuPDF
Already implemented in `render.py` [VERIFIED]: isolated `-env:UserInstallation` per call, `--norestore`,
subprocess timeout, existence check of the PDF (LibreOffice can exit 0 without output when another instance
is open), PyMuPDF `page.get_pixmap(dpi=200)`.

---

## 8. LLM calls — full prompts (store each in `recur_deck/prompts/<name>.md`)

Every call returns a Pydantic model (structured output). Below: purpose · model · input · output model ·
system prompt. Put dynamic data in the user message as tagged blocks (`<entity>…</entity>` etc.).

### 8.1 `entity_judge` · judge model
Output model:
```python
class EntityCandidateScore(BaseModel):
    domain: str; score: float  # 0-1
    display_name: str; legal_name: str | None; aliases: list[str]
    vertical: str; one_liner: str; evidence_quotes: list[str]; why: str
class EntityVerdict(BaseModel):
    ranked: list[EntityCandidateScore]
    rebrand_detected: bool; notes: str
```
System prompt:
```
You identify which real company a user means by a short name. You will receive the name the user typed,
an optional hint, and several candidate websites (homepage markdown, title, description) plus search
snippets. Score each candidate 0-1 for "this is the software company the user means".

Rules:
- A Recur target is a B2B or vertical software company. Prefer candidates whose site sells software.
- Companies rebrand. If a candidate's site or a directory snippet says "formerly X", "now X", "X is now Y",
  or the name differs only by a prefix/suffix (e.g. "Ai2000" vs "A2000"), treat them as the same company
  and list every name in aliases. Set display_name to how the company currently writes its brand.
- Quote the exact sentences that support your score in evidence_quotes (copied verbatim from the input).
- If two candidates are plausible, keep both scores honest; do not force a winner.
- Never use knowledge that is not in the input to raise a score.
```
Gate in code: accept top if `score ≥ 0.75` and `top - second ≥ 0.2`. Otherwise:
- interactive terminal (default) → show the top 3 and ask;
- `--non-interactive` → print the top 3 as JSON and **exit 3** (the Claude Code skill uses this to ask the user);
- `--non-interactive --accept-low-confidence` → proceed with the top candidate and set
  `low_confidence_identity=true` in the report (for batch runs).

### 8.2 `profile_extract` · judge model
Output: `Profile` (fields in §3 S2), each list item `{text, evidence_quote}`.
System prompt:
```
Extract facts about the company from the pages provided. Only state what the pages say. For every fact,
copy the exact supporting sentence into evidence_quote. Prefer specifics: module names, customer types,
named retailers/partners, deployment model, integrations, years in business, customer size signals
(revenue bands, employee counts, "enterprise", "growing brands", pricing tiers). If the pages do not say
something, leave it out. Do not summarise marketing adjectives ("powerful", "leading").
```

### 8.3 `competitor_verify` · judge model (one call per candidate, run in parallel, max 6 concurrent)
Output:
```python
class CompetitorCheck(BaseModel):
    is_software_vendor: bool; same_vertical: bool; operating: bool
    acquired_or_renamed: str | None; display_name: str
    segment: str; scope: str; head_to_head_with_target: bool
    evidence_quotes: list[str]; relevance: float  # 0-1 how directly it competes for the same buyer
```
System prompt:
```
Decide whether a candidate company is a real, currently operating competitor to the target company for
the same buyer. You get the target's profile and the candidate's homepage plus any snippets that mention
both. same_vertical means the candidate sells to the same kind of customer for the same job (an apparel
ERP vs. a generic accounting tool is NOT the same vertical unless the snippets show buyers comparing them
directly). Mark operating=false if the site is dead, parked, or says the product is discontinued. If the
site says it was acquired or renamed, give the new name. segment = who it sells to (size/type), scope = what
the product covers, both in ≤12 words, supported by evidence_quotes copied verbatim from the input.
Be strict: when in doubt, lower relevance.
```
Selection in code: keep `is_software_vendor and same_vertical and operating and relevance ≥ 0.6` and
(channels ≥ 2 or head_to_head_with_target). Rank by relevance, then channel count. Keep top 8. If < 5 remain,
relax to relevance ≥ 0.45 with ≥1 channel and log `thin_market=true`. Never add companies from model memory.

### 8.4 `logo_check` · vision model (batch: all candidate logos for one company in one call)
Input: for each candidate, the original image (as downloaded) and the white version on navy (#0B1630) side by
side, plus the company name and domain.
Output:
```python
class LogoVerdict(BaseModel):
    candidate_id: str; is_logo: bool; text_read: str; matches_company: bool
    kind: Literal["wordmark", "lockup", "symbol_only", "banner_or_photo", "generic_icon", "other"]
    legible_on_navy: bool; confidence: float
class LogoVerdicts(BaseModel):
    verdicts: list[LogoVerdict]
```
System prompt:
```
You check candidate logo images for a company. For each candidate: read any text in the logo exactly
(text_read); say whether it is that company's logo (matches_company); classify it; and say whether the
white version is still clearly legible on the navy background (parts can vanish when a multi-colour logo is
turned white). A banner with a tagline, a product screenshot, a photo, a generic icon, or another company's
logo is not acceptable. Be conservative.
```
Accept the first candidate (in source order) with `is_logo and matches_company and kind in {wordmark,
lockup} and legible_on_navy and confidence ≥ 0.7`; additionally `fuzzy_ratio(text_read, name or alias) ≥ 0.6`
when text_read is non-empty, where
`fuzzy_ratio(a, b) = difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()` and `norm` lowercases and strips
everything except `[a-z0-9]` (take the max over the display name and each alias). Otherwise typeset fallback
(`logos.typeset_wordmark`, which wraps long names onto two lines automatically).

### 8.5 `write_deck` · writer model (Opus) · output `DeckContent`
System prompt (static → cache it):
```
You are a partner at Recur Software writing three slides of a printed letter-deck that will be mailed to
the founder of a software company. Recur acquires and grows vertical software businesses and holds them for
the long term. The founder has never heard from us. The slides must show that we understand their market
and their company, in plain words, and make them want to talk.

THE THREE SLIDES
1) Thesis — "What we see in <Company>": exactly three points, each a headline + one subline.
   [0] WHY WE'RE HERE — the shift happening in their customers' market (not about Recur, not about the company).
   [1] WHY WE'RE EXCITED — why THIS company is positioned to win that shift; the subline gives the concrete
       mechanism (a product fact from the evidence), ideally with a "so…" consequence.
   [2] HOW WE CAN HELP — start with "Help <Company> …". Choose the one or two Recur levers that fit a real
       gap in the evidence: outbound/AI-native sales (Recur OS), AI built into the product, modernising a
       legacy stack, building the leadership bench. Phrase as adding to what already works.
2) Market map — "The opportunity for <Company>":
   - subtitle: the customer need that defines this market, as one plain sentence.
   - two axes that are real buying criteria in this vertical (typically y = customer size or complexity,
     x = product scope or depth). Each axis has a short title and two half-labels. The target must sit in
     the high/high (top-right) cell AND the evidence must support that.
   - place every competitor you were given (choose 5-8; drop the weakest if more) into a cell using its
     evidence. Max 3 logos per cell including the target; use at least 3 of the 4 cells.
   - our_take: a bold headline that starts with the company name and states what it does that the others
     don't, plus two short points that explain why each GROUP of competitors falls short (name groups like
     "Small-brand tools", "Ops-only suites", never individual companies).
3) Cover — no text from you.

STYLE (this is how Recur writes — follow it exactly)
- Plain declarative sentences. One idea per line. No full stops at the end of slide lines.
- Concrete nouns from the industry (e.g. "retailer orders", "the books", "EDI", "chargebacks", "placements",
  "candidates") instead of abstractions ("operations", "solutions", "workflows", "processes").
- Sentence case. Numbers as digits. "and" instead of "&" except inside axis labels.
- {STYLE_RULES}   ← insert `qa.STYLE_RULES_FOR_PROMPT` here at runtime (string formatting), so the
  prompt and the checker always use the same list. Company and competitor names are exempt.
- No flattery, no hype, nothing negative about the founder's company, no claims about revenue, customer
  counts, growth rates or funding unless an evidence quote states them.
- Every factual claim must be supported by an evidence id you were given. Put the ids in evidence_ids.

LENGTH BUDGETS (hard limits are measured in the real font after you answer; aim for the targets)
- thesis headline: about 6-11 words, target ≤ 5.7 in at 15pt (≈ 60 characters); hard limit 6.2 in
- thesis subline: about 10-14 words, target ≤ 5.6 in at 11pt (≈ 78 characters); hard limit 6.2 in
- market subtitle: ≤ 15 words (≤ 8.5 in at 12pt, ≈ 95 characters)
- axis title: 1-3 words; x half-labels ≤ 20 characters; y half-labels ≤ 2 short lines of ≤ 12 characters,
  separated by "\n" (e.g. "Small &\nmid brands"). No line breaks anywhere else.
- our_take headline: ≤ 14 words (wraps to ≤ 4 lines in a 1.86 in column); each point ≤ 10 words
The code measures every line after you answer and will send back anything that does not fit.

GOLD EXAMPLE (Recur's own deck for Ai2000 — match its register, not its words)
Thesis
[0] Apparel brands are trading legacy ERPs for systems with AI
    Many run generic ERPs or ops tools that keep financials in another system
[1] Ai2000 is set to win as apparel brands replace legacy ERPs
    Brands run retailer orders and their books on Ai2000, so switching is hard
[2] Help Ai2000 grow with outbound sales and AI
    We add outbound sales to what already works and build AI into the product
Market map
subtitle: Apparel and home goods brands need one system for product, orders, retailers and books
y axis "Customer size": high "Large brands", low "Small &\nmid brands"
x axis "Product scope": low "Ops only", high "Ops + financials"
top-left BlueCherry | top-right Aptean, Ai2000 | bottom-left AIMS360, ApparelMagic, Uphance |
bottom-right NetSuite, FDM4
our_take: "Ai2000 runs product, retailer orders and financials for big brands in one system"
  - "Small-brand tools lack the depth big retailer programs need"
  - "Ops-only suites push the books into a second system"
```
User message: `<entity>`, `<profile>`, `<competitors>` (verified, with evidence), `<evidence>` (id, quote,
url — only items referenced by the above), `<recur_levers>` (§2.1 list).

### 8.6 `rewrite_field` · writer model
Triggered by `qa` issues with fix `rewrite_shorter` or `rewrite_copy`. Output:
`class Rewrites(BaseModel): candidates: list[str]  # exactly 3, best first`.
System prompt:
```
Rewrite one line of a slide so it fits and follows the style rules. Keep the meaning and the facts; do
not add new claims. You get the field name, the current text, why it failed (e.g. "6.71 in wide; limit
6.20 in", or "stock AI word: leverage"), the style rules and the neighbouring lines for context. Return 3
alternatives, shortest last. Each must be a complete, natural sentence fragment like the gold example.
```
Code checks each candidate with `qa.check_field(field_path, candidate, content, extra_protected)` (errors only,
in the context of the whole deck; handles every field incl. "our take" lines and 2-line y labels) and takes the
first that returns `[]`. If none passes, call once more with the new failure reasons; then fall back to the
candidate with the fewest errors and flag it.

### 8.7 `vision_qa` · vision model
Input: `png/slide1-3.png` (≤2576 px), the annotated versions (`render.annotate` draws every manifest element;
annotate slides 1, 2 and 3), the element list from the build manifest (`<pptx>.manifest.json`: logos with
`id="logo:<name>"`, the target, the glow, and text fields with `kind="text"` and `field="thesis[0]"`,
`"market.subtitle"`, `"market.our_take"`, axis labels… — so a text defect maps straight to the field to rewrite), the **template baseline renders** (`assets/baseline/slide{1,2,3}.png` — already generated
from the untouched template [VERIFIED]), and the content JSON.
Output:
```python
class Defect(BaseModel):
    slide: int; element_id: str | None
    type: Literal["text_overflow", "text_overlap", "logo_illegible", "logo_wrong_company",
                  "logo_too_small", "logo_too_large", "logo_overlap", "logo_misaligned",
                  "visual_imbalance", "off_brand", "typo_or_grammar", "factual_mismatch", "other"]
    severity: Literal["major", "minor"]; description: str
    suggested_fix: Literal["rewrite_shorter", "rewrite_copy", "swap_logo", "relayout", "none"]
class VisionVerdict(BaseModel):
    passed: bool; defects: list[Defect]; summary: str
```
System prompt:
```
You are the final visual check before a deck is printed and mailed to a company founder. Compare the three
rendered slides with the template baseline renders (same design, other company). Report only defects you can
see. Major = a person would notice it on paper (text cut off or touching another element, a logo that is
unreadable, wrong, much larger or smaller than its neighbours, overlapping logos, misspelt company name).
Minor = small polish issues. Use the numbered element map to name elements. passed=true only if there are no
major defects. Known renderer artefacts to ignore: slight font hinting differences; the faint "Recur Software
Highly Confidential" footer; the watercolour texture; in the BASELINE images only, rectangular seams inside
the glow behind the target logo (our output uses a smooth bitmap glow instead — that difference is intended).
```

---

## 9. QA loop and the fix menu

```
round = 0
build → issues_A = qa.run_all(...)          # exact
while round < MAX_QA_ROUNDS:
    fix every issues_A error (table below); rebuild; issues_A = qa.run_all(...)
    if no errors in issues_A:
        render → verdict = vision_qa(...)
        if verdict.passed: break
        map major defects → fixes; rebuild
    round += 1
deliver (always). qa_report.json records every round, every issue, every fix, final status:
  "pass" | "pass_with_warnings" | "needs_review" (unresolved major defect after max rounds)
```

| Fix code | Action | Limit |
|---|---|---|
| `rewrite_shorter` / `rewrite_copy` | `rewrite_field` (§8.6) for that field only | 2 attempts/field |
| `swap_logo` | next candidate source for that company; when exhausted → typeset | until typeset |
| `relayout` | move the lowest-relevance competitor in the crowded cell to its second-best valid cell (ask `write_deck` for an alternative placement only if no valid cell) else drop it if > 5 remain | 2 |
| `shorten_display_name` | title overflow caused by a very long company name: ask `entity_judge` output for the short brand form (e.g. "Lighthouse" for "Lighthouse Veterinary Practice Cloud"), set `display_name`, rebuild | 1 |
| `needs_review` | cannot be fixed automatically (e.g. glyphs the font lacks in the company name); deliver and flag | — |
| `rebuild` | rebuild from a fresh template copy; if it persists, abort with a clear error (bug) | 1 |

There is deliberately **no font-size shrinking**: the reference style is fixed at 15/11pt, so overflow is
always fixed by rewriting. Pass `extra_protected=[entity.vertical words, aliases]` to `qa.run_all` /
`qa.check_field` so industry words like "Landscape" in "landscape contractors" are not linted as AI-speak.
Vision defects map to fixes via the element id: `logo:<name>` → `swap_logo` or `relayout`; a text field →
`rewrite_shorter`/`rewrite_copy` on that field (for `thesis[i]`, ask the rewrite for headline or subline
according to the defect description).

Never loop on the same unchanged input twice (hash the content + logo set per round).

---

## 10. Failure and fallback policy

| Situation | Behaviour |
|---|---|
| Exa unavailable (no key/402/5xx) | Use Firecrawl search for C2–C4; if Firecrawl also down use Claude's server web search tool (check current tool version in the Anthropic docs) |
| Firecrawl unavailable | Fetch pages with httpx + readability (`bs4` text extraction); JS-heavy sites → Playwright if installed, else skip page |
| Brandfetch unavailable / quota / 404 | Site scrape → typeset |
| Entity ambiguous | §8.1 gate behaviour |
| < 5 verified competitors | Relaxation rule §8.3; if still < 5 and ≥ 3 exist, validate with `context={"min_competitors": 3}` (already supported) and flag `thin_market` in the report (this knowingly misses acceptance criterion A5 — say so) |
| LibreOffice missing | Skip render + vision QA, deliver PPTX, `status=needs_review`, explain how to install |
| Claude 5xx / overloaded | Retry with backoff; for judges fall back from Sonnet to Haiku; writer has no fallback (fail clearly) |
| Any stage exception | Catch at the orchestrator, write `qa_report.json` with the traceback summary, exit code 2 |

Exit codes: 0 pass · 1 pass with warnings / needs_review · 2 error · 3 ambiguous entity (needs user choice).

---

## 11. Logo scraping details (`stages/logo_sources.py`)

From the target's homepage `rawHtml` (Firecrawl, `onlyMainContent:false`), collect candidates in this order:
1. JSON-LD `@type` Organization/Corporation `logo` (string or `{url}`) — usually designed for white
   backgrounds, ideal for whitening.
2. Inline `<svg>` inside `header`, `nav`, or an `a[href="/"]` / `a[href="<root url>"]` (serialize outer HTML;
   ensure `xmlns="http://www.w3.org/2000/svg"`; skip if it uses `<use href="#…">` pointing outside the snippet).
3. `<img>` in header/nav/first `a[href="/"]` whose `src|alt|class|id` contains `logo` or the company
   name/alias (case-insensitive); prefer `.svg`, then largest `width` attr; resolve relative URLs against the
   final URL; handle `srcset` (take the largest).
4. `link[rel~=icon][type="image/svg+xml"]` (symbol only → last resort before typeset; usually rejected by
   `logo_check` as `symbol_only`).
5. `og:image` — accept only if `logo_check` says wordmark/lockup.
Download with httpx (follow redirects, ≤5 MB, accept `image/*` or SVG text). Then `logos.process_logo(bytes)`
(rejects blank, solid blocks, too-faint, <40 px sources, >12:1 banners) → `logo_check`.

---

## 12. Testing

### 12.1 Keep green
`pytest -q` must stay green (37 existing tests). Add tests for every new module.

### 12.2 New tests to write
- **Client contract tests** with recorded responses (`tests/cassettes/*.json`, captured once from real calls,
  keys stripped): request bodies match §7 exactly; response parsing handles missing optional fields.
- **Entity gate**: synthetic verdicts → accept / ask / non-interactive behaviours and exit code 3.
- **Evidence integrity**: quotes not found in the source markdown are dropped.
- **Competitor selection**: channel counting, domain merge (`www.`, subdomains, `tldextract`), relaxation.
- **Fix loop**: inject an overlong headline → `rewrite_field` mocked → loop converges in 1 round; inject a
  bad logo → swap to typeset; same-hash guard prevents infinite loops.
- **Pipeline replay**: `recur-deck --replay runs/<folder>` rebuilds from cached JSON without network.

### 12.3 Live acceptance matrix (run in M8; record results in `docs/TEST_RESULTS.md`)
| Company | Why it's in the matrix | Must show |
|---|---|---|
| Ai2000 | Recur's own example; rebrand A2000 → Ai2000 | aliases found, competitors overlap the reference |
| shopVOX | small vertical SaaS (print/sign shops) | real niche competitors, not generic CRMs |
| Gingr | pet-care business software | correct vertical, logos |
| Dockwa | marina software, tiny market | thin-market handling |
| FDM4 | a competitor from the reference used as target | Ai2000 appears on its map |
| "Atlas" (no hint) then "Atlas" + `--domain` | ambiguous name | exit 3 / clean resolution |
(Verify each still exists before testing; replace any that don't.)

For each: open the PDF and check by eye against §1.1; confirm every competitor in `sources.md` with one click.

---

## 13. Packaging (what Recur receives)

1. **CLI** — `pyproject.toml` with `[project.scripts] recur-deck = "recur_deck.cli:main"`.
   `recur-deck "Ai2000" [--domain D] [--hint H] [--non-interactive] [--accept-low-confidence]
   [--out ./output] [--replay RUN_DIR]`.
   Prints a short progress log (rich), ends with paths to the PPTX/PDF and the QA status.
2. **Claude Code skill** — `.claude/skills/build-deck/SKILL.md`:
   ```markdown
   ---
   name: build-deck
   description: Build a Recur outreach deck for a company. Use when the user says "build a deck for <company>"
     or asks for a Recur deck, mailer deck or first-touch deck for a company.
   ---
   1. Extract the company name (and a domain or industry hint if the user gave one).
   2. Run: `recur-deck "<name>" [--domain <d>] [--hint "<h>"] --non-interactive --out ./output`
      from the repo root (activate the venv first if `recur-deck` is not on PATH).
   3. Exit code 3 means the name is ambiguous: show the user the candidate domains printed by the command,
      ask which one, then re-run with --domain.
   4. When it finishes, report the PDF and PPTX paths, the QA status, and any fallbacks listed in
      qa_report.json (typeset logos, thin market, low-confidence identity). Do not edit the deck by hand.
   ```
3. **README.md** for Recur: prerequisites (Python 3.11, LibreOffice, 5 API keys), install (3 commands),
   run (1 command or "build a deck for X" in Claude Code), outputs, costs per deck, troubleshooting, and
   the list of domains to allow if run inside a restricted sandbox (`api.anthropic.com`, `api.exa.ai`,
   `api.firecrawl.dev`, `api.brandfetch.io`, plus the target sites for logo downloads).
4. Ship `output/Recur_x_Ai2000.pdf` and one other company's PDF as samples.

---

## 14. Milestones (execute in order; show DoD evidence for each)

**M0 — Bootstrap (15 min).** venv, install deps, `render.install_fonts()`, `pytest -q` (37 pass),
`python -m recur_deck.offline fixtures/ai2000 --out out/ai2000` → open `out/ai2000/Recur_x_Ai2000.pdf`.
*DoD:* tests green, PDF looks like `assets/reference_Ai2000.pdf`.

**M1 — Foundations.** `config.py`, `cache.py` (disk cache + cost ledger), `evidence.py`, `clients/http.py`.
*DoD:* unit tests for cache hits, retry policy (mock 429 → retry, 400 → no retry), cost ledger.

**M2 — Clients.** `exa.py`, `firecrawl.py`, `brandfetch.py`, `claude.py` per §7 (structured output helper
`ask(model, system, user_blocks, OutputModel, images=None, effort=...)`). Record cassettes with one real call
each. *DoD:* contract tests green; a manual smoke script prints one result per client.

**M3 — S1 + S2.** Entity resolution with the gate; profile extraction with verified quotes.
*DoD:* "Ai2000" resolves to a2000software.com with alias A2000 (confidence ≥ 0.75); "Atlas" without hint
exits 3; profile.json has ≥ 8 facts with valid quotes.

**M4 — S3 + S4.** Competitor discovery/verification; logo sourcing + `logo_check`.
*DoD:* Ai2000 yields ≥ 5 verified competitors, each passing the §8.3 selection rule (≥ 2 channels, or 1
channel + head-to-head page) (expect overlap with BlueCherry,
Aptean, AIMS360, ApparelMagic, NetSuite, FDM4, Uphance, Sync, Datatex); every logo either verified or typeset.

**M5 — S5 + S6 + Layer A loop.** `write_deck`, `rewrite_field`, orchestrator round loop with Layer A only.
(`assets/baseline/slide{1,2,3}.png` already exist for M6.)
*DoD:* end-to-end Ai2000 run produces a deck with 0 Layer-A errors; copy passes the style lint.

**M6 — Layer B.** `vision_qa` + fix routing + same-hash guard + `qa_report.json` + `sources.md`.
*DoD:* inject faults (overlong subline, wrong logo, 4 logos in a cell) → loop fixes each within 3 rounds.

**M7 — Packaging.** CLI, skill, README, `.env.example`, sample outputs.
*DoD:* fresh clone on another machine → README steps → deck in < 10 min; "build a deck for Ai2000" works in
Claude Code.

**M8 — Acceptance.** Run the §12.3 matrix, fix what breaks, write `docs/TEST_RESULTS.md` (per company:
time, cost, QA status, fallbacks, one-line verdict). *DoD:* all criteria in §1.1 met for ≥ 5 of 6 companies;
failures explained.

---

## 15. Budgets

- **Latency:** S1 ≈ 15–25s · S2 ≈ 20–40s · S3 ≈ 40–90s (parallel) · S4 ≈ 15–30s · S5 ≈ 30–60s · build ≈ 2s ·
  render ≈ 6s · vision ≈ 15–25s per round. Target < 5 min end to end; hard timeout 12 min.
- **Cost per deck (target < $2):** Exa ~15 searches (~$0.10) · Firecrawl ~30–60 credits · Brandfetch ≤ 9
  calls (cached) · Claude ≈ $0.5–1.5 (Opus writer + Sonnet judges/vision). Record actuals in `cost.json`.
- **Concurrency:** ≤ 6 parallel HTTP calls per service; respect Firecrawl free-plan 10 req/min.

---

## 16. Do / Don't summary

**Do:** measure text with `fonts.py`; edit the template in place by shape id; whiten logos in code (pass
`designed_for_dark` for light-theme logos); keep
evidence quotes verbatim; cache everything; deliver even when degraded and say exactly what degraded.

**Don't:** touch slides 4–10; shrink fonts; use PowerPoint brightness tricks; call `cdn.brandfetch.io` or Exa
`/findSimilar`; send `temperature`/forced `tool_choice` to 5.5 models; let the LLM write XML; add competitors
from model memory; log API keys; trust LibreOffice's exit code without checking
the PDF exists.
