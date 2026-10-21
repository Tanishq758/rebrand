"""
loaders.py - normalise brand.json / content.json into the clean dicts that
renderer.render_full_page() expects.

Several real-world shapes are accepted, so the standalone renderer works with
files from this tool AND from the Branded Content Generator pipeline:

BRAND
  simple     {"brand_name", "colors": {"primary": "#hex"}, "typography": {...}}
  extractor  {"page_title", "colors": {"primary_color_guess",
              "primary_background_guess", "text_colors": [...],
              "background_colors": [...]}, "typography": {"font_families": [...]}}

CONTENT
  simple     {"sections": [{"heading", "body", "bullets"}]}
  blocks     {"content_blocks": [{"type": heading|paragraph|list, ...}]}
  outline    {"outline": {"sections": [hero|feature_callout|card_grid|
              statement|link_list|list_section, ...]}}
"""
from __future__ import annotations

import colorsys
import re
from urllib.parse import urlparse

from .extractors import hex_to_rgb, rgb_to_hex, luminance, saturation

_RGB_FN = re.compile(r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})")
_HSL_FN = re.compile(r"hsla?\(\s*([\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%")
_GENERIC = {"inherit", "initial", "unset", "system-ui", "serif", "sans-serif", "monospace",
            "arial", "helvetica", "-apple-system", "blinkmacsystemfont", "segoe ui", "roboto"}


class InputError(ValueError):
    """Raised with a human-readable message for a bad input file."""


# ---------------------------------------------------------------------------
# colour parsing
# ---------------------------------------------------------------------------

