# ElevenLabs cost-snapshot methodology

Why the report leads with **credits** for the workspace totals and **duration × per-minute rate** for per-user $.

## Voice is not "just ElevenLabs cost" — read this first

Every ElevenLabs voice turn round-trips through our backend: Mobile → ElevenLabs Conversational AI → IntelligenceService (`POST /ai/openai/chat-completion`, ELabs treats us as an LLM provider) → OpenAI (`coach-prompter`, `gpt-5.2-chat-latest`). Side effects (memory every 10 user turns, end-of-conversation pipeline 2 min after last turn) are identical to text mode. Full architecture in [`Tools/ElevenLabs` → Request flow](https://www.notion.so/34c75e23af20819abea7d64a826b72f3).

**This skill measures only the ElevenLabs portion of voice cost.** The OpenAI portion (per-turn `coach-prompter` + memory/summary/profile side effects) lands in the `Conversation`/Memory/Summary/etc. OpenAI projects and is reported separately by `eng:report-openai-costs` and `eng:analyze-openai-cost-per-user`. Empirical per-voice-minute total (verified 2026-05-17 on the top user): **~$0.09/min ElevenLabs in-plan + ~$0.04/min OpenAI ≈ ~$0.13/voice-minute total cost**.

When someone asks "what does Aurie voice mode cost?", neither this skill nor the OpenAI cost skills answer that on their own — combine them, or use [`eng:report-top-conversation-users`](../../report-top-conversation-users/SKILL.md) which sums both providers per user.

## Why no daily $ for the workspace total

ElevenLabs bills on a **flat plan + overage** model, not metered per-day spend:

- The **plan** is a fixed monthly fee ($500/mo Business at time of writing). It doesn't change with daily usage — a slow day and a busy day cost the same in plan fees as long as we stay under quota.
- The **overage** (`current_overage.amount` on `/v1/user/subscription`) is a live total of dollars owed above plan, accruing as the period progresses.

There is no API surface that returns "yesterday's billable $". The only true daily metric is **credits consumed** (`/v1/usage/character-stats`, `metric=credits`). So the report leads with credits and surfaces the live plan + overage above — never invents a daily $ figure for the whole workspace.

This is consistent with `eng:analyze-elevenlabs-costs`'s 2026-05 rework to "actuals only" — that skill explicitly removed the projected-overage and credits→dollars conversion code paths because both were misleading. We follow the same discipline here.

## Why per-user $ is computed differently

For the top-5 ConvAI users, the report DOES compute per-user dollars — because we have authoritative per-call data:

- `call_duration_secs` from `/v1/convai/conversations/{id}` metadata — actual call length
- `metadata.charging.is_burst` — actual flag for whether the call billed at burst rate
- Per-minute rates from `Tools/ElevenLabs`: **$0.09/min in-plan**, **$0.19/min burst**

Per-user $ = `duration_secs / 60 × rate`. Both inputs are API actuals, no derivation. This matches `eng:analyze-elevenlabs-cost-per-user`'s methodology after [its 2026-05 actuals-only refactor](https://github.com/AuraHealth/AINative).

The per-user $ figures don't sum to the workspace bill — they're a per-call cost basis, not a slice of the plan/overage. Specifically:

- A workspace day where total ConvAI minutes × $0.09 = $300 would show $300 of per-user $ in the top-5 (and lower tail), but the workspace might bill $500/mo (plan, distributed across all 30 days = $16.67/day baseline) + $0 overage if under quota.
- When the workspace IS in overage, per-call burst minutes × $0.19 is an authoritative measure of the burst portion's marginal cost.

The two views answer different questions:
- "What did the workspace pay?" → plan + overage from `/v1/user/subscription`
- "How much did this user cost us?" → duration × per-min rate from per-call detail

## Why credits, not minutes, for workspace totals

`/v1/usage/character-stats` returns **credits**, not minutes. Credits are ElevenLabs' internal billing unit and map roughly to characters generated (for TTS) or seconds of audio output (for ConvAI). They're directly comparable across days and across models — minutes would require a model-specific conversion that varies.

For the per-user section we use minutes (more legible) because we have call-level `duration_secs` directly.

## Retention caveat for top-5

ConvAI conversation detail (which contains `duration_secs`, `userId`, `is_burst`) has **10-day retention** in the Aura workspace's ElevenLabs Privacy settings. After 10 days the detail endpoint deletes the transcript + dynamic variables, returning 404. Implications:

- Default window (yesterday PT) is always inside retention — top-5 works.
- `--window last-7d` is fully inside retention.
- Custom windows that extend past 10 days will show top-5 understating the back end of the window; the script prints a stderr warning when this happens.

The workspace credit totals come from `character-stats`, which has longer retention, so the headline + by-model + by-product sections are unaffected by ConvAI retention.

## Subscription-state annotation (Aura PaymentService)

For each top-5 user, the script calls `POST https://services.aurahealth.io/subscriptions/list/:userId` (auth: `Bearer ${AURA_API_TOKEN}`) with `{"includeDeleted": true}` and derives a state label from the returned rows. `includeDeleted=true` is intentional — without it, refunded / revoked subscriptions are hidden and a user who had a trial that was refunded looks identical to a never-premium user. We learned this on 2026-05-17 when the top-2 ElevenLabs spender showed as "Never premium" with `includeDeleted=false` but was actually "Refunded" (had a trial, refunded it, kept using the service).

State derivation (single short label, displayed in the top-5 table):

| Condition (on `type:"ai"` rows) | Label |
|---|---|
| `lifetime=true`, no `deletedAt` | AI Lifetime |
| `autoRenew=true`, `trial=false`, active | AI Paid |
| `autoRenew=true`, `trial=true`, active | AI Trial |
| `autoRenew=false`, `trial=true`, active | AI Trial (cancelled) |
| `autoRenew=false`, active (not trial) | AI Cancelled (active) |
| AI history, no active row, no deletions | AI Expired |
| Deleted row, `trial=true`, `autoRenew=false`, `\|deletedAt − expirationAt\| < 5 min` | AI Trial Expired |
| Deleted row, `deletedAt < expirationAt − 5 min` | AI Refunded |
| Deleted row, neither of the above | AI Revoked |
| Only `type:"content"` rows present | Content-only (Aura) / Content Expired (Aura) |
| Empty array | Never premium |

The state derivation prefers the AI dimension (Aurie) since the report is about Aurie ConvAI usage. A user with both AI and Content rows gets the AI label. When multiple deleted AI rows exist, the most-recently-deleted row determines the label.

**`cancelReason` / `cancelFeedback` are not surfaced** — they're PII-adjacent (user free-text) and the state derivation uses `expirationAt` + `autoRenew` + `lifetime` + `deletedAt` first. Per [ENGBACK-1081](https://linear.app/aura-health/issue/ENGBACK-1081/subscription-rows-surface-stale-cancelreason-cancelfeedback-while), `cancelReason` can be stale (populated on a row that's still active), so it's not a reliable signal anyway.

