# Decisions and deviations from BUILD_PROMPT.md

Every place the implementation adapts the build prompt, and why. Newest first.

## Judgment-layer fixes (docs/agents briefs, 2026-11-10)
The seven briefs in `docs/agents/` were carried out one after another by a single agent, each phase on its
own branch merged into `main` before the next (order 0, 2, 3, 1, 4, 5, 6). Where a brief did not match the
code, the brief's intent was followed and the adaptation is listed here, per phase.

### Phase 2: market memory and competitor recall
- **Same-market links, not only edges (D8).** FDM4 is not a node in the Ai2000 or Uphance runs (not even a
  candidate), so "neighbours within depth 2" of fdm4.com is empty. `graph.related_targets(entity)` links a new
  target to earlier targets whose vertical shares a distinctive word ("apparel"; generic words such as ERP,
  software, inventory, wholesale, distribution, home are ignored). Those targets (depth 1) and their competitors
  (depth 2) become `graph` seeds, at most 12, closest and most-verified first; companies an earlier run
  rejected for the same target are skipped. Every seed is still verified on its live site.
- **Which runs the graph records.** The `after_run` hook adds only pass / pass_with_warnings runs (the brief).
  `market_graph ingest` and `from_runs` also accept needs_review runs, because their competitor research was
  verified even when the deck needed a person's look (two of the three runs the live test plan ingests are
  needs_review). Placements whose rationale rests on missing evidence ("no financials cited", "without a
  warehouse module") are never recorded as history. `add_run` skips needs_review unless told otherwise, as the
  brief's test requires.
- **Offline runs neither read nor write the graph** (`market_graph.for_run`): a cached run must replay the
  same way, and a graph seed adds the `graph` channel, which changes the writer's input and its cache key.
  `pipeline.py` uses `for_run` (one-line change in Agent 6's file).
- **Derived placements.** Earlier decks rarely used the same axis kinds (Ai2000: product scope + customer size;
  FDM4: industry depth + customer size). `placements(domain)` therefore also returns one *derived* reading per
  (run, kind) for kinds that run's map did not use, read from its rationale and quotes with plain-word cues
  (`recur_deck/axes.py`, kinds industry_depth, customer_size, deployment_modernity only). Example: the Ai2000
  run's quotes "specially tailored solutions built for fast-moving apparel sectors" and "industry-specific ERP"
  read as industry_depth = high, which is what FDM4's "General ERP" placement contradicts (D7).
- **New shared module `recur_deck/axes.py`**: the fixed axis kinds with their directions, title -> kind
  inference for decks written before axes had a kind, and the cue reader. Agent 3's checks use it too.
- **Segment searches only for sub-segments the vertical does not already name.** One Haiku call
  (`prompts/competitor_segments.md`) turns the profile's segment facts into up to 2 search phrases; a phrase
  whose distinctive words are all in the vertical is skipped (FDM4: "apparel decorators" is searched,
  "promotional products distributors" is not). Each segment costs 2 searches: `<segment> software` with
  markdown (2 results, ~4 credits) and `<segment> ERP` (or `management software`) titles only (~2 credits).
- **Self-compare pages** come from `profile["site_urls"]` when present, else the profile stage's `/v2/map`
  call repeated (served from the cache, 0 credits), plus the homepage links; up to 4 pages, compare/vs first,
  then switch/migrate, then integration/partner. The extractor prompt gained a closing paragraph (appended, so
  its opening words, which the fake Claude keys on, are unchanged).
- **Verification cap**: `MAX_VERIFY` 14 -> 20, and weak (one-channel) candidates now fill up to 20 instead of
  12. FDM4's run had Ai2000, ApparelMagic, Uphance and Sync as one-channel candidates that the old cap never
  verified.

### Phase 0: baseline and scaffolding
- **The reviewed Ai2000 deck is `runs/ai2000-20261109-163637`, not `-212951`.** The briefs name
  `runs/ai2000-20261109-212951` as "the deck the user reviewed", but that folder is an `--offline` replay that
  stopped at the competitors stage (cache miss): no `content.json`, `competitors.json`, logos or manifest. The
  deck in `output/` with every defect the briefs quote ("set to win as brands bring AI into the ERP",
  BlueCherry "with no financials cited", "Large-brand suites lack financials") is `-163637`. It is frozen as
  `tests/fixtures_live/ai2000-20261109-163637-deck/`. The existing `tests/fixtures_live/ai2000-20261109-163637/`
  (re-shaped logo verdicts for `test_live_fixes.py`) is untouched, which is why the new folder has a suffix.
- **Native logo sizes in the fixtures are trimmed source pixels.** The briefs estimate Ai2000's cover logo at
  "about 343 dpi"; that divides the *upscaled* width (453 px) by the printed width (1.32 in). The source PNG is
  600 x 448 px and `process_logo` enlarges it x1.339 before trimming, so the logo itself is 338 x 154 source
  pixels: about **256 dpi** on the cover. `native_w`/`native_h` in each fixture's `logo_metrics.json` are
  processed size / upscale factor (`None` for vector and typeset logos). Uphance's cover logo is 219 x 41 source
  pixels (about 106 dpi).
- **20 xfailed items, 12 defects.** D1-D3 and D12 are parametrised over the three decks.
- **Prompt snapshot.** `tests/snapshots/write_deck_system.txt` pins the rendered writer prompt. The split was
  proven byte-identical against it; a phase that changes the prompt on purpose regenerates it in the same commit.
- **Hook plugins.** `hooks.PLUGIN_MODULES` lists the modules that register handlers (market graph, placement
  audit, critic); `hooks.fire` imports them on first use, so no stage module edits `pipeline.py` to register.
- `pyproject.toml` package data now includes `prompts/style_examples/*.md`; `tests/conftest.py` also unsets
  `RECUR_MARKET_GRAPH`, so no test can read or write the real graph.

## Services: Anthropic + Firecrawl only (decided by Recur, 2026-11-07)
The build prompt specifies five keys (Anthropic, Exa, Firecrawl, Brandfetch API + client ID). The pipeline
uses only **Anthropic and Firecrawl**. The Exa and Brandfetch clients were removed rather than kept as unused
branches. What replaced each use:

| Was | Now |
|---|---|
| S1 candidates from Brandfetch Brand Search + Exa company search + Firecrawl search | Firecrawl search only: `"<name>" software`, `"<name>" formerly`, `"<name>" <hint>` (or `company`), weighted by rank, plus domains written in snippets |
| S2 news from Exa (`category: news`) | One Firecrawl search with `sources: [{"type": "news"}]` (titles + snippets) |
| S3 C2 directories via Exa `includeDomains` | Firecrawl search with `includeDomains` (documented; Firecrawl adds `site:`) on capterra/g2/getapp/softwareadvice, with `scrapeOptions` markdown (2 results) |
| S3 C3 similar companies via Exa `category: company` | Firecrawl search for the target's one-liner with `excludeDomains`; results that are vendor homepages (not listicles) become candidates |
| S3 C4 roundups via Exa | Firecrawl search `best <vertical> software` with markdown (4 results) |
| S3 name -> domain via Exa / Brandfetch search | See "Name -> domain" below |
| S4 logos via Brandfetch Brand API first | The company's own site only (JSON-LD, header SVG/img, SVG icon, og:image) -> typeset |

Channel names in `competitors.json` are now `vs_page`, `directory`, `similar`, `roundup`.

**Name -> domain (new).** A domain counts only after the candidate site is fetched and the product name is
found on it (`competitors.name_on_page`). Guesses, in order: a domain stated in the text or a markdown link,
the vendor's own page (a "X vs Y" page hosted on x.com), a similar-channel result with the same name,
then one batched Claude call (`prompts/domain_guess.md`, Haiku) for names still unresolved. Up to 5 names
that are still left get a Firecrawl search lookup. The competitor itself always comes from a fetched page.
The model only proposes where its website is, and the live site has to confirm it, so the
"never from model memory" rule holds.

**Direct-first page fetching (new, `RECUR_FETCH_MODE`).** With Firecrawl credits the only research cost,
plain pages (homepages, product pages, competitor sites) are fetched over HTTPS and parsed with BeautifulSoup
(`<main>` preferred; nav, footer, forms and cookie banners dropped). Firecrawl scrape is used when that page
is a bot wall, returns 403/429/5xx or times out, or has under 400 characters of text (JS-rendered). It is
not used for a 404 or a domain that doesn't exist: nothing to recover. DNS failures are not retried.
`RECUR_FETCH_MODE=firecrawl` restores the spec's Firecrawl-first order. Searches and `/v2/map` always use
Firecrawl.

**Logos without Brandfetch.** Site logos may be made for a dark or a light header, and `process_logo`
can't tell which. So a logo with white parts (on transparency) is whitened both ways, white as knockout and
white as ink, and the vision check picks the legible one. Header search covers `<header>`, `<nav>`, links to
the homepage, and div-based headers (`[class*=header|navbar|logo|brand]`). Sections whose class names say
client/customer/partner/testimonial/footer are skipped, so customer-logo strips can't win.

## Rendering: full fonts embedded in the render copy (changes `render.py`, a verified module)
**Finding (M0, macOS, LibreOffice 26.8).** The PDF came out in a serif fallback font (Linux Libertine /
Frank Ruhl). Cause: LibreOffice ships its own `Noto Sans Arabic` Regular/Bold inside the app bundle. Those
files contain Arabic, digits and punctuation but **no Latin letters**, and LibreOffice prefers them over the
copies `install_fonts()` puts in `~/Library/Fonts`. The template's own embedded fonts are MTX-compressed EOT
subsets, which LibreOffice can't decode without libeot. In sandboxed environments LibreOffice also can't
reach the OS font service at all. The kit's baseline renders were evidently made on Linux, where this
doesn't happen.

**Fix.** `render.to_pdf` now converts a *render copy* of the deck, never the deliverable. In that copy:
1. the embedded font parts for `Noto Sans Arabic` and `Noto Sans Arabic Light` (regular + bold) are replaced
   with uncompressed EOT wrappers around the full bundled fonts (`render.eot_wrap`);
2. those embedded fonts and every `typeface=` reference to them are renamed to `RecurRender Noto` /
   `RecurRender Noto Light`, so they can't collide with LibreOffice's bundled Arabic-only copies.

Glyphs and metrics are unchanged, so `fonts.py` measurements still hold, and slides 4-10 of the
**.pptx** stay byte-identical to the template (`qa.check_integrity`). Verified by checking which fonts the
PDF embeds (`RecurRenderNoto*` only, plus Liberation Sans for Arial page numbers) and by eye against
`assets/baseline/slide3.png`. Test: `tests/test_foundations.py::test_render_copy_embeds_full_fonts_under_unique_names`.
`install_fonts()` is kept unchanged (it still helps PowerPoint/Keynote users on that machine).

## Structured output: raw `output_config.format` path instead of `messages.parse`
§7.4 allows both. The raw path (`output_config={"format": {"type": "json_schema", "schema":
anthropic.transform_schema(Model)}, "effort": ...}`) lets us validate with a Pydantic **context**
(`DeckContent` thin-market `min_competitors`), and control the single "retry with the validation error"
ourselves. `messages.parse` validates without context, which would reject valid thin-market decks.
All calls stream (`messages.stream(...).get_final_message()`), so a long Opus answer can't hit the SDK's
non-streaming timeout guard.

## Refusal handling
Opus 5.5 and Sonnet 5.5 calls send the server-side refusal fallback (`fallbacks: "default"`, beta
`server-side-fallback-2026-07-01`) via `extra_headers`/`extra_body`. If the API rejects it with a 400 that
mentions fallbacks, the client turns it off for the rest of the run and retries. Haiku 5.5 has no server-side
fallback. In code, judges fall back Sonnet → Haiku on refusal or overload (§10); the writer has no fallback.
Set `RECUR_SERVER_FALLBACK=false` to disable.

## Profile: one `/v2/map` call, ranked locally
§3 lists map searches for product|features|solutions|about|customers|pricing|industries (7 calls). We make one
`/v2/map` call (no `search`, `limit: 200`, every other field as in §7.2) and rank the URLs by those keywords
in code. Same pages, 1 credit instead of 7, and it respects the free plan's 10 requests/minute.

## Competitors
- **Mention extraction prompt.** §8 has no prompt for turning search results into `{name, domain}`, so
  `prompts/competitor_extract.md` was added in the same register. Every mention's quote must be found
  verbatim in its document, or the mention is dropped.
- **vs/alternatives searches use titles + descriptions only** (no `scrapeOptions`): those pages carry the
  competitor names in their titles, and scraping each result would cost +1 credit per result.
- **Selection.** The strict rule is §8.3 **plus** at least 2 independent evidence sources (distinct
  domains), which is acceptance criterion A5. The relaxed rule is exactly §8.3's. Fewer than 2 verified
  competitors stops the run with a clear error (a map needs at least 3 quadrants); 2-4 → `thin_market`.

## Entity
- Candidate domains that redirect to the same site are merged before judging, and the 0.2 margin is
  measured against the next *different* company, so `a2000.com → a2000software.com` doesn't count as
  ambiguity.
- With `--domain`, the gate is skipped (the user has chosen). A judge score < 0.5 for that domain is
  reported as a warning.

## Logos
- The vision composite shows the original on a mid-grey checkerboard (so both dark and white originals are
  visible) next to the whitened version on navy #0B1630.
- The `swap_logo` fix uses the other candidates that passed `logo_check`, in source order, then typeset.

## QA loop
- At most `RECUR_MAX_QA_ROUNDS` fix rounds. After the last round's fixes, one **verification** render +
  vision call runs (not another fix round), so a fix is never delivered unchecked. The same-hash guard
  (content + logo pixels) stops any loop that makes no progress.
- The delivered PDF is always rendered from the delivered PPTX. If the deck changed after the last
  vision check, the status can't be `pass`.
- Status: `needs_review` = Layer-A errors left, a major visual defect left, no render, or a fix that
  needed a person. `pass_with_warnings` = clean, but with soft warnings, minor defects, fallbacks or
  flags. `pass` = none of these.

## Smaller choices
- `tldextract` uses its bundled public-suffix snapshot (`suffix_list_urls=()`), so no network call at runtime.
- `.env.example` adds `FIRECRAWL_RPM` (default 10, the free plan), `RECUR_FETCH_MODE` and `RECUR_SERVER_FALLBACK`.
- Firecrawl credits are taken from the API's `creditsUsed` field when it is present, else computed from the
  documented prices (search 2 per 10 results + 1 per scraped result; scrape 1; map 1).
- Claude responses are cached on disk like HTTP responses (keyed by model, prompts, images and schema), so
  `--replay` and re-runs are free and reproducible.
