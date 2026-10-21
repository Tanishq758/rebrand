"""
Browser test for the UI.

Starts the real Flask app on an offline fake network (with a small delay on two
steps so the mid-build state can be observed) and drives it in headless
Chromium. It needs `playwright` and a Chromium build:

    pip install playwright && playwright install chromium
    python tests/ui_smoke.py
"""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fakenet  # noqa: E402

fakenet.install()

from brandforge import pipeline  # noqa: E402
from brandforge.renderer import build_theme  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

# One page whose text tries to be markup: scraped text is untrusted.
EVIL = "&lt;img src=x onerror=&quot;window.__pwned=1&quot;&gt;"
# The title differs from the headings on purpose: the brand-name swap would
# otherwise replace the hostile heading before it ever reached the UI.
fakenet.PAGES["https://evil.test"] = (
    f"<!doctype html><html><head><title>Evil {EVIL}</title></head><body><main>"
    f"<section><h1>{EVIL}</h1><p>Body text that is long enough to count as a real paragraph here.</p></section>"
    f"<section><h2>Second {EVIL}</h2><p>Another paragraph that is comfortably long enough to keep.</p>"
    f"<ul><li>{EVIL}</li><li>Plain bullet</li></ul></section></main></body></html>")

# slow two steps so the state between them can be observed
DELAY = 1.0
_css, _rb = pipeline.fetch_css, pipeline.rebrand_content


def slow_css(html, base, report=None):
    time.sleep(DELAY)
    return _css(html, base, report)


def slow_rebrand(*a, **k):
    time.sleep(DELAY)
    return _rb(*a, **k)


pipeline.fetch_css, pipeline.rebrand_content = slow_css, slow_rebrand

from app import app  # noqa: E402

PORT = 5111
BASE = f"http://127.0.0.1:{PORT}"
server = make_server("127.0.0.1", PORT, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()

from playwright.sync_api import sync_playwright  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))


def rgb(hexstr):
    h = hexstr.lstrip("#")
    return "rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


THEME = build_theme("#7a3e1d")          # the coffee fixture's brand
SHOTS = ROOT / "output" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)


def build(pg, a, b):
    pg.fill("#urlA", a)
    pg.fill("#urlB", b)
    pg.click("#goBtn")


