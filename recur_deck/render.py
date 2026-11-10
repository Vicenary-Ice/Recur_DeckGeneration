"""Render .pptx -> PDF (LibreOffice headless) -> PNG (PyMuPDF), plus an annotated element map for the
vision check. The bundled fonts must be installed (see install_fonts) or line breaks will drift."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from .spec import FONT_DIR, FONTS, SLIDE_W_IN


def soffice_bin() -> str:
    for cand in (os.environ.get("SOFFICE"), shutil.which("soffice"), shutil.which("libreoffice"),
                 "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError("LibreOffice not found. macOS: `brew install --cask libreoffice` (or set SOFFICE=...)")


def install_fonts() -> None:
    """Copy bundled fonts to the user font folder so LibreOffice renders with the real typefaces."""
    dest = Path.home() / ("Library/Fonts" if platform.system() == "Darwin" else ".fonts")
    dest.mkdir(parents=True, exist_ok=True)
    for f in FONT_DIR.glob("*.ttf"):
        target = dest / f.name
        if not target.exists() or target.read_bytes() != f.read_bytes():
            shutil.copy2(f, target)
    if platform.system() == "Linux" and shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f"], check=False, capture_output=True)


# ------------------------------------------------------------------ embedded render fonts
# Installing fonts is not enough on macOS: LibreOffice bundles its own "Noto Sans Arabic" (Arabic + digits, NO
# Latin letters) and that copy wins, so Latin text falls back to a serif. The template embeds MTX-compressed EOT
# subsets that LibreOffice cannot decode. Fix (render copy only, the delivered .pptx is untouched): replace those
# embedded parts with uncompressed EOT wrappers around the full bundled fonts. LibreOffice registers embedded
# fonts per process, ahead of its own, so this works on any OS and without access to the system font service.
RENDER_EMBEDS = {
    "Noto Sans Arabic": {"regular": "NotoSansArabic-Regular.ttf", "bold": "NotoSansArabic-Bold.ttf"},
    "Noto Sans Arabic Light": {"regular": "NotoSansArabic-Light.ttf", "bold": "NotoSansArabic-LightBold.ttf"},
}
# Embedded fonts that share a family / PostScript name with LibreOffice's bundled Arabic-only copy lose to it
# (Latin text then renders blank), so in the render copy both the typeface references and the embedded fonts
# get unique names. Glyphs and metrics are unchanged.
RENDER_ALIASES = {"Noto Sans Arabic": "RecurRender Noto", "Noto Sans Arabic Light": "RecurRender Noto Light"}


@lru_cache(maxsize=None)
def _render_font_eot(fname: str, family: str, style: str) -> bytes:
    import io as _io

    from fontTools.ttLib import TTFont
    f = TTFont(str(FONT_DIR / fname))
    ps = (family + "-" + style.title()).replace(" ", "")
    name = f["name"]
    name.names = [r for r in name.names if r.nameID not in (16, 17)]
    for nid, val in ((1, family), (2, style.title()), (3, "RecurRender;" + ps),
                     (4, family if style == "regular" else f"{family} {style.title()}"), (6, ps)):
        name.setName(val, nid, 3, 1, 0x409)
        name.setName(val, nid, 1, 0, 0)
    buf = _io.BytesIO()
    f.save(buf)
    return eot_wrap(buf.getvalue())


def eot_wrap(ttf: bytes) -> bytes:
    """Uncompressed, unobfuscated EOT 2.1 container around a TrueType font (the format PowerPoint embeds)."""
    import io as _io
    import struct

    from fontTools.ttLib import TTFont
    f = TTFont(_io.BytesIO(ttf))
    os2, nm = f["OS/2"], f["name"]

    def s(n: int) -> bytes:
        v = (nm.getDebugName(n) or "").encode("utf-16-le")
        return struct.pack("<H", 0) + struct.pack("<H", len(v)) + v  # padding + size + string

    p = os2.panose
    body = struct.pack("<LL", len(ttf), 0x00020001) + struct.pack("<L", 0)
    body += bytes([p.bFamilyType, p.bSerifStyle, p.bWeight, p.bProportion, p.bContrast, p.bStrokeVariation,
                   p.bArmStyle, p.bLetterForm, p.bMidline, p.bXHeight])
    body += struct.pack("<BBLHH", 1, 0, os2.usWeightClass, os2.fsType, 0x504C)
    body += struct.pack("<LLLL", os2.ulUnicodeRange1, os2.ulUnicodeRange2, os2.ulUnicodeRange3, os2.ulUnicodeRange4)
    body += struct.pack("<LLL", os2.ulCodePageRange1, os2.ulCodePageRange2, f["head"].checkSumAdjustment)
    body += b"\0" * 16 + s(1) + s(2) + s(5) + s(4) + struct.pack("<HH", 0, 0)  # padding5 + empty root string
    return struct.pack("<L", 4 + len(body) + len(ttf)) + body + ttf


def _embedded_font_parts(zi) -> dict[str, tuple[str, str, str]]:
    """{zip part name: (bundled font file, render family, style)} for embedded fonts we replace."""
    import re
    pres = zi.read("ppt/presentation.xml").decode("utf-8")
    rels = zi.read("ppt/_rels/presentation.xml.rels").decode("utf-8")
    targets = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', rels))
    out = {}
    for typeface, inner in re.findall(r'<p:embeddedFont><p:font typeface="([^"]+)"[^>]*/>(.*?)</p:embeddedFont>', pres):
        for style, rid in re.findall(r"<p:(regular|bold) r:id=\"(rId\d+)\"/>", inner):
            fname = RENDER_EMBEDS.get(typeface, {}).get(style)
            if fname and rid in targets:
                out["ppt/" + targets[rid].lstrip("/").removeprefix("ppt/")] = (fname, RENDER_ALIASES[typeface], style)
    return out


def _render_copy(pptx: Path, dst: Path) -> Path:
    """Copy of the deck for LibreOffice: full fonts embedded under unique names (see RENDER_EMBEDS)."""
    import re
    import zipfile
    pat = re.compile(r'typeface="(' + "|".join(re.escape(k) for k in RENDER_ALIASES) + r')"')
    with zipfile.ZipFile(pptx) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        swap = _embedded_font_parts(zi)
        for it in zi.infolist():
            data = zi.read(it.filename)
            if it.filename in swap:
                data = _render_font_eot(*swap[it.filename])
            elif it.filename.endswith(".xml"):
                data = pat.sub(lambda m: f'typeface="{RENDER_ALIASES[m.group(1)]}"', data.decode("utf-8")).encode("utf-8")
            zo.writestr(it, data)
    return dst


def to_pdf(pptx: Path, out_dir: Path, timeout: int = 180) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = out_dir / (pptx.stem + ".pdf")
    pdf.unlink(missing_ok=True)  # never let a stale PDF from an earlier round pass the existence check
    with tempfile.TemporaryDirectory() as profile, tempfile.TemporaryDirectory() as work:  # isolated profile
        src = _render_copy(pptx, Path(work) / pptx.name)
        cmd = [soffice_bin(), f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
               "--convert-to", "pdf", "--outdir", work, str(src)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
        produced = Path(work) / (pptx.stem + ".pdf")
        if produced.exists():
            shutil.move(str(produced), pdf)
    if not pdf.exists():
        raise RuntimeError(f"LibreOffice produced no PDF for {pptx}")
    return pdf


def to_pngs(pdf: Path, out_dir: Path, pages: range = range(3), dpi: int = 200) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with pymupdf.open(str(pdf)) as doc:
        for i in pages:
            p = out_dir / f"slide{i + 1}.png"
            doc[i].get_pixmap(dpi=dpi).save(str(p))
            paths.append(p)
    return paths


def annotate(png: Path, elements: list[dict], out: Path) -> Path:
    """Draw numbered boxes over the elements we placed so the vision model can name a defect by id."""
    im = Image.open(png).convert("RGB")
    scale = im.width / SLIDE_W_IN
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(FONTS["bold"]), max(14, im.width // 90))
    for n, e in enumerate(elements, 1):
        x0, y0 = e["x"] * scale, e["y"] * scale
        x1, y1 = (e["x"] + e["w"]) * scale, (e["y"] + e["h"]) * scale
        d.rectangle([x0, y0, x1, y1], outline=(255, 64, 64), width=2)
        d.text((x0 + 2, y0 - font.size - 2), f"[{n}] {e['id']}", fill=(255, 220, 0), font=font)
    im.save(out)
    return out
