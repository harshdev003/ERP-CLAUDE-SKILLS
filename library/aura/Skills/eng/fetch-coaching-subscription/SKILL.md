---
name: fetch-coaching-subscription
description: >
  Fetch the coaching subscription details for a user + coach pair from CoachService and print
  a readable summary — subscriptionId, status, type, createdAt, cancelledAt, and package info.
  Takes a userId + coachId and calls `GET /coaching/subscriptions/:userId/:coachId`. Built for
  CX and engineering investigations into coaching access issues — whether a subscription
  exists, its current state, and when it was created or cancelled. Trigger when the user says
  "fetch coaching subscription", "get subscription for user", "does user have a coaching
  subscription", "check coaching subscription for {userId}/{coachId}", "subscription state for
  user", "coaching sub info", "is user subscribed to coach", or provides a userId + coachId
  and asks about their subscription. Do NOT use for: reading credit balances (use
  `eng:fetch-coaching-credits`), adjusting credits (use `eng:adjust-coaching-credits`), or
  full session diagnosis (use `eng:investigate-coaching-session`).

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/Aura API     # backend host + auth conventions
    - Compliance/Privacy # PII handling — UID-only output
---

# Fetch Coaching Subscription

A thin wrapper around `GET /coaching/subscriptions/:userId/:coachId` that
returns a readable summary of a user's coaching subscription state.

## Goal

Given a `userId` + `coachId`, fetch and display the subscription record —
its ID, status, type, package, and key dates.

## Inputs

- **`userId`** — Aura Firebase user ID
- **`coachId`** — CoachService coach ID

If either is missing, ask for both before doing anything.

## Required environment

- `AURA_API_TOKEN` — service-level Aura backend token. Missing token is a hard fail.

## Workflow

### 1. Fetch the subscription

```
GET /coaching/subscriptions/:userId/:coachId
Authorization: Bearer $AURA_API_TOKEN
```

### 2. Display the summary

Print a readable table:

```
## Coaching Subscription — <userId> → <coachId>

| Field           | Value                                  |
|-----------------|----------------------------------------|
| subscriptionId  | <uuid>                                 |
| status          | active / cancelled / expired           |
| type            | coach                                  |
| packageId       | <uuid>                                 |
| createdAt       | 2026-01-15T10:30:00Z                   |
| cancelledAt     | — (or timestamp if cancelled)          |
```

### 3. Privacy

Only `userId` is PII in this context. Do not surface full name, email, or
phone. Follow `Compliance/Privacy` redaction rules before sharing output in
Slack or Notion.

## Output

A markdown table with the subscription fields, or a clear `NO_SUBSCRIPTION`
message if the record is not found.

## Edge cases

- **Subscription not found (404)** — report `NO_SUBSCRIPTION` and suggest
  `eng:investigate-coaching-session` for a full diagnostic or
  `eng:check-user-receipts` to verify payment.
- **Token missing** — stop before the call with a message pointing to
  `~/.claude/settings.json` → `env.AURA_API_TOKEN`.

## References

- Related: [`eng:fetch-coaching-credits`](../fetch-coaching-credits/SKILL.md) —
  read credit balances for this subscription.
- Related: [`eng:adjust-coaching-credits`](../adjust-coaching-credits/SKILL.md) —
  manually credit or debit sessions.
- Related: [`eng:investigate-coaching-session`](../investigate-coaching-session/SKILL.md) —
  full session health diagnostic (subscription + credits + coach + appointments).
