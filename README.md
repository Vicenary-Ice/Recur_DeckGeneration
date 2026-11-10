# Recur deck builder

One command builds Recur's 10-slide first-touch deck for any software company:

```bash
recur-deck "Ai2000"
```

or, in Claude Code from this folder: **"build a deck for Ai2000"**.

The output is a `.pptx` and a print-ready `.pdf`. Slide 1 is the cover with both logos, slide 2 is
"What we see in [Company]" and slide 3 is the market map with real competitors. Slides 4–10 are copied
byte-for-byte from Recur's template. Each deck also comes with `sources.md`, where every claim and
competitor links to the page it came from, and `qa_report.json`, which records every check and fix.

## Prerequisites
- macOS or Linux, Python 3.11+
- LibreOffice (renders the PDF and the visual check): `brew install --cask libreoffice` on macOS, or
  `sudo apt install libreoffice-impress` on Linux
- Two API keys: Anthropic and Firecrawl

## Install (about 3 minutes)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env        # then paste your two keys into .env
```
Check the keys with `python -m recur_deck.smoke`. It makes one cheap call per endpoint (about 5 Firecrawl
credits and less than a cent of Claude).

## Run
```bash
recur-deck "Ai2000"                                  # interactive: asks if the name is ambiguous
recur-deck "Atlas" --domain atlas-hq.com             # pin the company when the name is ambiguous
recur-deck "Gingr" --hint "pet care software"        # industry hint for unusual names
recur-deck "Ai2000" --non-interactive                # never prompts (exit 3 + candidates if ambiguous)
recur-deck "Ai2000" --non-interactive --accept-low-confidence   # batch: take the best match, flag it
recur-deck --replay runs/ai2000-20261107-181500      # rebuild a past run offline, from cache
recur-deck check runs/ai2000-20261110-101500 runs/fdm4-20261110-102500   # re-check saved decks: free, no network
recur-deck check --scorecard runs/<dir> runs/<dir>   # the live test plan's scorecard table
python -m recur_deck.market_graph ingest runs/<dir> ...   # add finished runs to the market graph (free)
python -m recur_deck.market_graph show aptean.com    # what the graph knows about a company
```
In Claude Code, the `build-deck` skill (`.claude/skills/build-deck/SKILL.md`) runs the same command
and asks you to pick a domain if the name is ambiguous.

**Exit codes:** `0` pass · `1` pass with warnings or needs review (the deck is still delivered;
`qa_report.json` says what to look at) · `2` error or cost cap reached before a deck existed · `3` ambiguous
company (re-run with `--domain`).

**Statuses.**
- `pass` means every check is clean and the founder-eyes critic found the final deck ready to mail. Every
  check here: no layout or copy error, the placement audit clean, the visual check passed.
- `pass_with_warnings` means only minor findings remain, or a check could not run: `--no-vision`, the critic
  unavailable, a thin market, typeset logos.
- `needs_review` means at least one error or major finding is still open, or the cost cap stopped the fixing.
  `qa_report.json → needs_review` lists each one with the reason, including the critic's explanation.

**Cost cap.** `RECUR_MAX_COST_USD` (default `2.00`) caps each deck: Claude dollars plus Firecrawl credits ×
`FIRECRAWL_USD_PER_CREDIT` (default `0`, so credits don't count). Every Claude or Firecrawl call that isn't
cached is estimated first; a call that would pass the cap is not sent.
- In a research stage, the run stops with exit 2 and status `budget_exceeded`.
- In QA, fixing stops and the best deck so far is delivered as `needs_review: budget reached`.

The CLI prints the spend and the cap at the end.

## Outputs
`./output/` gets `Recur_x_<Company>.pptx`, `Recur_x_<Company>.pdf`, `Recur_x_<Company>_sources.md` and
`Recur_x_<Company>_qa_report.json`. The full working folder is `runs/<company>-<timestamp>/`:

| File | What it is |
|---|---|
| `entity.json` | Which company (domain, aliases such as A2000 → Ai2000, confidence, rejected look-alikes) |
| `profile.json` | Facts about the company, each with a verbatim quote id |
| `evidence.jsonl` | Every quote with its URL. Facts whose quote isn't on the page are dropped |
| `candidates.json` / `competitors.json` | All competitor candidates with channels, and the verified ones kept |
| `logos/` | White logos used; `_raw/` keeps every downloaded candidate |
| `content.json` | The deck's words and placements (`content.round<k>.json` per QA round) |
| `png/` | Slide renders and the numbered element maps the visual check used |
| `qa_report.json` | Status, every QA round, issue and fix, fallbacks, timings, cost, `critic` (grades, findings, mail-ready), `placement_audit`, `cost_cap_usd`, `budget_reached` |
| `cost.json` / `run.log` | Every external call with latency and cost (and the cap, and any refused call); the run log (no keys) |

Shared across runs, in `runs/`:
- `_market_graph.json` holds which companies compete with which, the verified quotes about them, and where
  earlier decks placed them. Finished `pass` / `pass_with_warnings` runs are added automatically, and earlier
  runs with `market_graph ingest`. A new run uses it to find competitors and to check placements against
  history. Reset it with `rm runs/_market_graph.json*`; `RECUR_MARKET_GRAPH` moves it.
- `_thesis_history.jsonl` holds every delivered deck's thesis, so a new deck can't repeat another company's.
  Reset it with `rm runs/_thesis_history.jsonl`.

`--offline` and `--replay` runs neither read nor write either file, so a cached run replays exactly.

## How it works
1. **Identify the company.** Firecrawl web search produces candidate domains, including the ones written
   in "formerly X" snippets. Each homepage is fetched and Claude judges which company you mean, catching
   rebrands. Ambiguous names stop and ask.
2. **Profile.** Up to 8 pages of the company's site plus a news search. Facts are kept only with an exact
   quote.
3. **Competitors.** Four Firecrawl search channels run in parallel: "X vs" pages, software directories
   (Capterra, G2, GetApp, Software Advice), vendor sites for the company's category, and category
   roundups. Three more channels add to them:
   - the target's own compare, "vs" and switch pages;
   - searches for up to 2 customer sub-segments the vertical doesn't name ("apparel decorators");
   - the market graph: competitors earlier runs verified in the same market.

   A competitor's website counts only once the product name is found on that live site. Up to 20 candidates
   are then checked on their own homepage: still operating, same buyer, segment and scope. Companies are never
   added from model memory.
4. **Logos.** Taken from each company's own website (structured-data logo, header SVG or image), checked by
   a vision model and whitened in code.
   - Ranking: vector first, then the highest source resolution; a sharper `@2x` file is tried.
   - A tagline that would print under 5pt is cropped.
   - A clean typeset short name ("Sync", never "Sync (iSync Solutions)") is the fallback.
5. **Write.** Claude Opus writes slides 2–3 from the evidence, in Recur's style but never Recur's sentences.
   Its style examples come from other verticals. The thesis must cite three things: a why-now quote about the
   customers, a durability quote (tenure, customers, switching) and the gap behind its lever. Every placement
   cites a quote per axis, and axes come from six ordinal kinds. Code pins the identity and drops anything
   unverified.
6. **Build and check.** The template is filled in place and every line is measured in the real font. Each QA
   round runs:
   1. the rule checks: layout, copied language, the thesis rules, axis kinds, evidence for each placement,
      source diversity, the target alone, consistency with earlier decks, logo dpi and taglines;
   2. one Haiku audit of whether each quote supports its placement;
   3. the render and the visual check against Recur's original;
   4. once that passes, a founder-eyes critic (Opus, at most twice) reading slides 2–3 as the founder and as a
      Recur partner.

   Fixes are routed automatically: one line rewritten, the thesis or the market section rewritten with the
   other frozen, a logo cropped, swapped or typeset. At most 3 rounds, plus a final check.

## Cost and time per deck
Estimated from the per-call budgets; measured numbers for real runs are in `docs/TEST_RESULTS.md`.

| Service | Typical use per deck | Estimate |
|---|---|---|
| Claude (Opus writer + critic, Sonnet judges + vision, Haiku extraction + audit) | ~30–45 calls | $0.80 – 1.60 (capped at $2.00) |
| Firecrawl | ~15–19 searches + 1 map + a few scrapes | ~40–80 credits |

Plain web pages (homepages, product pages, logo files) are fetched directly over HTTPS, which is free.
Firecrawl is used for every search, for the site map, and for any page that blocks plain requests or needs
JavaScript. Set `RECUR_FETCH_MODE=firecrawl` to send every page through Firecrawl instead (more credits).

Time is about 4–8 minutes (the QA loop may rewrite a section). On Firecrawl's free plan, its 10 requests/minute limit adds about a minute.
Raise `FIRECRAWL_RPM` in `.env` if your plan allows more.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Exit 3 "ambiguous" | Re-run with `--domain <company site>` |
| `typeset_logos` flag | No logo on that company's site passed the checks. The deck uses a clean typeset wordmark; drop in a logo by hand if you prefer |
| `thin_market` flag | Fewer than 5 competitors passed the strict checks. The map uses 3–4 (by design, never invented) |
| `needs_review` | Open `qa_report.json` → `needs_review` (each open issue with its reason), `critic`, `placement_audit`, `final_layer_a_errors`. `recur-deck check runs/<dir>` lists the rule checks again for free |
| Exit 2, `budget_exceeded` | A research call would have passed `RECUR_MAX_COST_USD`; raise it in `.env` and run again (cached calls are free the second time) |
| A logo looks soft on the cover | `logo_low_res` in `qa_report.json`. The target's own cover logo is kept (with a warning) down to 200 dpi; set `RECUR_TARGET_COVER_MIN_DPI=300` to typeset it instead |
| Old decks keep a placement or thesis flagged | They come from the market graph / thesis history. `python -m recur_deck.market_graph show <domain>` shows why; `rm runs/_market_graph.json*` or `rm runs/_thesis_history.jsonl` resets them |
| PDF missing, status `needs_review` | LibreOffice isn't installed or not found. Set `SOFFICE=/path/to/soffice` |
| Firecrawl 402 | Out of credits. Pages are still fetched directly, and searches fall back to Claude's web search (fewer competitors). Both are noted in the report |
| Serif font in the PDF | Not expected: the renderer embeds the bundled fonts. Check `run.log` for LibreOffice errors |

## Running in a restricted network sandbox
Allow `api.anthropic.com` and `api.firecrawl.dev`, plus outbound HTTPS to company websites (each company's
own domain and its CDN, for pages and logo files). Rendering needs no network.

## For developers
- `pytest -q` runs everything offline (simulated web + fake Claude for the pipeline tests).
- Build spec: `BUILD_PROMPT.md`. Deviations and why: `docs/DECISIONS.md`. The judgment-layer fixes:
  `docs/agents/` (briefs and reports) and `docs/LIVE_TEST_PLAN.md`.
- Known defects of three real decks are frozen as tests (`tests/test_known_defects.py`, fixtures in
  `tests/fixtures_live/`). Deterministic checks live in `recur_deck/checks/` (auto-registered), pipeline
  extension points in `recur_deck/hooks.py`.
- Fonts in `assets/fonts` are SIL Open Font License (licences included).
