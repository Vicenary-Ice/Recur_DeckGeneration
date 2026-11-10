"""Offline end-to-end run of the deterministic half of the pipeline (no network, no AI):

    content.json + raw logo candidates  ->  logos -> build -> QA (layer A) -> render -> annotated PNGs

Usage:
    python -m recur_deck.offline fixtures/ai2000 --out out/ai2000

Fixture layout:
    fixtures/<case>/content.json                 # a DeckContent JSON (what the AI layer will produce)
    fixtures/<case>/logos/<slug>/<n>_*.{svg,png,jpg,webp,ico}   # candidates, tried in filename order
The live pipeline replaces the fixture folders with Brandfetch / site-scrape downloads but calls the
exact same functions.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import build, qa, render
from .logos import file_stem, process_logo, save, slug, typeset_wordmark
from .models import DeckContent


def resolve_logo(name: str, candidates: list[Path], out_dir: Path) -> tuple[Path, dict]:
    """Try each candidate in order; first one that passes the checks wins; else typeset fallback."""
    tried = []
    for cand in candidates:
        # fixture convention: "<n>_<source>_fordark.<ext>" marks a logo designed for dark backgrounds
        img, met = process_logo(cand.read_bytes(), designed_for_dark="fordark" in cand.stem)
        tried.append({"source": cand.name, **met.as_dict()})
        if met.ok:
            return save(img, out_dir / f"{slug(name)}.png"), {**met.as_dict(), "source": cand.name, "tried": tried}
    img, met = typeset_wordmark(name)
    return save(img, out_dir / f"{slug(name)}.png"), {**met.as_dict(), "source": "typeset", "tried": tried}


def run(case_dir: Path, out_dir: Path, render_pngs: bool = True) -> dict:
    t0 = time.time()
    content = DeckContent.model_validate_json((case_dir / "content.json").read_text())
    logo_dir = out_dir / "logos"
    cand_root = case_dir / "logos"

    def cands(n: str) -> list[Path]:
        d = cand_root / slug(n)
        return sorted(p for p in d.iterdir() if p.is_file()) if d.exists() else []

    name = content.company.display_name
    target_png, target_meta = resolve_logo(name, cands(name), logo_dir)
    comp_pngs, metrics = {}, {name: target_meta}
    for c in content.market.competitors:
        comp_pngs[c.name], metrics[c.name] = resolve_logo(c.name, cands(c.name), logo_dir)

    out_pptx = out_dir / f"{file_stem(name)}.pptx"
    typeset = frozenset(k for k, v in metrics.items() if v["source"] == "typeset")
    manifest = build.build_deck(content, target_png, comp_pngs, out_pptx, typeset=typeset)
    issues = qa.run_all(out_pptx, content, manifest, metrics)

    report = {"company": name, "pptx": str(out_pptx), "seconds_build": round(time.time() - t0, 2),
              "logos": {k: {kk: v[kk] for kk in ("source", "ok", "aspect", "ink_coverage", "problems")} for k, v in metrics.items()},
              "issues": [i.as_dict() for i in issues]}
    if render_pngs:
        t1 = time.time()
        render.install_fonts()
        pdf = render.to_pdf(out_pptx, out_dir)
        pngs = render.to_pngs(pdf, out_dir / "png")
        for i, p in enumerate(pngs, 1):
            els = [e for e in manifest["elements"] if e["slide"] == i]
            if els:
                render.annotate(p, els, p.with_name(f"slide{i}_annotated.png"))
        report.update(pdf=str(pdf), pngs=[str(p) for p in pngs], seconds_render=round(time.time() - t1, 2))
    (out_dir / "qa_report.json").write_text(json.dumps(report, indent=2))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args(argv)
    rep = run(a.case_dir, a.out, not a.no_render)
    errs = [i for i in rep["issues"] if i["severity"] == "error"]
    print(json.dumps({k: rep[k] for k in rep if k != "issues"}, indent=2))
    for i in rep["issues"]:
        print(f"[{i['severity'].upper()}] slide {i['slide']} {i['field']}: {i['message']} -> {i['fix']}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
