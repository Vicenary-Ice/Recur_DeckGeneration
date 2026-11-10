# Agent 0 — Baseline, regression fixtures and scaffolding

**Runs first and alone.** Agents 1–5 branch from your final commit, so finish completely before they
start.

## Goal
Make parallel work safe:
- version control
- the three failing live runs frozen as regression fixtures
- every known defect written as a failing test
- extension points so no two agents need to edit the same file

**You change no behaviour.** Every existing test must still pass at the end.

## You own
`.gitignore`, `tests/fixtures_live/**`, `tests/test_known_defects.py`, `recur_deck/checks/**` (new),
`recur_deck/hooks.py` (new), `recur_deck/market_graph.py` (new, stub only), the split of
`recur_deck/prompts/write_deck.md`, the minimal wiring in `recur_deck/qa.py`, `recur_deck/pipeline.py`
and `recur_deck/stages/write.py` described below, plus `docs/agents/reports/0.md`.

## Tasks

### 1. Git
- `git init`, then make sure `.gitignore` covers `.env`, `.venv/`, `runs/`, `output/`, `out/`,
  `__pycache__/`, `.pytest_cache/`, `*.egg-info/` and `Recur_CaseProject_kit.zip`.
- Commit everything else as `baseline: state before agent fixes`. Tag it `baseline`.

### 2. Freeze the three live runs as fixtures
For each run below, copy into `tests/fixtures_live/<run>/`: `entity.json`, `profile.json`,
`candidates.json`, `competitors.json`, `content.json`, `content.round*.json`, `logo_metrics.json`,
`evidence.jsonl`, `Recur_x_*.manifest.json`, `qa_report.json`, `logos/*.png` and `logos/_raw/**`.

The runs:
- `runs/ai2000-20261109-212951` (the deck the user reviewed)
- `runs/fdm4-20261109-214123`
- `runs/uphance-20261109-215758`

Don't copy the `.pptx`, `.pdf` or `png/` files: they're too large and not needed.

`tests/fixtures_live/ai2000-20261109-163637/` already exists. Leave it alone; it's used by
`test_live_fixes.py`.

Add `tests/fixtures_live/README.md` explaining what each run shows (use the table in
`docs/agents/README.md`).

### 3. Known-defect tests
Create `tests/test_known_defects.py` with one test per defect D1–D12 (table in `docs/agents/README.md`).
Each is marked `@pytest.mark.xfail(strict=True, reason="D<n> — owner Agent <k>")`.

Each test loads the fixture and calls the function the owning agent will implement. Use the names below,
and import inside the test body so collection doesn't fail before the function exists:

| ID | Test calls | Asserts |
|---|---|---|
| D1 | `checks.copy.copied_spans(content)` | non-empty for all three fixtures, and the issues have code `copied_language` |
| D2 | `checks.copy.lever_issues(content, evidence)` | flags point 03 with no gap evidence |
| D3 | `checks.copy.durability_issues(content, evidence)` | flags point 02 |
| D4 | `checks.market.placement_issues(content, evidence)` | flags BlueCherry and the "lack financials" claim (ai2000-212951) |
| D5 | `checks.market.plausibility_issues(content, extra={})` | flags `target_alone` (fdm4) |
| D6 | `checks.market.axis_issues(content)` | flags `axis_not_ordinal` (uphance) |
| D7 | `checks.market.consistency_issues(content, graph)` | flags Aptean. Build `graph` with `market_graph.from_runs([ai2000 fixture])` |
| D8 | `stages.competitors.graph_seeds(graph, entity)` | for FDM4, with a graph built from the ai2000 and uphance fixtures, includes `a2000software.com` |
| D9 | `recur_deck.names.brand_short_name("Sync (iSync Solutions)")` | `== "Sync"` |
| D10 | `checks.logo_print.print_dpi_issues(manifest, logo_metrics)` | flags the Uphance cover logo |
| D11 | `stages.logo_sources.tagline_issue(logo_png, printed_height_in)` | flags AIMS360 (uphance fixture) |
| D12 | `stages.critic.review(...)` with the fake Claude returning a scripted verdict | findings route to the correct fix codes |

For D10, store native pixel sizes if they're missing from `logo_metrics.json`: read the largest file in
`logos/_raw/uphance/`. Agent 4 will start recording them properly.

### 4. Check registry
Create `recur_deck/checks/__init__.py`:
- `@register` decorator with the signature
  `fn(content: DeckContent, manifest: dict, logo_metrics: dict, extra: dict) -> list[qa.Issue]`
- `run_registered(...)`
- auto-import of every submodule in the package (`pkgutil.iter_modules`), so agents only add files

`extra` carries `evidence` (EvidenceStore or `None`), `entity`, `graph` (market graph or `None`) and
`run_dir`.

In `qa.run_all`, append `checks.run_registered(...)`. Pass `extra=None → {}` so existing callers keep
working.

### 5. Hooks
Create `recur_deck/hooks.py` with registries and `fire(name, **kw)` for: `after_profile(ctx, entity,
profile)`, `after_competitors(ctx, entity, comps_out)`, `after_write(ctx, content) -> list[str]`
(problems), `after_vision(ctx, content, pngs, manifest) -> list[Finding-like dicts]` and
`after_run(ctx, report)`.

Call them from `pipeline.py` at the matching points. Return values default to empty and are logged.
Agent 6 wires their results into the fix loop later.

### 6. Market-graph stub
Create `recur_deck/market_graph.py` with these signatures and a no-op in-memory implementation:

```python
class MarketGraph:
    def neighbors(self, domain: str, depth: int = 2) -> list[dict]: ...  # [{domain, name, via, runs}]
    def placements(self, domain: str) -> list[dict]: ...  # [{run, axis_kind, axis_title, side, label, evidence_ids}]
    def add_run(self, entity: dict, comps_out: dict, content: dict | None) -> None: ...
    def save(self) -> None: ...

def load(settings) -> MarketGraph
def from_runs(run_dirs: list[Path]) -> MarketGraph
```

Path: `runs/_market_graph.json`, configurable through env `RECUR_MARKET_GRAPH`. Agent 2 implements it.

### 7. Split the writer prompt
This is a pure refactor: the rendered system prompt must come out byte-identical.
- `prompts/write_deck.md` becomes the skeleton, with `{THESIS_RULES}`, `{MARKET_RULES}`,
  `{STYLE_EXAMPLES}` and `{STYLE_RULES}`.
- Move the thesis section to `prompts/write_thesis_rules.md`.
- Move the market-map section to `prompts/write_market_rules.md`.
- Move the gold example to `prompts/style_examples/00_recur_ai2000.md`. `write.py` concatenates
  `style_examples/*.md` in name order.

Add a test that the rendered prompt equals a snapshot taken before the split.

### 8. Finish
Run `pytest -q`: every existing test must pass, and D1–D12 are xfail. Commit as
`scaffolding: fixtures, known-defect tests, checks registry, hooks, graph stub, prompt split` and tag it
`agents-start`.

## Definition of done
- `git log` shows the `baseline` and `agents-start` tags.
- `pytest -q` is green, with 12 xfailed.
- Rendered writer prompt is unchanged, proven by the snapshot test.
- Report written to `docs/agents/reports/0.md`.
