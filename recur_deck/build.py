"""Fill slides 1-3 of the Recur template in place. Slides 4-10 are never touched.

Inputs are already-validated content (models.DeckContent) and already-whitened logo PNGs.
This module performs no network calls and no AI calls, so it is fully deterministic.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.oxml.ns import qn
from pptx.util import Emu

from . import fonts, layout
from .models import DeckContent
from .spec import (COVER, COVER_SHAPES, LINE_BUDGETS, MARKET, MARKET_SHAPES, OUR_TAKE, TEMPLATE_PATH, THESIS,
                   THESIS_SHAPES, emu, inches)


# ------------------------------------------------------------------ xml helpers
def shape_by_id(slide, shape_id: int):
    for sh in slide.shapes:
        if sh.shape_id == shape_id:
            return sh
    raise KeyError(f"shape id {shape_id} not found on slide {slide.slide_id} - template changed?")


def set_paragraphs(shape, texts: list[str], start: int = 0) -> None:
    """Replace paragraph texts from index `start`, keeping each paragraph's first-run formatting.
    Adds paragraphs (cloned from the last one) or removes extras so exactly start+len(texts) remain."""
    txBody = shape.text_frame._txBody
    paras = txBody.findall(qn("a:p"))
    need = start + len(texts)
    while len(paras) < need:
        clone = copy.deepcopy(paras[-1])
        paras[-1].addnext(clone)
        paras = txBody.findall(qn("a:p"))
    for extra in paras[need:]:
        txBody.remove(extra)
    paras = txBody.findall(qn("a:p"))
    for p, text in zip(paras[start:need], texts):
        runs = p.findall(qn("a:r"))
        if not runs:
            raise ValueError(f"paragraph without a run in shape {shape.shape_id}")
        for r in runs[1:]:
            p.remove(r)
        for br in p.findall(qn("a:br")):
            p.remove(br)
        runs[0].find(qn("a:t")).text = text


def replace_picture(slide, old_shape, png: Path, box: layout.Box, name: str):
    """Insert a new picture at `box` in the old picture's z-position, then delete the old one."""
    pic = slide.shapes.add_picture(str(png), Emu(emu(box.x)), Emu(emu(box.y)), Emu(emu(box.w)), Emu(emu(box.h)))
    pic.name = name
    pic._element.nvPicPr.cNvPr.set("descr", name)
    if old_shape is not None:
        old_shape._element.addprevious(pic._element)
        old_shape._element.getparent().remove(old_shape._element)
    return pic


def drop_orphan_media(slide) -> list[str]:
    """Remove image/hdphoto relationships no longer referenced by the slide XML (stops old logos,
    e.g. leftover Surgence/Palantir files in the template, from shipping inside the deck)."""
    xml = slide.part._element.xml
    dropped = []
    for rId, rel in list(slide.part.rels.items()):
        if rel.reltype.endswith(("/image", "/hdphoto")) and f'"{rId}"' not in xml:
            slide.part.drop_rel(rId)
            dropped.append(rel.target_ref)
    return dropped


def make_glow_png(w_in: float, h_in: float, out: Path, dpi: int = 300) -> Path:
    """Bitmap twin of the template's 'Target glow' ellipse (path gradient #6FA8DC, alpha 44% -> 12% @55%
    -> 0% at the edge). LibreOffice cannot draw DrawingML path gradients (it shows rectangular seams),
    so a PNG keeps the PDF/print output identical to PowerPoint."""
    import numpy as np
    W, H = int(w_in * dpi), int(h_in * dpi)
    y, x = np.mgrid[0:H, 0:W]
    r = np.sqrt(((x - W / 2) / (W / 2)) ** 2 + ((y - H / 2) / (H / 2)) ** 2)
    alpha = np.interp(r, [0.0, 0.55, 1.0], [0.44, 0.12, 0.0])
    arr = np.zeros((H, W, 4), dtype=np.uint8)
    arr[..., 0], arr[..., 1], arr[..., 2] = 0x6F, 0xA8, 0xDC
    arr[..., 3] = (alpha * 255).astype(np.uint8)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, "RGBA").save(out)
    return out


def _text_el(slide_no: int, shape, field: str, lines: list[tuple[str, str]], inset: float = 0.0,
             align: str = "l") -> dict:
    """Manifest entry for a text field: the box the text actually occupies (measured), so the vision check
    can point at it and the orchestrator can map a visual defect back to the content field."""
    x, y, w, h = (inches(v) for v in (shape.left, shape.top, shape.width, shape.height))
    widths = [fonts.text_width_in(t, LINE_BUDGETS[k][0], LINE_BUDGETS[k][1]) for t, k in lines]
    tw = max(widths) if widths else w
    if align == "l":
        bx = x + inset
    elif align == "r":
        bx = x + w - inset - tw
    else:
        bx = x + (w - tw) / 2
    return {"slide": slide_no, "id": field, "kind": "text", "field": field, "x": bx, "y": y, "w": tw, "h": h}


def _aspect(png: Path) -> float:
    with Image.open(png) as im:
        return im.width / im.height