def parse_color(value) -> tuple | None:
    """Parse #hex / rgb() / hsl() into an (r,g,b) tuple. Skips translucent colours."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if v.startswith("#"):
        raw = v.lstrip("#")
        if len(raw) == 8 and int(raw[6:8], 16) / 255 < 0.6:
            return None
        return hex_to_rgb(v)
    m = _RGB_FN.match(v)
    if m:
        if re.search(r"[/,]\s*(0?\.\d+)\s*\)", v) and float(re.search(r"[/,]\s*(0?\.\d+)\s*\)", v).group(1)) < 0.6:
            return None
        return tuple(min(255, int(m.group(i))) for i in (1, 2, 3))
    m = _HSL_FN.match(v)
    if m:
        h, s, l = float(m.group(1)) / 360, float(m.group(2)) / 100, float(m.group(3)) / 100
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return (round(r * 255), round(g * 255), round(b * 255))
    return None


def _is_brandy(rgb) -> bool:
    """A colour a person would call 'the brand colour': real chroma, and not
    so dark or so light that it reads as a neutral. Slate/charcoal greys with
    a whisper of blue (saturation ~0.25) are deliberately NOT brandy."""
    return 0.008 <= luminance(rgb) <= 0.65 and saturation(rgb) >= 0.55


def _weighted_candidates(colors: dict) -> list[tuple[tuple, int]]:
    out = []
    for key in ("background_colors", "text_colors"):
        for item in colors.get(key, []) or []:
            rgb = parse_color(item.get("value") if isinstance(item, dict) else item)
            if rgb:
                out.append((rgb, int(item.get("count", 1)) if isinstance(item, dict) else 1))
    return out


def _merged_usage(colors: dict) -> dict:
    """Total use of each exact colour across the text and background lists."""
    merged: dict[tuple, int] = {}
    for rgb, n in _weighted_candidates(colors):
        merged[rgb] = merged.get(rgb, 0) + n
    return merged


def pick_primary(colors: dict) -> tuple[str, str]:
    """Choose a real brand colour. Returns (hex, note explaining the choice).

    All candidate colours are scored together. The extractor's own guess is
    only a nudge: extractors commonly report white (most frequent text colour)
    or a dark slate panel (biggest background) - neither is the brand colour.
    Score = how often it is used x how chromatic it is, so a vivid colour used
    often beats a dull one used a bit more.
    """
    if not isinstance(colors, dict):
        raise InputError("brand.json 'colors' must be an object.")

    explicit = colors.get("primary")
    if explicit:
        rgb = parse_color(explicit)
        if rgb:
            return rgb_to_hex(rgb), "used colors.primary as given"

    merged = _merged_usage(colors)
    guesses = {parse_color(colors.get(k)) for k in ("primary_background_guess", "primary_color_guess")}
    guesses.discard(None)

    scored = []
    for rgb, n in merged.items():
        if not _is_brandy(rgb):
            continue
        score = n * (0.4 + saturation(rgb) ** 2)
        if rgb in guesses:
            score *= 1.25  # the extractor's guess gets a nudge, not a veto
        scored.append((score, rgb, n))
    if scored:
        scored.sort(reverse=True)
        _, best, n = scored[0]
        nudged = " (also the extractor's guess)" if best in guesses else ""
        return rgb_to_hex(best), f"most-used chromatic colour, {n} uses{nudged}"

    # No vivid colour at all (a genuinely monochrome brand): trust a guess that
    # at least has some chroma, else fall back to a neutral.
    for key in ("primary_background_guess", "primary_color_guess"):
        rgb = parse_color(colors.get(key))
        if rgb and 0.01 < luminance(rgb) < 0.85 and saturation(rgb) >= 0.12:
            return rgb_to_hex(rgb), f"monochrome brand; used {key}"
    return "#334155", "no usable brand colour found; used a neutral slate"


def pick_accent(colors: dict, primary_hex: str) -> str | None:
    """A second, distinct brand colour if the file has one."""
    p = hex_to_rgb(primary_hex)
    if not p:
        return None
    ph = colorsys.rgb_to_hsv(*(c / 255 for c in p))[0]
    merged: dict[tuple, int] = {}
    for rgb, n in _weighted_candidates(colors):
        if _is_brandy(rgb):
            merged[rgb] = merged.get(rgb, 0) + n
    best, best_score = None, 0
    for rgb, n in merged.items():
        hh = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0]
        d = min(abs(hh - ph), 1 - abs(hh - ph))
        if d > 0.08 and n > best_score:
            best, best_score = rgb, n
    return rgb_to_hex(best) if best else None


# ---------------------------------------------------------------------------
# fonts
# ---------------------------------------------------------------------------

def pick_fonts(typography) -> list[str]:
    fams = []
    if isinstance(typography, dict):
        raw = typography.get("font_families") or typography.get("fonts") or []
        heading = typography.get("heading_font") or typography.get("heading")
        body = typography.get("body_font") or typography.get("body")
        for name in (heading, body):
            if isinstance(name, str) and name.strip():
                fams.append(name)
        for item in raw:
            fams.append(item.get("value") if isinstance(item, dict) else item)
    elif isinstance(typography, list):
        fams = [t.get("name") if isinstance(t, dict) else t for t in typography]

    out = []
    for f in fams:
        if not isinstance(f, str):
            continue
        for part in f.split(","):
            n = part.strip().strip("\"'")
            # drop generics, CSS vars and build-hashed names like __geistSans_1e4310
            if not n or n.lower() in _GENERIC or n.startswith("__") or n.startswith("var("):
                continue
            if n.lower() not in {x.lower() for x in out}:
                out.append(n)
    return out[:3]


# ---------------------------------------------------------------------------
# brand loader
# ---------------------------------------------------------------------------

def _name_from_source(data: dict) -> str:
    """Best-guess brand name from page_title + source_url.

    The domain is the strongest signal: if a word (or word run) in the title
    matches the hostname label, that is the brand - 'W3Schools Online Web
    Tutorials' + w3schools.com -> 'W3Schools'. Otherwise fall back to the
    shortest title segment.
    """
    title = (data.get("page_title") or "").strip()
    host = urlparse(data.get("source_url") or "").netloc.lower().replace("www.", "")
    label = re.sub(r"[^a-z0-9]", "", host.split(".")[0]) if host else ""

    if title and label:
        words = title.split()
        # try runs of 1-3 words whose letters/digits concatenate to the host label
        for size in (1, 2, 3):
            for i in range(len(words) - size + 1):
                run = words[i:i + size]
                joined = re.sub(r"[^a-z0-9]", "", "".join(run).lower())
                if joined == label:
                    return re.sub(r"^[^\w]+|[^\w]+$", "", " ".join(run))[:40]

    if title:
        parts = [p for p in re.split(r"\s+[|\u2013\u2014\-:\u00b7\\]\s+", title) if p.strip()]
        if parts:
            return (min(parts, key=len) if len(parts) > 1 else parts[0]).strip()[:40]
    return label.capitalize() if label else ""


def load_brand(data: dict) -> tuple[dict, list[str]]:
    """Return (brand dict for the renderer, list of human-readable notes)."""
    if not isinstance(data, dict):
        raise InputError("brand.json must contain a JSON object.")
    notes: list[str] = []

    name = (data.get("brand_name") or data.get("name") or "").strip() or _name_from_source(data)
    if not name:
        name = "Brand"
        notes.append("no brand name found; used 'Brand'")

    colors = data.get("colors")
    if colors is None:
        raise InputError("brand.json is missing a 'colors' section.")
    if isinstance(colors, list):  # ["#aaa", "#bbb"] shorthand
        colors = {"background_colors": [{"value": c, "count": 1} for c in colors]}
    primary, why = pick_primary(colors)
    notes.append(f"primary {primary}: {why}")
    accent = pick_accent(colors, primary) if isinstance(colors, dict) else None

    fonts = pick_fonts(data.get("typography") or data.get("fonts"))
    if not fonts:
        notes.append("no named fonts found; using the system font stack")

    brand = {"brand_name": name, "colors": {"primary": primary, "accent": accent},
             "fonts": fonts, "tone": data.get("tone")}
    return brand, notes


# ---------------------------------------------------------------------------
# content loaders
# ---------------------------------------------------------------------------

# Zero-width / invisible characters that scraped text often carries. They are
# not whitespace to str.strip(), so an "empty" heading made only of them looks
# non-empty and renders as a blank <h2>.
_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f\u202a-\u202e]")


def _clean(t) -> str:
    if not isinstance(t, str):
        return ""
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", t)).strip()


def _sections_from_blocks(blocks: list) -> list[dict]:
    """content_blocks -> sections: each h1/h2 starts one; h3s become bullets."""
    sections: list[dict] = []
    cur = None
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "heading":
            text, lvl = _clean(b.get("text")), int(b.get("level", 2) or 2)
            if not text:
                continue
            if lvl <= 2 or cur is None:
                cur = {"heading": text, "body": "", "bullets": []}
                sections.append(cur)
            elif len(cur["bullets"]) < 6:
                cur["bullets"].append(text)
        elif cur is not None:
            if t == "paragraph":
                text = _clean(b.get("text"))
                if text and not cur["body"]:
                    cur["body"] = text
                elif text and len(cur["bullets"]) < 6:
                    cur["bullets"].append(text)
            elif t == "list":
                for it in b.get("items") or []:
                    it = _clean(it)
                    if it and len(cur["bullets"]) < 6:
                        cur["bullets"].append(it)
    return sections


def _sections_from_outline(outline_sections: list) -> list[dict]:
    out = []
    for s in outline_sections:
        if not isinstance(s, dict):
            continue
        typ = s.get("type")
        sec = {"heading": _clean(s.get("heading")), "body": _clean(s.get("body") or s.get("intro")),
               "bullets": []}
        if typ == "card_grid":
            for c in s.get("cards") or []:
                h = _clean(c.get("heading"))
                if h:
                    sec["bullets"].append(h)
        elif typ == "link_list":
            for it in s.get("items") or []:
                t = _clean(it.get("text"))
                if t:
                    sec["bullets"].append(t)
        elif typ == "list_section":
            for grp in s.get("item_groups") or []:
                for it in grp:
                    it = _clean(it)
                    if it:
                        sec["bullets"].append(it)
        sec["bullets"] = sec["bullets"][:6]
        if sec["heading"] or sec["body"]:
            out.append(sec)
    return out


def load_content(data: dict) -> tuple[dict, list[str]]:
    if not isinstance(data, dict):
        raise InputError("content.json must contain a JSON object.")
    notes: list[str] = []

    if isinstance(data.get("sections"), list):
        raw = [s for s in data["sections"] if isinstance(s, dict)]
        sections = []
        for s in raw:
            b = s.get("bullets") or s.get("items") or []
            sections.append({
                "heading": _clean(s.get("heading") or s.get("title")),
                "body": _clean(s.get("body") or s.get("text")),
                "bullets": [_clean(x) for x in b if _clean(x)],
            })
        shape = "simple"
    elif isinstance(data.get("content_blocks"), list):
        sections, shape = _sections_from_blocks(data["content_blocks"]), "content_blocks"
    elif isinstance(data.get("outline"), dict) and isinstance(data["outline"].get("sections"), list):
        sections, shape = _sections_from_outline(data["outline"]["sections"]), "outline"
    else:
        raise InputError(
            "content.json not recognised. Expected 'sections', 'content_blocks' or 'outline.sections'.")

    sections = [s for s in sections if s["heading"] or s["body"]]
    if not sections:
        raise InputError("content.json has no usable sections (every heading/body is empty).")
    for i, s in enumerate(sections):
        s["role"] = "hero" if i == 0 else "content"
    notes.append(f"content shape: {shape}, {len(sections)} sections")
    return {"sections": sections}, notes
