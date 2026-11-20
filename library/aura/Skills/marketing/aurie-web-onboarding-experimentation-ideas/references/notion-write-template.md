# Per-idea Notion write template

The conceptual `notion-create-page` call shape used by Step 6 to write one approved idea to `Learnings/Experiments`. Field mapping and required fields stay in SKILL.md Step 6; this file holds the full body-block template. The 9 idea-specific fields follow the body-stuffing template in [`idea-schema.md`](idea-schema.md).

#### Per-idea Notion MCP call (conceptual shape)

```yaml
notion-create-page:
  parent_database_id: "2cbe45a3246040658b9f20559b5b452b"
  properties:
    Title: <experiment_name>
    App: "Aurie"
    Platform: "Aurie Web"
    Experiment Type: ["Onboarding"]
    Status: null
    Start Date: <today ISO>
    Hypothesis: <hypothesis>
    Variants: |
      a: control
      b: <one-line summary of proposed_change>
  children:
    - heading_2: "What was tested"
    - paragraph: |
        [PROPOSED — not yet built]
        Phase: Idea
        Experiment Type (aurie-web-onboarding-experimentation-ideas enum): <experiment_type>
        Target Screen: <target_screen>
        Target Metric: <target_metric>
        Priority: <priority>
        Difficulty: <difficulty>
        Current Copy / Component: <current_copy_or_component>
        Proposed Change: <proposed_change>
        Noom patterns cited: <noom_patterns_cited>
        Anti-pattern check: <anti_pattern_check>
        Audit grounding (Step 2):
          - opportunity_ids: <comma-separated opportunity_ids>
          - concern_ids: <comma-separated concern_ids>
          - cited reason: <verbatim reason text from the audit>
        Funnel grounding: <Mixpanel event name + drop_off_pct, or "n/a — Mixpanel skipped">
        Historical grounding: <historical_grounding>
        Grounding source: <funnel | audit | history>
    - heading_2: "Result"
    - paragraph: "_Pending — experiment not yet shipped._"
    - heading_2: "Learning"
    - paragraph: "_Pending — to be added after experiment ends._"
    - heading_2: "Follow-up experiments"
    - paragraph: "_To be added._"
    - heading_1: "Sources"
    - bulleted_list_item: "ideate run <run-id> — flow_source: <flow_source value> (e.g. 'live-github-clone @ a3f9c1d ref=main' or 'snapshot Captured: 2026-04-29')"
    - bulleted_list_item: "Variant audited: <variant_decision.variant> (source export: <variant_decision.source_export>); basis: <variant_decision.basis>; <variant_decision.basis_detail>"
    - bulleted_list_item: "Onboarding flow audit (aurie-web-onboarding-audit) — audit_mode: <full | focused | skipped>; audited_at: <ISO timestamp>"
    - bulleted_list_item: "Mixpanel — Aurie Web onboarding funnel (last 30 days, internal accounts excluded)"  # only if funnel data was used
    - bulleted_list_item: "Prompt + model: model=<model>, prompt=<first-200-chars>"
```
