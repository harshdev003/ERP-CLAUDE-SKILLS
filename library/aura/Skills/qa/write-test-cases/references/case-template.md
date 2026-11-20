# Case template and property values

Read by `write-test-cases` in Step 3. The authority on *why* each rule exists is
`QA/Conventions` in AI Knowledge; this file is the mechanical shape.

## Title

`<ID> — <what the case proves>`, e.g. `VOI-003 — Closing voice mode returns to chat`.

The title is a claim about behaviour, not a label for a screen. "Voice mode" is not a
title; "Closing voice mode returns to chat" is.

## Properties

| Property | Value |
|---|---|
| ID | `PREFIX-NNN`, from `next-id --prefix PREFIX`. Permanent. |
| Product | `Aurie Mobile` (the built-out suite), `Aurie Desktop`, or `Aura Core` |
| Feature | one of the 21 `PREFIX — Name` options; the prefix must match the ID |
| Priority | `P1`–`P4`, per the scale in `QA/Conventions` |
| Runner | any of `sim`, `backend`, `analytics`, `device`, `human` |
| Automatable | ticked only when an unattended run can reach a verdict |
| Status | `Draft` while being reviewed, `Active` once agreed, `Retired` instead of deleting |
| Preconditions | `New account`, `Existing free`, `Existing premium`, or `Any` |
| Screens | the repo's screen doc names this case drives, comma separated |
| Experiment gate | the key and arm required, or blank when the surface is unconditional |
| Last run / Last result | written by `qa:run-test-cases` — never set these by hand |

**Automatable is not a wish.** Tick it only if `sim`, `backend` or `analytics` alone can
reach a verdict. A case that needs a purchase, a physical device, a person's judgement, or
permission to delete something is not automatable, however much you want it to be.

## Body

```markdown
## Preconditions
- Account shape, build, experiment arm, device state — everything true before step 1.

## Steps
| # | Action | Expected |
|---|---|---|
| 1 | Tap `home_voiceButton`. | The session connects. |
| 2 | … | … |

## Expected outcome
The one sentence that decides pass or fail.

## Data check
- Firebase / Postgres / Mixpanel / a named log line. Omit the whole section only when
  there is genuinely nothing to check outside the screen.

## Notes
- Why the case exists, known quirks, what it deliberately does not cover.
```

## Writing the Expected column

| Instead of | Write |
|---|---|
| "check it works" | "a non-empty assistant turn arrives and completes" |
| "the button is disabled" | "nothing advances; `state.screen` is unchanged — the tap reports success but the control is inert" |
| "the screen looks right" | "the full affirmation is visible, or scrollable to its end" |
| "audio plays" | "`audio_stopped` replaces `audio_playing` once narration finishes" |

## Notes worth writing

A Note earns its place when it stops the next runner making a mistake:

- A behaviour that **looks** like a bug and is not — a removed tutorial overlay, a screen
  correctly absent on a control arm.
- A trap that has already cost someone a run — a second tap on an already-granted voice
  button muting rather than connecting.
- A non-deterministic result and what to assert instead — never assert an exact transcript.
- A safety constraint — never tap the crisis call controls, never tap external links.
