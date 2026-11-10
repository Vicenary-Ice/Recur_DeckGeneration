"""Disk cache for every external response + a cost ledger.

Cache entries live under runs/_cache/<namespace>/<k[:2]>/<k>.json (or .bin for downloads), keyed by
sha256(method + url + body). Secrets are never part of the key material or the stored entry: callers pass
credentials in headers (never hashed) or list secret query params in `redact`.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


def cache_key(method: str, url: str, body: object = None) -> str:
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str) if body is not None else ""
    return hashlib.sha256(f"{method.upper()} {url}\n{blob}".encode("utf-8")).hexdigest()


class DiskCache:
    def __init__(self, root: Path, read: bool = True):
        self.root = Path(root)
        self.read = read
        self._lock = threading.Lock()
        self.misses: list[str] = []  # offline mode: every call that would have needed the network, in order

    def record_miss(self, service: str, call: str) -> None:
        """Offline mode only. Kept here (not just raised) because fallback layers may swallow the exception;
        the pipeline checks this list after every stage and stops the run on the first entry."""
        with self._lock:
            self.misses.append(f"{service}: {call}")

    def _path(self, ns: str, key: str, ext: str) -> Path:
        return self.root / ns / key[:2] / f"{key}.{ext}"

    def get(self, ns: str, key: str) -> dict | None:
        if not self.read:
            return None
        p = self._path(ns, key, "json")
        try:
            return json.loads(p.read_text("utf-8")) if p.exists() else None
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, ns: str, key: str, value: dict) -> None:
        p = self._path(ns, key, "json")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".tmp{threading.get_ident()}")
        tmp.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
        with self._lock:
            tmp.replace(p)

    def get_bytes(self, ns: str, key: str) -> tuple[bytes, dict] | None:
        if not self.read:
            return None
        p, m = self._path(ns, key, "bin"), self._path(ns, key, "meta.json")
        if p.exists() and m.exists():
            return p.read_bytes(), json.loads(m.read_text("utf-8"))
        return None

    def put_bytes(self, ns: str, key: str, data: bytes, meta: dict) -> None:
        p, m = self._path(ns, key, "bin"), self._path(ns, key, "meta.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        m.write_text(json.dumps(meta), "utf-8")


@dataclass
class CostEntry:
    service: str
    endpoint: str
    status: int | str
    latency_ms: int
    cost_usd: float = 0.0
    credits: float = 0.0
    cached: bool = False
    note: str = ""
    at: float = field(default_factory=time.time)


class CostLedger:
    """Thread-safe record of every external call (cached ones too, at zero cost)."""

    def __init__(self):
        self.entries: list[CostEntry] = []
        self._lock = threading.Lock()

    def record(self, service: str, endpoint: str, status: int | str, latency_ms: int, cost_usd: float = 0.0,
               credits: float = 0.0, cached: bool = False, note: str = "") -> None:
        with self._lock:
            self.entries.append(CostEntry(service, endpoint, status, int(latency_ms), round(cost_usd, 6),
                                          credits, cached, note))

    def totals(self) -> dict:
        out: dict = {}
        with self._lock:
            for e in self.entries:
                t = out.setdefault(e.service, {"calls": 0, "cached": 0, "errors": 0, "cost_usd": 0.0, "credits": 0.0})
                t["calls"] += 1
                t["cached"] += int(e.cached)
                t["errors"] += int(not (isinstance(e.status, int) and 200 <= e.status < 300) and not e.cached)
                t["cost_usd"] = round(t["cost_usd"] + e.cost_usd, 6)
                t["credits"] += e.credits
        return out

    def total_usd(self) -> float:
        with self._lock:
            return round(sum(e.cost_usd for e in self.entries), 4)

    def to_json(self) -> dict:
        with self._lock:
            entries = [asdict(e) for e in self.entries]
        return {"total_usd": self.total_usd(), "by_service": self.totals(), "calls": entries,
                "note": "Claude cost is computed from token usage; Firecrawl is in credits (creditsUsed when "
                        "the API reports it); direct page fetches are free."}

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_json(), indent=2), "utf-8")
