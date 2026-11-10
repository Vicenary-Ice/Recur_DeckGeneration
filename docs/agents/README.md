# Fix plan for Cursor agents

Three live decks (Ai2000, FDM4, Uphance; runs in `runs/`) pass every layout check, but none of them is
ready to mail. The problems are about judgment, not layout. This folder splits the fixes into 7 briefs,
one per Cursor agent.

## Order

```
Agent 0  Baseline + scaffolding          ── run first, alone (≈ 1 hour)
   │
   ├── Agent 1  Thesis quality (stop copying, real investment case)
   ├── Agent 2  Market memory + competitor recall
   ├── Agent 3  Market-map plausibility           ← these five run in parallel,
   ├── Agent 4  Print-grade logos                    each on its own branch
   └── Agent 5  Founder-eyes critic
   │
Agent 6  Integration + acceptance        ── run last, alone, after 1–5 are merged
```

Give each agent this README plus its own brief, e.g. *"Read docs/agents/README.md and
docs/agents/03_map_plausibility.md and carry out the brief."*

## Rules for every agent (non-negotiable)

1. **Never spend API credits.** Don't run `recur-deck "<name>"` without `--offline`, don't run
   `python -m recur_deck.smoke`, and don't call Anthropic or Firecrawl. Tests use `tests/fake_world.py`
   and the fake Claude client. Only the user runs live commands.
2. **Stay in your lane.** Edit only the files your brief lists under "You own". If you need a change
   elsewhere, write it under "Requests for Agent 6" in your report. Don't make the change yourself.
3. **One branch per agent:** `agent/<n>-<short-name>`. Commit in small steps with clear messages.
4. **Keep the suite green.** Run `pytest -q` before every commit. Never delete or weaken an existing
   test to make it pass. The one exception is removing the `xfail` marker on the known-defect tests your
   brief assigns to you, once your fix makes them pass.
5. **Verified core.** `fonts.py`, `build.py`, `render.py` and `spec.py` geometry were measured against
   Recur's template. Don't change them.
6. **Report back** in `docs/agents/reports/<n>.md` with: what changed, the test results, decisions with
   reasons, open risks, and requests for Agent 6.

## Problems found and the chosen fixes

| # | Problem (where it showed) | Options considered | Chosen fix | Agent |
|---|---|---|---|---|
| P1 | All 3 decks reuse Recur's sentences ("set to win", "so switching is hard", "Help X grow with outbound sales and AI") | (a) ban phrases only; (b) keep the Ai2000 example and add an n-gram lint; (c) **remove Recur's deck from the prompt**, add neutral style examples, lint against copying | (c). The Ai2000 deck in the prompt also leaks the answer when Recur tests Ai2000 | 1 |
| P2 | Thesis describes the product, not why it's a good business | (a) prompt wording only; (b) **structured requirements: why-now evidence, a durability fact, a lever tied to an evidenced gap**, enforced in code | (b) | 1 |
| P3 | Point 03 is always "outbound + AI" | (a) random lever; (b) **gap signals → allowed levers, with the writer citing the gap** | (b) | 1 |
| P4 | Competitor recall swings 4–8; FDM4 missed Ai2000, BlueCherry, ApparelMagic | (a) more searches; (b) **a market graph shared across runs, plus seeds from the target's own compare pages and searches for its sub-segment** | (b) plus a few more searches | 2 |
| P5 | Aptean placed three different ways across three decks | (a) ignore; (b) **record placements in the market graph; a contradiction needs new evidence** | (b) | 2 + 3 |
| P6 | A placement or claim built on missing evidence ("no financials cited" → "lack financials") | (a) prompt rule; (b) **every placement and group claim must cite a quote that supports it, audited in code + Haiku** | (b) | 3 |
| P7 | Non-ordinal axis (Uphance "Core buyer") | (a) LLM judge; (b) **axis kinds from a fixed list with a defined low/high** | (b), and the critic double-checks | 3 |
| P8 | Target alone in its quadrant (FDM4); lead segment missing from the map | **Plausibility checks: a rival in the target's cell, ≤ 1 empty cell, segment coverage** | as stated | 3 |
| P9 | One competitor's marketing page used as evidence about its rivals | **Source diversity: ≤ 40% of slide-3 evidence from one domain; a placement needs the rival's own site or a neutral directory** | as stated | 3 |
| P10 | Soft upscaled logo (Uphance cover); 2pt tagline text (AIMS360); "Sync (iSync Solutions)" typeset | **Native-resolution dpi check, cropping or rejecting tiny taglines, short brand names for typeset fallbacks** | as stated | 4 |
| P11 | QA checks layout but not credibility | (a) more rules; (b) **a "founder-eyes" critic: Opus reads slides 2–3 as the founder and as a Recur partner** | (b) | 5 |
| P12 | No cost ceiling on live runs | **`RECUR_MAX_COST_USD` budget guard** | as stated | 6 |

