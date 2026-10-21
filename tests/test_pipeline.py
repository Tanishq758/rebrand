"""
End-to-end tests over the offline fixtures.

The real pipeline runs unchanged; only the two network calls (fetch,
fetch_css) are replaced, so these tests exercise scraping-side logic,
extraction, ML, rebranding and rendering without needing internet access.

Run:  python tests/test_pipeline.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brandforge import pipeline  # noqa: E402
from brandforge.pipeline import PipelineError, run_pipeline  # noqa: E402
from brandforge.scraper import FetchError  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def load(name):
    return (FIX / name).read_text(encoding="utf-8")


def install_fake_network(pages: dict):
    """pages maps url -> html (or an Exception instance to raise)."""
    def fake_fetch(url):
        key = url.rstrip("/")
        for k, v in pages.items():
            if k.rstrip("/") == key or "https://" + k.rstrip("/") == key:
                if isinstance(v, Exception):
                    raise v
                return url, v
        raise FetchError(f"no fixture for {url}")

    def fake_fetch_css(html, base, report=None):
        # fixtures carry their CSS inline, so pretend nothing extra is linked
        if report is not None:
            report.update({"linked": 0, "read": 0, "inline": 1})
        return html

    pipeline.fetch = fake_fetch
    pipeline.fetch_css = fake_fetch_css


PAGES = {
    "https://a-coffee.test": load("01_clean_sections.html"),
    "https://a-law.test": load("02_div_soup_no_section_tags.html"),
    "https://a-plain.test": load("04_no_brand_colour_greyscale.html"),
    "https://a-bakery.test": load("05_zero_width_and_entities.html"),
    "https://a-cloud.test": load("06_nested_wrappers.html"),
    "https://a-press.test": load("08_headings_only_flat.html"),
    "https://b-js.test": load("03_js_only_shell.html"),
    "https://b-empty.test": load("07_almost_empty.html"),
    "https://b-blocked.test": FetchError("b-blocked.test blocked automated access (HTTP 403)."),
}
install_fake_network(PAGES)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))


def rendered_text(html):
    return re.sub(r"<[^>]+>", " ", html)


def body_copy(html):
    """The page's copy, without the footer. The footer deliberately credits
    Site B ("Content from X, restyled in Y's brand"), so a brand-swap check
    must not look there."""
    return rendered_text(re.sub(r"<footer.*?</footer>", "", html, flags=re.S))


# --- 1. a healthy pair: Site A's brand carries onto Site B's content ---------
r = run_pipeline("https://a-coffee.test", "https://a-cloud.test")
html = r["rendered_html"]
check("healthy pair: page is a full HTML document", html.startswith("<!DOCTYPE html>") and "</html>" in html)
check("healthy pair: Site A's brand colour is in the CSS", "#7a3e1d" in html.lower() or r["site_a"]["colors"]["primary"] == "#7a3e1d")
check("healthy pair: A's brand name replaces B's throughout the copy",
      "Atlas Cloud" not in body_copy(html) and "Northwind Coffee" in body_copy(html))
check("healthy pair: footer credits Site B as the source",
      re.search(r"<footer.*Content from Atlas Cloud", html, re.S) is not None)
check("healthy pair: B's real headings survive, in order",
      [s["heading"] for s in r["generated"]["sections"]][:2] == ["Infrastructure that stays out of your way", "Deploy anywhere"])
check("healthy pair: no warnings", r["warnings"] == [], str(r["warnings"]))

# --- 2. every fixture as Site A gives a sane brand -----------------------------
expected_primary = {"a-coffee": "#7a3e1d", "a-law": "#0b3d5c", "a-bakery": "#b3202a",
                    "a-cloud": "#5b21b6", "a-press": "#166534"}
for host, want in expected_primary.items():
    r = run_pipeline(f"https://{host}.test", "https://a-cloud.test" if host != "a-cloud" else "https://a-press.test")
    check(f"brand from {host}: primary is {want}", r["site_a"]["colors"]["primary"] == want,
          r["site_a"]["colors"]["primary"])

# --- 3. no brand colour -> honest neutral + a warning --------------------------
r = run_pipeline("https://a-plain.test", "https://a-cloud.test")
check("greyscale Site A: falls back to the neutral", r["site_a"]["colors"]["fallback"] is True)
check("greyscale Site A: user is told", any("brand colour" in w for w in r["warnings"]), str(r["warnings"]))

# --- 4. Site B failures --------------------------------------------------------
for label, url, needle in [
    ("JavaScript-only Site B", "https://b-js.test", "no readable sections"),
]:
    try:
        run_pipeline("https://a-coffee.test", url)
        check(f"{label}: raises a clean error", False, "no error raised")
    except PipelineError as e:
        check(f"{label}: raises a clean PipelineError", needle in str(e).lower() or "javascript" in str(e).lower(), str(e))

r = run_pipeline("https://a-coffee.test", "https://b-empty.test")
check("near-empty Site B: still renders a page", "<h1>" in r["rendered_html"])
check("near-empty Site B: warns it is thin, not 'JavaScript'",
      r["warnings"] and "very little text" in r["warnings"][0] and "JavaScript" not in r["warnings"][0], str(r["warnings"]))

try:
    run_pipeline("https://a-coffee.test", "https://b-blocked.test")
    check("blocked Site B: raises", False, "no error raised")
except PipelineError as e:
    check("blocked Site B: error names Site B", str(e).startswith("Site B:"), str(e))

try:
    run_pipeline("https://b-blocked.test", "https://a-coffee.test")
    check("blocked Site A: raises", False, "no error raised")
except PipelineError as e:
    check("blocked Site A: error names Site A", str(e).startswith("Site A:"), str(e))

try:
    run_pipeline("https://a-coffee.test", "https://a-coffee.test/")
    check("same URL twice: raises", False, "no error raised")
except PipelineError as e:
    check("same URL twice: clean message", "same page" in str(e))

# --- 5. content fidelity edge cases ---------------------------------------------
r = run_pipeline("https://a-coffee.test", "https://a-bakery.test")
text = rendered_text(r["rendered_html"])
check("bullets-only section is not dropped", "Sourdough loaf" in r["rendered_html"] and "Seasonal fruit tart" in r["rendered_html"])
check("no empty <h2> from an invisible heading", not re.search(r"<h2>\s*</h2>", r["rendered_html"]))
check("HTML entities decoded (no '&amp;amp;')", "&amp;amp;" not in r["rendered_html"])
check("accented Site B name is swapped out of the copy", "Lumière" not in body_copy(r["rendered_html"]), "still present")
check("accented Site B name is credited in the footer", "Content from Lumière" in rendered_text(r["rendered_html"]))

# --- 6. structure --------------------------------------------------------------
r = run_pipeline("https://a-coffee.test", "https://a-press.test")
gen = r["generated"]
check("flat headings-only page yields 3 sections", len(gen["sections"]) == 3, str(len(gen["sections"])))
check("first section is the hero", gen["sections"][0]["role"] == "hero")
check("result is JSON-serialisable", bool(__import__("json").dumps(r)))

# --- 7. self-contained page ----------------------------------------------------
html = r["rendered_html"]
check("page has no <script> (nothing to execute in the preview)", "<script" not in html.lower())
check("page loads no images or remote scripts", not re.search(r'<(img|script)[^>]+src=', html))

# --- 8. fonts: monospace is for code, never for running text -----------------
from brandforge.renderer import build_font_stacks  # noqa: E402

f = build_font_stacks([{"name": "Source Sans 3"}, {"name": "Source Code Pro"}])
check("font: mono second in the list is NOT used as body", "Source Code Pro" not in f["body"] and "Source Sans 3" in f["body"], f["body"][:40])
f = build_font_stacks([{"name": "JetBrains Mono"}, {"name": "Inter"}])
check("font: mono FIRST in the list is skipped for headings", "Inter" in f["heading"] and "JetBrains" not in f["heading"], f["heading"][:40])
f = build_font_stacks([{"name": "Fira Code"}, {"name": "Courier New"}])
check("font: only-monospace falls back to system fonts", f["heading"].startswith("system-ui") and f["google_href"] == "")
f = build_font_stacks([])
check("font: no fonts falls back to system fonts", f["body"].startswith("system-ui"))

failed = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
