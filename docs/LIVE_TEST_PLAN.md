# Live test plan: the judgment-layer fixes

Everything below except step 3 is free and local. Step 3 makes live Claude and Firecrawl calls; each deck is
capped at `RECUR_MAX_COST_USD` (default $2.00). Run the commands from the repo root.

## 0. Set up (free, ~1 minute)
```bash
cd ~/Recur_CaseProject
source .venv/bin/activate
pytest -q                          # expect: 217 passed, nothing xfailed, nothing skipped (LibreOffice installed)
```
Optional, in `.env` (defaults shown):
```
RECUR_MAX_COST_USD=2.00            # per-deck ceiling
FIRECRAWL_USD_PER_CREDIT=0.0       # set e.g. 0.0053 (Hobby: $16 / 3,000 credits) to count credits toward the cap
# RECUR_TARGET_COVER_MIN_DPI=200   # 300 = typeset the target's own cover logo below 300 dpi (the brief's strict rule)
```
No new packages: the editable install picks up the code.

## 1. Seed the market graph with the earlier runs (free)
```bash
python -m recur_deck.market_graph ingest runs/ai2000-20261109-163637 runs/fdm4-20261109-214123 runs/uphance-20261109-215758
python -m recur_deck.market_graph show aptean.com
```
Expected output:
- `ingest`: `3 run(s) added; graph at runs/_market_graph.json has 42 companies, 55 edges, 3 runs`.
- `show aptean.com`: 8 placements on record, including FDM4's "General ERP" next to two "built for apparel"
  readings.

The briefs named `runs/ai2000-20261109-212951`. That folder is an `--offline` replay that stopped before
competitors (no deck), and `ingest` says so if you pass it. The reviewed Ai2000 deck is `-163637`.

## 2. See what the new checks say about the old decks (free)
```bash
recur-deck check runs/ai2000-20261109-163637 runs/fdm4-20261109-214123 runs/uphance-20261109-215758
recur-deck check --scorecard runs/ai2000-20261109-163637 runs/fdm4-20261109-214123 runs/uphance-20261109-215758
```
Expected: exit 1, with 13, 21 and 20 errors. The full listing is in `docs/agents/reports/6.md`. The
scorecard is the "before" row for each deck:

| Deck | Status then | Competitors | On the other's map | Copied spans | Errors now | Logo dpi (cover, map min) | Cost $ |
|---|---|---|---|---|---|---|---|
| Ai2000 | needs_review | 5 | FDM4: no/no | 3 | 13 | 256, 285 | 0.54 |
| FDM4 | pass_with_warnings | 4 | Ai2000: no/no | 5 | 21 | 3087, 415 | 0.72 |
| Uphance | needs_review | 7 | — | 5 | 20 | 106, 230 | 0.83 |

## 3. Live runs (costs money)
Run in this order: each finished `pass` or `pass_with_warnings` run joins the market graph, so FDM4 benefits
from the Ai2000 run and the other way round.
```bash
recur-deck "Ai2000"
recur-deck "FDM4"
recur-deck "Uphance"
recur-deck "shopVOX"
recur-deck "Crystal PM"
```
- If a name is ambiguous, the command asks you to choose. In batch use, add `--domain`: `a2000software.com`,
  `fdm4.com`, `uphance.com`.
- Each run prints its status, the critic's grades, the spend and the cap, and the paths in `output/`.
- Responses cached from the earlier runs are reused for free: entity checks, many searches and pages,
  competitor verifications. Set `RECUR_USE_CACHE=0` for a run from scratch, at roughly 1.3–1.5× the cost.

### Expected cost and time
Measured before these fixes: $0.54 / $0.72 / $0.83 and 36 / 68 / 55 credits. The new work per deck:

| Added | Calls | ~$ |
|---|---|---|
| Up to 8 more competitor verifications (Sonnet, ~$0.012 each) | ≤ 8 | ≤ 0.10 |
| Writer sent back once when the thesis breaks the rules (Opus, ~$0.12) | 0–1 | ≤ 0.12 |
| Market or thesis rewrites in the QA loop (Opus, ~$0.12 each; ≤ 2 each) | 1–3 typical | 0.12–0.36 |
| Placement audit (Haiku, one per distinct deck) | 1–3 | < 0.01 |
| Founder-eyes critic (Opus, at most 2) | 1–2 | 0.09–0.35 |
| Extra visual checks after rewrites (Sonnet, ~$0.07) | 0–2 | ≤ 0.14 |
| Sub-segment searches, tagline crop checks, segment extraction | few | < 0.02 |

