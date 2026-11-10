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
```
In Claude Code, the `build-deck` skill (`.claude/skills/build-deck/SKILL.md`) runs the same command
and asks you to pick a domain if the name is ambiguous.

**Exit codes:** `0` pass · `1` pass with warnings or needs review (the deck is still delivered;
`qa_report.json` says what to look at) · `2` error · `3` ambiguous company (re-run with `--domain`).

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
| `qa_report.json` | Status, every QA round, issue and fix, fallbacks, timings, cost |
| `cost.json` / `run.log` | Every external call with latency and cost; the run log (no keys) |

## How it works
1. **Identify the company.** Firecrawl web search produces candidate domains, including the ones written
   in "formerly X" snippets. Each homepage is fetched and Claude judges which company you mean, catching
   rebrands. Ambiguous names stop and ask.
2. **Profile.** Up to 8 pages of the company's site plus a news search. Facts are kept only with an exact
   quote.
3. **Competitors.** Four Firecrawl search channels run in parallel: "X vs" pages, software directories
   (Capterra, G2, GetApp, Software Advice), vendor sites for the company's category, and category
   roundups. A competitor's website counts only once the product name is found on that live site. Each
   candidate's homepage is then checked: still operating, same buyer, segment and scope. Companies are never
   added from model memory.
4. **Logos.** Taken from each company's own website (structured-data logo, header SVG or image), checked by
   a vision model and whitened in code. A clean typeset wordmark is the fallback.
5. **Write.** Claude Opus writes slides 2–3 from the evidence, in Recur's style. Code pins the
   identity and drops anything unverified.
6. **Build and check.** The template is filled in place. Text is measured in the real font. A vision model
   compares the render with Recur's original, and defects are fixed automatically (at most 3 rounds).

## Cost and time per deck
Estimated from the per-call budgets; measured numbers for real runs are in `docs/TEST_RESULTS.md`.

| Service | Typical use per deck | Estimate |
|---|---|---|
| Claude (Opus writer, Sonnet judges + vision, Haiku extraction) | ~25 calls | $0.50 – 1.50 |
| Firecrawl | ~12–15 searches + 1 map + a few scrapes | ~25–40 credits |

Plain web pages (homepages, product pages, logo files) are fetched directly over HTTPS, which is free.
Firecrawl is used for every search, for the site map, and for any page that blocks plain requests or needs
JavaScript. Set `RECUR_FETCH_MODE=firecrawl` to send every page through Firecrawl instead (more credits).

Time is about 3–5 minutes. On Firecrawl's free plan, its 10 requests/minute limit adds about a minute.
Raise `FIRECRAWL_RPM` in `.env` if your plan allows more.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Exit 3 "ambiguous" | Re-run with `--domain <company site>` |
| `typeset_logos` flag | No logo on that company's site passed the checks. The deck uses a clean typeset wordmark; drop in a logo by hand if you prefer |
| `thin_market` flag | Fewer than 5 competitors passed the strict checks. The map uses 3–4 (by design, never invented) |
| `needs_review` | Open `qa_report.json` → `needs_review`, `final_vision`, `final_layer_a_errors` |
| PDF missing, status `needs_review` | LibreOffice isn't installed or not found. Set `SOFFICE=/path/to/soffice` |
| Firecrawl 402 | Out of credits. Pages are still fetched directly, and searches fall back to Claude's web search (fewer competitors). Both are noted in the report |
| Serif font in the PDF | Not expected: the renderer embeds the bundled fonts. Check `run.log` for LibreOffice errors |

## Running in a restricted network sandbox
Allow `api.anthropic.com` and `api.firecrawl.dev`, plus outbound HTTPS to company websites (each company's
own domain and its CDN, for pages and logo files). Rendering needs no network.

## For developers
- `pytest -q` runs everything offline (simulated web + fake Claude for the pipeline tests).
- Build spec: `BUILD_PROMPT.md`. Deviations and why: `docs/DECISIONS.md`. Acceptance runs:
  `docs/TEST_RESULTS.md`.
- Fonts in `assets/fonts` are SIL Open Font License (licences included).
