"""S4 - logos for the target and every competitor (BUILD_PROMPT §8.4, §11).

Source order: the company's own site (JSON-LD logo, header/nav inline SVG, header <img> 'logo', SVG icon,
og:image) -> typeset wordmark. Each candidate is whitened by logos.process_logo; a logo with white parts is
whitened both ways (white = knockout, white = ink) because a site header logo may be made for a dark or a light
background. All candidates of one company are checked in a single vision call. The first candidate (in source order) that passes wins; the other passing candidates are kept
for the QA loop's swap_logo fix.
"""
from __future__ import annotations

import base64
import difflib
import io
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw
from pydantic import BaseModel

from .. import logos as L
from ..clients.http import ServiceError
from .base import Ctx, prompt

log = logging.getLogger("recur_deck.logo_sources")
NAVY = (0x0B, 0x16, 0x30)
MAX_CANDS = 6


class LogoVerdict(BaseModel):
    candidate_id: str
    is_logo: bool
    text_read: str
    matches_company: bool
    kind: Literal["wordmark", "lockup", "symbol_only", "banner_or_photo", "generic_icon", "other"]
    legible_on_navy: bool
    confidence: float


class LogoVerdicts(BaseModel):
    verdicts: list[LogoVerdict]


@dataclass
class Cand:
    id: str
    source: str
    url: str | None
    data: bytes = b""
    ext: str = "png"
    designed_for_dark: bool = False
    white: Image.Image | None = None
    metrics: dict = field(default_factory=dict)
    verdict: dict | None = None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def fuzzy_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


# Words that never identify a company on their own, so they can't be the "distinctive" first token.
GENERIC_TOKENS = {"the", "software", "systems", "system", "solutions", "technology", "technologies", "group", "inc",
                  "corp", "company", "apparel", "fashion", "cloud", "online", "digital", "global", "erp", "tech",
                  "labs", "app", "apps"}


def first_distinctive_token(name: str) -> str | None:
    """The first non-generic word of the name, if it has >= 4 characters: 'InStyle Apparel Software' -> 'instyle',
    'The Sync Group' -> 'sync', 'CGS Fashion' -> None (a short first word is not distinctive enough, and the
    next word is not used instead: it would usually be a descriptor)."""
    for tok in re.findall(r"[A-Za-z0-9]+", name or ""):
        t = tok.lower()
        if t in GENERIC_TOKENS:
            continue
        return t if len(t) >= 4 else None
    return None


def text_matches_name(text_read: str, name: str) -> bool:
    """Does the wordmark text the vision model read belong to `name`? Lockups add taglines ('AIMS360 FASHION
    TECHNOLOGY', 'BlueCherry by CGS') and names add descriptors ('InStyle Apparel Software'), so containment
    either way counts, as does the name's first distinctive word, or a close fuzzy match."""
    t, n = _norm(text_read), _norm(name)
    if not t or not n:
        return False
    if n in t or t in n:
        return True
    tok = first_distinctive_token(name)
    if tok and tok in t:
        return True
    return difflib.SequenceMatcher(None, t, n).ratio() >= 0.6


def accept(v: LogoVerdict, names: list[str]) -> bool:
    """`names` = the company name followed by its aliases."""
    if not (v.is_logo and v.matches_company and v.kind in ("wordmark", "lockup") and v.legible_on_navy
            and v.confidence >= 0.7):
        return False
    return not v.text_read.strip() or any(text_matches_name(v.text_read, n) for n in names if n)


# ------------------------------------------------------------------ site scraping
def _ext_of(url: str, ct: str = "") -> str:
    path = urlsplit(url).path.lower()
    for e in ("svg", "png", "jpg", "jpeg", "webp", "gif", "ico"):
        if path.endswith("." + e) or e in ct:
            return "jpg" if e == "jpeg" else e
    return "svg" if "svg" in ct else "png"


def _largest_from_srcset(srcset: str) -> str | None:
    best, best_w = None, -1.0
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = 1.0
        if len(bits) > 1:
            try:
                w = float(bits[1].rstrip("wx"))
            except ValueError:
                pass
        if w > best_w:
            best, best_w = bits[0], w
    return best


