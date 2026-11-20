# The Aura event vocabulary

`aura-main.firebase.raw_events` carried **253 distinct `name` values** when this skill
was written. This file is a map of the families that matter, not a frozen copy of the
list — **the table is the source of truth**, so enumerate rather than trust this page:

```bash
python3 scripts/search_events.py --list-names
python3 scripts/search_events.py --list-names --match subscription
```

That read touches only the `name` column, so it is far cheaper than anything
touching `data` — the cheap way to turn an English question ("when was this
cancelled?") into a real event name. `--list-names` prints the measured scan size
before it runs.

## Account lifecycle

What `--lifecycle` selects. Counts are from the live window at authoring time and move.

| Event | ~count | Notes |
|---|---:|---|
| `UserLoggedIn` | 895k | `$.userId` only |
| `EmailPermissionsUpdated` | 363k | one row per permission toggle — very chatty |
| `UserUpdated` | 68k | **carries PII**: `email`, `givenName`, `gender`, `ageGroup`, `locale` |
| `UserAttributed` | 29k | install attribution: `installAt`, `installType`, `sourceType`, `sourcePlatform` |
| `UserCreated` | 25k | **carries PII** — same shape as `UserUpdated` |
| `EmailValidated` / `EmailInvalidated` | 23k / 1.8k | `$.status` |
| `UserAgentUpdated` | 16k | |
| `UserFollower` / `UserFollowing` / `UserUnfollowing` / `UserUnfollower` | 13k / 13k / 3.9k / 2.7k | social graph |
| `UserDeleted` | 1.4k | `$.userId` only — no reason, no actor |
| `RoleUpdated` | 8 | `$.role`; `role=null` is the de-role signal |

`RoleUpdated` being **eight rows in 45 days** is why it is easy to miss and worth
knowing about: it is the rarest lifecycle event in the table.

## Subscription families

Four parallel families, same verb set, different product:

- `Subscription*` — the Aura consumer subscription (the big one)
- `AISubscription*` — Aurie
- `CommunitySubscription*` — community tier
- `CoachSubscription*` / `CoachingSubscription*` — coaching
- `BusinessSubscription*` — B2B seats (`BusinessSubscriptionMemberAdded`)

Verbs seen across them: `Created`, `Changed`, `Updated`, `Renewed`, `Cancelled`,
`Expired`, `Ended`, `Deleted`, `Activated`, `Restored`, `Revoked`, `Refunded`,
`Shown`, `Paid`, `PaidStarted`, `PaidEnded`, `PaidExpired`, `PaidCancelled`,
`PaidRestored`, `TrialStarted`, `TrialEnded`, `TrialCancelled`, `TrialExpired`,
`TrialConverted`, `TrialRestored`, `FreeStarted`, `FreeEnded`, `FreeExpired`,
`GraceStarted`, `GraceEnded`, `GraceConverted`, `PaymentPending`,
`ProductChanged`, `PackageChanged`, `ProviderChanged`.

`--lifecycle` matches these by **prefix**, so a new verb is picked up without an
edit to the skill.

Subscription events carry a rich payload: `productId`, `provider`, `revenue`,
`period`, `duration`, `trial`, `autoRenew`, `purchaseAt`, `expirationAt`,
`transactionId`, `receiptId`, `sourcePlatform`, plus `before` / `after` on
`Changed`.

## Experiments

| Event | Pivot | Notes |
|---|---|---|
| `ExperimentAssigned` | `$.userId` **and** `$.experimentId` | one row per user per assignment; `$.value` is the variant |
| `ExperimentCreated` / `ExperimentUpdated` / `ExperimentEnded` / `ExperimentDeleted` | `$.experimentId` only | **no `$.userId`** — experiment-level, cannot be pivoted by user |

`ExperimentUpdated` carries `active`, `endDate`, `minVersion`, `n`, `result`,
`notes`, `targetApp` — the readout metadata, which is why it is the event to pull
when reconstructing what an experiment concluded and when.

## Other `data` keys worth knowing

Keys vary per event, so **sample before filtering**:

```bash
python3 scripts/search_events.py --sample SerieCreated
```

Common pivots beyond `$.userId` / `$.experimentId`: `$.serieId`, `$.contentId`,
`$.coachId`, `$.courseId`, `$.role`, `$.status`, `$.productId`, `$.transactionId`.

Two keys are on essentially every event and are hidden by default in the timeline
because they are noise:

- `$.at` — the event's own timestamp. Verified equal to the `createdAt` column to
  the millisecond, so `createdAt` is authoritative and `$.at` adds nothing.
- `$.origin` — the emitting service (`firebase-producer`, `experiment-service`, …).
  Useful when the same logical change emits twice from two producers, which does
  happen (duplicate `ExperimentAssigned` rows differing only by `origin`).

Pass `--fields at,origin,…` to bring them back.

## PII

`UserCreated` and `UserUpdated` carry `email` and `givenName`. `CoachCreated` /
`CoachUpdated` carry a real person's name in `$.name`. All are redacted by default;
`--show-pii` opts in. `$.name` is **not** redacted on any other event — it is an
entity name (experiment, serie, course) there, and blanket redaction hid the
experiment identity on every `ExperimentUpdated` row. See `Compliance/Privacy`
before any of this reaches Slack, Notion, or a ticket.
