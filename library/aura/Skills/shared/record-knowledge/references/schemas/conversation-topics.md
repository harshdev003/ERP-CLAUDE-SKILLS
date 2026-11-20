# Schema — `Signals/Conversation Topics`

**Kind:** `snapshot` — dated, write-once child pages. `record-knowledge` rejects `action: update` on this path.

**Status:** stable. Derived from the existing migrated snapshot pages under `Signals/Conversation Topics/`. Update only if the producer (`data:fetch-conversation-topics`) changes its output shape.

Each write creates a new dated page under `Signals/Conversation Topics/` with BERTopic classifications from the Braintrust Conversation project.

**Which conversation path?** Five AI Knowledge paths hold conversation- or feedback-shaped entries and they are **not** interchangeable — each has a different grain, and the same fact belongs in exactly one. Route by grain per [`Guide/Placement Rules` → *Conversation & feedback entries: route by grain*](https://www.notion.so/35a75e23af20812ca987c2bcedf27dd7). This path takes a **window aggregate of topic mix** — Task / Sentiment / Issues distribution.

## Fields (the caller's `fields` object)

| Field | Type | Required | Notes |
|---|---|---|---|
| `producer` | string | yes | The tool that produced the snapshot (e.g. `Braintrust Topics`) — used in the page title |
| `snapshot_date` | ISO date | yes | Appears in the page title; drives idempotency |
| `window` | object | yes | `{ queried_since: ISO datetime, effective_window: string, pulled_on: ISO date }` |
| `task_topics` | list of `TopicRow` | yes | Topic distribution across Task facet |
| `sentiment_topics` | list of `TopicRow` | yes | Topic distribution across Sentiment facet |
| `issue_topics` | list of `TopicRow` | yes | Topic distribution across Issues facet. **May be empty** — the Issues facet is `no_match` for nearly all conversations, so a window with zero flagged issues is valid |
| `headline` | string | yes | One-paragraph `At a glance` summary (top Task / top Sentiment; top Issue only when `issue_topics` is non-empty, else "none flagged this window") |
| `sources` | list | yes | Per [base-schema](../base-schema.md); at minimum the Braintrust URL |

`TopicRow` is `{ pct: number, traces: integer, topic: string }`.

## Page title

`<producer> — YYYY-MM-DD snapshot`

Example: `Braintrust Topics — 2026-04-22 snapshot`

## Page body structure

The producer agrees to this exact body so `retrieve-knowledge` can parse snapshots without ambiguity:

```markdown
# At a glance
<headline>

## Window
- Queried: <queried_since> ... <pulled_on>
- Effective: <effective_window>
- Pulled: <pulled_on>

## Task — <N topics>, <M traces>
| % | Traces | Topic |
| -- | -- | -- |
| <pct>% | <traces> | <topic> |
...

## Sentiment — <N topics>, <M traces>
<same table shape>

## Issues — <N topics>, <M traces>
<same table shape>

# Related
- [Topics](<parent-url>) — rolling summary + all snapshots
- [Conversation](<grandparent-url>) — grandparent
- [Experiments](<learnings-experiments-url>) — topic shifts often motivate prompt experiments
- [Identity](<identity-url>) — brand voice that responses to these topics must respect

# Sources
- <per sources list>
```

## Validation rules

1. All required fields present.
2. `snapshot_date` is an ISO date and not in the future.
3. `task_topics` and `sentiment_topics` are non-empty lists. `issue_topics` **may be empty** — the Issues facet is `no_match` for nearly all conversations, so a zero-issue window is valid; render it as "none flagged this window".
4. Within each *non-empty* topic list: `pct` values are in `[0, 100]` and sum to roughly 100% (warn if outside 99–101 — rounding is expected; larger drift suggests miscounted traces). An empty `issue_topics` skips this check.
5. Within each topic list: `traces` values are non-negative integers.
6. `window.queried_since` ≤ `window.pulled_on`.
7. `sources` includes at least the Braintrust URL.

## Idempotency

Snapshot pages are write-once. `record-knowledge` lists children of `Signals/Conversation Topics` before writing and refuses if a page with the same title already exists. Callers wanting to "update" a snapshot should instead produce a new snapshot with a later `snapshot_date`.

## Example

```yaml
producer: "Braintrust Topics"
snapshot_date: "2026-04-22"
window:
  queried_since: "2026-03-23T22:41:00Z"
  effective_window: "last ~7 days of classified logs"
  pulled_on: "2026-04-22"
headline: "Top Task: **Process relationship and emotional safety concerns (36.4%)**. Top Sentiment: **Distress and hopelessness (30.6%)**. Top Issue (of 826 flagged traces): **Refused to engage (41.9%)**."
task_topics:
  - { pct: 36.4, traces: 35574, topic: "Process relationship and emotional safety concerns" }
  - { pct: 22.7, traces: 22176, topic: "Seek emotional support for chronic struggles" }
  # ...
sentiment_topics:
  - { pct: 30.6, traces: 26806, topic: "Distress and hopelessness" }
  # ...
issue_topics:
  - { pct: 41.9, traces: 346, topic: "Refused to engage" }
  # ...
sources:
  - label: "Braintrust Conversation project — Topics view"
    url: "https://www.braintrust.dev/app/Aura%20Health%201/p/Conversation/logs?v=Review+-+All&qs=topics"
    primary: true
```
