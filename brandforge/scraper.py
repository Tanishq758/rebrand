"""
scraper.py - fetching pages and stylesheets.

Everything network-related lives here so the rest of the pipeline can be
tested offline with fixtures.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 15
MAX_STYLESHEETS = 14   # big sites split CSS across dozens of files
MAX_CSS_BYTES = 600_000


class FetchError(Exception):
    """Raised with a user-presentable message when a page can't be fetched."""


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise FetchError("URL is empty.")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        raise FetchError(f"'{url}' does not look like a valid URL.")
    return url


def fetch(url: str) -> tuple[str, str]:
    """GET a page. Returns (final_url, html). Raises FetchError on failure."""
    url = normalize_url(url)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.exceptions.SSLError:
        raise FetchError(f"SSL error while connecting to {url}.")
    except requests.exceptions.ConnectionError:
        raise FetchError(f"Could not connect to {url}. Check the address and your network.")
    except requests.exceptions.Timeout:
        raise FetchError(f"{url} took too long to respond.")
    except requests.exceptions.RequestException as exc:
        raise FetchError(f"Request to {url} failed: {exc}")

    if resp.status_code in (401, 403, 429):
        raise FetchError(
            f"{urlparse(url).netloc} blocked automated access (HTTP {resp.status_code}). "
            "Try a different page on the site, or a different site."
        )
    if resp.status_code >= 400:
        raise FetchError(f"{url} returned HTTP {resp.status_code}.")

    ctype = resp.headers.get("Content-Type", "")
    if "html" not in ctype.lower() and "xml" not in ctype.lower():
        raise FetchError(f"{url} is not an HTML page (Content-Type: {ctype or 'unknown'}).")

    resp.encoding = resp.apparent_encoding if resp.encoding in (None, "ISO-8859-1") else resp.encoding
    return resp.url, resp.text


def fetch_css(html: str, base_url: str, report: dict | None = None) -> str:
    """Collect inline <style> blocks and linked stylesheets into one string,
    so color/font mining sees the real CSS rather than just the HTML.

    If `report` is given it is filled with how many sheets were linked vs
    actually read, so callers can tell the user when a site's stylesheets
    could not be reached instead of silently falling back to defaults.
    """
    soup = BeautifulSoup(html, "lxml")
    chunks: list[str] = [tag.get_text() for tag in soup.find_all("style")]

    # Select by inspecting each tag's own rel, rather than passing a callable to
    # find_all(rel=...): for multi-valued attributes bs4 calls that callable once
    # per token with a plain string, so " ".join(r) spelled it "s t y l e s h e e t"
    # and silently matched nothing - every site looked like it had no stylesheet.
    links = []
    for l in soup.find_all("link", href=True):
        rel = l.get("rel") or []
        rel = rel.split() if isinstance(rel, str) else list(rel)
        if any(t.lower() == "stylesheet" for t in rel):
            links.append(l["href"])

    # Prefer a site's own stylesheets: they carry the brand. Third-party hosts
    # (font CDNs) are skipped because the font list comes from the <link> href.
    candidates = [urljoin(base_url, h) for h in links if "fonts.googleapis.com" not in h]
    fetched = 0
    for css_url in candidates[:MAX_STYLESHEETS]:
        try:
            r = requests.get(css_url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and len(r.text) <= MAX_CSS_BYTES:
                chunks.append(r.text)
                fetched += 1
        except requests.exceptions.RequestException:
            continue  # one missing stylesheet must never fail the whole run

    if report is not None:
        report.update({"linked": len(candidates), "read": fetched, "inline": len(chunks) - fetched})
    return "\n".join(chunks)
