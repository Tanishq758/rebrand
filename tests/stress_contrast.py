"""Stress test: every foreground/background pair the page CSS actually paints
must meet WCAG AA (4.5:1), for any primary colour.

Run:  python tests/stress_contrast.py
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from brandforge.renderer import build_theme, _contrast, _hex_to_rgb  # noqa: E402

PAIRS = {
    "ink/paper": ("ink", "paper"),
    "ink/paper_2": ("ink", "paper_2"),
    "muted/paper": ("muted", "paper"),
    "muted/paper_2": ("muted", "paper_2"),
    "link/paper": ("link", "paper"),
    "link/paper_2": ("link", "paper_2"),
    "on_primary/primary": ("on_primary", "primary"),
    "on_deep/deep": ("on_deep", "deep"),
    "hero_accent/deep": ("hero_accent", "deep"),
    "on_hero_accent/hero_accent": ("on_hero_accent", "hero_accent"),
    "on_accent/accent": ("on_accent", "accent"),
}


def c(a, b):
    return _contrast(_hex_to_rgb(a), _hex_to_rgb(b))


def rand_hex():
    return "#%02x%02x%02x" % tuple(random.randint(0, 255) for _ in range(3))


def run(n=5000, seed=7):
    random.seed(seed)
    worst = {k: 99.0 for k in PAIRS}
    fails = {k: 0 for k in PAIRS}
    for _ in range(n):
        accent = rand_hex() if random.random() < 0.3 else None
        t = build_theme(rand_hex(), accent)
        for k, (a, b) in PAIRS.items():
            r = c(t[a], t[b])
            worst[k] = min(worst[k], r)
            if r < 4.5:
                fails[k] += 1
    return worst, fails


if __name__ == "__main__":
    worst, fails = run()
    total = 0
    for k in PAIRS:
        flag = "" if fails[k] == 0 else "   <-- FAIL"
        print(f"  {k:28} worst={worst[k]:5.2f}  fails={fails[k]}{flag}")
        total += fails[k]
    print("TOTAL FAILURES:", total)
    sys.exit(1 if total else 0)
