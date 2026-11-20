---
name: aurie-web-onboarding-experimentation-ideas
description: >
  Generate fresh A/B experiment ideas for the Aurie Web (a.k.a. AIWeb / AI Web)
  onboarding/paywall flow. Top-level orchestrator that composes three
  sub-skills: aurie-web-flow-snapshot (live source → flow_map),
  aurie-web-onboarding-audit (Mixpanel funnel + per-screen friction analysis),
  and aurie-web-experiment-history (dedup baseline + Aura Web pattern priors).
  Layers Noom UX priors on top (passed through from the audit sub-skill — the
  audit grades every screen against the 7 patterns + 6 anti-patterns), drops
  any idea that duplicates a feature already shipped on the target screen
  (per-screen shipped_features map from the flow snapshot), runs two-pass
  dedup against prior experiments, and writes 5–8 ideas (hypothesis, target
  screen, metric, priority, difficulty) to Learnings/Experiments via the
  Notion MCP. Trigger when the user mentions experiment ideation for
  Aurie Web / AIWeb / AI Web onboarding.
metadata:
  version: "3.0.5"
knowledge:
  reads:
    - Guide/Index                               # baseline routing aid (universal)
    - Products/Aurie                       # brand voice priors used by Step 3 hypothesis copy
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
    - Tools/Mixpanel                 # Mixpanel service-account / MCP access
  writes:
    - Learnings/Experiments                     # new idea rows (Step 6) — DB 2cbe45a3246040658b9f20559b5b452b
---

# aurie-web-onboarding-experimentation-ideas

A self-contained Claude Skill for generating onboarding experiment ideas for the **Aurie Web onboarding/paywall surface** (`https://www.aurie.ai/signup`). Built as a thin orchestrator over three sub-skills that each do one job well.

**Architecture:**

```
aurie-web-onboarding-experimentation-ideas (this skill — orchestrator)
    ├── aurie-web-flow-snapshot         (sub-skill: builds flow_map from AIWeb source)
    ├── aurie-web-onboarding-audit      (sub-skill: per-screen audit + Mixpanel funnel)
    │       └── aurie-web-flow-snapshot (transitive — audit needs flow_map too)
    └── aurie-web-experiment-history    (sub-skill: Aurie + Aura Web slices of Learnings/Experiments)
```

This skill owns: idea generation, dedup, preview, write to Notion, final report. Everything else delegates to a sub-skill — including loading Noom priors (which the audit sub-skill loads + passes through in `audit_run.noom_priors`).

**What this skill is NOT:**

