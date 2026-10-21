"""
app.py - BrandForge web app.

    python app.py            ->  http://127.0.0.1:5000

Routes
    GET  /                    the UI
    POST /api/rebrand         {url_a, url_b, density} -> JSON incl. rendered_html
    POST /api/rebrand/stream  same request, answered as newline-delimited JSON:
                                {"type":"step","done":i,"detail":...,"data":...}  x8
                                {"type":"result","result":{...}}   or
                                {"type":"error","message":...}
    GET  /api/render/<id>     the rendered page by itself (for "Open in new tab")
    GET  /api/health          liveness check
"""
from __future__ import annotations

import json
import queue
import secrets
import threading
from collections import OrderedDict
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from brandforge.pipeline import PipelineError, run_pipeline

BASE = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(BASE / "static"), static_url_path="/static")
app.config["JSON_SORT_KEYS"] = False

# Rendered pages are kept in memory (newest 40) so a new tab can load one by
# id. This is deliberately not a database: pages are disposable previews.
_MAX_PAGES = 40
_pages: "OrderedDict[str, str]" = OrderedDict()
_lock = threading.Lock()


def _store_page(html: str) -> str:
    page_id = secrets.token_urlsafe(9)
    with _lock:
        _pages[page_id] = html
        while len(_pages) > _MAX_PAGES:
            _pages.popitem(last=False)
    return page_id


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


def _read_request():
    """Validate the JSON body. Returns ((url_a, url_b, density), None) or
    (None, (json_error_response, status))."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "Send a JSON body with url_a and url_b."}), 400)
    url_a = str(data.get("url_a", "")).strip()
    url_b = str(data.get("url_b", "")).strip()
    if not url_a or not url_b:
        return None, (jsonify({"error": "Both Site A and Site B need a URL."}), 400)
    return (url_a, url_b, str(data.get("density", "medium"))), None


_CRASH_MESSAGE = "Something went wrong while building the page. Try different URLs."


@app.post("/api/rebrand")
def rebrand():
    args, err = _read_request()
    if err:
        return err
    try:
        result = run_pipeline(*args)
    except PipelineError as exc:
        # Expected failures (blocked site, bad URL, empty page): a clean 422.
        return jsonify({"error": str(exc)}), 422
    except Exception:  # noqa: BLE001 - never leak a traceback to the browser
        app.logger.exception("pipeline crashed")
        return jsonify({"error": _CRASH_MESSAGE}), 500

    result["render_id"] = _store_page(result["rendered_html"])
    return jsonify(result)


@app.post("/api/rebrand/stream")
def rebrand_stream():
    """Same job as /api/rebrand, but reports each finished step as it happens
    so a UI can show real progress instead of a timer."""
    args, err = _read_request()
    if err:
        return err

    events: "queue.Queue" = queue.Queue()
    finished = object()

    def on_step(index, detail, data):
        events.put({"type": "step", "done": index, "detail": detail, "data": data})

    def work():
        try:
            result = run_pipeline(*args, on_step=on_step)
            result["render_id"] = _store_page(result["rendered_html"])
            events.put({"type": "result", "result": result})
        except PipelineError as exc:
            events.put({"type": "error", "message": str(exc)})
        except Exception:  # noqa: BLE001
            app.logger.exception("pipeline crashed")
            events.put({"type": "error", "message": _CRASH_MESSAGE})
        finally:
            events.put(finished)

    threading.Thread(target=work, daemon=True).start()

    def generate():
        while True:
            try:
                item = events.get(timeout=2)
            except queue.Empty:
                yield "\n"          # keep-alive during a slow fetch; clients skip blank lines
                continue
            if item is finished:
                return
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return Response(generate(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/render/<page_id>")
def render_page(page_id: str):
    with _lock:
        html = _pages.get(page_id)
    if html is None:
        return Response("This preview has expired. Run the transplant again.",
                        status=404, mimetype="text/plain")
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
