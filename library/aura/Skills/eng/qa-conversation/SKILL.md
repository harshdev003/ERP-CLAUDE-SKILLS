---
name: qa-conversation
description: >
  Evaluate a conversational Braintrust prompt (Coach/Aurie and siblings) against
  the Conversation QA Rubric, and emit a scorecard plus the transcripts behind it.
  Builds on eng:qa-prompt — that skill owns Braintrust prompt fetch, tool-default
  strategy, and single-variant drives; this one owns the rubric, the validated
  detectors, the reactive and matched-script drive harnesses, drive-level
  replication, and the scorecard format. Use when the user says "test this coach
  prompt", "QA the new dev prompt", "run the rubric against this version",
  "compare dev vs prod on tone", "did the new wording regress anything",
  "run the stall probes", "score naturalness", or hands over a Braintrust prompt
  URL for a conversation prompt with intent to evaluate rather than smoke-test.
  Also the home of the stall and amplification probe sets and their controls.
  Do NOT use for non-conversational prompts (use eng:qa-prompt), or to edit a
  prompt (use eng:update-intelligence-prompt).
metadata:
  version: "0.8.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Braintrust
    - Tools/OpenAI
    - Products/Aurie
---

# QA Conversation

Score a conversation prompt against the Conversation QA measurement spec. The human
rubric lives in Notion (`Conversation QA Rubric`, page
`3c175e23af2081319614e1ef4c434ca3`) and the executable spec lives at
`references/rubric.v3.yaml`. **The YAML is the operational copy** — thresholds,
anchors, suite IDs and mode contracts are read from it at run time so a run is
reproducible. Every run must select `dataset_replay` or `multi_turn` before choosing
an instrument; never combine their replication units or aggregate scores. Read
`notion_sync` at the start of a run and warn when it is not `synced`; do not imply
that Notion and YAML are identical while an operational revision is pending there.

**Check `review_status` before letting a row gate anything.** A row marked
`machine_parsed` is derived from the Notion tables rather than from use: it may be
reported, and it may not block a release until a human has read it against Notion and
removed the flag. `notion_sync` states how many are outstanding.

**A mirror missing rows still parses, still runs, and still prints a clean scorecard
over whatever it contains** — so the row count is an invariant, not a statistic.
`tests/test_rubric_mirror.py` enforces it.

## Select the measurement mode first

| Mode | Start here |
|---|---|
| `dataset_replay` | `scripts/offline_gate.py` — `plan` shows what the candidate's change class requires — read from `references/release_contract.yaml`, not held in the script — and the recorded baseline for each dataset; `contract` prints what blocks a release for a class and what only informs; `score` turns results into the scorecard; `gate` returns the verdict alone. Identical fixed inputs, ≥3 generations per ordinary case per arm, five-response cohorts for repetition, blinded pass/fail/uncertain review. Baselines live in `references/baselines.dataset_replay.yaml` — dataset-replay values only, because `measured.md` is drive data and mixing the two compares across two rulers. |
| `multi_turn` | Use the drive, matched-script, detector and scorecard workflow below. Drive variance and user-side denominator rules apply here. |

Never mix their denominators, replication units, or aggregate scores.

## Multi-turn rules that cost the most to learn

Every one of these was paid for with a wrong result. They apply to `multi_turn` unless a row explicitly says otherwise.

1. **Drives, not scorings, are the unit of replication — and drive variance is the
   larger term.** Two independent reactive drives of one *identical* prompt returned
   V1a 4.80 and 4.40, V1c 15.3% and 34.7%, either/or 27.6% and 19.7%. Five repeated
   judge scorings had already cut judge SD to ~0.10. Repeated scoring fixes the
   *small* term. **≥3 drives per arm for any comparative claim**, reported as a mean
   of drive means with drive-level variance.
2. **Confidence language may never exceed the number of independent drives.** Five
   judge passes on one drive licenses "in this drive", never "established".
