"""Layer B of the QA loop: render slides 1-3, annotate every placed element, and ask a vision model for
defects a person would notice on paper. Defects name manifest element ids, so each maps to one fix."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .. import render
from ..spec import KIT_ROOT
from .base import Ctx, prompt

log = logging.getLogger("recur_deck.vision_qa")
BASELINE = KIT_ROOT / "assets" / "baseline"


class Defect(BaseModel):
    slide: int
    element_id: str | None
    type: Literal["text_overflow", "text_overlap", "logo_illegible", "logo_wrong_company",
                  "logo_too_small", "logo_too_large", "logo_overlap", "logo_misaligned",
                  "visual_imbalance", "off_brand", "typo_or_grammar", "factual_mismatch", "other"]
    severity: Literal["major", "minor"]
    description: str
    suggested_fix: Literal["rewrite_shorter", "rewrite_copy", "swap_logo", "relayout", "none"]


class VisionVerdict(BaseModel):
    passed: bool
    defects: list[Defect]
    summary: str


def render_slides(pptx: Path, run_dir: Path, manifest: dict) -> tuple[Path, list[Path], list[Path]]:
    pdf = render.to_pdf(pptx, run_dir)
    pngs = render.to_pngs(pdf, run_dir / "png")
    annotated = []
    for i, p in enumerate(pngs, 1):
        els = [e for e in manifest["elements"] if e["slide"] == i]
        annotated.append(render.annotate(p, els, p.with_name(f"slide{i}_annotated.png")))
    return pdf, pngs, annotated


def element_map(manifest: dict) -> list[dict]:
    """Numbered like render.annotate draws them: per slide, in manifest order, starting at 1."""
    out = []
    for s in (1, 2, 3):
        for n, e in enumerate((e for e in manifest["elements"] if e["slide"] == s), 1):
            out.append({"slide": s, "n": n, "id": e["id"], "kind": e["kind"],
                        **({"field": e["field"]} if e.get("field") else {}), **({"label": e["label"]} if e.get("label") else {}),
                        **({"cell": e["cell"]} if e.get("cell") else {})})
    return out


def check(ctx: Ctx, pngs: list[Path], annotated: list[Path], manifest: dict, content_json: dict, label: str) -> VisionVerdict:
    images = []
    for i, (p, a) in enumerate(zip(pngs, annotated), 1):
        images.append((f"Slide {i} - our render", p.read_bytes()))
        images.append((f"Slide {i} - our render with the numbered element map", a.read_bytes()))
        base = BASELINE / f"slide{i}.png"
        if base.exists():
            images.append((f"Slide {i} - BASELINE (template, other company)", base.read_bytes()))
    v = ctx.claude.ask(ctx.settings.model_vision, prompt("vision_qa"),
                       [("elements", element_map(manifest)), ("content", content_json)], VisionVerdict,
                       images=images, effort="medium", label=label, fallbacks=ctx.judge_fallbacks())
    v.passed = not any(d.severity == "major" for d in v.defects)  # the definition: no major defects
    return v
