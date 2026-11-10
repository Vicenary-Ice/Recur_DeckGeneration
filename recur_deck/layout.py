"""Deterministic placement of logos: cover logo box and market-map slots."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .spec import CELLS, COVER_LOGO, GLOW_SIZE, MAP_LOGO, SLOT_DX, SLOT_DY, TARGET_CELL


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def gap_to(self, o: "Box") -> float:
        """Separation between boxes (negative = overlap depth)."""
        dx = max(o.x - (self.x + self.w), self.x - (o.x + o.w))
        dy = max(o.y - (self.y + self.h), self.y - (o.y + o.h))
        return max(dx, dy)


def size_for_area(aspect: float, area: float, max_w: float, max_h: float, min_h: float) -> tuple[float, float]:
    """Width/height (in) so the bounding box has ~`area` sq in, clamped. Keeps every logo the same
    visual weight regardless of aspect ratio."""
    h = math.sqrt(area / aspect)
    h = min(max(h, min_h), max_h)
    w = h * aspect
    if w > max_w:
        w, h = max_w, max_w / aspect
    return w, h


def size_typeset(aspect: float, height: float, max_w: float) -> tuple[float, float]:
    """Typeset fallbacks are sized by text height, not area, so short and long names read at the same
    font size (area sizing makes 'Loxo' huge and 'Recruit CRM' tiny)."""
    w = height * aspect
    if w > max_w:
        w, height = max_w, max_w / aspect
    return w, height


def cover_logo_box(aspect: float, typeset: bool = False) -> Box:
    c = COVER_LOGO
    if typeset:
        w, h = size_typeset(aspect, c["typeset_h"], c["max_w"])
    else:
        w, h = size_for_area(aspect, c["target_area"], c["max_w"], c["max_h"], c["min_h"])
    return Box(c["left"], c["center_y"] - h / 2, w, h)


def _slots(n: int, cx: float, cy: float) -> list[tuple[float, float]]:
    if n == 1:
        return [(cx, cy)]
    if n == 2:
        return [(cx - SLOT_DX, cy), (cx + SLOT_DX, cy)]
    if n == 3:
        return [(cx - SLOT_DX, cy - SLOT_DY), (cx + SLOT_DX, cy - SLOT_DY), (cx, cy + SLOT_DY)]
    raise ValueError("max 3 logos per cell")


TARGET_KEY = "__target__"


def market_layout(target_aspect: float, competitors: list[tuple[str, str, str, float]],
                  typeset: frozenset[str] = frozenset(), target_typeset: bool = False,
                  typeset_h: dict[str, float] | None = None):
    """competitors: [(name, x, y, aspect)]. Returns ({name: Box}, target Box, glow Box).
    `typeset` names the logos that are typeset fallbacks (sized by height, see size_typeset).
    `typeset_h` gives individual typeset logos their own text height (QA-loop resize; the target's key is
    TARGET_KEY); it replaces the shared height for that logo only.

    The target always occupies the right-hand slot of the top-right cell (top-right slot when the
    cell holds 3), mirroring the reference deck.
    """
    placed: dict[str, Box] = {}
    by_cell: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for name, x, y, aspect in competitors:
        by_cell.setdefault((x, y), []).append((name, aspect))

    # One shared text height for every typeset fallback on the slide, so they read as the same font size.
    # Outliers (> 8.5:1, single very long words) are excluded so one name can't shrink every other one;
    # they get width-capped individually and qa flags them if they fall under MIN_LOGO_H.
    own_h = typeset_h or {}
    ts_aspects = [a for n, _, _, a in competitors if n in typeset and n not in own_h] + \
                 ([target_aspect] if target_typeset and TARGET_KEY not in own_h else [])
    ts_h = min([MAP_LOGO["typeset_h"], *(MAP_LOGO["max_w"] / a for a in ts_aspects if a <= 8.5)])
    ts_h = max(ts_h, 0.11)

    target_box = None
    for key, cell in CELLS.items():
        members = list(by_cell.get(key, []))
        if key == TARGET_CELL:
            # target goes into slot index 1 (right / top-right)
            members.insert(min(1, len(members)), (TARGET_KEY, target_aspect))
        if not members:
            continue
        for (name, aspect), (sx, sy) in zip(members, _slots(len(members), cell["cx"], cell["cy"])):
            is_typeset = target_typeset if name == TARGET_KEY else name in typeset
            if is_typeset:
                w, h = size_typeset(aspect, own_h.get(name, ts_h), MAP_LOGO["max_w"])
            else:
                w, h = size_for_area(aspect, MAP_LOGO["target_area"], MAP_LOGO["max_w"], MAP_LOGO["max_h"], MAP_LOGO["min_h"])
            box = Box(sx - w / 2, sy - h / 2, w, h)
            if name == TARGET_KEY:
                target_box = box
            else:
                placed[name] = box
    gw, gh = GLOW_SIZE
    glow = Box(target_box.cx - gw / 2, target_box.cy - gh / 2, gw, gh)
    return placed, target_box, glow
