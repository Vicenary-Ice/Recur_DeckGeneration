"""Market graph shared across runs: which companies compete with which, and where earlier decks placed them.

STUB (Agent 0). The interface below is what other modules code against; Agent 2 replaces the in-memory no-op
implementation. Stored at runs/_market_graph.json, or the path in env RECUR_MARKET_GRAPH.
"""
from __future__ import annotations

import os
from pathlib import Path


def default_path(settings) -> Path:
    env = (os.environ.get("RECUR_MARKET_GRAPH") or "").strip()
    return Path(env) if env else Path(settings.runs_dir) / "_market_graph.json"


class MarketGraph:
    def __init__(self, path: Path | None = None):
        self.path = path

    def neighbors(self, domain: str, depth: int = 2) -> list[dict]:
        """[{domain, name, via, runs}] for companies within `depth` competitor edges of `domain`."""
        return []

    def placements(self, domain: str) -> list[dict]:
        """[{run, axis_kind, axis_title, side, label, evidence_ids}] from earlier decks."""
        return []

    def add_run(self, entity: dict, comps_out: dict, content: dict | None) -> None:
        return None

    def save(self) -> None:
        return None


def load(settings) -> MarketGraph:
    return MarketGraph(default_path(settings))


def from_runs(run_dirs: list[Path]) -> MarketGraph:
    return MarketGraph(None)
