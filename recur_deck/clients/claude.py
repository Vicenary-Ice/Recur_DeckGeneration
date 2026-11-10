"""Claude structured-output helper (https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

ask(model, system, user_blocks, OutputModel, images=..., effort=...) -> validated Pydantic instance.

- Schema: output_config.format = json_schema(anthropic.transform_schema(Model)) (never raw model_json_schema()).
- 5.5 models: no temperature/top_p/top_k, no assistant prefill, no forced tool_choice; thinking is adaptive, so
  the answer is the `text` block(s), never content[0].
- Always re-validated with Pydantic (with an optional validation context). One retry with the error text,
  then LLMError.
- Streams every request (no HTTP timeout on long outputs). Responses are cached on disk (re-runs and the fix
  loop are free and reproducible) and every call is written to the cost ledger.
- Refusals / overload: judges fall back to the next model in `fallbacks`; the writer has none.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import time
from typing import Any, Sequence, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from ..cache import CostLedger, DiskCache
from ..config import Settings
from .http import OfflineMiss

log = logging.getLogger("recur_deck.claude")
M = TypeVar("M", bound=BaseModel)

# $ per million tokens: input, output, cache read, cache write (5-min TTL = 1.25x input). Source: claude-api
# reference, cached 2026-10-06.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
SERVER_FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable")  # Haiku 5.5 has none
MAX_IMAGE_EDGE = 2576


class LLMError(RuntimeError):
    pass


class Refusal(LLMError):
    pass


def cost_of(model: str, usage: Any) -> float:
    pin, pout, pread, pwrite = PRICES.get(model, PRICES["claude-opus-5-5"])
    g = lambda k: float(getattr(usage, k, 0) or 0)  # noqa: E731
    return (g("input_tokens") * pin + g("output_tokens") * pout + g("cache_read_input_tokens") * pread
            + g("cache_creation_input_tokens") * pwrite) / 1e6


EST_OUTPUT = {"low": 1500, "medium": 4000, "high": 8000}  # output + thinking tokens a call usually spends
EST_IMAGE_TOKENS = 3000                                    # a slide render at <= 2576 px


def estimate_cost(model: str, system: str, content: list[dict], max_tokens: int, effort: str) -> float:
    """A deliberately generous estimate of one call's cost, for the budget guard (before the call is sent)."""
    pin, pout, *_ = PRICES.get(model, PRICES["claude-opus-5-5"])
    chars = len(system) + sum(len(c.get("text", "")) for c in content)
    tokens_in = chars / 3.5 + EST_IMAGE_TOKENS * sum(1 for c in content if c.get("type") == "image")
    tokens_out = min(max_tokens, EST_OUTPUT.get(effort, 4000))
    return (tokens_in * pin + tokens_out * pout) / 1e6


def image_block(png: bytes, media_type: str = "image/png") -> dict:
    """Base64 image block, downscaled to <= 2576 px on the long edge."""
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    if max(im.size) > MAX_IMAGE_EDGE:
        im.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        png, media_type = buf.getvalue(), "image/png"
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": base64.standard_b64encode(png).decode("ascii")}}


def render_blocks(user_blocks: Sequence[tuple[str, Any]] | dict) -> str:
    items = user_blocks.items() if isinstance(user_blocks, dict) else user_blocks
    parts = []
    for tag, val in items:
        body = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False, indent=1, default=str)
        parts.append(f"<{tag}>\n{body}\n</{tag}>")
    return "\n\n".join(parts)


