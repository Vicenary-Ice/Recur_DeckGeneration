"""Print-grade logos (Agent 4), run in Layer A after layout:

  logo_low_res            a raster logo prints under 300 dpi on the cover or 200 dpi on the map
                          (dpi = native source pixels / printed inches; vector and typeset logos are exempt)
  logo_tagline_microtext  a lockup's tagline line prints under 5pt

Both carry fix `swap_logo` on field "logo:<name>" (the QA loop crops a tagline first, then tries the next
candidate, then typesets the short name).

The target's own cover logo: replacing a founder's real logo with typeset type on the cover of a letter to that
founder is a bigger change than a slightly soft logo, so between TARGET_COVER_FLOOR_DPI (default 200, env
RECUR_TARGET_COVER_MIN_DPI) and 300 dpi the issue is a warning; below the floor it is an error like any other.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..models import DeckContent
from ..qa import Issue
from . import register

COVER_MIN_DPI, MAP_MIN_DPI = 300, 200


def target_cover_floor() -> float:
    try:
        return float(os.environ.get("RECUR_TARGET_COVER_MIN_DPI") or 200)
    except ValueError:
        return 200.0


def _placed(manifest: dict) -> list[dict]:
    return [e for e in manifest.get("elements", []) if e.get("kind") == "logo"]


def print_dpi_issues(manifest: dict, logo_metrics: dict) -> list[Issue]:
    out: list[Issue] = []
    floor = target_cover_floor()
    for e in _placed(manifest):
        name = e.get("label")
        lm = logo_metrics.get(name) or {}
        native = lm.get("native_w")
        if lm.get("typeset") or not native or not e.get("w"):
            continue
        dpi = native / e["w"]
        cover = e["id"] == "cover_logo"
        limit = COVER_MIN_DPI if cover else MAP_MIN_DPI
        if dpi >= limit:
            continue
        sev = "warn" if cover and dpi >= floor else "error"
        out.append(Issue("logo_low_res", sev, e["slide"], f"logo:{name}",
                         f"{name} {'cover' if cover else 'map'} logo prints at {dpi:.0f} dpi ({native} source pixels over "
                         f"{e['w']:.2f}in; min {limit})" + ("; kept: the real logo reads better than a typeset name"
                                                          if sev == "warn" else ""),
                         "swap_logo", round(dpi), limit))
    return out


def _png(lm: dict, run_dir: Path | None) -> Path | None:
    p = Path(lm.get("png") or "")
    if p.is_file():
        return p
    if run_dir is not None and p.name and (Path(run_dir) / "logos" / p.name).is_file():
        return Path(run_dir) / "logos" / p.name  # a run folder copied elsewhere (fixtures)
    return None


def tagline_issues(manifest: dict, logo_metrics: dict, run_dir: Path | None = None) -> list[Issue]:
    from ..stages.logo_sources import tagline_issue
    out: list[Issue] = []
    seen: set[str] = set()
    # smallest placement first: the map instance of the target is smaller than its cover
    for e in sorted(_placed(manifest), key=lambda e: e.get("h") or 0):
        name = e.get("label")
        lm = logo_metrics.get(name) or {}
        if name in seen or lm.get("typeset"):
            continue
        png = _png(lm, run_dir)
        if png is None:
            continue
        issue = tagline_issue(png, e["h"])
        if issue is not None:
            seen.add(name)
            issue.slide, issue.field = e["slide"], f"logo:{name}"
            issue.message = f"{name}: {issue.message}"
            out.append(issue)
    return out


@register
def logo_print_checks(content: DeckContent, manifest: dict, logo_metrics: dict, extra: dict) -> list[Issue]:
    logos = extra.get("logos") or logo_metrics
    return print_dpi_issues(manifest, logos) + tagline_issues(manifest, logos, extra.get("run_dir"))