3. **Rows with a user-side denominator are never comparable across unmatched drives.** R7, F7, F8, H1 and A12 all count over "turns where the user sought help / presented an opportunity / stalled". In a reactive drive the *tester* decides how often that happens. Measured: help was sought in 8 of 12 prod conversations and 3 of 12 dev conversations because the tester asked one arm for wording and not the other; R7 read 5.00 vs 4.56 across non-overlapping sets. On matched help-seeking probes the same two prompts both scored 4.67. **These rows require matched scripts — more drives will not fix them.**
4. **Match the scripts wherever the behaviour allows it.** If the user's job in a
   scenario is to withhold, understate, or stay flat regardless of what the assistant
   says, fix the turns and reuse them across arms. That removes the tester from the
   measurement, which was the single largest confound. Reactive drives are for reading
   behaviour; matched scripts are for comparing arms. Matched scripts still carry
   model sampling variance — a resistant-stall count moved 5→8→3 across three
   samples of the same prompt — so still run ≥3 samples.
5. **Never make predictions before a run.** In a self-driven reactive drive the
   predictor also authors the data, so a prediction can steer the drive invisibly.
   Declaring *which rows will be scored* is fine and encouraged; predicting *what
   they will show* is not.
6. **Freeze the definition before the threshold.** Four readings of "standalone
   Yeah." moved a gate from failing two arms to failing five. A row whose detector is
   looser than its name inverts its own verdict.
7. **A floor needs a positive control; a scale needs a discrimination check; a
   defect row needs both, plus a negative control.** A detector reading zero
   everywhere with no control that fires is an untested detector, not good behaviour.
   Store controls beside the row and run them every time.
8. **Carve-outs belong in a second stage, not in the detector prompt.** Writing "do not
   flag warranted praise" or "do not flag pushback on self-criticism" into a detector
   makes it more lenient about *everything*: V3 lost 0.72 sensitivity that way and R5
   lost precision down to 0.37. The fix is two stages composed in code — a sensitive
   stage-1 detector with no leniency language at all, then a narrow single-purpose
   classifier that sees only the flagged span and answers one question. Measured, that
   raised V3 to 0.78 sensitivity with perfect specificity and R5's precision to ~0.70,
   costing neither. `scripts/label_two_stage.py`.
9. **Instrument prompts are generated from the row definition, never maintained
   separately.** V3 was narrowed to *adverse* judgment in the rubric while its
   labelling prompt kept the old wording, and it went on flagging warranted praise —
   twice, because the first time it was noted and not fixed.
10. **Score defects per turn, not per conversation.** Conversation-level averaging hid
   dismissiveness completely: every arm scored 4.83+ with nothing at or below 3.
11. **A counter that does not move is not evidence an edit failed.** State the
   dissociation and name what each side measures.
12. **A gate failure blocks a release; it does not rank candidates during selection.**
    One stray brace in 72 turns is a zero-tolerance release failure and simultaneously
    tells you nothing about which wording is better.
13. **Transcripts ship BEFORE the scorecard**, always, without being asked. Every
    substantive finding in this project came from reading the material: the leaked
    deliberation, the F6 definition inversion, Tier-4 turns miscounted as judgment,
    the tic relocating from "yeah" to "oof".
14. **The scorecard prints what did not run.** A five-row smoke run and a full run
    must not produce documents that look alike.

## Multi-turn phases

Dataset replay follows the locked suite contract in the YAML and stops before the drive harness. The phases below are for `multi_turn`.

### 1. Set up

- `scripts/fetch_prompt.py <prompt-id> <out.json>` pulls prompt text + config from
  Braintrust. Run it for every arm. **Diff the arms** — on 08-18 a "dev vs prod" run
  was byte-identical and would have been a null by construction.
- Record M1–M7. Config identical across arms makes it a text-only comparison; if
  reasoning effort or verbosity differ, say so, because config has halved a template
  rate with the prompt held byte-identical.
- Extract the prompt's own prohibitions for S8, and **diff them against the previous
  version** — a dropped `refuses romantic roleplay` line was caught this way before
  any drive was spent.

### 2. Choose the corpus before blaming the instrument

A ceilinged judged row may be reporting the corpus, not its own anchors. Measured: R2
read 4.83 on the standard 12 scenarios and **2.91 on matched probes**, where it used all
five scale points with 77% of conversations at or below 3. The standard set is
cooperative, articulate users handing over rich material — conditions in which every
competent arm looks alike. **Check a ceilinged row's scale usage on hard material before
re-anchoring it.**

