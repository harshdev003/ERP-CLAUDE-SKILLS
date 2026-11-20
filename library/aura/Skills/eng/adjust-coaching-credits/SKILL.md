---
name: adjust-coaching-credits
description: >
  Manually credit or debit coaching session credits on a user's coaching
  subscription. Accepts a userId + coachId (resolves the subscriptionId
  automatically), a type ('credit' or 'debit'), a reason, and an optional
  count (defaults to 1). Shows the resolved request and asks for confirmation
  before executing against production. Trigger when the user says "adjust
  coaching credits", "add a session credit for user", "debit a coaching
  session", "refund a coaching credit", "manually credit coaching sessions",
  "give user X coaching credits", "remove coaching credit for user", or
  provides a userId + coachId and asks to add/remove/adjust credits. Do NOT
  use for: reading credit history (use `eng:investigate-coaching-session`),
  creating a subscription (use `eng:call-aura-api`), or adjusting Aura
  app-level premium credits (unrelated system).
metadata:
  version: "3.0.0"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/Aura API     # backend host + auth conventions
    - Compliance/Privacy # PII handling — UID-only output
---

# Adjust Coaching Credits

Manually credit or debit session credits on a coaching subscription. Wraps
`POST /coaching/subscriptions/credits/adjust` with a two-step flow: resolve
the `subscriptionId` from the user's `userId` + `coachId`, show the assembled
request, then execute after explicit confirmation.

## Goal

Given a `userId` + `coachId`, `type`, `reason`, and optional `count`, produce
and (on confirmation) execute the credit adjustment against production.

## Inputs

- **`userId`** — Aura Firebase user ID
- **`coachId`** — CoachService coach ID
- **`type`** — `'credit'` (add sessions back) or `'debit'` (remove sessions)
- **`reason`** — free-text reason stored in credit history (e.g. `"manual correction — session was incorrectly consumed"`)
- **`count`** _(optional, default 1)_ — number of credits to adjust

If `userId`, `coachId`, `type`, or `reason` are missing, ask for them before doing anything.

## Required environment

- `AURA_API_TOKEN` — service-level Aura backend token. Missing token is a hard fail.

## Workflow

### 1. Resolve the subscriptionId

Call:
```
GET /coaching/subscriptions/:userId/:coachId
```

Extract `id` from the response — this is the `subscriptionId` needed for the
adjust call. If the subscription is not found (404 / empty), stop and report
`NO_SUBSCRIPTION` — do not proceed to the adjust call.

### 2. Build the request

Assemble the adjust payload:
```json
{
  "subscriptionId": "<resolved from step 1>",
  "type": "<credit|debit>",
  "reason": "<user-supplied reason>",
  "count": <count, default 1>
}
```

Endpoint: `POST /coaching/subscriptions/credits/adjust`
Auth: `Authorization: Bearer $AURA_API_TOKEN`
Role required: `admin` or `agent`

### 3. Show and confirm

Display the resolved curl and a variable table:

```
| Variable        | Source            | Value                        |
|-----------------|-------------------|------------------------------|
| AURA_API_TOKEN  | shell env         | eyJh…lDw (masked)            |
| subscriptionId  | GET subscription  | <uuid>                       |
| type            | user-supplied     | credit                       |
| reason          | user-supplied     | manual correction            |
| count           | user-supplied     | 1                            |
```

Then ask:

> "Ready to run this **`<type>`** adjustment of **`<count>`** credit(s) for
> user `<userId>` against production? Reply `yes` to execute."

Only execute on `yes`. Any other reply — stop.

### 4. Execute and handle response

Run the curl. On success (`200`), print the updated credit record and confirm:

> "`<count>` credit(s) **<type>d** on subscription `<subscriptionId>`.
> Updated balance: totalCredits=`<n>`, totalUsed=`<n>`."

On error, surface the raw response with the status code.

### 5. Privacy

Only `userId` is PII. Do not print full email, name, or phone in output.
Follow `Compliance/Privacy` redaction rules before sharing output in Slack or
Notion.

## Output

A one-line confirmation with the updated credit counts, or a clear error
message with the raw API response if the call fails.

## Edge cases

- **No subscription found** — stop at step 1 with `NO_SUBSCRIPTION`. Suggest
  `eng:investigate-coaching-session` to diagnose further.
- **type validation fails** — the API returns `COACH_SUBSCRIPTION_CREDIT_INVALID_REQUEST`.
  Surface the error and remind the user that only `'credit'` and `'debit'` are valid.
- **Not enough credits to debit** — the API returns
  `COACH_SUBSCRIPTION_CREDIT_INVALID_REQUEST`. Surface the error and suggest
  checking current balance via `eng:investigate-coaching-session` first.
- **Token missing** — stop before step 2 with a clear message pointing to
  `~/.claude/settings.json` → `env.AURA_API_TOKEN`.

## References

- New endpoint: `POST /coaching/subscriptions/credits/adjust` (ENGBACK-1131)
- Parent: [ENGBACK-982](https://linear.app/aura-health/issue/ENGBACK-982)
- Related: [`eng:investigate-coaching-session`](../investigate-coaching-session/SKILL.md) —
  read credit balance and diagnose the full session state before adjusting.
- Related: [`eng:call-aura-api`](../call-aura-api/SKILL.md) — for arbitrary
  Aura API calls not covered by a dedicated skill.
