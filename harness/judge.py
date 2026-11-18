"""Judge: blind A/B comparison of the reference and the current attempt.

The judge (Opus) sees two PNGs named A.png and B.png in a random order drawn
once per round and saved to disk (so a resumed run keeps the same order). It
scores both images on the same criteria, lists defects for both, and decides
parity with the rule written in its prompt. The harness then:
  - validates the verdict against a strict schema,
  - maps A/B back to reference/attempt ("unblinds"),
  - recomputes the parity rule from the scores and flags any disagreement.
"""
import json
import random
import shutil
from pathlib import Path

# Default criteria. config.json "criteria" can replace them; the judge prompt must
# then use the same names (checked before any spend, see check_prompt_criteria).
# These are the criteria of judge_v1 and judge_v2, used when config.json has no
# "criteria" line. The current judge (v4) renames craft to clean and lists its
# criteria in config.json explicitly.
CRITERIA = ("fidelity", "coherence", "readability", "composition", "craft")
SEVERITIES = ("critical", "major", "minor")
DEFECT_FIELDS = ("criterion", "severity", "what", "where", "expected")
MAX_DEFECTS = 5
# v1 only: judge_v1.txt has this placeholder and received the drawing prompt.
# From judge_v2 on, no judge prompt has it, so the judge never sees the request.
# Kept so the v1 runs (out-final-*) stay reproducible.
PROMPT_PLACEHOLDER = "{{DRAWING_PROMPT}}"


class VerdictError(RuntimeError):
    """The judge's answer is not a valid verdict: a new judge session is needed."""


def build_prompt(template_path: Path, drawing_prompt: str) -> str:
    """The judge prompt as sent. judge_v1 carried the drawing prompt ({{DRAWING_PROMPT}});
    from judge_v2 on, the judge sees only the two renders, so there is nothing to insert."""
    template = template_path.read_text(encoding="utf-8")
    return template.replace(PROMPT_PLACEHOLDER, drawing_prompt)  # v1 only; a no-op from judge_v2 on


def check_prompt_criteria(prompt_text: str, criteria) -> list:
    """Criteria whose quoted name ("craft") is missing from the judge prompt. The
    prompt's JSON shape must list every criterion as a key, or the judge cannot
    answer in the expected schema and every verdict would be rejected (paid)."""
    return [c for c in criteria if f'"{c}"' not in prompt_text]


def blind_mapping(path: Path) -> dict:
    """{"A": "reference"|"attempt", "B": ...}, drawn once and saved for resume."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    roles = ["reference", "attempt"]
    random.SystemRandom().shuffle(roles)
    mapping = {"A": roles[0], "B": roles[1]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return mapping


def prepare_workdir(workdir: Path, mapping: dict, pngs: dict) -> None:
    """Fresh dir holding only A.png and B.png. `pngs` maps role -> PNG path."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    for label, role in mapping.items():
        shutil.copyfile(pngs[role], workdir / f"{label}.png")


def _extract_json(text: str) -> dict:
    """The prompt asks for bare JSON; tolerate a code fence or stray prose around it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise VerdictError("no JSON object in the judge's answer")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise VerdictError(f"judge answer is not valid JSON: {e}") from e


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise VerdictError(f"invalid verdict: {msg}")


def parse_verdict(text: str, criteria=CRITERIA):
    """Parse and validate against `criteria`. Returns (verdict, warnings). Raises VerdictError."""
    v = _extract_json(text)
    warnings = []
    _require(isinstance(v, dict), "top level is not an object")
    images = v.get("images")
    _require(isinstance(images, dict) and set(images) == {"A", "B"}, "'images' must have exactly A and B")

    for label in ("A", "B"):
        img = images[label]
        _require(isinstance(img, dict), f"images.{label} is not an object")
        _require(isinstance(img.get("summary"), str), f"images.{label}.summary missing")
        scores = img.get("scores")
        _require(isinstance(scores, dict) and set(scores) == set(criteria),
                 f"images.{label}.scores must have exactly {', '.join(criteria)}")
        for c, s in scores.items():
            _require(isinstance(s, int) and not isinstance(s, bool) and 1 <= s <= 5,
                     f"images.{label}.scores.{c} must be an integer 1-5, got {s!r}")
        defects = img.get("defects")
        _require(isinstance(defects, list), f"images.{label}.defects must be a list")
        for i, d in enumerate(defects):
            _require(isinstance(d, dict), f"images.{label}.defects[{i}] is not an object")
            for f in DEFECT_FIELDS:
                _require(isinstance(d.get(f), str) and d[f].strip(),
                         f"images.{label}.defects[{i}].{f} missing or empty")
            _require(d["criterion"] in criteria, f"images.{label}.defects[{i}].criterion {d['criterion']!r}")
            _require(d["severity"] in SEVERITIES, f"images.{label}.defects[{i}].severity {d['severity']!r}")
        if len(defects) > MAX_DEFECTS:
            warnings.append(f"image {label}: {len(defects)} defects, kept the first {MAX_DEFECTS}")
            img["defects"] = defects[:MAX_DEFECTS]

    na = v.setdefault("not_applicable", [])
    _require(isinstance(na, list), "'not_applicable' must be a list")
    _require(isinstance(v.get("parity"), bool), "'parity' must be true or false")
    _require(isinstance(v.get("parity_reason"), str), "'parity_reason' missing")
    return v, warnings


def rule_parity(v: dict, max_total_gap: int = 1) -> bool:
    """The parity rule from the judge prompt, recomputed from the judge's own scores.

    max_total_gap must match the judge prompt (config.json "parity_max_total_gap"):
    1 for the 5-criteria judges (v1, v2, v4), 2 for judge_v3 (7 criteria, same strictness).
    """
    a, b = v["images"]["A"], v["images"]["B"]
    close = all(abs(a["scores"][c] - b["scores"][c]) <= 1 for c in a["scores"])
    total_gap = abs(sum(a["scores"].values()) - sum(b["scores"].values()))
    no_critical = not any(d["severity"] == "critical" for d in a["defects"] + b["defects"])
    return close and total_gap <= max_total_gap and no_critical


def unblind(v: dict, mapping: dict) -> dict:
    """Same verdict keyed by role instead of A/B."""
    return {role: v["images"][label] for label, role in mapping.items()}


def attempt_at_least_reference(by_role: dict) -> bool:
    """Stop rule (harness-side, see REPORT): attempt >= reference on every criterion, no critical defect.

    Needed because blind parity is symmetric: an attempt that clearly beats the
    reference would otherwise read as "no parity".
    """
    att, ref = by_role["attempt"], by_role["reference"]
    return (all(att["scores"][c] >= ref["scores"][c] for c in ref["scores"])
            and not any(d["severity"] == "critical" for d in att["defects"]))
