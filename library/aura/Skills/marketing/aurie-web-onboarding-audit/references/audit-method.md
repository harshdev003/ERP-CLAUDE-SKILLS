# Step 1 Pass 1 — per-screen audit method

The full `journey_audit` output schema, grading rubrics, and field-population rules for the forward sequential read in Step 1 Pass 1 of `aurie-web-onboarding-audit`. For each screen in the variant-filtered `flow_map`, write a `journey_audit` block using the schema and rubrics below.

## `journey_audit` block schema

```yaml
journey_audit:
  user_state_arriving: |
    <one-sentence description of what the user has done up to this point and their likely emotional/cognitive state arriving at this screen>
  cognitive_load: low | medium | high
  decision_difficulty: low | medium | high
  exit_points:
    back_button_present: true | false
    can_skip: true | false
    close_modal_present: true | false
    redirect_after_complete: <url_path | null>
  tracking_status:
    has_event: true | false
    event_name: <string | null>
    breakdown: <object | null>
    measurable_today: true | false                 # backfilled after Step 2 runs
    instrumentation_recommendation: |
      <only when has_event=false; one-line: "Add tracking before any test on this screen — currently flying blind">
  drop_off_opportunities:
    - opportunity_id: "audit-<screen_id>-<seq>"   # e.g. audit-q5_daily_burden-001
      reason: |
        <specific, observable reason a user might bounce on THIS screen — not generic advice>
      risk_level: low | medium | high
      evidence: |
        <cite the screen field that triggered this concern: headline, options, components, branching, position-in-flow, etc.>
      measurable: true | false                    # mirrors tracking_status.measurable_today
  branching_impact:
    - "<plain-English description of how an option here changes the downstream flow length or path>"
  value_delivered_so_far: |
    <what tangible value the user has received from the product up to this screen>
  closest_value_signal: |
    <how many screens until the next value moment>
  noom_anti_pattern_check:
    violations:
      - anti_pattern: "<name from the 6 Noom anti-patterns, verbatim>"
        evidence: |
          <which screen field triggered the violation: headline, options, components, branching, etc.>
        severity: low | medium | high
    passes_clean: true | false                # true iff violations is empty
```

**Rubrics for `cognitive_load`:**

- `low` — single tap on a 2–5 option list, no typing, no reading > 1 sentence beyond H1
- `medium` — 5+ options, slider, or reading 2–4 sentences of body copy
- `high` — multi-input form, free-text typing, multi-step within one screen, or > 5 sentences of body copy

**Rubrics for `decision_difficulty`:**

- `low` — concrete factual selection (e.g. "How many hours of sleep?")
- `medium` — self-assessment with subjective scale ("How much does this affect you? 1–5")
- `high` — emotionally loaded ("What's your biggest fear?") or commitment ("Choose your trial fee")

**Rubrics for `risk_level`:**

- `high` — directly testable as a likely top-3 drop-off cause; should be cited in idea generation
- `medium` — plausible friction worth considering for an idea
- `low` — micro-friction; useful context but not idea-worthy on its own

**Rules for opportunity reasons:**

- Specific: cite the actual headline/option/component, not generic best-practice
- Single-screen: the reason must be locatable on THIS screen, not "the whole flow is too long"
- Falsifiable: state in a form a future test could disprove

**How to populate `tracking_status`:**

- Read `flow_map[i].mixpanel_event` (resolved by R6 + R7 in `aurie-web-flow-snapshot`'s `flow-extract.md`).
- If non-null → `has_event: true`, copy `event_name` and `breakdown`.
- If null → `has_event: false`, write a one-line `instrumentation_recommendation`.
- `measurable_today` defaults to `null` until Step 2 runs; backfill it after Step 2 returns.

**How to populate `noom_anti_pattern_check`:**

For every screen, walk the 6 Noom anti-patterns loaded in Step 0(a). For each anti-pattern, decide whether the screen exhibits it:

- **Match criteria:** the anti-pattern is concretely observable on this screen (e.g. "Stacked guilt-trip framing" — the screen's headline + body uses negative emotional framing without a balancing positive). Use the anti-pattern's full text from the Notion page as your matching criterion — don't paraphrase.
- **Severity rubric:**
  - `high` — the violation is the dominant feature of the screen (most users will encounter it directly)
  - `medium` — the violation is present but qualified (e.g. only on one of multiple options)
  - `low` — the violation is technically present but unlikely to materially affect drop-off
- **Empty violations** are common and expected — most screens pass clean. Set `passes_clean: true` and an empty `violations: []` array in that case.

A violation does NOT automatically become a `drop_off_opportunity`. The two are separate lenses:

- `drop_off_opportunities` are screen-specific friction reasons the audit identifies independently.
- `noom_anti_pattern_check.violations` are matches against a fixed taxonomy of bad-UX-pattern templates.

When a screen has both a `high`-severity anti-pattern violation AND a high-risk drop-off opportunity that cite the same evidence, that's a strong signal — surface it in Pass 3's cross-flow concerns.
