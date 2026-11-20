# Algorithm reference — `eng:report-livekit-user-cost`

## The pattern

```
1. Resolve the window (resolve_window.py, the canonical family copy).
2. Expand it to UTC dates at the store's daily grain:
   [from, to) when `to` lands on midnight, else [from, to] (the partial last day counts).
3. Query A: SELECT DISTINCT date FROM the store WHERE date in range
   → the dates the store has written (the `Other` row exists on every stored day,
     so any written day has at least one row).
4. Query B: SELECT date, <LIVEKIT_COST_COLUMNS>, livekit_minutes
   FROM the store WHERE user_id = @u AND date in range.
5. For each window date:
     not in A            → pending  (cost None, minutes None)
     in A, not in B      → stored, $0 (the user had no attributable LiveKit voice)
     in A and B          → stored, sum of LIVEKIT_COST_COLUMNS
6. total.cost_usd = sum over stored days, or null when no day is stored.
```

Both queries filter on the partition column (`date`), so they scan only the window's partitions.

## Why pending is a separate state

The store writes a day once, after it ends, and defaults to yesterday. So on any window reaching
today, the last day has no rows at all. Rendering that day as `$0.00` claims the user's LiveKit
voice cost nothing. The truth is that nothing has measured it yet. That is the exact failure the
card was written about: a canceller's same-day LiveKit session vanished into a low total.
`null` in the JSON makes a composing caller handle the state rather than add it as zero.

## Status values

| `status` | Meaning | `total.cost_usd` |
|---|---|---|
| `ok` | every window day is stored | number |
| `partial` | some days stored, some pending | number (stored days only) |
| `pending` | no window day is stored yet | `null` |
| `unavailable` | BigQuery unreachable — no `GCP_KEY`, or no `google-cloud-bigquery` | `null` |
| `error` | the query itself failed | `null` |

`unavailable` / `error` exit `2`; the other three exit `0`.

## JSON shape

```json
{
  "user_id": "<full UID>",
  "window": {"from": "...", "to": "...", "label": "...", "days_in": 30, "from_iso": "...", "to_iso": "..."},
  "status": "partial",
  "total": {"cost_usd": 0.0, "livekit_minutes": 0.0, "stored_days": 30, "pending_days": 1},
  "by_vendor": {"voice_livekit": 0.0, "voice_livekit_platform": 0.0, "voice_cartesia": 0.0,
                "voice_fish": 0.0, "voice_inworld": 0.0, "voice_deepgram": 0.0, "voice_11labs_livekit": 0.0},
  "pending_dates": ["2026-09-25"],
  "by_day": [{"date": "2026-09-24", "status": "stored", "cost_usd": 0.0, "minutes": 0.0},
             {"date": "2026-09-25", "status": "pending", "cost_usd": null, "minutes": null}],
  "notes": ["..."],
  "source": "aura-main.aurie_cost.aurie_per_user_daily_cost"
}
```

`total.cost_usd` and `notes` match the sibling legs' keys, so `eng:report-user-costs` reads all
four the same way.

## What this skill does not do

- It does not re-derive any allocation. Per-user dollars are what `store-aurie-user-daily-cost`
  booked, including its minute-share allocation and invoice-based LiveKit booking.
- It does not read the `_prod` view, so a dev-env UID still resolves.
- It does not fill pending days from live GCP logs (out of scope, see SKILL.md).
