"""
nlp.py - the machine-learning parts.

  extract_candidate_keywords()      TF-IDF over Site B's sentences
  classify_keywords_seo_strength()  KNN classifier -> Strong / Medium / Weak
  topical_similarity()              cosine similarity of Site A vs Site B

The KNN confidence is a real probability from predict_proba(), not a
distance transform.
"""
from __future__ import annotations

import re

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.neighbors import KNeighborsClassifier
from sklearn.preprocessing import StandardScaler

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(text: str) -> list[str]:
    out = [s.strip() for s in _SENT_SPLIT.split(text or "") if len(s.split()) >= 4]
    return out


# ---------------------------------------------------------------------------
# Candidate keywords (TF-IDF)
# ---------------------------------------------------------------------------

def extract_candidate_keywords(text: str, headings: list[str] | None = None, top_n: int = 15) -> list[dict]:
    """Rank 1-3 word candidate keywords by summed TF-IDF over sentences.

    Returns dicts carrying the raw features the classifier needs.
    """
    sents = _sentences(text)
    headings = headings or []
    if len(sents) < 3:
        sents = sents + [h for h in headings if len(h.split()) >= 2]
    if len(sents) < 2:
        return []

    vec = TfidfVectorizer(
        stop_words="english", ngram_range=(1, 3), lowercase=True,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z\-]{2,}\b", max_df=0.9, min_df=1,
    )
    try:
        matrix = vec.fit_transform(sents)
    except ValueError:
        return []
    terms = vec.get_feature_names_out()
    scores = np.asarray(matrix.sum(axis=0)).ravel()
    if scores.max() <= 0:
        return []
    norm = scores / scores.max()

    low_text = " ".join(sents).lower()
    low_headings = " ".join(headings).lower()
    n_words = max(1, len(low_text.split()))

    order = np.argsort(-scores)
    picked: list[dict] = []
    for idx in order:
        term = terms[idx]
        # de-duplicate: skip a term contained in an already-picked longer/equal one
        if any(term in p["keyword"] or p["keyword"] in term for p in picked):
            continue
        freq = len(re.findall(r"\b" + re.escape(term) + r"\b", low_text))
        first = low_text.find(term)
        picked.append({
            "keyword": term,
            "tfidf": float(norm[idx]),
            "word_count": len(term.split()),
            "char_length": len(term),
            "frequency": freq,
            "in_heading": 1 if term in low_headings else 0,
            "position": 1.0 - (first / max(1, len(low_text))) if first >= 0 else 0.0,
        })
        if len(picked) >= top_n:
            break
    return picked


# ---------------------------------------------------------------------------
# KNN classifier
# ---------------------------------------------------------------------------
# Feature vector: [normalized tfidf, word count, char length, frequency,
#                  in_heading, position score]
# Hand-labelled from on-page SEO heuristics: strong keywords are specific
# (2-3 words), frequent, appear in headings and early in the page; weak ones
# are single generic words that appear once, late, and nowhere prominent.

_REFERENCE_SET = [
    # tfidf, words, chars, freq, in_heading, position, label
    (0.95, 2, 16, 6, 1, 0.92, "Strong"),
    (0.88, 3, 22, 5, 1, 0.85, "Strong"),
    (0.90, 2, 14, 7, 1, 0.80, "Strong"),
    (0.82, 2, 18, 4, 1, 0.90, "Strong"),
    (0.78, 3, 26, 4, 0, 0.88, "Strong"),
    (0.60, 2, 15, 3, 1, 0.55, "Medium"),
    (0.55, 1, 9, 4, 1, 0.60, "Medium"),
    (0.50, 2, 17, 2, 0, 0.65, "Medium"),
    (0.62, 1, 8, 3, 0, 0.50, "Medium"),
    (0.45, 2, 13, 2, 1, 0.40, "Medium"),
    (0.20, 1, 6, 1, 0, 0.20, "Weak"),
    (0.15, 1, 5, 1, 0, 0.10, "Weak"),
    (0.28, 1, 7, 1, 0, 0.30, "Weak"),
    (0.10, 1, 8, 1, 0, 0.05, "Weak"),
    (0.25, 1, 10, 2, 0, 0.15, "Weak"),
]
_LABELS = ["Weak", "Medium", "Strong"]  # display order (low -> high)


def _build_classifier():
    X = np.array([r[:6] for r in _REFERENCE_SET], dtype=float)
    y = np.array([r[6] for r in _REFERENCE_SET])
    scaler = StandardScaler().fit(X)
    knn = KNeighborsClassifier(n_neighbors=3, weights="distance").fit(scaler.transform(X), y)
    return scaler, knn


_SCALER, _KNN = _build_classifier()


def classify_keywords_seo_strength(candidates: list[dict]) -> list[dict]:
    """Classify each candidate as Strong/Medium/Weak.

    confidence = predict_proba() of the predicted class - a real probability.
    """
    if not candidates:
        return []
    X = np.array([[c["tfidf"], c["word_count"], c["char_length"], c["frequency"],
                   c["in_heading"], c["position"]] for c in candidates], dtype=float)
    Xs = _SCALER.transform(X)
    probs = _KNN.predict_proba(Xs)
    classes = list(_KNN.classes_)
    results = []
    for cand, p in zip(candidates, probs):
        best = int(np.argmax(p))
        results.append({
            "keyword": str(cand["keyword"]),
            "strength": str(classes[best]),   # np.str_ -> str (JSON-safe)
            "confidence": round(float(p[best]), 3),
            "tfidf": round(cand["tfidf"], 3),
            "frequency": int(cand["frequency"]),
            "in_heading": bool(cand["in_heading"]),
        })
    rank = {"Strong": 0, "Medium": 1, "Weak": 2}
    results.sort(key=lambda r: (rank[r["strength"]], -r["confidence"], -r["tfidf"]))
    return results


# ---------------------------------------------------------------------------
# Topical similarity
# ---------------------------------------------------------------------------

def topical_similarity(text_a: str, text_b: str) -> float:
    """Cosine similarity of the two sites' TF-IDF vectors, 0-1."""
    a, b = (text_a or "").strip(), (text_b or "").strip()
    if not a or not b:
        return 0.0
    try:
        vec = TfidfVectorizer(stop_words="english", lowercase=True,
                              token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z\-]{2,}\b")
        m = vec.fit_transform([a, b])
        return round(float(cosine_similarity(m[0], m[1])[0][0]), 3)
    except ValueError:
        return 0.0
