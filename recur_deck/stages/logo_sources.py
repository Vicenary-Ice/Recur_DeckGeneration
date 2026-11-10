"""S4 - logos for the target and every competitor (BUILD_PROMPT §8.4, §11).

Source order: the company's own site (JSON-LD logo, header/nav inline SVG, header <img> 'logo', SVG icon,
og:image) -> typeset wordmark. Each candidate is whitened by logos.process_logo; a logo with white parts is
whitened both ways (white = knockout, white = ink) because a site header logo may be made for a dark or a light
background. All candidates of one company are checked in a single vision call. Among the candidates that pass,
the winner is (Agent 4, print grade): one whose tagline would not print as micro-text on the map, then a vector
(SVG) source, then the highest native resolution, then source order. The other passing candidates are kept for the
QA loop's swap_logo fix.

Taglines: a lockup whose lower line (e.g. "FASHION TECHNOLOGY") would print under 5pt on the map also goes to the
vision check cropped (id suffix "t"); the cropped mark wins if the check still reads it as the company's logo.
Low-resolution rasters: the chosen raster's @2x / -2x / _2x siblings are tried (direct GETs) when it would print
under 300 dpi on the cover (target) or 200 dpi on the map.
Typeset fallbacks use the short brand name (names.brand_short_name), never a bracketed full name.
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

from .. import layout
from .. import logos as L
from ..clients.http import ServiceError
from ..names import brand_short_name
from ..spec import MAP_LOGO
from .base import Ctx, prompt

log = logging.getLogger("recur_deck.logo_sources")
NAVY = (0x0B, 0x16, 0x30)
MAX_CANDS = 6
MIN_TAGLINE_PT = 5.0
COVER_MIN_DPI, MAP_MIN_DPI = 300, 200
HIRES_SUFFIXES = ("@2x", "-2x", "_2x")


class LogoVerdict(BaseModel):
    candidate_id: str
    is_logo: bool
    text_read: str
    matches_company: bool
    kind: Literal["wordmark", "lockup", "symbol_only", "banner_or_photo", "generic_icon", "other"]
    legible_on_navy: bool
    confidence: float
    tagline_present: bool = False  # a hint only; ink_bands decides whether a tagline is cropped


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


def map_height(aspect: float) -> float:
    """How tall a (non-typeset) logo of this aspect prints on the market map, in inches."""
    return layout.size_for_area(aspect, MAP_LOGO["target_area"], MAP_LOGO["max_w"], MAP_LOGO["max_h"], MAP_LOGO["min_h"])[1]


def tagline_issue(logo_png, printed_height_in: float):
    """qa.Issue `logo_tagline_microtext` when the logo's tagline line prints under 5pt at this height, else None.
    `logo_png` is a path or a PIL image (white logo on transparency)."""
    from ..qa import Issue
    img = logo_png if isinstance(logo_png, Image.Image) else Image.open(logo_png)
    pt = L.tagline_pt(img, printed_height_in)
    if pt is None or pt >= MIN_TAGLINE_PT:
        return None
    tb = L.tagline_band(img)
    how = "crop it" if tb and tb["croppable"] else "use another logo (the bands touch or there are more than 2)"
    return Issue("logo_tagline_microtext", "error", 3, "", f"tagline line prints {pt:.1f}pt tall (min {MIN_TAGLINE_PT:g}pt); "
                 f"{how}", "swap_logo", pt, MIN_TAGLINE_PT)


def _hires_sibling(ctx: Ctx, url: str | None, white: Image.Image, designed_for_dark: bool):
    """A sharper copy of the same raster at an @2x / -2x / _2x sibling URL: (bytes, white, metrics) or None."""
    if not url:
        return None
    m = re.match(r"^(.*?)(\.(?:png|jpe?g|webp|gif))(\?.*)?$", url, re.I)
    if not m:
        return None
    for suf in HIRES_SUFFIXES:
        try:
            data, _ = _download(ctx, f"{m.group(1)}{suf}{m.group(2)}")
        except Exception:  # noqa: BLE001 - a missing sibling is the normal case
            continue
        img, met = L.process_logo(data, designed_for_dark=designed_for_dark)
        if met.ok and met.native_w and abs(met.aspect - white.width / white.height) / max(met.aspect, 1e-6) < 0.05:
            return data, img, met
    return None


def _needed_px(aspect: float, is_target: bool) -> float:
    """Native width the logo needs: 300 dpi on the cover (target), 200 dpi on the map."""
    map_w = map_height(aspect) * aspect
    need = map_w * MAP_MIN_DPI
    if is_target:
        need = max(need, layout.cover_logo_box(aspect).w * COVER_MIN_DPI)
    return need


def _rank_key(c: "Cand", order: int) -> tuple:
    met = c.metrics
    micro = met.get("tagline_pt_map") is not None and met["tagline_pt_map"] < MIN_TAGLINE_PT
    svg = met.get("source_kind") == "svg"
    return (micro, not svg, -(met.get("native_w") or 0) if not svg else 0, order)


def resolve_company(ctx: Ctx, name: str, domain: str, aliases: list[str], homepage: str | None, out_dir: Path,
                    is_target: bool = False) -> dict:
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
            pt = L.tagline_pt(img, map_height(met.aspect)) if met.ok else None
            if pt is not None:
                v.metrics["tagline_pt_map"] = pt
            tried.append({"id": v.id, "source": v.source, "url": v.url, "raw": str(raw_path.relative_to(out_dir.parent)),
                          "white_as_ink": v.designed_for_dark, **v.metrics})
            if met.ok:
                usable.append(v)
            cropped = L.crop_tagline(img) if met.ok and pt is not None and pt < MIN_TAGLINE_PT else None
            if cropped is not None:  # the same mark without its micro-text tagline, checked in the same vision call
                scale = met.height_px / met.native_h if met.native_h else 1.0
                cm = L.measure(cropped, met.source_kind, met.knocked_out_white, (met.native_w or 1, met.native_h or 600), scale)
                ct = Cand(v.id + "t", v.source, v.url, v.data, v.ext, designed_for_dark=v.designed_for_dark)
                ct.white, ct.metrics = cropped, {**cm.as_dict(), "cropped_tagline": True}
                tried.append({"id": ct.id, "source": ct.source, "url": ct.url, "raw": str(raw_path.relative_to(out_dir.parent)),
                              "white_as_ink": ct.designed_for_dark, **ct.metrics})
                if cm.ok:
                    usable.append(ct)
    usable = usable[:MAX_CANDS + 2]
    passed: list[Cand] = []
    if usable:
        images = []
        for c in usable:
            try:
                orig = c.white if c.metrics.get("cropped_tagline") else L.load_rgba(c.data)[0]
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
    order = {id(c): i for i, c in enumerate(usable)}
    passed.sort(key=lambda c: _rank_key(c, order[id(c)]))
    if passed and passed[0].metrics.get("source_kind") != "svg":
        best = passed[0]
        if (best.metrics.get("native_w") or 0) < _needed_px(best.metrics["aspect"], is_target):
            hi = _hires_sibling(ctx, best.url, best.white, best.designed_for_dark)
            if hi is not None and (hi[2].native_w or 0) > (best.metrics.get("native_w") or 0):
                best.white, best.metrics = hi[1], {**hi[2].as_dict(), "hires_sibling": True}
                log.info("logo %s: sharper @2x sibling used (%s px wide)", name, hi[2].native_w)
    keys = ("aspect", "ink_coverage", "ok", "problems", "native_w", "native_h")
    alts = []
    for i, c in enumerate(passed):
        p = L.save(c.white, out_dir / ("" if i == 0 else "_alt") / (f"{slug}.png" if i == 0 else f"{slug}_{i}.png"))
        alts.append({"path": str(p), "source": c.source, "id": c.id, **{k: c.metrics.get(k) for k in keys},
                     **({"cropped_tagline": True} if c.metrics.get("cropped_tagline") else {})})
    if passed:
        chosen = alts[0]
        return {"name": name, "domain": domain, "png": chosen["path"], "source": chosen["source"], "typeset": False,
                "ok": True, "problems": [], "aspect": chosen["aspect"], "ink_coverage": chosen["ink_coverage"],
                "native_w": chosen["native_w"], "native_h": chosen["native_h"], "chosen_id": chosen["id"],
                **({"cropped_tagline": True} if chosen.get("cropped_tagline") else {}),
                "alternatives": alts[1:], "tried": tried}
    return {"name": name, "domain": domain, **typeset(name, out_dir, aliases), "alternatives": [], "tried": tried}


def resolve_all(ctx: Ctx, entity: dict, competitors: list[dict]) -> dict[str, dict]:
    out_dir = ctx.run_dir / "logos"
    jobs = [(entity["display_name"], entity["domain"], entity.get("aliases", []), entity.get("homepage"), True)]
    jobs += [(c["name"], c["domain"], [], c.get("homepage"), False) for c in competitors]
    results = ctx.pmap(lambda j: resolve_company(ctx, *j[:4], out_dir, is_target=j[4]), jobs)
    logos: dict[str, dict] = {}
    for (name, domain, *_), r in zip(jobs, results):
        if isinstance(r, Exception):
            ctx.warn(f"logo sourcing failed for {name}: {type(r).__name__}: {r}; using typeset wordmark")
            r = {"name": name, "domain": domain, **typeset(name, out_dir), "alternatives": [], "tried": []}
        logos[name] = r
    typeset = [n for n, r in logos.items() if r["typeset"]]
    if typeset:
        ctx.flag("typeset_logos", typeset)
    ctx.save_json("logo_metrics.json", logos)
    return logos


def typeset(name: str, out_dir: Path, aliases: list[str] | tuple[str, ...] = ()) -> dict:
    """Typeset wordmark of the short brand name ("Sync", not "Sync (iSync Solutions)"). A name that needs 2 lines
    gets a map text height that keeps each line >= 0.12in (typeset_h)."""
    short = brand_short_name(name, aliases)
    img, met = L.typeset_wordmark(short)
    p = L.save(img, out_dir / f"{L.slug(name)}.png")
    out = {"png": str(p), "source": "typeset", "typeset": True, "ok": True, "problems": [], "aspect": met.aspect,
           "ink_coverage": met.ink_coverage, "typeset_name": short, "lines": met.lines, "native_w": None, "native_h": None}
    if met.lines == 2:
        out["typeset_h"] = round(2 * L.TYPESET_MIN_H + 0.02, 3)
    return out


def crop_logo(ctx: Ctx, name: str, lg: dict, out_dir: Path, aliases: list[str] | tuple[str, ...] = ()) -> dict:
    """QA-loop fix for `logo_tagline_microtext`: crop the tagline band off the placed logo, then check with one
    vision call (cached) that the cropped mark still reads as the company's logo. Updates `lg` in place on success.
    Returns {"fix": "crop_tagline", "applied": bool, ...}."""
    if lg.get("typeset") or lg.get("cropped_tagline"):
        return {"fix": "crop_tagline", "applied": False, "company": name, "why": "typeset or already cropped"}
    img = Image.open(lg["png"]).convert("RGBA")
    cropped = L.crop_tagline(img)
    if cropped is None:
        return {"fix": "crop_tagline", "applied": False, "company": name, "why": "no separate tagline band to crop"}
    try:
        v = ctx.claude.ask(ctx.settings.model_vision, prompt("logo_check"),
                           [("company", {"name": name, "aliases": list(aliases)}),
                            ("candidates", [{"candidate_id": "crop", "source": lg.get("source"), "url": None}])],
                           LogoVerdicts, images=[("candidate_id: crop (tagline cropped)", composite(cropped, cropped))],
                           effort="medium", label=f"logo_check:crop:{L.slug(name)}", fallbacks=ctx.judge_fallbacks())
        verdict = next((x for x in v.verdicts if x.candidate_id == "crop"), None)
    except Exception as e:  # noqa: BLE001 - unverified crops are never used
        return {"fix": "crop_tagline", "applied": False, "company": name, "why": f"vision check failed: {type(e).__name__}"}
    if verdict is None or not accept(verdict, [name, *aliases]):
        return {"fix": "crop_tagline", "applied": False, "company": name,
                "why": f"cropped mark not read as the logo ({verdict.text_read if verdict else 'no verdict'!r})"}
    p = L.save(cropped, out_dir / "_cropped" / f"{L.slug(name)}.png")
    scale = img.height / lg["native_h"] if lg.get("native_h") else 1.0
    lg.update(png=str(p), aspect=round(cropped.width / cropped.height, 3), cropped_tagline=True,
              native_w=round(cropped.width / scale) if lg.get("native_w") else None,
              native_h=round(cropped.height / scale) if lg.get("native_h") else None)
    return {"fix": "crop_tagline", "applied": True, "company": name, "text_read": verdict.text_read}
