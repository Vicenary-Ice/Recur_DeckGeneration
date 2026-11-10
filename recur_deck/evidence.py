"""Evidence store: every fact in a deck points to a quote that was found verbatim in a fetched page.

`add()` refuses quotes that are not a (normalised) substring of the source text, so a model can never
"cite" something it made up. IDs (e1, e2, ...) are stable within a run and written to evidence.jsonl.
"""
from __future__ import annotations

import html
import json
import re
import threading
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

MAX_QUOTE = 300
KINDS = ("identity", "product", "segment", "scope", "news", "comparison")

_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
                            "“": '"', "”": '"', "„": '"', "″": '"',
                            "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
                            " ": " ", " ": " ", " ": " ", "​": "", "­": ""})


def normalise(text: str) -> str:
    """Case-, whitespace-, quote-style- and markdown-insensitive form used for verbatim checks."""
    t = html.unescape(text or "")
    t = unicodedata.normalize("NFKC", t).translate(_TRANSLATE)
    t = _MD_LINK.sub(r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[*_`#>|\\]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def quote_in(quote: str, text: str) -> bool:
    """True if `quote` appears in `text` (normalised). Ellipses ("..." / "…") may elide parts, but every
    fragment of >= 12 characters must appear, in order."""
    nq, nt = normalise(quote), normalise(text)
    if not nq or not nt:
        return False
    if nq in nt:
        return True
    parts = [p.strip(" .,;:") for p in re.split(r"\.\.\.|…", nq)]
    parts = [p for p in parts if p]
    if len(parts) < 2 or any(len(p) < 12 for p in parts):
        return False
    pos = 0
    for p in parts:
        i = nt.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


def _trim(quote: str) -> str:
    q = re.sub(r"\s+", " ", quote).strip()
    if len(q) <= MAX_QUOTE:
        return q
    cut = q[:MAX_QUOTE].rsplit(" ", 1)[0]  # a prefix of a verbatim quote is still verbatim
    return cut or q[:MAX_QUOTE]


@dataclass
class Evidence:
    id: str
    url: str
    title: str
    fetched_at: str
    source: str        # firecrawl | web (direct fetch) | claude_web_search
    quote: str
    about: str         # "target" or a competitor domain
    kind: str          # identity | product | segment | scope | news | comparison


class EvidenceStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._items: list[Evidence] = []
        self._docs: dict[str, str] = {}
        self._index: dict[tuple[str, str, str], str] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- documents
    def register_doc(self, url: str, text: str) -> None:
        """Remember fetched text so later quotes about `url` can be verified against it."""
        if url and text:
            with self._lock:
                prev = self._docs.get(url, "")
                if text not in prev:
                    self._docs[url] = (prev + "\n\n" + text).strip()

    def doc(self, url: str) -> str:
        return self._docs.get(url, "")

    def find_source(self, quote: str, urls: list[str] | None = None) -> str | None:
        """URL of the first registered document that contains the quote (optionally limited to `urls`)."""
        for u in (urls if urls is not None else list(self._docs)):
            if quote_in(quote, self._docs.get(u, "")):
                return u
        return None

    # ---------------------------------------------------------------- evidence
    def add(self, url: str, quote: str, about: str, kind: str, source: str, title: str = "",
            text: str | None = None) -> str | None:
        """Store a quote if it is found verbatim in `text` (or in the registered document for `url`).
        Returns the evidence id, or None when the quote cannot be verified (the fact must then be dropped)."""
        if not quote or not url:
            return None
        hay = text if text is not None else self._docs.get(url, "")
        if not quote_in(quote, hay):
            return None
        if text is not None:
            self.register_doc(url, text)
        q = _trim(quote)
        kind = kind if kind in KINDS else "product"
        key = (url, normalise(q), about)
        with self._lock:
            if key in self._index:
                return self._index[key]
            eid = f"e{len(self._items) + 1}"
            self._items.append(Evidence(eid, url, title or "", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        source, q, about, kind))
            self._index[key] = eid
        return eid

    def add_any(self, quote: str, about: str, kind: str, source: str, urls: list[str] | None = None,
                titles: dict[str, str] | None = None) -> str | None:
        """Like add(), but finds which registered document contains the quote."""
        url = self.find_source(quote, urls)
        return self.add(url, quote, about, kind, source, (titles or {}).get(url or "", "")) if url else None

    def get(self, eid: str) -> Evidence | None:
        try:
            item = self._items[int(eid.lstrip("e")) - 1]
            return item if item.id == eid else None
        except (ValueError, IndexError):
            return None

    def items(self) -> list[Evidence]:
        return list(self._items)

    def about(self, about: str) -> list[Evidence]:
        return [e for e in self._items if e.about == about]

    def subset(self, ids: list[str]) -> list[dict]:
        seen, out = set(), []
        for i in ids:
            e = self.get(i)
            if e and i not in seen:
                seen.add(i)
                out.append({"id": e.id, "quote": e.quote, "url": e.url, "about": e.about, "kind": e.kind})
        return out

    def valid_ids(self, ids: list[str]) -> list[str]:
        return [i for i in dict.fromkeys(ids) if self.get(i)]

    # ---------------------------------------------------------------- persistence
    def save(self, path: Path | None = None) -> None:
        p = path or self.path
        if p:
            p.write_text("".join(json.dumps(asdict(e), ensure_ascii=False) + "\n" for e in self._items), "utf-8")

    @classmethod
    def load(cls, path: Path) -> "EvidenceStore":
        st = cls(path)
        if path.exists():
            for line in path.read_text("utf-8").splitlines():
                if line.strip():
                    e = Evidence(**json.loads(line))
                    st._items.append(e)
                    st._index[(e.url, normalise(e.quote), e.about)] = e.id
        return st
