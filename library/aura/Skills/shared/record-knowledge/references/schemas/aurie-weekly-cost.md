# Schema — `Signals/Aurie Weekly Cost`

**Kind:** `snapshot` — dated, write-once child pages. `record-knowledge` rejects `action: update` on this path.

**Status:** stable. Produced by [`eng:report-aurie-weekly-costs`](../../../../../eng/skills/report-aurie-weekly-costs/SKILL.md). Update only if that skill changes its output shape.

Each write creates a new dated page under `Signals/Aurie Weekly Cost/` capturing one Sun–Sat week's LLM + Voice cost row, the LLM-by-AI-premium-tier breakdown, and (optionally) the voice-by-AI-premium-tier breakdown.

Body is **verbatim Markdown** — the page body IS the rendered report (the spreadsheet row + TSV + tier tables). The headline fields below are extracted for fast retrieval / query without re-parsing the body.

This is the strict Sun–Sat companion to `Signals/Aurie Conversations`, which uses rolling windows. The two snapshot streams will overlap on cost; they answer different questions (recurring weekly bill vs. rolling engagement + cost).

## Fields (the caller's `fields` object)

| Field | Type | Required | Notes |
|---|---|---|---|
| `producer` | string | yes | Always `"Aurie Weekly Cost"`. Appears in the page title. |
| `snapshot_date` | ISO date | yes | The Sunday that **starts** the reported week (Pacific calendar). Appears in the page title; drives idempotency. |
| `week_label` | string | yes | Sun–Sat label (e.g. `"5/24 – 5/30"`). Matches the spreadsheet column. |
| `since_iso` | ISO datetime | yes | Window start (UTC midnight of the Pacific Sunday). |
| `until_iso` | ISO datetime | yes | Window end, half-open (UTC midnight of the following Sunday). |
| `headline` | string | yes | One-line `At a glance` summary — LLM $ + Voice $ + dominant tier. |
| `llm_cost_usd` | number | yes | Total OpenAI spend across all projects (`/v1/organization/costs`). |
| `voice_cost_usd` | number | yes | ElevenLabs `fiat_units_spent` ÷ 100. |
| `total_cost_usd` | number | yes | `llm_cost_usd + voice_cost_usd`. |
| `tier_breakdown_llm` | list | yes | Per-AI-premium-tier LLM spend rows. Each `{ tier, spend_usd, share_pct, active_users, per_user_usd }`. May contain an `(untagged)` row when window straddles or predates the 2026-05-12 rollout. |
| `tier_breakdown_voice` | list | optional | Same shape as `tier_breakdown_llm`, only present when the user opted into Step 3 of the producer skill. Includes a `(no OpenAI activity in window)` row for voice users without matching OpenAI metadata. |
| `tier_rollout_window_status` | enum | optional | `pre` / `mixed` / `post`. Set when the window touches the 2026-05-12 metadata-tagging rollout (ENG-498); informs how to read the `(untagged)` slice. |
| `body_md` | string | yes | The full rendered report Markdown (spreadsheet row + TSV + tier tables) — pasted verbatim into the page body, above the `# Sources` section. |
| `sources` | list | yes | Per [base-schema](../base-schema.md). Always include the producer skill path + OpenAI Costs API + ElevenLabs dashboard URLs. On a `--from-bigquery` write, swap the two live-API entries for the BigQuery table + `eng:store-aurie-user-daily-cost`. |

## Two tier taxonomies — don't normalise them

`tier` labels come in **two** shapes depending on which path the producer ran, and a snapshot must not be rewritten from one into the other:

