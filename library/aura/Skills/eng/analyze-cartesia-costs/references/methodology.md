# Cartesia cost methodology

How `analyze-cartesia-costs` turns Cartesia's usage API into a spend report, and
why one dollar column is **actual** while the other is **derived**.

## The credit model

Cartesia is our TTS (text-to-speech) provider. It meters TTS in **credits** —
roughly **1 credit per synthesised character**. Credits are the ground truth:
`GET /usage/credits` returns exactly what Cartesia recorded for each day, so the
credit column of the report is never estimated.

There is, however, **no per-request USD figure** in the usage API for TTS. To
put a dollar value on TTS consumption we multiply credits by the plan's
**effective per-credit rate**:

```
TTS USD (derived) = credits / 1,000,000 × rate_per_million
```

This is a *plan-rate approximation*, not a bill. It answers "what are these
credits worth at our plan's rate?" — useful for budgeting and trend-watching —
but the actual Cartesia invoice is the flat plan price plus any overage, not
this figure. The report labels the TTS dollar column **derived** everywhere so
it is never misread as an invoice. (Same philosophy as
`eng:analyze-elevenlabs-costs`, which hides ElevenLabs' retail-equivalent
figure for the same reason.)

## The default rate

The default `--rate-per-million` is **38.00** — the Cartesia **Scale** plan rate
($38 per 1M credits, list). We are on Scale ($299/mo, 8M credits/mo) as of the
LiveKit voice go-live; $38/M is the marginal/list rate and the real negotiated
contract overage rate is still TBD. List rates are from cartesia.ai/pricing.

Override it with `--rate-per-million <usd>` on a plan or contract-rate change (e.g.
the Startup tier's $45/1M overage) or to model a what-if. The value is echoed in every report headline and
in the `--json` payload (`rate_per_million_usd`) so the derivation is auditable.

## Plan tiers & overage rates (contract, 2026-07-02)

Cartesia bills **flat monthly fee + overage** on credits beyond the allotment
(paid users enable overages to go negative; blocked when off). While over
allotment the **overage** rate is the marginal cost — that's what this skill's
default uses.

| Plan | Monthly flat | Credits included | Overage $/1M |
|---|---:|---:|---:|
| Free | $0 | 20,000 | — (blocked) |
| Pro | $5 | 100,000 | $65 |
| Startup | $49 | 1,250,000 | $45 |
| Scale | $299 | 8,000,000 | **$38** ← current |
| Enterprise | custom | custom | custom |

Pass `--rate-per-million` with the row that matches our plan's overage rate.
For full monthly cost (flat + overage) and the Startup→Scale break-even, use
`eng:report-cartesia-quota`.

## Why agent $ is actual but TTS $ is derived

Cartesia has a second usage surface — **agent calling** (Cartesia's own voice
agent orchestration) — exposed at `GET /usage/agents`. Unlike TTS, that endpoint
returns a **`cents`** field directly, so:

```
Agent USD (actual) = cents / 100
```

is a real billed figure, reported as **actual**. We currently use **LiveKit**
for voice orchestration and Cartesia only for TTS, so agent usage is typically
0 — but it is included so the report stays correct (and flags the spend) the
moment any agent calling happens. Minutes and calls are shown alongside for
context.

So the two dollar columns come from different places:

- **TTS $** — no billed figure exists in the API; **derived** from credits × plan rate.
- **Agent $** — the API returns cents directly; **actual**.

## Windows

All windows are **UTC**. Named windows (`yesterday`, `today`, `last-7d`,
`last-30d`) anchor to UTC midnight so day buckets line up with the API's own day
boundaries. `--since`/`--until` accept either a bare `YYYY-MM-DD` (interpreted as
UTC midnight) or a full RFC3339 string, and are converted to the `start_ts` /
`end_ts` the API expects. The script always requests `interval=day` and sums
client-side, so partial-day windows and the per-day table both behave correctly.

## The admin key

`/usage` endpoints require the **admin** key (`sk_car_admin_...`) in
`CARTESIA_ADMIN_API_KEY`. A regular `sk_car_` inference key returns **401** on
`/usage`. The key is read from the environment only — never printed, never
passed in argv, never written to any file (including the `--json` payload).
