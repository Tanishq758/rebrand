"""
extractors.py - turn raw HTML/CSS into structured data.

  parse_page()            title, description, brand name, headings, text
  extract_real_sections() the page's own sections in document order
  extract_colors()        K-means palette + a primary that is not white/black/grey
  extract_fonts()         font-family rules + Google Fonts links
  detect_tone()           lexicon scoring across five tone dimensions

Nothing here is specific to any one website.
"""
from __future__ import annotations

import colorsys
import re
from collections import Counter

import numpy as np
from bs4 import BeautifulSoup
from sklearn.cluster import KMeans

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_RGB_RE = re.compile(
    r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})(?:[\s,/]+([\d.]+%?))?\s*\)", re.I
)


def hex_to_rgb(value: str):
    v = (value or "").strip().lstrip("#")
    if len(v) == 8:
        v = v[:6]
    if len(v) == 4:
        v = v[:3]
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        return None
    try:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def rgb_to_hex(rgb) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def luminance(rgb) -> float:
    """WCAG relative luminance, 0 (black) to 1 (white)."""
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def saturation(rgb) -> float:
    r, g, b = (c / 255.0 for c in rgb)
    return colorsys.rgb_to_hsv(r, g, b)[1]


def _alpha_ok(alpha) -> bool:
    """Skip mostly-transparent colours - they are overlays, not brand colours."""
    if alpha is None:
        return True
    try:
        a = float(alpha[:-1]) / 100 if str(alpha).endswith("%") else float(alpha)
    except ValueError:
        return True
    return a >= 0.6


def mine_colors(text: str) -> list[tuple[int, int, int]]:
    """Regex-mine every hex / rgb() colour from HTML + CSS."""
    found: list[tuple[int, int, int]] = []
    for m in _HEX_RE.finditer(text):
        raw = m.group(0)
        if len(raw) == 9:  # #rrggbbaa
            alpha = int(raw[7:9], 16) / 255.0
            if alpha < 0.6:
                continue
        rgb = hex_to_rgb(raw)
        if rgb:
            found.append(rgb)
    for m in _RGB_RE.finditer(text):
        if not _alpha_ok(m.group(4)):
            continue
        rgb = tuple(min(255, int(m.group(i))) for i in (1, 2, 3))
        found.append(rgb)
    return found


def extract_colors(html: str, css: str, k: int = 6) -> dict:
    """K-means reduces the mined colours to a dominant palette.

    The primary colour is the most dominant cluster that has mid-range
    luminance AND real chroma - so plain white/black/grey (which dominate the
    raw counts on almost every site) can never be picked as the brand colour.
    """
    mined = mine_colors(css + "\n" + html)
    result = {"palette": [], "primary": None, "accent": None, "raw_count": len(mined), "fallback": False}

    if len(mined) >= 3:
        pts = np.array(mined, dtype=float)
        uniq = np.unique(pts, axis=0)
        n_clusters = int(min(k, len(uniq)))
        km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42).fit(pts)
        counts = Counter(km.labels_)
        clusters = []
        for idx, center in enumerate(km.cluster_centers_):
            rgb = tuple(int(round(c)) for c in center)
            clusters.append(
                {"hex": rgb_to_hex(rgb), "rgb": rgb, "weight": counts[idx] / len(pts),
                 "lum": luminance(rgb), "sat": saturation(rgb)}
            )
        clusters.sort(key=lambda c: c["weight"], reverse=True)
        result["palette"] = [
            {"hex": c["hex"], "weight": round(c["weight"], 3)} for c in clusters
        ]

        def is_brandy(c):
            # Saturation, not brightness, is what separates a brand colour from
            # a neutral. Dark navy / burgundy / forest (lum ~0.015-0.05) are among
            # the commonest brand colours, so the luminance floor is near zero;
            # greys and bluish slates (sat 0-0.45) are what must be excluded.
            return 0.008 <= c["lum"] <= 0.62 and c["sat"] >= 0.55

        candidates = [c for c in clusters if is_brandy(c)]
        if candidates:
            # weight dominance, with a small nudge for stronger chroma
            candidates.sort(key=lambda c: c["weight"] * (0.6 + c["sat"]), reverse=True)
            result["primary"] = candidates[0]["hex"]
            others = [c for c in candidates[1:] if _hue_distance(c["rgb"], candidates[0]["rgb"]) > 0.08]
            if others:
                result["accent"] = others[0]["hex"]

    if not result["primary"]:
        # Honest fallback: a neutral slate, flagged so the UI can say so.
        result["primary"] = "#334155"
        result["fallback"] = True
    return result


