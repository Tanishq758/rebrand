"""API tests: the JSON route, the streaming route, and the new-tab route.

Run:  python tests/test_api.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fakenet  # noqa: E402

fakenet.install()

from app import app  # noqa: E402
from brandforge.pipeline import STEPS  # noqa: E402

client = app.test_client()
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))


def stream(body):
    """POST to the stream route; return (status, [parsed events])."""
    r = client.post("/api/rebrand/stream", json=body)
    text = r.get_data(as_text=True)
    events = [json.loads(line) for line in text.split("\n") if line.strip()]
    return r, events


GOOD = {"url_a": "https://coffee.test", "url_b": "https://cloud.test", "density": "medium"}

# --- stream: happy path ------------------------------------------------------
r, ev = stream(GOOD)
check("stream: 200 and NDJSON content type", r.status_code == 200 and r.mimetype == "application/x-ndjson", r.mimetype)
steps = [e for e in ev if e["type"] == "step"]
check("stream: exactly eight step events, in order", [e["done"] for e in steps] == list(range(8)), str([e["done"] for e in steps]))
check("stream: every step carries a plain-language detail", all(e["detail"] for e in steps))
check("stream: the colour step carries brand + theme data",
      steps[2]["data"] and steps[2]["data"]["brand"]["theme"]["primary"] and steps[2]["data"]["brand"]["primary"] == "#7a3e1d")
check("stream: other steps carry no brand data", all(not e["data"] for i, e in enumerate(steps) if i != 2))
kinds = [("brand" if (e.get("data") or {}).get("brand") else e["type"]) for e in ev]
check("stream: colour data arrives well before the result (5 more steps follow it)",
      kinds.index("result") - kinds.index("brand") == 6, str(kinds))
res = ev[-1]["result"]
check("stream: result has the page, theme and a render id", res["rendered_html"].startswith("<!DOCTYPE html>") and res["theme"] and res["render_id"])
check("stream: theme in result equals the theme sent early", res["theme"] == steps[2]["data"]["brand"]["theme"])
check("stream: step labels line up with STEPS", len(STEPS) == 8)

# --- stream: failures arrive as one error event, not a broken stream ---------
for label, body, needle in [
    ("blocked Site B", {**GOOD, "url_b": "https://blocked.test"}, "Site B:"),
    ("blocked Site A", {**GOOD, "url_a": "https://blocked.test"}, "Site A:"),
    ("same page twice", {**GOOD, "url_b": "https://coffee.test/"}, "same page"),
    ("JavaScript-only Site B", {**GOOD, "url_b": "https://js.test"}, "readable sections"),
]:
    r, ev = stream(body)
    check(f"stream error ({label}): ends with a single clean error event",
          len(ev) >= 1 and ev[-1]["type"] == "error" and needle in ev[-1]["message"] and not any(e["type"] == "result" for e in ev),
          str(ev[-1:] ))

# --- stream: bad requests are ordinary JSON 400s (before any streaming) ------
r = client.post("/api/rebrand/stream", json={"url_a": "https://coffee.test"})
check("stream: missing field -> 400 JSON", r.status_code == 400 and "error" in r.get_json())
r = client.post("/api/rebrand/stream", data="hello")
check("stream: non-JSON body -> 400 JSON", r.status_code == 400)

# --- the original JSON route is unchanged -------------------------------------
r = client.post("/api/rebrand", json=GOOD)
d = r.get_json()
check("json route: still returns the full result", r.status_code == 200 and d["rendered_html"] and d["render_id"] and d["theme"])
check("json route: blocked site -> 422 with a message", client.post("/api/rebrand", json={**GOOD, "url_b": "https://blocked.test"}).status_code == 422)
check("json route: missing field -> 400", client.post("/api/rebrand", json={}).status_code == 400)

# --- new-tab route -------------------------------------------------------------
r2 = client.get(f"/api/render/{d['render_id']}")
check("render route: serves the stored page as HTML", r2.status_code == 200 and r2.mimetype == "text/html" and b"<h1>" in r2.data)
check("render route: unknown id -> 404", client.get("/api/render/nope").status_code == 404)

# --- static assets the UI depends on -------------------------------------------
for path in ["/", "/static/style.css", "/static/app.js",
             "/static/fonts/bricolage-grotesque-latin-wght-normal.woff2",
             "/static/fonts/hanken-grotesk-latin-wght-normal.woff2"]:
    check(f"static: {path} is served", client.get(path).status_code == 200)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
