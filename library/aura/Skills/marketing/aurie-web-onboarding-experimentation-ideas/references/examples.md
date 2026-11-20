# Example invocations

Worked end-to-end examples of the `aurie-web-onboarding-experimentation-ideas` orchestrator for common flag combinations. Referenced from the SKILL.md "Example invocations" pointer.

### Default — fresh batch

> "ideate experiments for Aurie Web"

1. Step 0 loads brand voice + idea-schema (Noom priors load transitively via the audit sub-skill in Step 2).
2. Step 1 calls `aurie-web-experiment-history` → gets `experiment_state` + `experiment_history`.
3. Step 2 calls `aurie-web-onboarding-audit` → which calls `aurie-web-flow-snapshot` → returns full audit + funnel + flow_map.
4. Step 3 generates 5 ideas.
5. Step 4 dedupes (Pass A + Pass B).
6. Step 5 previews.
7. User picks `(2) write all to Learnings/Experiments`.
8. Step 6 creates 5 rows via Notion MCP.
9. Step 7 prints the final report.

### Focused on one screen

> "ideate 3 experiments for the Soft Mirror screen"

1. Same Steps 0–2, but `focus=Soft Mirror` flag flows through to the audit.
2. Audit narrows its high-risk list to the Soft Mirror screen + neighbours.
3. Step 3 generates 3 ideas all targeting that screen.
4. Steps 4–7 as normal.

### Dry run

> "ideate 5 experiments dry-run"

1. Same as default through Step 5.
2. Step 5 prints the preview and stops — no Notion writes (Step 6 skipped).

### History-only (no Mixpanel)

> "ideate 5 experiments skip-mixpanel"

1. `skip-mixpanel` flows through to `aurie-web-onboarding-audit`.
2. The audit's qualitative passes (Pass 1, 2, 3) still run; only its Mixpanel funnel step is skipped.
3. All ideas have `grounding_source: audit` (or `history` for the rare case audit was also skipped).
4. Step 5 instrumentation banner prints `funnel_status: skipped` with `recommended_action: investigate`.