def _hue_distance(a, b) -> float:
    ha = colorsys.rgb_to_hsv(*(c / 255 for c in a))[0]
    hb = colorsys.rgb_to_hsv(*(c / 255 for c in b))[0]
    d = abs(ha - hb)
    return min(d, 1 - d)


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

_GENERIC_FONTS = {
    "inherit", "initial", "unset", "serif", "sans-serif", "monospace", "cursive",
    "fantasy", "system-ui", "ui-sans-serif", "ui-serif", "ui-monospace", "-apple-system",
    "blinkmacsystemfont", "segoe ui", "roboto", "helvetica", "helvetica neue", "arial",
    "emoji", "apple color emoji", "segoe ui emoji", "noto color emoji",
}
_FONT_RULE_RE = re.compile(r"font-family\s*:\s*([^;}{]+)", re.I)
_GFONT_RE = re.compile(r"fonts\.googleapis\.com/css2?\?([^\"'>\s]+)", re.I)


# Icon and symbol fonts draw glyphs, not words - never a brand's text face.
_ICON_FONT = re.compile(
    r"awesome|icon|glyph|symbol|material|emoji|dingbat|codicon|octicon|feather|"
    r"bootstrap|ionicons|entypo|typicons|lucide|remix|boxicons|line-?awesome", re.I)


_MONO_FONT = re.compile(r"mono|code|consolas|courier|menlo|fira ?code|jetbrains|cascadia|hack\b|terminal", re.I)


def _clean_family(name: str) -> str:
    """Strip quotes and CSS escapes: the source text 'Source Sans\\ 3' is how CSS
    writes the family 'Source Sans 3' (an escaped space)."""
    name = name.strip().strip("\"'").strip()
    name = re.sub(r"\\(.)", r"\1", name)      # unescape:  \<space> -> space
    return re.sub(r"\s+", " ", name).strip()


def extract_fonts(html: str, css: str, limit: int = 4) -> list[dict]:
    """Return fonts ranked by use: [{'name', 'source': 'google'|'css'}]."""
    counts: Counter = Counter()
    google: set[str] = set()

    for m in _GFONT_RE.finditer(html + css):
        query = m.group(1).replace("&amp;", "&")
        for fam in re.findall(r"family=([^&:;]+)", query):
            name = _clean_family(fam.replace("+", " "))
            if name:
                google.add(name)
                counts[name] += 5  # explicitly loaded => strong signal

    for m in _FONT_RULE_RE.finditer(css + "\n" + html):
        for part in m.group(1).split(","):
            name = _clean_family(part)
            low = name.lower()
            if not name or low in _GENERIC_FONTS or low.startswith("var(") or low.startswith("__"):
                continue
            if len(name) > 40 or re.search(r"[{}()]", name) or _ICON_FONT.search(name):
                continue
            counts[name] += 1

    # Monospace faces (code samples, terminals) are rarely a brand's text face.
    # Keep them, but rank them after every proportional font so the renderer's
    # heading/body picks (the first two) are real text fonts.
    def is_mono(n):
        return bool(_MONO_FONT.search(n))

    # A font used once or twice on a page that uses another 44 times is an
    # accent (a logotype, one hero word), not the brand's face. Require a real
    # share of the leader's usage before a font counts as a distinct choice.
    top = counts.most_common(1)[0][1] if counts else 0
    ranked = [n for n, c in counts.most_common() if top == 0 or c >= max(2, top * 0.10)]
    ordered = [n for n in ranked if not is_mono(n)] + [n for n in ranked if is_mono(n)]
    return [{"name": n, "source": "google" if n in google else "css"} for n in ordered[:limit]]


# ---------------------------------------------------------------------------
# Tone
# ---------------------------------------------------------------------------

