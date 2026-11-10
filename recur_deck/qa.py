"""Layer A of the self-check loop: exact, deterministic checks that run before any vision call.

Every issue carries a machine-readable `fix` from a closed menu so the orchestrator can act:
  rewrite_shorter        - ask the writer to shorten one field (field path + measured/allowed values given)
  rewrite_copy           - style/format problem in one field; rewrite that field
  swap_logo              - try the next logo source for that company (falls back to typeset)
  relayout               - move a competitor to another valid cell / drop the weakest one
  shorten_display_name   - the company name itself is too long for a title: use the short brand form
  needs_review           - cannot be fixed automatically (e.g. characters the font lacks); deliver + flag
  rebuild                - template integrity problem; rebuild from a fresh template copy

Field paths match DeckContent, e.g. "thesis[1].subline", "market.our_take.points[0]", "market.x_axis.low_label".
Titles are "thesis.title" and "market.title" (generated from the company name, never rewritten).
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path

from pptx import Presentation

from . import fonts
from .layout import Box
from .models import DeckContent
from .spec import (ALLOWED_TYPEFACES, CELLS, FIXED_SLIDES, LINE_BUDGETS, LINE_TARGETS, MIN_LOGO_GAP,
                   MIN_LOGO_H, OUR_TAKE, PLOT, TEMPLATE_LEAK_TOKENS, TEMPLATE_PATH)


@dataclass
class Issue:
    code: str
    severity: str          # error | warn
    slide: int
    field: str
    message: str
    fix: str
    measured: float | None = None
    allowed: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ style rules (single source of truth)
BANNED_WORDS = [
    "delve", "tapestry", "testament", "robust", "seamless", "leverage", "landscape", "pivotal", "underscore",
    "cutting-edge", "game-changing", "revolutionize", "revolutionise", "unlock", "empower", "synergy", "holistic",
    "best-in-class", "world-class", "innovative", "paradigm", "ecosystem", "streamline", "elevate",
    "supercharge", "next-gen",
]
BANNED_PHRASES = ["in today's", "ever-evolving", "fast-paced", "not just X but Y", "not only X but also Y"]
_STEMS = r"delv\w*|tapestr\w*|testament\w*|robust\w*|seamless\w*|leverag\w*|landscap\w*|pivotal\w*|underscor\w*|" \
         r"cutting[- ]edge|game[- ]chang\w*|revolutioni[sz]\w*|unlock\w*|empower\w*|synerg\w*|holistic\w*|" \
         r"best[- ]in[- ]class|world[- ]class|innovat\w*|paradigm\w*|ecosystem\w*|streamlin\w*|elevat\w*|" \
         r"supercharg\w*|next[- ]gen\w*"
BANNED_PATTERNS = [
    (r"—|–|\s--\s", "dash used as punctuation (use a comma or a new sentence)"),
    (r"\bnot (just|only)\b.*\bbut\b", "'not just X but Y' construction"),
    (rf"\b({_STEMS})\b", "stock AI / sales word"),
    (r"\bin today's\b|\bever[- ]evolving\b|\bfast[- ]paced\b", "filler phrase"),
    (r"!", "exclamation mark"),
]
STYLE_RULES_FOR_PROMPT = (
    "Never use: dashes as punctuation (em/en dash or ' -- '), exclamation marks, full stops at the end of a "
    "slide line, " + ", ".join(f'"{p}"' for p in BANNED_PHRASES) + ", or these words in any form: "
    + ", ".join(BANNED_WORDS) + "."
)  # render this string into the writer prompt so prompt and checker never drift apart


def protected_terms(content: DeckContent, extra: list[str] | None = None) -> list[str]:
    """Names that may legitimately contain 'banned' words or punctuation (e.g. 'Streamline Health',
    'Elevate', 'Yahoo!', 'Acme Co.') plus caller-supplied terms such as the entity's vertical."""
    terms = [content.company.display_name, *content.company.aliases, *(c.name for c in content.market.competitors)]
    return sorted({t for t in [*terms, *(extra or [])] if t}, key=len, reverse=True)


def lint_copy(text: str, protected: list[str] | None = None) -> list[str]:
    scrubbed = text
    for t in protected or []:
        scrubbed = re.sub(re.escape(t), " ", scrubbed, flags=re.I)
    hits = [why for pat, why in BANNED_PATTERNS if re.search(pat, scrubbed, flags=re.I)]
    if text.rstrip().endswith(".") and not any(text.rstrip().endswith(t) for t in protected or []):
        hits.append("ends with a full stop (slide lines don't)")
    return hits


