# Algorithm reference — `eng:report-listens-user-cost`

Short technical note on how the per-user listens cost is computed.
Loaded by the SKILL.md on demand.

## The pattern in one line

```
POST /payouts/listens/aggregate
  body: { since, until, groupBy: "userId", users: [<UID>], ... }
→ rows[0].cost_cents             # this user's authoritative coach-payout cost
→ rows[0].tier_breakdown         # per-tier listens + cents
```

PaymentService's `POST /payouts/listens/aggregate` natively supports a `users`
filter on the request body. The skill is a thin wrapper that POSTs with a
single-UID `users` array, parses the single row from the `rows` array, and
formats it for human reading. **Same rate logic as the monthly payout job
(`src/jobs/payout.js`)** — not an estimate.

See [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) for the
endpoint spec; canonical implementation in
[`eng:analyze-aura-cost-per-user`](../../analyze-aura-cost-per-user/SKILL.md).

## Per-listen `$` logic (server-side, from PaymentService)

A listen earns the coach money only if:

- `userContentListen.premiumPaid IS TRUE`
- `userContentListen.dailyCoachListenedCount <= 6`
- `content.type <> 'podcast'`
- `coach.podcaster IS NOT TRUE`
- `coach.deletedAt IS NULL`
- `content.deletedAt IS NULL OR content.deletedAt > userContentListen.createdAt`

Rate per qualifying listen (from `config.payout` in PaymentService):

| Tier | Rate | Condition |
|---|---:|---|
| `music` | 5¢ | `content.type IN ('music', 'soundscape')` |
| `lt500` | 6¢ | Non-music, `contentListenedCount < 500` |
| `lt2000` | 10¢ | Non-music, `500 ≤ contentListenedCount < 2000` |
| `lt5000` | 13¢ | Non-music, `2000 ≤ contentListenedCount < 5000` |
| `other` | 17¢ | Non-music, `contentListenedCount ≥ 5000` |

## Two CLI flags that flip rules

- `--ignore-daily-cap` — sets `respectDailyCap: false` in the request body.
  Useful for *"what if the cap were lifted?"* analyses; production behaviour
  always caps at 6 listens per (user, coach) per day.
- `--include-free-listens` — sets `requirePremiumPaid: false`. Brings free /
  trial / payment-pending listens into the result set. Their `cost_cents`
  is always `0` (they earn the coach nothing) but the listen count goes
  up, which is sometimes useful for behaviour analysis.

Both are off by default — the skill reports the same authoritative cost
the payout job would produce.

## Why this skill exists vs the rank-N sibling

`eng:analyze-aura-cost-per-user` ranks the top-N spending users and
includes a distribution-stats block. For a single-user lookup that's
overkill — the endpoint can already filter to one UID server-side. This
skill is a single POST, no client-side sorting, no enumeration of all
users. Roughly *seconds* instead of the rank skill's per-window overhead.

## What this skill does NOT do

- **Cohort analysis.** Use the rank-N sibling.
- **Cost forecasting.** Descriptive only.
- **OpenAI inference or ElevenLabs TTS cost.** Sibling skills handle
  those — `eng:report-openai-user-cost` and `eng:report-elevenlabs-user-cost`.
  LiveKit-path voice is `eng:report-livekit-user-cost`. The parent
  orchestrator `eng:report-user-costs` composes all four.
- **Refund / payout-cycle boundary edge cases.** A listen at 11:59 PM on
  the last of the month vs 00:01 AM on the 1st lands in different payout
  cycles; per-listen cost is identical so the answer is the same either
  way for THIS skill, but the rendered $ may not align with a specific
  monthly invoice if your window straddles a month boundary.

## Optional `--by-coach` breakdown

Makes a second POST with `groupBy: "coachId"` (still filtered to one
user). Returns one row per coach this user has listened to, with their
listen count and cost. Useful when a save-attempt or CX inquiry needs
"who is this user's favourite coach to listen to?" without running a
separate skill.