TONE_LEXICON = {
    "technical": ["api", "code", "developer", "data", "platform", "infrastructure", "open source",
                  "deploy", "engineer", "software", "algorithm", "cloud", "integration", "sdk",
                  "repository", "package", "library", "framework", "automation", "workflow"],
    "warm": ["together", "community", "care", "love", "friendly", "welcome", "people", "help",
             "share", "support", "family", "story", "join", "human", "connect", "everyone"],
    "bold": ["fastest", "best", "leading", "revolution", "unstoppable", "power", "win", "dominate",
             "ultimate", "breakthrough", "never", "change", "transform", "limitless", "unleash"],
    "trustworthy": ["secure", "trusted", "reliable", "safe", "privacy", "compliance", "guarantee",
                    "certified", "proven", "responsible", "safety", "transparent", "standard",
                    "governance", "protect", "audit"],
    "premium": ["luxury", "exclusive", "crafted", "elegant", "refined", "curated", "premium",
                "bespoke", "signature", "timeless", "excellence", "artisan", "finest"],
}


def detect_tone(text: str) -> dict:
    """Lexicon scoring: hits per 1,000 words, normalised to a 0-1 profile."""
    low = " " + re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()) + " "
    words = max(1, len(low.split()))
    raw = {}
    for dim, terms in TONE_LEXICON.items():
        hits = sum(low.count(f" {t} ") for t in terms)
        raw[dim] = hits / words * 1000
    total = sum(raw.values())
    scores = {d: (round(v / total, 3) if total else 0.0) for d, v in raw.items()}
    dominant = max(scores, key=scores.get) if total else "neutral"
    return {"scores": scores, "dominant": dominant}


# ---------------------------------------------------------------------------
# Page parsing
# ---------------------------------------------------------------------------

_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f\u202a-\u202e]")


def _clean(text: str) -> str:
    """Collapse whitespace AND drop zero-width characters, which str.strip()
    does not treat as whitespace (an 'empty' heading of only ZWJs would
    otherwise render as a blank <h2>)."""
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", text or "")).strip()


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "template", "form"]):
        tag.decompose()


def guess_brand_name(soup: BeautifulSoup, url: str = "") -> str:
    og = soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        return _clean(og["content"])[:40]
    app = soup.find("meta", attrs={"name": "application-name"})
    if app and app.get("content"):
        return _clean(app["content"])[:40]
    if soup.title and soup.title.string:
        title = _clean(soup.title.string)
        parts = re.split(r"\s+[|\u2013\u2014\-:\u00b7]\s+", title)
        parts = [p for p in parts if p]
        if parts:
            # Brand is usually the shortest segment (the other side is a slogan).
            best = min(parts, key=len) if len(parts) > 1 else parts[0]
            return best[:40]
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].split(".")[0]
    return host.capitalize() or "Brand"


def parse_page(html: str, url: str = "") -> dict:
    soup = BeautifulSoup(html, "lxml")
    title = _clean(soup.title.string) if soup.title and soup.title.string else ""
    md = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
    description = _clean(md["content"]) if md and md.get("content") else ""
    brand = guess_brand_name(soup, url)

    body_soup = BeautifulSoup(html, "lxml")
    _strip_noise(body_soup)
    headings = [_clean(h.get_text(" ")) for h in body_soup.find_all(["h1", "h2", "h3"])]
    headings = [h for h in headings if h]
    paragraphs = [_clean(p.get_text(" ")) for p in body_soup.find_all("p")]
    paragraphs = [p for p in paragraphs if len(p.split()) >= 4]
    body_text = " ".join(paragraphs) or _clean((body_soup.body or body_soup).get_text(" "))

    return {
        "url": url,
        "title": title,
        "description": description,
        "brand_name": brand,
        "headings": headings,
        "paragraphs": paragraphs,
        "body_text": body_text,
    }


# ---------------------------------------------------------------------------
# Real sections - fully general, no site-specific logic
# ---------------------------------------------------------------------------

_SKIP_SECTION_HINT = re.compile(r"cookie|consent|newsletter|subscribe|signup|sign-up|login|breadcrumb|skip", re.I)


