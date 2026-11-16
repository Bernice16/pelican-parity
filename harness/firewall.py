"""Firewall: the only path from the judge's verdict to the refining model.

Haiku must improve its own drawing without ever seeing the reference. So:
  - only the ATTEMPT's defects pass (never the reference's, never scores,
    never summaries, never the A/B labels);
  - each defect is checked for leaks (mentions of the other image, exact
    colour values, coordinates or numbers, SVG/code vocabulary); a defect
    that leaks is dropped whole and logged, never "cleaned up";
  - the final refine prompt is checked once more before it is sent.
The judge prompt already forbids all of this; the firewall does not trust it.
"""
import re

FEEDBACK_FIELDS = ("severity", "criterion", "what", "where", "expected")
SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}

# (name, pattern). Case-insensitive, except the A/B labels themselves:
# "image A" is a leak, "the drawing a bit lower" is not.
LEAK_PATTERNS = [
    ("other-image", r"(?-i:\b(?i:image|drawing|picture|version)\s*[\"'(]?[AB]\b)"),
    ("other-image", r"(?-i:\b[AB]\.png\b)"),
    ("other-image", r"\b(the\s+)?other\s+(image|drawing|picture|one|version)\b"),
    ("other-image", r"\b(reference|unlike|compared\s+(to|with)|in\s+contrast)\b"),
    ("model-name", r"\b(fable|opus|haiku|claude)\b"),
    ("number", r"\d"),
    ("colour-value", r"#[0-9a-f]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\("),
    ("code", r"[<>={}]|\b(svg|xml|viewbox|stroke-width|attribute|element|transform|xmlns)\b"),
]
_COMPILED = [(name, re.compile(p, re.I)) for name, p in LEAK_PATTERNS]


def leaks(text: str) -> list:
    """Names of the leak patterns found in `text` (empty list = clean)."""
    return sorted({name for name, rx in _COMPILED if rx.search(text)})


def filter_feedback(verdict: dict):
    """Attempt defects that are safe to show the refining model.

    `verdict` is the unblinded verdict saved by the harness (keys "attempt",
    "reference", ...). Returns (kept, dropped): kept is a list of defects with
    only FEEDBACK_FIELDS, most severe first; dropped lists (defect, leak_names).
    """
    kept, dropped = [], []
    for d in verdict["attempt"]["defects"]:
        found = leaks(" ".join(d[f] for f in ("what", "where", "expected")))
        if found:
            dropped.append((d, found))
        else:
            kept.append({f: d[f] for f in FEEDBACK_FIELDS})
    kept.sort(key=lambda d: SEVERITY_ORDER[d["severity"]])
    return kept, dropped


def check_refine_prompt(prompt_text: str, reference_svg: str) -> None:
    """Last check on what Haiku will actually receive. Raises AssertionError on a leak.

    The reference SVG must not appear in it, even partly (its first 200 chars
    are enough to catch a copy-paste bug in the harness).
    """
    head = reference_svg.strip()[:200]
    if head and head in prompt_text:
        raise AssertionError("refine prompt contains the reference SVG")
