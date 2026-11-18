# REPORT

All numbers come from each run's `usage.csv` and `summary.json`. The final runs use `prompts/judge_v3.txt`: 7 criteria scored 1–5, so a total is out of 35. "Fable alone" is the reference session; "the loop" is everything else (Haiku baseline, refines, Opus judgments).

## 1. Results

| run | stop | refine rounds | judgments | Haiku total, round 0 → final | reference total | cost |
|---|---|---|---|---|---|---|
| `out-final-v3-pelican`: a pelican riding a bicycle | `parity` | 1 | 3 | 25 → 29 | 29 (confirmation 30 vs 31) | **$0.3302** |
| `out-final-v3-beaver`: a beaver cooking sunshine eggs (own prompt) | `parity` | 4 | 7 | 21 → 28 | 28 (confirmation 26 vs 27) | **$0.8232** |
| `out-final-v3-santa`: Santa holding presents on a scooter (own prompt) | `round_cap` | 5 | 7 | 28 → 31 | 30 (confirmation 28 vs 32) | $0.6897 |
| `out-final-v3-monkey`: a monkey climbing an apple tree (own prompt) | `stagnation`* | 3 | 4 | 20 → 21 | 30 | $0.4298 |

\* Run with the earlier stagnation setting (2 rounds); see §3.

- **Pelican.** Parity after one refine, confirmed with A and B swapped.
- **Beaver.** Round 3 was called parity (29 vs 30), but the swapped confirmation scored it 26 vs 29 and rejected it. Round 4 passed both.
- **Santa.** Close all along (28–31 against 30–32) but never confirmed. Round 3 was judged better than the reference (31 vs 30); the swapped confirmation said 28 vs 32 and rejected it. The run used all 5 rounds; the final SVG is round 3, the best total.
- **Monkey.** Haiku never got closer than 9 points. Each round the judge said the same thing (hands and feet float, a leg is missing), the feedback reached Haiku, and Haiku could not draw limbs attached to a climbing body. This is the model's limit, not the loop's.
- **Checks.** No lost sessions, no incidents. Each `usage.csv` total equals `total_cost_usd`, and every row is in `ledger/usage.csv`.

**Project spend: $14.75** over 256 sessions, all in `ledger/usage.csv`: references $6.32, judgments $7.51, Haiku $0.26, calibration $0.31, manual exploration $0.35. Most of it is exploration runs used to tune the judge.

## 2. Headline: is the cheap model plus a judge cheaper?

**No.** In every final run the loop cost more than Fable alone:

| run | Fable alone | the loop | ratio |
|---|---|---|---|
| pelican | $0.142 | $0.188 | 1.3× |
| beaver | $0.390 | $0.433 | 1.1× |
| Santa (no success) | $0.253 | $0.437 | 1.7× |
| monkey (no success) | $0.183 | $0.247 | 1.3× |

- **Haiku is almost free.** A Haiku drawing costs $0.001–0.006, under 3% of a Fable drawing (average $0.23 over 28 references).
- **The judge is the cost.** It is about 96% of the loop. One judgment costs about $0.06, a quarter of a Fable drawing, and a run needs at least 3 (round 0, the round that passes, its confirmation).
- **So the loop only breaks even when Fable is expensive** (the beaver reference cost $0.39) and Haiku succeeds in a round or two. It would pay off if judging cost much less than the drawing it replaces.

## 3. Decisions ("You decide")

**Judge: blind A/B.** The judge sees only `A.png` and `B.png`, in a random order saved per round. It never knows which one is Fable.

**The judge does not get the drawing prompt** (from v2 on). It works out the scene from the two drawings. Reason: the brief gives the prompt to the drawers only. Consequence: it cannot catch a request that both drawings miss (§4).

**Judge prompt versions** (all in `prompts/`):

| version | change | why |
|---|---|---|
| v1 | 5 criteria, gets the drawing prompt | first version |
| v2 | no drawing prompt, 5 generic criteria | follow the brief as written |
| v3 (final) | 7 short criteria in plain words: main, background, count, shape, contact, clean, color | v2's criteria could be read several ways |
| v4 | v2's 5 criteria with a "Check" line each and a counting scale | tried on one run (`out-v4-santa`), not kept |

Three rules were added to v3 after real runs:
- **Scene rule.** Both drawings come from the same request, so they must show the same subjects doing the same things; layout, pose, colours and extras are free. A missing subject or action is at least a "major" defect.
- **Shared unusual choices are the scene.** If both drawings show a dog on two legs, that is not a mistake.
- **"Subject" is defined** as any character or thing that matters: who acts and what they ride, hold or use. So a missing scooter counts as much as a missing Santa.

