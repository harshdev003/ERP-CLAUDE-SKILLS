# Card pattern — Pause coach subscription

The card layout this skill creates mirrors the [ENG-109](https://linear.app/aura-health/issue/ENG-109)
runbook: **one parent card** + **two sub-cards per paused renewal** (a credit
removal and a payout removal).

## Parent card

- **Title:** `Pause coach subscription for N month(s)` (from the plan's `parentTitle`)
- **Team:** Engineering
- **Labels:** `Support`, `Coaching`, `Payout`
- **Description template** (fill the `< >` placeholders from the plan):

```markdown
- [ ] User id:- <userId>
- [ ] Coach id:- <coachId>
- [ ] Subscription <subscriptionId> - <price>
- [ ] Pause for <N> month(s) until <verifyOn> (<paused renewal dates, comma-separated>)
- [ ] <link to each credit sub-card>
- [ ] <link to each payout sub-card>
- [ ] Check unpaused on <verifyOn>
- [ ] Documentation https://www.notion.so/aurahealth/Pausing-subscriptions-1cf75e23af2080df8df8fed7e9491b1a
```

Keep the raw `userId` / `coachId` / `subscriptionId` in the body — the assignees
need them to find the records. Do not add name / email / phone (`Compliance/Privacy`).

## Sub-cards (one pair per paused renewal)

For each renewal date `d` in the pause window:

| Card kind | Title | Assignee | Due date |
|---|---|---|---|
| credit | `Remove the credit on renewal <Mon> <day>th` | **Ashish** | the renewal date `d` |
| payout | `Remove the payout for <Mon> for this coach` | **Jerome** | the renewal date `d` |

- `<Mon>` is the 3-letter month abbreviation (Nov, Dec, Jan…).
- Each sub-card sets `parentId` = the parent card's issue ID.
- Assignees are resolved from the names "Ashish" / "Jerome" to Linear user IDs
  at runtime — never hardcode the IDs.
- The skill's `scripts/plan_pause.py` emits these titles/assignees/due-dates so
  the card set is deterministic and reproducible.

## Worked example (ENG-109, 3-month pause)

First renewal Nov 27 → paused Nov 27, Dec 27, Jan 27 → resume Feb 27.

| Renewal | Credit card (→ Ashish) | Payout card (→ Jerome) |
|---|---|---|
| Nov 27 | Remove the credit on renewal Nov 27th | Remove the payout for Nov for this coach |
| Dec 27 | Remove the credit on renewal Dec 27th | Remove the payout for Dec for this coach |
| Jan 27 | Remove the credit on renewal Jan 27th | Remove the payout for Jan for this coach |

Parent checklist closes with `Check unpaused on Feb 27th`. In the original
ENG-109 that verification step was never ticked — the skill surfaces `verifyOn`
explicitly so the operator can schedule the unpause check.
