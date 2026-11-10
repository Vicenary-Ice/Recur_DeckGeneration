"""Test-suite guard: no test may ever reach a paid API (or any real network).

Autouse, for every test:
- ANTHROPIC_API_KEY and FIRECRAWL_API_KEY are removed from the environment,
- Settings.load() can not read the real .env (dotenv loading is a no-op),
- RECUR_OFFLINE=1, so Settings.load() comes back in offline (cache-only) mode,
- RECUR_RUNS_DIR points into the test's tmp dir, so the real runs/_cache is never read or written,
- RECUR_MARKET_GRAPH is unset, so the real runs/_market_graph.json is never read or written,
- socket connects to anything but a local Unix socket raise LiveNetworkBlocked, and every attempt is recorded.
  The fixture fails the test at teardown if an attempt was made, even when the code under test swallowed the
  exception (Ctx.pmap and the fallback layers catch broad exceptions).

Tests that talk to "the web" do it through httpx.MockTransport (tests/fake_world.py), which never opens a socket.
"""
from __future__ import annotations

import socket

import pytest

ENV_KEYS = ("ANTHROPIC_API_KEY", "FIRECRAWL_API_KEY")


class LiveNetworkBlocked(RuntimeError):
    """Raised when a test tries to open a real network connection."""


class NetGuard:
    def __init__(self):
        self.attempts: list[str] = []


def _blocked(guard: NetGuard, original):
    def connect(self, address, *a, **kw):
        if self.family == getattr(socket, "AF_UNIX", object()):
            return original(self, address, *a, **kw)  # local IPC (e.g. multiprocessing), not the network
        guard.attempts.append(repr(address))
        raise LiveNetworkBlocked(f"test tried to open a network connection to {address!r}")
    return connect


@pytest.fixture(autouse=True)
def net_guard(monkeypatch, tmp_path):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RECUR_OFFLINE", "1")
    monkeypatch.setenv("RECUR_RUNS_DIR", str(tmp_path / "guarded-runs"))
    monkeypatch.delenv("RECUR_MARKET_GRAPH", raising=False)  # the shared market graph lives under the tmp runs dir
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)

    guard = NetGuard()
    monkeypatch.setattr(socket.socket, "connect", _blocked(guard, socket.socket.connect))
    original_ex = socket.socket.connect_ex

    def connect_ex(self, address, *a, **kw):
        if self.family == getattr(socket, "AF_UNIX", object()):
            return original_ex(self, address, *a, **kw)
        guard.attempts.append(repr(address))
        raise LiveNetworkBlocked(f"test tried to open a network connection to {address!r}")
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    yield guard
    if guard.attempts:
        pytest.fail(f"test attempted live network connection(s): {guard.attempts[:5]}", pytrace=False)
