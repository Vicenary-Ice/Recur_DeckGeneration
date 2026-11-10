# Live-run fixtures

Files copied from real runs in `runs/`, frozen so the judgment fixes can be tested offline. Renders (`png/`),
`.pptx` and `.pdf` are left out on purpose. Paths inside `logo_metrics.json` still point at the original
`runs/...` folder; tests resolve logos by file name under each fixture's `logos/`.

| Folder | Source run | Status then | What it shows |
|---|---|---|---|
| `ai2000-20261109-163637/` | `runs/ai2000-20261109-163637` | needs_review | Logo verdicts only (`logo_metrics.json` re-shaped with `companies`, raw logo files). Used by `test_live_fixes.py`. Leave it alone. |
| `ai2000-20261109-163637-deck/` | `runs/ai2000-20261109-163637` | needs_review | **The Ai2000 deck the user reviewed.** Copies Recur's sentences ("set to win", "to what already works"); point 03 is "outbound + AI" with no gap evidence; point 02 cites no durability fact; BlueCherry and InStyle placed "Ops only" because "no financials cited"; "our take" claims "Large-brand suites lack financials"; much of slide 3's evidence comes from aims360.com's own marketing pages. (D1–D4, D7 history) |
| `fdm4-20261109-214123/` | `runs/fdm4-20261109-214123` | pass_with_warnings | FDM4 sits **alone** in the top-right cell; Aptean is placed "General ERP" although its own cited quotes say it is built for apparel (and the Ai2000 run read it as apparel-specific); the thesis leads with decorators but no decorator vendor is on the map; only 4 competitors, while Ai2000, ApparelMagic, Uphance and Sync were found as one-channel candidates and never verified. (D1–D3, D5, D7, D8) |
| `uphance-20261109-215758/` | `runs/uphance-20261109-215758` | needs_review | y axis "Core buyer" ("Enterprise and makers" → "Wholesale + DTC brands") is not a scale; the cover logo is a 219 × 41 px raster printed 2.07 in wide (≈ 106 dpi); AIMS360's "CONSUMER BRANDS ERP" tagline prints at about 2pt; "Sync (iSync Solutions)" was typeset with its brackets. (D1–D3, D6, D9–D11) |

`ai2000-20261109-212951`, which the agent briefs name as the reviewed Ai2000 deck, is an `--offline` replay
that stopped at the competitors stage (cache miss): it has no `content.json`, `competitors.json` or logos.
The deck the user reviewed is `ai2000-20261109-163637` (the one in `output/`), so it is frozen as
`ai2000-20261109-163637-deck/` (see `docs/DECISIONS.md`).

Each deck folder holds `entity.json`, `profile.json`, `candidates.json`, `competitors.json`, `content.json`,
`content.round*.json`, `logo_metrics.json`, `evidence.jsonl`, `Recur_x_*.manifest.json`, `qa_report.json`,
`logos/*.png`, `logos/_raw/**` and `logos/_alt/**`.

**Added at fixture time:** `native_w` / `native_h` (+ `native_note`) in each `logo_metrics.json` entry: the
placed logo's trimmed size in *source* pixels, i.e. its processed size divided by the upscale factor
`process_logo` applied (`None` for vector sources and typeset wordmarks). Runs before Agent 4 did not record
it. Example: Uphance's cover logo came from a 219 × 41 px PNG that `process_logo` enlarged 14.6× to 600 px
tall.

`critic/` (Agent 5) holds scripted critic verdicts for the three decks, used with the fake Claude client.

Known-defect tests that use these folders: `tests/test_known_defects.py` (D1–D12, table in
`docs/agents/README.md`).