### Three causes of `deletedAt` — why we disambiguate

A `deletedAt`-set row can mean one of three things. The original implementation lumped them into a single `AI Refunded/Revoked` label, which broke cohort analysis. Discovered on 2026-05-17 when the report labelled a top-2 spender as `AI Refunded/Revoked`, but the actual row was:

```json
{"trial": true, "autoRenew": false,
 "expirationAt": "2026-05-17T02:52:42Z",
 "deletedAt":    "2026-05-17T02:53:09Z"}  // 27 seconds after expiry
```

That's a **natural trial expiration** after the user disabled autoRenew — *not* a refund. Three causes, three labels:

| Cause | Heuristic | Cohort meaning |
|---|---|---|
| **Trial expired naturally** | `trial=true` + `autoRenew=false` + `\|deletedAt − expirationAt\| < 5 min` | Price-sensitive trial user who didn't convert. Standard end-of-trial flow (Apple/Google/Aura auto-clean the row seconds after the trial ages out). |
| **Refunded** | `deletedAt < expirationAt − 5 min` (materially before natural expiry) | User requested Apple/Google refund mid-period, or chargeback. Also catches "refund-then-keep-using" abuse pattern. |
| **Revoked** | Everything else (admin/CS action, payment-failure cleanup, edge cases without `expirationAt`) | Rare fallback. |

