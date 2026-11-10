"""One shared httpx client for every external service: timeouts, retries, disk cache, cost ledger, throttling.

Retry policy (BUILD_PROMPT §7): 3 attempts, exponential backoff 1-8 s, honour Retry-After, only on
429 / 5xx / timeouts / connection errors. 400/401/402/403 are never retried; 401/402/403 mark the service
unavailable for the rest of the run so stages fall back instead of hammering it.
"""
from __future__ import annotations

import email.utils
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..cache import CostLedger, DiskCache, cache_key
from ..config import Settings

UA = "RecurDeckBuilder/1.0"
log = logging.getLogger("recur_deck.http")
DNS_FAILURE = re.compile(r"nodename nor servname|name or service not known|no address associated|getaddrinfo failed|"
                         r"temporary failure in name resolution|name resolution", re.I)


class ServiceError(Exception):
    def __init__(self, service: str, status: int | str, message: str, retry_after: float | None = None):
        super().__init__(f"{service} {status}: {message}")
        self.service, self.status, self.message, self.retry_after = service, status, message, retry_after


class RetryableError(ServiceError):
    """429 / 5xx / timeout / connection error."""


class ServiceUnavailable(ServiceError):
    """Missing key, auth failure, out of credits (401/402/403) or marked down for this run."""


class OfflineMiss(ServiceError):
    """Replay mode and the response is not in the cache."""


@dataclass
class Response:
    status: int
    data: Any
    url: str
    cached: bool = False


def _retry_after(headers: httpx.Headers) -> float | None:
    v = headers.get("retry-after")
    if not v:
        return None
    try:
        return max(0.0, float(v))
    except ValueError:
        try:
            return max(0.0, email.utils.parsedate_to_datetime(v).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


def redact_url(url: str, secret_params: tuple[str, ...] = ()) -> str:
    if not secret_params:
        return url
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k not in secret_params]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


def _brief(body: Any, n: int = 160) -> str:
    """Short, key-free description of a request body for offline-miss messages (query / url first)."""
    if isinstance(body, dict):
        for k in ("query", "url"):
            if body.get(k):
                return f"{k}={body[k]!r}"
    return str(body)[:n]


