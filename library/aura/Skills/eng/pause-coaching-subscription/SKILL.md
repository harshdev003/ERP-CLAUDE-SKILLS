---
name: pause-coaching-subscription
description: >
  Pause a user's coaching subscription for N months and create the Linear tracking cards (a
  parent plus per-month sub-cards) that mirror the "Pause coach subscription" runbook. Fetches
  the subscription and credit state first via `eng:fetch-coaching-subscription` and
  `eng:fetch-coaching-credits`, shows a plan, and only after approval calls the Stripe pause
  endpoint and opens the cards. Trigger on "pause coaching subscription", "pause coach
  subscription", "pause this user's subscription for 3 months", "pause subscription and create
  cards", "pause billing for this coach subscription", "put this coaching sub on hold for N
  months", or a userId plus coachId with a request to pause their coaching subscription. Do
  NOT use for just reading subscription state (`eng:fetch-coaching-subscription`), reading
  credit balances (`eng:fetch-coaching-credits`), adjusting credits
  (`eng:adjust-coaching-credits`), or session diagnosis (`eng:investigate-coaching-session`).
  This skill WRITES — it pauses live Stripe billing.

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index                 # baseline routing aid (universal)
    - Tools/Aura API              # POST /subscriptions/stripe/pause + the two fetch endpoints
    - Tools/Linear                # creating the parent + sub-cards
    - Compliance/Privacy          # writes userId/coachId/subId into Linear cards
---

# Pause Coaching Subscription