def wait_page(pg, timeout=30000):
    pg.wait_for_selector("#pageFrame:not([hidden])", timeout=timeout)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
    pg = ctx.new_page()

    app_errors, js_errors = [], []
    pg.on("pageerror", lambda e: js_errors.append(str(e)))
    pg.on("response", lambda r: app_errors.append(f"{r.status} {r.url}")
          if r.url.startswith(BASE) and r.status >= 400 and "/api/rebrand" not in r.url else None)

    # ---------------------------------------------------------------- empty state
    pg.goto(BASE)
    pg.wait_for_load_state("networkidle")
    check("empty: headline and example are shown", pg.locator("#emptyCopy h3").is_visible() and pg.locator("#exampleBtn").is_visible())
    check("empty: stage tools are hidden until a page exists", pg.locator("#stageTools").is_hidden())
    check("empty: inspector says what will appear", "After a page is built" in pg.inner_text("#inspectorEmpty"))
    check("empty: button is neutral ink before any brand is known",
          pg.eval_on_selector("#goBtn", "e => getComputedStyle(e).backgroundColor") == rgb("#1b2027"))
    check("fonts: bundled UI font is applied and loaded",
          pg.evaluate("document.fonts.check('16px \"Hanken Grotesk\"') && getComputedStyle(document.body).fontFamily.startsWith('\"Hanken Grotesk\"')"))
    check("fonts: bundled display font is loaded", pg.evaluate("document.fonts.check('700 20px \"Bricolage Grotesque\"')"))
    check("empty: no recent list yet", pg.locator("#recent").is_hidden())

    # ---------------------------------------------------------------- validation
    pg.click("#goBtn")
    check("validation: empty submit is explained", "both the brand and the content" in pg.inner_text("#formError"))
    check("validation: focus goes to the first empty field", pg.evaluate("document.activeElement.id") == "urlA")
    pg.fill("#urlA", "coffee.test")
    pg.fill("#urlB", "not a url")
    pg.click("#goBtn")
    check("validation: bad address names the field", "content site" in pg.inner_text("#formError") and pg.evaluate("document.activeElement.id") == "urlB")

    # ---------------------------------------------------------------- swap
    pg.fill("#urlA", "one.test")
    pg.fill("#urlB", "two.test")
    pg.click("#swapBtn")
    check("swap: the two sites trade places", pg.input_value("#urlA") == "two.test" and pg.input_value("#urlB") == "one.test")

    # ---------------------------------------------------------------- build, observed mid-flight
    build(pg, "coffee.test", "cloud.test")
    check("build: button shows it is working", pg.inner_text("#goBtn") == "Building…" and pg.is_disabled("#goBtn"))
    check("build: the intro gives way to live steps", pg.locator("#steps").is_visible() and pg.locator("#emptyCopy").is_hidden())
    pg.wait_for_function("document.querySelectorAll('.step.done').length >= 3", timeout=20000)
    mid = pg.evaluate("""() => ({
        frameHidden: document.getElementById('pageFrame').hidden,
        sketchHero: document.getElementById('sketch').style.getPropertyValue('--sk-hero'),
        accent: document.documentElement.style.getPropertyValue('--accent'),
        done: document.querySelectorAll('.step.done').length,
        active: document.querySelector('.step.active .name')?.textContent || '',
    })""")
    check("mid-build: the page is not shown yet, but the wireframe already wears the brand",
          mid["frameHidden"] and mid["sketchHero"].lower() == THEME["deep"].lower(), str(mid))
    check("mid-build: the tool's own accent has become the brand colour", mid["accent"].lower() == THEME["primary"].lower(), str(mid))
    check("mid-build: the next step is marked active", mid["active"] != "" and mid["done"] >= 3, str(mid))
    pg.screenshot(path=str(SHOTS / "v2_ui_midbuild.png"))
    wait_page(pg)
    pg.wait_for_timeout(300)

    # ---------------------------------------------------------------- finished
    labels = pg.eval_on_selector_all(".step .name", "els => els.map(e => e.textContent)")
    details = pg.eval_on_selector_all(".step .detail", "els => els.map(e => e.textContent)")
    check("steps: labels match the server's STEPS exactly", labels == pipeline.STEPS, str(labels))
    check("steps: every step ended with a real detail", all(d.strip() for d in details), str(details))
    check("result: stage title names both sites", pg.inner_text("#stageTitle") == "Atlas Cloud's content in Northwind Coffee's brand", pg.inner_text("#stageTitle"))
    check("result: page loaded into the frame", pg.evaluate("document.getElementById('pageFrame').srcdoc.startsWith('<!DOCTYPE html>')"))
    check("result: stage tools are shown", pg.locator("#stageTools").is_visible())
    check("result: the button now wears Site A's brand",
          pg.eval_on_selector("#goBtn", "e => getComputedStyle(e).backgroundColor") == rgb(THEME["primary"]))
    check("result: the page inside the frame really rendered",
          pg.frame_locator("#pageFrame").locator("h1").inner_text() == "Infrastructure that stays out of your way")
    check("result: screen readers were told", "Page built" in pg.inner_text("#live"))
    check("result: build button is ready again", pg.inner_text("#goBtn") == "Build page" and pg.is_enabled("#goBtn"))
    pg.screenshot(path=str(SHOTS / "v2_ui_result.png"))

    # ---------------------------------------------------------------- inspector
    check("brand tab: shows swatches with the primary marked", pg.locator("#swatches li").count() >= 2 and "Primary" in pg.inner_text("#swatches"))
    check("brand tab: palette bar is described for assistive tech", pg.get_attribute("#paletteBar", "aria-label").startswith("Palette shares:"))
    check("brand tab: fonts listed", pg.locator("#fontList li").count() >= 1)
    tabs = ["brand", "content", "keywords"]
    pg.focus("#tab-brand")
    pg.keyboard.press("ArrowRight")
    check("tabs: ArrowRight moves to Content and focuses it",
          pg.get_attribute("#tab-content", "aria-selected") == "true" and pg.evaluate("document.activeElement.id") == "tab-content")
    check("tabs: only the selected panel is visible", pg.locator("#panel-content").is_visible() and pg.locator("#panel-brand").is_hidden())
    check("content tab: lists the sections in order",
          pg.eval_on_selector_all("#sectionList .h", "els => els.map(e => e.textContent)") == ["Infrastructure that stays out of your way", "Deploy anywhere"])
    check("content tab: states the brand swap", "“Atlas Cloud” is replaced by “Northwind Coffee”" in pg.inner_text("#swapNote"))
    pg.keyboard.press("End")
    check("tabs: End goes to Keywords", pg.get_attribute("#tab-keywords", "aria-selected") == "true" and pg.locator("#panel-keywords").is_visible())
    check("keywords tab: explains the outcome in words", len(pg.inner_text("#kwOutcome")) > 20)
    check("keywords tab: rows carry a strength label", pg.locator("#kwList .tag").count() >= 1)
    pg.keyboard.press("Home")
    check("tabs: Home returns to Brand", pg.get_attribute("#tab-brand", "aria-selected") == "true")

    # ---------------------------------------------------------------- device preview
    w = lambda: pg.eval_on_selector("#pageFrame", "e => Math.round(e.getBoundingClientRect().width)")
    full = w()
    pg.click("[data-device=tablet]")
    check("device: tablet is 768px wide", w() == 768, str(w()))
    check("device: pressed state follows", pg.get_attribute("[data-device=tablet]", "aria-pressed") == "true" and pg.get_attribute("[data-device=desktop]", "aria-pressed") == "false")
    pg.click("[data-device=phone]")
    check("device: phone is 390px wide", w() == 390, str(w()))
    check("device: the page reflows inside the narrow frame (no sideways scroll)",
          pg.evaluate("(() => { const d = document.getElementById('pageFrame').contentDocument; return d.documentElement.scrollWidth <= d.documentElement.clientWidth + 1; })()"))
    pg.screenshot(path=str(SHOTS / "v2_ui_phone.png"))
    pg.click("[data-device=desktop]")
    check("device: desktop fills the stage again", w() == full and full > 800, f"{w()} vs {full}")

    # ---------------------------------------------------------------- actions
    with pg.expect_download() as dl:
        pg.click("#downloadBtn")
    d = dl.value
    saved = Path(d.path()).read_text(encoding="utf-8")
    check("download: named after both sites", d.suggested_filename == "northwind-coffee-brand-atlas-cloud-content.html", d.suggested_filename)
    check("download: is the full standalone page", saved.startswith("<!DOCTYPE html>") and "Infrastructure that stays out of your way" in saved)
    check("download: button confirms", pg.inner_text("#downloadBtn") == "Downloaded")

    pg.click("#copyBtn")
    pg.wait_for_function("document.getElementById('copyBtn').textContent === 'Copied'")
    clip = pg.evaluate("navigator.clipboard.readText()")
    check("copy: clipboard holds the page HTML", clip == saved, f"{len(clip)} vs {len(saved)}")
    pg.wait_for_function("document.getElementById('copyBtn').textContent === 'Copy HTML'", timeout=4000)
    check("copy: label resets after a moment", True)

    with ctx.expect_page() as newp:
        pg.click("#openTabBtn")
    tab = newp.value
    tab.wait_for_url("**/api/render/**", timeout=8000)
    check("new tab: opens the stored page at a real URL", tab.locator("h1").inner_text() == "Infrastructure that stays out of your way")
    tab.close()

    # ---------------------------------------------------------------- recent pairs
    pg.reload()
    pg.wait_for_load_state("networkidle")
    check("recent: the pair is remembered after a reload", pg.locator("#recent").is_visible() and "coffee.test with cloud.test" in pg.inner_text("#recentList"))
    pg.click("#recentList button:has-text('coffee.test with cloud.test')")
    wait_page(pg)
    check("recent: clicking a pair rebuilds it", pg.inner_text("#stageTitle") == "Atlas Cloud's content in Northwind Coffee's brand")

    # ---------------------------------------------------------------- failure keeps what you had
    before_title, before_accent = pg.inner_text("#stageTitle"), pg.evaluate("document.documentElement.style.getPropertyValue('--accent')")
    build(pg, "coffee.test", "blocked.test")
    pg.wait_for_selector("#formError:not([hidden])", timeout=20000)
    check("failure: the error names Site B", pg.inner_text("#formError").startswith("Site B:"), pg.inner_text("#formError"))
    check("failure: the previous page is still on screen", pg.locator("#pageFrame").is_visible() and pg.inner_text("#stageTitle") == before_title)
    check("failure: the brand colour is restored, not left half-applied",
          pg.evaluate("document.documentElement.style.getPropertyValue('--accent')") == before_accent)
    check("failure: build button is usable again", pg.is_enabled("#goBtn") and pg.inner_text("#goBtn") == "Build page")
    check("failure: inspector still shows the previous run", pg.locator("#panel-brand").is_visible())

    # ---------------------------------------------------------------- failure on a fresh page returns to the start
    pg.goto(BASE)
    build(pg, "coffee.test", "js.test")
    pg.wait_for_selector("#formError:not([hidden])", timeout=20000)
    check("failure (fresh): returns to the intro, not a blank stage", pg.locator("#emptyCopy").is_visible() and pg.locator("#steps").is_hidden())
    check("failure (fresh): the accent goes back to ink",
          pg.eval_on_selector("#goBtn", "e => getComputedStyle(e).backgroundColor") == rgb("#1b2027"))
    check("failure (fresh): the message says why", "readable sections" in pg.inner_text("#formError"))

    # ---------------------------------------------------------------- untrusted text
    pg.goto(BASE)
    build(pg, "coffee.test", "evil.test")
    wait_page(pg)
    pg.wait_for_timeout(300)
    check("untrusted: markup in scraped text does not execute in the app", pg.evaluate("window.__pwned === undefined"))
    check("untrusted: a hostile brand name is shown as literal text in the stage title",
          "<img src=x" in pg.inner_text("#stageTitle") and pg.locator("#stageTitle img").count() == 0, pg.inner_text("#stageTitle"))
    pg.click("#tab-content")
    check("untrusted: hostile section headings are shown as text, and create no element",
          pg.locator("#sectionList img").count() == 0 and "<img src=x" in pg.inner_text("#sectionList"), pg.inner_text("#sectionList")[:120])
    check("untrusted: nothing anywhere in the app became an <img>", pg.locator("main img").count() == 0)
    check("untrusted: the generated page holds it escaped", pg.evaluate("document.getElementById('pageFrame').contentDocument.querySelectorAll('img').length") == 0)

    # ---------------------------------------------------------------- keyboard focus is visible
    pg.goto(BASE)
    for _ in range(12):
        pg.keyboard.press("Tab")
        if pg.evaluate("document.activeElement.id") == "goBtn":
            break
    ring = pg.eval_on_selector("#goBtn", "e => { const s = getComputedStyle(e); return [s.outlineStyle, s.outlineWidth]; }")
    check("keyboard: the build button shows a 3px focus ring", ring == ["solid", "3px"], str(ring))

    # ---------------------------------------------------------------- narrow screen
    m = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    mp = m.new_page()
    mp.goto(BASE)
    mp.wait_for_load_state("networkidle")
    check("mobile: no sideways scroll on the empty screen", mp.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"))
    build(mp, "coffee.test", "cloud.test")
    wait_page(mp)
    mp.wait_for_timeout(500)
    check("mobile: no sideways scroll with a result", mp.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"))
    check("mobile: the width switcher is hidden (the screen is the phone)", mp.locator(".devices").is_hidden())
    gw = mp.eval_on_selector("#goBtn", "e => e.getBoundingClientRect().width")
    iw = mp.eval_on_selector("#urlA", "e => e.getBoundingClientRect().width")
    check("mobile: the build button is as wide as the fields", abs(gw - iw) < 2 and gw > 300, f"button {gw} vs input {iw}")
    mp.screenshot(path=str(SHOTS / "v2_ui_mobile.png"), full_page=True)
    m.close()

    # ---------------------------------------------------------------- reduced motion
    r = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    rp = r.new_page()
    rp.goto(BASE)
    build(rp, "coffee.test", "cloud.test")
    rp.wait_for_selector(".step.active", timeout=10000)
    anim = rp.eval_on_selector(".step.active .dot", "e => getComputedStyle(e).animationName")
    check("reduced motion: the working pulse is off", anim == "none", anim)
    wait_page(rp)
    r.close()

    check("no JavaScript errors on any page", not js_errors, str(js_errors))
    check("no failed requests to the app's own files", not app_errors, str(app_errors))
    browser.close()

server.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
