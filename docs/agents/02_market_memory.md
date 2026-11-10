# Agent 2 — Market memory and competitor recall

Branch: `agent/2-market-memory`. Fixes P4 and P5 (data side), and known defect D8.

## The problem (evidence)
- Recall swings from run to run, even within one market: Ai2000 found 5 competitors (8 candidates),
  FDM4 found **4**, Uphance found 8 (`tests/fixtures_live/*/candidates.json`).
- The FDM4 run missed Ai2000, BlueCherry and ApparelMagic, which are its most direct rivals, although
  the Ai2000 run had found and verified them an hour earlier.
- FDM4's thesis leads with decorators, yet no decorator software (DecoNetwork, Printavo, InkSoft,
  shopVOX) was searched for.
- Each run starts from zero, so the same company (Aptean) gets described and placed differently every
  time.

## You own
`recur_deck/market_graph.py` (replace Agent 0's stub), `recur_deck/stages/competitors.py`,
`recur_deck/prompts/competitor_extract.md`, `recur_deck/prompts/competitor_verify.md`,
`tests/fake_world.py` (additions only), and your tests. Register your hooks from inside
`market_graph.py`; don't edit `pipeline.py`.

## Design

### A. Market graph (`runs/_market_graph.json`, path from env `RECUR_MARKET_GRAPH`)
```json
{"version": 1,
 "companies": {"aptean.com": {"names": ["Aptean", "Aptean Apparel ERP"], "aliases": [],
     "facts": [{"kind": "segment|scope", "quote": "...", "url": "...", "run": "ai2000-20261109-212951"}],
     "placements": [{"run": "...", "target": "a2000software.com", "axis_kind": "product_scope",
                     "axis_title": "Product scope", "side": "high", "label": "PLM to financials",
                     "evidence_ids": ["e12"], "quotes": ["..."]}],
     "verified_in_runs": ["..."]}},
 "edges": [{"a": "a2000software.com", "b": "aptean.com", "type": "competitor", "runs": ["..."],
            "channels": ["roundup"]}]}
```
- Implement the Agent 0 interface: `load`, `from_runs`, `neighbors(domain, depth=2)`, `placements`,
  `add_run`, `save`.
- Writes are atomic: write a temp file, then rename. Keep a `.bak` of the previous version.
- `add_run` stores **quotes and URLs, not just ids**, because evidence ids are only unique within a run.
- Register `hooks.after_run` → `graph.add_run(...)` then `save()`. **Only runs with status `pass` or
  `pass_with_warnings` are added**, so a bad run can't pollute the graph.
- Add a CLI: `python -m recur_deck.market_graph ingest runs/<dir> [...]` and
  `python -m recur_deck.market_graph show <domain>`.

  In your report, tell the user to ingest the three existing runs. That's local and free.

### B. More candidate channels (`stages/competitors.py`)
1. **`graph_seeds(graph, entity) -> list[Cand]`**: every neighbour within depth 2 becomes a candidate
   with channel `graph`.
   - It still has to pass `_verify` (live site + same buyer). Cached pages make this free in practice.
   - The `graph` channel counts as **one** channel and never as independent evidence on its own.
2. **Target's own pages**: from the target's `/v2/map` URL list (or the links on pages fetched in the
   profile stage, read from `ctx.evidence`), take URLs matching
   `compare|vs|versus|alternative|switch|migrat|replace|integrat|partner`. Extract vendor mentions from
   them with the existing `competitor_extract` flow. Channel `self_compare`.
3. **Sub-segment searches**: for the top 2 segments in `profile.json` (e.g. "decorators",
   "promotional products"), run Firecrawl searches for `"<segment> software"` and
   `"<segment> ERP"` / `"<segment> management software"`. Channel `segment`. Record which segment each
   candidate came from, so Agent 3 can check segment coverage.
4. Raise the targets: **≥ 15 unique candidates before verification** when the market allows it.
   - Set `MAX_VERIFY` 14 → 20.
   - Read directory and roundup pages in full chunks (already 4 × 8000 chars). Check that every vendor
     on a category page is extracted, not only the first ones.

Credit budget: at most +6 Firecrawl searches per run versus today. Log the counts.

### C. Consistency data for Agent 3
For each verified competitor, `comps_out["competitors"][i]["graph_placements"]` = `graph.placements(domain)`
for runs whose axis kind matches an axis kind the writer could choose. Agent 3 decides what to do with it.

## Tests (offline)
- Remove the **D8** xfail: with a graph built from the ai2000 and uphance fixtures, `graph_seeds` for
  FDM4 includes `a2000software.com`, `apparelmagic.com` and `cgsinc.com` (or BlueCherry's domain as
  recorded in the fixtures).
- `test_graph_roundtrip`: `from_runs` → `save` → `load` → identical. Writes are atomic. `add_run` skips
  `needs_review` runs.
- `test_self_compare_channel`: a fake site with `/compare/x-vs-y` yields candidate y with channel
  `self_compare`.
- `test_segment_search`: a profile with segment "decorators" triggers a `"decorators software"` search
  in the fake world, and its result becomes a candidate carrying segment metadata.
- `test_recall_floor`: in a fake world with 20 vendors spread across channels, ≥ 15 candidates reach
  verification.
- Existing competitor tests stay green. Extend `fake_world.py` only by adding pages, not by changing
  existing ones.

## Definition of done
- D8 passes; full suite green.
- The report shows the graph built from the three fixture runs, from `market_graph show aptean.com`,
  including every placement on record.
- The report states the extra Firecrawl searches per run (target ≤ 6) and the estimated credit cost.