class RateLimiter:
    """Sliding-window limiter: at most `per_minute` calls in any 60 s window."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.per_minute, self.clock, self.sleep = per_minute, clock, sleep
        self.calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self.clock()
                while self.calls and now - self.calls[0] >= 60.0:
                    self.calls.popleft()
                if len(self.calls) < self.per_minute:
                    self.calls.append(now)
                    return
                wait = 60.0 - (now - self.calls[0]) + 0.05
            self.sleep(wait)


_ERROR_KINDS = {"ServiceError": ServiceError, "RetryableError": RetryableError, "ServiceUnavailable": ServiceUnavailable}


class Http:
    """Final failures (after retries) are cached too, as negative entries in '<ns>_err'. Only offline mode reads
    them, so an offline replay fails the same calls the live run saw (a 404, a dead domain, a bad certificate),
    while live runs still retry everything."""

    def __init__(self, settings: Settings, cache: DiskCache, ledger: CostLedger,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep):
        self.settings, self.cache, self.ledger, self._sleep = settings, cache, ledger, sleep
        self.client = httpx.Client(timeout=httpx.Timeout(60.0, connect=30.0), headers={"User-Agent": UA},
                                   follow_redirects=True, transport=transport)
        self.down: dict[str, str] = {}
        self._sems: dict[str, threading.BoundedSemaphore] = {}
        self._limiters: dict[str, RateLimiter] = {"firecrawl": RateLimiter(settings.firecrawl_rpm, sleep=sleep)}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- service state
    def mark_down(self, service: str, reason: str) -> None:
        if service not in self.down:
            log.warning("service %s unavailable for this run: %s", service, reason)
            self.down[service] = reason

    def is_up(self, service: str) -> bool:
        return service not in self.down

    def _put_err(self, ns: str, key: str, e: ServiceError, url: str) -> None:
        if isinstance(e, OfflineMiss):
            return
        try:
            self.cache.put(f"{ns}_err", key, {"kind": type(e).__name__, "service": e.service, "status": e.status,
                                              "message": str(e.message)[:300], "url": url})
        except OSError:
            pass

    def _replay_err(self, ns: str, key: str, endpoint: str) -> None:
        """Offline: re-raise the failure the live run recorded for this call, if any."""
        neg = self.cache.get(f"{ns}_err", key)
        if neg is None:
            return
        self.ledger.record(neg["service"], endpoint, neg["status"], 0, cached=True, note="recorded failure")
        err = _ERROR_KINDS.get(neg["kind"], ServiceError)(neg["service"], neg["status"], neg["message"])
        if isinstance(err, ServiceUnavailable) and neg["status"] in (401, 402, 403):
            self.mark_down(neg["service"], f"HTTP {neg['status']}: {neg['message']} (recorded)")
        raise err

    def _sem(self, service: str) -> threading.BoundedSemaphore:
        with self._lock:
            return self._sems.setdefault(service, threading.BoundedSemaphore(self.settings.http_concurrency))

    def _retrying(self) -> Retrying:
        expo = wait_exponential(multiplier=1, min=1, max=8)

        def wait(rs) -> float:
            exc = rs.outcome.exception() if rs.outcome else None
            ra = getattr(exc, "retry_after", None)
            return min(float(ra), 30.0) if ra else expo(rs)

        return Retrying(stop=stop_after_attempt(3), wait=wait, retry=retry_if_exception_type(RetryableError),
                        reraise=True, sleep=self._sleep)

    # ---------------------------------------------------------------- JSON APIs
    def request_json(self, service: str, method: str, url: str, *, json_body: Any = None,
                     params: dict | None = None, headers: dict | None = None, ns: str | None = None,
                     secret_params: tuple[str, ...] = (), ok_statuses: tuple[int, ...] = (),
                     cost_fn: Callable[[Any], tuple[float, float]] | None = None, endpoint: str = "") -> Response:
        """Call a JSON API. Statuses in `ok_statuses` (besides 2xx) are returned (and cached) instead of
        raised (and cached), e.g. a 404 that is a valid answer rather than an error."""
        full = str(httpx.URL(url, params=params)) if params else url
        safe_url = redact_url(full, secret_params)
        key = cache_key(method, safe_url, json_body)
        ns = ns or service
        endpoint = endpoint or urlsplit(url).path
        hit = self.cache.get(ns, key)
        if hit is not None:
            self.ledger.record(service, endpoint, hit["status"], 0, cached=True)
            return Response(hit["status"], hit["data"], safe_url, cached=True)
        if self.settings.offline:
            self._replay_err(ns, key, endpoint)
            if service in self.down:  # the live run stopped calling it too (e.g. after a recorded 402)
                raise ServiceUnavailable(service, "down", self.down[service])
            call =f"{method} {safe_url}" + (f" {_brief(json_body)}" if json_body is not None else "")
            self.cache.record_miss(service, call)
            raise OfflineMiss(service, "offline", f"not cached: {call}")
        if service in self.down:
            raise ServiceUnavailable(service, "down", self.down[service])

        def once() -> Response:
            if service in self._limiters:
                self._limiters[service].acquire()
            t0 = time.monotonic()
            try:
                with self._sem(service):
                    r = self.client.request(method, full, json=json_body, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                self.ledger.record(service, endpoint, type(e).__name__, (time.monotonic() - t0) * 1000)
                raise RetryableError(service, type(e).__name__, str(e)[:200]) from e
            ms = (time.monotonic() - t0) * 1000
            try:
                data = r.json()
            except ValueError:
                data = {"_text": r.text[:2000]}
            if r.is_success or r.status_code in ok_statuses:
                cost, credits = cost_fn(data) if (cost_fn and r.is_success) else (0.0, 0.0)
                self.ledger.record(service, endpoint, r.status_code, ms, cost, credits)
                log.info("%s %s %s -> %s (%d ms)", service, method, safe_url, r.status_code, ms)
                return Response(r.status_code, data, safe_url)
            self.ledger.record(service, endpoint, r.status_code, ms)
            msg = (str(data.get("error") or data.get("message") or data) if isinstance(data, dict) else str(data))[:300]
            log.warning("%s %s %s -> %s: %s", service, method, safe_url, r.status_code, msg)
            if r.status_code == 429 or r.status_code >= 500:
                raise RetryableError(service, r.status_code, msg, _retry_after(r.headers))
            if r.status_code in (401, 402, 403):
                self.mark_down(service, f"HTTP {r.status_code}: {msg}")
                raise ServiceUnavailable(service, r.status_code, msg)
            raise ServiceError(service, r.status_code, msg)

        try:
            resp = self._retrying()(once)
        except ServiceError as e:
            self._put_err(ns, key, e, safe_url)
            raise
        self.cache.put(ns, key, {"status": resp.status, "data": resp.data, "url": safe_url})
        return resp

    # ---------------------------------------------------------------- downloads (logos, raw pages)
    def get_bytes(self, url: str, *, max_bytes: int = 5_000_000, kind: str = "image",
                  headers: dict | None = None) -> tuple[bytes, str, str]:
        """Download a file. kind="image" accepts image/* and SVG text; kind="html" accepts text/html.
        Returns (bytes, content_type, final_url). Raises ServiceError on any failure."""
        service = "download"
        key = cache_key("GET", url, {"kind": kind})
        hit = self.cache.get_bytes("download", key)
        if hit is not None:
            self.ledger.record(service, kind, 200, 0, cached=True)
            return hit[0], hit[1].get("content_type", ""), hit[1].get("final_url", url)
        if self.settings.offline:
            self._replay_err("download", key, kind)
            self.cache.record_miss(service, f"GET {url} ({kind})")
            raise OfflineMiss(service, "offline", f"not cached: GET {url}")

        def once() -> tuple[bytes, str, str]:
            t0 = time.monotonic()
            try:
                with self._sem(service), self.client.stream("GET", url, headers=headers) as r:
                    ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if r.status_code == 429 or r.status_code >= 500:
                        raise RetryableError(service, r.status_code, url, _retry_after(r.headers))
                    if not r.is_success:
                        raise ServiceError(service, r.status_code, url)
                    if kind == "image" and not (ct.startswith("image/") or "svg" in ct or ct in ("", "application/octet-stream", "text/plain", "text/xml", "application/xml")):
                        raise ServiceError(service, "content-type", f"{ct} is not an image: {url}")
                    if kind == "html" and ct and "html" not in ct and "xml" not in ct and "text" not in ct:
                        raise ServiceError(service, "content-type", f"{ct} is not a page: {url}")
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) > max_bytes:
                            raise ServiceError(service, "too-large", f"> {max_bytes} bytes: {url}")
                    final = str(r.url)
            except httpx.ConnectError as e:
                if DNS_FAILURE.search(str(e)):  # the domain does not exist: retrying cannot help
                    raise ServiceError(service, "dns", f"{url}: {str(e)[:120]}") from e
                raise RetryableError(service, type(e).__name__, str(e)[:200]) from e
            except (httpx.TimeoutException, httpx.TransportError) as e:
                raise RetryableError(service, type(e).__name__, str(e)[:200]) from e
            self.ledger.record(service, kind, 200, (time.monotonic() - t0) * 1000)
            return bytes(buf), ct, final

        try:
            data, ct, final = self._retrying()(once)
        except ServiceError as e:
            self._put_err("download", key, e, url)
            raise
        self.cache.put_bytes("download", key, data, {"content_type": ct, "final_url": final, "url": url})
        return data, ct, final

    def close(self) -> None:
        self.client.close()