# ------------------------------------------------------------------ text checks
def _line(issues: list[Issue], slide: int, field: str, text: str, budget_key: str,
          protected: list[str] | None = None, fix: str = "rewrite_shorter", lint: bool = True) -> None:
    font_key, pt, max_w = LINE_BUDGETS[budget_key]
    if "\n" in text or "\r" in text:
        issues.append(Issue("newline", "error", slide, field, f"line break not allowed here: {text!r}", "rewrite_copy"))
        text = text.replace("\r", " ").replace("\n", " ")
    w = fonts.text_width_in(text, font_key, pt)
    if w > max_w:
        issues.append(Issue("text_overflow", "error", slide, field,
                            f"'{text}' is {w:.2f}in wide; limit {max_w:.2f}in", fix, round(w, 3), max_w))
    elif budget_key in LINE_TARGETS and w > LINE_TARGETS[budget_key]:
        issues.append(Issue("text_long", "warn", slide, field,
                            f"'{text}' is {w:.2f}in; reference style sits under {LINE_TARGETS[budget_key]:.2f}in",
                            "rewrite_shorter", round(w, 3), LINE_TARGETS[budget_key]))
    missing = fonts.missing_glyphs(text, font_key)
    if missing:
        issues.append(Issue("missing_glyph", "error", slide, field, f"font lacks glyphs {sorted(missing)}",
                            "needs_review" if fix == "shorten_display_name" else "rewrite_copy"))
    if lint:
        for why in lint_copy(text, protected):
            issues.append(Issue("style_lint", "error", slide, field, f"{why}: '{text}'", "rewrite_copy"))


def our_take_height(headline: str, points: list[str]) -> tuple[float, list[int]]:
    W = OUR_TAKE["width"]
    total, line_counts = 0.0, []
    for (fk, pt, sp, after), texts in ((OUR_TAKE["label"], ["OUR TAKE"]), (OUR_TAKE["headline"], [headline]),
                                       (OUR_TAKE["point"], points)):
        for t in texts:
            n = len(fonts.wrap(t, fk, pt, W))
            line_counts.append(n)
            total += n * fonts.line_height_in(fk, pt, sp) + after / 72.0
    return total, line_counts[1:]


def _our_take(issues: list[Issue], c: DeckContent, protected: list[str]) -> None:
    m, name = c.market, c.company.display_name
    h, counts = our_take_height(m.our_take.headline, m.our_take.points)
    if counts[0] > OUR_TAKE["headline_max_lines"]:
        issues.append(Issue("our_take_lines", "error", 3, "market.our_take.headline",
                            f"headline wraps to {counts[0]} lines (max {OUR_TAKE['headline_max_lines']})",
                            "rewrite_shorter", counts[0], OUR_TAKE["headline_max_lines"]))
    for i, n in enumerate(counts[1:]):
        if n > OUR_TAKE["point_max_lines"]:
            issues.append(Issue("our_take_lines", "error", 3, f"market.our_take.points[{i}]",
                                f"point wraps to {n} lines (max {OUR_TAKE['point_max_lines']})", "rewrite_shorter",
                                n, OUR_TAKE["point_max_lines"]))
    if h > OUR_TAKE["max_height"]:
        longest = max([("market.our_take.headline", m.our_take.headline),
                       *((f"market.our_take.points[{i}]", p) for i, p in enumerate(m.our_take.points))],
                      key=lambda kv: len(kv[1]))[0]
        issues.append(Issue("our_take_height", "error", 3, longest,
                            f"'our take' block is {h:.2f}in tall; limit {OUR_TAKE['max_height']}in", "rewrite_shorter",
                            round(h, 3), OUR_TAKE["max_height"]))
    for f, t in (("market.our_take.headline", m.our_take.headline),
                 *((f"market.our_take.points[{i}]", p) for i, p in enumerate(m.our_take.points))):
        if "\n" in t:
            issues.append(Issue("newline", "error", 3, f, "line break not allowed here", "rewrite_copy"))
        missing = fonts.missing_glyphs(t, "bold" if f.endswith("headline") else "light")
        if missing:
            issues.append(Issue("missing_glyph", "error", 3, f, f"font lacks glyphs {sorted(missing)}", "rewrite_copy"))
        for why in lint_copy(t, protected):
            issues.append(Issue("style_lint", "error", 3, f, f"{why}: '{t}'", "rewrite_copy"))
    if not m.our_take.headline.startswith(name):
        issues.append(Issue("our_take_subject", "warn", 3, "market.our_take.headline",
                            "headline should open with the company name, as in the reference", "rewrite_copy"))