The corollary is a product finding worth keeping in view: both production and dev score
around 3 on empathic attunement once the user withholds or understates, which the
standard scenario set structurally cannot show.

### 3. Choose the instrument for the question

| Question | Instrument |
|---|---|
| How does it behave? | reactive drive, `scripts/drive_reactive.py` |
| Is arm A better than arm B on a judged row? | ≥3 reactive drives per arm, or matched scripts if the behaviour allows |
| Does it handle a specific hard condition? | matched probes, `scripts/drive_probes.py` |
| Mechanical counts | `scripts/counters.py` |
| Latency of an undeployed model/config | `scripts/measure_latency.py` (streaming, round-robin, n≥30) |
| Latency in production | `eng:analyze-aura-latency` — per prompter, P50/P95/P99, real traffic |

The standard 12-scenario set is **regression surveillance** — naturalness, pacing,
question behaviour, warmth, forward motion. It is *not* evidence for the stall or
amplification rows: measured, only 1 of 12 scenarios produces even two consecutive
minimal user answers, so those denominators are empty there.

### 4. Drive

`scripts/drive_reactive.py` refuses to append a duplicate consecutive user turn and
refuses to run when state ends on an unanswered turn. Both guards exist because a
crashed invocation had already called the model and persisted state; re-running it
duplicated all 12 openers and voided a whole run.

### 5. Score

- `scripts/judge_row.py` — **conversation-level** judged rows (R1, R2, R3, R6, R9). One
  dimension per call: scored three at once, a dismissive control dragged all three to
  the floor. It refuses R4, R5, R7 and V1b with a pointer, because those are no longer
  conversation-level scales and scoring them here measures the retired construct.
- **Per-turn labellers wrap the target in `>>> TARGET TURN <<<` / `>>> END TARGET <<<`
  and verify the returned span actually sits inside that turn.** A weaker marker made a
  detector score whole conversations and invent a 10-point difference; the provenance
  check is what catches that, and it warns rather than reporting the counts. Conversation-
  level rows in `judge_row.py` score the whole transcript by design and have no target
  turn to check against.
- `scripts/label_two_stage.py` — **the per-turn defect labeller for V2/V3 and R5.** Stage 1
  is a sensitive detector with no leniency language; stage 2 is a narrow exclusion
  classifier over the flagged span only; code composes them. `label_turns.py` is the
  stage-1 component alone and should not be run as the labeller on its own.
- `scripts/label_movetype.py` — A12 move-type classification; code detects kind-change.
- `scripts/holds.py` — F1's hold denominator, majority of ≥5. It is unstable
  (6/7 then 7/6 on identical data); freeze the labels after review.
- `scripts/report.py` — scorecard.

### 6. Report

Transcripts first. Then the scorecard: M block, per-row results with gate verdicts,
findings, **instrument problems found this run**, not-measured list, caveats.

## Cost: the drives and judges run on flex

Every script here that calls OpenAI sends an explicit `service_tier`, and the
default is **flex** — half the price of standard, slower, which is irrelevant to
a judge pass or an overnight drive. An omitted tier bills standard, so writing
nothing is the expensive option; this skill's spend was the single largest line
in `eng:report-prompt-testing-cost` before the tier was made explicit.

These scripts take positional arguments, so the tier comes from the environment:

```bash
AINATIVE_OPENAI_SERVICE_TIER=standard python3 scripts/judge_row.py transcripts.json R2
```

Ask the user which tier they want before a run that will spend meaningfully, and
say what it costs: flex 0.5x, standard 1x, fast 2x. Rates and model eligibility
are canonical on AI Knowledge `Tools/OpenAI`.

A tier name that isn't a tier stops the run. For an unattended sweep that would
rather finish at 1x than abort on a typo, set
`AINATIVE_OPENAI_TIER_FALLBACK=standard`; the fallback is announced on stderr,
never taken quietly.

Unlike `eng:qa-prompt`, these scripts do **not** auto-retry a refused flex call —
they own a hand-rolled retry loop over a request body they serialize once, so
swapping the tier mid-loop would mean rebuilding it. Instead they print the
refusal and its one-line fix to stderr, so a capacity refusal is never mistaken
for a model that would not answer. Re-run with the env var set.

`scripts/measure_latency.py` is the exception below.

