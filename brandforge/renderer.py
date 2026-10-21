"""
renderer.py - generate a complete standalone HTML page from a brand + content.

Single source of truth for the page's look. Used by both:
  - the Flask pipeline   (app.py -> render_full_page)
  - the standalone CLI   (render_from_json.py)

Inputs are plain dicts:

brand = {
    "brand_name": "PyPI",
    "colors": {"primary": "#0068a4", "accent": "#..." (optional)},
    "fonts": ["Source Sans Pro", ...]   (names only, optional),
    "tone": "technical"                 (optional)
}
content = {
    "sections": [{"role": "hero"|"content", "heading", "body", "bullets": [...]}]
}

Everything visual is derived from CSS custom properties computed from the
primary colour, so the whole page re-themes from a single hex value.
"""
from __future__ import annotations

import colorsys
import html
import re

# ---------------------------------------------------------------------------
# Colour maths
# ---------------------------------------------------------------------------

def _hex_to_rgb(value: str):
    v = (value or "").strip().lstrip("#")
    if len(v) in (4, 8):
        v = v[:-1] if len(v) == 4 else v[:6]
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        return None
    try:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _rgb_to_hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(round(c)))) for c in rgb))


def _lum(rgb) -> float:
    def ch(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _q(rgb):
    """Round to the 8-bit values that actually end up in the emitted hex."""
    return tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _contrast(a, b) -> float:
    # Always measure the quantised colours: a float pair can pass at 4.5 while
    # the rounded hex the browser paints lands a hair under.
    la, lb = _lum(_q(a)), _lum(_q(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _mix(rgb, target, t: float):
    """Mix rgb toward target by t (0 = rgb, 1 = target)."""
    return tuple(rgb[i] + (target[i] - rgb[i]) * t for i in range(3))


def _on_color(rgb) -> str:
    """Pick white or near-black text for a background, by real contrast."""
    return "#ffffff" if _contrast(rgb, (255, 255, 255)) >= _contrast(rgb, (17, 17, 17)) else "#111111"


def _ensure_contrast(rgb, bgs, minimum: float = 4.5):
    """Move rgb toward black/white until it meets `minimum` on EVERY bg in bgs.

    `bgs` may be one colour or a list (the page alternates two paper tones,
    so text has to hold up on both). Small steps and a final check mean we
    never stop just short of the threshold.
    """
    if bgs and isinstance(bgs[0], (int, float)):
        bgs = [bgs]
    dark_bg = min(_lum(b) for b in bgs) < 0.4
    target = (255, 255, 255) if dark_bg else (0, 0, 0)
    cur = rgb
    for _ in range(80):
        if all(_contrast(cur, b) >= minimum for b in bgs):
            return cur
        cur = _mix(cur, target, 0.04)
    return target


def _on_color_strict(rgb, minimum: float = 4.5) -> str:
    """White or near-black text on rgb. If neither clears `minimum` (mid-tones),
    the colour itself is nudged by the caller - see _fit_on_color."""
    return "#ffffff" if _contrast(rgb, (255, 255, 255)) >= _contrast(rgb, (17, 17, 17)) else "#111111"


def _fit_fill(rgb, minimum: float = 4.5):
    """Return (fill_rgb, text_hex) with guaranteed `minimum` contrast.

    In the mid-tone gap where neither white nor near-black text works, the
    fill is shifted (slightly darker) until white text passes - it stays the
    same hue, so it still reads as the brand colour.
    """
    text = _on_color_strict(rgb)
    text_rgb = (255, 255, 255) if text == "#ffffff" else (17, 17, 17)
    if _contrast(rgb, text_rgb) >= minimum:
        return rgb, text
    cur = rgb
    for _ in range(60):
        cur = _mix(cur, (0, 0, 0), 0.03)
        if _contrast(cur, (255, 255, 255)) >= minimum:
            return cur, "#ffffff"
    return cur, "#ffffff"


def build_theme(primary_hex: str, accent_hex: str | None = None) -> dict:
    """Derive the whole colour system from one primary colour."""
    rgb = _hex_to_rgb(primary_hex) or (51, 65, 85)
    accent = _hex_to_rgb(accent_hex) if accent_hex else None
    fill, on_fill = _fit_fill(rgb)   # brand colour as a painted surface, text guaranteed legible
    acc_fill, on_acc_fill = _fit_fill(accent) if accent else (fill, on_fill)

    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    # a tinted paper: the primary hue at very low saturation, very light
    paper = tuple(c * 255 for c in colorsys.hls_to_rgb(h, 0.975, min(s, 0.55)))
    paper_2 = tuple(c * 255 for c in colorsys.hls_to_rgb(h, 0.94, min(s, 0.6)))
    ink = tuple(c * 255 for c in colorsys.hls_to_rgb(h, 0.11, min(s, 0.55)))
    muted = _ensure_contrast(_mix(ink, paper, 0.40), [paper, paper_2], 4.5)
    border = tuple(c * 255 for c in colorsys.hls_to_rgb(h, 0.86, min(s, 0.5)))
    link = _ensure_contrast(rgb, [paper, paper_2], 4.5)
    # The hero's dark anchor. A dark/mid primary can simply be deepened, but
    # mixing a bright primary (gold, mint) toward black only makes it muddy -
    # so bright brands get a genuinely dark, hue-tinted ground instead and the
    # brand colour itself is used as the accent on top of it.
    if _lum(rgb) > 0.30:
        deep = tuple(c * 255 for c in colorsys.hls_to_rgb(h, 0.09, min(max(s, 0.25), 0.6)))
        hero_accent = rgb
    else:
        # Darken more for saturated, mid-lightness primaries: a 42% mix left a
        # saturated green at #02633f, so close in lightness to the brand colour
        # that the accent had to be pushed to neon just to be legible on it.
        deep_mix = 0.42 + 0.30 * max(0.0, min(1.0, (s - 0.5) / 0.5)) * max(0.0, min(1.0, (l - 0.2) / 0.2))
        deep = _mix(rgb, (0, 0, 0), min(deep_mix, 0.72))
        # Lighten in HLS (not by mixing with white) so the accent keeps its
        # saturation and still reads as the brand colour, not a grey tint.
        # Saturation is taken from the source as-is, so a neutral stays neutral.
        acc_s = s   # keep the brand's own saturation; boosting it pushes greens/cyans to neon
        hero_accent = None
        # Joint search: the accent must clear AA against the dark ground AND
        # carry a legible label (white or near-black). For saturated blues a
        # mid lightness can't do both, so we keep lightening: once the fill is
        # light enough, near-black label text passes and so does the ground.
        # Start from the brand's OWN lightness (not a fixed 0.50): a mid green at
        # l=0.34 jumped straight to a neon l=0.50. Moving only as far as the
        # contrast requirement forces keeps the colour recognisably the brand's.
        start_l = max(l, 0.30)
        for step in range(0, 70):
            acc_l = min(0.97, start_l + step * 0.01)
            cand = tuple(c * 255 for c in colorsys.hls_to_rgb(h, acc_l, acc_s))
            if _contrast(cand, deep) >= 4.5:
                label = _on_color_strict(cand)
                label_rgb = (255, 255, 255) if label == "#ffffff" else (17, 17, 17)
                if _contrast(cand, label_rgb) >= 4.5:
                    hero_accent = cand
                    break
        if hero_accent is None:  # extremely unlikely; fall back to a light neutral
            hero_accent = (235, 235, 235)

    hero_fill, on_hero_fill = _fit_fill(hero_accent)   # fill + label fitted as a pair

    return {
        "primary": _rgb_to_hex(fill),
        "on_primary": on_fill,
        "deep": _rgb_to_hex(deep),
        "on_deep": _on_color(deep),
        "hero_accent": _rgb_to_hex(hero_fill),
        "on_hero_accent": on_hero_fill,
        "paper": _rgb_to_hex(paper),
        "paper_2": _rgb_to_hex(paper_2),
        "ink": _rgb_to_hex(ink),
        "muted": _rgb_to_hex(muted),
        "border": _rgb_to_hex(border),
        "link": _rgb_to_hex(link),
        "accent": _rgb_to_hex(acc_fill),
        "on_accent": on_acc_fill,
        "primary_soft": _rgb_to_hex(_mix(rgb, (255, 255, 255), 0.88)),
    }


# ---------------------------------------------------------------------------
# Style profile: tone -> layout personality (not just colour)
# ---------------------------------------------------------------------------

STYLE_PROFILES = {
    # radius, section padding, heading weight, heading tracking, hero alignment
    "technical":   {"radius": "6px",  "pad": "4.5rem", "h_weight": 700, "track": "-0.02em", "hero_align": "left",   "measure": "62ch"},
    "warm":        {"radius": "18px", "pad": "5rem",   "h_weight": 700, "track": "-0.01em", "hero_align": "left",   "measure": "60ch"},
    "bold":        {"radius": "4px",  "pad": "4rem",   "h_weight": 800, "track": "-0.035em","hero_align": "left",   "measure": "58ch"},
    "trustworthy": {"radius": "8px",  "pad": "4.75rem","h_weight": 650, "track": "-0.015em","hero_align": "left",   "measure": "64ch"},
    "premium":     {"radius": "2px",  "pad": "6rem",   "h_weight": 500, "track": "-0.01em", "hero_align": "center", "measure": "56ch"},
    "neutral":     {"radius": "10px", "pad": "4.75rem","h_weight": 700, "track": "-0.02em", "hero_align": "left",   "measure": "62ch"},
}


def _profile(tone: str | None) -> dict:
    return STYLE_PROFILES.get((tone or "neutral").lower(), STYLE_PROFILES["neutral"])


# ---------------------------------------------------------------------------
# Font stacks
# ---------------------------------------------------------------------------

_SERIF_HINT = re.compile(r"serif|georgia|garamond|times|playfair|merriweather|lora|baskerville|cormorant|fraunces|source serif", re.I)
_MONO_HINT = re.compile(r"mono|code|consolas|courier|menlo|fira ?code|jetbrains|cascadia|hack\b|terminal", re.I)
_SANS_FALLBACK = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
_SERIF_FALLBACK = 'Georgia, "Times New Roman", Times, serif'


def _font_names(fonts) -> list[str]:
    names = []
    for f in fonts or []:
        n = f.get("name") if isinstance(f, dict) else str(f)
        n = (n or "").strip().strip("\"'")
        if n and n.lower() not in {x.lower() for x in names} and not n.startswith("__"):
            names.append(n)
    return names


def build_font_stacks(fonts) -> dict:
    names = _font_names(fonts)
    if not names:
        return {"heading": _SANS_FALLBACK, "body": _SANS_FALLBACK, "google_href": ""}

    def stack(n):
        fb = _SERIF_FALLBACK if _SERIF_HINT.search(n) else _SANS_FALLBACK
        return f'"{n}", {fb}'

    # Monospace faces belong to code samples, not to running text. Whatever
    # order the extractor returned, headings and body are set in proportional
    # fonts only; a page whose only fonts are monospace falls back to system.
    proportional = [n for n in names if not _MONO_HINT.search(n)]
    if not proportional:
        return {"heading": _SANS_FALLBACK, "body": _SANS_FALLBACK, "google_href": ""}
    heading_name = proportional[0]
    body_name = proportional[1] if len(proportional) > 1 else proportional[0]
    href = ""
    families = []
    for n in dict.fromkeys([heading_name, body_name]):
        # Only request fonts Google actually hosts; unknown names fall back cleanly.
        if re.fullmatch(r"[A-Za-z0-9 ]{2,40}", n):
            families.append("family=" + n.replace(" ", "+") + ":wght@400;500;600;700;800")
    if families:
        href = "https://fonts.googleapis.com/css2?" + "&".join(families) + "&display=swap"
    return {"heading": stack(heading_name), "body": stack(body_name), "google_href": href}


# ---------------------------------------------------------------------------
# HTML building
# ---------------------------------------------------------------------------

def esc(text) -> str:
    return html.escape(str(text)) if text not in (None, "") else ""


def _split_hero_cta(sec: dict) -> str:
    return "Get started"


def _hero_html(sec: dict, brand_name: str, has_next: bool) -> str:
    bullets = sec.get("bullets") or []
    chips = "".join(f'<li>{esc(b)}</li>' for b in bullets[:4])
    chips_html = f'<ul class="hero-points">{chips}</ul>' if chips else ""
    lead = f'<p class="lead">{esc(sec.get("body"))}</p>' if sec.get("body") else ""
    href = "#s1" if has_next else "#top"
    return f"""
  <header class="hero" id="top">
    <div class="wrap hero-inner">
      <p class="brand-mark">{esc(brand_name)}</p>
      <h1>{esc(sec.get("heading") or brand_name)}</h1>
      {lead}
      <a class="btn" href="{href}">{esc(_split_hero_cta(sec))}</a>
      {chips_html}
    </div>
  </header>"""


def _content_html(sec: dict, idx: int) -> str:
    bullets = sec.get("bullets") or []
    body = f'<p>{esc(sec.get("body"))}</p>' if sec.get("body") else ""
    lis = "".join(f"<li>{esc(b)}</li>" for b in bullets)
    ul = f'<ul class="checks">{lis}</ul>' if lis else ""
    alt = " alt" if idx % 2 == 0 else ""
    heading = f'<h2>{esc(sec.get("heading"))}</h2>' if sec.get("heading") else ""
    return f"""
  <section class="block{alt}" id="s{idx}">
    <div class="wrap">
      {heading}
      {body}
      {ul}
    </div>
  </section>"""


def _css(theme: dict, fonts: dict, prof: dict) -> str:
    return f"""
:root {{
  --primary: {theme['primary']};
  --on-primary: {theme['on_primary']};
  --deep: {theme['deep']};
  --on-deep: {theme['on_deep']};
  --hero-accent: {theme['hero_accent']};
  --on-hero-accent: {theme['on_hero_accent']};
  --paper: {theme['paper']};
  --paper-2: {theme['paper_2']};
  --ink: {theme['ink']};
  --muted: {theme['muted']};
  --border: {theme['border']};
  --link: {theme['link']};
  --soft: {theme['primary_soft']};
  --accent: {theme['accent']};
  --on-accent: {theme['on_accent']};
  --radius: {prof['radius']};
  --pad: {prof['pad']};
  --measure: {prof['measure']};
  --font-heading: {fonts['heading']};
  --font-body: {fonts['body']};
}}
*, *::before, *::after {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
  margin: 0; background: var(--paper); color: var(--ink);
  font-family: var(--font-body); font-size: 1.0625rem; line-height: 1.65;
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}}
.wrap {{ width: min(100% - 2.5rem, 68rem); margin-inline: auto; }}

h1, h2 {{ font-family: var(--font-heading); font-weight: {prof['h_weight']}; letter-spacing: {prof['track']}; line-height: 1.12; margin: 0; text-wrap: balance; }}
h1 {{ font-size: clamp(2.3rem, 5.4vw + 0.6rem, 4.4rem); }}
h2 {{ font-size: clamp(1.7rem, 2.6vw + 0.8rem, 2.7rem); margin-bottom: 1.1rem; }}
p {{ margin: 0 0 1.1rem; max-width: var(--measure); text-wrap: pretty; }}

/* Hero: the deep brand colour anchors the page */
.hero {{ background: var(--deep); color: var(--on-deep); padding: calc(var(--pad) + 1rem) 0 var(--pad); }}
.hero-inner {{ text-align: {prof['hero_align']}; }}
.hero .brand-mark {{
  display: inline-block; margin: 0 0 1.6rem; padding: .35rem .8rem; border-radius: 999px;
  background: color-mix(in srgb, var(--hero-accent) 22%, transparent); color: var(--hero-accent);
  border: 1px solid color-mix(in srgb, var(--hero-accent) 45%, transparent); font-weight: 600; font-size: .9rem; letter-spacing: .01em;
}}
.hero h1 {{ max-width: 18ch; {'margin-inline: auto;' if prof['hero_align'] == 'center' else ''} }}
.hero .lead {{ font-size: clamp(1.1rem, .5vw + 1rem, 1.35rem); margin: 1.4rem 0 2rem; opacity: .92; max-width: 52ch; {'margin-inline: auto;' if prof['hero_align'] == 'center' else ''} }}
.btn {{
  display: inline-block; padding: .85rem 1.7rem; border-radius: var(--radius); font-weight: 700; text-decoration: none;
  background: var(--primary); color: var(--on-primary); border: 2px solid var(--primary);
  transition: transform .15s ease, box-shadow .15s ease;
}}
.hero .btn {{ background: var(--hero-accent); color: var(--on-hero-accent); border-color: var(--hero-accent); }}
.btn:hover {{ transform: translateY(-2px); box-shadow: 0 8px 22px rgba(0,0,0,.22); }}
.btn:focus-visible, a:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 3px; }}
.hero-points {{
  list-style: none; margin: 2.6rem 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: .6rem;
  {'justify-content: center;' if prof['hero_align'] == 'center' else ''}
}}
.hero-points li {{
  padding: .4rem .9rem; border-radius: 999px; font-size: .92rem;
  border: 1px solid color-mix(in srgb, var(--on-deep) 28%, transparent);
}}

/* Content sections alternate paper tones so the scroll has rhythm */
.block {{ padding: var(--pad) 0; }}
.block.alt {{ background: var(--paper-2); }}
.block h2 {{ color: var(--ink); position: relative; padding-top: 1.1rem; }}
.block h2::before {{
  content: ""; position: absolute; top: 0; left: 0; width: 3.2rem; height: 4px; border-radius: 4px; background: var(--primary);
}}
.checks {{ list-style: none; margin: 1.8rem 0 0; padding: 0; display: grid; gap: .8rem; max-width: 46rem; }}
.checks li {{
  position: relative; padding: .9rem 1rem .9rem 3rem; background: #fff; border: 1px solid var(--border);
  border-radius: var(--radius);
}}
.checks li::before {{
  content: ""; position: absolute; left: 1rem; top: 1.12rem; width: 1.15rem; height: 1.15rem; border-radius: 50%;
  background: var(--primary);
}}
.checks li::after {{
  content: ""; position: absolute; left: 1.42rem; top: 1.34rem; width: .34rem; height: .62rem;
  border: solid var(--on-primary); border-width: 0 2px 2px 0; transform: rotate(45deg) translate(-1px, -1px);
}}
.foot {{ padding: 2.2rem 0; border-top: 1px solid var(--border); color: var(--muted); font-size: .92rem; }}
.foot .wrap {{ display: flex; justify-content: space-between; flex-wrap: wrap; gap: .6rem; }}

@media (max-width: 640px) {{
  :root {{ --pad: 3.2rem; }}
  .hero h1 {{ max-width: none; }}
}}
@media (prefers-reduced-motion: reduce) {{
  html {{ scroll-behavior: auto; }}
  .btn {{ transition: none; }}
  .btn:hover {{ transform: none; }}
}}
"""


def render_full_page(brand: dict, content: dict, note: str = "") -> str:
    """Render a complete standalone HTML document.

    brand   -> brand_name, colors{primary, accent?}, fonts[], tone?
    content -> sections[{role, heading, body, bullets}]
    note    -> optional footer attribution line (already plain text)
    """
    brand_name = (brand.get("brand_name") or "Brand").strip()
    colors = brand.get("colors") or {}
    theme = build_theme(colors.get("primary") or "#334155", colors.get("accent"))
    fonts = build_font_stacks(brand.get("fonts"))
    prof = _profile(brand.get("tone"))

    # Keep any section that has something to show. A heading-less block of
    # bullets is real content (a menu, a feature list) and must not be dropped.
    sections = [s for s in (content.get("sections") or [])
                if s.get("heading") or s.get("body") or s.get("bullets")]
    if not sections:
        sections = [{"role": "hero", "heading": brand_name, "body": "", "bullets": []}]

    # The hero needs a headline. Use the first section that has one; anything
    # before it (bullets with no heading) stays in the page, in its original order.
    hero_idx = next((i for i, s in enumerate(sections) if s.get("heading")), None)
    if hero_idx is None:
        hero = {"role": "hero", "heading": brand_name, "body": "", "bullets": []}
        rest = sections
    else:
        hero = sections[hero_idx]
        rest = sections[:hero_idx] + sections[hero_idx + 1:]
    parts = [_hero_html(hero, brand_name, has_next=bool(rest))]
    for i, sec in enumerate(rest, start=1):
        parts.append(_content_html(sec, i))

    font_link = f'<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" href="{esc(fonts["google_href"])}">' if fonts["google_href"] else ""
    foot_note = esc(note) if note else f"{esc(brand_name)}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(hero.get("heading") or brand_name)} | {esc(brand_name)}</title>
{font_link}
<style>{_css(theme, fonts, prof)}</style>
</head>
<body>
{"".join(parts)}
  <footer class="foot"><div class="wrap"><span>{esc(brand_name)}</span><span>{foot_note}</span></div></footer>
</body>
</html>
"""