Pause a coaching subscription for a fixed number of months and stand up the
Linear cards that track the monthly cleanup work — one parent card plus, for
each paused renewal, a "remove the credit on renewal" card (assigned to Ashish)
and a "remove the payout for <month>" card (assigned to Jerome). Modeled on the
[ENG-109](https://linear.app/aura-health/issue/ENG-109) runbook.

## Goal

Given a `userId` + `coachId` + pause duration, **pause the Stripe subscription**
and **create the tracking cards**, after showing a plan the user approves.

## Inputs

- **`userId`** — Aura Firebase user ID
- **`coachId`** — CoachService coach ID
- **`months`** — how many months to pause (default **3**)
- **`--resumes-at`** _(optional)_ — explicit resume instant (ISO, e.g.
  `2026-09-25T00:00:00.000Z`). If omitted, computed as the next renewal after
  the paused window.
- **`--first-renewal`** _(optional)_ — `YYYY-MM-DD` of the first renewal that
  falls inside the pause window. If omitted, derive it from the subscription's
  renewal day (see Step 3).

If `userId` or `coachId` are missing, ask for both before doing anything.

## Required environment

- `AURA_API_TOKEN` — service-level Aura backend token. Missing token is a hard
  fail for the pause call. The two fetch sub-skills enforce the same.

## This skill makes real writes — plan first

This skill **mutates production billing** (one Stripe pause) and **creates
multiple Linear cards**. Per best-practices principle 4, **never call the pause
endpoint or create a card before showing the plan and getting explicit
approval** (Step 5). Treat Step 6 onward as the only place side-effects happen.

## Workflow

### 1. Resolve inputs

Confirm `userId`, `coachId`, and `months`. If the user gave a phrase like
"pause for 3 months", `months = 3`.

### 2. Fetch subscription state (compose `eng:fetch-coaching-subscription`)

Call the sub-skill with `userId` + `coachId`. Capture `subscriptionId` (`id`),
`productId`, `status`, `createdAt`, `expirationAt`, and the price.

**Guards — stop before any write if:**
- `NO_SUBSCRIPTION` / 404 → report it; suggest `eng:investigate-coaching-session`.
- The subscription is already paused/cancelled (`cancelledAt`, `autoCancelledAt`,
  or a paused status set) → report current state and stop. Don't double-pause.

### 3. Fetch credit state (compose `eng:fetch-coaching-credits`)

Call the sub-skill with the same `userId` + `coachId`. Capture the balance
(`available`, `totalCredits`, `totalUsed`) and recent history for the plan
readout — it tells the operator whether credits already need clawing back.

### 4. Compute the schedule (deterministic — `scripts/plan_pause.py`)

Derive the **first renewal date** inside the pause window (the next monthly
renewal on the subscription's billing day; infer the day-of-month from
`expirationAt` / `createdAt`, or use `--first-renewal` if supplied), then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/pause-coaching-subscription/scripts/plan_pause.py \
  --first-renewal <YYYY-MM-DD> \
  --months <N> \
  --subscription-id <sub_...> \
  --user-id <userId> --coach-id <coachId> \
  --price "<e.g. 139 USD/month>" \
  [--resumes-at <ISO>]
```

The script returns JSON: `resumesAt`, `pausedRenewals`, `verifyOn`, the
`stripeCall` body, and the `cards` list (title + assignee + dueDate for each).
All date math is here, not in an LLM step — re-running with the same inputs
yields the identical plan.

### 5. Show the plan and WAIT for approval

Render, then stop until the user approves:

- **Subscription:** id, price, status, current `expirationAt`
- **Credits:** available / total / used
- **Pause:** N months, paused renewals (dates), **resumesAt**
- **Stripe call:** `POST /subscriptions/stripe/pause` with the exact body
- **Cards to create:** the parent + each sub-card with assignee + due date
  (table form — see `references/card-pattern.md`)

Do not proceed without an explicit "yes".

### 6. Idempotency guard — check for an existing pause

Before creating anything, search Linear for an existing parent card for this
**subscription** — match on `subscriptionId`, or the `userId` + `coachId` pair
together; **never `userId` alone**, since one user can hold separate
subscriptions/pauses for different coaches and a userId-only match would wrongly
no-op a legitimate new pause. If a card for this same subscription (or this
exact user+coach pair) exists, **stop** and link it rather than creating a
duplicate set. (best-practices principle 8.)

### 7. Pause the subscription

```
POST /subscriptions/stripe/pause
Authorization: Bearer $AURA_API_TOKEN
Content-Type: application/json
Body: { "subscriptionId": "<sub_...>", "resumesAt": "<resumesAt ISO>" }
```

Confirm a success response before touching Linear. If the pause fails, **stop**
and report — do not create cards for a pause that didn't happen.

### 8. Create the Linear cards (Linear MCP `save_issue`; fall back to Linear API)

Per the MCP-first convention, use the Linear MCP `save_issue` create path.

1. **Parent card** — title from the plan (`Pause coach subscription for N
   month(s)`), team **Engineering**, description built from the
   `references/card-pattern.md` template (the checklist with userId, coachId,
   subscriptionId, price, paused months, the linked sub-cards, a `Check
   unpaused on <verifyOn>` item, and the runbook link). Capture its issue ID.
2. **Sub-cards** — for each entry in the plan's `cards`, create a child issue
   with `parentId = <parent issue id>`:
   - `kind: credit` → assign to **Ashish**, `dueDate` = renewal date.
   - `kind: payout` → assign to **Jerome**, `dueDate` = renewal date.
   Resolve "Ashish" / "Jerome" to Linear user IDs at runtime (Linear MCP user
   lookup / `list_users`) — do not hardcode IDs in the skill.

If Linear MCP is unreachable, fall back to the Linear API (`LINEAR_API_KEY`).

### 9. Summarize

Report: the Stripe pause confirmation (subscriptionId + resumesAt), the parent
card URL, and each sub-card URL with its assignee + due date.

## Output

A readable summary: pause confirmation + a table of the created cards (id,
title, assignee, due date) — or a clear stop message if a guard fired
(NO_SUBSCRIPTION, already paused, existing pause card, pause API failure).

## Privacy

`userId`, `coachId`, and `subscriptionId` are written into the Linear cards —
they're operationally required for the assignees to action the payout/credit
removals, matching the ENG-109 precedent. Do **not** surface full name, email,
or phone in any card or summary (`Compliance/Privacy`: UID prefix + email
domain only; never full email / givenName / familyName).

## Edge cases

- **Missing userId/coachId** — ask for both; don't guess.
- **NO_SUBSCRIPTION (404 in Step 2)** — stop; suggest
  `eng:investigate-coaching-session` or `eng:check-user-receipts`.
- **Already paused / cancelled** — report state, stop, don't double-pause.
- **Existing pause card found (Step 6)** — stop, link it, don't duplicate.
- **Stripe pause fails (Step 7)** — stop before creating cards.
- **Re-run after a successful pause** — Step 6 finds the parent card and no-ops.
- **Token missing** — stop before Step 7 pointing to
  `~/.claude/settings.json` → `env.AURA_API_TOKEN`.

## References

- [`references/card-pattern.md`](references/card-pattern.md) — the parent-card
  description template + the sub-card naming/assignee/due-date rules, with the
  worked ENG-109 example.
- Related: [`eng:fetch-coaching-subscription`](../fetch-coaching-subscription/SKILL.md) —
  composed in Step 2.
- Related: [`eng:fetch-coaching-credits`](../fetch-coaching-credits/SKILL.md) —
  composed in Step 3.
- Related: [`eng:adjust-coaching-credits`](../adjust-coaching-credits/SKILL.md) —
  manual credit/debit if a renewal credit needs clawing back by hand.
- Related: [`eng:investigate-coaching-session`](../investigate-coaching-session/SKILL.md) —
  full session diagnostic.
