# Analyzer Workers

Analyzers are **opt-in workers** that review the app alongside the main test.
They run on screen change (first visit per session only) via sub-agents spawned
by the main orchestrator. Each analyzer has one focused lens and writes findings
to its own file.

## Enabling analyzers

Set `analyzers` in the config (any combination, none by default):

```json
"analyzers": { "ui": true, "ux": true, "copy": false, "logs": true, "ideas": false }
```

## The analyzers

| Analyzer | Reviews | Input | Output |
|---|---|---|---|
| `ui` | layout, touch targets (≥44pt), overlaps, alignment, safe area, **contrast** | screenshot + frames | `findings_ui.json` |
| `ux` | flow, discoverability, dead-ends, unconfirmed destructive actions, form friction | screenshot + elements + exploration graph | `findings_ux.json` |
| `copy` | typos, tone, brand (`Aura` vs `Aurie`), placeholder text, apostrophe truncation | visible text strings + screenshot | `findings_copy.json` |
| `logs` | errors, warnings, exceptions, crashes in `app.log` | delta-only log lines (regex pre-filtered) | `findings_logs.json` |
| `ideas` | growth/retention/conversion proposals — features, friction, UX, copy, icons, navigation, paywall | screenshot + elements | `findings_ideas.json` → ingested into `Learnings/Ideas/Aurie` Notion via AINative `product:record-ideas` (cross-session dedup happens in Notion via `Fingerprint`) |

## Orchestration

1. Swift side (`AdHocSimTest.swift`) detects first-visit screen change and
   emits `state.analyzerTrigger = { analyzers: [...], screen, step }`.
2. Main agent reads the trigger on next state observation.
3. For each enabled analyzer: invoke `sim_bridge.py analyze <name>` → prints
   an input bundle on stdout.
4. Spawn one sub-agent per analyzer (`Agent` tool, `run_in_background: true`)
   with the prompt template from `Scripts/analyzers/prompts/<name>.md` + the
   bundle as input. Model per analyzer:
   - `ui` → `model: "haiku"` (layout classification)
   - `ux` → `model: "sonnet"` (flow/friction needs stronger reasoning)
   - `copy` → `model: "haiku"` (text pattern matching)
   - `logs` → `model: "haiku"` (log-line classification)
   - `ideas` → `model: "opus"` (open-ended product ideation benefits from the strongest model)
5. Sub-agent reviews, writes `findings_{name}.json`, exits. Main test loop
   continues without waiting.
6. End of run: `sim_bridge.py report` merges findings into `findings.md`.

## Severity rubric (same across all analyzers)

- **critical** — blocks a user action, breaks trust (wrong brand in headline,
  crash, 5xx on auth path, touch target unreachable)
- **major** — significant issue, likely drop-off or user confusion (misaligned
  primary CTA, unclear CTA, repeated errors in logs, tone wildly off)
- **minor** — cosmetic / stylistic (padding inconsistency, minor typo,
  one-off warning)

## Dedup & cost controls

- **First visit only** — an analyzer fires once per screen per session. Re-visits
  don't re-trigger.
- **Log deltas** — the log analyzer tracks a byte offset in `log_offset.txt`;
  each invocation only sees lines appended since last check.
- **Empty input skip** — if `sim_bridge.py analyze logs` returns
  `suspiciousLines: []`, skip spawning a sub-agent for it (nothing to review).
- **Haiku for classification, Sonnet for judgment** — `ui`, `copy`, `logs` run on Haiku (pattern-matching tasks). `ux` runs on Sonnet (flow and friction calls need stronger reasoning).

## Scope boundaries (no overlap)

Each analyzer explicitly does NOT flag what another analyzer covers:

- `ui` does NOT flag text typos or flow issues
- `ux` does NOT flag touch target sizes or text quality
- `ui` gets contrast **measured** for it — `analyzers/contrast.py` samples each element's frame out of the PNG and the bundle carries only the rows below their WCAG bar. The sub-agent never estimates a ratio, and never reports a `confidence: low` row (a gradient, a photo or a flat fill, where the foreground/background split is meaningless).
- `copy` does NOT flag layout or navigation
- `logs` does NOT flag anything visual

This prevents duplicate findings and keeps each worker's review surface small.

## Do NOT

- Do NOT run analyzers as `mode` values — `mode` is only `"goal"` or `"explore"`.
  The old `uiAnalysis`/`uxAnalysis`/`copyAnalysis` modes are removed.
- Do NOT let analyzer sub-agents touch the simulator, `action.json`,
  `state.json`, or `knowledge/`. They only write `findings_{name}.json`.
- Do NOT wait for sub-agents to finish before continuing the main loop —
  they run in the background.
- Do NOT enable all four unless doing a full audit. Each adds LLM cost per
  screen change.