def _section_from(node) -> dict | None:
    """Pull a heading, first real paragraph and up to 4 bullet candidates."""
    heading_tag = node.find(["h1", "h2", "h3"])
    heading = _clean(heading_tag.get_text(" ")) if heading_tag else ""

    body = ""
    for p in node.find_all("p"):
        t = _clean(p.get_text(" "))
        if len(t.split()) >= 5 and t != heading:
            body = t
            break

    bullets: list[str] = []
    for li in node.find_all("li"):
        t = _clean(li.get_text(" "))
        if 2 <= len(t.split()) <= 24 and t not in bullets and t != heading:
            bullets.append(t)
        if len(bullets) == 4:
            break
    if len(bullets) < 2:
        bullets = []
        for h3 in node.find_all("h3"):
            t = _clean(h3.get_text(" "))
            if t and t != heading and t not in bullets:
                bullets.append(t)
            if len(bullets) == 4:
                break

    # Keep a section if it carries ANY real content. A heading-less block of
    # bullets (a menu, a feature list under a decorative or invisible heading)
    # is real content; only sections with nothing worth showing are dropped.
    if not (heading or body) and len(bullets) < 2:
        return None
    if not heading and not bullets and len(body.split()) < 8:
        return None
    return {"heading": heading, "body": body, "bullets": bullets[:4]}


def _dedupe_sections(sections: list[dict]) -> list[dict]:
    """Drop repeated sections.

    Pages nest <section> inside <section> (a wrapper and its child), and both
    yield the same heading and paragraph. A section is a repeat when its
    heading matches an earlier one, OR its body matches an earlier body. When
    two are repeats the richer one (more bullets, then longer body) is kept in
    the position of the first, so document order is preserved.
    """
    def key(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()

    kept: list[dict] = []
    for sec in sections:
        hk, bk = key(sec.get("heading", "")), key(sec.get("body", ""))
        twin = None
        for i, prev in enumerate(kept):
            same_h = hk and hk == key(prev.get("heading", ""))
            same_b = bk and bk == key(prev.get("body", ""))
            if not hk and not bk:   # two bullet-only blocks: compare the bullets
                same_b = key(" ".join(sec.get("bullets", []))) == key(" ".join(prev.get("bullets", [])))
            if same_h or same_b:
                twin = i
                break
        if twin is None:
            kept.append(sec)
            continue
        prev = kept[twin]
        richer = (len(sec.get("bullets", [])), len(sec.get("body", ""))) > \
                 (len(prev.get("bullets", [])), len(prev.get("body", "")))
        if richer:
            kept[twin] = sec
    return kept


def extract_real_sections(soup_or_html, max_sections: int = 8) -> list[dict]:
    """Walk the page's own <section> tags (or <main> children) in document
    order. The first is the hero; the rest are content sections."""
    soup = BeautifulSoup(soup_or_html, "lxml") if isinstance(soup_or_html, str) else soup_or_html
    _strip_noise(soup)

    nodes = [s for s in soup.find_all("section")
             if not _SKIP_SECTION_HINT.search(" ".join(s.get("class", [])) + " " + (s.get("id") or ""))]
    if len(nodes) < 2:
        main = soup.find("main") or soup.find("article") or soup.body
        if main:
            kids = [c for c in main.find_all(recursive=False) if getattr(c, "name", None) in ("div", "section", "article")]
            nodes = kids if len(kids) >= 2 else nodes

    # Some sites nest everything in a single wrapper - drill in once.
    if len(nodes) == 1:
        inner = [c for c in nodes[0].find_all(recursive=False) if getattr(c, "name", None) in ("div", "section", "article")]
        if len(inner) >= 2:
            nodes = inner

    sections: list[dict] = []
    for node in nodes:
        sec = _section_from(node)
        if sec:
            sections.append(sec)
        if len(sections) >= max_sections:
            break

    # Last resort: build sections from the heading sequence.
    if len(sections) < 2:
        sections = _sections_from_headings(soup, max_sections)

    sections = _dedupe_sections(sections)
    for i, sec in enumerate(sections):
        sec["role"] = "hero" if i == 0 else "content"
    return sections


def _sections_from_headings(soup, max_sections: int) -> list[dict]:
    out: list[dict] = []
    current = None
    root = soup.body or soup
    for el in root.find_all(["h1", "h2", "h3", "p", "li"]):
        if el.name in ("h1", "h2"):
            if current and (current["heading"] or current["body"]):
                out.append(current)
            current = {"heading": _clean(el.get_text(" ")), "body": "", "bullets": []}
        elif current is not None:
            t = _clean(el.get_text(" "))
            if el.name == "p" and not current["body"] and len(t.split()) >= 5:
                current["body"] = t
            elif el.name in ("li", "h3") and 2 <= len(t.split()) <= 24 and len(current["bullets"]) < 4:
                current["bullets"].append(t)
    if current and (current["heading"] or current["body"]):
        out.append(current)
    return out[:max_sections]
