# Schema — `Signals/Aurie Conversations`

**Kind:** `snapshot` — dated, write-once child pages. `record-knowledge` rejects `action: update` on this path.

**Status:** stable. Produced by [`data:report-aurie-conversations`](../../../../../data/skills/report-aurie-conversations/SKILL.md). Update only if that skill changes its output shape.

Each write creates a new dated page under `Signals/Aurie Conversations/` capturing one run of the Aurie conversation report — engagement (conversations, active users, voice %, durations, turns) and full cost-to-serve (LLM + Voice + side-effects) over a resolved window.

Body is **verbatim Markdown** — the page body IS the rendered report. The headline fields below are extracted for fast retrieval / query without re-parsing the body.

**Which conversation path?** Five AI Knowledge paths hold conversation- or feedback-shaped entries and they are **not** interchangeable — each has a different grain, and the same fact belongs in exactly one. Route by grain per [`Guide/Placement Rules` → *Conversation & feedback entries: route by grain*](https://www.notion.so/35a75e23af20812ca987c2bcedf27dd7). This path takes a **window aggregate of engagement and cost-to-serve**.

## Fields (the caller's `fields` object)

| Field | Type | Required | Notes |
|---|---|---|---|
| `producer` | string | yes | Always `"Aurie Conversation Report"`. Appears in the page title. |
| `snapshot_date` | ISO date | yes | Date of the run (UTC). Appears in the page title; drives idempotency. |
| `window_label` | string | yes | Human-readable window label (e.g. `"Last 7 days"`, `"Last 30 days"`, `"2026-05-27 → 2026-06-03"`). |
| `since_iso` | ISO datetime | yes | Window start (UTC). |
| `until_iso` | ISO datetime | yes | Window end, half-open (UTC). |
| `headline` | string | yes | One-line `At a glance` summary. Surfaces top-line counts + voice% + per-conversation $. |
| `conversations` | integer | yes | Distinct coach `conversationId` count in the window. |
| `active_coach_users` | integer | yes | Distinct `metadata.user` for the `coach` prompter in the window. |
| `voice_pct` | number | yes | Percent of conversations that had voice (0–100). |
| `voice_users` | integer | yes | Distinct voice users in the window. |
| `voice_minutes` | integer | yes | Total ElevenLabs minutes consumed in the window. |
| `total_cost_usd` | number | yes | Full cost-to-serve for the window (LLM + voice + side-effects). |
| `llm_cost_usd` | number | yes | Conversation [Production] OpenAI project. |
| `voice_cost_usd` | number | yes | ElevenLabs spend for the window. |
| `side_effects_cost_usd` | number | yes | Sum of Memory + Summary + Profile + Journal + Notification + Feed projects. |
| `per_conversation_usd` | number | yes | `total_cost_usd / conversations`. |
| `body_md` | string | yes | The full rendered report Markdown (everything the producer printed for the user) — pasted verbatim into the page body, above the `# Sources` section. |
| `sources` | list | yes | Per [base-schema](../base-schema.md). Always include the producer skill path + the OpenAI + ElevenLabs dashboard URLs. |

## Page title

`<producer> — YYYY-MM-DD snapshot`

Example: `Aurie Conversation Report — 2026-06-03 snapshot`

## Page body structure

```markdown
# At a glance
<headline>

## Window
- Label: <window_label>
- Range: <since_iso> → <until_iso>
- Snapshot date: <snapshot_date>

## Report
<body_md verbatim — the producer's stdout, lightly cleaned of progress noise>

# Related
- [Aurie Conversations](<parent-url>) — rolling index + all snapshots
- [Aurie Weekly Cost](<sibling-signals-url>) — strict Sun–Sat cost row (overlapping on cost, narrower window)
- [Tools/OpenAI](<openai-tools-url>) — Conversation project + prompter inventory
- [Tools/ElevenLabs](<11labs-tools-url>) — ConvAI conversations + retention

# Sources
- <per sources list>
```

## Validation rules

1. All required fields present.
2. `snapshot_date` is an ISO date and not in the future.
3. `since_iso < until_iso`.
4. `voice_pct` ∈ `[0, 100]`.
5. Counts (`conversations`, `active_coach_users`, `voice_users`, `voice_minutes`) are non-negative integers.
6. Dollar fields are non-negative numbers.
7. `total_cost_usd ≈ llm_cost_usd + voice_cost_usd + side_effects_cost_usd` (warn if drift > 1¢).
8. `body_md` is non-empty.
9. `sources` includes at least the producer skill identifier (`data:report-aurie-conversations`) and one external dashboard URL.

## Idempotency

Snapshot pages are write-once. `record-knowledge` lists children of `Signals/Aurie Conversations` before writing and refuses if a page with the same title already exists. Callers wanting a fresh capture for the same calendar day should adjust the `window_label` and `snapshot_date` only if a genuinely new run with a different window has been produced.

## Example

```yaml
producer: "Aurie Conversation Report"
snapshot_date: "2026-06-03"
window_label: "Last 7 days"
since_iso: "2026-05-27T17:57:56Z"
until_iso: "2026-06-03T17:57:56Z"
headline: "1,317 conversations from 500 coach users; 41% had voice (274 users). Median 9.3 min / 11 turns. Voice ~46% of $1,698 cost — Memory pipeline is the largest side-effect ($207)."
conversations: 1317
active_coach_users: 500
voice_pct: 41
voice_users: 274
voice_minutes: 8643
total_cost_usd: 1697.56
llm_cost_usd: 510.03
voice_cost_usd: 777.86
side_effects_cost_usd: 409.67
per_conversation_usd: 1.289
body_md: |
  *Engagement*
  • 1,317 conversations · 500 active coach users
  • 41% had voice (536 conversations, 274 voice users); 59% text-only
  ...
sources:
  - label: "Producer skill — data:report-aurie-conversations"
    url: "https://github.com/AuraHealth/AINative/tree/main/plugins/data/skills/report-aurie-conversations"
    primary: true
  - label: "OpenAI Conversation project usage"
    url: "https://platform.openai.com/usage?project=proj_esFyd4eq2VIAjKUn69X1ZFfk"
  - label: "ElevenLabs workspace usage"
    url: "https://elevenlabs.io/app/usage"
```