class Claude:
    def __init__(self, settings: Settings, cache: DiskCache, ledger: CostLedger,
                 client: anthropic.Anthropic | None = None):
        self.settings, self.cache, self.ledger = settings, cache, ledger
        self._client = client
        self._server_fallback = settings.server_fallback

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not self.settings.anthropic_api_key:
                raise LLMError("ANTHROPIC_API_KEY is not set (add it to .env)")
            self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key, max_retries=3, timeout=600)
        return self._client

    # ---------------------------------------------------------------- public
    def ask(self, model: str, system: str, user_blocks: Sequence[tuple[str, Any]] | dict, output_model: type[M], *,
            images: Sequence[tuple[str, bytes]] | None = None, effort: str = "medium", max_tokens: int = 16000,
            context: dict | None = None, label: str = "", fallbacks: Sequence[str] = ()) -> M:
        text = render_blocks(user_blocks)
        content: list[dict] = []
        for caption, png in images or []:
            content.append({"type": "text", "text": caption})
            content.append(image_block(png))
        content.append({"type": "text", "text": text})
        last: Exception | None = None
        for m in [model, *fallbacks]:
            try:
                return self._ask_validated(m, system, content, output_model, effort, max_tokens, context, label)
            except (Refusal, anthropic.InternalServerError, anthropic.APIConnectionError, anthropic.RateLimitError) as e:
                last = e
                log.warning("claude %s on %s failed (%s); %s", label, m, type(e).__name__,
                            "falling back" if m != ([model, *fallbacks])[-1] else "no fallback left")
            except anthropic.APIStatusError as e:
                if getattr(e, "status_code", 0) >= 500:
                    last = e
                    continue
                raise LLMError(f"{label}: Claude API error {e.status_code}: {e.message}") from e
        raise LLMError(f"{label}: {type(last).__name__}: {last}") from last

    # ---------------------------------------------------------------- internals
    def _ask_validated(self, model, system, content, output_model, effort, max_tokens, context, label):
        raw = self._call(model, system, content, output_model, effort, max_tokens, label)
        try:
            return output_model.model_validate_json(raw, context=context)
        except ValidationError as e:
            err = str(e)[:3000]
            log.info("claude %s: validation failed, retrying once: %s", label, err.splitlines()[0])
        retry = content + [{"type": "text", "text": render_blocks([
            ("previous_answer", raw), ("validation_errors", err)]) +
            "\n\nYour previous answer failed validation. Return a corrected answer that fixes every error above."}]
        raw2 = self._call(model, system, retry, output_model, effort, max_tokens, label + ":retry")
        try:
            return output_model.model_validate_json(raw2, context=context)
        except ValidationError as e2:
            raise LLMError(f"{label}: output failed validation twice: {str(e2)[:600]}") from e2

    def _key(self, model, system, content, output_model, effort, max_tokens) -> str:
        def strip(c):
            if c.get("type") == "image":
                return {"type": "image", "sha": hashlib.sha256(c["source"]["data"].encode()).hexdigest()}
            return c
        blob = json.dumps({"m": model, "s": system, "c": [strip(c) for c in content], "e": effort, "t": max_tokens,
                           "o": output_model.__name__, "schema": output_model.model_json_schema()},
                          sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _call(self, model, system, content, output_model, effort, max_tokens, label) -> str:
        key = self._key(model, system, content, output_model, effort, max_tokens)
        hit = self.cache.get("anthropic", key)
        if hit is not None:
            self.ledger.record("anthropic", label or output_model.__name__, 200, 0, cached=True, note=model)
            return hit["text"]
        if self.settings.offline:
            self.cache.record_miss("anthropic", f"{label or output_model.__name__} ({model})")
            raise OfflineMiss("anthropic", "offline", f"{label}: response not cached")
        self.ledger.guard("anthropic", label or output_model.__name__,
                          estimate_cost(model, system, content, max_tokens, effort))
        kwargs: dict = dict(
            model=model, max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort,
                           "format": {"type": "json_schema", "schema": anthropic.transform_schema(output_model)}},
        )
        use_fb = self._server_fallback and model.startswith(SERVER_FALLBACK_MODELS)
        t0 = time.monotonic()
        for attempt in range(3):
            extra = ({"extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
                      "extra_body": {"fallbacks": "default"}} if use_fb else {})
            try:
                with self.client.messages.stream(**kwargs, **extra) as stream:
                    resp = stream.get_final_message()
            except anthropic.BadRequestError as e:
                if use_fb and "fallback" in str(e).lower():
                    log.warning("server-side fallback rejected (%s); continuing without it", str(e)[:160])
                    self._server_fallback = use_fb = False
                    continue
                raise
            ms = (time.monotonic() - t0) * 1000
            cost = cost_of(model, resp.usage)
            self.ledger.record("anthropic", label or output_model.__name__, 200, ms, cost_usd=cost,
                               note=f"{model} in={resp.usage.input_tokens} out={resp.usage.output_tokens} "
                                    f"cache_read={getattr(resp.usage, 'cache_read_input_tokens', 0) or 0}")
            log.info("claude %s %s: %s, in=%s out=%s $%.4f (%d ms)", label, model, resp.stop_reason,
                     resp.usage.input_tokens, resp.usage.output_tokens, cost, ms)
            if resp.stop_reason == "refusal":
                det = getattr(resp, "stop_details", None)
                raise Refusal(f"{model} declined ({getattr(det, 'category', None)})")
            if resp.stop_reason == "max_tokens" and attempt < 2:
                kwargs["max_tokens"] = min(kwargs["max_tokens"] * 2, 64000)
                continue
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            if not text:
                raise LLMError(f"{label}: empty answer (stop_reason={resp.stop_reason})")
            self.cache.put("anthropic", key, {"text": text, "model": model, "stop_reason": resp.stop_reason})
            return text
        raise LLMError(f"{label}: no usable answer after retries")

    # ---------------------------------------------------------------- web search fallback (§10)
    def web_search(self, query: str, model: str | None = None, max_uses: int = 2) -> list[dict]:
        """Last-resort search via Claude's server web search tool: [{url, title, description}]."""
        model = model or self.settings.model_judge
        key = hashlib.sha256(f"websearch|{model}|{query}".encode()).hexdigest()
        hit = self.cache.get("anthropic_search", key)
        if hit is not None:
            return hit["results"]
        if self.settings.offline:
            self.cache.record_miss("anthropic", f"web_search {query!r} ({model})")
            raise OfflineMiss("anthropic", "offline", "web search not cached")
        self.ledger.guard("anthropic", "web_search", 0.01 * max_uses + estimate_cost(model, "", [], 4000, "low"))
        t0 = time.monotonic()
        with self.client.messages.stream(
                model=model, max_tokens=4000, output_config={"effort": "low"},
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}],
                messages=[{"role": "user", "content": f"Search the web for: {query}\nReply with 'done' when finished."}],
        ) as stream:
            resp = stream.get_final_message()
        self.ledger.record("anthropic", "web_search", 200, (time.monotonic() - t0) * 1000,
                           cost_usd=cost_of(model, resp.usage) + 0.01 * max_uses, note=model)
        out = []
        for b in resp.content:
            if getattr(b, "type", "") == "web_search_tool_result" and isinstance(getattr(b, "content", None), list):
                for r in b.content:
                    if getattr(r, "url", None):
                        out.append({"url": r.url, "title": getattr(r, "title", "") or "", "description": ""})
        self.cache.put("anthropic_search", key, {"results": out})
        return out
