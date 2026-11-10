"""Run settings, read from the environment / `.env` (never from code). Keys are never logged.

Services: Anthropic (Claude) and Firecrawl. Nothing else needs a key.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .spec import KIT_ROOT

SERVICES = ("anthropic", "firecrawl")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    return default if not v else v in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = field(default=None, repr=False)
    firecrawl_api_key: str | None = field(default=None, repr=False)
    model_writer: str = "claude-opus-5-5"
    model_judge: str = "claude-sonnet-5-5"
    model_fast: str = "claude-haiku-5-5"
    model_vision: str = "claude-sonnet-5-5"
    max_qa_rounds: int = 3
    firecrawl_rpm: int = 10                 # Firecrawl free plan: 10 requests / minute
    fetch_mode: str = "direct-first"        # direct-first | firecrawl  (how plain pages are fetched)
    http_concurrency: int = 6               # per service
    runs_dir: Path = KIT_ROOT / "runs"
    use_cache: bool = True                  # read cached responses (writes always happen)
    offline: bool = False                   # replay / --offline: serve from cache only, never touch the network
    vision_qa: bool = True                  # False (--no-vision): skip render + vision QA, layer A checks only
    server_fallback: bool = True            # Anthropic server-side refusal fallback ("fallbacks": "default")

    @property
    def cache_dir(self) -> Path:
        return self.runs_dir / "_cache"

    @classmethod
    def load(cls, env_file: Path | None = None, **overrides) -> "Settings":
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file or KIT_ROOT / ".env", override=False)
        except ImportError:  # pragma: no cover - dotenv is a hard dependency, but never crash on it
            pass
        g = lambda k: (os.environ.get(k) or "").strip() or None  # noqa: E731
        fm = (g("RECUR_FETCH_MODE") or "direct-first").lower()
        s = cls(
            anthropic_api_key=g("ANTHROPIC_API_KEY"), firecrawl_api_key=g("FIRECRAWL_API_KEY"),
            model_writer=g("RECUR_MODEL_WRITER") or cls.model_writer,
            model_judge=g("RECUR_MODEL_JUDGE") or cls.model_judge,
            model_fast=g("RECUR_MODEL_FAST") or cls.model_fast,
            model_vision=g("RECUR_MODEL_VISION") or cls.model_vision,
            max_qa_rounds=max(1, _int("RECUR_MAX_QA_ROUNDS", 3)),
            firecrawl_rpm=max(1, _int("FIRECRAWL_RPM", 10)),
            fetch_mode=fm if fm in ("direct-first", "firecrawl") else "direct-first",
            runs_dir=Path(g("RECUR_RUNS_DIR") or KIT_ROOT / "runs"),
            use_cache=_bool("RECUR_USE_CACHE", True),
            offline=_bool("RECUR_OFFLINE", False),
            server_fallback=_bool("RECUR_SERVER_FALLBACK", True),
        )
        return replace(s, **overrides) if overrides else s

    def has(self, service: str) -> bool:
        return bool({"anthropic": self.anthropic_api_key, "firecrawl": self.firecrawl_api_key}.get(service))

    def redacted(self) -> dict:
        """Safe to log: which keys are present, never their values."""
        return {"keys": {s: self.has(s) for s in SERVICES},
                "models": {"writer": self.model_writer, "judge": self.model_judge, "fast": self.model_fast,
                           "vision": self.model_vision},
                "max_qa_rounds": self.max_qa_rounds, "firecrawl_rpm": self.firecrawl_rpm,
                "fetch_mode": self.fetch_mode, "offline": self.offline, "use_cache": self.use_cache,
                "vision_qa": self.vision_qa}

    def secrets(self) -> list[str]:
        return [v for v in (self.anthropic_api_key, self.firecrawl_api_key) if v]
