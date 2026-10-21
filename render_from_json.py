#!/usr/bin/env python3
"""
render_from_json.py - render a branded HTML page from two JSON files.

Standalone: no URL fetching, no scraping, no ML. Give it a brand.json and a
content.json and it writes one self-contained HTML file.

    python render_from_json.py brand.json content.json output.html

Accepted brand.json shapes
    simple      {"brand_name": "Acme", "colors": {"primary": "#0068a4"},
                 "typography": {"heading_font": "Inter", "body_font": "Inter"}}
    extractor   the Branded Content Generator's raw brand.json
                (primary_color_guess / background_colors / font_families ...)

Accepted content.json shapes
    simple      {"sections": [{"heading", "body", "bullets": []}]}
    blocks      {"content_blocks": [{"type": "heading|paragraph|list", ...}]}
    outline     {"outline": {"sections": [...]}}  (analyze_content.py output)

Options
    --brand-name NAME   override the brand name shown on the page
    --tone TONE         technical | warm | bold | trustworthy | premium
    --primary HEX       override the primary colour
    --quiet             suppress the notes about how inputs were interpreted
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brandforge.loaders import InputError, load_brand, load_content
from brandforge.renderer import STYLE_PROFILES, render_full_page


def read_json(path: str, label: str):
    p = Path(path)
    if not p.exists():
        raise InputError(f"{label} file not found: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))  # -sig tolerates a BOM
    except json.JSONDecodeError as exc:
        raise InputError(f"{label} is not valid JSON ({path}, line {exc.lineno}: {exc.msg}).")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="render_from_json.py",
        description="Render a branded HTML page from brand.json + content.json.",
    )
    ap.add_argument("brand", help="path to brand.json")
    ap.add_argument("content", help="path to content.json")
    ap.add_argument("output", nargs="?", default="output.html", help="output HTML path (default: output.html)")
    ap.add_argument("--brand-name", help="override the brand name")
    ap.add_argument("--tone", choices=sorted(k for k in STYLE_PROFILES if k != "neutral"),
                    help="layout personality (default: from brand.json, else neutral)")
    ap.add_argument("--primary", help="override the primary colour, e.g. #0068a4")
    ap.add_argument("--quiet", action="store_true", help="do not print interpretation notes")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        brand, brand_notes = load_brand(read_json(args.brand, "brand.json"))
        content, content_notes = load_content(read_json(args.content, "content.json"))

        if args.brand_name:
            brand["brand_name"] = args.brand_name.strip()
        if args.tone:
            brand["tone"] = args.tone
        if args.primary:
            from brandforge.extractors import hex_to_rgb
            if not hex_to_rgb(args.primary):
                raise InputError(f"--primary '{args.primary}' is not a valid hex colour.")
            brand["colors"]["primary"] = args.primary if args.primary.startswith("#") else "#" + args.primary

        page = render_full_page(brand, content, note="Rendered from brand.json + content.json")

        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        for n in brand_notes + content_notes:
            print(f"  - {n}")
    print(f"Wrote {out}  ({len(page):,} bytes, {len(content['sections'])} sections, brand: {brand['brand_name']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
