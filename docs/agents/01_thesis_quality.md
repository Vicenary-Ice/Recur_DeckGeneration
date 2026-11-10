# Agent 1 — Thesis quality: stop copying, write a real investment case

Branch: `agent/1-thesis`. Fixes P1, P2 and P3, and known defects D1–D3.

## The problem (evidence)
All three live decks (`tests/fixtures_live/*/content.json`) reuse Recur's own sentences:

- Ai2000: "Ai2000 is set to win as brands bring AI into the ERP"
- FDM4: "FDM4 is set to win as decorators move to one system / …so switching is hard"; "Help FDM4 grow
  with outbound sales and AI / We add outbound sales to what already works and build AI into…"
- Uphance: "Uphance is set to win as brands drop disconnected tools"; "Help Uphance grow with outbound
  sales and AI / We add outbound to the sales-led model and build AI into the product"

**Root cause:** the writer prompt's only example is Recur's own Ai2000 deck
(`prompts/style_examples/00_recur_ai2000.md` after Agent 0's split), and the rules require point 03 to
start with "Help <Company>". The model fills in the template. When Recur tests Ai2000, the output will
echo their own deck back to them.

The thesis also describes the product, not the investment. None of the decks says why the business is
durable, and point 03 is always "outbound + AI", whatever the evidence says.

## You own
`recur_deck/prompts/write_thesis_rules.md`, `recur_deck/prompts/style_examples/**`,
`recur_deck/prompts/profile_extract.md`, `recur_deck/stages/profile.py`, `recur_deck/stages/write.py`
(thesis logic and `enforce()`; leave the market parts as they are), `recur_deck/checks/copy.py` (new),
`assets/reference_text.json` (new), `ThesisPoint` plus new thesis-level fields in `recur_deck/models.py`,
and your tests.

## Design

### A. Remove Recur's deck from the prompt (P1)
- Delete `style_examples/00_recur_ai2000.md`. Recur's exact sentences must never appear in the prompt.
- Write **3 style examples** for other verticals, using placeholders (`<Company>`, `<Rival group>`), each
  annotated with *why* it works. Suggested verticals:
  - funeral-home software (sticky, records-heavy)
  - brewery ERP (compliance and inventory)
  - staffing ATS (placements and the database)

  Show different sentence shapes in each: a "because" clause, a number from evidence, a named customer
  type. **No two examples may share a sentence structure.**
- Add a short "**Don't**" list of bad and good pairs, built from the real failures above (paraphrased,
  never quoted verbatim).
- Store Recur's reference text (slide 2/3 lines from `assets/reference_Ai2000.pdf`) in
  `assets/reference_text.json`, **for the checker only**.

### B. Copy lint: `checks/copy.py` (P1)
- `copied_spans(content) -> list[Issue]`: flags any thesis, subtitle or our-take line that shares a
  **5-word sequence** (after lowercasing; company names count as a single token) with
  `reference_text.json` or any style example. Code `copied_language`, fix `rewrite_copy`.
- **Banned stock phrases**, kept in a list in this module: "set to win", "so switching is hard",
  "to what already works", "grow with outbound sales and AI", "sales-led model".
- **Cross-deck similarity**: append each finished thesis to `runs/_thesis_history.jsonl` (the path comes
  from `extra["run_dir"]`'s parent). Flag a headline whose token-set similarity with any previous deck's
  headline in the same slot is > 0.6. Code `repetitive_across_decks`, fix `rewrite_copy`.
- Register all of these with `@register`.

### C. A real investment case (P2)
Extend `profile_extract.md` and `profile.py` to collect **durability and gap signals**. Each signal is
a verbatim quote with an evidence id, as today:
- `tenure`: founded year, years serving the niche
- `customers`: named customers, customer counts, retailer or partner networks
- `switching`: data held, integrations, compliance or records that make leaving costly
- `gtm_signals`: sales or demo-request pages, careers listings for sales roles, partner channels. The
  *absence* of a sales team stays absent; record only what is present.
- `tech_signals`: on-prem, desktop, legacy-stack or "cloud since" mentions
- `team_signals`: founder-led wording, the leadership page

Then add thesis-level rules, enforced in `enforce()` and in `checks/copy.py`:
- **Point 01 (why now):** must cite ≥ 1 evidence id of kind `news`, `segment` or `comparison` that
  describes a change in the *customers'* world. A subline about what vendors are doing is invalid.
- **Point 02 (why this business):** must cite ≥ 1 durability id (`tenure`, `customers` or
  `switching`). `durability_issues()` flags it otherwise.
- **Point 03 (how we help):** add `DeckContent.lever: Literal["ai_native_gtm", "ai_rebuild",
  "ai_modernization", "built_the_bench"]` and `DeckContent.lever_gap_evidence_ids: list[str]` (≥ 1).
  Allowed levers come from the signals:

  | Gap signal | Allowed lever |
  |---|---|
  | No outbound or sales signals, OR partner-only channel | `ai_native_gtm` |
  | Legacy, desktop or on-prem tech signals | `ai_modernization` or `ai_rebuild` |
  | Founder-led with no senior hires found | `built_the_bench` |
  | No clear gap | Use the weakest-signal default `ai_native_gtm`, and write it as "adding to" what exists |

  `lever_issues()` flags a lever whose gap evidence isn't in the evidence store, or that the signals don't
  allow. Point 03's headline must **not** start with "Help". Its subline must name the specific gap in
  plain words.

### D. Writer prompt (`write_thesis_rules.md`)
Rewrite the thesis section to match the above:
- describe the three roles
- require the evidence kinds
- forbid starting point 03 with "Help"
- put "match the register of the style examples, never their sentences" in bold

Keep `{STYLE_RULES}` and all length budgets unchanged.

## Tests (offline, fake Claude)
- Remove the xfail markers on **D1, D2 and D3**; they must pass.
- `test_prompt_has_no_reference_text`: the rendered writer prompt contains no 5-word sequence from
  `assets/reference_text.json`.
- `test_lever_mapping`: a profile with only tech signals → only modernization or rebuild is allowed. A
  profile with no gtm signals → `ai_native_gtm` is allowed.
- `test_enforce_rejects_uncited_durability`: point 02 without a durability id fails validation, and
  the writer is re-asked with the problem.
- `test_style_examples_distinct`: no two examples share a 5-word sequence or the same opening two words.
- Re-run the existing writer tests and update their fake outputs to include `lever` and the gap ids.

## Definition of done
- D1–D3 pass; full suite green.
- The rendered writer prompt shown in your report contains no Recur sentences and has 3 new style
  examples.
- In your report, show what each of the three fixture decks *would be flagged for* now: run the checks
  on the fixture content. No live calls.
