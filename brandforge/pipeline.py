"""
pipeline.py - the two-URL brand transplant, end to end.

    run_pipeline(url_a, url_b, density) -> dict

Site A supplies brand identity (colours, fonts, tone).
Site B supplies content (real sections, real copy, real order).
The result carries everything the UI shows plus `rendered_html`, the full page.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .extractors import (
    detect_tone,
    extract_colors,
    extract_fonts,
    extract_real_sections,
    parse_page,
)
from .nlp import (
    classify_keywords_seo_strength,
    extract_candidate_keywords,
    topical_similarity,
)
from .rebrand import rebrand_content
from .renderer import build_theme, render_full_page
from .scraper import FetchError, fetch, fetch_css, normalize_url

# Step labels double as the frontend's pipeline visualiser copy.
STEPS = [
    "Fetching both sites",
    "Reading Site A's stylesheets",
    "Mining brand colours and fonts",
    "Detecting brand tone",
    "Extracting Site B's real sections",
    "Ranking SEO keywords",
    "Rebranding the content",
    "Rendering the page",
]


class PipelineError(Exception):
    """A failure whose message is safe to show the user as-is."""


def _script_signals(html: str) -> dict:
    """Evidence about WHY a page has little text. Blaming JavaScript is only
    fair when the page actually looks like a JavaScript app."""
    low = html.lower()
    return {
        "scripts": low.count("<script"),
        # the mount points and notices JS frameworks leave behind
        "app_root": bool(re.search(r'id=["\'](?:root|app|__next|__nuxt|svelte|q-app)["\']', low)),
        "noscript_notice": bool(re.search(r"<noscript[^>]*>[^<]*(enable|require)[^<]*javascript", low)),
    }


def _thin_content_warning(sections: list[dict], body_text: str, html: str) -> str | None:
    """Explain a page with almost no readable text - honestly.

    A near-empty page is one of two very different things: a JavaScript app
    whose content a plain fetch cannot see, or a page that really is empty
    (a placeholder, a "coming soon"). The advice for each is different.
    """
    words = len(body_text.split())
    sig = _script_signals(html)
    looks_like_js_app = sig["noscript_notice"] or (sig["app_root"] and sig["scripts"] >= 2) or sig["scripts"] >= 8

    # Trouble means the page yielded too little to build a page from: fewer
    # than two sections. A short but complete page (several real sections) is
    # fine - a small site is not a broken one. The one extra case is a script-
    # heavy page with hardly any text, where a few stray sections are likely
    # just the parts of the shell that rendered without JavaScript.
    too_few_sections = len(sections) < 2
    script_shell = looks_like_js_app and words < 60
    if not (too_few_sections or script_shell):
        return None

    if looks_like_js_app:
        return ("Site B looks like a JavaScript app: it builds its content in the browser, "
                "which this tool cannot see. Try a page that serves its content as plain HTML.")
    return ("Site B has very little text on this page, so there is not much to transplant. "
            "Try a page with more written content, such as its homepage or an article.")


def _brand_warning(colors: dict, css_report: dict, site: str) -> str | None:
    """Tell the user when Site A's brand could not really be read, rather than
    silently presenting a neutral fallback as if it were the brand."""
    if colors.get("fallback"):
        if css_report.get("linked", 0) and css_report.get("read", 0) == 0:
            return (f"{site} links {css_report['linked']} stylesheets but none could be read "
                    "(the host may block automated access), so its real brand colours were not found. "
                    "The page uses a neutral colour instead.")
        return (f"No distinct brand colour was found on {site}, so the page uses a neutral colour. "
                "Try a page with more visible branding, such as its homepage.")
    return None


def _kb(text: str) -> str:
    return f"{max(1, round(len(text) / 1024))} KB"


def run_pipeline(url_a: str, url_b: str, density: str = "medium", on_step=None) -> dict:
    """Run the transplant.

    on_step(index, detail, data) is called as each of the eight STEPS finishes,
    with a short factual `detail` ("6 sections found") and, for the colour step,
    `data` carrying the brand + AA-checked theme so a UI can start wearing the
    brand before the page is finished. It is optional; without it nothing changes.
    """
    if density not in ("low", "medium", "high"):
        density = "medium"

    def step(i: int, detail: str = "", data: dict | None = None) -> None:
        if on_step:
            on_step(i, detail, data)

    try:
        url_a, url_b = normalize_url(url_a), normalize_url(url_b)
    except FetchError as exc:
        raise PipelineError(str(exc))
    if url_a.rstrip("/") == url_b.rstrip("/"):
        raise PipelineError("Site A and Site B are the same page. Use two different URLs.")

    # 1 - fetch (each failure names which site it was)
    try:
        final_a, html_a = fetch(url_a)
    except FetchError as exc:
        raise PipelineError(f"Site A: {exc}")
    try:
        final_b, html_b = fetch(url_b)
    except FetchError as exc:
        raise PipelineError(f"Site B: {exc}")

    step(0, f"{_kb(html_a)} from Site A, {_kb(html_b)} from Site B")

    # 2 - real CSS so colour and font mining sees more than inline styles
    css_report: dict = {}
    css_a = fetch_css(html_a, final_a, css_report)
    if css_report.get("linked"):
        step(1, f"{css_report['read']} of {css_report['linked']} stylesheets read")
    else:
        step(1, "No linked stylesheets; used the styles inside the page")

    # 3 - brand identity from Site A
    page_a = parse_page(html_a, final_a)
    colors = extract_colors(html_a, css_a)
    fonts = extract_fonts(html_a, css_a)

    theme = build_theme(colors["primary"], colors["accent"])
    step(2, f"{colors['raw_count']} colour values, primary {colors['primary']}", {
        "brand": {
            "name": page_a["brand_name"],
            "primary": colors["primary"],
            "accent": colors["accent"],
            "fonts": [f["name"] for f in fonts],
            "fallback": colors["fallback"],
            "theme": theme,
        }})

    # 4 - tone
    tone = detect_tone(page_a["body_text"] + " " + " ".join(page_a["headings"]))
    step(3, tone["dominant"] if tone["dominant"] != "neutral" else "No strong tone signal")

    # 5 - content from Site B
    page_b = parse_page(html_b, final_b)
    real_sections = extract_real_sections(BeautifulSoup(html_b, "lxml"))
    if not real_sections:
        raise PipelineError(
            "Site B has no readable sections to transplant. It may load its content with "
            "JavaScript, or be a page with almost no text.")
    warnings = [w for w in (
        _brand_warning(colors, css_report, "Site A"),
        _thin_content_warning(real_sections, page_b["body_text"], html_b),
    ) if w]

    step(4, f"{len(real_sections)} sections found")

    # 6 - SEO keywords from Site B
    candidates = extract_candidate_keywords(page_b["body_text"], page_b["headings"])
    seo = classify_keywords_seo_strength(candidates)
    similarity = topical_similarity(page_a["body_text"], page_b["body_text"])
    n_strong = sum(1 for k in seo if k["strength"] == "Strong")
    step(5, f"{len(seo)} keywords ranked, {n_strong} strong")

    # 7 - rebrand
    generated = rebrand_content(real_sections, page_a["brand_name"], page_b["brand_name"],
                                seo, density, source_text=page_b["body_text"])

    step(6, f"{page_b['brand_name']} replaced by {page_a['brand_name']}")

    # 8 - render
    brand = {
        "brand_name": page_a["brand_name"],
        "colors": {"primary": colors["primary"], "accent": colors["accent"]},
        "fonts": fonts,
        "tone": tone["dominant"],
    }
    rendered_html = render_full_page(
        brand, generated,
        note=f"Content from {page_b['brand_name']}, restyled in {page_a['brand_name']}'s brand")

    step(7, f"{_kb(rendered_html)} page")

    return {
        "theme": theme,
        "site_a": {
            "url": final_a, "brand_name": page_a["brand_name"], "title": page_a["title"],
            "colors": colors, "fonts": fonts, "tone": tone,
        },
        "site_b": {
            "url": final_b, "brand_name": page_b["brand_name"], "title": page_b["title"],
            "section_count": len(real_sections),
        },
        "seo": {"keywords": seo, "primary_keyword": generated["primary_keyword"]},
        "similarity": similarity,
        "generated": generated,
        "warnings": warnings,
        "warning": warnings[0] if warnings else None,
        "css_report": css_report,
        "steps": STEPS,
        "rendered_html": rendered_html,
    }