# ------------------------------------------------------------------ main entry
def build_deck(content: DeckContent, target_logo: Path, competitor_logos: dict[str, Path], out_path: Path,
               template: Path = TEMPLATE_PATH, typeset: frozenset[str] = frozenset(),
               typeset_h: dict[str, float] | None = None) -> dict:
    """Write the finished .pptx and return a layout manifest used by qa.py and the vision check.
    `typeset`: names (target and/or competitors) whose logo is a typeset fallback.
    `typeset_h`: {name: text height in} for typeset map logos the QA loop resized."""
    prs = Presentation(str(template))
    name = content.company.display_name
    manifest: dict = {"company": name, "elements": []}

    # ---- slide 1: cover
    s1 = prs.slides[COVER]
    box = layout.cover_logo_box(_aspect(target_logo), typeset=name in typeset)
    replace_picture(s1, shape_by_id(s1, COVER_SHAPES["old_logo"]), target_logo, box, f"logo:{name}")
    manifest["elements"].append({"slide": 1, "id": "cover_logo", "kind": "logo", "label": name, **asdict(box)})
    drop_orphan_media(s1)

    # ---- slide 2: thesis
    s2 = prs.slides[THESIS]
    t2 = shape_by_id(s2, THESIS_SHAPES["title"])
    set_paragraphs(t2, [f"What we see in {name}"])
    manifest["elements"].append(_text_el(2, t2, "thesis.title", [(f"What we see in {name}", "thesis_title")], 0.1))
    for i, (sid, pt) in enumerate(zip(THESIS_SHAPES["points"], content.thesis)):
        sh = shape_by_id(s2, sid)
        set_paragraphs(sh, [pt.headline, pt.subline])
        manifest["elements"].append(_text_el(2, sh, f"thesis[{i}]", [(pt.headline, "thesis_headline"),
                                                                     (pt.subline, "thesis_subline")]))

    # ---- slide 3: market map
    s3 = prs.slides[MARKET]
    m = content.market
    S = MARKET_SHAPES
    set_paragraphs(shape_by_id(s3, S["title"]), [f"The opportunity for {name}"])
    set_paragraphs(shape_by_id(s3, S["subtitle"]), [m.subtitle])
    set_paragraphs(shape_by_id(s3, S["y_top_label"]), m.y_axis.high_label.split("\n"))
    set_paragraphs(shape_by_id(s3, S["y_bottom_label"]), m.y_axis.low_label.split("\n"))
    set_paragraphs(shape_by_id(s3, S["x_left_label"]), [m.x_axis.low_label])
    set_paragraphs(shape_by_id(s3, S["x_right_label"]), [m.x_axis.high_label])
    set_paragraphs(shape_by_id(s3, S["y_title"]), [m.y_axis.title])
    set_paragraphs(shape_by_id(s3, S["x_title"]), [m.x_axis.title])
    set_paragraphs(shape_by_id(s3, S["our_take"]), [m.our_take.headline, *m.our_take.points], start=1)
    for field, sid, lines, inset, align in (
            ("market.title", S["title"], [(f"The opportunity for {name}", "market_title")], 0.1, "l"),
            ("market.subtitle", S["subtitle"], [(m.subtitle, "market_subtitle")], 0.1, "l"),
            ("market.y_axis.high_label", S["y_top_label"], [(t, "y_label_line") for t in m.y_axis.high_label.split("\n")], 0, "r"),
            ("market.y_axis.low_label", S["y_bottom_label"], [(t, "y_label_line") for t in m.y_axis.low_label.split("\n")], 0, "r"),
            ("market.x_axis.low_label", S["x_left_label"], [(m.x_axis.low_label, "x_label")], 0.1, "c"),
            ("market.x_axis.high_label", S["x_right_label"], [(m.x_axis.high_label, "x_label")], 0.1, "c")):
        manifest["elements"].append(_text_el(3, shape_by_id(s3, sid), field, lines, inset, align))
    from .qa import our_take_height  # local import: qa imports build-free modules only
    ot = shape_by_id(s3, S["our_take"])
    manifest["elements"].append({"slide": 3, "id": "market.our_take", "kind": "text", "field": "market.our_take",
                                 "x": inches(ot.left), "y": inches(ot.top), "w": OUR_TAKE["width"],
                                 "h": our_take_height(m.our_take.headline, m.our_take.points)[0]})

    comps = [(c.name, c.x, c.y, _aspect(competitor_logos[c.name])) for c in m.competitors]
    own_h = {(layout.TARGET_KEY if n == name else n): h for n, h in (typeset_h or {}).items()}
    boxes, tbox, gbox = layout.market_layout(_aspect(target_logo), comps, typeset=typeset,
                                             target_typeset=name in typeset, typeset_h=own_h)

    glow_png = make_glow_png(gbox.w, gbox.h, out_path.parent / "logos" / "_glow.png")
    replace_picture(s3, shape_by_id(s3, S["glow"]), glow_png, gbox, "target-glow")

    old = [shape_by_id(s3, sid) for sid in S["old_logos"]]
    anchor = old[-1]  # new logos go where the old ones sat in z-order (above the glow)
    for c in m.competitors:
        b = boxes[c.name]
        pic = replace_picture(s3, None, competitor_logos[c.name], b, f"logo:{c.name}")
        anchor._element.addnext(pic._element)
        anchor = pic
        manifest["elements"].append({"slide": 3, "id": f"logo:{c.name}", "kind": "logo", "label": c.name,
                                     "cell": [c.x, c.y], **asdict(b)})
    tpic = replace_picture(s3, None, target_logo, tbox, f"logo:{name}")
    anchor._element.addnext(tpic._element)
    manifest["elements"].append({"slide": 3, "id": "target_logo", "kind": "logo", "label": name,
                                 "cell": ["right", "top"], **asdict(tbox)})
    manifest["elements"].append({"slide": 3, "id": "glow", "kind": "glow", "label": "glow", **asdict(gbox)})
    for o in old:
        o._element.getparent().remove(o._element)
    manifest["dropped_media"] = drop_orphan_media(s3)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    (out_path.with_suffix(".manifest.json")).write_text(json.dumps(manifest, indent=2))
    return manifest
