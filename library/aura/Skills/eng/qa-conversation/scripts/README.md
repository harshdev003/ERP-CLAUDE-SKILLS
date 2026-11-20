# Scripts

Lifted from the 2026-08-19 working session and renamed to their role. They produced
every number in `references/measured.md`.

Arms and paths are parameterised: each script derives its arms from the data, or takes
them as arguments. `label_two_stage.py` additionally reads `TWOSTAGE_ROWS` and
`TWOSTAGE_ARMS`, and `counters.py` reads `COUNTERS_PER_CONV`.

**What still needs a measurement run, not a code change** (see ENGPROM-64):

- **F1's hold boundary is not frozen.** 68% unanimity across five reps on a gate row
  with a 75% floor. Do not quote F1 precisely until a review freezes the labels.
- **F9's baseline looks stale.** The gate is 73 words/turn, failing above ~84 — and
  the two arms with the highest user ratings measure 84.7 and 86.7. Either the
  baseline is old or "creep" is the wrong framing for a length users tolerate.
- **V1c has no controls.** It separates arms most widely of any row and cannot be
  distinguished from a loose detector until a control set exists. Do not report it.

**And there is no observational mode.** Every instrument here assumes a drive, and the
variance rules are drive-centric. Which of them transfer to logged production traffic
is currently a judgement call made per row, in the moment.

| Script | Role |
|---|---|
| `parse_transcripts.py` | markdown transcript file -> structured turns |
| `drive_reactive.py` | reactive drive; refuses duplicate consecutive user turns and mid-round state |
| `drive_probes.py` | matched-script probe runner |
| `counters.py` | N1/N2/N3, F2, F4, F9, F10, interjection triple |
| `judge_row.py` | judged rows from the rubric spec, k reps, one dimension per call |
| `judge_v1ab.py` | V1a (and the retired V1b), 5 reps, per-arm means and drive SD |
| `label_turns.py` | per-turn V2/V3 defect labeller, two-field, composed in code |
| `label_movetype.py` | A12 move-type classifier -> code detects kind-change |
| `label_register.py` | V1c therapist-register per-turn detector |
| `holds.py` | F1 hold denominator, k reps, majority vote |

Every judged script wraps the target turn in `>>> TARGET TURN <<<` /
`>>> END TARGET <<<` and checks quote provenance. Do not weaken that marker: a
softer one made the judge score whole conversations and produced a 10-point
difference that did not exist.
