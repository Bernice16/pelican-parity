# REPORT

All numbers come from the run folders' `usage.csv` and `summary.json`. Judge totals are the sum of 5 criteria scored 1–5 (max 25).

## 1. Results

| run | stop | refine rounds | Haiku total, round 0 → final | reference total (confirmation) | judgments | cost |
|---|---|---|---|---|---|---|
| `out-final-pelican`: pelican on a bicycle | `parity` | 1 | 18 → 22 | 23 (21 vs 22) | 3 | **$0.3819** |
| `out-final-monkey`: a monkey climbing an apple tree (own prompt) | `attempt_exceeds_reference` | 3 | 19 → 22 | 19 (22 vs 22) | 5 | **$0.5295** |

**Cost by role:**

| run | reference | baseline | judge | refine |
|---|---|---|---|---|
| pelican | $0.2027 | $0.0012 | $0.1753 | $0.0026 |
| monkey | $0.2190 | $0.0018 | $0.2999 | $0.0087 |

**Run checks:**
- **No lost sessions.** Neither run had a lost session.
- **Monkey `parity: false`.** The first judgment rated the attempt *above* the reference, so `summary.json` has `parity: false` (see §4).
- **Totals match.** Each run's `usage.csv` total equals its `total_cost_usd`.
- **Ledger complete.** Every row is also in `ledger/usage.csv`.

**Project spend: $3.2175** (all sessions):

| item | cost |
|---|---|
| reference | $1.0512 |
| judge | $1.4664 |
| Haiku | $0.0371 |
| judge calibration | $0.3105 |
| manual exploration before the harness existed | $0.3523 |

## 2. Headline: is cheap model + judge cheaper?

**No.** Over the four runs made with the final code ([`eval/headline.md`](eval/headline.md)), a run cost on average:

| Fable alone | Haiku drawings | Opus judgments | full loop |
|---|---|---|---|
| **$0.165** | $0.008 | $0.291 | **$0.299 (1.8×)** |

Per run, the full loop cost 0.88× to 3.15× what Fable alone cost.

- **Haiku is cheap enough.** Its drawings cost about 20× less than Fable's, and it reached the reference's level in 1–4 rounds every time.
- **The judge is what makes the loop expensive.** It is about 97% of the loop's cost. One judgment costs $0.05–0.09, about a third of a Fable drawing, and a run needs 3–7 judgments.
- **When the loop would pay off.** Only when judging costs much less than the drawing it replaces.

## 3. Decisions

**Judge** ([`prompts/judge_v1.txt`](prompts/judge_v1.txt), the only version):
- **Blind A/B.** The judge sees `A.png` and `B.png` in random order, so it cannot favour the strong model by name.
- **Five generic criteria.** Fidelity, coherence, readability, composition and craft, each scored 1–5 on the same anchors. Each criterion is defined relative to the prompt, so the same judge prompt works for any drawing.
- **Concrete defects.** Up to 5 per image. Each has a criterion, a severity, *what*, *where* and *expected*. *Expected* is written as an outcome. Numbers, colour values, code and any mention of the other image are forbidden.
- **Parity rule in the prompt.** Every criterion within 1, totals within 1, and no critical defect. The harness recomputes the rule and logs any disagreement with the judge.
- **Why "totals within 1".** I added it after a draft without it declared 23 vs 20 a parity. This happened before the prompt was first committed.

**Confirmation.** A "yes" (parity, or attempt ≥ reference) stands only if a second judgment agrees. It is a new session with the same judge, and A and B swapped.
- **Why.** The judge is noisy near the threshold (§4). A false "yes" ends the run, while a false "no" only costs one round.
- **Result.** The confirmation rejected 2 of 6 first-judgment "yes" verdicts.

**Firewall** (`harness/firewall.py`). This is the only path from the judge to Haiku.
- **What Haiku gets.** It receives the prompt, its own best SVG and the attempt's defects. It never gets scores, summaries, labels or the reference's defects. It has no tools and works in an empty folder.
- **Leak check.** Each defect field is checked for leaks: other-image mentions, numbers, colour values and code words. A leaking defect is dropped whole and logged.
- **Final check.** The refine prompt is checked for the reference SVG.
- **No Ralph loop.** Every round is built from a new verdict.

