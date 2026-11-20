# Idea schema — `aurie-web-onboarding-experimentation-ideas` field definitions

**Status:** stable for v1. Defines the shape of a generated experiment idea, the Difficulty + Priority classification rules, validation rules, and the body-stuffing template used while the `Learnings/Experiments` Notion DB schema is being extended.

This file is **DB-agnostic**. The canonical Notion schema for `Learnings/Experiments` lives in [`record-knowledge/references/schemas/experiments.md`](../../../../shared/skills/record-knowledge/references/schemas/experiments.md). This file documents what an *idea* looks like before it's written there.

## Why a separate idea schema

`Learnings/Experiments` is a post-mortem-shaped DB (Title / App / Platform / Status / Variants / Result / Learning). Generated ideas have 9 additional concepts the post-mortem schema doesn't carry as native properties:

| Concept | Purpose |
|---|---|
| `Target Screen` | The screen from `flow_map` the idea targets — anchors the idea to the live crawl. |
| `Target Metric` | `Activation Rate` / `Step Completion` / `Drop-off Reduction` / `Conversion` / `Retention D7` |
| `Current Copy / Component` | What the targeted screen says today (verbatim from crawl). |
| `Proposed Change` | Specific, implementable description — name the element, the new value, any conditional behavior. |
| `Priority` | `High` / `Medium` / `Low` — based on impact × ease. |
| `Difficulty` | `Easy` / `Medium` / `Hard` — auto-assigned per [Difficulty classification](#difficulty-classification). |
| `Prompt Used` | Full prompt text that generated the idea — for reproducibility / audit. |
| `Model Used` | Claude model identifier (e.g. `claude-sonnet-4-5-20250929`). |
| `Date Generated` | The date the idea was generated. |

These are serialised into the page body's `## What was tested` section using a greppable single-line `<key>: <value>` convention — see [Body-stuffing template](#body-stuffing-template) below.

**This is by design, not a workaround.** It lets the skill operate against the canonical `Learnings/Experiments` schema with no external coordination — `shared:record-knowledge` validates fine, no schema-extension PR required against another skill.

## Difficulty classification

Auto-assign per generated idea — never leave blank.

| Difficulty | When to assign | Examples |
|---|---|---|
| **Easy** | Copy / text / labels / button text / headlines / subheadlines / microcopy / simple style tweaks. No logic changes, no new components, no backend. | Rewrite CTA, change headline wording, swap button label, tweak microcopy under a form field |
| **Medium** | UI component changes, layout adjustments, reordering steps, adding/removing a form field, light conditional logic. No backend changes. | Add a progress bar, reorder onboarding steps, add an illustration, change input field type, add a skip button |
| **Hard** | New features, backend changes, API integrations, personalization logic, branching flows, new data pipelines, significant architectural changes. | Personalised onboarding paths based on segment, dynamic content from an API, new payment flow, A/B test infrastructure setup |

## Priority classification

Auto-assign per generated idea — never leave blank.

| Priority | When to assign |
|---|---|
| **High** | Targets a worst-drop-off screen from Step 3, OR cites a winning historical pattern (Step 5) that's directly applicable. Easy or Medium difficulty. |
| **Medium** | Targets a moderate drop-off, OR a tracking-gap screen, OR a Hard-difficulty idea that addresses a top-3 worst drop-off. |
| **Low** | Tests a hypothesis with weak grounding (no Mixpanel pain signal AND no historical winner), or a Hard-difficulty idea on a low-traffic screen. |

If two ideas are equally well-grounded, the one targeting an under-tested screen (Step 5 output) gets the higher Priority — expanding experimental coverage is a portfolio-level win.

## Pipeline phase ↔ `Learnings/Experiments` Status mapping

`Learnings/Experiments` uses a post-mortem Status enum (`Running` / `Won` / `Lost` / `Inconclusive` / `Turned off`, blank for not-yet-started). Ideation thinks in pipeline phases (`Idea` / `In Review` / `Running` / `Passed` / `Failed` / `Complete` / `Archived`).

This mapping converts between them — no new Status values are added to the Notion enum:

| Pipeline phase | Native `Status` value | Body marker (in `## What was tested`) |
|---|---|---|
| `Idea` | blank | `Phase: Idea` |
| `In Review` | blank | `Phase: In Review` |
| `Running` | `Running` | (no marker — Status is authoritative) |
| `Passed` | `Won` | (no marker — Status is authoritative) |
| `Failed` | `Lost` | (no marker — Status is authoritative) |
| `Complete` | `Inconclusive` | (no marker — Status is authoritative) |
| `Archived` | `Turned off` | (no marker — Status is authoritative) |

Read direction (Step 1): a row with `Status: blank` and a `Phase: <X>` body marker is in pipeline phase X. A row with no `Phase` marker but `Status: blank` is treated as `Idea` (default for legacy bulk-imported rows).

Write direction (Step 10):
- **Option 2 (new ideas):** always write `Status: blank` + `Phase: Idea` body marker.
- **Option 3 (status updates from Step 6):** write the terminal `Status` from `analyze-experiment` directly (`Won` / `Lost` / `Inconclusive` / `Turned off`). Body marker not needed once Status is set.

## Body-stuffing template

Every new idea-row gets this body in the `## What was tested` section. Markers are deliberately greppable — single-line `<key>: <value>` pairs, multi-line values prefixed with `|`. If the team ever decides to promote any of these to native columns, a one-time migration script can extract them — but the skill never depends on that happening.

```markdown
## What was tested
[PROPOSED — not yet built]
Phase: Idea
Experiment Type (aurie-web-onboarding-experimentation-ideas enum): A/B Copy
Target Screen: Step 4 — Lifestyle Quiz
Target Metric: Drop-off Reduction
Priority: High
Difficulty: Easy
Current Copy / Component: |
  Tell us about your lifestyle
Proposed Change: |
  Add a 1-line social proof above the question — "73% of users who answered honestly here found Aurie helpful"
Noom patterns cited: ["Pattern 3 — Social proof at decision points"]
Anti-pattern check: passed
Historical grounding: |
  Won — predictedGoalDateReveal (+14% trial start) shows social-proof framing works at decision points
Grounding source: funnel
```

`Grounding source` is one of `funnel` (target screen has live Mixpanel data per Step 4 `instrumentation_health.events_present`) or `history` (no live funnel — idea is grounded in Step 5 historical patterns + Noom priors only, with the headline `instrumentation_health` finding warning that quantitative readout requires instrumentation work first).

After Step 10 Option 2 writes the row, the rest of the body sections are stubs:

```markdown
## Result
_Pending — experiment not yet shipped._

## Learning
_Pending — to be added by analyze-experiment after experiment ends._

## Follow-up experiments
_To be added._

# Sources
- aurie-web-onboarding-experimentation-ideas run <run-id> (primary)
- Mixpanel — Aurie Web onboarding funnel: <funnel-url>
- Live crawl — <Target Screen>: outputs/flow-crawl/<run-id>/screen-<N>.png
- Prompt + model: model=<model>, prompt=<first-200-chars-of-prompt>
```

## Validation rules on create

These run in Step 8 before the Step 10 write. Failures cause the idea to be reframed or dropped — never written as-is.

1. `Title` (= `Experiment Name`) must be unique in `Learnings/Experiments` (case-insensitive). On collision: skip and log, never auto-rename.
2. `Title` must be valid camelCase, ≤ 40 chars, no spaces / hyphens / underscores. Matches the `Learnings/Experiments` `Title` convention.
3. `Hypothesis` must contain the words `If`, `then`, and `because` (the three-clause structure). Ideas missing any of the three are reframed before write.
4. `Target Screen` must match the `name` of a screen captured in the current run's `flow_map` (Step 2). Hallucinated screen names are blocked.
5. `Current Copy / Component` must be a verbatim quote from the live crawl — not paraphrased. If the screen has no extractable text (image-only), use `[image-only screen — see screenshot at <path>]` as a placeholder.
6. `Proposed Change` must be specific enough to implement: name the element, the new value, and any conditional behavior. Vague proposals like "make it more engaging" are reframed before write.
7. `Priority` and `Difficulty` are required and must come from the enum — never blank.
8. At least one Noom pattern must be cited from the `Noom Onboarding — UX Priors` Notion page (loaded in Step 0).
9. All 6 anti-patterns from `Noom Onboarding — UX Priors` (Notion) must be checked — anti-pattern violations cause reframing.
10. `Proposed Change` must not duplicate any entry in `flow_map[target_screen].shipped_features` (populated by `flow-extract.md` R12 from live source, or read from the Notion snapshot's per-screen YAML in Tier 3 fallback). Same-tag + same-mechanism overlaps are hard rejects (`reject_reason: already_shipped`); same-tag + different-mechanism overlaps must call out the differentiation in `Hypothesis` explicitly. Enforced in SKILL.md Step 7 Pass B.

## Idempotency

Re-running the skill must not create duplicate rows. Step 7 (dedup) does:

1. Case-insensitive `Title` match against existing `Learnings/Experiments` rows for Aurie Web.
2. Fuzzy `(Target Screen, Target Metric)` match against active rows (`Status: blank` or `Status: Running`) — flagged for user decision, not auto-dropped.
3. **Shipped-feature dedup** against `flow_map[target_screen].shipped_features`. Same-tag + same-mechanism is `reject_reason: already_shipped`; same-tag + different-mechanism is allowed only if the hypothesis names the differentiation. Conditional/experiment-gated shipped features are soft-shipped (only rejected when the proposed variant collides with the gating experiment).

See SKILL.md Step 7 for the exact rules.

## Optional future: native-property promotion

If the team ever decides to promote any of the 9 body-stuffed fields to native columns in `Learnings/Experiments` Notion DB, the upgrade path is:

1. Add the columns to the Notion DB (manual).
2. Update `record-knowledge/references/schemas/experiments.md` to declare them (owned by `record-knowledge`, not this skill).
3. Update this skill's `SKILL.md` Step 10 Option 2 to pass them as `fields` instead of stuffing into `body."## What was tested"`.
4. Run a one-time migration to extract the body markers from existing ideate-generated rows and set the new properties (markers are greppable for exactly this reason).
5. Update this skill's Step 1 to read native properties instead of body-parsing.

This is **purely optional**. The skill works as-is against the canonical schema today.