- **Live-API path** — 7 buckets: `AI Premium Paid` / `Trial` / `Free` / `Payment Pending` / `Non AI Premium` / `Never AI Premium` / `(untagged)`, allocated by token weight from the Intelligence Service LLM-call ledger or the stored-completions `metadata.aiPremium*` tags.
- **`--from-bigquery` backfill path** — 5 buckets from the daily cost store's `subscription_state`: `Paid (AI Premium)` / `Trial` / `Free` / `Grace / payment pending` / `Other (unattributed / reconciling)`. These are exactly the `label` values in the producer's `--json` `by_subscription_state` array (`report.py`'s `BQ_STATE_LABELS`) — copy `label` rather than the raw `state` key, so this list and the script can't drift. `Other` is the store's reconciling remainder (`user_id='Other'`), not a user segment. Set `tier_rollout_window_status: "post"` — the ENG-498 rollout banner doesn't apply to this cut.

The two are **not comparable at the tier level** even though the top-line `llm_cost_usd` / `voice_cost_usd` agree (BigQuery voice runs ~1–2.5% under ElevenLabs' billed workspace total). Validation rules 7–8 below (shares sum to ~100%) hold for either shape; nothing else about the labels is enforced, so the enum is deliberately open.

BigQuery-mode writes populate `tier_breakdown_voice` **as well** — the store already carries per-user voice $, so that table doesn't depend on the producer's slow opt-in `/convai/conversations` walk the way the live path's does.

## Page title

`<producer> — YYYY-MM-DD snapshot`

Example: `Aurie Weekly Cost — 2026-05-24 snapshot` (for week of 5/24 – 5/30)

## Page body structure

```markdown
# At a glance
<headline>

## Window
- Week: <week_label> (Sun–Sat, Pacific calendar)
- Range: <since_iso> → <until_iso>
- Snapshot date: <snapshot_date>

## Spreadsheet row
<body_md verbatim — Markdown table + TSV line + LLM tier breakdown table>
<voice tier breakdown table appended when present>

# Related
- [Aurie Weekly Cost](<parent-url>) — rolling index + all snapshots
- [Aurie Conversations](<sibling-signals-url>) — rolling engagement + cost (overlapping on cost, broader scope)
- [Tools/OpenAI](<openai-tools-url>) — Costs API context; `metadata.aiPremium*` semantics
- [Tools/ElevenLabs](<11labs-tools-url>) — workspace usage endpoint; retention

# Sources
- <per sources list>
```

## Validation rules

1. All required fields present.
2. `snapshot_date` is an ISO date, falls on a Sunday, and is not in the future.
3. `since_iso` and `until_iso` are exactly 7 days apart (`until_iso - since_iso == 7d`).
4. `since_iso` matches `snapshot_date` at UTC midnight.
5. Dollar fields are non-negative numbers.
6. `total_cost_usd ≈ llm_cost_usd + voice_cost_usd` (warn if drift > 1¢).
7. `tier_breakdown_llm` shares sum to roughly 100% (warn outside 99–101).
8. When present, `tier_breakdown_voice` shares sum to roughly 100% (same warn band).
9. `tier_rollout_window_status` is one of `pre` / `mixed` / `post`, or omitted (treated as `post`).
10. `body_md` is non-empty.
11. `sources` includes at least the producer skill identifier (`eng:report-aurie-weekly-costs`) and one external dashboard URL.

## Idempotency

Snapshot pages are write-once. `record-knowledge` lists children of `Signals/Aurie Weekly Cost` before writing and refuses if a page with the same title already exists. A re-run for the same week (e.g. after a Costs API lag refresh) should NOT produce a second snapshot — point the caller at the existing page and offer to record only when explicitly different (e.g. backfilling an older week with `--since/--until`).

## Example

```yaml
producer: "Aurie Weekly Cost"
snapshot_date: "2026-05-24"   # Sunday
week_label: "5/24 – 5/30"
since_iso: "2026-05-24T00:00:00Z"
until_iso: "2026-05-31T00:00:00Z"
headline: "LLM $804.77 · Voice $766.18 · Total $1,570.95. AI Premium Paid + Trial = 67% of LLM ($542). Never AI Premium = 47% of Voice ($351) — voice leaks toward non-converting users harder than LLM does."
llm_cost_usd: 804.77
voice_cost_usd: 766.18
total_cost_usd: 1570.95
tier_breakdown_llm:
  - { tier: "AI Premium Paid", spend_usd: 310.89, share_pct: 38.6, active_users: 216, per_user_usd: 1.44 }
  - { tier: "AI Premium Trial", spend_usd: 230.72, share_pct: 28.7, active_users: 298, per_user_usd: 0.77 }
  - { tier: "AI Premium Free", spend_usd: 13.05, share_pct: 1.6, active_users: 46, per_user_usd: 0.28 }
  - { tier: "AI Premium Payment Pending", spend_usd: 5.34, share_pct: 0.7, active_users: 45, per_user_usd: 0.12 }
  - { tier: "Non AI Premium", spend_usd: 20.63, share_pct: 2.6, active_users: 344, per_user_usd: 0.06 }
  - { tier: "Never AI Premium", spend_usd: 205.89, share_pct: 25.6, active_users: 1315, per_user_usd: 0.16 }
  - { tier: "(untagged)", spend_usd: 18.25, share_pct: 2.3, active_users: 0, per_user_usd: 0 }
tier_breakdown_voice:
  - { tier: "AI Premium Paid", spend_usd: 173.68, share_pct: 23.0, active_users: 71, per_user_usd: 2.45 }
  - { tier: "AI Premium Trial", spend_usd: 191.82, share_pct: 25.4, active_users: 46, per_user_usd: 4.17 }
  # ...
tier_rollout_window_status: "post"
body_md: |
  **Week of 5/24 (Sun) → 5/30 (Sat)** — Pacific calendar week, UTC-aligned buckets

  | Week of | LLM costs | ... |
  ...
sources:
  - label: "Producer skill — eng:report-aurie-weekly-costs"
    url: "https://github.com/AuraHealth/AINative/tree/main/plugins/eng/skills/report-aurie-weekly-costs"
    primary: true
  - label: "OpenAI Costs API"
    url: "https://platform.openai.com/usage"
  - label: "ElevenLabs workspace usage"
    url: "https://elevenlabs.io/app/usage"
```
