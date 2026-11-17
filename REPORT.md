# REPORT

Every number below comes from the run folders' `usage.csv` and `summary.json`. The judge totals are sums of 5 criteria, scored 1–5, so the maximum is 25.

## 1. Results: rounds to parity and cost

| run | prompt | stop | refine rounds | Haiku total (round 0 → final) | reference total | judgments | total cost |
|---|---|---|---|---|---|---|---|
| `out-final-pelican` | pelican riding a bicycle | `parity` | 1 | 18 → 22 | 23 (confirmation: 21 vs 22) | 3 | **$0.3819** |
| `out-final-monkey` | a monkey climbing an apple tree (own prompt) | `attempt_exceeds_reference` | 3 | 19 → 22 | 19 (confirmation: 22 vs 22) | 5 | **$0.5295** |

**Cost by role:**
- pelican: reference $0.2027, baseline $0.0012, judge $0.1753, refine $0.0026;
- monkey: reference $0.2190, baseline $0.0018, judge $0.2999, refine $0.0087.

**Summary flags.** Neither run had a lost session (`reserved_unmeasured_usd` = 0). In the monkey run, the judge rated the attempt *above* the reference, so `summary.json` says `parity: false` and stop `attempt_exceeds_reference`. The confirmation judgment called it parity (22 vs 22).

**Numbers agree.** Each run's `usage.csv` total equals its `summary.json` `total_cost_usd`. All rows are also in `ledger/usage.csv`.

**Project spend (all sessions, `ledger/usage.csv`): $3.2175.** It splits as:
- reference $1.0512;
- judge $1.4664;
- baseline and refine $0.0371;
- judge calibration $0.3105;
- manual exploration before the harness existed $0.3523.

## 2. Headline: is the cheap model plus judge cheaper?

**No, not at these prices.** The table covers the four runs made with the final code (see [`eval/headline.md`](eval/headline.md)):

| | Fable alone | Haiku drawings | Opus judgments | loop (Haiku + judge) |
|---|---|---|---|---|
| average per run | **$0.165** | $0.008 | $0.291 | **$0.299 (1.8×)** |

Per run, the loop costs between 0.88× and 3.15× what Fable alone costs.

**Haiku is not the problem.** Its drawings cost about 20× less than Fable's, and it reached the reference's level in 1–4 rounds in all four runs. **The judge is the problem**: it makes up about 97% of the loop's cost. Each judgment costs $0.05–0.09, about a third of a Fable drawing, and a run needs 3–7 of them.

The loop only pays off when the judge is cheaper than the drawing it replaces. That would happen with a much longer or harder drawing, or if one judgment could serve many attempts (see §5).

## 3. Decisions ("You decide")

**Judge prompt and criteria** ([`prompts/judge_v1.txt`](prompts/judge_v1.txt), the only version):
- **Blind A/B.** The judge sees `A.png` and `B.png` in random order (`blind.json`), not "reference" and "attempt", so it cannot favour the strong model by name.
- **Five generic criteria, scored 1–5 with shared anchors.** Fidelity, coherence, readability, composition and craft. Each is defined relative to the prompt, so the prompt works for any drawing.
- **Up to 5 concrete defects per image.** Each one has a criterion, a severity, *what*, *where* and *expected*. *Expected* is written as an outcome, not as a recipe, and coordinates, numbers, colour values, code and mentions of the other image are forbidden.
- **The parity rule is written into the prompt.** Every criterion must be within 1, the totals within 1, and neither image may have a critical defect. The judge applies the rule. The harness recomputes it and logs any disagreement.
- **Why "totals within 1" was added.** A first draft, without that condition, declared 23 vs 20 a parity during development. I tightened it before the prompt was ever committed.

**Confirmation.** A "yes" (parity, or attempt ≥ reference) is accepted only if a second judgment agrees. It uses the same judge and prompt in a new session, with A and B swapped.
- **Why.** Calibration showed the judge is noisy near the threshold (see §4), and a false "yes" ends the run. A false "no" costs one extra round and corrects itself, so "no" verdicts are not re-checked.
- **Effect.** In the four final-code runs, the confirmation rejected 2 of the 6 first-judgment "yes" verdicts.

**Firewall** (`harness/firewall.py`). This is the only path from the judge to Haiku.
- **What passes.** Only the attempt's own defects pass: no scores, summaries, A/B labels or reference defects.
- **Leak checks.** Each defect is checked, field by field, for mentions of the other image, numbers, colour values and code words. A leaking defect is dropped whole and logged; it is never edited.
- **Last check.** The final refine prompt is checked for the reference SVG.
- **What Haiku gets.** The original prompt, its own best SVG so far, and the defects (`refine_prompt.txt` in each round). It gets no tools and an empty folder.
- **No Ralph loop.** Every round is built from a new verdict.