The 5-minute window is generous against clock skew and cron-batch deletion delays. Verified 2026-05-17 against `KKBjkNBB…`: gap was 27 seconds. Tracked in [ENG-540](https://linear.app/aura-health/issue/ENG-540).

The `_classify_deletion(row)` helper in `scripts/report.py` returns `"trial_expired" | "refunded" | "revoked" | None`. Both `derive_state` (single label) and `derive_timeline` (per-row status) use it. Reusable across any skill that needs the same disambiguation.

## Subscription timeline (full per-user history)

The script also computes a **subscription timeline** for each top-5 user — one entry per subscription row, sorted by `createdAt` ascending. Surfaced only in `--json` output (not in the rendered markdown). Each entry:

```json
{
  "type": "ai" | "content" | ...,
  "provider": "stripe" | "aura" | "apple" | "google" | ...,
  "trial": true | false,
  "lifetime": true | false,
  "created_at": "2026-05-10T14:30:00Z",
  "purchase_at": "2026-05-10T14:30:00Z",
  "converted_at": null,                  // trial → paid transition timestamp
  "expiration_at": "2026-05-17T14:30:00Z",
  "deleted_at": null,                    // refund / revoke timestamp
  "auto_renew": true | false | null,
  "status": "active" | "cancelled-active" | "expired" | "refunded"
}
```

`status` is derived per row, using the same `_classify_deletion` heuristic as the single-label state derivation above:
- **active** — currently in-period; auto-renewing or lifetime
- **cancelled-active** — `autoRenew=false` but `expirationAt > now` (will expire soon)
- **expired** — `expirationAt <= now`, no `deletedAt`
- **trial-expired** — `deletedAt` set, classified as natural trial expiration (see ENG-540 table above)
- **refunded** — `deletedAt` set, classified as refund (deletion materially before expirationAt)
- **revoked** — `deletedAt` set, neither of the above (admin / payment-failure cleanup / edge cases)

This is more informative than the single state label for downstream agents — `data:analyze-user-behavior`, churn analysis, "user X had three trials and refunded one" pattern detection, etc.

**Reusable pattern.** Other skills that want the same per-user subscription timeline can copy the `fetch_subscription_rows` + `derive_state` + `derive_timeline` trio out of this script's `scripts/report.py`. Per AINative architecture principle 1 (skills are self-contained), each consumer ships its own copy rather than reaching into this skill's directory. If you find 3+ skills carrying copies, propose extracting to a shared helper (same pattern as `notion_rest.py`) — track in a follow-up Linear ticket.

## Graceful degrade without `AURA_API_TOKEN`

The skill is deliberately permissive about the Aura token. The full report still produces all six sections — the top-5 table just shows `(no state — AURA_API_TOKEN missing)` in the state column. That's a feature, not a bug: skill contributors without backend access shouldn't be blocked from running the report.

## Sanity-check against the sibling skills

The top-5 minutes + $ in this skill should match what `eng:analyze-elevenlabs-cost-per-user` produces for the same window (both walk the same `/v1/convai/conversations` endpoint with the same attribution extraction + rate logic). The workspace credit totals should match `eng:analyze-elevenlabs-costs` for the same window (both hit `/v1/usage/character-stats`). If either diverges by more than 1%, something's wrong.

## When NOT to use this skill

- **Are we on budget for the month?** → `eng:analyze-elevenlabs-costs` (billing-period-to-date is the right window, not yesterday).
- **Who are the top 20 / 50 spenders?** → `eng:analyze-elevenlabs-cost-per-user` with `--top 20`; this skill always shows 5.
- **Day-over-day heavy-hitter shifts?** → `eng:analyze-elevenlabs-cost-per-user --trend`.
- **Per-conversation drill-down (transcripts, ratings)?** → `data:analyze-user-conversations`.