- **Not a flow extractor.** Source extraction lives in `aurie-web-flow-snapshot`.
- **Not an auditor.** Per-screen friction analysis + Mixpanel funnel live in `aurie-web-onboarding-audit`.
- **Not a history reader.** Experiment-history reads live in `aurie-web-experiment-history`.
- **Not an analyzer for shipped experiments.** That's `data:analyze-experiment`. This skill only generates new ideas.
- **Not multi-variant.** Each run audits one variant (the active one, picked Notion-first by `aurie-web-flow-snapshot`'s R1).

## When to use

Trigger when the user says any of (`Aurie Web`, `AIWeb`, and `AI Web` all mean the same thing — match on any):

- "ideate experiments for Aurie Web"
- "generate Aurie Web onboarding experiment ideas"
- "what should we test next on Aurie Web onboarding"
- "propose A/B tests for Aurie Web onboarding"
- "next batch of Aurie Web experiments"
- "give me fresh ideas for the Aurie Web signup funnel"

Run on demand any time the team wants a fresh batch of experiment proposals — typically after a previous batch has either shipped or been triaged out of the backlog.

## Inputs

The skill takes no required arguments. Optional flags:

- `count=<N>` — how many ideas to generate. Default `5`. Capped at `8`.
- `focus=<screen-name>` — narrow ideation to a specific screen (must match a `screen_name` in the resolved `flow_map`).
- `dry-run` — produce ideas + preview, don't write to Notion. For iterating on prompt quality.
- `allow-cached-subskills` — opt in to reusing recent sub-skill results from prior runs in the same session instead of invoking each sub-skill fresh. Off by default. See [Sub-skill invocation policy](#sub-skill-invocation-policy). Use only when you have just run this skill end-to-end against the same `aiweb-ref` in the last hour and want to iterate on idea generation cheaply.
- Pass-through to sub-skills:
  - `aiweb-ref=<branch-or-sha>` → forwarded to `aurie-web-flow-snapshot`.
  - `skip-aiweb-sync` → forwarded.
  - `skip-live-verify` → forwarded.
  - `skip-mixpanel` → forwarded to `aurie-web-onboarding-audit`.
  - `skip-audit` → forwarded.
  - `audit-mode=full|focused` → forwarded.

If the user didn't pass any flags, generate 5 ideas across the full flow with live AIWeb sync, full audit, and Mixpanel funnel enabled.

## Prerequisites

### Hard requirements

- **Notion access** to `Learnings/Experiments` (DB id `2cbe45a3246040658b9f20559b5b452b`). Step 6 needs **write** access; the sub-skill `aurie-web-experiment-history` needs **read** access. At least one of:
  - `NOTION_TOKEN` set in `~/.claude/settings.json` env — **strongly preferred** for sub-skill dedup (see root [CLAUDE.md → Notion REST integration](../../../../CLAUDE.md#notion-rest-integration)). When set, the experiment-history sub-skill enumerates ~330+ Aura Web rows + the full Aurie Web slice for accurate priors + dedup. The orchestrator's Step 6 writes can use either REST (`notion-create-pages` via `notion_rest.py`) or the Notion MCP — both produce identical writes.
  - **Notion MCP** connected — fallback. Writes work, but the history sub-skill's dedup will be incomplete (semantic search caps at 25 results per query; see its SKILL.md for the partial-dedup warning).
- Sub-skills declare their own Notion read requirements: `aurie-web-onboarding-audit` needs read access to `Noom Onboarding — UX Priors`; `aurie-web-flow-snapshot` needs `Aurie Web Signup Flow — Static Snapshot`; `aurie-web-experiment-history` needs `Learnings/Experiments`. See their SKILL.md files for details.
- The reference file `references/idea-schema.md` is present (idea field definitions + body-stuffing template + validation rules).

### Soft requirements (graceful degradation)

- Sub-skills handle their own soft requirements:
  - `aurie-web-flow-snapshot` — live AIWeb access (Tier 1 GitHub Connector or Tier 2 git clone) is recommended; falls back to Notion snapshot.
  - `aurie-web-onboarding-audit` — Mixpanel MCP is recommended; falls back to qualitative audit only.
  - `aurie-web-experiment-history` — no soft requirements.

If Mixpanel is unavailable, ideas use `grounding_source: audit` instead of `grounding_source: funnel`. The readout flags this clearly.

## Sub-skill invocation policy

This skill orchestrates three sub-skills. By default, each one MUST be invoked fresh on every run — the agent is NOT permitted to silently shortcut a sub-skill by reusing its output from a prior run, even if a recent run produced identical inputs. The shortcut is the failure mode this skill is hardened against.

**Strict mode (default):**

- Each sub-skill call in Step 1 / Step 2 must produce a fresh entry banner with a unique `run_id` and an `invoked_at` ISO timestamp BEFORE the orchestrator continues.
- Each sub-skill call must also produce a freshness signal at completion (a real Notion query timestamp, a real Mixpanel query timestamp, a real git-clone commit SHA + clone timestamp — depending on the skill).
- The orchestrator captures every sub-skill `run_id` and freshness signal and surfaces them in the [Sub-skill ledger](#sub-skill-ledger) in Step 7.
- If, at the start of Step 7, the orchestrator finds it cannot account for a fresh `run_id` from any of the three sub-skills, it MUST halt the final report, declare a strict-mode violation, and ask the user to re-run.

**Cached mode (opt-in via `allow-cached-subskills`):**

- The orchestrator MAY reuse a sub-skill's output from an earlier run in the same session, but ONLY if all of these hold:
  - The earlier run completed successfully end-to-end (printed the ✅ exit banner).
  - The earlier run's freshness signal is < 60 minutes old.
  - The user passed `allow-cached-subskills` explicitly on this run.
- When reusing, the orchestrator MUST print the cached `run_id` + age + reason in the ledger, and the cached entry MUST appear with a `⚠ REUSED` marker (not a `✓` marker).

**What strict mode forbids:**

- Skipping a sub-skill because "the previous batch already did exactly that work."
- Inferring `flow_source`, `commit_sha`, `funnel` numbers, or `noom_priors` from prior context without a fresh sub-skill invocation.
- Treating the sub-skill's `SKILL.md` as a recipe to follow inline by hand (with direct Notion/Mixpanel calls) instead of invoking the sub-skill as a skill. If you find yourself reading a sub-skill's SKILL.md to extract its query and run that query directly, you are in violation — invoke the sub-skill instead.

This policy exists because Cowork agents tend to optimize toward token efficiency and will silently elide sub-skill invocations when prior context "looks good enough." Silent elision destroys grounding integrity: the next batch might be ideating against a stale `flow_map` or a Notion DB that has new rows.

## Workflow

### Step 0 — Pre-load priors

Pre-load only what this skill itself needs (sub-skills handle their own priors — Noom priors live in the audit sub-skill, flow snapshot lives in the snapshot sub-skill, experiment history lives in the history sub-skill).

#### (a) Notion knowledge — load via `shared:retrieve-knowledge` (or the Notion MCP directly)

| Knowledge path | What it gives you | Used by |
|---|---|---|
| `Products/Aurie` | Brand-voice priors (warm, therapeutic, conversational; no Aura-style content/retention framing). | Step 3 per-idea copy |

#### (b) Skill-local references

| File | What it gives you |
|---|---|
| `references/idea-schema.md` | Generated-idea field definitions, Difficulty/Priority classification, body-stuffing template, validation rules. |

#### Print

```
✅ Loaded Aurie brand voice
✅ Loaded idea schema
   (Noom priors will arrive via the audit sub-skill in Step 2)
```

### Step 1 — Get experiment history

**INVOCATION REQUIRED.** Invoke [`aurie-web-experiment-history`](../aurie-web-experiment-history/SKILL.md) as a sub-skill (do not inline its recipe by reading its SKILL.md and running its Notion query directly — see [Sub-skill invocation policy](#sub-skill-invocation-policy)). No flags needed by default.

Before the sub-skill returns, the orchestrator MUST observe its 🔧 entry banner and ✅ exit banner. Capture the sub-skill's `run_id` and `notion_query_at` for the ledger.

If `allow-cached-subskills` was passed AND a successful `experiment-history` run exists in this session within the last 60 minutes, reuse that run's output and mark the ledger entry `⚠ REUSED`.

Receive in memory:

```yaml
experiment_history_run:
  run_id: <fresh uuid8 from the sub-skill>
  invoked_at: <ISO>
  read_at: <ISO timestamp — freshness signal>
  experiment_state: { rows: [...], by_title_index: {...}, by_phase: {...}, total: N }
  experiment_history:
    aurie_web: { winning_patterns, failure_patterns, under_tested_screens }
    aura_web:  { winning_themes, losing_themes }
```

Print the experiment-state summary the sub-skill returns (counts per phase + recurring patterns), prefixed with the sub-skill's run_id.

### Step 2 — Get audit + funnel + flow_map + Noom priors

**INVOCATION REQUIRED.** Invoke [`aurie-web-onboarding-audit`](../aurie-web-onboarding-audit/SKILL.md) as a sub-skill (do not inline its recipe — see [Sub-skill invocation policy](#sub-skill-invocation-policy)). The audit sub-skill will in turn invoke `aurie-web-flow-snapshot` for `flow_map`, so this single call covers both. Pass through the user's flags (`aiweb-ref`, `skip-aiweb-sync`, `skip-live-verify`, `skip-mixpanel`, `skip-audit`, `audit-mode`, `focus`).

Pass `experiment_state` from Step 1 through to the audit so its sub-call to `aurie-web-flow-snapshot` can satisfy R1 without re-querying Notion.

Before the audit sub-skill returns, the orchestrator MUST observe:

1. The audit's 🔧 entry banner and ✅ exit banner.
2. Inside the audit's run, the nested 🔧 / ✅ banners from `aurie-web-flow-snapshot`.
3. Real freshness signals: a Notion fetch timestamp on Noom priors, a real `commit_sha` + `cloned_at` from the flow snapshot (or a real Notion-snapshot `Captured:` date if Tier 3 is the source), and either a real Mixpanel query timestamp or an explicit `funnel_status: skipped` declaration.

Capture both `run_id`s for the ledger. If any banner is missing, halt and declare a strict-mode violation.

If `allow-cached-subskills` was passed AND successful `audit` and `flow-snapshot` runs exist in this session within the last 60 minutes against the same `aiweb-ref`, reuse and mark `⚠ REUSED`.

Receive in memory:

```yaml
audit_run:
  run_id: <fresh uuid8 from audit>
  invoked_at: <ISO>
  sub_skill_runs:
    aurie-web-flow-snapshot:
      run_id: <fresh uuid8 from flow-snapshot>
      invoked_at: <ISO>
      commit_sha: <sha or null if Tier 3>
      cloned_at: <ISO or null if Tier 3>
  noom_priors_fetched_at: <ISO>      # freshness signal
  funnel_pulled_at: <ISO | null>     # freshness signal
  flow_source: <value>
  variant_decision: { ... }
  flow_map: [...]                    # variant-filtered, with shipped_features per screen
  flow_audit: { ... }                # journey_audit per screen (incl. noom_anti_pattern_check) + cross_flow_concerns
  funnel: { funnel_status, steps, worst_drop_offs }
  instrumentation_health: { coverage_pct, recommended_action, ... }
  noom_priors:                       # passed through from audit's Step 0(a)
    applicable_patterns: [...]       # 7 entries
    anti_patterns: [...]             # 6 entries
```

Print the sub-skill's per-step blocks (sync outcomes, audit summary including Noom violations, funnel, instrumentation banner) — this is the bulk of what the user sees during the run.

If `focus=<screen-name>` was passed, the audit has already been filtered. If `audit-mode=focused` was passed and Mixpanel is unavailable, the sub-skill falls back to `full` mode automatically.

### Step 3 — Generate ideas

Synthesise `count` (default 5) new experiment ideas using all the inputs above:

- **Anchored in flow_map** — every idea must reference a specific `screen_name` from `flow_map` (in `audit_run.flow_map`).
- **Grounded in audit** — every idea must cite **at least one** of: a specific `drop_off_opportunity.opportunity_id` from `flow_audit.per_screen[*].journey_audit`, OR a `cross_flow_concern.concern_id` from `flow_audit.cross_flow_concerns`. This is the primary grounding mechanism.
- **Funnel-grounded when possible** — if `funnel.funnel_status == ok | partial` AND the target screen has a non-null `mixpanel_event` AND the audit's risk assessment is corroborated by the funnel drop-off rate, set `grounding_source: funnel` and cite both the audit `opportunity_id` and the Mixpanel drop %.
- **Audit-grounded when funnel unavailable** — if `funnel.funnel_status == skipped | lexicon_empty | lexicon_stale`, set `grounding_source: audit` and cite only the `opportunity_id` / `concern_id`.
- **History-grounded as last resort** — only when neither funnel nor audit can support the idea (rare — should only happen if `skip-audit` was passed). Set `grounding_source: history`.
- **Grounded in Noom priors** — every idea must also cite at least one of the 7 Applicable Patterns from `audit_run.noom_priors.applicable_patterns` (passed through by the audit sub-skill in Step 2). Patterns explain *why* a fix should work; the audit explains *what* needs fixing.
- **Checked against anti-patterns** — every idea must be evaluated against the 6 Anti-Patterns from `audit_run.noom_priors.anti_patterns`. Ideas that violate an anti-pattern are dropped or reframed. **Bonus signal:** if the target screen already shows a `noom_anti_pattern_check.violations` entry in `flow_audit.per_screen[*].journey_audit`, the idea SHOULD address that violation specifically — call it out in the hypothesis.
- **Informed by history** — prefer ideas that align with `experiment_history.aurie_web.winning_patterns` or `experiment_history.aura_web.winning_themes`. Avoid repeating `failure_patterns` unless there's a clear reason to retry (must justify in the hypothesis).
- **Aware of state** — never duplicate a row already in pipeline phase `Idea`, `In Review`, or `Running` (uses `experiment_state.by_title_index` for lookup). Step 4 enforces this; Step 3 should pre-filter to avoid wasted generations.
- **Aware of shipped features** — for every candidate idea, walk `flow_map[target_screen].shipped_features` (populated by `aurie-web-flow-snapshot`'s R12) and confirm the `proposed_change` does NOT duplicate any tag's `mechanism` already in production. The 12 taxonomy tags are: `personalization`, `social_proof`, `trust_signals`, `risk_reversal`, `urgency_scarcity`, `pricing_patterns`, `progress_indicators`, `conditional_branching`, `transition_copy`, `safety_guardrails`, `save_progress`, `post_signup_followup`. **Allowed**: proposing a *different* mechanism within the same tag (e.g. an LLM-generated paragraph on a screen that today has rule-based personalization) — call out the distinction explicitly in the hypothesis. Step 4 enforces this with a hard dedup pass; Step 3 should pre-filter.
- **Bias toward high-risk audit findings** — when picking which screens to ideate on, prefer screens in `flow_audit.high_risk_screens` and concerns in `flow_audit.high_risk_concerns`.
- **Cover under-tested screens** when impact is roughly equal — `experiment_history.aurie_web.under_tested_screens` lists Aurie Web screens with zero prior experiments.
- **Voiced for Aurie** — use the brand voice from `Products/Aurie` (warm, therapeutic, conversational; no Aura-style content/retention framing).

#### Per-idea output shape

```yaml
experiment_name: <camelCase, unique vs experiment_state>
hypothesis: |
  If <change>, then <metric movement> will improve because <noom pattern + audit observation>.
experiment_type: A/B Copy | Flow Change | UI/UX Component | Personalization
target_screen: <exact screen_name from flow_map>
target_metric: Activation Rate | Step Completion | Drop-off Reduction | Conversion | Retention D7
current_copy_or_component: |
  <verbatim quote from flow_map for that screen>
proposed_change: |
  <specific, implementable description — name the element, the new value, any conditional behavior>
priority: High | Medium | Low
difficulty: Easy | Medium | Hard
noom_patterns_cited: ["Pattern N — <name>"]
anti_pattern_check: passed
shipped_features_check: passed                          # confirms proposed_change does NOT duplicate any flow_map[target_screen].shipped_features entry; if same-tag-different-mechanism, name the existing tag and explain the differentiation
audit_grounding:
  opportunity_ids: ["audit-<screen_id>-<seq>", ...]    # from flow_audit.per_screen
  concern_ids:     ["audit-flow-<seq>", ...]            # from flow_audit.cross_flow_concerns
  cited_reasons: |
    <inline quote of the audit's reason text(s)>
historical_grounding: |
  <one-line reference to a Step 1 winner/loser/under-tested-screen — either Aurie Web row or Aura Web pattern theme>
funnel_grounding:                                        # only when grounding_source: funnel
  mixpanel_event: <event name + breakdown>
  drop_off_pct: <int>
grounding_source: funnel | audit | history
tracking_required: true | false                          # true iff target screen's flow_audit.per_screen.journey_audit.tracking_status.has_event == false
tracking_prerequisite: |                                  # only when tracking_required: true
  <one-line: "Add Mixpanel event '<name>' with breakdown { Screen: '<screen>' } to the <component> before launching this test, otherwise the result will be unreadable.">
```

#### Quality gates (every idea must pass — see [`references/idea-schema.md`](references/idea-schema.md))

- ✅ Cites at least one `opportunity_id` or `concern_id` from `flow_audit` (when audit is available)
- ✅ When funnel data exists, also cites the specific Mixpanel drop-off the idea targets (drop %)
- ✅ When neither audit nor funnel is available, explicitly cite the documented tracking gap from `instrumentation_health` the idea addresses
- ✅ Considers historical context — aligns with a winning pattern OR explicitly justifies retrying a loser
- ✅ Doesn't duplicate an existing row in pipeline phase `Idea` / `In Review` / `Running` (lookup `experiment_state.by_title_index`)
- ✅ `shipped_features_check: passed` — `proposed_change` does not duplicate any `flow_map[target_screen].shipped_features` entry. Same-tag + different-mechanism overlaps must call out the differentiation in `hypothesis` (e.g. "today's personalization is rule-based; this idea adds an LLM-generated paragraph").
- ✅ Specific enough to implement as a real A/B test
- ✅ Three-clause `If / then / because` hypothesis structure
- ✅ Sized to a 2-week sprint
- ✅ Cites ≥1 noom pattern, passes all 6 anti-pattern checks
- ✅ Targets a `screen_name` actually in `flow_map`
- ✅ When `audit-mode=focused` was used, idea targets either the focus screen, a worst-Mixpanel-drop-off screen, or an untracked screen
- ✅ `tracking_required` is set correctly: `true` whenever the target screen's `tracking_status.has_event == false`, otherwise `false`. When `true`, `tracking_prerequisite` must name the specific Mixpanel event + breakdown to add.

#### Cross-cutting takeaways (when `count >= 3`)

Also produce:

- **Theme** — one sentence describing the connective tissue across the batch.
- **Portfolio guardrail** — one suggestion for an ongoing measurement the team should add.

These go in the chat preview but **not** as separate `Learnings/Experiments` rows.

### Step 4 — Dedup

Before writing anything, run two dedup passes — one against historical `experiment_state` (Step 1) and one against `flow_map[*].shipped_features` (Step 2). The first catches "we already tested this idea"; the second catches "this idea is already shipped, not an experiment at all".

#### Pass A — Historical experiment dedup (vs `experiment_state.by_title_index`)

1. **Exact title match** (case-insensitive) on `experiment_name` ↔ row `Title` → drop the duplicate, log it, generate a replacement idea or reduce `count`.
2. **Fuzzy match on (`target_screen`, `target_metric`) tuple** against active rows (Status blank or `Running`) → flag as "near-duplicate of `<existing>`" and surface for user decision (don't auto-drop).
3. **Naming sanity** — `experiment_name` must be valid camelCase, ≤ 40 chars, no spaces / hyphens / underscores. Reformat or regenerate if invalid.

#### Pass B — Shipped-feature dedup (vs `flow_map[target_screen].shipped_features`)

For each surviving idea, look up `flow_map[target_screen].shipped_features` and check `proposed_change` against every entry's `mechanism`. Reject conditions:

4. **Same tag + same mechanism** → hard reject with `reject_reason: already_shipped`. Log: matching `tag`, cite of existing `mechanism` and `source` (file:line), one-line summary of proposed change. Generate a replacement idea targeting a different screen or a different feature dimension.
5. **Same tag + different mechanism** (e.g. shipped feature is rule-based personalization, idea is LLM-generated personalization) → keep the idea, but require its `hypothesis` to call out the differentiation explicitly. If the hypothesis doesn't already, send the idea back to Step 3 for a one-shot reframe.
6. **Conditional / experiment-gated shipped features** (entry's `inputs:` lists an `AB_EXPERIMENTS.*` constant) → soft-shipped. Only reject if the test variant the idea proposes would also have that experiment bucket on. Otherwise pass.

#### Reporting

If dedup (across both passes) drops more than 30% of ideas, regenerate the dropped ones with a note that those dimensions are over-tested or already-shipped. The Step 5 preview must surface the per-pass drop count separately so the user can tell whether the skill is duplicating prior tests vs. inventing already-live features.

### Step 5 — Present preview, ask for routing

Print the full preview in chat:

1. **Header** — what was loaded (`flow_source` + commit/snapshot date, screens, snapshot drift if applicable), funnel summary, run-id.
2. **AIWeb source sync result** — from Step 2's sub-skill output.
3. **Variant decision** — print `variant_decision` from Step 2 so reviewers can see which onboarding variant was audited and why. Use literal source-code names.
4. **Live verification result** — Step 2's SSR `curl` summary table.
5. **Onboarding flow audit summary** — from Step 2 (`flow_audit`). Show: total screens audited, count of high/medium/low-risk drop-off opportunities, the high-risk screens list, and the high-risk cross-flow concerns. Tracking summary (`X` of `N` screens have a Mixpanel event from source, `Y` are tracking-blind). If `skip-audit` was passed, show the skip notice.
6. **Instrumentation health** — from Step 2 (`instrumentation_health`). Prepend warning if `coverage_pct < 0.5` or `funnel_status == skipped`.
7. **Experiment state summary** — counts per pipeline phase from Step 1 (`experiment_state.by_phase`).
8. **Worst drop-off screens** — top 3 from `funnel.worst_drop_offs`, bolded (only when funnel data is available). When both funnel and audit data exist, annotate each with whether the audit also flagged the screen as high-risk (validation signal).
9. **Each idea** in a readable block (markdown table or labelled list — not raw YAML). Each idea shows `grounding_source: funnel | audit | history`, the cited `opportunity_id` / `concern_id`, and `tracking_required: true | false` (when `true`, also show the prerequisite line).
10. **Cross-cutting theme + portfolio guardrail** if `count >= 3`.

Then ask the user:

> Want to write these to Notion?
> (1) Nothing — keep the preview in chat (default).
> (2) Write all ideas to `Learnings/Experiments` (Status blank → "Idea" phase).
> (3) Write a subset — list which idea names to keep.

Wait for the user's pick before proceeding to Step 6. If `dry-run` was passed, skip the question — print the preview and stop.

### Step 6 — Write to Notion

For each approved idea, create a new page in `Learnings/Experiments` (DB ID `2cbe45a3246040658b9f20559b5b452b`) via the Notion MCP.

#### Field mapping

The canonical schema requires `Title`, `App`, `Platform`, `Start Date`. `Experiment Type` is optional there (most rows are blank), but this skill always knows it — every idea is `Onboarding` — so it is set on every create. The 9 idea-specific fields (`Target Screen`, `Target Metric`, `Current Copy / Component`, `Proposed Change`, `Priority`, `Difficulty`, `Prompt Used`, `Model Used`, `Date Generated`) don't have native columns — they go into the page body's `## What was tested` section using the body-stuffing template in [`references/idea-schema.md`](references/idea-schema.md).

| Idea field | Goes to |
|---|---|
| `experiment_name` | native property `Title` |
| `hypothesis` | native property `Hypothesis` |
| `experiment_type` | mapped to native `Experiment Type` enum (`Onboarding`) |
| `proposed_change` | summarised as a 2-variant `Variants` field: `a: control` / `b: <one-line summary>` |
| All 9 idea-specific fields | body section `## What was tested` per `idea-schema.md` body template |
| Audit (prompt + model) | body section `# Sources` |

**Required fields:**

- `App: "Aurie"`, `Platform: "Aurie Web"` (fixed for this skill)
- `Experiment Type: ["Onboarding"]`
- `Start Date: <today's ISO date>` — repurposed as `Date Generated`. Team overwrites when the experiment ships.
- `Status: blank` (= "Idea" pipeline phase)
- `End Date` left blank

#### Per-idea Notion MCP call (conceptual shape)

The full `notion-create-page` call shape (properties + `children` body blocks, including the `## What was tested` and `# Sources` scaffolding) lives in [`references/notion-write-template.md`](references/notion-write-template.md). Follow it verbatim per approved idea.

For each row, after a successful create, capture the new page URL and report it. If a write fails (validation, rate limit, Notion 5xx), do not abort the batch — log the failure with the validation message, continue with the rest, and surface failed rows at the end so the user can retry.

### Step 7 — Final report

#### Step 7a — Pre-flight strict-mode check

Before printing the final report, validate that every required sub-skill produced a fresh `run_id` (or, if `allow-cached-subskills` is in effect, a properly-disclosed reused `run_id`).

Required sub-skill `run_id`s:

- `experiment_history_run.run_id` (from Step 1)
- `audit_run.run_id` (from Step 2)
- `audit_run.sub_skill_runs.aurie-web-flow-snapshot.run_id` (from Step 2's nested invocation)

If any are missing, halt the report and print:

```
❌ STRICT MODE VIOLATION
   The following sub-skills did not produce a fresh run_id during this run:
     - <skill-name> (expected from Step <N>)
   The output of this run cannot be trusted because grounding integrity
   is unverified. Re-run, or pass `allow-cached-subskills` if you accept
   reusing recent sub-skill output.
```

Then stop. Do NOT print the ledger, do NOT print the success summary, do NOT count Step 6 writes as legitimate. (If Step 6 already wrote to Notion, surface those URLs with a `⚠ written under unverified strict mode — review before promoting` warning, but still halt.)

#### Step 7b — Sub-skill ledger

```
─── Sub-skill ledger (run <orchestrator-run-id>) ───
  ✓ aurie-web-experiment-history    run_id=<id>   notion_query_at=<ISO>   aurie=<N> aura=<M>
  ✓ aurie-web-onboarding-audit      run_id=<id>   noom_fetched_at=<ISO>   funnel_pulled_at=<ISO|skipped>
    ↳ aurie-web-flow-snapshot      run_id=<id>   commit_sha=<sha|snapshot>   cloned_at=<ISO|null>
─────────────────────────────────────────────────
  Strict mode: <enforced | bypassed via allow-cached-subskills>
```

When `allow-cached-subskills` is active and one or more sub-skills were reused, replace `✓` with `⚠ REUSED` and add `age=<minutes>m from prior run <prior-run-id>` on that line.

#### Step 7c — Run summary

```
aurie-web-onboarding-experimentation-ideas run <run-id> complete:
  ✅ flow_source: live-github-clone @ a3f9c1d (ref=main) — <N> screens for variant <variant_decision.variant>
  ✅ Variant audited: webOnboardingV3 (basis: notion)
  ✅ Snapshot drift: 0 screens   [or: "📌 3 changed since 2026-04-29"]
  ✅ Live verification: <X>/<Y> SSR pages OK
  ✅ Flow audit: audit_mode=full, <N> screens audited
       High-risk: 5 screens, 3 cross-flow concerns
       Tracking coverage: <X>/<N> screens have a Mixpanel event
  ✅ Funnel pulled, N steps (internal accounts excluded)   [or: "⚠️ Mixpanel skipped"]
  ✅ History: <H> Aurie Web rows analyzed, <A> Aura Web pattern themes
  ✅ <count> ideas generated, all passed dedup and quality gates
       Dedup: Pass A dropped <X>, Pass B dropped <Y> (already-shipped)
       Grounding: <X> funnel-grounded, <Y> audit-grounded, <Z> history-only
       Tracking required: <N> ideas (target tracking-blind screens)

  Writes to Learnings/Experiments:
    + lifestyleQuizSocialProof    https://notion.so/...   (cites: audit-q5_daily_burden-002)
    + paywallEscapeHatch          https://notion.so/...   (cites: audit-flow-001 + Mixpanel -38%)
    ...

  Instrumentation health (from Step 2):
    Coverage: 87% (13 of <flow_map.length> screens have live Mixpanel data)
    Recommended action: add_tracking
```

## Output format

The chat preview (Step 5) is the primary user-facing artifact. Structure:

1. **Run header** — run-id, `flow_source`, screens loaded, funnel summary (1–2 lines).
2. **Onboarding flow audit summary** — from Step 2's sub-skill output.
3. **Instrumentation health** — `coverage_pct` + `recommended_action`. Prepend the blocking warning when needed.
4. **Experiment state summary** — counts per phase from Step 1.
5. **Worst drop-off screens** — top 3, bolded (only when funnel data available). Annotate which are also flagged in the audit.
6. **Generated ideas** — one block per idea with all fields from Step 3. Use a labelled list, not raw YAML.
7. **Cross-cutting theme + portfolio guardrail** if `count >= 3`.
8. **Routing question** from Step 5.

Avoid raw markdown tables that don't render well — use code-fenced ASCII for funnel + analysis blocks.

## Notes and gotchas

Operational constraints and edge-case reminders (sub-skill failure cascade, single-variant happy path, mandatory Mixpanel exclusion filter, single write destination, body-stuffed fields, `Start Date` repurposing, append-only writes, Aurie-Web-only scope, `count` cap, hypothesis format) live in [`references/notes-and-gotchas.md`](references/notes-and-gotchas.md).

## Example invocations

Worked end-to-end examples for the common flag combinations (default fresh batch, focused-on-one-screen, dry-run, history-only/skip-mixpanel) live in [`references/examples.md`](references/examples.md).

## References

### Notion knowledge (loaded at Step 0)

- **`Products/Aurie`** — brand-voice priors used by Step 3 hypothesis copy. Direct read.

### Notion knowledge (loaded transitively by sub-skills)

- **`Noom Onboarding — UX Priors`** — loaded by `aurie-web-onboarding-audit` and passed through in `audit_run.noom_priors`. Page: <https://app.notion.com/p/aurahealth/Noom-Onboarding-UX-Priors-35375e23af2080f8b297c912a3ca6ccf>.
- **`Aurie Web Signup Flow — Static Snapshot`** — loaded by `aurie-web-flow-snapshot` (Tier 3 fallback). Page: <https://app.notion.com/p/aurahealth/Aurie-Web-Signup-Flow-Static-Snapshot-35375e23af2080c581a8ebc7bb8c1af8>.
- **`Learnings/Experiments`** — read by `aurie-web-experiment-history`. DB id `2cbe45a3246040658b9f20559b5b452b`.

### Skill-local references

- [`references/idea-schema.md`](references/idea-schema.md) — generated-idea field definitions, Difficulty/Priority classification, body-stuffing template, validation rules.
- [`references/notion-write-template.md`](references/notion-write-template.md) — full per-idea `notion-create-page` call shape for Step 6 (properties + body blocks).
- [`references/notes-and-gotchas.md`](references/notes-and-gotchas.md) — operational constraints + edge-case reminders.
- [`references/examples.md`](references/examples.md) — worked end-to-end example invocations.

### Sub-skills (called in workflow)

- [`aurie-web-flow-snapshot`](../aurie-web-flow-snapshot/SKILL.md) — Steps 1.5 + 2 + 2.5 from the pre-split version (called transitively via the audit sub-skill).
- [`aurie-web-onboarding-audit`](../aurie-web-onboarding-audit/SKILL.md) — Steps 2.7 + 3 + 4 from the pre-split version, plus Noom priors load. Called directly in Step 2 above.
- [`aurie-web-experiment-history`](../aurie-web-experiment-history/SKILL.md) — Steps 1 + 5 from the pre-split version. Called in Step 1 above.

### External

- Live signup URL: <https://www.aurie.ai/signup>
- Mixpanel project: `963821` (Project Aura), workspace `30768`
- `Learnings/Experiments` Notion DB: <https://www.notion.so/2cbe45a3246040658b9f20559b5b452b>