**Stop rules.**
- The run stops on success, which is a confirmed parity or a confirmed attempt ≥ reference.
- It also stops at the round cap of 5 or on stagnation, when the best total has not improved for 2 rounds.
- Before a round starts, the runtime checks that the round's worst case fits in the budget; if not, the run stops with `budget`.
- Haiku always refines its *best* round so far, so a bad refine never becomes the base for the next one.
- **Why these numbers.** In every run, success came within 1–4 rounds. A cap of 5 leaves room for noise. Even in the worst case (every step at its cap), round 0 and 5 rounds come to about $5.10, well under $20. Observed rounds cost about $0.06–0.12.

**Budget.** Each role has a worst case per session (`max_step_usd`), set at about 3× the highest cost observed.
- **Refusal.** A step is refused before any spend if that worst case does not fit in `budget − measured − reserved`.
- **Second guard.** `--max-budget-usd` is passed too. Probes showed it is checked only between turns (a $0.0001 cap spent $0.00098), so it is not a hard cap; any overrun is logged.
- **Lost sessions.** If a session is lost before reporting its cost (timeout, Ctrl+C), its cap is *reserved* in `reserved.csv`. It counts against the budget but never goes into `usage.csv`, which holds measured costs only.

**Live output.** One line per session: round, role, model, the four token counts, step cost, run total and budget left. Judge lines add per-criterion scores and totals. I chose this because it is plain text, greppable, and readable while the run is going.

**Resume.** Each paid result is saved as soon as it arrives: done markers, verdict files and `raw/`. A rerun of the same command reuses them and pays only for what is missing.
- **Tested for real:** Ctrl+C during the Fable session, then a rerun (`out-m3-crash`).
- **Tested offline:** timeout with kill, reserve and resume, and a rerun of a finished run, which makes 0 new calls.

**Orchestration and merging.**
- **One process drives the run.** `harness/main.py` runs every session in sequence. Sessions never talk to each other: the harness moves files between them and owns all decisions.
- **Testing without paying.** A fake `claude` and a fake renderer run 16 scenarios offline at $0.
- **Repo.** One issue, branch and PR per milestone (M0–M5), and I merge each PR myself. I built the harness with Claude Code as a pair programmer, and I reviewed, ran and committed everything myself.

## 4. Where the judge was wrong

**Scores are noisy; comparisons are steadier** ([`eval/calibration-*.json`](eval)).
- **Self-comparison.** Shown the same image as both A and B, the judge gave identical scores and parity both times.
- **Same reference, different scores.** The same Fable PNG got totals from 19 to 23. In the monkey run, the reference scored 22–23 in rounds 0–2 and 19 in the round 3 primary judgment. That low score is what made "attempt exceeds reference" possible, and the confirmation scored the same pair 22 vs 22.
- **Same pair, different verdicts.** Over five judgments of one pair (23 vs 20–22), parity flipped twice. This is why I added the confirmation and kept the parity rule strict.

**False "yes" verdicts caught by the confirmation.** Two first-judgment "yes" verdicts were not confirmed when A and B were swapped:
- `out-m3-crash` round 0: 21 vs 22, then 19 vs 22;
- `out-m3-second` round 3: 20 vs 19, then 19 vs 22.

In `out-final-monkey` round 3, the two judgments disagreed on the kind of success: the primary said "better", the confirmation said "parity". I found no consistent position effect.

**The judge never leaked.** In every run, no defect broke the firewall rules. The firewall's three drops were all *false positives*, and both causes were fixed with a regression test:
- **"compared with".** "The monkey is small compared with the tree" was dropped twice, because "compared with" was then a leak pattern.
- **Joined fields.** The fields "…the lower third of the image" and "A balanced framing…" were checked joined together, so they read as "image A". The final monkey run still ran with this bug and lost that one minor defect.

**Review of the final runs' verdicts** ([`eval/verdict_review.md`](eval/verdict_review.md)): _TODO, filled in after my review. For each verdict: was parity right, which defects were missed, which were unfair._

## 5. What I would change next

1. **Make the judge cheaper, since it is ~97% of the loop.**
   - Run the primary judgment at a lower effort and keep high effort for the confirmation.
   - Skip judging attempts that obviously fail.
   - Ask for defects only for the attempt, not the reference.
2. **Let Haiku see its own render.** Right now it refines the SVG text blind. A PNG of its *own* attempt does not break the firewall and would help with layout defects.
3. **Average judgments instead of trusting one.** Use the median of 2–3 cheap judgments near the threshold rather than a single score.
4. **Run more prompts and more runs per prompt.** With 4 runs, the rounds-to-parity and cost figures are anecdotes, not estimates.
5. **Close the plain-word leak.** The judge's *expected* text can still describe the reference in plain words ("a long orange beak"). The firewall cannot tell this apart from a valid outcome. A second, cheap judge pass that rewrites *expected* without looking at the reference would close it.
