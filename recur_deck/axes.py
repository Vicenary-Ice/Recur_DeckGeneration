"""Ordinal axis kinds for the market map: a fixed list, each with a defined low end (left / bottom) and high end
(right / top). Shared by the market graph (reading decks written before axes had a kind) and the map checks.

- `infer_kind(title, low, high)`: the kind an axis title names, or None ("Core buyer" is not a scale).
- `read_side(kind, texts)`: which end of a kind a set of sentences points to, from plain-word cues
  ("built for the fashion industry" -> industry_depth high). None when the cues are absent or balanced.
  Only kinds with `derive=True` have cues reliable enough to read a placement from prose.
"""
from __future__ import annotations

import re
from typing import Iterable

AXIS_KINDS: dict[str, dict] = {
    "customer_size": {
        "low": "small / single-site", "high": "large / multi-entity / enterprise", "derive": True,
        "titles": [r"customer size", r"company size", r"business size", r"brand size", r"client size", r"firm size",
                   r"customer scale", r"\bsize\b", r"\bscale\b"],
        "high_cues": [r"\benterprise", r"\blarge\b", r"\bglobal\b", r"\bmulti[- ]?entity", r"\bmultinational",
                      r"\bbig (brands|companies|firms|programs|retailers)", r"\bfashion houses?\b", r"\bupmarket\b",
                      r"\bmulti[- ]?(site|location|brand)s?\b", r"\b(group|groups)\b"],
        "low_cues": [r"\bsmall\b", r"\bsmb\b", r"\bstart[- ]?ups?\b", r"\bemerging\b", r"\bboutique",
                     r"\bsingle[- ]?(site|brand|location|entity)s?\b", r"\bgrowing\b", r"\bgrowth[- ]stage",
                     r"\bindependent\b", r"\bsolo\b"],
    },
    "customer_complexity": {
        "low": "simple programs", "high": "complex programs (many retailers, entities, compliance)", "derive": False,
        "titles": [r"complexity", r"program depth", r"operational depth"],
        "high_cues": [r"\bcomplex", r"\bmany retailers", r"\bcompliance", r"\bmulti[- ]?entity", r"\bchargebacks?",
                      r"\bregulated", r"\bmulti[- ]?channel"],
        "low_cues": [r"\bsimple\b", r"\bbasic\b", r"\bsingle[- ]channel", r"\bstraightforward"],
    },
    "product_scope": {
        "low": "point tool / ops only", "high": "full suite (incl. financials, WMS...)", "derive": False,
        "titles": [r"product scope", r"\bscope\b", r"breadth", r"coverage", r"suite", r"functional", r"modules?"],
        "high_cues": [r"\bfinancials?\b", r"\baccounting\b", r"\bgeneral ledger", r"\bfull[- ]suite", r"\bend[- ]to[- ]end",
                      r"\ball[- ]in[- ]one", r"\bwms\b", r"\bwarehouse management", r"\bthe books\b", r"\bto financials\b",
                      r"\bbuilt[- ]in\b"],
        "low_cues": [r"\bops only\b", r"\bpoint (tool|solution)", r"\b(orders|inventory|stock) only\b",
                     r"\bsyncs? (with|to) quickbooks", r"\bintegrates? with quickbooks", r"\bstandalone\b"],
    },
    "industry_depth": {
        "low": "generic / horizontal", "high": "built for this industry", "derive": True,
        "titles": [r"industry depth", r"industry focus", r"industry fit", r"vertical (depth|focus|fit)", r"speciali[sz]",
                   r"\bdepth\b", r"\bfocus\b", r"\bverticali"],
        "high_cues": [r"\bindustry[- ]specific", r"\bpurpose[- ]built", r"\bspecially tailored", r"\btailored (solutions? )?for",
                      r"\bbuilt (exclusively |only |specifically |just )?for (the )?\w+( \w+)? (industry|industries|sectors?|brands|businesses|companies)",
                      r"\bdesigned (specifically |exclusively )?(for|around) (the )?\w+( and \w+)? (industry|operations|brands|businesses)",
                      r"\b(exclusively|only) for\b", r"\bbuilt (exclusively|only) for\b", r"\b\w+[- ]focused\b",
                      r"\bfocuses on\b", r"\bdedicated to\b", r"\bbuilt for (apparel|fashion|footwear)\b",
                      r"\b(apparel|fashion) built\b"],
        "low_cues": [r"\bhorizontal\b", r"\bgeneral[- ]purpose", r"\bgeneric\b", r"\bbroad (cloud )?(erp|platform|suite)",
                     r"\bany industry", r"\bacross (many |multiple |all |most )?industries", r"\ball industries",
                     r"\bgeneral (cloud )?erp", r"\bindustry[- ]agnostic", r"\bmulti[- ]industry", r"\bgeneral erps?\b"],
    },
    "deployment_modernity": {
        "low": "desktop / on-prem / legacy", "high": "cloud-native", "derive": True,
        "titles": [r"deployment", r"modernity", r"technology", r"tech stack", r"architecture", r"\bplatform age",
                   r"\bcloud\b"],
        "high_cues": [r"\bcloud[- ]native", r"\bcloud[- ]based", r"\bsaas\b", r"\bbrowser[- ]based", r"\bweb[- ]based",
                      r"\bcloud (erp|platform|software)"],
        "low_cues": [r"\bon[- ]prem", r"\bdesktop\b", r"\blegacy\b", r"\bclient[- ]server", r"\bas/?400\b", r"\bibm i\b",
                     r"\binstalled (locally|on)"],
    },
    "segment_focus": {
        "low": "other segments", "high": "the target's segment", "derive": False,
        "titles": [r"segment", r"\bniche\b", r"\bmarket focus\b"],
        "high_cues": [], "low_cues": [],
    },
}
KINDS = tuple(AXIS_KINDS)
# A title like "Industry focus" must be read as industry_depth before "focus" alone is considered.
_TITLE_ORDER = ["industry_depth", "customer_complexity", "deployment_modernity", "customer_size", "product_scope",
                "segment_focus"]


def infer_kind(title: str, low_label: str = "", high_label: str = "") -> str | None:
    """The kind an axis title names ('Customer size' -> customer_size), else None. Labels are not used to guess
    a kind: an axis whose title names no scale ('Core buyer') is exactly what the map checks must flag."""
    t = (title or "").lower()
    for kind in _TITLE_ORDER:
        if any(re.search(p, t) for p in AXIS_KINDS[kind]["titles"]):
            return kind
    return None


def cue_hits(kind: str, text: str) -> tuple[int, int]:
    """(high cue count, low cue count) in one text."""
    k = AXIS_KINDS.get(kind) or {}
    t = (text or "").lower()
    hi = sum(1 for p in k.get("high_cues", []) if re.search(p, t))
    lo = sum(1 for p in k.get("low_cues", []) if re.search(p, t))
    return hi, lo


def read_side(kind: str, texts: Iterable[str]) -> str | None:
    """'high' / 'low' when the cues across `texts` clearly point one way, else None."""
    hi = lo = 0
    for t in texts:
        h, l = cue_hits(kind, t)
        hi, lo = hi + h, lo + l
    if hi > lo:
        return "high"
    if lo > hi:
        return "low"
    return None


def side_of(axis_key: str, position: str) -> str:
    """('x', 'right') -> 'high'; ('y', 'bottom') -> 'low'."""
    return "high" if position in ("right", "top") else "low"