**Parity rule.** Every criterion within 1 point, totals within 1, and no critical defect. The harness recomputes it from the judge's scores and logs any disagreement. With 7 criteria I tried a total gap of 2 (the same strictness, in proportion, as 1 on 25). It let a weaker Santa pass (§4), so I went back to 1.

**Confirmation.** A "yes" stands only if a second judgment, in a new session with A and B swapped, agrees. A false "yes" ends the run; a false "no" costs one round. It rejected beaver round 3.

**Success.** Parity, or the attempt at least as good as the reference on every criterion. `summary.json` has `success` and a strict `parity` ("equal"), because a blind judge can only say "close", not "at least as good".

**Firewall** (`harness/firewall.py`). The only path from the judge to Haiku. Haiku gets its own SVG and at most 5 of its own defects, never the reference, scores, summaries or the prompt. Defects mentioning the other image, numbers, colour values or code are dropped and logged. The first version also dropped plain words ("a background element", "compared with"); those were fixed and are tested.

**Stop rules.** Round cap 5. Stagnation: the best total has not improved for 3 rounds in a row (it was 2, and an earlier Santa run stopped only 2 points short). Budget: the next round's worst case must fit. Haiku refines its best round; each judge verdict feeds at most one refine, so the same prompt is never sent twice (no Ralph loop).

**Budget.** Each role has a worst-case step cap, checked before every session against `budget − measured − reserved`. A session lost before reporting its cost is reserved, not billed. `--max-budget-usd` is a second guard only.

**Live output, resume, tests.** One line per session with tokens, step cost, total and budget left. Every paid result is saved on arrival, so rerunning the same command pays only for what is missing. 23 offline scenarios run with a fake `claude` at $0. I built the harness with Claude Code as a pair programmer, and reviewed, ran and committed everything myself.

## 4. Where the judge was wrong

I reviewed all 21 judgments of the four final runs ([`eval/verdict_final_review.md`](eval/verdict_final_review.md)). The parity decision was right in 19. The two errors were first judgments saying "yes" too early (beaver and Santa, round 3), and the swapped confirmation caught both.

- **Santa sitting on the presents** (`out-v3-santa`, total gap 2). The request was "holding presents". In Haiku's drawing the presents sit on the deck and Santa's hand is a dot on top of them; he looks like he is sitting on them. The judge saw it ("unclear whether he is holding them") but rated it minor, and the confirmation passed at 28 vs 30. Fixes: a missing action is now at least major, `contact` checks "hands on what they hold", and the gap went back to 1.
- **It cannot check the request.** In an earlier Santa run, Fable also put the presents on the deck. Both drawings missed "holding", so a blind judge with no prompt saw nothing wrong.
- **Noise.** The same unchanged reference PNG got 27 to 31 within the beaver run, 30 to 32 in the Santa run and 29 to 31 in the monkey run. The same pair can flip: Santa round 3 got 31 vs 30 (attempt better), then 28 vs 32 with A and B swapped; beaver round 3 got 29 vs 30, then 26 vs 29. Comparisons are steadier than absolute scores, which is why every "yes" is confirmed.
- **Text in the image.** Haiku sometimes adds a title. The judge counted Santa's "Merry Christmas!" as a plus in one session and as a distraction in the next. Fable does it too (the beaver reference has a caption). I do not remove text: the request might ask for it.
- **Defects are concrete and usable.** On the pelican, one refine took Haiku from 25 to 29. On the monkey, the judge described the same real problem in every round; Haiku is what failed.

## 5. What I would change next

1. **Show Haiku the PNG of its own attempt.** It edits SVG text blind; many defects are about layout and contact.
2. **Stop refine regressions.** On Santa, each refine fixed the listed defects and broke something else. Also pass the attempt's own strengths ("keep: …"), which is about the attempt only and leaks nothing.
3. **Add a request check.** A separate session that sees the prompt and the attempt only (never the reference) and answers "does it show what was asked?". It closes the gap in §4 without unblinding the judge.
4. **Cut the judge's cost.** Ask for the attempt's defects only, and skip the full judgment when the attempt obviously fails.
5. **Get more data.** A few runs per prompt are anecdotes; run each prompt several times and report the spread.
6. **Close the plain-word leak.** A defect's "expected" can still describe the reference in plain words ("a long orange beak"); pattern checks cannot catch that.
