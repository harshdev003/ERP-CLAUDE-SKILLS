# aurie-web-onboarding-audit

A diagnostic skill that walks the **Aurie Web onboarding/paywall flow** and tells you where users are dropping off — both quantitatively (Mixpanel funnel) and qualitatively (per-screen friction analysis).

## What it does

1. **Loads Noom UX priors** (7 patterns + 6 anti-patterns) from the `Noom Onboarding — UX Priors` Notion page.
2. **Gets `flow_map`** by calling `aurie-web-flow-snapshot`.
3. **Walks the flow per-screen** in three passes:
   - **Pass 1 — Forward sequential read.** For each screen, produces a `journey_audit` with cognitive load, decision difficulty, exit points, drop-off opportunities, branching impact, tracking status, **and a Noom anti-pattern check** against all 6 anti-patterns.
   - **Pass 2 — Branching audit.** For screens whose options route to materially different paths.
   - **Pass 3 — Cross-flow concerns.** Patterns no single screen reveals (paywall position, value-delivery distance, question-fatigue zones, etc.).
4. **Pulls Mixpanel funnel data** when available (gracefully degrades if not).
5. **Computes instrumentation health** — coverage %, tracking-blind screens, recommended action.

The Noom priors are passed through unchanged in the returned `audit_run.noom_priors` so caller skills (e.g. ideation) can use them without refetching the page.

## What it returns

```yaml
audit_run:
  flow_source: "live-github-clone @ a3f9c1d"
  variant_decision: { variant: "webOnboardingV3", ... }
  flow_audit:
    audit_mode: full
    total_screens_audited: 39
    per_screen: [...]                      # 39 entries with journey_audit (incl. noom_anti_pattern_check) + branching_audit
    cross_flow_concerns: [...]
    high_risk_screens: ["q5 Switching off at night", "Subscribe", ...]
    noom_summary: { total_violations: 12, high_severity_violations: 3, ... }
    tracking_summary: { screens_with_event: 32, screens_without_event: 7, ... }
  funnel:
    funnel_status: ok | partial | skipped
    steps: [...]
    worst_drop_offs: ["Subscribe", "q14 Past patterns", "Get App"]
  instrumentation_health:
    coverage_pct: 0.82
    recommended_action: add_tracking
  noom_priors:
    applicable_patterns: [ ... ]           # 7 entries, passed through to ideation
    anti_patterns: [ ... ]                 # 6 entries
```

## When to use

The skill triggers on phrases like "audit Aurie Web onboarding", "where are the worst drop-offs", or "check Aurie Web analytics coverage". It also runs as a sub-call when `aurie-web-onboarding-experimentation-ideas` needs grounded drop-off data.

See `SKILL.md` → "When to use" for the full trigger phrase list.

## Repository layout

```
AINative/plugins/marketing/skills/aurie-web-onboarding-audit/
├── SKILL.md      # the skill itself (frontmatter declares Notion knowledge)
└── references/
    └── README.md   # you are here
```

This skill has no `references/` or `scripts/` of its own — it composes `aurie-web-flow-snapshot` for the flow data and Mixpanel for the funnel.

## Knowledge sources

```yaml
knowledge:
  reads:
    - Noom Onboarding — UX Priors       # 7 patterns + 6 anti-patterns applied per screen
    - Tools/Mixpanel/Events             # event lexicon cross-check
    - Tools/Mixpanel/Super Properties   # exclusion filters
  writes: []
```

## Sibling skills

| Sibling | Relationship |
|---|---|
| `aurie-web-flow-snapshot` | Sub-skill — called in Step 0 to obtain `flow_map` |
| `aurie-web-experiment-history` | Independent peer — both feed ideation |
| `aurie-web-onboarding-experimentation-ideas` | Parent orchestrator — calls this skill in its main workflow |

## Limitations

- **Single variant per run.** The audit walks whatever variant `aurie-web-flow-snapshot` resolved (Notion-first, source-heuristic fallback). Auditing two variants requires two runs.
- **No browser-based verification of dynamic copy.** Quiz-step copy is trusted to source extraction; live SSR pages are spot-checked by the sub-skill.
- **`focused` mode requires Mixpanel.** Without funnel data, the skill falls back to `full` mode and audits every screen.

## External

- Mixpanel project: `963821` (Project Aura), workspace `30768`
- Live signup URL: <https://www.aurie.ai/signup>
