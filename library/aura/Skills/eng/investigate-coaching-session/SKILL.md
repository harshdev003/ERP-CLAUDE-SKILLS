---
name: investigate-coaching-session
description: >
  Diagnose a user's coaching session state across CoachService — subscription presence, credit
  balance, coach bookability, and recent appointment status — and return a one-screen verdict:
  NO_SUBSCRIPTION, OUT_OF_CREDITS, COACH_NOT_BOOKABLE, APPOINTMENT_UNCONFIRMED, CANCELLED or
  HEALTHY. When the verdict is fixable, emits a ready-to-run copy-paste fix command.
  Read-only: it produces fix commands but never writes to production. Trigger on "investigate
  coaching session", "why can't {userId} book with coach {coachId}", "diagnose coaching for
  user {uid}", "user missing coaching credits", "coach not bookable for {coachId}", "coaching
  subscription check", "session count wrong for {uid}", "coaching triage", "check coaching
  state", or a userId plus coachId with a question about booking, scheduling, credits or
  subscription state. Do NOT use for aggregate coaching analytics, editing coach availability,
  or arbitrary Aura API exploration (`eng:call-aura-api`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/Aura API     # backend host + auth conventions
    - Compliance/Privacy # PII handling — UID-only by default
---

# Investigate Coaching Session

Hand a `userId` + `coachId`. The skill correlates five CoachService
read endpoints and returns a one-screen verdict — the same correlation that
today requires hand-running 4–5 endpoints and cross-referencing the results.

## Goal

Given one `userId` + `coachId`, return a single verdict word
(`HEALTHY` / `NO_SUBSCRIPTION` / `OUT_OF_CREDITS` / `COACH_NOT_BOOKABLE` /
`APPOINTMENT_UNCONFIRMED` / `CANCELLED`) plus, for any fixable verdict, the
exact copy-paste API call needed to resolve it. Optionally drill into a
specific appointment by also providing `--appointment-id`.

## Inputs

- **`userId`** — Aura Firebase user ID (long alphanumeric string)
- **`coachId`** — CoachService coach ID
- **`--appointment-id`** (optional) — investigate one specific appointment
  rather than the most-recent match

If the caller did not provide `userId` and `coachId`, ask for both before
doing anything.

## Required environment

- `AURA_API_TOKEN` — service-level Aura backend token (see
  `/CLAUDE.md` → "Secrets and environment"). Required for all CoachService
  endpoints. Missing token is a hard fail.

## Workflow

### 1. Resolve inputs

Accept `userId` and `coachId` from the caller's message. If either is absent,
ask: *"Which userId and coachId should I investigate?"* — and wait.

### 2. Run the script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-coaching-session/scripts/investigate.py \
  <userId> <coachId> [--appointment-id <id>] [--json] [--quiet]
```

The script makes these calls in order:

1. `GET /scheduling/coaches/:coachId` — checks `bookable` flag and
   `resourceId` (calendar connected). A missing `resourceId` means OnSched
   can't find slots even if `bookable=true`.
2. `GET /coaching/subscriptions/:userId/:coachId` — checks whether a
   subscription exists and its state.
3. `POST /coaching/subscriptions/credits/list` (body: `{"userId": "<uid>"}`) —
   checks credits remaining.
4. `POST /coaching/subscriptions/credits/history/list`
   (body: `{"userId": "<uid>"}`) — last few credit transactions for context.
5. `POST /scheduling/appointments/list` (body: `{"userId": "<uid>", "coachId":
   "<coachId>"}`) — recent appointments, status, confirmation state.
6. If `--appointment-id` provided: `GET /scheduling/appointments/:appointmentId`
   for the full detail of that specific appointment.

### 3. Verdict taxonomy

| Verdict | Condition | Fixable? |
|---|---|---|
| `HEALTHY` | Sub active, credits > 0, coach bookable + has resourceId, no unresolved appointment issue | No fix needed |
| `NO_SUBSCRIPTION` | No subscription found for this user/coach pair | Manual — create via the coaching subscription flow |
| `OUT_OF_CREDITS` | Subscription active but 0 credits remaining | ✅ `credits/adjust` |
| `COACH_NOT_BOOKABLE` | Coach `bookable=false` OR no `resourceId` | ✅ `PUT /coaches/bookable` or coach calendar setup |
| `APPOINTMENT_UNCONFIRMED` | Most recent (or named) appointment is pending/unconfirmed | ✅ `PUT /appointments/:id/confirm` |
| `CANCELLED` | Most recent (or named) appointment is cancelled | ✅ `PUT /appointments/:id/reschedule` |

Priority order when multiple conditions are true:
`COACH_NOT_BOOKABLE` > `NO_SUBSCRIPTION` > `OUT_OF_CREDITS` > `APPOINTMENT_UNCONFIRMED` > `CANCELLED` > `HEALTHY`

### 4. Interpret the verdict

- **HEALTHY** — no intervention needed. All signals are green.
- **NO_SUBSCRIPTION** — the user never had (or no longer has) a coaching
  subscription with this coach. Confirm with the user whether they purchased
  one; if so, check PaymentService via `eng:check-user-receipts` or
  `eng:investigate-missing-premium` for a stuck subscription.
- **OUT_OF_CREDITS** — the subscription is valid but the session credit
  balance is zero. The script emits a ready-to-run `credits/adjust` body to
  refund one session — apply after confirming with the user that a credit
  was incorrectly consumed.
- **COACH_NOT_BOOKABLE** — the coach's bookable flag is off or their calendar
  is disconnected (`resourceId` absent). The script emits the `PUT
  /coaches/bookable` fix. Calendar reconnect must be done by the coach via
  their settings page.
- **APPOINTMENT_UNCONFIRMED** — an appointment is stuck in pending state
  (free sessions should auto-confirm; paid sessions need a `paymentId`). The
  script emits a `PUT /appointments/:id/confirm` call.
- **CANCELLED** — the relevant appointment is cancelled. Script emits a
  `PUT /appointments/:id/reschedule` template — supply the new `start` time
  before running.

### 5. Privacy

`userId` is the only PII this skill emits. Output retains the full UID
(caller needs it for follow-up calls). Apply the standard `Compliance/Privacy`
UID-prefix truncation rule before pasting output into Slack / Linear / Notion.
The script does not auto-truncate.

## Output

A markdown verdict block with a per-signal status table and (when fixable) a
copy-paste fix command. Example for an OUT_OF_CREDITS case:

```
## Coaching session investigation: `abc123` → coach `coach456`

| Signal | Status | Detail |
|---|---|---|
| Coach bookable | ✓ | resourceId present |
| Subscription | ✓ active | subscriptionId: sub_789 |
| Credits remaining | ✗ 0 | last deducted 2026-06-05 |
| Last appointment | completed | 2026-06-05 — confirmed |

**Verdict: OUT_OF_CREDITS**

### Fix — refund one session credit

Run this call after confirming the credit was incorrectly consumed:

\`\`\`bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:investigate-coaching-session (session ${AINATIVE_SESSION_NAME:-adhoc})" -X POST https://services.aurahealth.io/coaching/subscriptions/credits/adjust \
  -H "Authorization: Bearer $AURA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"userId":"abc123","coachId":"coach456","adjustment":1,"reason":"manual correction — session credit refund"}'
\`\`\`
```

## Edge cases

- **Token missing** — print install hint, exit 2. No retries.
- **Coach 404** — coach record not found. Surface the response and exit.
- **Subscription 404** — treated as `NO_SUBSCRIPTION`.
- **Credits endpoint error** — warn and show the raw response; verdict
  defaults to the subscription-only view.
- **No appointments found** — shown as "no appointments" in the table; verdict
  driven by subscription + credits signals.
- **Multiple appointments** — uses the most-recent by `createdAt` unless
  `--appointment-id` is provided.
- **Re-run on same inputs** — pure read, no side effects. Idempotent.

## References

- Related: [`eng:investigate-missing-premium`](../investigate-missing-premium/SKILL.md) —
  subscription lockout pattern; the natural follow-up if `NO_SUBSCRIPTION` but
  the user says they paid.
- Related: [`eng:check-user-receipts`](../check-user-receipts/SKILL.md) —
  PaymentService receipt view.
- Related: [`eng:call-aura-api`](../call-aura-api/SKILL.md) — run the
  emitted fix commands interactively with a yes-gate.
- Source research: [ENGBACK-983](https://linear.app/aura-health/issue/ENGBACK-983)
  (CX triage bucket), parent [ENGBACK-975](https://linear.app/aura-health/issue/ENGBACK-975).
