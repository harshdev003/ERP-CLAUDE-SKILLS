---
name: report-aurie-margin-users
description: >
  INTERNAL, UID-bearing per-user Aurie profitability table for ops — ranks users by amortized
  net subscription revenue minus variable LLM and voice cost, so ops can see exactly who we're
  losing money on. Two sections: top-N paying-but-unprofitable users (subscribed AND net below
  zero) and top-N most-negative users overall including free. Same amortization and
  net-revenue definition as `eng:chart-aurie-daily-margin`, at the per-user grain. Trigger on
  "top loss-making users", "which users are we losing money on", "unprofitable users",
  "per-user margin table", "who is costing us the most relative to what they pay", "paying
  users we're losing money on", or "report aurie margin users". Output contains Firebase UIDs
  — print to terminal or write to a local CSV only; NEVER publish via the Artifact tool and
  NEVER post to Slack without an explicit user ask. Do NOT trigger for aggregate or segment
  margin (`eng:chart-aurie-daily-margin`), or all-source cost for one known UID
  (`eng:report-user-costs`).

metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/BigQuery                 # aura-main.aurie_cost store access (GCP_KEY)
---

# Report Aurie Margin Users

Per-user contribution-margin ranking: for each real user (the `Other` reconciling row is
excluded), how much amortized net subscription revenue did they generate vs how much
variable cost (LLM + voice) did they incur, over the chosen window? Answers the ops
question "which specific users are we losing the most money on" — the per-user
companion to `eng:chart-aurie-daily-margin`'s aggregate daily view.

## Privacy — read before running

**This skill's entire output is Firebase UIDs ranked by dollar loss. That is the point —
it exists so ops can look up and act on specific accounts.** It is not aggregate-only
like the sibling `chart-aurie-*` / `analyze-*-cost-per-user` family.

- **INTERNAL USE ONLY.** Print to the terminal, or write to a local CSV file (`--csv`)
  that stays on the operator's machine. Both are fine.
- **NEVER pipe this output into the Artifact tool.** Artifacts are shareable web pages;
  a UID-bearing table must never become one.
