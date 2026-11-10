"""The test-suite guard itself (tests/conftest.py): keys gone, .env ignored, offline on, network blocked."""
from __future__ import annotations

import os

import anthropic
import httpx
import pytest

from recur_deck.cache import CostLedger, DiskCache
from recur_deck.clients.claude import Claude, LLMError
from recur_deck.clients.firecrawl import Firecrawl
from recur_deck.clients.http import Http, OfflineMiss
from recur_deck.config import Settings
from recur_deck.spec import KIT_ROOT
from tests.conftest import LiveNetworkBlocked


def test_keys_removed_and_dotenv_ignored(tmp_path):
    assert not any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "FIRECRAWL_API_KEY"))
    s = Settings.load()  # would read the real KIT_ROOT/.env without the guard
    assert not s.has("anthropic") and not s.has("firecrawl")
    assert s.offline is True
    assert s.runs_dir != KIT_ROOT / "runs"  # the real runs/_cache is never touched


def test_live_http_call_raises(net_guard):
    with pytest.raises(LiveNetworkBlocked):
        httpx.get("https://api.firecrawl.dev/v2/search", timeout=2)
    assert net_guard.attempts
    net_guard.attempts.clear()  # this test made the attempt on purpose


def test_live_anthropic_call_raises(net_guard):
    client = anthropic.Anthropic(api_key="sk-ant-not-a-real-key", max_retries=0)
    with pytest.raises((LiveNetworkBlocked, anthropic.APIConnectionError)):
        client.messages.create(model="claude-haiku-5-5", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    assert net_guard.attempts
    net_guard.attempts.clear()


def test_live_firecrawl_through_our_client_raises(tmp_path, net_guard):
    """Even with a key and offline off, our Http client (no mock transport) can not reach Firecrawl."""
    s = Settings(runs_dir=tmp_path, firecrawl_api_key="fc-not-real")
    http = Http(s, DiskCache(s.cache_dir), CostLedger(), sleep=lambda _: None)
    with pytest.raises(Exception):
        Firecrawl(http, s.firecrawl_api_key).search("Ai2000 competitors")
    assert net_guard.attempts
    net_guard.attempts.clear()


def test_loaded_settings_never_reach_the_network(tmp_path, net_guard):
    """Settings.load() in tests is offline: our clients stop at the cache instead of opening a socket."""
    s = Settings.load()
    cache, ledger = DiskCache(s.cache_dir), CostLedger()
    with pytest.raises(OfflineMiss):
        Firecrawl(Http(s, cache, ledger), "fc-not-real").search("Ai2000")
    with pytest.raises((OfflineMiss, LLMError)):
        from pydantic import BaseModel

        class Out(BaseModel):
            x: int
        Claude(s, cache, ledger).ask("claude-haiku-5-5", "sys", [("q", "hi")], Out)
    assert not net_guard.attempts