## Latency

Production latency already has an owner: `eng:analyze-aura-latency` reports TTFT, TTLT
and handler duration per prompter at P50/P95/P99 from real traffic. Do not duplicate it.

`scripts/measure_latency.py` exists for the one case that skill cannot cover — a model
or config that is not deployed, so no production data can exist. It streams, round-robins
across arms rather than batching them, discards warm-ups so no arm is compared cold
against warm, and wants n≥30 because latency is heavy-tailed. At n=3 a medium-effort arm
measured *faster* than the same prompt at low effort, which cannot be real.

**It refuses to run without an explicit service tier.** Every other script in
this skill defaults to flex to halve the bill; this one measures latency, so the
tier *is* the variable under test and a silent flex default would produce a real
number for a tier production does not run. Set
`AINATIVE_OPENAI_SERVICE_TIER` to whatever production uses for the prompter —
required **unless every arm names its own `"service_tier"`**, which is already
explicit and is how you compare two tiers against each other. One unstated arm
means the run's tier is unstated, and it refuses. The tier is printed in the
header and carried per arm as a column in the results table, because a latency
figure without its tier is unquotable.

**Only the relative ordering between configs transfers.** The harness sends a small system
prompt; production sends ~18–20K cached tokens through IntelligenceService. Never quote
harness milliseconds as production latency, and never attribute a TTFT change to prompt
wording — wording reaches latency only through token count, which M5 already records.

## Row status, as measured

| Row | State |
|---|---|
| V1a conversational naturalness | works; ≥5 scorings AND ≥3 drives; per-conversation scores never reportable |
| V1b Aurie voice | **retired** — ceilinged in six consecutive measurements |
| R1 emotional contact | conversation-level 1–5. A 5 requires the most vulnerable thing to be met first and at the user's size, as the substance of the turn rather than its preface. Controls score 1.00–2.67 against arms at 4.44/4.56 |
| R4 warmth–directness | floor only. Controls deduct to ~3.0 but 81–86% of real conversations score 5 — integration is ubiquitous, so do not rank on it |
| R5 mistimed directness | per-turn defect count, two-stage. 0.72 sensitivity, 1.00 specificity, ~0.70 precision. Raw directness recorded descriptively (3.08 against a 3.23 baseline) |
| V1c therapist register | diagnostic within a drive only; ~19pt drive variance |
| V2 dismissiveness / V3 adverse judgment | **two-stage**: sensitive detector then a narrow exclusion classifier on the flagged span, composed in code. 0.78 sensitivity, 1.00 specificity |
| V4 holistic preference | optional, forced-choice, needs a baseline arm |
| R10 amplification | validated both directions; controls fire 5/5, negative control silent |
| A12 stall response | validated; two criteria — rubric-standard shift, and the stricter *change of move kind*. Report both. |
| N1 tic load | equal-n bootstrap only; shared-vocab estimator dropped as pool-dependent |
| N2 / N3 | ~4–5pt drive variance; nothing under ~10pt survives one drive |
| F1 | one-sided floor ≥75%, holds excluded from the denominator, question counts anywhere in the turn |
| F6 / F3 | retired into N1/N2 and N3 |
| R2 / R3 / R6 | no regression on matched inputs (+0.18 / −0.13 / 0.00); their unmatched-drive negatives were artefacts. They discriminate well on hard corpora |
| R7 practical usefulness | needs its user-side denominator AND matched scripts; matched probes in `references/probes/help_seeking_v1.json` |
| S1–S3 safety suite | **never run on any dev version.** Needs the roster snapshot, a pinned judge, and a fresh baseline |

## Probes

`references/probes/` holds the matched-script probe sets; `references/controls/` holds
their positive and negative controls with recorded validation results. They live under
`references/` deliberately — Agent Skills loads only `scripts/`, `references/` and
`assets/`, so a top-level `probes/` directory would be silently dropped from the skill
surface even though the workflow depends on it. Probe 6 of the amplification set
is the negative control — genuine distress the user is minimising, where matching the
real weight is *correct*. Without it the row punishes accurate attunement.

Gaps logged, not built: a friendship-register stall probe (would confound A12 with
A11's mode-switch decision), and scope/constraint probes for S4/S5/S8/S9.
