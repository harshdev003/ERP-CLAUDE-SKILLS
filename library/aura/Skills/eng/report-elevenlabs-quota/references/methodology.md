# report-elevenlabs-quota — methodology

## Data source — actual quota, derived overage

Unlike `report-cartesia-quota` (Cartesia has no balance API, so quota is *derived* from consumption
vs a configured allotment), ElevenLabs exposes the quota directly from
`GET /v1/user/subscription`:

| Field | Meaning |
|---|---|
| `character_count` | Credits **used** this refresh period (the workspace counter) |
| `character_limit` | The **live** limit: the base grant plus every on-demand extension so far |
| `next_character_count_reset_unix` | When the counter resets (period end) |
| `character_refresh_period` / `billing_period` | Period label (`annual_period` / `monthly_period`) |
| `current_overage.amount` | Stays `"0"` on this plan (see below); a cross-check only |
| `next_invoice.subtotal_cents` | The base plan price (cents); extensions are billed separately as top-ups |
| `tier`, `status` | Plan identifier, subscription status |

"used / live limit / reset" are exact. Burn rate, days until overage and projected overage $ are
extrapolations (there is no per-day field on the subscription object). `current_overage.amount`
arrives as a string (`"0"`, `"123.45"`).

## The live limit moves; overage is measured against the base grant

ElevenLabs bills on-demand overage by **raising `character_limit`** in fixed blocks as usage reaches
it (1,212,120 credits, about $200, about one a day in the 10-13 → 11-13 period). So `used` never
passes the live limit and `current_overage.amount` stays `"0"` all period, while `next_invoice`
shows only the $990 base plan (ENG-1726, PROD-7658). Measuring overage as `used − limit` read $0
every day and the $500-step alert could never fire.

- **Base grant** (`base_credits`, with `base_source`):
  - `--base-credits` — set by hand.
  - `plan_price_at_rate` (default) — `next_invoice.subtotal_cents / 100 ÷ rate`, rounded to 1,000.
    On the Scale plan that is $990 ÷ $0.000165 = **6,000,000** credits. Cross-check: the
    extensions above it come to about the same dollars as the block count × $200.
  - `live_limit` — no rate could be read, so the old understating measure; the report says so.
- `overage_credits = max(0, used − base)`; **`overage_usd = overage_credits × rate`** (derived).
- `extension_credits = limit − base` (informational).
- `remaining`, `% used` and `days_until_overage` are against the base grant.

## Going past the limit is normal

Aura pays usage-based on-demand overage every month on purpose; there is no enterprise deal, and
voice keeps working past the limit (Jerome, 2026-10-30). PROD-7658 shows the Jul–Oct periods at
roughly twice the limit. So the limit is the point where on-demand billing starts, not a run-out
date, and the report and alert are framed around overage dollars.

## Burn rate and days until overage

`character_count` is cumulative since the last reset, so:

- `period_days` — inferred from the period label (annual → 365, else 30). Only used to size the
  elapsed window; the exact reset instant is the API's `next_character_count_reset_unix`.
- `last_reset ≈ reset − period_days`; `days_elapsed = now − last_reset`.
- `burn_per_day = character_count / days_elapsed`.
- **`days_until_overage = remaining / burn_per_day`**, or `0` once usage is past the limit.
- `projected_at_reset = used + burn_per_day × days_to_reset`.

## Projected overage $ (derived)

- `projected_overage_credits = max(0, projected_at_reset − base)`.
- **Rate** (`overage_rate_usd_per_credit`, with `overage_rate_source`), from
  `GET /v1/usage/character-stats` (`fiat_units_spent` in cents ÷ 100, over `credits`):
  - `current_period_spend` — this period, from the last reset to now. It has been a flat
    $0.000165/credit every period since July, the same price as the base plan.
  - `previous_period_spend` — the period before, when this period has no spend yet.
  - Neither available (the Usage API read failed) → overage falls back to `current_overage`, the
    projection is n/a, and the report still runs.
- `projected_overage_usd = projected_overage_credits × rate`, labelled derived wherever shown.

### Migration caveat

ElevenLabs ConvAI is the incumbent voice path being migrated to LiveKit, so burn will *fall* as the
migration proceeds. Treat the projection as a **current-pace worst case**, not a fixed forecast.

## Alert model (`--alert`)

- **Overage steps (default):** WARN once the derived `overage_usd` reaches `--overage-step-usd`
  ($500). `overage_step = floor(overage $ / step)` is in the payload, so a caller can alert once per
  new step. Overage below the first step, or at $0, is OK.
- **Opt-in thresholds** (off unless passed): `--warn-pct` / `--crit-pct` on % of base grant, and
  `--warn-runway-days` / `--crit-runway-days` on days until on-demand overage starts.
- A non-`active` subscription `status` adds a WARN.

The highest severity wins.

## Exit codes

Match `report-cartesia-quota` so a wrapper can gate on `$?`: `0`=OK; with `--alert`, **`10`=WARN /
`20`=CRITICAL** for the verdict, while `1` (API failure) and `2` (missing key) stay reserved for
actual failures — a wrapper reads `$? -ge 10` as a real alert.

## Auth

`ELEVENLABS_ADMIN_API_KEY` (preferred; workspace-scoped) or `ELEVENLABS_API_KEY` (fallback), sent as
the `xi-api-key` header. Read from the environment only — never printed, logged, or passed on argv.