- **Per deck:** about $0.90–1.60, typically ~$1.20, never above the $2.00 cap.
- **Firecrawl:** about 40–80 credits per deck; at most 4 more searches (~12 credits) than before.
- **All five runs:** about $5–8 and 250–400 credits.
- **Time:** 4–8 minutes per deck. A run stops on its own at 12 minutes.

## 4. Scorecard
After the five runs, fill the table in one command (free). Pass the five `run_dir` paths the runs printed
(folders are named `<company>-<date>-<time>`: `ai2000-…`, `fdm4-…`, `uphance-…`, `shopvox-…`, `crystal-pm-…`):
```bash
recur-deck check --scorecard runs/ai2000-<new> runs/fdm4-<new> runs/uphance-<new> runs/shopvox-<new> runs/crystal-pm-<new>
```
Columns, and what "done" means for each:

| Deck | Status | Critic s2/s3 | Competitors | On the other's map | Copied spans | Logo dpi (cover, map min) | Cost $ | Time s |
|---|---|---|---|---|---|---|---|---|
| Ai2000 | | | | FDM4: _/_ | | | | |
| FDM4 | | | | Ai2000: _/_ | | | | |
| Uphance | | | | | | | | |
| shopVOX | | | | | | | | |
| Crystal PM | | | | | | | | |
| **Target** | pass or pass_with_warnings | no major finding (B or better) | ≥ 5 | Ai2000 ↔ FDM4: yes/yes | 0 | ≥ 300 cover (or the target's real logo kept with a warning ≥ 200, or typeset), ≥ 200 map | ≤ 2.00 | ≤ 720 |

Then `recur-deck check runs/<each new run>` (free) must show none of these:
- `copied_language`, `repetitive_across_decks`;
- `axis_not_ordinal`, `placement_unsupported`, `target_alone`.

`qa_report.json` → `critic` holds the grades and every finding; `needs_review` lists anything still open, each
with its reason.

## 5. What to look at by eye in each PDF (slides 1–3)
Cover
- [ ] The target's own logo, sharp at arm's length; or its name typeset cleanly on one line (never brackets).

Slide 2 (thesis)
- [ ] 01 says what changed for the **customers**, not what vendors are doing.
- [ ] 02 says why this **business** lasts (records it holds, years in the niche, named customers), not just
  what the product does.
- [ ] 03 names a **gap** first ("sales come only through partners", "the desktop client…") and does not start
  with "Help".
- [ ] No line you have read in Recur's own deck or in another of today's decks ("set to win", "so switching is
  hard", "what already works").
- [ ] Each claim matches its quote in `Recur_x_<Company>_sources.md`.

Slide 3 (market map)
- [ ] Both axes read as scales: you can say which end is "more" ("Single brands → Multi-entity groups").
- [ ] A direct rival sits in or next to the target's cell; the target is not alone.
- [ ] If slide 2 or the subtitle names a segment (e.g. decorators), a competitor from it is on the map.
- [ ] Each competitor's place is backed by a quote from its own site or a directory in `sources.md`; nothing
  rests on "no X cited".
- [ ] FDM4 is on Ai2000's map and Ai2000 on FDM4's; Aptean sits where its quotes say it does (apparel-built).
- [ ] Every logo is legible; no tagline smaller than about 5pt (e.g. AIMS360's "CONSUMER BRANDS ERP" is gone).
- [ ] "Our take" points each describe a group, and the quote behind them really shows the group lacks it.

## 6. If something looks off
- **`needs_review`:** read `qa_report.json` → `needs_review`. Each entry names the field and why it is open.
  `recur-deck check runs/<dir>` repeats the rule checks for free.
- **A placement or thesis keeps getting flagged from history:** run
  `python -m recur_deck.market_graph show <domain>` to see the record. Reset with `rm runs/_market_graph.json*`
  or `rm runs/_thesis_history.jsonl`, then re-run step 1.
- **Exit 2 with `budget_exceeded`:** raise `RECUR_MAX_COST_USD` and run the same command again. Calls already
  made are cached and free.
- **Optional second opinion on any finished run:** `recur-deck check runs/<dir> --critic --yes` makes one live
  Opus call per run (~$0.09–0.17) and needs the run's slide renders.
