/* BrandForge frontend.
 *
 * Submit -> POST /api/rebrand/stream -> the server reports each of the eight
 * pipeline steps as it finishes (newline-delimited JSON). The wireframe and
 * the tool's own accent take on Site A's colours the moment they are found;
 * when the result arrives the page loads into the stage and the inspector
 * fills in. All page text is inserted with textContent, never innerHTML.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const STEP_LABELS = [
    "Fetching both sites",
    "Reading Site A's stylesheets",
    "Mining brand colours and fonts",
    "Detecting brand tone",
    "Extracting Site B's real sections",
    "Ranking SEO keywords",
    "Rebranding the content",
    "Rendering the page",
  ];
  const TABS = ["brand", "content", "keywords"];
  const RECENT_KEY = "brandforge.recent";

  const els = {
    form: $("recipe"), urlA: $("urlA"), urlB: $("urlB"), go: $("goBtn"), swap: $("swapBtn"), error: $("formError"),
    stage: $("stage"), title: $("stageTitle"), tools: $("stageTools"), warnings: $("warnings"),
    viewport: $("viewport"), preflight: $("preflight"), emptyCopy: $("emptyCopy"), steps: $("steps"),
    sketch: $("sketch"), frame: $("pageFrame"), example: $("exampleBtn"),
    recent: $("recent"), recentList: $("recentList"),
    openTab: $("openTabBtn"), download: $("downloadBtn"), copy: $("copyBtn"),
    inspectorEmpty: $("inspectorEmpty"), live: $("live"),
  };

  let last = null;          // the last successful result
  let building = false;
  let activeTab = "brand";
  let inspectorReady = false;

  /* ---------- small helpers ---------- */

  function el(tag, attrs = {}, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    }
    node.append(...kids);
    return node;
  }
  const pct = (v) => `${Math.round(v * 100)}%`;
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const announce = (msg) => { els.live.textContent = msg; };
  const slug = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "page";

  function rgba(hex, a) {
    const n = parseInt(hex.replace("#", ""), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }

  function hostOf(url) {
    try {
      return new URL(/^https?:\/\//i.test(url) ? url : `https://${url}`).hostname.replace(/^www\./, "");
    } catch { return url; }
  }

  function looksLikeUrl(v) {
    try { return new URL(/^https?:\/\//i.test(v) ? v : `https://${v}`).hostname.includes("."); }
    catch { return false; }
  }

  function showError(msg) { els.error.textContent = msg; els.error.hidden = !msg; }

  /* ---------- brand theming: the tool wears Site A ---------- */

  function applyTheme(theme) {
    const s = document.documentElement.style;
    s.setProperty("--accent", theme.primary);
    s.setProperty("--on-accent", theme.on_primary);
    s.setProperty("--accent-mark", theme.link);
    s.setProperty("--stripe", theme.on_primary.toLowerCase() === "#ffffff" ? "rgba(0, 0, 0, .18)" : "rgba(255, 255, 255, .3)");
  }
  function clearTheme() {
    const s = document.documentElement.style;
    ["--accent", "--on-accent", "--accent-mark", "--stripe"].forEach((p) => s.removeProperty(p));
  }
  function restoreTheme() { last ? applyTheme(last.theme) : clearTheme(); }

  const SKETCH_VARS = ["--sk-hero", "--sk-hero-line", "--sk-accent", "--sk-paper", "--sk-paper2", "--sk-line", "--sk-ink", "--sk-dot"];
  function tintSketch(t) {
    const s = els.sketch.style;
    s.setProperty("--sk-hero", t.deep);
    s.setProperty("--sk-hero-line", rgba(t.on_deep, 0.72));
    s.setProperty("--sk-accent", t.hero_accent);
    s.setProperty("--sk-paper", t.paper);
    s.setProperty("--sk-paper2", t.paper_2);
    s.setProperty("--sk-line", rgba(t.ink, 0.16));
    s.setProperty("--sk-ink", rgba(t.ink, 0.4));
    s.setProperty("--sk-dot", t.primary);
  }
  function resetSketch() { SKETCH_VARS.forEach((p) => els.sketch.style.removeProperty(p)); }

  /* ---------- stage states ---------- */

  function showEmpty() {
    els.preflight.hidden = false; els.emptyCopy.hidden = false; els.steps.hidden = true;
    els.frame.hidden = true; els.tools.hidden = true;
    els.title.textContent = "Page preview";
  }
  function showBuilding() {
    els.preflight.hidden = false; els.emptyCopy.hidden = true; els.steps.hidden = false;
    els.frame.hidden = true; els.tools.hidden = true;
    els.title.textContent = "Building the page";
  }
  function showPage() {
    els.preflight.hidden = true; els.frame.hidden = false; els.tools.hidden = false;
  }

  /* ---------- progress steps ---------- */

  function buildSteps() {
    els.steps.replaceChildren(...STEP_LABELS.map((label, i) =>
      el("li", { class: i === 0 ? "step active" : "step" },
        el("span", { class: "dot" }),
        el("div", {}, el("span", { class: "name", text: label }), el("span", { class: "detail" })))));
  }

  function stepDone(i, detail) {
    const items = els.steps.children;
    if (items[i]) {
      items[i].className = "step done";
      items[i].querySelector(".detail").textContent = detail || "";
    }
    if (items[i + 1]) items[i + 1].className = "step active";
    announce(`Step ${i + 1} of ${STEP_LABELS.length} done: ${STEP_LABELS[i]}. ${detail || ""}`);
  }

  /* ---------- inspector ---------- */

  function setInspector(state) {           // "empty" | "loading" | "ready"
    inspectorReady = state === "ready";
    els.inspectorEmpty.hidden = inspectorReady;
    els.inspectorEmpty.textContent = state === "loading"
      ? "Reading both sites. The palette, sections and keywords appear here when the page is built."
      : "After a page is built, this shows what was read from each site: the palette and fonts, the sections, and the keywords.";
    selectTab(activeTab);
  }

  function selectTab(name, focus = false) {
    activeTab = name;
    TABS.forEach((t) => {
      const tab = $(`tab-${t}`), panel = $(`panel-${t}`);
      const on = t === name;
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
      panel.hidden = !(on && inspectorReady);
      if (on && focus) tab.focus();
    });
  }

  document.querySelector(".tabs").addEventListener("click", (e) => {
    const tab = e.target.closest('[role="tab"]');
    if (tab) selectTab(tab.id.replace("tab-", ""));
  });
  document.querySelector(".tabs").addEventListener("keydown", (e) => {
    const i = TABS.indexOf(activeTab);
    const next = { ArrowRight: (i + 1) % TABS.length, ArrowLeft: (i + TABS.length - 1) % TABS.length, Home: 0, End: TABS.length - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    selectTab(TABS[next], true);
  });

  function renderBrand(r) {
    const a = r.site_a, colors = a.colors;

    // palette shares, straight from the K-means cluster weights
    const bar = $("paletteBar"), list = $("swatches");
    const palette = colors.palette || [];
    bar.replaceChildren(...palette.map((p) => {
      const seg = el("span", { title: `${p.hex.toUpperCase()}, ${pct(p.weight)}` });
      seg.style.flexGrow = String(p.weight);
      seg.style.background = p.hex;
      return seg;
    }));
    bar.hidden = palette.length === 0;
    bar.setAttribute("aria-label", "Palette shares: " + palette.map((p) => `${p.hex.toUpperCase()} ${pct(p.weight)}`).join(", "));

    const share = (hex) => { const m = palette.find((p) => p.hex.toLowerCase() === hex.toLowerCase()); return m ? pct(m.weight) : ""; };
    const rows = [];
    rows.push({ hex: colors.primary, role: colors.fallback ? "Neutral fallback" : "Primary" });
    if (colors.accent) rows.push({ hex: colors.accent, role: "Accent" });
    palette.forEach((p) => { if (!rows.some((x) => x.hex.toLowerCase() === p.hex.toLowerCase())) rows.push({ hex: p.hex, role: "" }); });
    list.replaceChildren(...rows.map((row) => {
      const chip = el("span", { class: "chip" });
      chip.style.background = row.hex;
      const name = el("span", {}, el("span", { class: "hex", text: row.hex.toUpperCase() }));
      if (row.role) name.append(el("span", { class: "role", text: row.role }));
      return el("li", {}, chip, name, el("span", { class: "share", text: share(row.hex) }));
    }));

    const css = r.css_report || {};
    let note = colors.fallback
      ? "No distinct brand colour was found, so a neutral was used."
      : `${colors.raw_count} colour values, grouped into ${palette.length} with K-means.`;
    if (css.linked) note += ` ${css.read} of ${css.linked} stylesheets could be read.`;
    $("paletteNote").textContent = note;

    // fonts
    const fonts = a.fonts || [];
    $("fontList").replaceChildren(...(fonts.length
      ? fonts.map((f) => el("li", {}, el("span", { text: f.name }), el("span", { class: "from", text: f.source === "google" ? "Google Fonts" : "Site CSS" })))
      : [el("li", {}, el("span", { text: "None found. The page uses the system font." }))]));

    // tone
    const scores = Object.entries(a.tone.scores || {}).filter(([, v]) => v > 0).sort((x, y) => y[1] - x[1]);
    $("toneList").replaceChildren(...scores.map(([k, v]) => {
      const fill = el("span"); fill.style.width = pct(v);
      return el("li", {}, el("span", { text: k[0].toUpperCase() + k.slice(1) }), el("span", { class: "track" }, fill), el("span", { class: "val", text: pct(v) }));
    }));
    $("toneNote").textContent = a.tone.dominant && a.tone.dominant !== "neutral"
      ? `The page layout follows the ${a.tone.dominant} tone.`
      : "No strong tone signal, so the page uses a neutral layout.";
  }

  function renderContent(r) {
    const v = r.similarity, p = Math.round(v * 100);
    $("overlapPct").textContent = `${p}%`;
    $("overlapWords").textContent = v >= 0.5 ? "Very similar topics."
      : v >= 0.25 ? "Some overlap in topic."
      : v >= 0.1 ? "Mostly different topics."
      : "Very different topics, so this is a big transplant.";
    $("overlapFill").style.width = `${Math.max(2, Math.min(100, p))}%`;
    $("overlapMeter").setAttribute("aria-label", `Topic overlap ${p} percent`);
    $("swapNote").textContent = `“${r.site_b.brand_name}” is replaced by “${r.site_a.brand_name}” throughout the copy.`;

    // Same order the page uses: the hero is the first section with a heading.
    const secs = r.generated.sections;
    const hi = secs.findIndex((s) => s.heading);
    const ordered = hi <= 0 ? secs : [secs[hi], ...secs.slice(0, hi), ...secs.slice(hi + 1)];
    $("sectionList").replaceChildren(...ordered.map((s, i) => {
      const parts = [];
      if (i === 0 && hi !== -1) parts.push("Hero");
      if (s.body) parts.push("paragraph");
      if (s.bullets.length) parts.push(plural(s.bullets.length, "bullet", "bullets"));
      const meta = parts.join(", ");
      return el("li", {},
        el("span", {},
          el("span", { class: s.heading ? "h" : "h none", text: s.heading || "No heading" }),
          el("span", { class: "m", text: meta ? meta[0].toUpperCase() + meta.slice(1) : "" })));
    }));
  }

  function renderKeywords(r) {
    const brandA = r.site_a.brand_name, brandB = r.site_b.brand_name;
    const kw = (r.seo.primary_keyword || "").trim();
    const woven = r.generated.keyword_weaves || 0;
    const swapped = kw && kw.toLowerCase() === brandA.toLowerCase();

    $("kwOutcome").textContent = !kw
      ? "None of the strong keywords were suitable to add to the copy, so the text was left as written."
      : swapped ? `The top keyword was ${brandB}'s own name, so every mention of it now reads ${brandA}.`
      : woven > 0 ? `“${kw}” was added to the copy in ${plural(woven, "place", "places")}.`
      : `“${kw}” was chosen, but the copy already covered it, so nothing was added.`;

    const b = brandB.toLowerCase(), bFirst = b.split(/[\s,]+/)[0];
    const ul = $("kwList");
    if (!r.seo.keywords.length) {
      ul.replaceChildren(el("li", {}, el("span"), el("span", { text: "Not enough text on Site B to rank keywords." })));
      return;
    }
    ul.replaceChildren(...r.seo.keywords.map((k) => {
      const word = el("span", { class: "word", text: k.keyword });
      const kl = k.keyword.toLowerCase();
      if (swapped && (kl === b || kl === bFirst)) word.append(el("span", { class: "used", text: `Now reads ${brandA}` }));
      else if (kw && !swapped && woven > 0 && kl === kw.toLowerCase()) word.append(el("span", { class: "used", text: "Added to the copy" }));
      const fill = el("span"); fill.style.width = pct(k.confidence);
      return el("li", {},
        el("span", { class: `tag ${k.strength.toLowerCase()}`, text: k.strength }),
        word,
        el("span", { class: "conf", "aria-label": `${pct(k.confidence)} confident` },
          el("span", { class: "track" }, fill), el("span", { class: "val", text: pct(k.confidence) })));
    }));
  }

  function renderWarnings(list) {
    els.warnings.replaceChildren(...(list || []).map((w) => el("p", { text: w })));
    els.warnings.hidden = !(list && list.length);
  }

  /* ---------- recent pairs (URLs only, stored in this browser) ---------- */

  function loadRecent() {
    try { const v = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); return Array.isArray(v) ? v : []; }
    catch { return []; }
  }
  function saveRecent(a, b) {
    try {
      const rest = loadRecent().filter((x) => !(x.a === a && x.b === b));
      localStorage.setItem(RECENT_KEY, JSON.stringify([{ a, b }, ...rest].slice(0, 4)));
    } catch { /* storage unavailable: recents are a convenience only */ }
    renderRecent();
  }
  function renderRecent() {
    const items = loadRecent();
    els.recent.hidden = items.length === 0;
    els.recentList.replaceChildren(...items.map((x) => {
      const btn = el("button", { type: "button", text: `${hostOf(x.a)} with ${hostOf(x.b)}` });
      btn.addEventListener("click", () => { els.urlA.value = x.a; els.urlB.value = x.b; els.form.requestSubmit(); });
      return el("li", {}, btn);
    }));
    if (items.length) {
      const clear = el("button", { type: "button", class: "clear", text: "Clear" });
      clear.addEventListener("click", () => { try { localStorage.removeItem(RECENT_KEY); } catch { /* ignore */ } renderRecent(); });
      els.recentList.append(el("li", {}, clear));
    }
  }

  /* ---------- building ---------- */

  async function readStream(res, onEvent) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (line) onEvent(JSON.parse(line));
        }
      }
      buf += decoder.decode();
      if (buf.trim()) onEvent(JSON.parse(buf));
    } finally {
      reader.cancel().catch(() => {});
    }
  }

  class BuildError extends Error {}

  async function build(a, b, density) {
    building = true;
    els.go.disabled = true;
    els.go.textContent = "Building…";
    showError("");
    renderWarnings([]);
    resetSketch();
    buildSteps();
    showBuilding();
    setInspector("loading");

    let result = null;
    try {
      const res = await fetch("/api/rebrand/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url_a: a, url_b: b, density }),
      });
      if (!res.ok) {
        let data = null;
        try { data = await res.json(); } catch { /* not JSON */ }
        throw new BuildError((data && data.error) || `The server answered with an error (${res.status}).`);
      }
      await readStream(res, (ev) => {
        if (ev.type === "step") {
          stepDone(ev.done, ev.detail);
          if (ev.data && ev.data.brand) { applyTheme(ev.data.brand.theme); tintSketch(ev.data.brand.theme); }
        } else if (ev.type === "result") {
          result = ev.result;
        } else if (ev.type === "error") {
          throw new BuildError(ev.message);
        }
      });
      if (!result) throw new BuildError("The connection closed before the page was finished. Try again.");

      if (!reduceMotion) await sleep(450);   // let the finished steps and tinted sketch register
      last = result;
      applyTheme(result.theme);
      renderWarnings(result.warnings);
      renderBrand(result); renderContent(result); renderKeywords(result);
      els.title.textContent = `${result.site_b.brand_name}'s content in ${result.site_a.brand_name}'s brand`;
      els.frame.srcdoc = result.rendered_html;
      showPage();
      setInspector("ready");
      saveRecent(a, b);
      announce(`Page built: ${els.title.textContent}.`);
      els.stage.focus({ preventScroll: true });
      if (window.innerWidth < 1100) els.stage.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    } catch (err) {
      restoreTheme();
      if (last) {
        showPage();
        setInspector("ready");
        els.title.textContent = `${last.site_b.brand_name}'s content in ${last.site_a.brand_name}'s brand`;
      } else {
        showEmpty();
        setInspector("empty");
      }
      const offline = err instanceof TypeError;
      showError(offline ? "Couldn't reach the BrandForge server. Check that app.py is still running." : err.message);
    } finally {
      building = false;
      els.go.disabled = false;
      els.go.textContent = "Build page";
    }
  }

  els.form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (building) return;
    showError("");
    const a = els.urlA.value.trim(), b = els.urlB.value.trim();
    if (!a || !b) { showError("Enter a site for both the brand and the content."); (a ? els.urlB : els.urlA).focus(); return; }
    if (!looksLikeUrl(a)) { showError("The brand site doesn't look like a web address. Try something like pypi.org."); els.urlA.focus(); return; }
    if (!looksLikeUrl(b)) { showError("The content site doesn't look like a web address. Try something like github.com."); els.urlB.focus(); return; }
    build(a, b, els.form.querySelector('input[name="density"]:checked').value);
  });

  els.swap.addEventListener("click", () => {
    [els.urlA.value, els.urlB.value] = [els.urlB.value, els.urlA.value];
    announce("Swapped: the brand site and the content site have traded places.");
  });

  els.example.addEventListener("click", () => {
    els.urlA.value = "pypi.org"; els.urlB.value = "github.com";
    els.form.requestSubmit();
  });

  /* ---------- stage actions ---------- */

  els.tools.querySelector(".devices").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-device]");
    if (!btn) return;
    els.viewport.dataset.device = btn.dataset.device;
    els.tools.querySelectorAll(".devices button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
  });

  els.openTab.addEventListener("click", () => {
    if (!last) return;
    // Opened synchronously from the click so it is not treated as a pop-up.
    const win = window.open("", "_blank");
    if (!win) { showError("Your browser blocked the new tab. Allow pop-ups for this page and try again."); return; }
    const url = `/api/render/${encodeURIComponent(last.render_id)}`;
    fetch(url, { method: "HEAD" })
      .then((r) => { if (!r.ok) throw new Error("expired"); win.location.href = url; })
      .catch(() => {
        // The server may have dropped the page; the HTML is still in memory here.
        const blobUrl = URL.createObjectURL(new Blob([last.rendered_html], { type: "text/html" }));
        win.location.href = blobUrl;
        setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
      });
  });

  els.download.addEventListener("click", () => {
    if (!last) return;
    const blob = new Blob([last.rendered_html], { type: "text/html" });
    const a = el("a", { href: URL.createObjectURL(blob), download: `${slug(last.site_a.brand_name)}-brand-${slug(last.site_b.brand_name)}-content.html` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    flash(els.download, "Downloaded");
  });

  els.copy.addEventListener("click", async () => {
    if (!last) return;
    try {
      await navigator.clipboard.writeText(last.rendered_html);
      flash(els.copy, "Copied");
    } catch {
      // Clipboard API unavailable (insecure origin or blocked): fall back to selection.
      const ta = el("textarea", { "aria-hidden": "true" });
      ta.value = last.rendered_html; ta.style.cssText = "position:fixed;left:-9999px;top:0";
      document.body.append(ta); ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { /* ignore */ }
      ta.remove();
      flash(els.copy, ok ? "Copied" : "Copy failed");
    }
  });

  function flash(btn, text) {
    const original = btn.dataset.label || btn.textContent;
    btn.dataset.label = original;
    btn.textContent = text;
    announce(text);
    clearTimeout(btn._t);
    btn._t = setTimeout(() => { btn.textContent = original; }, 1600);
  }

  /* ---------- start ---------- */

  const params = new URLSearchParams(location.search);
  if (params.get("a")) els.urlA.value = params.get("a");
  if (params.get("b")) els.urlB.value = params.get("b");
  renderRecent();
  setInspector("empty");
})();