**Stop rules.**
- **Success.** A confirmed parity, or a confirmed attempt ≥ reference.
- **Other stops.** The run also stops at the round cap (5), on stagnation (best total not improved for 2 rounds), or when the next round's worst case does not fit the budget.
- **Refine base.** Haiku always refines its *best* round so far.
- **Why a cap of 5.** Success came in 1–4 rounds. Even with every step at its cap, 5 rounds cost about $5.10, well under $20.

**Budget.**
- **Step caps.** Each role has a worst case per session, `max_step_usd`, set at about 3× the highest cost observed.
- **Refusal.** A step is refused before any spend if its cap does not fit in `budget − measured − reserved`.
- **Not a hard cap.** `--max-budget-usd` is passed as a second guard. It is only checked between turns: a $0.0001 cap spent $0.00098.
- **Lost sessions.** A session lost before reporting its cost (timeout, Ctrl+C) has its cap *reserved* in `reserved.csv`, which counts against the budget. `usage.csv` stays measured-only.

**Live output.** The runtime prints one plain-text line per session: role, model, the 4 token counts, step cost, run total and budget left. Judge lines add the scores. It is readable live and greppable afterwards.

**Resume.**
- **What is saved.** Every paid result is saved on arrival: done markers, verdicts and `raw/`.
- **What a rerun pays for.** Rerunning the same command pays only for what is missing.
- **Real test.** A Ctrl+C during the Fable step, then a rerun (`out-m3-crash`).
- **Offline tests.** A timeout with kill, reserve and resume. A rerun of a finished run makes 0 calls.

**Orchestration and merging.**
- **One process.** `harness/main.py` runs every session in sequence, and sessions never talk to each other.
- **Offline tests.** 16 scenarios run with a fake `claude` and a fake renderer, at $0.
- **Repo.** One issue, branch and PR per milestone, and I merge each one myself.
- **How it was built.** I built the harness with Claude Code as a pair programmer, and I reviewed, ran and committed everything myself.

## 4. Where the judge was wrong

**Calibration** ([`eval/`](eval)): absolute scores are noisy, comparisons are steadier.
- **Same image twice.** Shown the same image as A and B, the judge gave identical scores and parity, 2 times out of 2.
- **Same pair, verdict flips.** One pair (23 vs 20–22) flipped parity in 2 of 5 judgments.
- **Same reference, different totals.** One unchanged Fable PNG got totals from 19 to 23.

**Review of the 8 judgments of the final runs** ([`eval/verdict_review.md`](eval/verdict_review.md)):
- **Parity decision: right in 7 of 8.**
  - **The error.** The monkey round 3 first judgment scored the unchanged reference 19. The other four judgments of that image gave it 22–23 and fidelity 5. That one low score is why the run ended as `attempt_exceeds_reference`.
  - **My view.** The reference is still about one point better, so the confirmation's 22 vs 22 parity is defensible, but "attempt exceeds reference" is not.
- **Defects are almost always real and fixable without the reference.** The refiner fixed 4 of the 5 pelican defects in one round.
  - **Missed.** Half-drawn spokes and the missing chain on the first pelican attempt, and the monkey's single visible leg (only the confirmation caught it).
  - **A stretch.** "Ears barely visible".
- **Noise.** The same defect changes severity between sessions: "no saddle" was minor in one session and major in another.
- **No systematic error,** so `judge_v1` is kept. The weak spot is the stop rule (§5).

**Firewall.** The judge never leaked. The firewall's three drops were all false positives, and both causes now have a fix and a regression test:
- **"compared with" pattern.** "small compared with the tree" was dropped twice because "compared with" was treated as a leak.
- **Joined fields.** "…of the image" + "A balanced framing" were checked together and read as "image A". The final monkey run still had this bug and lost one minor defect.

## 5. What I would change next

1. **Make "attempt exceeds reference" need both judgments.** Today, "better" followed by "parity" stops as `attempt_exceeds_reference`; it should stop as `parity`.
2. **Cut the judge's cost within the fixed model and effort.** Ask for the attempt's defects only, and skip judging attempts that obviously fail.
3. **Show Haiku the PNG of its own attempt.** It refines the SVG text blind today. Its own render does not break the firewall and would help with layout defects.
4. **Use the median of 2–3 judgments near the threshold** instead of trusting a single score.
5. **Get more data.** 4 runs give anecdotes, not estimates. Run more prompts, several times each.
6. **Close the plain-word leak.** *Expected* can still describe the reference in plain words ("a long orange beak"), and pattern checks cannot catch that.
