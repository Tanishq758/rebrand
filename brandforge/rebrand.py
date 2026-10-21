"""
rebrand.py - rewrite Site B's real sections in Site A's brand voice.

Real section order and roles are preserved. Site B's own brand name is
swapped for Site A's name throughout the extracted text.
"""
from __future__ import annotations

import re


def _name_variants(name: str) -> list[str]:
    """A brand like 'GitHub, Inc.' should also be found as 'GitHub'."""
    name = (name or "").strip()
    if not name:
        return []
    variants = {name}
    stripped = re.sub(r",?\s+(inc|llc|ltd|corp|co|gmbh|plc)\.?$", "", name, flags=re.I).strip()
    if stripped:
        variants.add(stripped)
    first = stripped.split()[0] if stripped.split() else ""
    if len(first) >= 4:
        variants.add(first)
    # longest first so 'Acme Cloud' is replaced before 'Acme'
    return sorted(variants, key=len, reverse=True)


def rebrand_text(text: str, old_brand: str, new_brand: str) -> str:
    """Swap old_brand for new_brand (case-insensitive, whole-word)."""
    if not text:
        return text
    out = text
    for variant in _name_variants(old_brand):
        out = re.sub(r"(?<![\w-])" + re.escape(variant) + r"(?![\w-])",
                     new_brand, out, flags=re.I)
    return out


def _keyword_display(keyword: str, site_b_brand: str) -> str:
    """Use Site B's own capitalization if the keyword IS its brand name."""
    for variant in _name_variants(site_b_brand):
        if keyword.lower() == variant.lower():
            return variant
    return keyword


# One-word keywords this generic make poor SEO anchors and read badly when
# stitched into copy ("Built around code."), even when the classifier scores
# them Strong on frequency alone.
_GENERIC_ONE_WORD = {
    "code", "tools", "tool", "data", "work", "team", "teams", "make", "build", "built", "using",
    "use", "new", "best", "more", "free", "help", "get", "and", "platform", "software",
    "business", "development", "faster", "ship", "manage", "projects", "scale", "people",
}


def _is_proper_noun_in(keyword: str, source_text: str) -> bool:
    """True if the keyword only ever appears Capitalised in mid-sentence source
    text - i.e. it is a name (a product, a person) that belongs to Site B.
    Weaving 'Copilot' into Site A's copy would attribute B's product to A."""
    hits = list(re.finditer(r"(?<![.!?]\s)(?<!^)\b" + re.escape(keyword) + r"\b",
                            source_text, flags=re.I))
    mid = [m for m in hits if m.start() > 0 and not re.search(r"[.!?]\s*$", source_text[:m.start()])]
    if not mid:
        return False
    return all(m.group(0)[:1].isupper() for m in mid)


# A keyword phrase that is really a slice of one of Site B's sentences (a verb
# + object: "publishes literary fiction", "read submission") is not a topic. It
# reads as ungrammatical when woven in, and it would credit Site A with what
# Site B does. Third-person / gerund / past verb forms are the giveaway.
_VERB_FORM = re.compile(r"^[a-z]{3,}(?:s|ing|ed)$")
_NOT_VERBS = {"news", "business", "press", "success", "access", "analytics", "solutions", "services",
              "pricing", "marketing", "training", "learning", "software", "hardware", "settings",
              "series", "process", "progress", "address", "class", "glass", "mass", "loss", "cross",
              "boss", "focus", "status", "bonus", "campus", "virus", "plus", "bus", "gas", "atlas"}


def _looks_like_sentence_fragment(keyword: str) -> bool:
    """Heuristic, and deliberately one-sided. A suffix rule cannot tell
    'builds tests' (a fragment) from 'running shoes' (a topic), so some good
    keywords are rejected. That is the safe direction to be wrong in: a
    rejected keyword just means the copy is left exactly as Site B wrote it,
    whereas a woven-in fragment produces a broken sentence."""
    words = keyword.lower().split()
    if len(words) < 2:
        return False
    first = words[0]
    return first not in _NOT_VERBS and bool(_VERB_FORM.match(first))


