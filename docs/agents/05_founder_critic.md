# Agent 5 — Founder-eyes critic

Branch: `agent/5-critic`. Fixes P11 and known defect D12.

## The problem
Today's QA catches layout defects: overflow, overlaps, logo size. It **passed** all of these:
- copied Recur sentences (all three decks)
- a non-ordinal axis (Uphance)
- the target alone in its quadrant (FDM4)
- a claim built on missing evidence (Ai2000)
- an unreadable typeset logo (Uphance "Sync")

A person who knows the market spots each of these in seconds. Agents 1, 3 and 4 add rule-based checks
for the known cases. You add the **judgment layer** that catches the cases nobody has written a rule for.

## You own
`recur_deck/stages/critic.py` (new), `recur_deck/prompts/critic.md` (new),
`tests/fixtures_live/critic/**` (new scripted verdicts), and your tests. Register through
`hooks.after_vision`. Don't edit `pipeline.py`.

## Design

### Inputs, one call per round
- Rendered `slide2.png` and `slide3.png`, ≤ 2576 px long edge.
- `content.json`.
- The evidence quotes cited by the content: id, quote, URL, domain.
- The verified competitor list with segment and scope.
- The market-graph placements for the competitors on the map (Agent 0 interface).
- `assets/reference_text.json` (Agent 1), so the critic can recognise Recur's own sentences. **Never
  include it in any writer prompt.**

### Model
`settings.model_writer` (Opus), effort `high`, structured output. Cost is about one Opus call per round.
Run it **only after the vision check passes**, and at most twice per deck.

### Prompt (`critic.md`), two personas, one pass
1. **The founder** of the target, who knows their market and competitors well. "What here is wrong,
   unfair, generic or copied? What would make me bin this letter?"
2. **A Recur partner** signing the letter. "Does this show investment judgment: why now, why this
   business is durable, a lever tied to a real gap? Would I be embarrassed if the founder checked any
   claim?"

The checklist the critic must answer explicitly:
- unsupported or likely-false claims
- disputable placements
- an obvious competitor missing
- axes that don't read as scales
- the target alone or oddly placed
- language echoing Recur's reference text or boilerplate
- a thesis that describes the product instead of the business
- a logo that is illegible or blurry at print size
- anything negative about the founder's company

### Output schema
```python
class Finding(BaseModel):
    persona: Literal["founder", "partner"]
    slide: Literal[2, 3]
    target: str            # content field path (e.g. "thesis[2].subline", "market.competitors[3]") or "logo:<name>"
    category: Literal["unsupported_claim", "disputed_placement", "missing_competitor", "axis_not_ordinal",
                      "target_isolated", "copied_language", "generic_thesis", "illegible_logo",
                      "negative_tone", "other"]
    severity: Literal["major", "minor"]
    explanation: str       # one or two sentences, cites evidence ids where relevant
    suggested_fix: Literal["rewrite_copy", "rewrite_shorter", "regenerate_market", "relayout",
                           "swap_logo", "add_competitor", "none"]
class CriticVerdict(BaseModel):
    findings: list[Finding]
    slide_grades: dict[str, Literal["A", "B", "C", "D", "F"]]   # {"2": "B", "3": "C"}
    mail_ready: bool       # true only if no major findings
```
`review(ctx, content, pngs, manifest, evidence, graph) -> CriticVerdict`. Map the findings to the
pipeline's issue and fix vocabulary in `critic.to_fixes(verdict) -> list[dict]`. Agent 6 wires that into
the loop.

### Calibration (few-shot inside `critic.md`)
Include three **short** worked examples drawn from the real fixture decks, with the findings an expert
would make. Use the issues listed in `docs/agents/README.md` (P1, P6, P7, P8, P10) and these grades:

| Deck | Slide 2 | Slide 3 |
|---|---|---|
| Ai2000 | B | C+ |
| FDM4 | C | D+ |
| Uphance | B- | C+ |

Paraphrase the decks. Don't paste whole slides, and keep the examples under 600 tokens total.

**Guard against an over-strict critic:** a finding is `major` only if a founder would notice it on
paper and it would lower trust. Style nits are `minor`.

## Tests (offline, fake Claude)
- Remove the xfail on **D12**: scripted verdicts in `tests/fixtures_live/critic/` (one per fixture deck,
  containing the known findings) → `to_fixes` maps them:
  - copied language → `rewrite_copy` on the right field
  - target alone → `regenerate_market`
  - illegible logo → `swap_logo`
- `test_critic_inputs`: the request contains 2 images, the cited evidence and the reference text, and
  **no API keys**. The writer prompt never contains the reference text (assert on `write.py`'s rendered
  prompt).
- `test_mail_ready_semantics`: any major finding → `mail_ready` is forced to false, even if the model
  says true.
- `test_round_cap`: the critic is called at most twice per deck.

## Definition of done
- D12 passes; full suite green.
- The report contains the full `critic.md` prompt and the estimated cost per call (input tokens ×
  price).
