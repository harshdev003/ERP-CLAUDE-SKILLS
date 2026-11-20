---
name: fetch-coaching-credits
description: >
  Fetch the coaching credit balance and credit history for a user and coach pair from
  CoachService and print a readable summary — totalCredits, totalUsed, currentPeriodCredits,
  currentPeriodUsed, and the last N history entries (type, count, reason, createdAt). Takes a
  userId plus coachId and resolves the subscriptionId automatically. Built for CX
  investigations into missing credits, unexpected debits, and post-adjustment verification.
  Trigger on "fetch coaching credits", "check credit balance for user", "how many coaching
  credits does user have", "credit history for user", "coaching credit info for
  {userId}/{coachId}", "show credit balance", "credit transactions for user", "why did the
  user lose a credit", or a userId plus coachId with a question about their session credits.
  Do NOT use for adjusting credits (`eng:adjust-coaching-credits`), subscription details
  (`eng:fetch-coaching-subscription`), or full session diagnosis
  (`eng:investigate-coaching-session`).

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/Aura API     # backend host + auth conventions
    - Compliance/Privacy # PII handling — UID-only output
---

# Fetch Coaching Credits

Fetches credit balance and history for a user's coaching subscription by
resolving `subscriptionId` from `userId` + `coachId`, then calling the credits
list and credit history endpoints.

## Goal

Given a `userId` + `coachId`, display:
1. Current credit balance (from the credits list)
2. Recent credit history entries (from the history list)

## Inputs

- **`userId`** — Aura Firebase user ID
- **`coachId`** — CoachService coach ID
- **`--history-type`** _(optional)_ — filter history to `'credit'` or `'debit'` only; omit for both
- **`--history-limit`** _(optional, default 10)_ — number of history entries to show

If `userId` or `coachId` are missing, ask for both before doing anything.

## Required environment

- `AURA_API_TOKEN` — service-level Aura backend token. Missing token is a hard fail.

## Workflow

### 1. Resolve the subscriptionId

```
GET /coaching/subscriptions/:userId/:coachId
Authorization: Bearer $AURA_API_TOKEN
```

Extract `id` as `subscriptionId`. If not found, stop with `NO_SUBSCRIPTION`.

### 2. Fetch credit balance

```
POST /coaching/subscriptions/credits/list
Authorization: Bearer $AURA_API_TOKEN
Body: { "subscriptionId": "<subscriptionId>" }
```

### 3. Fetch credit history

```
POST /coaching/subscriptions/credits/history/list
Authorization: Bearer $AURA_API_TOKEN
Body: { "subscriptionId": "<subscriptionId>", "type": "<--history-type if set>" }
```

### 4. Display the summary

```
## Coaching Credits — <userId> → <coachId>

### Balance
| Field                | Value |
|----------------------|-------|
| totalCredits         | 6     |
| totalUsed            | 4     |
| available            | 2     |
| currentPeriodCredits | 3     |
| currentPeriodUsed    | 1     |

### Recent History (last 10)
| Date                 | Type   | Count | Reason                        |
|----------------------|--------|-------|-------------------------------|
| 2026-06-10T12:00:00Z | debit  | 1     | use                           |
| 2026-06-08T09:00:00Z | credit | 1     | manual correction             |
| 2026-06-01T00:00:00Z | credit | 3     | coaching                      |
```

### 5. Privacy

Only `userId` is PII. Do not surface full name, email, or phone. Follow
`Compliance/Privacy` redaction rules before sharing output in Slack or Notion.

## Output

Two tables — credit balance and recent history — or a clear error message
with the raw response if a call fails.

## Edge cases

- **No subscription (404 on step 1)** — stop with `NO_SUBSCRIPTION`. Suggest
  `eng:investigate-coaching-session` or `eng:check-user-receipts`.
- **No credits found** — report zero balance explicitly; do not treat as an error.
- **No history entries** — report "No history entries found" for the given filters.
- **Token missing** — stop before step 1 with a message pointing to
  `~/.claude/settings.json` → `env.AURA_API_TOKEN`.

## References

- Related: [`eng:fetch-coaching-subscription`](../fetch-coaching-subscription/SKILL.md) —
  fetch the subscription record itself.
- Related: [`eng:adjust-coaching-credits`](../adjust-coaching-credits/SKILL.md) —
  manually credit or debit sessions after reviewing the balance here.
- Related: [`eng:investigate-coaching-session`](../investigate-coaching-session/SKILL.md) —
  full session health diagnostic (subscription + credits + coach + appointments).