def _weavable(kw: dict, brand_b: str, source_text: str = "") -> bool:
    """A keyword is worth weaving into copy if it is a phrase, or a distinctive
    single word - not a generic filler word or one of Site B's proper nouns."""
    word = kw["keyword"].lower()
    if any(word == v.lower() for v in _name_variants(brand_b)):
        return True   # the source brand itself: becomes the new brand name
    if source_text and _is_proper_noun_in(kw["keyword"], source_text):
        return False
    if _looks_like_sentence_fragment(kw["keyword"]):
        return False
    if len(word.split()) >= 2:
        return True
    return len(word) >= 5 and word not in _GENERIC_ONE_WORD


_WEAVE_TEMPLATES = (
    "Explore {kw} with {brand}.",
    "{brand} makes {kw} simpler.",
    "See how {brand} approaches {kw}.",
    "Learn more about {kw} at {brand}.",
)


def _weave_sentence(keyword: str, brand_a: str, n: int = 0) -> str:
    """Cycle through templates so a repeated weave never reads as a copy-paste."""
    return _WEAVE_TEMPLATES[n % len(_WEAVE_TEMPLATES)].format(kw=keyword, brand=brand_a)


def rebrand_content(real_sections: list[dict], brand_a: str, brand_b: str,
                    seo_keywords: list[dict] | None = None, density: str = "medium",
                    source_text: str = "") -> dict:
    """Rebrand every real section and weave the top usable Strong keyword in.

    density: 'low' | 'medium' | 'high' - how many sections carry the keyword.
    Returns primary_keyword="" when no keyword is worth weaving (better than
    forcing a filler word into the copy).
    """
    seo_keywords = seo_keywords or []
    # Only a keyword the classifier calls Strong is worth weaving in. There is
    # deliberately no fallback to Medium/Weak: on a small page nothing may be
    # Strong, and forcing a weak word ("deploy", "bread") into the copy reads
    # worse than leaving the copy exactly as written.
    usable = [k for k in seo_keywords if k["strength"] == "Strong" and _weavable(k, brand_b, source_text)]
    primary_kw = usable[0]["keyword"] if usable else ""

    # If the keyword IS Site B's brand name, Site A's own name is what belongs
    # in the copy (and B's capitalisation is irrelevant because B is replaced).
    kw_is_b_brand = bool(primary_kw) and any(
        primary_kw.lower() == v.lower() for v in _name_variants(brand_b))
    kw_for_copy = brand_a if kw_is_b_brand else primary_kw

    weave_count = {"low": 1, "medium": 2, "high": 4}.get(density, 2)

    sections = []
    woven = 0
    for i, sec in enumerate(real_sections):
        heading = rebrand_text(sec.get("heading", ""), brand_b, brand_a)
        body = rebrand_text(sec.get("body", ""), brand_b, brand_a)
        bullets = [rebrand_text(b, brand_b, brand_a) for b in sec.get("bullets", [])]

        # Only weave where the keyword is genuinely absent, and never into a
        # brand-name keyword (it is already everywhere after the swap).
        already = kw_for_copy.lower() in (heading + " " + body).lower() if kw_for_copy else True
        if kw_for_copy and not kw_is_b_brand and woven < weave_count and body and not already:
            body = body.rstrip()
            if not body.endswith((".", "!", "?")):
                body += "."
            body += " " + _weave_sentence(kw_for_copy, brand_a, woven)
            woven += 1

        sections.append({
            "role": sec.get("role", "hero" if i == 0 else "content"),
            "heading": heading,
            "body": body,
            "bullets": bullets,
        })

    return {
        "brand_name": brand_a,
        "primary_keyword": kw_for_copy,
        "source_brand": brand_b,
        "keyword_weaves": woven,
        "sections": sections,
    }
