You review slides 2 and 3 of a printed letter-deck that Recur Software will mail to the founder of a vertical
software company. Recur buys and grows such companies and holds them for the long term. Read the deck twice,
as two people, and report what each would object to.

1. THE FOUNDER of the target company. You know your market, your customers and every competitor by name.
   Ask: what here is wrong, unfair, generic or copied? Which claim could I disprove from my own website? What
   would make me bin this letter?
2. A RECUR PARTNER who signs the letter. Ask: does slide 2 show investment judgment (why now, why this
   business lasts, a lever tied to a real gap) or does it just describe the product? Would I be embarrassed if
   the founder checked any claim against the cited quotes?

You get the rendered slides, the deck's words (deck_content), every quote the deck cites (cited_evidence, with
its source domain), the verified competitors and the ones verified but left off, where earlier decks placed
these competitors (earlier_placements), the printed size and resolution of every logo (logos), and Recur's own
reference deck for another company (recur_reference_text).

Answer every item of this checklist; each problem you find is one finding:
- a claim the cited quotes don't support, or that is likely false (unsupported_claim)
- a competitor placed where its own evidence or an earlier deck says otherwise (disputed_placement)
- an obvious direct competitor missing from the map (missing_competitor; name it in the explanation)
- an axis that doesn't read as a scale from low to high (axis_not_ordinal)
- the target alone in its cell, or oddly placed (target_isolated)
- wording that echoes recur_reference_text or reads like a template (copied_language)
- a thesis that describes the product instead of the business: no why-now, no durability, a lever with no gap
  (generic_thesis)
- a logo that is illegible, blurry or a bare typeset name where a real logo should be (illegible_logo)
- anything negative or condescending about the founder's company (negative_tone)

For each finding give target as the exact field path ("thesis[1].headline", "market.subtitle",
"market.our_take.points[0]", "market.competitors[2]", "market.y_axis", "market.competitors" for the map as a
whole) or "logo:<company name>", and cite evidence ids in the explanation where relevant.

Severity. A finding is major only if a founder would notice it on paper and it would lower their trust in the
letter. Style preferences, a slightly long line, a word you would have chosen differently: minor. Do not invent
problems to look thorough; a clean deck gets few or no findings.

Grade each slide from A (would mail as is) to F. mail_ready is true only if there is no major finding.

Calibration (three real decks, paraphrased):
- Apparel ERP A: point 02 reused the reference deck's "is set to win as ..." shape (major, copied_language);
  why-now was about ERP vendors adding AI, not about brands (major, generic_thesis); a large-brand rival was
  placed "ops only" because no financials were cited, and "our take" said that group lacks financials (major,
  unsupported_claim on the placement and on the point). Grades: slide 2 B, slide 3 C+.
- Apparel and decorator ERP B: the thesis led with decorators, yet no decorator software was on the map (major,
  missing_competitor); the target sat alone in its cell (major, target_isolated); a rival described in its own
  cited quote as built for fashion was placed "General ERP" (major, disputed_placement); point 03 promised the
  same outbound-and-AI help as every other deck (major, generic_thesis). Grades: slide 2 C, slide 3 D+.
- Wholesale apparel ERP C: the y axis went from "Enterprise and makers" to "Wholesale + DTC brands", which is
  not a scale and reads upside down (major, axis_not_ordinal); one rival's typeset name wrapped onto two tiny
  lines (major, illegible_logo); a rival's tagline was too small to read (minor, illegible_logo). Grades: slide 2
  B-, slide 3 C+.
