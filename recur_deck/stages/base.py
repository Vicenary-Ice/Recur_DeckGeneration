"""Shared run context for every stage: services, evidence, run folder, report flags, helpers."""
from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

import tldextract

from ..cache import CostLedger, DiskCache
from ..clients.claude import Claude
from ..clients.firecrawl import Firecrawl
from ..clients.http import Http
from ..clients.web import Web
from ..config import Settings
from ..evidence import EvidenceStore

T = TypeVar("T")
R = TypeVar("R")
PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
_TLD = tldextract.TLDExtract(suffix_list_urls=())  # bundled public-suffix snapshot: no network at runtime

# Sites that talk ABOUT companies (directories, social, press, search). Never a company's own domain.
NON_COMPANY_DOMAINS = {
    "capterra.com", "g2.com", "getapp.com", "softwareadvice.com", "softwareconnect.com", "trustradius.com",
    "crunchbase.com", "linkedin.com", "facebook.com", "twitter.com", "x.com", "youtube.com", "wikipedia.org",
    "glassdoor.com", "indeed.com", "zoominfo.com", "bloomberg.com", "pitchbook.com", "cbinsights.com", "owler.com",
    "medium.com", "reddit.com", "quora.com", "instagram.com", "github.com", "apple.com", "google.com",
    "sourceforge.net", "softwaresuggest.com", "selecthub.com", "gartner.com", "saasworthy.com", "slashdot.org",
    "prnewswire.com", "businesswire.com", "globenewswire.com", "techcrunch.com", "forbes.com", "bing.com",
    "yelp.com", "bbb.org", "tracxn.com", "rocketreach.co", "dnb.com", "apollo.io", "signalhire.com", "craft.co",
    "comparably.com", "producthunt.com", "softwareworld.co", "goodfirms.co", "itqlick.com", "financesonline.com",
    "technologyevaluation.com", "erpfocus.com", "betterbuys.com", "softwarereviews.com", "peerspot.com",
    "trustpilot.com", "builtin.com", "wellfound.com", "angel.co", "ycombinator.com", "statista.com", "tiktok.com",
    "pinterest.com", "vimeo.com", "amazon.com", "microsoft.com", "shopify.com", "wordpress.com", "blogspot.com",
    "substack.com", "squarespace.com", "wix.com", "hubspot.com", "salesforce.com", "yahoo.com", "duckduckgo.com",
    "glassdoor.co.uk", "bizjournals.com", "inc.com", "fastcompany.com", "nytimes.com", "wsj.com", "reuters.com",
    "emis.com", "leadiq.com", "lusha.com", "datanyze.com", "6sense.com", "theorg.com", "contactout.com",
    "softwarepundit.com", "capterra.co.uk", "capterra.ca", "getapp.co.uk", "softwareadvice.co.uk", "appvizer.com",
    "saashub.com", "alternativeto.net", "stackshare.io", "slintel.com", "enlyft.com", "similarweb.com",
}


def registrable(url_or_domain: str) -> str:
    """'https://www.app.aims360.com/x' -> 'aims360.com'. Empty string if not a real domain."""
    s = (url_or_domain or "").strip().lower()
    s = re.sub(r"^[a-z]+://", "", s).split("/")[0].split("?")[0].split(":")[0]
    ext = _TLD(s)
    return f"{ext.domain}.{ext.suffix}" if ext.domain and ext.suffix else ""


def is_company_domain(domain: str) -> bool:
    d = registrable(domain)
    return bool(d) and d not in NON_COMPANY_DOMAINS


DOMAIN_IN_TEXT = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|io|net|co|ai|app|software|org|us|biz|tech|cloud|co\.uk|com\.au|ca))\b", re.I)


def prompt(name: str, **fmt: str) -> str:
    text = (PROMPTS / f"{name}.md").read_text("utf-8")
    for k, v in fmt.items():
        text = text.replace("{" + k + "}", v)
    return text


class AmbiguousEntity(Exception):
    def __init__(self, candidates: list[dict], message: str = "company name is ambiguous"):
        super().__init__(message)
        self.candidates = candidates


class StageError(Exception):
    pass


@dataclass
class Ctx:
    settings: Settings
    run_dir: Path
    http: Http
    ledger: CostLedger
    cache: DiskCache
    claude: Claude
    firecrawl: Firecrawl
    web: Web
    evidence: EvidenceStore
    report: dict = field(default_factory=lambda: {"fallbacks": [], "flags": {}, "warnings": []})
    pages: dict = field(default_factory=dict)  # domain -> fetched homepage Page, shared between stages
    strict_offline: bool = False  # recur-deck --offline: a cache miss stops the run (replay tolerates some)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def create(cls, settings: Settings, run_dir: Path, transport=None, claude_client=None) -> "Ctx":
        run_dir.mkdir(parents=True, exist_ok=True)
        cache = DiskCache(settings.cache_dir, read=settings.use_cache or settings.offline)
        ledger = CostLedger()
        http = Http(settings, cache, ledger, transport=transport)
        claude = Claude(settings, cache, ledger, client=claude_client)
        fc = Firecrawl(http, settings.firecrawl_api_key)
        return cls(settings, run_dir, http, ledger, cache, claude, fc, Web(http, fc, claude, settings.fetch_mode),
                   EvidenceStore(run_dir / "evidence.jsonl"))

    # ---------------------------------------------------------------- reporting
    def flag(self, name: str, value: Any = True) -> None:
        with self._lock:
            self.report["flags"][name] = value

    def fallback(self, msg: str) -> None:
        with self._lock:
            if msg not in self.report["fallbacks"]:
                self.report["fallbacks"].append(msg)
        logging.getLogger("recur_deck").warning("fallback: %s", msg)

    def warn(self, msg: str) -> None:
        with self._lock:
            if msg not in self.report["warnings"]:
                self.report["warnings"].append(msg)
        logging.getLogger("recur_deck").warning(msg)

    # ---------------------------------------------------------------- files
    def save_json(self, name: str, data: Any) -> Path:
        p = self.run_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), "utf-8")
        return p

    def load_json(self, name: str) -> Any:
        return json.loads((self.run_dir / name).read_text("utf-8"))

    # ---------------------------------------------------------------- concurrency
    def pmap(self, fn: Callable[[T], R], items: Iterable[T], workers: int = 6) -> list[R | Exception]:
        """Parallel map that never raises: failed items come back as their exception."""
        items = list(items)
        if not items:
            return []

        def safe(x):
            try:
                return fn(x)
            except Exception as e:  # noqa: BLE001 - the caller decides what a failure means
                logging.getLogger("recur_deck").info("parallel task failed: %s: %s", type(e).__name__, e)
                return e

        with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
            return list(ex.map(safe, items))

    def judge_fallbacks(self) -> list[str]:
        """Judges fall back from Sonnet to Haiku on overload/refusal (§10); the writer has no fallback."""
        return [self.settings.model_fast] if self.settings.model_fast != self.settings.model_judge else []