def site_candidates(html: str, base_url: str, names: list[str], og_image: str | None = None) -> list[tuple[str, str | None, bytes | None]]:
    """[(source, url, inline_bytes)] in §11 order. Inline SVGs come back as bytes with url None."""
    soup = BeautifulSoup(html or "", "lxml")
    out: list[tuple[str, str | None, bytes | None]] = []
    seen: set[str] = set()

    def add(src: str, url: str | None, data: bytes | None = None):
        if url:
            if url.startswith("data:image/"):
                try:
                    head, b64 = url.split(",", 1)
                    data, url = (base64.b64decode(b64) if ";base64" in head else b64.encode()), None
                except ValueError:
                    return
            else:
                url = urljoin(base_url, url)
                if url in seen:
                    return
                seen.add(url)
        out.append((src, url, data))

    # 1. JSON-LD Organization logo
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop(0)
            if isinstance(o, dict):
                stack.extend(o.get("@graph", []) if isinstance(o.get("@graph"), list) else [])
                types = o.get("@type") or []
                types = types if isinstance(types, list) else [types]
                if any(t in ("Organization", "Corporation", "LocalBusiness", "SoftwareCompany", "Brand") for t in types):
                    lg = o.get("logo")
                    if isinstance(lg, dict):
                        lg = lg.get("url") or lg.get("contentUrl")
                    if isinstance(lg, str) and lg:
                        add("jsonld", lg)
            elif isinstance(o, list):
                stack.extend(o)

    root = urlsplit(base_url)
    root_hrefs = {"/", f"{root.scheme}://{root.netloc}", f"{root.scheme}://{root.netloc}/"}
    home_links = [a for a in soup.find_all("a", href=True) if a["href"].strip() in root_hrefs][:3]
    not_ours = re.compile(r"client|customer|partner|trust|testimonial|award|integration|footer|press|featured", re.I)

    def attrs(el) -> str:
        return " ".join([" ".join(el.get("class", [])), el.get("id", "") or ""])

    div_scopes = [el for el in soup.select('[class*="header" i], [id*="header" i], [class*="navbar" i], '
                                           '[class*="logo" i], [id*="logo" i], [class*="brand" i]')
                  if not not_ours.search(attrs(el)) and not any(not_ours.search(attrs(p)) for p in el.parents if p.name)]
    # (rank, scope): real header / nav / home link first, div-based headers after
    scopes = [(0, sc) for sc in soup.find_all(["header", "nav"])[:4] + home_links] + [(1, sc) for sc in div_scopes[:8]]
    name_pat = re.compile("|".join(re.escape(n.lower()) for n in names if n) or "logo", re.I)

    # 2. inline SVG in header / nav / home link (at most 2; ones that name the company first)
    svgs, seen_svg = [], set()
    for rank, sc in scopes:
        for svg in sc.find_all("svg")[:3]:
            s = str(svg)
            ids = set(re.findall(r'\bid="([^"]+)"', s))
            refs = re.findall(r'(?:xlink:)?href="#([^"]+)"', s)
            if any(r not in ids for r in refs) or len(s) < 200 or s in seen_svg:
                continue  # sprite reference, tiny icon (hamburger, chevron), or already seen
            seen_svg.add(s)
            label = " ".join([svg.get("aria-label", ""), (svg.title.string if svg.title and svg.title.string else ""), attrs(svg)])
            svgs.append((rank, 0 if name_pat.search(label) or "logo" in label.lower() else 1, len(svgs), s))
    for *_, s in sorted(svgs)[:2]:
        if "xmlns=" not in s[:300]:
            s = s.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
        add("header_svg", None, s.encode("utf-8"))

    # 3. <img> logo in header / nav / home link (at most 2)
    imgs = []
    for rank, sc in scopes:
        for img in sc.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
            if img.get("srcset") or img.get("data-srcset"):
                src = _largest_from_srcset(img.get("srcset") or img.get("data-srcset")) or src
            hay = " ".join([src, img.get("alt", ""), attrs(img)]).lower()
            if src and ("logo" in hay or name_pat.search(hay)):
                try:
                    w = float(re.sub(r"[^\d.]", "", str(img.get("width", "0"))) or 0)
                except ValueError:
                    w = 0.0
                imgs.append((rank, 0 if name_pat.search(hay) else 1, 0 if ".svg" in src.lower() else 1, -w, src))
    for *_, src in sorted(dict.fromkeys(imgs))[:2]:
        add("header_img", src)

    # 4. SVG icon (symbol only, last resort before typeset)
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel", [])).lower()
        if "icon" in rel and (link.get("type") == "image/svg+xml" or link["href"].lower().endswith(".svg")):
            add("icon_svg", link["href"])
            break
    # 5. og:image (accepted only if the vision check says wordmark / lockup)
    og = og_image or (soup.find("meta", attrs={"property": "og:image"}) or {}).get("content")
    if og:
        add("og_image", og)
    return out


