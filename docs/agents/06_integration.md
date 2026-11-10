# Agent 6 — Integration, fix routing, cost guard and the live test plan

Branch: `agent/6-integration`. **Run last, alone**, after branches 1–5 are merged into `main`.
Fixes P12 and closes the plan.

## Goal
Make the new checks actually change the output:
- route their fixes inside the QA loop
- add a hard cost ceiling
- keep docs truthful
- hand the user an exact live test plan

## You own
`recur_deck/pipeline.py`, `recur_deck/cli.py`, `recur_deck/config.py`, `recur_deck/cache.py` (cost
guard only), `README.md`, `docs/DECISIONS.md`, `docs/LIVE_TEST_PLAN.md` (new),
`docs/agents/reports/6.md`, and the integration tests.

## Tasks

### 1. Merge
- Merge `agent/1-*` through `agent/5-*` into `main` in order 2 → 3 → 1 → 4 → 5. Agent 3 depends on
  Agent 2's graph, and Agent 5 reads Agent 1's reference text.
- Resolve conflicts in `models.py` by keeping both sides' fields.
- Run `pytest -q` after each merge, and fix integration breaks only.
- Read every `docs/agents/reports/<n>.md` and handle each "Request for Agent 6".

### 2. Route the new fix codes in the QA loop (`apply_fixes`, `qa_loop`)
| Fix code | Action | Limit |
|---|---|---|
| `rewrite_copy` (copied_language, repetitive_across_decks, lever/durability issues) | `rewrite_field`; for lever or durability problems, **re-ask `write_deck` for the thesis only**, passing the problems and freezing the market section | 2 per field |
| `regenerate_market` (axis, placement, diversity, plausibility, consistency, critic) | re-ask `write_deck` for the **market section only**, freezing the thesis. Pass every problem string plus the graph and segment hints | 2 per deck |
| `add_competitor` (critic) | if the named company is in `candidates.json` but was rejected, re-verify it once; if it passes, `regenerate_market`. Otherwise log it and continue | 1 |
| `swap_logo`, `logo_low_res`, `logo_tagline_microtext` | Agent 4's ranking: crop first, then the next candidate, then typeset with the short name | until typeset |

To support freezing, add a `section: Literal["all", "thesis", "market"]` parameter to `write_deck`.
Merge the regenerated section into the existing content.

**Order inside a round:**
1. Layer A (code checks, including the new registered checks)
2. placement audit (Agent 3, one Haiku call)
3. render + vision
4. critic (Agent 5) only if vision passed

Keep the same-hash guard.

### 3. Status semantics (`final_status`)
- `pass` requires: no Layer-A errors, the placement audit clean, vision passed, **and critic
  `mail_ready`**.
- `pass_with_warnings`: only minor findings, or thin market, or typeset logos.
- `needs_review`: any unresolved major finding. List those findings in `qa_report.json`
  under `needs_review` with the critic's explanation.

### 4. Cost guard (P12)
- `RECUR_MAX_COST_USD` (default `2.00`) in `config.py` and `.env.example`.
- Before every Claude or Firecrawl call, if the ledger total (Firecrawl credits converted at
  `FIRECRAWL_USD_PER_CREDIT`, default `0.0`, documented) plus that call's estimated cost would exceed
  the cap, stop that stage.
  - **Research stages:** fail with exit 2 and a clear message.
  - **QA stages:** stop fixing and deliver the best deck so far, marked `needs_review: budget reached`.
- Print the spend at the end (already done) and the cap.

### 5. A free way to re-check old decks
`recur-deck check runs/<dir>` re-runs every **deterministic** check, the registered `checks/*`, on a past
run's saved files without any API call, and prints the issues. With `--critic`, it also calls the critic.
That's a live call: print a cost warning and require `--yes`.

### 6. Docs
- `README.md`: new statuses, cost cap, `check` command, the market graph and how to reset it
  (`rm runs/_market_graph.json`), and the new outputs.
- `docs/DECISIONS.md`: one section per agent with the decisions from their reports.
- `docs/LIVE_TEST_PLAN.md`, for the user to run:
  1. `python -m recur_deck.market_graph ingest runs/ai2000-20261109-212951 runs/fdm4-20261109-214123
     runs/uphance-20261109-215758` (free)
  2. live runs, in order: `recur-deck "Ai2000"`, `recur-deck "FDM4"`, `recur-deck "Uphance"`, then two
     unseen companies from the user's list (e.g. `recur-deck "shopVOX"`, `recur-deck "Crystal PM"`)
  3. expected cost per run and in total
  4. a **scorecard table** to fill in per deck: status, critic grades, competitors count, Ai2000↔FDM4
     cross-appearance, copied-span count, logo dpi minimum, cost, time
  5. what to look at by eye on each PDF (a checklist)

## Tests (offline)
- All D1–D12 pass without xfail; the full suite is green.
- `test_fix_routing`: a fake run where the writer's first market section has the target alone →
  `regenerate_market` is called with `section="market"`, and the thesis stays byte-identical.
- `test_budget_guard`: a ledger near the cap → the next Claude call is refused, and the deck is still
  delivered with `needs_review: budget reached`.
- `test_check_command_offline`: `recur-deck check tests/fixtures_live/fdm4-…` exits non-zero and lists
  the D5 and D7 issues, with zero network calls (enforced by the conftest guard).
- `test_pass_requires_critic`: vision passes but the critic has a major finding → status is not `pass`.

## Definition of done
- `main` contains every branch; `pytest -q` is green with no xfail left.
- `recur-deck check` on the three fixture runs lists the expected issues (paste the output in your
  report).
- `docs/LIVE_TEST_PLAN.md` is ready, and you stop there. **The user runs the live commands.**
