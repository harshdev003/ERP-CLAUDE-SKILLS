# Prompt plan template

The skill's output. Fill every section; write "n/a" with a reason rather than dropping one.
Every decision row names its source (card comment, Figma note, code path, query result) so a
reader can check it.

```markdown
# Prompt plan: {feature} — `{configKey}`

**Feature:** {Linear link} · **Design:** {Figma link} · **Planned:** {date}

## Requirements
- {one line per requirement, with source}

## Decisions
| # | Decision | Choice | Why | Source |
|---|---|---|---|---|
| 1 | Trigger / cadence | … | … | … |
| 2 | Outputs per run | … | … | … |

## Open questions
| Question | Options | Recommendation | Owner |
|---|---|---|---|

## Starting point
| Candidate | Status (live / deleted / dead) | Take | Leave | Model · effort · tier · project |
|---|---|---|---|---|
**Chosen base:** {prompt + section}, because {reason}. Copy into a standalone class.

## Config
```js
{configKey}: {
  jsonOutput: true,
  key: '{configKey}',
  model: '…',
  name: '{kebab}-prompter',
  openaiAPI: 'responses',
  project: '…',
  reasoningEffort: '…',
  serviceTier: 'flex',   // omit for standard
  temperature: 1,
  useResponseConversation: false,
},
```
Tier: {flex|standard} because the call path is {entry point}.
Verify on first call: {model id, flex, strict JSON, effort}.

## Inputs
| # | Input | Builder (existing / new) | Window · cap | Why it's needed |
|---|---|---|---|---|
**Left out:** {builder} because {overlap or irrelevance}.

## Output schema
```json
{ … raw schema body … }
```

## Prompt content rules
- Base section to extend, and what changes.
- Categories / volume / zero-output rules.
- No-repeat rule and the input that enforces it.
- Content-safety rules.

## Eval plan
- Dataset scenarios (one line each).
- Variant comparisons and how to decide.

## Proposed execution steps
1. {step} — {what ships}
```
