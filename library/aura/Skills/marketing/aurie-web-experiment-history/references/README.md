# aurie-web-experiment-history

A read-only skill that returns historical experiment context for **Aurie Web** (a.k.a. AIWeb / AI Web) — the dedup baseline + transferable pattern priors that ground new ideation in prior outcomes.

## What it does

1. **Reads the Aurie Web slice** of `Learnings/Experiments` (Notion DB `2cbe45a3246040658b9f20559b5b452b`, filter `Platform == "Aurie Web"`). Categorizes by Status, indexes by Title for O(1) dedup, extracts Won/Lost themes.
2. **Reads the Aura Web slice** (filter `Platform == "Aura Web"`). Theme-tags rows by inferred category (paywall, copy, social proof, etc.) and surfaces the top winning + losing patterns. Used as transferable priors only — never row-level reuse.
3. **Optionally cross-references against a `flow_map`** (when passed in) to identify under-tested screens.

## What it returns

```yaml
experiment_history_run:
  experiment_state:
    rows: [...]                     # full Aurie Web row objects
    by_title_index: { ... }         # lowercase Title → row (dedup)
    by_phase: { Idea: 12, Running: 3, Passed: 8, Failed: 11, ... }
    total: 47
  experiment_history:
    aurie_web:
      winning_patterns: [...]
      failure_patterns: [...]
      under_tested_screens: [...]   # only when flow_map was passed
    aura_web:
      total_rows: 165
      winning_themes: ["social_proof at decision points (5W / 1L)", ...]
      losing_themes: [...]
```

## When to use

The skill triggers on phrases like "what Aurie Web experiments have we tried", "show me Aurie Web experiment patterns", or "what Aura Web wins might transfer to Aurie Web". It also runs as a sub-call when `aurie-web-onboarding-experimentation-ideas` needs dedup data + pattern priors.

See `SKILL.md` → "When to use" for the full trigger phrase list.

## Repository layout

```
AINative/plugins/marketing/skills/aurie-web-experiment-history/
├── SKILL.md              # the skill itself (frontmatter declares Notion knowledge)
├── scripts/
│   └── notion_rest.py    # Notion REST helper (bulk Learnings/Experiments read)
└── references/
    └── README.md         # you are here
```

The core of this skill is a read against one Notion DB via `scripts/notion_rest.py`.

## Knowledge sources

```yaml
knowledge:
  reads:
    - Learnings/Experiments         # both Aurie Web + Aura Web slices
  writes: []
```

## Why a separate skill

The history reads were previously embedded inside `aurie-web-onboarding-experimentation-ideas` (Steps 1 + 5). Splitting them out:

- **Lets other skills reuse** the experiment context (e.g. portfolio review, weekly retros).
- **Keeps the ideation skill thin** — it just consumes `experiment_history_run`.
- **Sits naturally next to `data:analyze-experiment`** — both read `Learnings/Experiments`, with this skill specialised for the Aurie-Web-ideation lens.

## Sibling skills

| Sibling | Relationship |
|---|---|
| `aurie-web-flow-snapshot` | Independent peer — provides optional `flow_map` for under-tested-screens cross-reference |
| `aurie-web-onboarding-audit` | Independent peer — both feed ideation |
| `aurie-web-onboarding-experimentation-ideas` | Parent orchestrator — calls this skill in its main workflow |
| `data:analyze-experiment` | Sibling that *writes* to the same DB (post-mortem analysis) — this skill is read-only |

## Limitations

- **Aurie Web only.** Aurie iOS / Aurie Android / Aura Mac are out of scope.
- **No row-level Aura Web reuse.** The Aura Web slice is theme-tagged; concrete row reuse across surfaces is intentionally blocked.
- **`under_tested_screens` requires `flow_map`.** Standalone runs skip the cross-reference.

## External

- Notion DB: <https://www.notion.so/2cbe45a3246040658b9f20559b5b452b>