## Shared interfaces (Agent 0 creates them; everyone codes against them)

- `recur_deck/checks/` — a check registry. Add `checks/<yours>.py` with functions decorated with
  `@register`. `qa.run_all` runs every registered check, and they're auto-discovered, so no shared file
  changes are needed.
- `recur_deck/hooks.py` — extension points so no parallel agent edits `pipeline.py`:
  `after_profile`, `after_competitors`, `after_write`, `after_vision`, `after_run`.
- `recur_deck/market_graph.py` — Agent 0 creates the interface with a stub implementation. Agent 2 fills
  it in. Agent 3 uses only these functions: `load(settings)`, `placements(domain)`, `neighbors(domain)`,
  `add_run(...)`.
- The writer prompt is split into `prompts/write_deck.md` (skeleton), `prompts/write_thesis_rules.md`
  (Agent 1), `prompts/write_market_rules.md` (Agent 3) and `prompts/style_examples/*.md` (Agent 1).
- `models.py`: Agent 1 may change only `ThesisPoint` and add thesis-level fields on `DeckContent`.
  Agent 3 may change only `Axis`, `Competitor`, `OurTake` and `Market`.

## Known-defect tests (Agent 0 writes them as `xfail(strict=True)`)

| ID | Defect | Fixture run | Owner |
|---|---|---|---|
| D1 | Copied Recur phrases in content | all three | 1 |
| D2 | Point 03 cites no gap evidence; identical lever wording across decks | all three | 1 |
| D3 | Point 02 has no durability evidence | all three | 1 |
| D4 | Placement or "our take" claim from missing evidence (BlueCherry, "lack financials") | ai2000-212951 | 3 |
| D5 | Target alone in its cell | fdm4 | 3 |
| D6 | Non-ordinal y axis "Core buyer" | uphance | 3 |
| D7 | Aptean placement contradicts an earlier run | fdm4 vs ai2000 | 3 (uses 2's graph) |
| D8 | FDM4 discovery misses Ai2000 when the graph knows it | fdm4 | 2 |
| D9 | Typeset "Sync (iSync Solutions)" | uphance | 4 |
| D10 | Uphance cover logo under 300 dpi native | uphance | 4 |
| D11 | AIMS360 tagline prints under 5pt | uphance | 4 |
| D12 | Critic flags D1, D4, D5, D6 from slide content (fake Claude, scripted verdict) | all | 5 |

A fix is done when its D-tests pass without the `xfail` marker and the rest of the suite is still green.

## What "done" means for the whole plan

The user re-runs Ai2000, FDM4 and Uphance live (Agent 6 writes the exact commands). Then:

- No sentence shares 5+ consecutive words with Recur's deck or with another deck in the batch.
- Each thesis cites a why-now quote, a durability fact and an evidenced gap for its lever.
- Each map has ≥ 5 competitors, ordinal axes, a rival in the target's cell, and no placement without
  supporting evidence. Ai2000 and FDM4 each appear on the other's map.
- Every logo is ≥ 300 dpi (cover) or ≥ 200 dpi (map), or a clean short typeset name. No tagline under 5pt.
- The critic reports no major findings, and status is `pass` or `pass_with_warnings`.
- Cost per deck ≤ $2.