def check_text(c: DeckContent, extra_protected: list[str] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    prot = protected_terms(c, extra_protected)
    name = c.company.display_name
    # Titles are generated from the name: never linted, never rewritten.
    _line(issues, 2, "thesis.title", f"What we see in {name}", "thesis_title", prot, "shorten_display_name", lint=False)
    _line(issues, 3, "market.title", f"The opportunity for {name}", "market_title", prot, "shorten_display_name", lint=False)
    for i, p in enumerate(c.thesis):
        _line(issues, 2, f"thesis[{i}].headline", p.headline, "thesis_headline", prot)
        _line(issues, 2, f"thesis[{i}].subline", p.subline, "thesis_subline", prot)
    m = c.market
    _line(issues, 3, "market.subtitle", m.subtitle, "market_subtitle", prot)
    for field, label in (("market.y_axis.high_label", m.y_axis.high_label), ("market.y_axis.low_label", m.y_axis.low_label)):
        lines = label.split("\n")
        if len(lines) > 2:
            issues.append(Issue("label_lines", "error", 3, field, "y labels may use at most 2 lines", "rewrite_shorter"))
        for ln in lines:
            _line(issues, 3, field, ln, "y_label_line", prot)
    _line(issues, 3, "market.x_axis.low_label", m.x_axis.low_label, "x_label", prot)
    _line(issues, 3, "market.x_axis.high_label", m.x_axis.high_label, "x_label", prot)
    _line(issues, 3, "market.x_axis.title", m.x_axis.title, "axis_title", prot)
    _line(issues, 3, "market.y_axis.title", m.y_axis.title, "axis_title", prot)
    _our_take(issues, c, prot)
    return issues


def check_field(field: str, text: str, content: DeckContent, extra_protected: list[str] | None = None) -> list[Issue]:
    """Errors (no warnings) for ONE candidate value of ONE field, in the context of the rest of the deck.
    Used by the rewrite loop to pick the first of N candidates that passes."""
    data = content.model_dump()
    m = re.fullmatch(r"([a-z_.]+)(?:\[(\d+)\])?(?:\.([a-z_]+))?", field)
    if not m:
        raise ValueError(f"unknown field path {field}")
    path, idx, leaf = m.group(1).split("."), m.group(2), m.group(3)
    node = data
    for key in path[:-1]:
        node = node[key]
    if idx is None:
        node[path[-1]] = text
    elif leaf is None:
        node[path[-1]][int(idx)] = text
    else:
        node[path[-1]][int(idx)][leaf] = text
    try:
        candidate = DeckContent.model_validate(data, context={"min_competitors": 0})
    except Exception as e:  # pydantic.ValidationError (e.g. a line break) -> report as a field error
        return [Issue("invalid", "error", 0, field, str(e).splitlines()[0], "rewrite_copy")]
    return [i for i in check_text(candidate, extra_protected) if i.field == field and i.severity == "error"]


# ------------------------------------------------------------------ layout checks
def check_layout(manifest: dict, logo_metrics: dict[str, dict]) -> list[Issue]:
    issues: list[Issue] = []
    logos = [e for e in manifest["elements"] if e["kind"] == "logo" and e["slide"] == 3]
    boxes = {e["id"]: Box(e["x"], e["y"], e["w"], e["h"]) for e in logos}
    ids = list(boxes)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            g = boxes[ids[i]].gap_to(boxes[ids[j]])
            if g < MIN_LOGO_GAP:
                issues.append(Issue("logo_overlap", "error", 3, f"{ids[i]}|{ids[j]}",
                                    f"logos {g:.3f}in apart (min {MIN_LOGO_GAP})", "relayout", round(g, 3), MIN_LOGO_GAP))
    for e in logos:
        b, cell = boxes[e["id"]], CELLS[tuple(e["cell"])]
        if b.x < cell["x0"] or b.x + b.w > cell["x1"] or b.y < cell["y0"] or b.y + b.h > cell["y1"]:
            issues.append(Issue("logo_out_of_cell", "error", 3, e["id"], f"logo box leaves its quadrant {e['cell']}", "relayout"))
        if b.x <= PLOT["x0"] + 0.05 or b.y + b.h >= PLOT["y1"] - 0.05:
            issues.append(Issue("logo_on_axis", "error", 3, e["id"], "logo touches an axis line", "relayout"))
        if b.h < MIN_LOGO_H:
            issues.append(Issue("logo_too_small", "error", 3, e["id"],
                                f"logo only {b.h:.3f}in tall (min {MIN_LOGO_H}); too wide for its slot", "swap_logo",
                                round(b.h, 3), MIN_LOGO_H))
    for label, met in logo_metrics.items():
        if not met.get("ok", False):
            issues.append(Issue("logo_quality", "error", 3, f"logo:{label}", "; ".join(met.get("problems", [])), "swap_logo"))
    return issues


# ------------------------------------------------------------------ package integrity
def check_integrity(out_pptx: Path, content: DeckContent, template: Path = TEMPLATE_PATH) -> list[Issue]:
    issues: list[Issue] = []
    with zipfile.ZipFile(template) as zt, zipfile.ZipFile(out_pptx) as zo:
        tnames, onames = set(zt.namelist()), set(zo.namelist())
        for n in FIXED_SLIDES:
            for part in (f"ppt/slides/slide{n + 1}.xml", f"ppt/slides/_rels/slide{n + 1}.xml.rels"):
                if part in tnames and (part not in onames or zt.read(part) != zo.read(part)):
                    issues.append(Issue("fixed_slide_changed", "error", n + 1, part, "slide 4-10 content differs from template", "rebuild"))
        # Media only reachable through slide 3's orphaned relationships in the template (Surgence / Palantir
        # logos from another deck) must not ship.
        h = lambda b: hashlib.sha1(b).hexdigest()  # noqa: E731
        orphan_hashes = {h(zt.read(f"ppt/media/{f}")) for f in ("image5.png", "image6.png") if f"ppt/media/{f}" in tnames}
        leftover = [n for n in onames if n.startswith("ppt/media/") and h(zo.read(n)) in orphan_hashes]
        if leftover:
            issues.append(Issue("orphan_media", "error", 3, ",".join(leftover), "unused third-party logos still packaged", "rebuild"))

    prs = Presentation(str(out_pptx))
    allowed_text = content.model_dump_json()
    for idx in range(3):
        s = prs.slides[idx]
        xml = s.part._element.xml
        # PowerPoint shows "needs repair" for duplicate shape ids or dangling relationship ids.
        # (mc:Fallback legitimately repeats the id of its mc:Choice twin - e.g. the think-cell object on slide 1)
        ids = re.findall(r'<p:cNvPr id="(\d+)"', re.sub(r"<mc:Fallback>.*?</mc:Fallback>", "", xml, flags=re.S))
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            issues.append(Issue("duplicate_shape_id", "error", idx + 1, ",".join(sorted(dupes)), "duplicate cNvPr ids", "rebuild"))
        for rid in set(re.findall(r'r:(?:embed|id|link)="(rId\d+)"', xml)):
            if rid not in s.part.rels:
                issues.append(Issue("dangling_rel", "error", idx + 1, rid, "relationship id has no target", "rebuild"))
        for face in set(re.findall(r'<a:latin typeface="([^"]+)"', xml)):
            if face not in ALLOWED_TYPEFACES:
                issues.append(Issue("foreign_font", "error", idx + 1, face, "typeface not in the template set", "rebuild"))
        text = " ".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame)
        for tok in TEMPLATE_LEAK_TOKENS:
            if tok in text and tok not in allowed_text:
                issues.append(Issue("template_leak", "error", idx + 1, tok, f"reference-deck text '{tok}' still on slide", "rebuild"))
    return issues


def run_all(out_pptx: Path, content: DeckContent, manifest: dict, logo_metrics: dict[str, dict],
            extra_protected: list[str] | None = None, extra: dict | None = None) -> list[Issue]:
    """Built-in checks, then every check registered in recur_deck/checks/ (see checks.register)."""
    from . import checks
    return (check_text(content, extra_protected) + check_layout(manifest, logo_metrics)
            + check_integrity(out_pptx, content) + checks.run_registered(content, manifest, logo_metrics, extra or {}))
