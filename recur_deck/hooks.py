"""Extension points of the pipeline, so stage modules can add behaviour without editing pipeline.py.

    from recur_deck import hooks

    @hooks.on("after_run")
    def remember(ctx, report): ...

Hooks and their keyword arguments (pipeline.py fires them):
  after_profile(ctx, entity, profile)                -> None
  after_competitors(ctx, entity, comps_out)          -> None
  after_write(ctx, content)                          -> list[str]   problems found in the draft
  after_vision(ctx, content, pngs, manifest)         -> list[dict]  finding-like dicts
  after_run(ctx, report)                             -> None        report is the final qa_report

`fire(name, **kw)` calls every handler in registration order and returns their results flattened into one list
(None counts as nothing). A handler that raises is logged and skipped: a hook never breaks a run.

Modules that register handlers are listed in PLUGIN_MODULES and imported on the first fire(); a module that
does not exist (yet) is skipped.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

log = logging.getLogger("recur_deck.hooks")
NAMES = ("after_profile", "after_competitors", "after_write", "after_vision", "after_run")
PLUGIN_MODULES = ("recur_deck.market_graph", "recur_deck.stages.placement", "recur_deck.stages.critic",
                  "recur_deck.checks.copy")
_handlers: dict[str, list[Callable[..., Any]]] = {n: [] for n in NAMES}
_plugins_loaded = False


def on(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    if name not in _handlers:
        raise ValueError(f"unknown hook {name!r}; known: {', '.join(NAMES)}")

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        if fn not in _handlers[name]:
            _handlers[name].append(fn)
        return fn
    return deco


def register(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    return on(name)(fn)


def unregister(name: str, fn: Callable[..., Any]) -> None:
    if fn in _handlers.get(name, []):
        _handlers[name].remove(fn)


def handlers(name: str) -> list[Callable[..., Any]]:
    load_plugins()
    return list(_handlers[name])


def load_plugins() -> None:
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    for mod in PLUGIN_MODULES:
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as e:
            if e.name != mod:  # the module exists but one of its imports is missing: that is a real error
                raise


def fire(name: str, **kw: Any) -> list[Any]:
    out: list[Any] = []
    for fn in handlers(name):
        try:
            res = fn(**kw)
        except Exception as e:  # noqa: BLE001 - an extension must never break a run
            log.warning("hook %s:%s failed: %s: %s", name, getattr(fn, "__name__", fn), type(e).__name__, e)
            continue
        if res is None:
            continue
        out.extend(res if isinstance(res, list) else [res])
    if out:
        log.info("hook %s returned %d item(s): %s", name, len(out), str(out)[:500])
    return out
