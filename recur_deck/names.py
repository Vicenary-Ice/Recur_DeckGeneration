"""Short brand names for typeset logo fallbacks: what the company's logo would say, never a bracketed full name.

    brand_short_name("Sync (iSync Solutions)")  -> "Sync"
    brand_short_name("Aptean Apparel ERP")      -> "Aptean"
    brand_short_name("GCS Software, LLC")       -> "GCS"
    brand_short_name("Recruit CRM")             -> "Recruit CRM"   (CRM is part of that brand, not a suffix)
"""
from __future__ import annotations

import re

LEGAL = {"inc", "incorporated", "llc", "l.l.c", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh", "plc",
         "pty", "sa", "sas", "bv", "ag", "llp", "lp", "group"}
GENERIC_SUFFIX = ["apparel erp", "fashion erp", "erp software", "software solutions", "software", "systems", "system",
                  "solutions", "technologies", "technology", "tech", "apparel software", "erp", "inc", "llc", "ltd",
                  "corporation", "corp", "limited"]
# Roots that are words of the market, not a brand: "Apparel Software" keeps its full name.
GENERIC_ROOTS = {"apparel", "fashion", "software", "systems", "solutions", "erp", "cloud", "retail", "business", "global",
                 "digital", "the", "smart", "online", "data", "enterprise", "store", "web", "app", "my"}


def _clean(name: str) -> str:
    n = re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*", " ", name or "")      # "Sync (iSync Solutions)" -> "Sync"
    n = re.sub(r"\s*[,|/–—-]\s*$", "", n.strip())
    return re.sub(r"\s+", " ", n).strip(" ,.")


def _strip_suffixes(name: str) -> str:
    n = name
    changed = True
    while changed:
        changed = False
        for suf in sorted(GENERIC_SUFFIX, key=len, reverse=True):
            m = re.match(rf"^(.*?)[\s,]+{re.escape(suf)}\.?$", n, re.I)
            if m and distinctive(m.group(1)):
                n, changed = m.group(1).strip(" ,"), True
                break
    return n


def distinctive(root: str) -> bool:
    """A root that can stand alone as a brand: >= 3 characters and not just market words."""
    words = re.findall(r"[A-Za-z0-9]+", root or "")
    return bool(words) and len("".join(words)) >= 3 and not all(w.lower() in GENERIC_ROOTS | LEGAL for w in words)


def brand_short_name(name: str, aliases: tuple[str, ...] | list[str] = ()) -> str:
    """The shortest distinctive form of a brand name (brackets, legal and generic suffixes removed; internal
    capitals and digits kept). Aliases are used only when they are shorter and share the name's first word
    ('Oracle NetSuite' may become 'NetSuite' only if that alias is given)."""
    base = _strip_suffixes(_clean(name)) or (name or "").strip()
    if not distinctive(base):
        base = _clean(name) or (name or "").strip()
    best = base
    first = (re.findall(r"[A-Za-z0-9]+", base) or [""])[0].lower()
    for a in aliases or ():
        cand = _strip_suffixes(_clean(a))
        words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", cand)]
        if cand and distinctive(cand) and len(cand) < len(best) and (first in words or base.lower() in cand.lower()):
            best = cand
    return best