# ------------------------------------------------------------------ vision composite
def _checker(w: int, h: int) -> Image.Image:
    im = Image.new("RGB", (w, h), (150, 155, 160))
    d = ImageDraw.Draw(im)
    for y in range(0, h, 16):
        for x in range(0, w, 16):
            if (x // 16 + y // 16) % 2:
                d.rectangle([x, y, x + 15, y + 15], fill=(175, 180, 185))
    return im


def composite(original: Image.Image, white: Image.Image) -> bytes:
    """Original (on a mid-grey checkerboard) | white version on navy #0B1630."""
    W, H, pad = 560, 200, 20

    def fit(im: Image.Image) -> Image.Image:
        im = im.copy()
        im.thumbnail((W - 2 * pad, H - 2 * pad), Image.LANCZOS)
        return im

    canvas = Image.new("RGB", (2 * W + 10, H), (255, 255, 255))
    left, right = _checker(W, H), Image.new("RGB", (W, H), NAVY)
    o, w = fit(L.trim(original.convert("RGBA"))), fit(white)
    left.paste(o, ((W - o.width) // 2, (H - o.height) // 2), o)
    right.paste(w, ((W - w.width) // 2, (H - w.height) // 2), w)
    canvas.paste(left, (0, 0))
    canvas.paste(right, (W + 10, 0))
    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ per company
def _download(ctx: Ctx, url: str) -> tuple[bytes, str]:
    data, ct, final = ctx.http.get_bytes(url, kind="image", max_bytes=5_000_000)
    return data, _ext_of(final, ct)


def _gather(ctx: Ctx, name: str, domain: str, aliases: list[str], homepage: str | None) -> list[Cand]:
    cands: list[Cand] = []
    page = ctx.pages.get(domain)
    if page is None or not page.raw_html or page.main_only:  # header/nav may be stripped from main-only scrapes
        page = ctx.web.fetch(homepage or f"https://{domain}", main_only=False) or page
        if page is not None:
            ctx.pages[domain] = page
    if page is None or not page.raw_html:
        return cands
    for src, url, inline in site_candidates(page.raw_html, page.final_url or f"https://{domain}", [name, *aliases], page.og_image):
        if len(cands) >= MAX_CANDS:
            break
        try:
            data, ext = (inline, "svg") if inline is not None else _download(ctx, url)
        except ServiceError as e:
            log.info("logo download failed %s: %s", url, e)
            continue
        cands.append(Cand(f"c{len(cands) + 1}", src, url, data, ext))
    return cands


def resolve_company(ctx: Ctx, name: str, domain: str, aliases: list[str], homepage: str | None, out_dir: Path) -> dict:
    slug = L.slug(name)
    raw_dir = out_dir / "_raw" / slug
    cands = _gather(ctx, name, domain, aliases, homepage)
    tried, usable = [], []
    for c in cands:
        raw_path = raw_dir / f"{c.id[1:]}_{c.source}.{c.ext}"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(c.data)
        variants = [(c, L.process_logo(c.data, designed_for_dark=False))]
        if variants[0][1][1].knocked_out_white and variants[0][1][1].source_kind in ("svg", "raster-alpha"):
            alt = Cand(c.id + "w", c.source, c.url, c.data, c.ext, designed_for_dark=True)  # white kept as ink
            variants.append((alt, L.process_logo(c.data, designed_for_dark=True)))
        for v, (img, met) in variants:
            v.white, v.metrics = img, met.as_dict()
            tried.append({"id": v.id, "source": v.source, "url": v.url, "raw": str(raw_path.relative_to(out_dir.parent)),
                          "white_as_ink": v.designed_for_dark, **v.metrics})
            if met.ok:
                usable.append(v)
    usable = usable[:MAX_CANDS]
    passed: list[Cand] = []
    if usable:
        images = []
        for c in usable:
            try:
                orig, _ = L.load_rgba(c.data)
            except Exception:  # noqa: BLE001 - process_logo already decoded it once
                orig = c.white
            images.append((f"candidate_id: {c.id} (source: {c.source})", composite(orig, c.white)))
        try:
            verdicts = ctx.claude.ask(
                ctx.settings.model_vision, prompt("logo_check"),
                [("company", {"name": name, "domain": domain, "aliases": aliases}),
                 ("candidates", [{"candidate_id": c.id, "source": c.source, "url": c.url} for c in usable])],
                LogoVerdicts, images=images, effort="medium", label=f"logo_check:{domain}", fallbacks=ctx.judge_fallbacks())
            by_id = {v.candidate_id: v for v in verdicts.verdicts}
        except Exception as e:  # noqa: BLE001 - without a check, never trust an unverified logo
            ctx.warn(f"logo check failed for {name} ({type(e).__name__}); using typeset wordmark")
            by_id = {}
        for c in usable:
            v = by_id.get(c.id)
            if v:
                c.verdict = v.model_dump()
                for t in tried:
                    if t["id"] == c.id:
                        t["verdict"] = c.verdict
                if accept(v, [name, *aliases]):
                    passed.append(c)
    alts = []
    for i, c in enumerate(passed):
        p = L.save(c.white, out_dir / ("" if i == 0 else "_alt") / (f"{slug}.png" if i == 0 else f"{slug}_{i}.png"))
        alts.append({"path": str(p), "source": c.source, "id": c.id, **{k: c.metrics[k] for k in ("aspect", "ink_coverage", "ok", "problems")}})
    if passed:
        chosen = alts[0]
        return {"name": name, "domain": domain, "png": chosen["path"], "source": chosen["source"], "typeset": False,
                "ok": True, "problems": [], "aspect": chosen["aspect"], "ink_coverage": chosen["ink_coverage"],
                "alternatives": alts[1:], "tried": tried}
    img, met = L.typeset_wordmark(name)
    p = L.save(img, out_dir / f"{slug}.png")
    return {"name": name, "domain": domain, "png": str(p), "source": "typeset", "typeset": True, "ok": True,
            "problems": [], "aspect": met.aspect, "ink_coverage": met.ink_coverage, "alternatives": [], "tried": tried}


def resolve_all(ctx: Ctx, entity: dict, competitors: list[dict]) -> dict[str, dict]:
    out_dir = ctx.run_dir / "logos"
    jobs = [(entity["display_name"], entity["domain"], entity.get("aliases", []), entity.get("homepage"))]
    jobs += [(c["name"], c["domain"], [], c.get("homepage")) for c in competitors]
    results = ctx.pmap(lambda j: resolve_company(ctx, *j, out_dir), jobs)
    logos: dict[str, dict] = {}
    for (name, domain, *_), r in zip(jobs, results):
        if isinstance(r, Exception):
            ctx.warn(f"logo sourcing failed for {name}: {type(r).__name__}: {r}; using typeset wordmark")
            img, met = L.typeset_wordmark(name)
            p = L.save(img, out_dir / f"{L.slug(name)}.png")
            r = {"name": name, "domain": domain, "png": str(p), "source": "typeset", "typeset": True, "ok": True,
                 "problems": [], "aspect": met.aspect, "ink_coverage": met.ink_coverage, "alternatives": [], "tried": []}
        logos[name] = r
    typeset = [n for n, r in logos.items() if r["typeset"]]
    if typeset:
        ctx.flag("typeset_logos", typeset)
    ctx.save_json("logo_metrics.json", logos)
    return logos


def typeset(name: str, out_dir: Path) -> dict:
    """Typeset fallback used by the QA loop when a logo is swapped out and no alternative is left."""
    img, met = L.typeset_wordmark(name)
    p = L.save(img, out_dir / f"{L.slug(name)}.png")
    return {"png": str(p), "source": "typeset", "typeset": True, "ok": True, "problems": [], "aspect": met.aspect,
            "ink_coverage": met.ink_coverage}
