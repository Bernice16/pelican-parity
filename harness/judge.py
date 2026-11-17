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

CRITERIA = ("fidelity", "coherence", "readability", "composition", "craft")
SEVERITIES = ("critical", "major", "minor")
DEFECT_FIELDS = ("criterion", "severity", "what", "where", "expected")
MAX_DEFECTS = 5
PROMPT_PLACEHOLDER = "{{DRAWING_PROMPT}}"


class VerdictError(RuntimeError):
    """The judge's answer is not a valid verdict: a new judge session is needed."""


def build_prompt(template_path: Path, drawing_prompt: str) -> str:
    """The judge prompt as sent. judge_v1 carried the drawing prompt ({{DRAWING_PROMPT}});
    from judge_v2 on, the judge sees only the two renders, so there is nothing to insert."""
    template = template_path.read_text(encoding="utf-8")
    return template.replace(PROMPT_PLACEHOLDER, drawing_prompt)


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


def parse_verdict(text: str):
    """Parse and validate. Returns (verdict, warnings). Raises VerdictError."""
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
        _require(isinstance(scores, dict) and set(scores) == set(CRITERIA),
                 f"images.{label}.scores must have exactly {', '.join(CRITERIA)}")
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
            _require(d["criterion"] in CRITERIA, f"images.{label}.defects[{i}].criterion {d['criterion']!r}")
            _require(d["severity"] in SEVERITIES, f"images.{label}.defects[{i}].severity {d['severity']!r}")
        if len(defects) > MAX_DEFECTS:
            warnings.append(f"image {label}: {len(defects)} defects, kept the first {MAX_DEFECTS}")
            img["defects"] = defects[:MAX_DEFECTS]

    na = v.setdefault("not_applicable", [])
    _require(isinstance(na, list), "'not_applicable' must be a list")
    _require(isinstance(v.get("parity"), bool), "'parity' must be true or false")
    _require(isinstance(v.get("parity_reason"), str), "'parity_reason' missing")
    return v, warnings


def rule_parity(v: dict) -> bool:
    """The parity rule from the judge prompt, recomputed from the judge's own scores."""
    a, b = v["images"]["A"], v["images"]["B"]
    close = all(abs(a["scores"][c] - b["scores"][c]) <= 1 for c in CRITERIA)
    total_gap = abs(sum(a["scores"].values()) - sum(b["scores"].values()))
    no_critical = not any(d["severity"] == "critical" for d in a["defects"] + b["defects"])
    return close and total_gap <= 1 and no_critical


def unblind(v: dict, mapping: dict) -> dict:
    """Same verdict keyed by role instead of A/B."""
    return {role: v["images"][label] for label, role in mapping.items()}


def attempt_at_least_reference(by_role: dict) -> bool:
    """Stop rule (harness-side, see REPORT): attempt >= reference on every criterion, no critical defect.

    Needed because blind parity is symmetric: an attempt that clearly beats the
    reference would otherwise read as "no parity".
    """
    att, ref = by_role["attempt"], by_role["reference"]
    return (all(att["scores"][c] >= ref["scores"][c] for c in CRITERIA)
            and not any(d["severity"] == "critical" for d in att["defects"]))
