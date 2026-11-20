# Flow Extract — Runtime Extraction + Maintainer Refresh Guide

This file has **two audiences**:

1. **The skill at runtime, in Step 2a** — when the GitHub Connector or a `git clone` succeeded in Step 1.5 and live AIWeb source is available, the skill follows the [Runtime extraction procedure](#runtime-extraction-procedure-when-live-source-is-available) below to derive `flow_map` directly from source.
2. **The maintainer, refreshing the Notion snapshot** — when the team needs to refresh the canonical Notion snapshot page (Tier 3 fallback used by Step 2b), follow the [Maintainer refresh procedure](#maintainer-refresh-procedure-for-the-notion-snapshot-page) further down.

Both procedures parse the same source files and produce the same `flow_map` shape. The only difference is *where the result lives* — runtime keeps it in memory; the maintainer pastes the regenerated markdown into the Notion page **`Aurie Web Signup Flow Static Snapshot`** (`35375e23af2080c581a8ebc7bb8c1af8`, knowledge path `Aurie Web Signup Flow — Static Snapshot`).

## Why a snapshot exists at all (Tier 3)

Even with live source as the primary channel, the Notion snapshot page is essential as the **fallback** for runs where:

- No GitHub Connector is installed in Cowork
- The Tier 2 `git clone` failed (network blocked, repo unreachable, `mcp__workspace__bash` unavailable, etc.)
- The user picked **Use bundled snapshot** at the Tier 2 prompt
- The user explicitly passed `skip-aiweb-sync`

Without the snapshot the skill would have to abort in any of those cases. With it, the skill always has *something* to work from — drift-warned, but functional.

The Aura Web experiment history that previously lived as a sibling markdown file is now sourced live from `Learnings/Experiments` (Notion DB `2cbe45a3246040658b9f20559b5b452b`) filtered `Platform == "Aura Web"` — see `SKILL.md` Step 5 Source 2.

## Runtime extraction procedure (when live source is available)

> **Followed by the skill itself in Step 2a, when `flow_source` starts with `live-github-…`.** Skip ahead to the Maintainer section if you're a human refreshing the snapshot.

The input to this procedure is whichever access mode won in Step 1.5:

| Mode | Where the source files live |
|---|---|
| `live-github-connector` | In the in-memory `live_files` map. Each value is the file's full text content. Parse via string operations — no further tool calls needed. |
| `live-github-clone` | On disk at `/tmp/aiweb/<path>`. Use `Read`/`Grep` against those paths. |

Treat the two modes interchangeably; the procedure below references files by their AIWeb-relative path (e.g. `src/lib/quiz-config.ts`) and the skill resolves to in-memory content or `/tmp/aiweb/<path>` based on mode.

### R1. Identify the active variant

The variant decision is **Notion-first**, with the source code as the verification + fallback. This way the skill audits whatever variant the team has actually shipped — not whatever variant a static code-pattern heuristic guesses at.

> **Naming convention — use literal source-code constant names everywhere.** The team's `Learnings/Experiments` Notion DB titles each onboarding-variant experiment using the exact `AB_EXPERIMENTS` constant value from `AIWeb/src/lib/constants.ts` (verified against the live DB; observed titles include `webOnboardingV3`, `webOnboardingVariant2`, `onboardingPromptResults`, `hardPaywall`, `twoStepPaywall`). **Do not** invent canonical labels like `v3-desktop` — match and store the literal Notion title. The same literal flows through every downstream step.

#### R1.a — Check Notion (primary signal)

Filter `experiment_state` (loaded in Step 1 of `SKILL.md`) for rows whose `Title` matches the **`webOnboarding*` literal pattern** that AIWeb uses for the onboarding-flow A/B family. Match rule (case-sensitive, anchored):

```regex
^webOnboarding[A-Z][A-Za-z0-9]*$
```

This catches the exact set of identifiers the team uses for *which onboarding flow shipped* — currently `webOnboardingV3`, `webOnboardingVariant2`, plus any future `webOnboardingV4` / `webOnboardingV5`. It deliberately excludes adjacent rows like `hardPaywall`, `onboardingResultsArchetype`, or `onboardingPromptResults` that affect downstream screens but aren't the flow-selection experiment.

> **Scoping note.** If the team renames the convention (e.g. `webSignupFlowVN`), update this regex and the R1.c lookup table together — they are the only two places R1 needs to know variant naming. The rest of the skill consumes whatever literal R1 emits.

For each matching row capture: `Title` (the literal variant name), `Status`, `End Date`, `Start Date`, `Hypothesis`, body `## What was tested`. Then classify by Status:

| Status | What it tells us about the active variant |
|---|---|
| `Running` | A variant is being actively tested right now. Capture in `running[]`. Does NOT make it the active variant. |
| `Won` | A variant won and was rolled out. The most recent Won row by `End Date` desc names the current production variant. |
| `Lost` / `Inconclusive` / `Turned off` | Historical context only — does NOT change which variant is active. |
| blank + body marker `Phase: Idea` / `In Review` | Proposed only; not yet shipped. Ignore for variant decision. |

**Tie-breaker for "latest Won":** sort matching `Won` rows by `End Date` desc; if `End Date` is missing or equal, fall back to `Start Date` desc; if still tied, prefer the row whose body explicitly says "rollout: 100%" or "default". The chosen row's literal `Title` becomes the active variant.

Build:

```yaml
onboarding_experiment_context:
  running:
    - title: <literal webOnboarding* row title>
      hypothesis: <one-line>
      started: <Start Date>
  won_and_default:
    variant_name: <literal Title from the chosen Won row>   # e.g. "webOnboardingV3"
    won_row_title: <same as variant_name>
    won_at: <End Date>
    rollout_marker: "<quote from body if present, else null>"
  inferred_active_variant: <literal Title> | null            # e.g. "webOnboardingV3"
  inference_basis: |
    <one-line traceability — e.g. "Notion: webOnboardingV3 — Won (End Date 2025-08-15), body says 'rollout: 100% default'">
```

If `inferred_active_variant` is set with a clear basis, proceed to R1.c (verify against source). If null, proceed to R1.b.

> **User override.** If the user invoked the skill with a `variant=<literal>` flag (tracked in `ENGWEB-216`), skip R1.a/b entirely and use that literal as the variant. Still run R1.c to verify the override has a corresponding source export.

#### R1.b — Source heuristic (fallback when Notion is silent)

If Notion has no `webOnboarding*` signal for an active variant, read `src/pages/signup/index.tsx`. Find `handleGetStarted` and the `chooseExperiments` call:

```ts
const result = chooseExperiments(allExperiments, [
  AB_EXPERIMENTS.WEB_ONBOARDING_V3,
]);
const v3 = result[AB_EXPERIMENTS.WEB_ONBOARDING_V3];
setIsV3Onboarding(v3 === 'a');
setIsMobileOnboarding(false);
```

The constant `AB_EXPERIMENTS.WEB_ONBOARDING_V3` resolves to the literal `'webOnboardingV3'` (per `src/lib/constants.ts` line 43). If `v3 === 'a'` is the treatment branch and the file routes treatment users into `cOnboardingQuizConfig`, set:

- `inferred_active_variant = "webOnboardingV3"`
- `inference_basis = "Source heuristic: chooseExperiments(WEB_ONBOARDING_V3 → 'webOnboardingV3') routes treatment 'a' to cOnboardingQuizConfig; no Notion signal"`

If the source heuristic also can't determine a variant (the bucketing pattern has changed or the constant has been removed), fall back to `webOnboardingV3` with a `no_signal` warning — this matches what's currently shipped per the Notion snapshot.

#### R1.c — Cross-check Notion's pick against source

Before finalising `variant = inferred_active_variant`, confirm the literal name maps to a `*OnboardingQuizConfig` export in `src/lib/quiz-config.ts` and to the corresponding `quiz.tsx` ternary branch (lines 108–112):

```ts
const quizConfig = isV3Onboarding
  ? cOnboardingQuizConfig
  : isMobileOnboarding === true
    ? mobileOnboardingQuizConfig
    : onboardingQuizConfig;
```

| Notion / source literal | `AB_EXPERIMENTS` constant | Source export | Quiz step count (verified by `awk` over source) | Active when |
|---|---|---|---|---|
| `webOnboardingV3` | `WEB_ONBOARDING_V3` | `cOnboardingQuizConfig` (line 751) | 33 | `store.isV3Onboarding === true` |
| `webOnboardingVariant2` | `WEB_ONBOARDING_VARIANT` | `onboardingQuizConfig` (line 78) | 19 | else branch, desktop UA |
| (no Notion row — UA-detected) | (none) | `mobileOnboardingQuizConfig` (line ~301) | (re-derive at runtime) | `store.isMobileOnboarding === true` |
| Future e.g. `webOnboardingV4` | TBD | TBD `*OnboardingQuizConfig` export | TBD | TBD |

> Re-derive the step counts at runtime from source rather than trusting the table — the count column is for human reference only. Snippet (Tier 2 clone): `awk '/^export const cOnboardingQuizConfig/,/^export const cSections/' src/lib/quiz-config.ts | grep -cE "^      id: '"`.

Three outcomes:

| Outcome | Action |
|---|---|
| Literal ↔ source export both exist | Proceed. Set `variant = inferred_active_variant`. Record the matched export in `variant_decision.source_export`. |
| Notion names a literal with no source export | Surface a strong warning (template below), then **fall back** to the source heuristic so the run still produces output. |
| Source has multiple variant exports but Notion only names one | Walk only the chosen one. Record the others as `source_variants_present_but_not_audited`. |

Mismatch warning template:

```
⚠️ Variant mismatch detected:
   Notion says active variant is "webOnboardingV4"
     basis: row "webOnboardingV4 — Won (End Date 2026-04-15)"
   AIWeb source has no matching *OnboardingQuizConfig export at src/lib/quiz-config.ts.
   Likely causes:
     - Repo is stale — try running with aiweb-ref=main and a fresh clone
     - The variant rolled out via backend/config service, not new source
     - The team adopted a new naming convention (update R1.a regex + R1.c table)
   Falling back to webOnboardingV3 (current source default). Verify with the team before publishing ideas.
```

#### R1.d — Multi-variant note

If `onboarding_experiment_context.running` is non-empty (a variant is being A/B tested right now), audit ONLY the literal Notion marks as Won-and-default in this run. Surface the running experiment(s) in the preview but do not audit them — multi-variant concurrent audit is tracked in `ENGWEB-216` for a future `variant=<literal>` flag enabling explicit per-variant audits.

Set the final variant decision and record it for Step 8 preview / Step 9 Notion sources / Step 10 final report:

```yaml
variant_decision:
  variant: <literal>                                # e.g. "webOnboardingV3"
  source_export: <export name>                      # e.g. "cOnboardingQuizConfig"
  active_when: <store flag check>                   # e.g. "store.isV3Onboarding === true"
  source_step_count: <int derived at runtime>       # e.g. 33 for V3
  basis: notion | source-heuristic | no-signal-fallback | user-override
  basis_detail: <one-line explanation, see inference_basis>
  notion_running_variants: [<literal>, ...]         # empty unless something is being A/B tested
  source_variants_present_but_not_audited:
    - { literal: "webOnboardingVariant2", source_export: "onboardingQuizConfig" }
    - { literal: "(mobile-ua)",          source_export: "mobileOnboardingQuizConfig" }
```

### R2. Pull the route map

Read `src/lib/constants.ts`. Locate the `ROUTES` object. Extract every key whose value is a path under `/signup`, `/subscribe`, or `/get-app`. For each route, capture `(route_key, url_path)`.

For confirmed happy-path order, also read the top-of-file comment in `e2e/signup-onboarding.spec.ts` — it lists the verified route order from the Playwright suite.

### R3. Pull the section structure

Read `src/lib/quiz-config.ts`. Locate `cSections` (around line 1285 in the snapshot's reference; if line numbers have shifted, search for `export const cSections`). Extract `(section_id, section_label, member_question_ids)` for each section.

### R4. Pull each quiz screen

Still in `src/lib/quiz-config.ts`, locate `cOnboardingQuizConfig` (around lines 751–1273 in the snapshot's reference; search for `cOnboardingQuizConfig` if shifted). Iterate over `cOnboardingQuizConfig.questions`. For each `QuizQuestion` object, extract:

| Field | Where in the QuizQuestion |
|---|---|
| `screen_id` | `id` property |
| `screen_name` | derive from `id` + `title` (use the `title` if present, else humanize the `id`) |
| `headline` | `title` |
| `subheadline` | `subtitle` (if present) |
| `route_kind` | `type` property — typically `"single"`, `"multi"`, `"slider"`, `"interstitial"`, etc. |
| `options` | `options[].label` (if `options` is present); for sliders, `sliderRange.min/max/labels` |
| `cta_primary_text` | usually fixed by the quiz host (`Continue`, `Next`); pull from the host page if not on the question itself |
| `mixpanel_screen` | look up `id` in `QUIZ_QUESTION_TO_SCREEN` from `src/services/onboardingAnalytics.ts` (see R6) |
| `interstitial_kind` | `interstitial.kind` (if present — narrative/testimonial/etc.) |
| `branching` | any `branching` rules (if present) |

Assign `screen_index` by enumeration order, starting at the index after the landing screen (which is index 0).

### R5. Pull each non-quiz screen (variant-conditional)

The post-quiz path **branches by variant**. Don't blindly include every page in the table below — first read the routing logic, then pull only the screens the chosen `variant_decision.variant` actually traverses. Tag every screen with the literal variants that reach it so Step 2.7's audit walk can filter correctly.

#### R5.0 — Read the post-quiz branch logic

The decisive code lives in `AIWeb/src/components/onboarding/results-v3/ResultsV3Page.tsx` lines 61–68:

```ts
// V3 + mobile-onboarding go straight to /subscribe; legacy long-web stops at /signup/goal.
const handleStart = useCallback(() => {
  if (store.isV3Onboarding || store.isMobileOnboarding) {
    router.push(ROUTES.SUBSCRIPTION);
  } else {
    router.push(ROUTES.SIGNUP_GOAL);
  }
}, [router, store.isMobileOnboarding, store.isV3Onboarding]);
```

Cross-reference with `src/pages/signup/quiz.tsx` line 530–542 for the save-modal path (also V3-aware).

#### R5.1 — Per-variant post-quiz screen sets

| Variant literal | Post-quiz path (in order) |
|---|---|
| `webOnboardingV3` | Save Progress Modal → `/loading` (inline in quiz.tsx) → Results V3 → `/subscribe` → `/get-app` |
| `webOnboardingVariant2` (legacy) | Save Progress Modal → `/loading` → `/signup/results` → `/signup/goal` → `/signup/choose-fee` → `/subscribe` → `/get-app` |
| `(mobile-ua)` | Save Progress Modal → `/loading` → Results (mobile) → `/subscribe` → `/get-app` (skips goal + choose-fee, same as V3) |

#### R5.2 — Per-screen extraction with `applicable_variants`

For every non-quiz screen pulled, set the `applicable_variants` field listing which literals reach it. The audit (Step 2.7 Pass 1) filters by membership:

| File | Screen | applicable_variants |
|---|---|---|
| `src/pages/signup/index.tsx` | Landing | `[webOnboardingV3, webOnboardingVariant2, (mobile-ua)]` |
| `src/pages/signup/quiz.tsx` (modal block) | Save Progress Modal | `[webOnboardingV3, webOnboardingVariant2, (mobile-ua)]` |
| `src/pages/signup/quiz.tsx` (loading phase) | Loading / Aurie Thinking | `[webOnboardingV3, webOnboardingVariant2, (mobile-ua)]` |
| `src/components/onboarding/results-v3/ResultsV3Page.tsx` | Results V3 | `[webOnboardingV3]` |
| `src/pages/signup/results.tsx` | Results (legacy / mobile) | `[webOnboardingVariant2, (mobile-ua)]` |
| `src/pages/signup/goal.tsx` | Goal — What this looks like for you | `[webOnboardingVariant2]` only — V3 + mobile skip per `ResultsV3Page.tsx` line 63 |
| `src/pages/signup/choose-fee.tsx` | Choose Trial Fee | `[webOnboardingVariant2]` only — V3 + mobile skip |
| `src/pages/subscribe/index.tsx` | Subscribe (paywall) | `[webOnboardingV3, webOnboardingVariant2, (mobile-ua)]` |
| `src/pages/get-app/index.tsx` | Get App | `[webOnboardingV3, webOnboardingVariant2, (mobile-ua)]` |

For each chosen file extract: H1, sub-copy, primary CTA text, components present (testimonials, value props, plan tables, etc.), and grep for any `track*Screen*` / `trackEvent` call that fires on mount → `mixpanel_event`.

> **Why tag instead of strip:** keeping every screen in `flow_map` with explicit `applicable_variants` makes the same `flow_map` audit-able for any variant by just changing the filter, and means future V4 audits don't require re-extracting unchanged screens.

#### R5.3 — Skip rule

When materialising `flow_map` for the chosen variant, only include screens whose `applicable_variants` contains `variant_decision.variant`. Quiz screens (R4) inherit `applicable_variants = [variant_decision.variant]` automatically because they came from the variant's own `*OnboardingQuizConfig` export.

### R6. Pull the Mixpanel lexicon

Read `src/services/onboardingAnalytics.ts`. Extract:

- The full `EVENTS` const (event-name string constants).
- The `OnboardingScreenName` enum (canonical screen-property values).
- Both `QUIZ_QUESTION_TO_SCREEN` and `MOBILE_QUIZ_QUESTION_TO_SCREEN` maps.
- The list of `track*` functions (note which event each fires and any breakdown property).

Build an in-memory lookup so R4 and R5 can resolve `mixpanel_event` for each screen.

### R7. Compute per-screen Mixpanel resolution

For each quiz screen from R4:

```
mixpanel_event = QUIZ_QUESTION_TO_SCREEN[questionId]
  ? { event: "AI Sign Up Screen Completed",
      breakdown: { Screen: QUIZ_QUESTION_TO_SCREEN[questionId] } }
  : null
```

For each non-quiz screen from R5: use the `track*` call you grepped — that names the event explicitly (e.g. `trackResultsScreenViewed` → `"AI Results Screen Viewed"`).

### R8. Compute tracking gaps and mapping mismatches

- **Tracking gap (`events_unmapped`):** any `QuizQuestion.id` from R4 that is **not** a key in `QUIZ_QUESTION_TO_SCREEN` (i.e. `mixpanel_event == null`). List as `events_unmapped`.
- **Mapping mismatch:** for each quiz `id` that *is* in `QUIZ_QUESTION_TO_SCREEN`, check that the mapped `Screen` value semantically matches the question's `title` / `subtitle`. Cross-reference against the `OnboardingScreenName` enum.
  - Example mismatch from the V3 snapshot: question `id: "primary_struggle"` (V3) maps to enum value `"Primary Struggle Screen"` even though the V3 question copy is "What's been weighing on you most?" — the enum name was inherited from the legacy variant.
  - Note these in the per-screen entry's `notes:` and aggregate at the bottom.

### R9. Annotate with V3 spec rationale

Read `docs/web-onboarding-v3-spec.md`. For each screen in the `flow_map`, look for a section that names the screen (or the section it belongs to). If the spec calls out drop-off concerns ("first known drop-off point"), narrative arc ("Empathy → Promise → Mechanism → Specificity → Personalization → Proof → Reassurance → Outcome"), or instrumentation gaps, fold that into the screen's `notes:` block.

This is what makes runtime ideation smarter: when an idea is generated for a screen, the skill can cite the spec rationale ("the spec calls this 'first known drop-off point'") rather than guessing.

### R10. Materialise the in-memory flow_map

Produce a list of dicts/objects (one per screen) with this shape:

```yaml
- screen_index: <int>
  screen_id: <string>
  screen_name: <string>
  url_path: <string>
  route_kind: <"landing"|"quiz"|"interstitial"|"results"|"goal"|"choose-fee"|"paywall"|"get-app"|"modal">
  applicable_variants: [<literal>, ...]   # which variants reach this screen (R5.2 / R4 inheritance)
  headline: <string>
  subheadline: <string|null>
  options: [...]                          # for selectable screens
  cta_primary_text: <string|null>
  components_present: [...]
  mixpanel_event: <object|null>           # { event, breakdown? } per R7
  notes: <string|null>
  shipped_features:                       # populated by R12 — empty list = nothing to dedup against
    - tag: <one of the 12 taxonomy tags from R12>
      mechanism: <one-line description of HOW the feature works at runtime>
      source: <file:line — must point at concrete AIWeb source>
      inputs: [<quiz_answer_id | user_field | AB_EXPERIMENTS.*>, ...]
```

**Filter before handoff.** Apply the R5.3 skip rule: `flow_map = [s for s in flow_map if variant_decision.variant in s.applicable_variants]`. The downstream Steps 2.5 / 3 / 6 consume only this filtered list.

(Optional: keep the unfiltered `full_flow_map` in memory for cross-variant comparisons in future audits.)

### R11. Sanity checks (runtime)

Before handing off to Step 2.5/3, validate the **filtered** `flow_map` against per-variant expectations:

| Variant | Expected screen count (after R5.3 filter) | Composition |
|---|---|---|
| `webOnboardingV3` | ~39 (33 quiz steps + 6 outer: Landing, Save Modal, Loading, Results V3, Subscribe, Get App) | No `/signup/goal`, no `/signup/choose-fee` |
| `webOnboardingVariant2` | ~26 (19 quiz steps + 7 outer: Landing, Save Modal, Loading, Results, Goal, Choose Fee, Subscribe, Get App) | All legacy post-quiz screens included |
| `(mobile-ua)` | (re-derive at runtime — depends on `mobileOnboardingQuizConfig` step count) | Skips Goal + Choose Fee like V3 |

Concrete checks (variant-aware):

- [ ] `len(flow_map_quiz_screens) == chosenConfig.questions.length` — derived dynamically from the chosen `*OnboardingQuizConfig.questions` array, NOT a hardcoded constant
- [ ] For `webOnboardingV3` (or `(mobile-ua)`): no screen with `url_path` in `["/signup/goal", "/signup/choose-fee"]` survives the R5.3 filter
- [ ] For `webOnboardingVariant2`: both `/signup/goal` AND `/signup/choose-fee` are present
- [ ] Every screen with `mixpanel_event: null` is also in `events_unmapped`
- [ ] `screen_index` is contiguous from 0 (re-number after filter)
- [ ] At least one screen has `route_kind: "landing"` and one has `route_kind: "paywall"`
- [ ] Every screen has a `shipped_features:` field (may be `[]`). Screens missing the field are an R12 bug; rebuild that screen's annotation rather than handing off an undefined value.

If any check fails, log the failure, fall back to the snapshot for the `flow_map` (then re-apply the R5.3 filter against snapshot's `applicable_variants` tags), and surface a warning. The runtime extraction is best-effort; the tagged snapshot is the safety net.

### R12. Annotate shipped_features per screen

For every screen in the `flow_map`, attach a `shipped_features:` list describing the UX patterns already shipped on that screen — anything an ideation skill might mistakenly propose as a "new idea". The dedup logic in `SKILL.md` Step 7 walks this list to drop ideas whose `proposed_change` overlaps any tag here.

#### Taxonomy (12 tags — these are the only legal values)

| tag | covers | typical detection signal |
|---|---|---|
| `personalization` | Dynamic copy from user/answers (rule-based or AI/LLM): name interpolation, quiz-driven phrasing, conditional copy variants, AI-generated paragraphs | `${`-style template literals reading user state; functions named `personalize*` / `getPersonalized*`; calls to `useUser` / `useAnswers` whose return values reach JSX text; LLM/Claude/GPT API calls |
| `social_proof` | Testimonials, user counts, ratings, award badges, press logos | `Testimonial*`, `AuraAwards*`, hard-coded "Nx users" / "5 Stars" strings, badge components |
| `trust_signals` | HIPAA / clinical / scientific validation, secure-payment badges, "built by therapists" framing, evidence-informed claims | `CredentialsCard`, `FeaturesList` with therapist/Yale/UCLA strings, `secure`/`encrypted`/`HIPAA` literals |
| `risk_reversal` | Free-trial framing, money-back guarantee, cancel-anytime copy, no-commitment language, refund disclosures | strings matching `free trial` / `money-back` / `cancel anytime` / `no commitment` / `refund` / `guarantee`; `TrialStepsCard` |
| `urgency_scarcity` | Countdowns, limited-time offers, deadline pricing, expiring-discount copy | timer components (anything ticking against `Date.now`), strings matching `expires` / `today only` / `limited` |
| `pricing_patterns` | Multi-tier plans, struck-through anchors, monthly/annual toggle, choose-your-fee, intro pricing, popular-option badges | `PlanPicker`, `popular: true` flags in config, struck-through `<s>` / `line-through` styles on prices, `displayTotalDueToday` |
| `progress_indicators` | Segmented progress bars, step counters, section labels, completion checkmarks | `progressBar` / `Progress*` components, `currentStep` / `step / total` rendering, section labels from `cSections` |
| `conditional_branching` | Softgates, age/severity gating, exit-intent modals, conditional CTAs, experiment-driven variants, `skippable: true` questions | `branching.optionJumpTargetIds`, `useExperiment` driving copy/route, `skippable: true` in quiz config, exit-intent listeners |
| `transition_copy` | Loader copy, interstitial educational screens, value-delivery framing during waits, operational transparency | `intermediate_*` quiz-config entries, `BuildingPlanContent`, `LoopCard` / `BeforeAfterBarGraphCard` / `AurieImpactGraphCard` |
| `safety_guardrails` | Crisis-detection messaging, severity escalation copy, hotline numbers, safety-net CTAs | strings matching `988` / `crisis` / `hotline` / `emergency`, `safety` toggles in FAQ/comparison content |
| `save_progress` | Email-capture mid-flow, save-your-spot modals, abandoned-flow recovery, progress-resume URLs | `SaveProgressModal`, `saveProgress` actions, magic-link / resume-flow URLs |
| `post_signup_followup` | Confirmation screens, app-download CTAs (QR, store badges, deep links), email/SMS handoff copy | `get-app/*`, App Store / Play Store URL constants, QR-code components, `trackGetAppScreenSeen` |

#### Procedure

For each `screen` in the (already-built) `flow_map`:

1. **Resolve the screen's source files.** Start from `screen.components_present` (if present) and the file/line refs collected in R4/R5. For quiz screens, also include `src/lib/quiz-config.ts` plus any per-screen interstitial component referenced in `signup/quiz.tsx`.

2. **Walk the 12 detection signals (table above).** For every match, emit one entry under `screen.shipped_features` with:
   - `tag`: the matching taxonomy tag.
   - `mechanism`: one-line description of how the feature manifests at runtime. Be specific enough that an ideator can tell whether their `proposed_change` overlaps. **Examples of good mechanism text:**
     - `Rule-based pattern selection from q6 + q8 -> patternDisplay (deterministic, NOT an LLM call)`
     - `popular:true badge anchored on '15 min' option (commitment-anchor pattern)`
     - `'Cancel anytime' + '30 days money-back guarantee' legal copy under the form`
   - `source`: `file:line-line` format. Single line ranges are fine; multi-file features get comma-separated refs.
   - `inputs`: list of quiz answer ids, user fields, or `AB_EXPERIMENTS.*` constants that drive the feature. Empty list `[]` is valid (means the feature is static / not user-driven).

3. **Distinguish rule-based vs LLM personalization.** If a screen has dynamic copy that comes from a deterministic mapping (no LLM call), tag `personalization` and call out `(deterministic — NOT an LLM call)` in `mechanism`. This matters because an idea proposing `add LLM-generated paragraph` does NOT overlap with shipped rule-based personalization, but `add quiz-driven paragraph` does.

4. **Conditionally-shipped features.** If a feature only renders under a specific experiment bucket (e.g. `WEB_LANDING_PAGE_FOOTER`, `PRICING_FAQS`), still emit the entry but list the experiment constant in `inputs:`. Step 7 dedup treats these as "soft-shipped" — proposals to ideate around them are allowed *as long as the variant being tested doesn't already include the experiment*.

5. **Empty list is valid.** Static screens with no shipped UX patterns (e.g. legacy routes that V3 skips, or a quiz question whose only feature is the universal progress bar — covered separately) get `shipped_features: []`. Don't fabricate entries to fill the field.

6. **Don't over-include.** A screen having "a button" or "some copy" is not a shipped feature. Feature tags are about UX *patterns* an ideator might re-propose — primary CTAs that aren't doing anything pattern-y don't earn an entry.

7. **Cross-cutting features.** Some patterns overlay multiple screens (e.g. the save-progress modal, experiment bucketing, analytics tracking). Model these as their own pseudo-screens (`screen_id: modal:save-progress`, etc.) rather than duplicating across every screen they overlay.

#### Output validation

Before handoff:
- Every `shipped_features[*].tag` must be one of the 12 taxonomy values above. Unknown tags are a bug.
- Every entry must have a non-empty `source:` pointing at concrete AIWeb code. If you can't cite a file:line, drop the entry — don't speculate.
- The Notion snapshot page (`Aurie Web Signup Flow — Static Snapshot`) carries the same `shipped_features:` field per screen. When live extraction runs (Tier 1/2), compare the runtime `shipped_features` with the snapshot's — drift here is informative even when other fields haven't drifted.

## Maintainer refresh procedure (for the Notion snapshot page)

> **Audience:** the maintainer of this skill, running locally with the `AIWeb/` repo on disk. The runtime extraction above does *not* call into this section.
>
> The maintainer procedure produces the same `flow_map` as the runtime procedure, but writes the result to the Notion page **`Aurie Web Signup Flow Static Snapshot`** (`35375e23af2080c581a8ebc7bb8c1af8`) as the team-wide canonical snapshot used by Tier 3 (Step 2b).

### When to refresh

Refresh the Notion snapshot page when **any** of these change in the AIWeb repo since the page's `Captured:` date:

| File | Why it matters |
|---|---|
| `AIWeb/src/lib/quiz-config.ts` | Question IDs, copy, options, branching all live here. Most signup ships touch this file. |
| `AIWeb/src/services/onboardingAnalytics.ts` | The `EVENTS` const + `QUIZ_QUESTION_TO_SCREEN` / `MOBILE_QUIZ_QUESTION_TO_SCREEN` maps. Adding a new event or fixing a mapping mismatch requires a refresh. |
| `AIWeb/src/lib/constants.ts` (`ROUTES`) | Adding a new signup route invalidates the route map. |
| `AIWeb/src/pages/signup/**/*.tsx` | New pages or copy changes. |
| `AIWeb/src/pages/subscribe/index.tsx`, `get-app/index.tsx` | Paywall + confirmation changes. |
| `AIWeb/docs/web-onboarding-v3-spec.md` | Authoritative narrative — spec changes flag intent shifts even when code hasn't yet caught up. |

A **suggested cadence** is monthly when live AIWeb access is configured (Tier 1 or Tier 2 in Step 1.5 of `SKILL.md`) — the snapshot is then mostly a fallback. **Weekly** when live access isn't configured and the snapshot is the primary source. Or after any onboarding-touching ship — whichever is sooner. The skill's runtime preview prints the snapshot's `Captured:` date so the user can see staleness at a glance.

### Refresh procedure (steps)

Run from a host with the `AIWeb/` repo on disk (i.e. **not** from inside Cowork). Steps:

#### 1. Confirm the active variants

Check `AIWeb/src/pages/signup/index.tsx` `handleGetStarted` to see which variants are bucketed:

```ts
const result = chooseExperiments(allExperiments, [
  AB_EXPERIMENTS.WEB_ONBOARDING_V3,
]);
const v3 = result[AB_EXPERIMENTS.WEB_ONBOARDING_V3];
setIsV3Onboarding(v3 === 'a');
setIsMobileOnboarding(false);
```

If V3 is still the experiment with `'a' = treatment`, the **V3 desktop variant is the default** for users in the treatment bucket — the snapshot should capture this (and only this) variant unless you're explicitly capturing the legacy or mobile variant.

If the experiment naming or the default selection has changed, update the variants section of the Notion page accordingly.

#### 2. Read each source file in order

For each source file, extract the data into the corresponding section of the regenerated markdown blob (which you'll paste back into the Notion page in step 5). Use the file:line references in the page's metadata header so you can cross-check what's been pulled.

| Section in the Notion page | Read from | What to extract |
|---|---|---|
| Route map | `AIWeb/src/lib/constants.ts` `ROUTES` + `AIWeb/e2e/signup-onboarding.spec.ts` top comment | The path constants and the e2e-confirmed happy-path order |
| V3 Quiz section structure | `AIWeb/src/lib/quiz-config.ts` `cSections` (~ line 1285) | Section IDs, labels, and which question IDs roll up to each section |
| flow_map (one entry per screen) | `AIWeb/src/lib/quiz-config.ts` `cOnboardingQuizConfig.questions` (line 751 onwards) + `AIWeb/src/pages/signup/*.tsx` for routes outside the quiz | For each `QuizQuestion`: `id`, `title`, `subtitle`, `screen`, `type`, `options[].label`, `interstitial.kind`, `branching`, `sliderRange`. For routes: H1, H2, body copy, CTA text, components rendered. |
| Mixpanel event lexicon | `AIWeb/src/services/onboardingAnalytics.ts` | `EVENTS` const, `OnboardingScreenName` enum, both `QUIZ_QUESTION_TO_SCREEN` maps, and every `track*` function (note where each fires) |
| Tracking gaps | Computed: every `QuizQuestion.id` that's not in `QUIZ_QUESTION_TO_SCREEN` | List as `events_unmapped` for that variant |
| Mapping mismatches | Computed: V3 questions that have a mapping but where the mapped `Screen` value doesn't semantically match the question | Cross-reference V3 question titles against the `OnboardingScreenName` enum |
| Variants summary | All three configs (`cOnboardingQuizConfig`, `onboardingQuizConfig`, `mobileOnboardingQuizConfig`) | At minimum list which variants exist; capture full snapshots for any non-default variant if the team activates them |

#### 3. Computing the per-screen Mixpanel resolution

For each quiz step:

```
mixpanel_event = QUIZ_QUESTION_TO_SCREEN[questionId]
  ? { event: "AI Sign Up Screen Completed",
      breakdown: { Screen: QUIZ_QUESTION_TO_SCREEN[questionId] } }
  : null
```

For non-quiz screens (landing, results, goal, choose-fee, subscribe, get-app), grep the corresponding page file for `track*Screen*` calls — those name the event explicitly.

#### 4. Annotate every tracking gap with intent

Per the V3 spec doc, some tracking gaps matter more than others (e.g. `intermediate_testimonial_1` is documented as "first known drop-off point"). When you find a gap, look at the V3 spec doc for any rationale that explains why instrumenting it is high-value, and include that in the screen entry's `notes:` block. This makes the runtime ideation step smarter (it can reference the spec rationale when proposing instrumentation experiments).

#### 5. Paste into the Notion page + update the metadata header

Open the Notion page **`Aurie Web Signup Flow Static Snapshot`** (`35375e23af2080c581a8ebc7bb8c1af8`) and replace the body content with the regenerated markdown. At the top of the page, update:

- `Captured:` → today's date
- `Source repo:` → if you're capturing from a worktree or a specific SHA, note it
- `Variant captured:` → confirm V3 desktop (or update if you're capturing a different variant)
- `Source files used:` → the line ranges you actually read

No commit / no PR / no zip rebuild is needed — the next skill run picks up the new content automatically via the Step 0 Notion fetch.

#### 6. Sanity checks before committing

Before finalising the refresh, verify:

- [ ] The total screen count in `flow_map` matches `cOnboardingQuizConfig.questions.length` + post-quiz routes (landing, loading-phase, save-modal, results, goal, choose-fee, subscribe, get-app)
- [ ] Every screen with `mixpanel_event: null` is also listed in the "V3-specific tracking gaps" table
- [ ] Every screen with `notes: "Mapping mismatch"` is listed in the "Mapping mismatches" table
- [ ] No `Target Screen` referenced by an in-Notion `Learnings/Experiments` row for `Aurie Web` is missing from the `flow_map` — would silently break dedup. Pull current rows and cross-check.
- [ ] Section IDs match between the section structure section and per-screen entries
- [ ] CTA texts, options, and headlines are verbatim from the source — no paraphrasing
- [ ] V3 spec doc references are still accurate (sections renumbered? new narrative arc?)

#### 7. Optional: capture legacy + mobile variants

The skill's primary surface is V3 desktop, so the default snapshot only includes that variant. If the team needs ideation for legacy or mobile:

- Add a separate section to the Notion page with a sub-heading like `## Legacy desktop variant flow_map` or `## Mobile web variant flow_map`
- Use the same per-screen YAML shape
- Update the variants summary table at the bottom to mark them as captured

The skill can then be invoked with a `variant=legacy` or `variant=mobile` flag (defined in `SKILL.md`) to switch which section it reads.

## File structure recap

```
AINative/plugins/marketing/skills/aurie-web-onboarding-experimentation-ideas/
├── SKILL.md                                # the skill itself (frontmatter declares Notion knowledge dependencies)
├── README.md                               # human-facing docs (incl. Live AIWeb access setup)
└── references/
    ├── flow-extract.md                     # ← THIS FILE — runtime extraction + maintainer refresh guide
    └── idea-schema.md                      # static — defines the generated-idea shape

# The canonical flow_map snapshot lives in Notion (page 35375e23af2080c581a8ebc7bb8c1af8,
# knowledge path Aurie Web Signup Flow — Static Snapshot), not as a local markdown file.
# The Noom UX priors live in Notion (page 35375e23af2080f8b297c912a3ca6ccf,
# knowledge path Noom Onboarding — UX Priors), not as a local markdown file.
# The historical Aura Web experiments are pulled live from Learnings/Experiments
# (DB 2cbe45a3246040658b9f20559b5b452b) filtered Platform == "Aura Web" — see SKILL.md Step 5.
```

## What this skill does NOT do at runtime

To be explicit:

- The skill **does not crawl `https://www.aurie.ai/signup`** with a real browser (no Chromium in Cowork). SSR pages are spot-checked via `curl` in Step 2.5; that's it for live HTTP.
- The skill **does not call the live Mixpanel API for the lexicon** — the lexicon is derived from AIWeb source (live or snapshot), not queried from Mixpanel. Funnel data *is* pulled live via the Mixpanel MCP in Step 3, when available.

The skill **does** read AIWeb source at runtime when configured (Step 1.5) via the GitHub Connector or a `git clone`. The Notion snapshot page is the Tier 3 fallback.

## Why this design beats alternatives

| Alternative considered | Why we didn't pick it |
|---|---|
| Crawl live in Cowork with Playwright | Chromium install too large for 45 s bash cap; flaky on cold sandboxes; the AIWeb source contains everything the crawl would observe and more (component tree, Mixpanel mapping, branching logic) |
| Snapshot-only — no live source ever | Snapshot drifts, requires manual maintainer refresh, no recourse when copy ships and the maintainer is offline. Live source as primary + snapshot as fallback gives both freshness and reliability. |
| Bundle the AIWeb source files into the skill folder | Massive (~thousands of files); needs to be re-bundled on every AIWeb change; legal/IP concern with shipping internal source in a portable skill |
| Read AIWeb at runtime via Notion / Slack mirror | Adds dependencies; lexicon and copy can drift between mirror and source; same staleness risk as a snapshot but with extra plumbing |
| Auto-refresh snapshot on a CI cron only | Reasonable for Tier 3 freshness, but doesn't replace runtime live source — you still want the option to read against an unmerged branch via `aiweb-ref=<branch>`. CI auto-refresh is a *complement* to Step 1.5, not a substitute. |

The 3-tier strategy in Step 1.5 (Connector → clone → snapshot) is the simplest path that's correct in all the operational states the team actually hits: connector-installed, token-only, and offline. Keep it that way.
