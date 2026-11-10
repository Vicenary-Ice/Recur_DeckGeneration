# Agent 3 — Market-map plausibility

Branch: `agent/3-map`. Fixes P5 (rules side), P6, P7, P8 and P9, and known defects D4–D7.

## The problem (evidence, `tests/fixtures_live/`)
- **Ai2000 (212951):**
  - BlueCherry and InStyle are placed "Ops only" with the rationale "no financials cited", which is the
    absence of evidence.
  - "Our take" then claims "Large-brand suites lack financials".
  - Most slide-3 evidence comes from **aims360.com's** marketing pages, i.e. a competitor describing its
    rivals.
- **FDM4:**
  - The target sits **alone** in the top-right cell, which tells the founder "you have no competition".
  - Aptean is placed "General ERP" here but apparel-specific in the Ai2000 run.
  - The thesis leads with decorators, yet no decorator vendor is on the map.
- **Uphance:** the y axis "Core buyer" goes from "Enterprise and makers" (bottom) to "Wholesale + DTC
  brands" (top). That isn't a scale, and it reads upside down.

## You own
`recur_deck/prompts/write_market_rules.md`, `recur_deck/checks/market.py` (new),
`recur_deck/stages/placement.py` (new), `recur_deck/prompts/placement_audit.md` (new), the
`Axis`/`Competitor`/`OurTake`/`Market` classes in `recur_deck/models.py`, and your tests.

You read the market graph through Agent 0's interface only (`placements`, `neighbors`). Until Agent 2
merges, code against the stub.

## Design

### A. Ordinal axes (P7)
Add `Axis.kind`, chosen from a fixed list, each with a defined direction:

| kind | low end (left / bottom) | high end (right / top) |
|---|---|---|
| `customer_size` | small / single-site | large / multi-entity / enterprise |
| `customer_complexity` | simple programs | complex programs (many retailers, entities, compliance) |
| `product_scope` | point tool / ops only | full suite (incl. financials, WMS…) |
| `industry_depth` | generic / horizontal | built for this industry |
| `deployment_modernity` | desktop / on-prem / legacy | cloud-native |
| `segment_focus` | other segments | the target's segment |

- `axis_issues(content)` flags code `axis_not_ordinal` when a kind is missing or unknown, when x and y
  have the same kind, or when the labels contradict the direction. Use a small keyword table per kind
  (e.g. `customer_size` high must not contain "small|single|boutique"). Fix `regenerate_market`.
- `write_market_rules.md` lists the kinds and their directions. The writer must pick two.

### B. Every placement needs positive evidence (P6, P9)
- `Competitor` gains `x_evidence_ids` and `y_evidence_ids` (each ≥ 1). `OurTake` gains
  `point_evidence_ids: list[list[str]]` (one list per point, ≥ 1 each).
- `stages/placement.py` → `audit(ctx, content, evidence) -> list[Issue]`. For each competitor × axis,
  Haiku (`model_fast`, structured output) receives the axis kind and direction, the cell side, and the
  cited quotes. It answers `supports | contradicts | insufficient`, with the supporting quote.
  - `insufficient` or `contradicts` → Issue `placement_unsupported`, fix `regenerate_market`, field
    `market.competitors[i]`.
  - Same treatment for each our-take point: does a quote show the group really lacks the thing?
  - Batch everything for one deck into a **single Haiku call**. Results are cached as usual.
- `placement_issues(content, evidence)` is the deterministic part, run before any LLM:
  - a rationale or claim matching `no .* (cited|mentioned|found)|not (cited|mentioned)` → unsupported
  - a missing or unknown evidence id → unsupported
- **Source diversity:** no single registrable domain may supply more than 40% of the evidence ids used
  on slide 3. For each competitor, at least one of its x/y evidence quotes must come from its **own
  domain or a neutral directory** (capterra, g2, getapp, softwareadvice, softwareconnect, trustradius),
  not from a rival's site. Code `evidence_not_diverse`, fix `regenerate_market`.

### C. Plausibility (P8)
`plausibility_issues(content, extra)`:
- `target_alone`: the target's cell holds no competitor, while ≥ 1 verified competitor has evidence
  for both high sides. Fix `regenerate_market` with the hint "place the strongest direct rival next to
  the target".
- `empty_cells`: more than 1 empty cell → `regenerate_market`.
- `segment_missing`: the thesis or subtitle names a segment (from Agent 2's candidate segment metadata,
  or the profile's segments) that no competitor on the map serves. Fix `regenerate_market` with the
  hint "either add a competitor from that segment or don't lead with it".

### D. Consistency with history (P5)
`consistency_issues(content, graph)`: for each competitor, compare its side on each axis kind with
`graph.placements(domain)` for the same kind. If they contradict, and the current placement cites no
quote that isn't already in the graph → `placement_contradicts_history`, fix `regenerate_market`, with
the earlier quote attached as a hint.

### E. Writer rules (`write_market_rules.md`)
Rewrite the market section:
- pick two axis kinds from the table
- cite x and y evidence for every competitor
- "Not mentioned is not evidence"
- the strongest direct rival goes in or next to the target's cell
- our-take points need quotes
- prefer each competitor's own site or a neutral directory over a rival's page

Keep the length budgets.

Register `audit` through `hooks.after_write`. It returns problem strings; Agent 6 routes
`regenerate_market`.

## Tests (offline)
- Remove the xfail on **D4, D5 and D6**; they must pass on the fixtures.
- **D7** depends on Agent 2's real graph. Prove your logic with your own test, using a hand-built fake
  graph object that returns Aptean's earlier placement. Leave D7's xfail in place: Agent 6 removes it
  after merging Agent 2.
- `test_axis_kinds`: every kind has a direction table; contradicting labels are caught.
- `test_diversity`: synthetic evidence that is 60% from one domain → flagged.
- `test_audit_batching`: `audit` makes exactly one fake-Claude call per deck and maps verdicts to issues.
- Update the existing fixtures and fake writer outputs for the new required fields, without weakening
  any assertion.

## Definition of done
- D4–D6 pass, and D7's logic is proven with a fake graph; full suite green.
- The report lists, for each of the three fixture decks, every issue `checks.market` now raises.
