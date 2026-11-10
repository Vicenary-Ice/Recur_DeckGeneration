# Agent 4 — Print-grade logos

Branch: `agent/4-logos`. Fixes P10 and known defects D9–D11.

## The problem (evidence)
- **Uphance cover:** the logo prints soft. The site served a small raster, and `process_logo` enlarges
  every source to 600 px tall, so pixel counts in `logo_metrics.json` look fine while the real source
  resolution is low. The only floor today is 40 px tall, which is screen-grade, not print-grade.
  Ai2000's cover logo is also only about 343 dpi equivalent.
- **AIMS360 (Uphance map):** the lockup's "CONSUMER BRANDS ERP" tagline prints at roughly 2pt and reads
  as smudge. InStyle's "In-Style Software" caption in the Ai2000 deck has the same problem.
- **"Sync (iSync Solutions)" (Uphance map):** the typeset fallback used the full name with brackets.
  It wraps to two lines at about 0.13 in tall and is unreadable.

## You own
`recur_deck/logos.py`, `recur_deck/stages/logo_sources.py`, `recur_deck/prompts/logo_check.md`,
`recur_deck/names.py` (new), `recur_deck/checks/logo_print.py` (new), and your tests.
`layout.py` only if strictly needed for typeset sizing. Don't touch `build.py` or `spec.py` geometry.

## Design

### A. Record native resolution
- `LogoMetrics` gains `native_w` and `native_h` (source pixels before any upscaling; for SVG, `None`,
  meaning vector).
- `logo_sources` writes them into `logo_metrics.json`.

### B. dpi check after layout: `checks/logo_print.py`
`print_dpi_issues(manifest, logo_metrics)`: for each placed logo, `dpi = native_w / printed_width_in`.
- **Cover: ≥ 300 dpi. Map: ≥ 200 dpi.** SVG and typeset logos are exempt.
- Below the limit → code `logo_low_res`, fix `swap_logo`.
- **Ranking change in `resolve_company`:** among accepted candidates, prefer SVG, then the highest native
  resolution, before source order. Also try:
  - the largest `srcset` entry (already parsed)
  - `@2x`/`-2x`/`_2x` sibling URLs of the chosen image (one HEAD or GET each; cheap, direct HTTPS, no
    Firecrawl)
  - inline SVG in the header, if present

### C. Tagline micro-text
`tagline_issue(logo_png, printed_height_in) -> Issue | None` (deterministic):
- Find horizontal ink bands in the alpha channel (row projection).
- If there's a separate lower band with height < 22% of the logo's ink height, the printed tagline is
  `band_h_fraction × printed_height_in × 72` pt. Under **5pt** → issue `logo_tagline_microtext`.
- **Fix in code, before any swap:** crop the tagline band (re-trim), then re-check that the remaining
  mark is still the logo, using the vision check's `text_read` on the cropped image, or text matching
  if it's cached. If cropping isn't safe (bands touch, or more than 2 bands), fall back to `swap_logo`.

Extend `logo_check.md` and `LogoVerdict` with `tagline_present: bool`, used as a hint. The deterministic
band test decides.

### D. Short brand names for typeset fallbacks: `names.py`
`brand_short_name(name, aliases=())`:
- strip brackets: "Sync (iSync Solutions)" → "Sync"
- strip legal and generic suffixes when something distinctive remains: "Inc", "LLC", "Ltd",
  "Corporation", "Software", "Systems", "Solutions", "Technologies", "Apparel ERP" (only when a
  distinctive root of ≥ 3 characters remains)
- keep internal capitals and digits: "AIMS360", "Recruit CRM" (CRM isn't generic here)
- prefer the shortest alias that is still distinctive

`logo_sources.typeset()` uses it. **Typeset text must print ≥ 0.12 in tall on the map.** If a short name
still can't fit in 1 line at ≥ 0.12 in, use 2 balanced lines (existing logic) and never brackets.

## Tests (offline, with `tests/fixtures_live/uphance-20261109-215758/logos/_raw/**` and the ai2000 fixtures)
- Remove the xfail on **D9, D10 and D11**; they must pass.
- `test_native_px_recorded`: processing a 120 px-tall PNG records `native_h == 120` even though the
  output is 600 px.
- `test_crop_tagline`: the AIMS360 lockup → the cropped result has no lower band, and its aspect ratio
  changes as expected.
- `test_short_names`: a table of ≥ 12 cases, including "Sync (iSync Solutions)", "Aptean Apparel ERP",
  "InStyle Apparel Software", "Recruit CRM", "AIMS360", "Bravo Store Systems", "GCS Software, LLC".
- `test_svg_preferred`: two accepted candidates, a PNG and an SVG → the SVG wins.
- Existing logo tests stay green.

## Definition of done
- D9–D11 pass; full suite green.
- The report shows before and after renders of the Uphance cover and map logos, built offline from
  fixture files with `recur_deck.offline`-style building (no network).
