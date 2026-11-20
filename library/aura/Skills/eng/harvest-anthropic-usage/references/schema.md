# `aura-main.anthropic_usage` schema

Three tables. All day-partitioned, all replaced wholesale per partition so a
re-harvest is idempotent.

## `daily_user_usage`

Partitioned by `usage_date`, clustered by `user_email, product, model`.
One row per (day × user × product × model). The Claude Tag rows add a
`slack_channel_id` dimension, so a user/product/model can legitimately repeat
within a day with different channels.

| Column | Type | Meaning |
|---|---|---|
| `usage_date` | DATE | UTC calendar day. Partition key. |
| `user_email` | STRING | Work email. **Blank for org-service rows** (e.g. Claude Tag acting for the org rather than a person) — keep the blank, don't coalesce it at write time. |
| `account_uuid` | STRING | Stable per-person id in the claude.ai org. |
| `user_id` | STRING | `user_…` tagged id. Same person, different id space. |
| `product` | STRING | `Claude Code`, `Chat`, `Cowork`, `Claude Tag`, `Claude in Chrome`. |
| `model` | STRING | Exact model string, e.g. `claude-opus-5`. Joins to `model_pricing.model`. |
| `slack_channel_id` | STRING | Claude Tag only. |
| `teams_channel_id` | STRING | Claude Tag only. |
| `requests` | INT64 | API request count. |
| `prompt_tokens` | INT64 | **All input tokens** = uncached + cache read + both cache writes. |
| `completion_tokens` | INT64 | Output tokens. |
| `uncached_input_tokens` | INT64 | Input that missed cache. Priced at full input rate. |
| `cache_read_tokens` | INT64 | Cache hits. Typically ~98% of all tokens. |
| `cache_write_5m_tokens` | INT64 | 5-minute cache writes. 1.25× input rate. |
| `cache_write_1h_tokens` | INT64 | 1-hour cache writes. 2× input rate. |
| `web_search_count` | INT64 | Server-side web searches. |
| `net_spend_usd` | NUMERIC | **Overage only.** `0` for usage inside the seat allowance. |
| `gross_spend_usd` | NUMERIC | Before discounts. Also overage-only. |
| `harvested_at` | TIMESTAMP | When this partition was written. |

**The load-bearing fact about this table:** the token columns are complete —
they cover usage inside the seat allowance as well as beyond it — while the two
dollar columns cover only overage. A user can show billions of tokens and
`$0.00`. Any report that ranks people by `net_spend_usd` is ranking who
overflowed their personal cap, which is a different question from who uses
Claude.

`prompt_tokens` is a total, not a sibling of the four detail columns. Summing
all five double-counts input.

## `member_seat`

Partitioned by `as_of_date`, clustered by `user_email`. A full snapshot of org
membership per harvest day.

| Column | Type | Meaning |
|---|---|---|
| `as_of_date` | DATE | Snapshot day. |
| `user_email` | STRING | Work email. Join key to usage. |
| `display_name` | STRING | Name as set in claude.ai. |
| `role` | STRING | `Primary owner`, `Owner`, `Admin`, `User`. |
| `status` | STRING | `Active`, `Pending`. |
| `seat_tier` | STRING | `premium` or `standard`, lowercased. |
| `harvested_at` | TIMESTAMP | When written. |

Seat tier exists **only** here. It is not in the usage export, so any per-tier
analysis requires this join. Departed members simply stop appearing on later
snapshots rather than being marked — take the latest snapshot for "who is on the
plan now", or the snapshot nearest a date for a historical view.

## `model_pricing`

Partitioned by `effective_from`, clustered by `model`.

| Column | Type | Meaning |
|---|---|---|
| `effective_from` | DATE | First day this rate applies. |
| `model` | STRING | Joins to usage. |
| `input_usd_per_mtok` | NUMERIC | Uncached input, per million tokens. |
| `output_usd_per_mtok` | NUMERIC | Output. |
| `cache_read_usd_per_mtok` | NUMERIC | Usually 0.1× input; some models differ. |
| `cache_write_5m_usd_per_mtok` | NUMERIC | 1.25× input. |
| `cache_write_1h_usd_per_mtok` | NUMERIC | 2× input. |
| `verified_on` | DATE | When a human last checked the price page. |
| `source` | STRING | Where they were read from. |

**Hand-entered, and dated for a reason.** There is no pricing API, so nothing
here refreshes itself. A stale rate yields a plausible number rather than an
error, which is the hard kind of wrong to notice. Check `verified_on` before
quoting derived value outside the team.

To change a rate, insert a row with a later `effective_from`. Never edit an
existing row — historical months must stay priced at the rate then in force.

## Valuing usage

```sql
SELECT
  u.user_email,
  SUM(u.prompt_tokens + u.completion_tokens) AS all_tokens,
  SUM((u.uncached_input_tokens * p.input_usd_per_mtok
     + u.cache_read_tokens     * p.cache_read_usd_per_mtok
     + u.cache_write_5m_tokens * p.cache_write_5m_usd_per_mtok
     + u.cache_write_1h_tokens * p.cache_write_1h_usd_per_mtok
     + u.completion_tokens     * p.output_usd_per_mtok) / 1000000) AS api_value_usd,
  SUM(u.net_spend_usd) AS billed_usd
FROM `aura-main.anthropic_usage.daily_user_usage` u
LEFT JOIN (
  SELECT model, input_usd_per_mtok, output_usd_per_mtok, cache_read_usd_per_mtok,
         cache_write_5m_usd_per_mtok, cache_write_1h_usd_per_mtok, effective_from,
         LEAD(effective_from) OVER (PARTITION BY model ORDER BY effective_from)
           AS effective_to
  FROM `aura-main.anthropic_usage.model_pricing`
) p
  ON  p.model = u.model
  AND u.usage_date >= p.effective_from
  AND (p.effective_to IS NULL OR u.usage_date < p.effective_to)
WHERE u.usage_date BETWEEN '2026-09-01' AND '2026-09-17'
GROUP BY 1 ORDER BY api_value_usd DESC
```

Two things in that join are load-bearing, and both fail silently if dropped.

**Bound each rate by a validity window.** `USING (model)` alone works today
because every model has exactly one rate row — and doubles every valuation the
moment a second one is added, by matching both. The `LEAD` window makes the
per-model ranges disjoint so exactly one matches.

**Match the rate in force on the usage date**, not the newest one. Picking the
latest rate per model reprices all of history the day a rate changes, which
defeats the reason prices are effective-dated at all. Neither mistake errors;
both just return a plausible wrong number.

**The `LEFT JOIN` is also deliberate**: an unpriced model must show as a null
value, not silently vanish from the ranking. If `api_value_usd` is null for a row
with real tokens, add that model to `model_pricing`.

## `harvest_log`

Partitioned by `harvest_date`. One row per day successfully read, **including
days with no usage**.

| Column | Type | Meaning |
|---|---|---|
| `harvest_date` | DATE | The day that was read. |
| `rows_loaded` | INT64 | Usage rows written for it. `0` is normal and meaningful. |
| `tokens` | INT64 | Total tokens that day. |
| `users` | INT64 | Distinct users with activity. |
| `source` | STRING | `api` (session key) or `download` (browser export). |
| `harvested_at` | TIMESTAMP | When it was read. |

This table is the answer to "which days do we actually have". Do not derive that
from `daily_user_usage` — a zero-row partition and a never-written partition are
indistinguishable there, so an idle Saturday and a missed harvest look the same.
