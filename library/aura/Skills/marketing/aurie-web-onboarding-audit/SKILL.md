---
name: aurie-web-onboarding-audit
description: >
  Diagnose where the Aurie Web (a.k.a. AIWeb / AI Web) onboarding/paywall flow
  is leaking. Walks the variant-filtered flow_map (from
  aurie-web-flow-snapshot) screen by screen, producing per-screen friction
  analysis (cognitive load, decision difficulty, exit points, drop-off
  opportunities, Noom anti-pattern violations), branching audits, and
  cross-flow concerns. Loads Noom UX priors (7 Applicable Patterns + 6
  Anti-Patterns) and grades every screen against them. Pulls Mixpanel funnel
  drop-offs when available (gracefully degrades), and computes instrumentation
  health (event coverage, tracking-blind screens, recommended action). Returns
  a structured flow_audit + funnel + instrumentation_health + noom_priors
  bundle for any caller skill (ideation, reporting, retros). Trigger when the
  user wants to audit the Aurie Web onboarding flow, see worst drop-offs, or
  check analytics coverage on the signup funnel.
metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index                                            # baseline routing aid (universal)
    - Learnings/UX Priors/Noom Onboarding — UX Priors        # page 35375e23af2080f8b297c912a3ca6ccf — 7 patterns + 6 anti-patterns applied to every screen
    - Tools/Mixpanel/Events                                  # canonical Aurie Web event lexicon (cross-check + fallback)
    - Tools/Mixpanel/Super Properties                        # Source App + test-account exclusion-filter properties
    - Tools/Notion                                    # Notion REST / MCP access pattern (NOTION_TOKEN)
---

# aurie-web-onboarding-audit

A diagnostic skill that walks the Aurie Web onboarding flow and tells you where users are dropping off — quantitatively (Mixpanel funnel) and qualitatively (per-screen friction analysis grounded in Noom UX priors). Other skills (`aurie-web-onboarding-experimentation-ideas`) call this one to ground their ideas in observable problems instead of fishing for opportunities.

**Scope:** Aurie Web onboarding/paywall surface only. Returns `flow_audit` + `funnel` + `instrumentation_health` + `noom_priors` bundle.

**What this skill is NOT:**

- **Not a flow extractor.** It does not read AIWeb source — it depends on `aurie-web-flow-snapshot` for `flow_map`.
- **Not an idea generator.** It does not propose experiments. The audit is the input to ideation, not ideation itself.
- **Not a tracking-fix skill.** Identifying tracking-blind screens does not auto-instrument them. It only flags where instrumentation is needed.

## When to use

Trigger when the user says any of (`Aurie Web`, `AIWeb`, and `AI Web` all mean the same thing — match on any):

- "audit Aurie Web onboarding"
- "where are the worst drop-offs in Aurie Web signup"
- "check Aurie Web analytics coverage"
- "instrumentation health for Aurie Web onboarding"
- "where is Aurie Web onboarding leaking"
- "analyse the Aurie Web signup funnel"
- "is our Aurie Web tracking complete"

Also runs as a sub-step when called by `aurie-web-onboarding-experimentation-ideas`.

## Inputs

The skill takes no required arguments. Optional flags:

- `audit-mode=full|focused` — Step 1 audit scope. `full` (default) walks every screen in the variant-filtered `flow_map`. `focused` audits only the worst-Mixpanel-drop-off screens + all untracked screens — faster, less coverage. Use `focused` for repeat runs in the same session.
- `skip-audit` — skip Step 1 (qualitative audit) entirely. Falls back to funnel-only output. Not recommended unless you only want quantitative numbers.
- `skip-mixpanel` — skip Step 2's Mixpanel funnel pull. Forces the audit to be qualitative-only.
- `focus=<screen-name>` — narrow audit to a specific screen (and its neighbours when in `focused` mode).
- Pass-through to `aurie-web-flow-snapshot`: `aiweb-ref=<ref>`, `skip-aiweb-sync`, `skip-live-verify` — forwarded as-is to the sub-skill.

