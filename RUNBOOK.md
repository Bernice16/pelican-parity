# RUNBOOK

How to rerun the harness from scratch on a new machine. Tested on Windows 11 with Git Bash. Nothing in it is Windows-specific: Linux and macOS use the same steps with their own package manager.

## 0. Get the code

```bash
git clone https://github.com/Bernice16/pelican-parity.git
cd pelican-parity
git checkout v1.1
```

Or download the source archive of the [v1.1 release](https://github.com/Bernice16/pelican-parity/releases/tag/v1.1). Python uses the standard library only: nothing to `pip install`. If `./run` says "Permission denied" (archive download), run `chmod +x run tests/run_offline.sh` once.

## 1. Install

| tool | version used | check |
|---|---|---|
| Claude Code CLI, logged in, with access to Fable 5.1, Haiku 5.5 and Opus 5.5 | 2.1.326 | `claude --version` |
| Python 3, standard library only (no `pip install`) | 3.8.3 | `python3 --version` or `python --version` |
| resvg (SVG → PNG renderer) | **0.49.0** | `resvg --version` |
| Bash (Git Bash on Windows) | any | runs `./run` |

**resvg.** Take the 0.49.0 binary for your OS from https://github.com/linebender/resvg/releases/tag/v0.49.0, or run `cargo install resvg --version 0.49.0`. On Windows, `winget install resvg` also works if it installs 0.49.0. Put it on `PATH`, then **open a new terminal** so the shell picks the new `PATH` up.

The renderer version matters because renders must be reproducible. Fonts are pinned in `fonts/`: resvg ignores the machine's fonts and uses only those (see `fonts/README.md`). Renders were bit-identical across reruns on Windows; other OSes were not tested, but the renderer version and fonts are pinned.

**Claude Code.** Install it, run `claude` once and log in. Each session runs with `--strict-mcp-config --setting-sources ""`, so your own MCP servers and settings are ignored.

## 2. Run

From the repository root:

```bash
./run --prompt prompts/pelican.txt --budget-usd 20 --out out-pelican
```

- `--prompt`: any text file holding one drawing prompt. Nothing in the harness is specific to pelicans.
- `--budget-usd`: hard cap for the whole run, reference included.
- `--out`: output directory. Use a new one for each new run, and the same one to resume.

The harness checks resvg before it spends anything.

**Live output.** One line per paid session:

```
[round 00] reference claude-fable-5-1   in=2 out=2,813 cache_r=5,594 cache_w=0  step=$0.1421  total=$0.1421  left=$19.8579
```

Judge lines add the verdict, the attempt/reference score per criterion and the totals, for example `parity=no  attempt/ref main=3/4 back=4/4 ... totals=25/30`. A last line gives `stop_reason`, `success`, `parity`, the final SVG and the total cost. `success=yes` means the attempt reached the reference: equal (`parity`) or better on every criterion (`attempt_exceeds_reference`). `parity` stays strict: it means "equal".

**Typical run:** $0.33 to $0.82 for the final runs, and 3 to 10 minutes. The judge (Opus) is about 96% of the cost of the loop.

## 3. Outputs

```
DIR/
  summary.json      prompt, success, parity, rounds, stop_reason, cost_usd per role, total_cost_usd,
                    final_svg / final_png, per-round history (scores, confirmation), models, prompt files
  usage.csv         one row per paid session, measured from claude's JSON (never estimated)
  rounds/00/        reference.svg/.png, attempt.svg/.png (baseline), blind.json (A/B order), verdict.json
  rounds/NN/        attempt.svg/.png, feedback.json (what the firewall kept and dropped),
                    refine_prompt.txt (exactly what Haiku received), verdict.json,
                    verdict_confirm.json (A/B-swapped second judgment, when there is one)
  raw/              claude's JSON result of each session (for audit; never in the ledger)
  reserved.csv      only if a session was lost before reporting its cost (see 5)
  incidents.log     only if something needs explaining (firewall drops, lost sessions, cap overruns)
```

Every session is also appended to `ledger/usage.csv`, the project-wide ledger. To use another ledger file, set `HARNESS_LEDGER=path`.

**Stop reasons and exit codes**

| stop_reason | meaning | exit |
|---|---|---|
| `parity` | the judge declared parity, and a second judgment with A/B swapped agreed | 0 |
| `attempt_exceeds_reference` | both judgments rated the attempt at least as good as the reference on every criterion | 0 |
| `round_cap` | 5 refine rounds done without success | 0 |
| `stagnation` | the best total did not improve for `stagnation_rounds` (3) rounds in a row | 0 |
| `budget` | the next round's worst case (2 refines + 2 judgments) does not fit in what is left | 0 |
| `budget` | a single step does not fit (for example a tiny budget before the reference) | 2 |
| `no_svg` | Haiku returned no usable SVG twice in a row | 0 |
| `no_new_feedback` | safety net: the next refine prompt would be identical to an earlier one (no Ralph loop). Should never happen | 0 |
| `step_cap` | one session hit its own `max_step_usd`. Raise it in `config.json`, then rerun | 1 |
| `error` | a session failed (timeout, API error, invalid verdict twice). Rerun to resume | 1 |
| `interrupted` | Ctrl+C. Rerun to resume | 130 |

## 4. Resume

Rerun **the exact same command** with the same `--out`:

- **Finished sessions are reused, never paid again.** Done markers (`*.done.json`), saved verdicts and `raw/` hold the results.
- **The loop continues where it stopped.** It does not restart the run.
- **A lost session is reserved, not billed.** If a session was killed before `claude` reported its cost (Ctrl+C, timeout, broken output), its step cap is written to `reserved.csv`. It counts against the budget, because it may have been billed, but it never goes into `usage.csv`, which holds measured costs only. `summary.json` shows it as `reserved_unmeasured_usd`.

To start over, use a new `--out` directory.

## 5. Budget control

- `config.json → max_step_usd` gives each role's worst case per session: reference 0.75, baseline 0.05, judge 0.30, refine 0.05. They are worst cases, not expected costs: over the project, references averaged $0.23 (max $0.49), judgments $0.06 (max $0.09), Haiku sessions under $0.01.
- **Before each session:** if `budget − spent < step cap`, the step is refused before any money is spent. "Spent" means measured plus reserved.
- **Before each new round:** the round needs 2 × refine + 2 × judge (one retry and one confirmation). If that does not fit, the run stops with `budget`.
- **During a session:** `claude --max-budget-usd min(step cap, remaining)` acts as a second guard. It is not a hard cap: Claude Code checks it between turns, so a session can go slightly over. Any overrun is logged in `incidents.log`.

## 6. Configuration

`config.json` sets:
- models and efforts (fixed by the brief, do not change during a run);
- `max_step_usd`;
- `session_timeout_s` (600);
- `round_cap` (5);
- `stagnation_rounds` (3): rounds in a row without a better best total before the run stops;
- the judge and refine prompt files;
- `criteria`: the judge's score names. The current judge (`judge_v3.txt`) uses main, background, count, shape, contact, clean, color; `judge_v4.txt` uses fidelity, coherence, readability, composition, clean. Without this line, the harness falls back to the judge_v1/v2 names (fidelity, coherence, readability, composition, craft). If you change them, the judge prompt's JSON shape must list the same names; the run is refused before any spend otherwise;
- `parity_max_total_gap`: the largest gap between the two totals that still counts as parity. It must match the number written in the judge prompt. Every judge prompt uses 1 (the default when the line is missing). judge_v3 was tried with 2 (same strictness on 35 points as 1 on 25), but that let a weaker drawing pass, so it went back to 1. The harness uses it to recheck the judge's parity;
- the render command (width 800).

To use another `claude` binary, set `CLAUDE_BIN=path`. To use another config, set `HARNESS_CONFIG=path`.

## 7. Offline tests ($0)

```bash
tests/run_offline.sh
```

This runs 23 scenarios with a fake `claude` and a fake renderer: no model call, no cost.

**What they cover:** confirmed parity, only the reference and baseline get the drawing prompt, rerun with zero new calls, firewall leaks (including the real false positives), stagnation, no Ralph loop (no refine prompt sent twice), attempt above reference, position bias rejected by the A/B-swapped confirmation, tiny budget, budget stop before a round, round cap, missing SVG (once and twice), malformed judge JSON (once and twice), timeout with kill, reserve and resume, step cap, missing renderer, custom criteria from `config.json`, criteria missing from the judge prompt refused before any spend, and the firewall word list (plain words pass, code is caught), a total gap of 2 accepted as parity when `parity_max_total_gap` is 2, and `success=yes` when the attempt beats the reference.

**What they check:** every scenario checks that `usage.csv`, the ledger and `summary.json` agree.

## 8. Evaluation tools (optional, paid except review and headline)

```bash
# Judge the same pair N times (judge noise); writes eval/calibration-NAME.json
python harness/calibrate.py --name self --prompt prompts/pelican.txt \
    --a out-final-v3-pelican/rounds/00/reference.png --b out-final-v3-pelican/rounds/00/reference.png \
    --times 2 --budget-usd 1
# Page for reviewing every verdict -> eval/verdict_review.md, or another name with --out (never overwrites without --force)
python harness/review.py --out verdict_final_review.md out-final-v3-pelican out-final-v3-beaver
# Reference alone vs loop cost table -> eval/headline.md
python harness/headline.py out-final-v3-pelican out-final-v3-beaver
```

## 9. Troubleshooting

| symptom | fix |
|---|---|
| `renderer not found: resvg` | install resvg 0.49.0 (see 1), open a new terminal |
| `python3` does nothing on Windows | it is the Microsoft Store stub. `./run` falls back to `python` |
| `claude: command not found`, or login errors | install Claude Code and run `claude` once to log in |
| `STOP: step cap hit ... Raise max_step_usd.<role>` | raise that role's value in `config.json`, then rerun the same command |
| `STOP: ... needs $X, $Y left` | the budget is too small for the next step or round. This is the intended refusal |
| a session hangs | it is killed after `session_timeout_s` and its cap is reserved. Rerun to resume |
| `UnicodeEncodeError` in a Windows console | set `PYTHONIOENCODING=utf-8` |
