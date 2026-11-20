# Cartesia quota estimation — methodology

## The problem: no public balance / plan API

Cartesia's dashboard shows two live numbers we care about — **Model Credits
Remaining** and **Voice Agent Dollars Remaining** — but there is **no public API
endpoint** that returns balance, plan, allotment, or renewal date. Every plausible
path 404s (verified against `https://api.cartesia.ai` with a valid admin key +
`Cartesia-Version: 2026-03-01`):

- `GET /balance` → 404
- `GET /subscription` → 404
- `GET /account` → 404
- `GET /plan` → 404
- `GET /billing` → 404

The only usage-facing endpoints that exist are the two consumption endpoints below.
So we cannot *read* the remaining balance — we **derive** it.

## What the API does expose

| Endpoint | Returns | Notes |
|---|---|---|
| `GET /usage/credits?start_ts=<RFC3339>[&interval=day]` | `{data:[{start_ts,end_ts,credits}]}` | Actual TTS + STT + Voice-Changer credit consumption. TTS = 1 credit/char, STT = 3 credits/sec, Voice Changer = 15 credits/sec. |
| `GET /usage/agents?start_ts=&interval=day` | `{data:[{start_ts,end_ts,cents,minutes,calls}]}` | Voice-agent calling. Agent calling $0.06/min, telephony $0.014/min. `cents / 100` = actual USD drawn from the Voice-Agent-Dollars pool. |

Headers on every call: `Authorization: Bearer $CARTESIA_ADMIN_API_KEY` +
`Cartesia-Version: 2026-03-01`. The key is read from the environment only — never
printed, passed on argv, or written to a file.

## The derivation

1. **Billing period** = `[most recent occurrence of the renewal day at or before
   today (UTC), now]`. Renewal day defaults to the one in `Tools/Cartesia` (`--renewal-day`), capped at
   1–28 so every month has that day (no end-of-month clamp).
2. **credits_consumed_this_period** = sum of `/usage/credits` over that window
   (this is the ACTUAL metered consumption).
3. **derived_remaining** = `allotment − consumed`. When negative, we're OVER
   allotment (overage billing applies).
4. **Burn rate** = `consumed / days_elapsed` (also a last-7-day average from the
   `interval=day` rows). **projected_period_end** = `consumed + burn × days_remaining`.
5. **Voice-Agent pool**: sum `/usage/agents` cents → USD spent this period, compared
   to the plan's prepaid agent-dollar figure.

## Why the derived number won't match the dashboard exactly

`derived_remaining` is an **estimate**, not a mirror of the dashboard. It will
diverge because none of the following are exposed by any API:

- **Rollover** — unused credits that carry into the next period.
- **Period-boundary alignment** — the dashboard's period may not start exactly at
  our assumed renewal-day UTC midnight (timezone, exact renewal timestamp).
- **Credit grants / promos / manual adjustments** applied on Cartesia's side.

Observed in practice: the **API-derived consumed figure ran ~400k credits ABOVE
what the dashboard reflected as consumed** for the same period. Treat the derived
remaining as directional. The **Cartesia dashboard is the source of truth** for the
exact remaining balance — the skill prints that caveat on every run.

## Plan tiers (config, not API-exposed)

| Plan | Monthly $ | Included credits | Prepaid agent $ |
|---|---|---|---|
| Free | $0 | 20,000 | $1 |
| Pro | $5 | 100,000 | $5 |
| Startup | $49 | 1,250,000 | $49 |
| Scale | $299 | 8,000,000 | $299 |
| Enterprise | custom | custom | custom |

These are flags with defaults (`--plan`, `--allotment`, `--renewal-day`,
`--overage-rate-per-million`) precisely because Cartesia doesn't return them.

**Current Aura config:** **Scale**, 8,000,000 credits/month ($299/mo), **overage
enabled**, renewal day as `Tools/Cartesia` → Plan & pricing states — the plan since the LiveKit voice
go-live (moved up from Startup, which was consistently over its 1.25M allotment).

## Overage

Cartesia does not publish the marginal cost per credit while over allotment. The
skill therefore leaves the overage cost **unknown** unless you pass
`--overage-rate-per-million <float>` (the true $/1M-credit rate). When over
allotment without that flag, the report states overage applies but that the rate is
unknown; with the flag it estimates the incremental dollar cost.

## Renewal-day assumption

The billing period is anchored to `--renewal-day` (default: the day in `Tools/Cartesia`) at UTC midnight.
If Cartesia's actual renewal falls on a different clock or timezone, the period
window is off by up to a day — another reason the derived figure is an estimate,
not an exact match. Override with `--renewal-day` if the plan's renewal date changes.

## Plan cost & overage — contract rates (Startup vs Scale)

Confirmed from cartesia.ai/pricing (2026-07-02). Cartesia bills **flat monthly fee + overage**
on credits beyond the plan's allotment (paid users enable overages to go negative; when off,
requests are blocked once credits run out). Overage rate varies by tier:

| Plan | Flat / mo | Included credits | Overage $ / 1M |
|------|-----------|------------------|----------------|
| Pro | $5 | 100k | $65 |
| Startup | $49 | 1.25M | $45 |
| Scale | $299 | 8M | $38 |

Monthly cost at consumption `C`: `flat + max(0, C − included)/1M × overage`.

The skill computes this for the current plan AND Scale at the projected monthly consumption, and
recommends Scale **only when it is actually cheaper** — being over the allotment is not enough.
At low overage volume Startup ($49 + $45/M) beats Scale's $299 flat: the **Startup→Scale
break-even is ≈ 6.81M credits/mo** (`1.25M + (299 − 49)/45 × 1M`). Below that, "stay on Startup,
overage is cheaper"; above it, "move to Scale, saves ~$X/mo". STT is 3 credits/sec, TTS 1
credit/char, Voice Changer 15/sec.
