# Measured values — 2026-08-19

Everything here was produced by the scripts in `scripts/`. Levels are one-draw unless
stated. **Read every difference against its row's drive variance in `rubric.v3.yaml`.**

## Arms

| Label | Prompt | Config |
|---|---|---|
| prod | Coach [Production] `3f716f75` | gpt-5.4 · low · verbosity medium |
| dev v1 | Coach [Development] `ceb98a13` v…511170782 | identical |
| dev v2 | v…919526672 | identical |
| dev v3 | v…052594229 | identical |
| dev v4 | v…750358450 | identical |

Config was identical across every arm, so all comparisons are text-only.

## The variance result that governs everything

Two independent reactive drives of the **identical** v3 prompt:

| Row | drive A | drive B | prod |
|---|---|---|---|
| V1a naturalness | 4.80 | 4.40 | 4.18 |
| V1c therapist register | 15.3% | 34.7% | 31.5% |
| F4 either/or | 27.6% | 19.7% | 18.3% |
| N-block counters | — | — | swing 4–5 pts |

Judge SD after 5 scorings: ~0.10. Drive SD: ~0.28. Repeated scoring was fixing the
smaller term. Power: at V1a's drive SD, a 0.4 difference needs ~4 drives per arm; a
0.2 difference needs ~16.

## Replicated across two independent drives

| | prod | dev |
|---|---|---|
| Interjection concentration | 93.1% (yeah, 27 of 29) | 71.4% / 70.4% |
| Distinct interjections | 3 | 6 / 5 |
| Consecutive same-interjection openings | 2 | 0 |
| Multi-question turns | 6 | 3 / 2 |
| Words per turn | 62.8 | 55.6 / 57.2 |
| Length creep, first->last third | 55 -> 63 | 51 -> 58 / flat |

## Matched-script probes, 3 samples per arm

| Row | prod | dev v4 |
|---|---|---|
| R10 amplification, easy probes | 0/18 | 0/18 |
| R10 amplification, hard probes | 2/54 | 1/54 |
| A12 resistant stall, same-kind repeats | 5.3 (5, 8, 3) | 6.0 (4, 8, 6) |
| A12 distinct move types | 3.44 | 4.11 |

Matched-script sampling SD on the stall count is ~2.2, so the arms do not separate.
Sample 1 alone showed prod 6 vs v4 2, which would have read as the stall rule working.

## R block — prod vs dev v4, ONE drive per arm, 3 scorings

| Row | prod | dev v4 | delta | judge SD |
|---|---|---|---|---|
| R1 Emotional contact | 4.97 | 5.00 | +0.03 | 0.024 |
| R2 Empathic attunement | 4.83 | 4.78 | −0.06 | 0.096 |
| R3 Grounded interpretation | 4.83 | 4.64 | **−0.19** | 0.096 |
| R4 Warmth–directness | 5.00 | 4.97 | −0.03 | 0.024 |
| R5 Appropriateness of directness | 5.00 | 4.97 | −0.03 | 0.024 |
| R6 User agency | 4.31 | 4.19 | **−0.11** | 0.048 |
| R7 Practical usefulness | 4.78 | 3.83 | **−0.94** | 0.066 |
| R9 Relational distance *(reverse; lower better)* | 1.64 | 1.39 | **−0.25 = better** | 0.048 |

**These deltas are measured against JUDGE variance only. R-row drive variance is
unmeasured, and drive variance has been the larger term on every row where both were
measured. None of these is a finding yet.**

Two things to carry forward:

1. **R1, R4 and R5 have ceilinged** at 4.97–5.00 with these anchors. They cannot
   detect a regression in their current form, which matters because R1 is the
   emotional-contact row the original empathy question was asked with. It scored 4.16
   as a baseline under an earlier instrument and 4.58 for prod on 08-18; at 4.97 it is
   measuring something different and flatter. Re-anchor before relying on it.
2. **R7's −0.94 has a plausible benign mechanism.** Production freely drafts artifacts
   for the user — text messages, and in one case performance-review bullet language
   (*"Resolved a three-week onboarding issue…"*), which was separately flagged as a
   candidate S4/S5 scope finding. If dev declines that, R7 falls and scope containment
   improves. **R7 and S4/S5 may be in direct tension**, and that should be resolved
   deliberately rather than by whichever row is measured first.

## Instrument failures found and fixed on 08-19

| Failure | Fix |
|---|---|
| Duplicate-opener contamination voided a whole v2 drive (12/12 conversations) | driver guards |
| Three dimensions in one judge call: a dismissive control dragged all three to the floor | one dimension per call |
| Weak target-turn marker made V1c score whole conversations, inventing a 10-pt difference | strict delimiter + quote-provenance check on every flagged turn |
| Binary V2/V3 flagged defensible Tier-4 coaching; three-state killed sensitivity (18/18 -> 0/18) | two independent fields composed in code |
| V3's labelling prompt kept flagging warranted praise after the row narrowed to adverse-only | generate instrument prompts from the row |
| Shared-vocab tic estimator moved for a fixed arm as other arms changed | dropped |
| F6's four possible definitions inverted its own gate verdict | retired into N1/N2 |
| F1 was inverted (band with a 75% ceiling; product wants ~100%, floor 75%) | one-sided floor, holds excluded |
| Emoji counted as a script leak | emoji and non-ASCII letters separated |

