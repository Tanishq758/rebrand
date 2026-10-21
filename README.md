# BrandForge

Take the **brand** of one website (Site A) and the **content** of another (Site B),
and get back a full, scrollable HTML page: Site B's real sections, headings and
copy, in Site B's document order, restyled in Site A's colours, fonts and tone.
Site B's own brand name is swapped for Site A's throughout the copy.

It is general: it works on whatever two URLs you give it.

## Run it

```bash
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

## The interface

One screen, no scrolling to find the result.

- **Recipe bar.** *Brand from* (Site A) and *Content from* (Site B), a swap button to
  try them the other way round, and a keyword-density control. `?a=pypi.org&b=github.com`
  in the address pre-fills the two fields.
- **Live progress.** The eight pipeline steps are reported by the server as each one
  finishes, with a real detail ("4 of 4 stylesheets read", "6 sections found"). It is not
  a timer. A wireframe page beside the steps takes on Site A's colours the moment they
  are found, and the tool's own accent (the button, the top strip, the tab underline,
  the bars) becomes the brand colour too. The interface is neutral until then.
- **Stage.** The generated page in a frame you can switch between desktop, tablet and
  phone widths, with **Open in new tab**, **Download HTML** and **Copy HTML**.
- **Inspector.** *Brand*: palette shown as proportional shares, fonts, tone.
  *Content*: topic overlap and the sections in page order. *Keywords*: each keyword's
  Strong/Medium/Weak verdict and the model's confidence, plus what happened to the top
  keyword in plain words.
- **Recent pairs** (URLs only) are remembered in your browser; **Clear** removes them.
- Keyboard and screen-reader friendly: labelled fields, real tabs with arrow-key
  navigation, visible focus, progress announced politely, reduced-motion respected.

The interface's fonts (Bricolage Grotesque and Hanken Grotesk, SIL Open Font License,
licences in `static/fonts/`) are bundled, so it works offline and makes no request to
Google.

## HTTP API

| Route | Purpose |
|-------|---------|
| `POST /api/rebrand` | `{url_a, url_b, density}` -> one JSON result including `rendered_html` |
| `POST /api/rebrand/stream` | Same request, answered as newline-delimited JSON: eight `{"type":"step","done":i,"detail":...}` events (the colour step also carries the brand and its theme), then one `{"type":"result",...}` or `{"type":"error","message":...}` |
| `GET /api/render/<id>` | A generated page by itself (used by *Open in new tab*) |
| `GET /api/health` | Liveness check |

## The standalone renderer (no scraping)

Render the same kind of page straight from two JSON files, with no network and no ML:

```bash
python render_from_json.py brand.json content.json output.html
```

Options: `--brand-name NAME`, `--tone technical|warm|bold|trustworthy|premium`,
`--primary #hex`, `--quiet`.

It accepts several input shapes, including the Branded Content Generator's own files:

| File | Accepted shapes |
|------|-----------------|
| `brand.json` | `{"brand_name", "colors": {"primary"}, "typography": {...}}`, or the raw extractor output (`primary_color_guess`, `background_colors`, `font_families`, ...) |
| `content.json` | `{"sections": [{"heading", "body", "bullets"}]}`, or `{"content_blocks": [...]}`, or `{"outline": {"sections": [...]}}` |

Extractor files often report white as `primary_color_guess` (the most frequent text
colour). The loader does not trust that; it picks the most-used *chromatic* colour.

## How it works

```
scraper.py     fetch() pages, fetch_css() linked stylesheets
extractors.py  parse_page, extract_real_sections, extract_colors (K-means),
               extract_fonts, detect_tone
nlp.py         TF-IDF keywords, KNN SEO classifier, topical similarity
rebrand.py     brand-name swap + keyword weaving
renderer.py    render_full_page(): the single source of truth for the page look
loaders.py     normalise brand.json / content.json shapes
pipeline.py    the two-URL orchestration
app.py         Flask: /api/rebrand, /api/rebrand/stream, /api/render/<id>
static/        the UI (index.html, app.js, style.css) and bundled fonts
```

- **Colours.** Every hex / rgb() value in the HTML and CSS is mined, then K-means
  reduces them to a palette. The primary is the most dominant cluster with real
  chroma, so white, black and grey can never win.
- **SEO keywords.** TF-IDF ranks candidates. A K-nearest-neighbours classifier
  (k=3, distance-weighted, standardised features) trained on a small hand-labelled
  reference set marks each Strong / Medium / Weak. Confidence is the classifier's
  real `predict_proba()` for its verdict.
- **Rendering.** The whole colour system is derived from one primary colour and
  every text/background pair is checked against WCAG AA (4.5:1). `tests/stress_contrast.py`
  verifies this over tens of thousands of random brand colours.

## Tests

```bash
python tests/test_pipeline.py     # 33 end-to-end checks over offline fixtures
python tests/test_api.py          # 25 checks: JSON route, streaming route, static files
python tests/stress_contrast.py   # WCAG AA across thousands of random brand colours
python tests/ui_smoke.py          # 70 checks driving the real UI in headless Chromium
```

The first three need no internet access: the real pipeline runs with only the network
replaced. `ui_smoke.py` also needs Playwright (`pip install playwright && playwright
install chromium`); it starts the real app on the same offline network, slows two steps
so the mid-build state can be observed, and checks things like the wireframe taking the
brand colour before the page appears, failures restoring the previous state, hostile page
text staying inert, and the layout at 390px wide.

## Known limitations

- **JavaScript-rendered sites.** Fetching uses `requests`, so content that a page
  builds in the browser after load is invisible. BrandForge detects the obvious
  cases and says so rather than returning a blank page.
- **Blocked sites.** Some sites return 403 to automated requests. The error names
  which site (A or B) was blocked.
- **Unreadable stylesheets.** If a site's CSS lives on a host that refuses
  automated access, its colours cannot be read and a neutral colour is used. The
  UI tells you when this happens.
- **Site B's product names are kept.** Only Site B's own brand name is replaced.
  A page mentioning "Copilot" or a partner's name will still say so.
- **The keyword weaver is cautious on purpose.** It only adds a keyword the
  classifier rates Strong, skips Site B's proper nouns and sentence fragments, and
  leaves the copy untouched otherwise. The fragment check is a heuristic and will
  occasionally reject a good keyword; that is the safe direction to be wrong in.
- **No images.** The generated page is text and CSS only.
- **Fonts in the generated page** are loaded from Google Fonts when the brand's font is
  available there; otherwise the page falls back to the system font stack. (The tool's own
  interface uses bundled fonts and makes no such request.)