- **NEVER post this output to Slack, Notion, or any other shared surface without an
  explicit user ask in the current conversation.** If asked to share a subset (e.g. "post
  the top 3 to #eng-budget"), confirm the exact rows and destination before doing it —
  don't default to sharing the full table.
- This mirrors the PII posture of the underlying cost stores: `aura-main.aurie_cost` is
  an internal warehouse table keyed by Firebase UID, not a public-facing dataset, and the
  `eng:store-aurie-user-daily-cost` / `eng:build-aurie-user-subscription` skills that
  populate it document the same boundary.

## When to use

- "top loss-making users"
- "which users are we losing money on"
- "unprofitable users"
- "per-user margin table"
- "who is costing us the most relative to what they pay"
- "paying users we're losing money on"

**Do NOT trigger for:**

- Aggregate/segment-level margin (paid contribution vs free drag, day over day) — use
  `eng:chart-aurie-daily-margin`.
- All-source dollar cost for one *already-known* UID — use `eng:report-user-costs`.
- Cohort payback / retention curves — use `eng:chart-aurie-cohort-payback` /
  `eng:chart-aurie-retention`.

## Prerequisites

`GCP_KEY` (BigQuery service-account JSON) + `google-cloud-bigquery`
(`python3 -m pip install --user google-cloud-bigquery`). No BigQuery MCP exists — this
is API-first by necessity, same as the rest of the `chart-aurie-*` / `eng:query-bigquery`
family (see CLAUDE.md → "API-first by necessity").

## Step 1 — Run the script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-margin-users/scripts/margin_users.py \
  --all --top 25
```

Flags:

- `--all` (default) — every day present in the cost data spine.
- `--last N` — most recent N days present in the store instead (overrides `--all`).
- `--fee-rate` — blended IAP store-fee rate applied to amortized gross revenue
  (default `0.30`, matching `eng:chart-aurie-daily-margin`).
- `--top N` — rows per section (default `25`).
- `--csv <path>` — also write the combined rows to a local CSV. Same INTERNAL/UID-bearing
  posture as stdout — don't drop this file anywhere shared.

## Step 2 — Read the output

Two ranked sections (ascending by `net` — most-negative first) plus a one-line total:

1. **Top-N paying-but-unprofitable users** — has at least one row in
   `aurie_user_subscription` (tier != `free/none`) AND `net < 0`. The actionable segment:
   these are users we've taken money from and are still losing money on.
2. **Top-N most-negative users overall** (incl. free) — the same ranking with no
   subscription filter, so a high-cost free/trial user who never converted can surface
   too.
3. A closing line: `N of M users are net-negative in this window; total net loss $X`.

Columns: `user_id` (full Firebase UID — see Privacy above), `tier`, `subscription_state`
(the user's latest state seen in the cost-table window: `paid` / `trial` / `grace` /
`free`), `amortized_net_revenue`, `cost`, `net`.

## Definitions (same as `eng:chart-aurie-daily-margin`)

```
amortized_net_revenue = SUM(amortized gross revenue active in window) * (1 - fee_rate)
cost                  = SUM(total_cost) from aurie_per_user_daily_cost (user_id != 'Other')
net                   = amortized_net_revenue - cost
```

Amortization is nominal-period, half-open `[purchase_at, purchase_at + period_days)`:

```
period_days = 365.25 (year) / 30.44 (month) /
              GREATEST(TIMESTAMP_DIFF(expiration_at, purchase_at, DAY), 1)  (fallback)
```

Keeping this identical to `chart-aurie-daily-margin/scripts/margin.py` is deliberate — the
two skills must agree on what "net" means so a user flagged here as unprofitable is
consistent with the aggregate daily/segment view. If you change one, change the other in
the same PR.

## Tier resolution

Tier comes from the user's **most recent subscription period** (by `purchase_at`) in
`aurie_user_subscription`, not from the query window — tier is a point-in-time fact about
the account, not a windowed aggregate. `package_id` is the full catalogue UUID; the
skill matches on its leading 8-hex-char segment (confirmed stable against live data):

| package_id prefix | tier |
|---|---|
| `b7b32619` | Aurie Lite |
| `9428b0ed` | Aurie Plus |
| `f727ce9a` | Aurie Growth |
| `4044faaf` | Aurie Flourish |
| `295a1ed6` | Aurie Unlimited-Internal |

Source of truth for this map: `eng:manage-ai-packages`'s `fetch_catalogue.py` (live
`/ai/packages/list`) — refresh the map in `margin_users.py` if the catalogue's tiers
change.

Legacy fallback for periods with `package_id IS NULL` (pre-tagging), by `product_id`:

- `ai_monthly_1999`, `ai_yearly_6999` -> `Plus (legacy)`
- anything else unmapped -> `Plus (legacy/unmapped)`

A user with cost but **no** subscription-period row at all is tier `free/none`.

## Notes & edge cases

- **"Paying" means ever-subscribed, not currently-paid.** A lapsed/cancelled user we're
  still serving cost against is exactly the segment ops wants surfaced, so the
  paying-but-unprofitable filter is `tier != 'free/none'`, not
  `subscription_state == 'paid'`. `subscription_state` (shown as its own column) is the
  user's latest state seen in the window and can legitimately read `trial` / `grace` /
  `free` for a tier'd user whose subscription has since lapsed.
- **Window vs tier scope mismatch is intentional.** `--last 7` narrows revenue/cost to
  the last 7 days present, but tier still reflects the user's most-recent-ever
  subscription period — a user who churned 60 days ago still shows their last tier.
- **Large output.** With no `--top` cap this could rank thousands of users; the default
  of 25 per section keeps stdout readable. Use `--csv` for a fuller export.
- **Aggregate-only sibling exists.** If the ask is "how's the whole free tier doing" or
  "trend of paid contribution over time" rather than "which specific accounts," redirect
  to `eng:chart-aurie-daily-margin` instead — it never emits a UID.

## References

- Daily/aggregate companion:
  [`eng:chart-aurie-daily-margin`](../chart-aurie-daily-margin/SKILL.md) — same
  amortization + net-revenue definitions, aggregate-only (never emits a UID).
- Revenue source:
  [`eng:build-aurie-user-subscription`](../build-aurie-user-subscription/SKILL.md) —
  builds `aurie_user_subscription` (incl. `package_id`/`product_id`, added under
  [PROD-3591](https://linear.app/aura-health/issue/PROD-3591)).
- Cost source:
  [`eng:store-aurie-user-daily-cost`](../store-aurie-user-daily-cost/SKILL.md) — builds
  `aurie_per_user_daily_cost`.
- Package catalogue: [`eng:manage-ai-packages`](../manage-ai-packages/SKILL.md)'s
  `fetch_catalogue.py` — source of truth for the packageId -> tier map.
- All-source single-UID cost lookup:
  [`eng:report-user-costs`](../report-user-costs/SKILL.md).
- Linear: [PROD-3593](https://linear.app/aura-health/issue/PROD-3593) (this card),
  [PROD-3592](https://linear.app/aura-health/issue/PROD-3592) (the daily-margin
  sibling), [PROD-3591](https://linear.app/aura-health/issue/PROD-3591) (package_id/
  product_id added to `aurie_user_subscription`).
