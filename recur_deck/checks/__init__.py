"""Registry of deterministic deck checks that run inside qa.run_all (Layer A).

A check module is any file in this package. It decorates its check functions with @register:

    @register
    def my_check(content: DeckContent, manifest: dict, logo_metrics: dict, extra: dict) -> list[qa.Issue]: ...

`extra` carries the run context a check may need, every key optional:
  evidence  EvidenceStore or None      entity  entity.json dict      graph  market graph or None
  run_dir   Path of the run folder     profile profile.json dict     comps  competitors.json dict
  logos     the full logo metrics per company (logo_metrics.json)

Every submodule is imported on first use (pkgutil), so adding a check never edits a shared file.
A check that raises is reported as a warning issue instead of breaking the QA round.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover
    from ..models import DeckContent
    from ..qa import Issue

log = logging.getLogger("recur_deck.checks")
CheckFn = Callable[["DeckContent", dict, dict, dict], list["Issue"]]
_REGISTRY: list[CheckFn] = []
_loaded = False


def register(fn: CheckFn) -> CheckFn:
    if fn not in _REGISTRY:
        _REGISTRY.append(fn)
    return fn


def load_all() -> None:
    """Import every module in this package once, so their @register decorators run."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    for mod in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        importlib.import_module(f"{__name__}.{mod.name}")


def registered() -> list[CheckFn]:
    load_all()
    return list(_REGISTRY)


def run_registered(content: "DeckContent", manifest: dict, logo_metrics: dict, extra: dict | None = None) -> list["Issue"]:
    from ..qa import Issue
    extra = extra or {}
    issues: list[Issue] = []
    for fn in registered():
        try:
            issues += fn(content, manifest, logo_metrics, extra) or []
        except Exception as e:  # noqa: BLE001 - a broken check must never break the QA round
            log.warning("check %s failed: %s: %s", fn.__name__, type(e).__name__, e)
            issues.append(Issue("check_failed", "warn", 0, fn.__name__, f"{type(e).__name__}: {e}"[:300], "needs_review"))
    return issues