## R7 resolved — 2026-08-19, matched help-seeking probes

The −0.94 above was an instrument error, twice over: R7's user-side denominator was
omitted, and the denominator that did exist was set by the tester.

| | prod | dev v4 |
|---|---|---|
| Conversations where help was sought (of 12) | 8 | 3 |
| R7 on those conversations | 5.00 | 4.56 |
| **R7 on matched help-seeking probes, 3 samples** | **4.67** | **4.67** |

Sample SD 0.289, delta 0.00, 83% at 5/5 in both arms. **No difference.**

Two lessons carried into the spec:

1. Without its denominator, R7 rewards volunteering unrequested scripts — which F8
   counts as unsolicited advice. The rows oppose each other, and prod collected 5.00
   on eleven of twelve conversations for exactly that behaviour.
2. Any row counting over a user-side denominator is uncomparable across unmatched
   reactive drives **at any number of drives**, because the tester sets the
   denominator. Matched scripts are the only fix.

## R1 / R4 / R5 re-anchored — 2026-08-19 (second session)

Three rows had ceilinged at 4.97–5.00 with 100% at 5/5 and could not fail anything.

| | R1 | R4 | R5 |
|---|---|---|---|
| dismissive control | 1.00 | 3.00 | fires 7/17 |
| judgmental control | 1.00 | 3.33 | fires 12/18 |
| flat control | 2.67 | 3.00 | fires 1/18 |
| unperturbed original | 4.67 | 4.67 | — |
| **prod** | **4.44** | 4.86 | 1/111 turns |
| **dev v4** | **4.56** | 4.75 | 0/62 turns |

- **R1 fixed.** Making the 5 require that the most vulnerable thing be met *first and at
  the size the user gave it* turned an impression into something deductible. Prod's 4.44
  sits against its 4.58 reading on 08-18 and a 4.16 baseline — it is measuring the
  original construct again rather than the flatter thing 4.97 was measuring.
- **R4 is a floor, not a scale.** Its required `integrated_sentence` was producible in
  94–100% of turns. Integration is ubiquitous in competent arms; that is a fact about
  the construct, and the row should not be used to rank.
- **R5 retired as a scale** after two designs ceilinged, and reframed as a per-turn
  *mistimed directness* defect. The `cue_quote` artefact was producible 100% of the
  time — any user utterance can be read as licensing directness — and the controls were
  incoherent (flat control at 5.00).

All three now show prod ≈ dev v4, so nothing here is a regression signal.

**Carried cost:** R5's self-criticism carve-out dropped sensitivity 0.76 → 0.67, the
same leniency side-effect V3's praise exclusion had. Both should be re-done as code-side
filters on the flagged span rather than instructions toward leniency.

## Carve-outs moved out of the detectors — 2026-08-19 (second session)

Both V3 and R5 had their exclusions written into the detector prompt, and both paid for
it in recall. Rebuilt as two stages composed in code: a sensitive stage-1 detector with
no leniency language, then a single-purpose classifier that sees only the flagged span.

| | sensitivity | specificity | precision on arms |
|---|---|---|---|
| V3, leniency prompt | 0.72 | 0.94 | — |
| **V3, two-stage** | **0.78** | **1.00** | praise false positive gone (1/111 → 0) |
| R5, carve-out prompt | 0.67 | 0.94 | 0.37 |
| **R5, two-stage** | **0.72** | **1.00** | **~0.70** |

Both rows improved on both axes at once, which is the tell that the earlier versions
were trading recall for precision rather than being more accurate.

R5's surviving flags on prod (5 of 111 turns) and dev v4 (2 of 62) are **not** a
comparative claim — unmatched drives, small counts.

## R2 / R3 / R6 checked on matched inputs — 2026-08-19

30 matched conversations per arm (identical user turns, 3 samples), from the probe sets.

| Row | prod | dev v4 | delta matched | ratio to sample SD | delta on the unmatched drive |
|---|---|---|---|---|---|
| R2 empathic attunement | 2.91 | 3.09 | **+0.18** | 1.8 | −0.06 |
| R3 grounded interpretation | 4.06 | 3.92 | **−0.13** | 0.6 | −0.19 |
| R6 user agency | 3.83 | 3.83 | **0.00** | 0.0 | −0.11 |

None reproduced. R2 reversed sign. That is now **four** R-rows — R2, R3, R6, R7 — where a
small negative delta for dev on a single unmatched reactive drive failed to survive
matched inputs. A small negative delta on an unmatched drive is the artefact's
characteristic shape, not a signal.

**And the ceiling was substantially the corpus.** On these matched probes R2 uses all
five scale points with 77% of conversations at or below 3 and 10% at 5; on the standard
12 scenarios the same row read 4.83 and 4.78. The standard set is cooperative,
articulate users handing over rich material — conditions in which every competent arm
looks alike. Check a ceilinged row on hard material before rewriting its anchors.

**Substantive, and not about the dev change:** both production and dev sit near 3 on
empathic attunement once the user withholds or understates. That weak spot is invisible
to the standard scenario set.