## Prerequisites

### Hard requirements

- **Notion access** — used for two reads: (1) `Noom Onboarding — UX Priors` page (loaded in Step 0 and passed through in `audit_run.noom_priors`); (2) transitively via the sub-skill `aurie-web-flow-snapshot` (Tier 3 fallback). At least one of:
  - `NOTION_TOKEN` set in `~/.claude/settings.json` env — preferred. REST fetch is direct and faster (see root [CLAUDE.md → Notion REST integration](../../../../CLAUDE.md#notion-rest-integration)).
  - **Notion MCP** connected — fallback. `notion-fetch` on the page id works for these single-page reads (this skill does NOT enumerate a database, only fetches two known pages, so MCP is fully sufficient here — no `partial` warning needed).

  If neither is available, stop with the error message the sub-skill produces.

### Soft requirements (graceful degradation)

- **Mixpanel MCP** — used in Step 2 for live funnel data. If not available, skill warns and proceeds with **history-only audit** — the qualitative `flow_audit` from Step 1 is still produced; only Step 2's drop-off table is skipped.
- **Live AIWeb access** (Tier 1 or Tier 2 of the sub-skill) — strongly recommended for fresh source. Without either, the snapshot is used.

## Workflow

### Step 0 — Entry banner + pre-load priors and flow_map

#### Entry banner — always first

Before any tool call, generate a fresh `run_id` and print an entry banner. This banner is how callers (orchestrator, user) verify the skill actually ran instead of being silently shortcut by an agent reusing prior context.

Generate `run_id = "aud-" + random 8-char hex` (fresh on every invocation — never reuse a `run_id` from a prior turn).

Print:

```
🔧 aurie-web-onboarding-audit starting (run_id: <id>)
   invoked_by: <caller-skill-name | "user">
   invoked_at: <ISO timestamp>
   inputs: { audit-mode: <full|focused>, skip-audit: <bool>, skip-mixpanel: <bool>, focus: <screen|null> }
```

The `invoked_at` timestamp must be the actual current time at the start of this run — not lifted from a prior run. The `run_id` proves a fresh invocation.

After the entry banner, the next two steps run, ideally in parallel.

#### (a) Load Noom UX priors

Fetch the Notion page `Noom Onboarding — UX Priors` (page id `35375e23af2080f8b297c912a3ca6ccf`). Read the page body and extract the **7 Applicable Patterns** (heading_2 section "Applicable Patterns for Aurie Web") and the **6 Anti-Patterns to Avoid** (heading_2 section "Anti-Patterns to Avoid") into memory.

These priors are used two ways in this skill:

1. **Per-screen anti-pattern check** in Step 1 Pass 1 — every screen is graded against all 6 anti-patterns. A violation becomes a `noom_anti_pattern_check.violations` entry on that screen's `journey_audit`.
2. **Cross-flow Noom pattern citations** in Step 1 Pass 3 — recurring patterns observed across screens that map to one of the 7 Applicable Patterns are flagged so downstream callers (e.g. ideation) can cite them.

The `noom_priors` object is also returned in the final `audit_run` artifact (Step 4) so caller skills don't have to re-fetch the page.

#### (b) Get flow_map

**INVOCATION REQUIRED.** Invoke [`aurie-web-flow-snapshot`](../aurie-web-flow-snapshot/SKILL.md) as a sub-skill (do NOT inline its recipe by reading its `SKILL.md` and running its `aiweb-clone.sh` directly — invoke it as a skill). Pass through any user-supplied flags (`aiweb-ref`, `skip-aiweb-sync`, `skip-live-verify`).

Capture the sub-skill's `run_id` (an `fls-...` id), `flow_source`, `commit_sha` / `cloned_at` / `snapshot_captured_at` for inclusion in this skill's `sub_skill_runs.aurie-web-flow-snapshot` block. The orchestrator above this skill expects these — do not omit them.

Before flow-snapshot returns, observe its 🔧 entry banner and ✅ exit banner. If either banner is missing, halt with: *"flow-snapshot did not produce entry/exit banners — strict mode requires fresh sub-skill invocation. Re-invoke with explicit sub-skill call."*

Receive in memory:

```yaml
flow_map: [...]                  # variant-filtered, with per-screen shipped_features
flow_source: <value>
variant_decision: { ... }
snapshot_drift: { ... }
live_verification: { ... }
```

Print:

```
✅ Loaded Noom priors — 7 patterns, 6 anti-patterns
```

Then print the AIWeb sync outcome banner that `aurie-web-flow-snapshot` returns (Tier 1/2/3 summary + variant decision). The audit runs against the variant-filtered `flow_map` — every screen in it has `variant_decision.variant in screen.applicable_variants` (the sub-skill enforces this).

### Step 1 — Onboarding flow audit (per-screen drop-off opportunity analysis)

The structural `flow_map` from Step 0 tells us **what** the onboarding flow contains. This step tells us **where users could drop off** — the qualitative friction analysis that complements Step 2's quantitative Mixpanel data. It runs regardless of whether Mixpanel is available, so even history-only runs have grounded drop-off reasoning.

**Skip this step entirely if** the user passed `skip-audit` — set `flow_audit = null` and continue to Step 2.

The audit is **pure reasoning over the `flow_map` already in memory** — no further tool calls, no browsing, no file reads. It's a sequential walk: read each screen object in `flow_map` order (from `/signup` landing through `/get-app`) and produce structured per-screen friction analysis, plus a final pass for cross-flow concerns.

> **Variant-filtered input.** `flow_map` arriving here has already been filtered by R5.3 / Step 2b's filter to only screens whose `applicable_variants` contains `variant_decision.variant`. So a `webOnboardingV3` audit walks ~39 screens (33 quiz + 6 outer); a `webOnboardingVariant2` audit walks ~26 (19 quiz + 7 outer including Goal + Choose Fee). Never audit screens that the active variant doesn't traverse.

#### Audit scope (controlled by `audit-mode` flag)

| Mode | Screens audited |
|---|---|
| `full` (default) | Every screen in the variant-filtered `flow_map` |
| `focused` | The `focus=<screen-name>` screen (if specified) + the top 5 worst-Mixpanel-drop-off screens (when funnel data exists) + ALL untracked screens (where `mixpanel_event == null`) |

If `audit-mode=focused` is passed but Step 2 hasn't run yet, postpone the focused audit until after Step 2. (Full mode runs unconditionally before Step 2.)

#### Three-pass walk procedure

##### Pass 1 — Forward sequential read (`/signup` → `/get-app`)

Pre-flight assertion: confirm `flow_map` is variant-filtered. Every screen MUST have `variant_decision.variant in screen.applicable_variants`. If any screen fails this check, drop it and log a warning.

Iterate the variant-filtered `flow_map` in `screen_index` order (re-numbered contiguously after the filter). For each screen, write a `journey_audit` block covering `user_state_arriving`, `cognitive_load`, `decision_difficulty`, `exit_points`, `tracking_status`, `drop_off_opportunities`, `branching_impact`, `value_delivered_so_far`, `closest_value_signal`, and `noom_anti_pattern_check`. The full `journey_audit` output schema, the grading rubrics (`cognitive_load` / `decision_difficulty` / `risk_level`), the opportunity-reason rules, and the field-population rules for `tracking_status` and `noom_anti_pattern_check` (including the violation-vs-drop-off distinction) live in [`references/audit-method.md`](references/audit-method.md) — follow it verbatim when writing each block.

##### Pass 2 — Branching audit

After Pass 1, scan `flow_map` for screens where `branching_impact` is non-empty. For each branching screen, list the **dominant + 1 alternative path** and analyse:

```yaml
branching_audit:
  - branch_screen: <screen_name>
    branch_screen_id: <screen_id>
    forks:
      - option_picked: <verbatim option label>
        downstream_screens_added: <int>
        downstream_screens_skipped: <int>
        early_exit: true | false
        impact_summary: |
          <one-sentence explanation>
```

Limit to forks where `downstream_screens_added + downstream_screens_skipped >= 2` — micro-branches aren't worth the analysis weight.

##### Pass 3 — Cross-flow concerns

After Pass 1 and Pass 2, look at the flow holistically and surface concerns no single screen can show:

```yaml
cross_flow_concerns:
  - concern_id: "audit-flow-<seq>"        # e.g. audit-flow-001
    pattern: |
      <one-sentence pattern>
    affected_screens: ["<screen_name>", ...]
    risk_level: low | medium | high
    evidence: |
      <which structural facts about the flow_map support this concern>
```

**Patterns to look for** (not exhaustive):

- **Value-delivery distance** — how many screens until the user sees something useful?
- **Question fatigue zones** — consecutive screens of the same `cognitive_load` / `decision_difficulty` profile
- **Paywall position** — too early (no investment) vs. too late (sunk-cost helps but abandonment risk grows)
- **Interrupt placement** — where does the save-progress modal appear?
- **Branching detours** — which question's branches add the most screens?
- **Tracking-blind zones** — consecutive screens with `mixpanel_event: null`
- **Component repetition** — screens that look/feel identical to the previous one
- **CTA-copy mismatch** — screen content sells one thing but CTA promises another

#### Output — `flow_audit` object

Materialise the audit as a single in-memory object:

```yaml
flow_audit:
  audit_mode: full | focused
  flow_source: <copied from Step 0>
  audited_at: <ISO timestamp>
  total_screens_audited: <int>
  per_screen:
    - screen_index: 0
      screen_name: "Landing"
      journey_audit: { ... }                    # from Pass 1
    - screen_index: 1
      screen_name: "Q1 Primary Struggle"
      journey_audit: { ... }
      branching_audit: { ... }                  # only if Pass 2 found branching here
  cross_flow_concerns: [ ... ]                  # from Pass 3
  high_risk_screens: ["<screen_name>", ...]
  high_risk_concerns: ["<concern_id>", ...]
  noom_summary:
    total_violations: <int>                     # sum of journey_audit[*].noom_anti_pattern_check.violations
    high_severity_violations: <int>
    screens_with_violations: ["<screen_name>", ...]
    most_common_anti_patterns: [               # top 3 by frequency
      { anti_pattern: "<name>", count: <int>, screens: ["<screen_name>", ...] }
    ]
  tracking_summary:
    screens_with_event: <int>
    screens_without_event: <int>
    tracking_blind_screens: ["<screen_name>", ...]
    measurable_today_screens: <int>             # backfilled after Step 2
```

#### Print

```
🔎 Onboarding flow audit (audit-mode: full):
   Variant audited: <variant_decision.variant>   (source export: <variant_decision.source_export>)
   Audited <N> screens

   Per-screen friction summary:
     High-risk drop-off opportunities: <N> across <M> screens
     Medium-risk:  <N> across <M> screens
     Low-risk:     <N> across <M> screens

   High-risk screens:
     - <screen_name> — "<one-line reason>"
     [...]

   Noom anti-pattern violations:
     <total>: <high-severity> high, <medium-severity> medium, <low-severity> low
     Most common: <anti-pattern name> (<N> screens), <anti-pattern name> (<M> screens)

   Tracking coverage (from source-extracted Mixpanel events):
     <X> of <N> screens have a Mixpanel event resolved from source
     <Y> screens are tracking-blind:
       - <screen_name>
       [...]

   Cross-flow concerns (high-risk only — full list in flow_audit object):
     - audit-flow-<id>: <one-line pattern>
     [...]
```

### Step 2 — Mixpanel funnel data (graceful degrade)

**Probe whether the Mixpanel MCP is available.** Try a no-op call to a Mixpanel MCP tool. Three outcomes:

| Outcome | What to do |
|---|---|
| MCP responds successfully | Run the funnel query below. Set `funnel_status` accordingly. |
| MCP not configured / not authenticated | Print a one-line warning. Set `funnel_status: skipped`. |
| User passed `skip-mixpanel` | Skip the probe entirely. Set `funnel_status: skipped`. |

#### Funnel query (when MCP is available)

The funnel query runs on every invocation. Issue the Mixpanel call below, capture the response timestamp as `funnel_pulled_at`, and use those numbers for the worst-drop-off ranking. The only valid skip path is the `skip-mixpanel` flag (which sets `funnel_status: skipped`).

Build a Mixpanel funnel report with one step per non-null `mixpanel_event` from `flow_map`, in `screen_index` order. Use the lexicon already loaded from `flow_map` (resolved by R6/R7 in the sub-skill's `flow-extract.md`) — **don't query the Mixpanel API for the lexicon**. When the in-memory lexicon and `Tools/Mixpanel/Events` disagree on an event name for a given screen, prefer the source-derived value and surface the disagreement in the readout.

```yaml
project_id: 963821                       # Project Aura, workspace 30768
report_type: funnels
report:
  name: "Aurie Web onboarding funnel — by screen (run <run-id>)"
  steps:
    # one step per non-null mixpanel_event, in screen_index order
    - eventName: "AI Sign Up Start"
    - eventName: "AI Sign Up Screen Completed"
      filters:
        - propertyName: "Screen"
          operator: "equals"
          value: "Primary Struggle Screen"
    # ... continue for every flow_map entry where mixpanel_event is non-null
  filters:
    # MANDATORY internal-account exclusion
    - { type: string, propertyName: "$email", propertyType: string, resource: user, operator: "does not contain", value: "test" }
    - { type: string, propertyName: "$email", propertyType: string, resource: user, operator: "does not contain", value: "farhan" }
    - { type: string, propertyName: "$name",  propertyType: string, resource: user, operator: "does not contain", value: "test" }
  dateRange: { type: relative, last: 30, unit: day }
```

#### Internal-account exclusion (mandatory on every Mixpanel call)

Never query unfiltered Mixpanel data. The three filters above are required on every Mixpanel call this skill makes. If the MCP doesn't support all three at query time, post-filter results in code before using them.

Always log the filter was applied:

```
Mixpanel: filter applied — excluded users where email/name contains "test" or "farhan"
```

#### Failure modes

| `funnel_status` | Detection | Top-level warning |
|---|---|---|
| `skipped` | MCP unavailable or `skip-mixpanel` flag | `⚠️ No Mixpanel funnel data this run — audit grounded on Step 1 only.` |
| `lexicon_empty` | All `flow_map[*].mixpanel_event` are `null` | `⚠️ Flow snapshot has no resolved Mixpanel events.` |
| `lexicon_stale` | Some events sent, all returned 0 rows | `⚠️ All Mixpanel events returned 0 rows in last 30 days. Likely cause: events renamed in Mixpanel since last source extract.` |
| `partial` | Some events returned data, others didn't | `⚠️ Funnel partially built: <N> of <M> steps have data. Worst-drop-off ranking computed from steps with data only.` |
| `ok` | All sent events returned non-zero rows | (no warning) |

In every failure mode, **continue to Step 3** — never abort.

#### Worst drop-off computation (when funnel data is available)

For each step `i` (1-indexed), `drop_off_rate = 1 - (users_at_step_i / users_at_step_{i-1})`. Step 1 has no drop-off. Round to the nearest whole percent. Sort descending; ties broken by step position (later steps win).

#### Backfill `measurable_today` on Step 1's audit

After Step 2 returns, walk `flow_audit.per_screen[*].journey_audit.tracking_status` and set `measurable_today: true` for any screen whose `event_name` returned non-zero rows in the funnel. Mirror to `flow_audit.tracking_summary.measurable_today_screens`.

#### Print

```
Funnel (last 30 days, internal accounts excluded):
  1. Landing                4,127 users
  2. Q1 Primary Struggle    3,412 users  (-17%)
  3. Q2 Duration            2,891 users  (-15%)
  ...
Worst drop-off screens: <Screen 1>, <Screen 2>, <Screen 3>
```

When both Step 1 and Step 2 ran, annotate worst-drop-offs with audit corroboration:

```
Worst drop-off screens (with audit cross-check):
  1. <Screen 1>  -38%   ← also flagged high-risk by audit (audit-<id>)
  2. <Screen 2>  -29%   ← audit found medium-risk friction (audit-<id>)
```

### Step 3 — Instrumentation health

Compute from the snapshot's tracking-gap annotations + Step 2's funnel result.

```yaml
instrumentation_health:
  total_screens: <int>
  events_present: [...]            # mixpanel_event != null AND funnel returned non-zero
  events_missing: [...]            # mixpanel_event != null AND funnel returned zero
  events_unmapped: [...]           # mixpanel_event == null (snapshot tracking gaps)
  mapping_mismatches: [...]        # snapshot's "Mapping mismatch" notes
  coverage_pct: <events_present / total_screens, 0.0-1.0>
  recommended_action: none | refresh_lexicon | add_tracking | investigate
```

`recommended_action` decision table:

| Conditions | `recommended_action` |
|---|---|
| `coverage_pct >= 0.8` | `none` — funnel is healthy |
| `events_present == 0` AND `events_missing > 0` | `refresh_lexicon` — events exist in code but Mixpanel has no data |
| `events_present == 0` AND `events_unmapped > 0` AND `events_missing == 0` | `refresh_lexicon` — also surfaces a snapshot-refresh recommendation |
| `events_present > 0` AND `events_unmapped > 0` | `add_tracking` — partial coverage; the unmapped screens are real tracking gaps worth filing |
| `funnel_status == skipped` | `investigate` — coverage unknown, surface raw counts only |
| Anything else | `investigate` |

#### Print

When `coverage_pct < 0.5` or `funnel_status == skipped`, prepend a blocking-style warning:

```
⚠️ INSTRUMENTATION GAP — funnel coverage 27% (4 of <N> screens) [or "no funnel data this run"]
   Recommended action: <add_tracking | refresh_lexicon | investigate>
   Why this matters: <N> of <M> captured screens have no usable Mixpanel
   data, so most decisions made off this audit need tracking work first.
```

### Step 4 — Return artifact

Return the full bundle to the caller:

```yaml
audit_run:
  run_id: <aud-<8-char-hex> from Step 0 entry banner>
  invoked_by: <caller-skill-name | "user">
  invoked_at: <ISO timestamp from Step 0>
  sub_skill_runs:
    aurie-web-flow-snapshot:                   # from Step 0(b) — capture the nested sub-skill's run_id
      run_id: <fls-<8-char-hex>>
      invoked_at: <ISO>
      flow_source: <value>
      commit_sha: <sha | null if Tier 3>
      cloned_at: <ISO | null>
      snapshot_captured_at: <ISO | null>
  noom_priors_fetched_at: <ISO timestamp from Step 0(a) — freshness signal>
  funnel_pulled_at: <ISO timestamp from Step 2 | null if skip-mixpanel>
  flow_source: <inherited from Step 0(b)>
  variant_decision: { ... }                    # inherited
  flow_map: [...]                              # inherited (caller may need it; saves another sub-call)
  flow_audit: { ... }                          # from Step 1, including noom_anti_pattern_check per screen
  funnel:                                      # from Step 2
    funnel_status: ok | partial | skipped | lexicon_empty | lexicon_stale
    steps: [...]                               # per-screen user counts + drop-off %
    worst_drop_offs: ["<screen_name>", ...]    # top 3
  instrumentation_health: { ... }              # from Step 3
  noom_priors:                                 # from Step 0(a) — passed through so callers don't refetch
    applicable_patterns: [
      { id: "Pattern N — <name>", description: "<full text>" },
      ...
    ]                                          # 7 entries
    anti_patterns: [
      { id: "<name>", description: "<full text>" },
      ...
    ]                                          # 6 entries
    source_page_id: "35375e23af2080f8b297c912a3ca6ccf"
    fetched_at: <ISO timestamp>
```

After the artifact is built, always print the exit banner (whether standalone or as a sub-call):

```
✅ aurie-web-onboarding-audit complete (run_id: <same id from Step 0>)
   sub_skill_runs:
     aurie-web-flow-snapshot: <fls-...>
   noom_priors_fetched_at: <ISO>
   funnel_pulled_at: <ISO | n/a>
   flow_audit: <N> screens, <H> high-risk
   elapsed_ms: <int>
```

When invoked standalone (not as a sub-call), also print a final summary:

```
aurie-web-onboarding-audit run <run-id> complete:
  ✅ flow_source: <value>, variant: <variant>
  ✅ Flow audit: <N> screens, <H> high-risk, <C> cross-flow concerns
  ✅ Noom anti-pattern violations: <total> across <screens-with-violations> screens
  ✅ Mixpanel funnel: <ok | partial | skipped>, worst drop-offs: <...>
  ✅ Instrumentation: <coverage_pct>%, recommended action: <action>
```

## Output format

When invoked **as a sub-call** by another skill, return the `audit_run` object in memory; the caller prints what's relevant.

When invoked **standalone**, print Step 1's audit summary, Step 2's funnel table (or skip notice), and Step 3's instrumentation banner. Do not print the entire `flow_audit.per_screen` array — it's hundreds of lines. If the user wants a specific screen's audit, ask which one and print just that screen's `journey_audit`.

## Notes and gotchas

Behavioural edge cases and cross-step gotchas (grounding-source fallback, the twice-computed `measurable_today`, audit+funnel corroboration, `focused`-without-Mixpanel fallback) are in [`references/notes-and-gotchas.md`](references/notes-and-gotchas.md).

## References

### Bundled reference files

- [`references/audit-method.md`](references/audit-method.md) — the Step 1 Pass 1 `journey_audit` schema, grading rubrics, and field-population rules.
- [`references/notes-and-gotchas.md`](references/notes-and-gotchas.md) — behavioural edge cases and cross-step gotchas.

### Notion knowledge

- **`Noom Onboarding — UX Priors`** — page id `35375e23af2080f8b297c912a3ca6ccf`. Loaded in Step 0(a). The 7 Applicable Patterns + 6 Anti-Patterns are applied to every screen in Step 1's per-screen `noom_anti_pattern_check`. Also passed through unchanged in `audit_run.noom_priors` so caller skills (e.g. ideation) don't have to refetch the page. URL: <https://app.notion.com/p/aurahealth/Noom-Onboarding-UX-Priors-35375e23af2080f8b297c912a3ca6ccf>.
- **`Tools/Mixpanel/Events`** — canonical Aurie Web event lexicon. Cross-checked against the source-extracted lexicon in `flow_map`.
- **`Tools/Mixpanel/Super Properties`** — `Source App` + test-account exclusion-filter properties used by Step 2.

### Sub-skills

- [`aurie-web-flow-snapshot`](../aurie-web-flow-snapshot/SKILL.md) — called in Step 0 to obtain `flow_map`.

### Sibling skills

- `aurie-web-experiment-history` — independent peer; both feed `aurie-web-onboarding-experimentation-ideas`.
- `aurie-web-onboarding-experimentation-ideas` — top-level orchestrator that calls this skill in its main flow.

### External

- Mixpanel project: `963821` (Project Aura), workspace `30768`
- Live signup URL: <https://www.aurie.ai/signup>
