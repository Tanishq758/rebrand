"""Offline stand-in for the network, shared by the API and browser tests.

Only fetch() and fetch_css() are replaced; the real pipeline runs unchanged.
"""
from pathlib import Path

from brandforge import pipeline
from brandforge.scraper import FetchError

FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


PAGES = {
    "https://coffee.test": _load("01_clean_sections.html"),
    "https://law.test": _load("02_div_soup_no_section_tags.html"),
    "https://plain.test": _load("04_no_brand_colour_greyscale.html"),
    "https://bakery.test": _load("05_zero_width_and_entities.html"),
    "https://cloud.test": _load("06_nested_wrappers.html"),
    "https://press.test": _load("08_headings_only_flat.html"),
    "https://js.test": _load("03_js_only_shell.html"),
    "https://empty.test": _load("07_almost_empty.html"),
    "https://blocked.test": FetchError("blocked.test blocked automated access (HTTP 403)."),
}


def install():
    def fake_fetch(url):
        key = url.rstrip("/")
        for k, v in PAGES.items():
            if k.rstrip("/") == key:
                if isinstance(v, Exception):
                    raise v
                return url, v
        raise FetchError(f"no fixture for {url}")

    def fake_fetch_css(html, base, report=None):
        if report is not None:
            report.update({"linked": 0, "read": 0, "inline": 1})
        return html

    pipeline.fetch = fake_fetch
    pipeline.fetch_css = fake_fetch_css
