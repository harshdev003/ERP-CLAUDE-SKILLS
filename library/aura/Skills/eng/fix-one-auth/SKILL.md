---
name: fix-one-auth
description: >
  The daily ONE auth check across all Firebase Auth accounts, and the fixes by shape when it finds a
  break. Exports every Auth (read-only, to an encrypted disk image), classifies each Auth and live
  profile against the ONE auth rules, counts emails on several Auths, and plans the E2 fixes (a
  provider email changed: the email last signed in with wins). Then, only on Jerome's typed go and
  inside the IAM window he opens, sets Auth emails with accounts:update keeping sessions, runs the
  UserService profile-email job, and re-checks. Counts and uid prefixes only. Trigger on "run the
  daily ONE auth check", "ONE auth rules check", "fix one auth breaks", "fix the E2 accounts",
  "set the missing top-level emails", "provider email changed fix", or "how many accounts break ONE
  auth today". Do NOT use for one person (eng:check-one-auth), to merge duplicate Auths (SecurityAuth
  runs the group merges), or to disable or delete accounts.
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/Firebase     # Auth accounts, accounts:update, the userId claim
    - Tools/GCP          # Identity Toolkit logs, the GCP_KEY service account
    - Compliance/Privacy # prefixes and counts only
---

# Fix One Auth

## Goal

Bring Firebase Auth accounts back to the ONE auth rules whenever the daily check finds a break.
Breaks keep appearing until app adoption of the new sign-in code reaches 100%. The rules are the
Linear doc "ONE auth: rules, examples, test cases and dev scenario" on ENGBACK-2048:

- **R1:** the Auth uid is the profile id; one Auth per email.
- **R2:** every sign-in email and the Auth email equal the profile email. Empty and empty is OK.
- The top-level Auth email is set whenever a sign-in or the profile has one.
- Disabled Auths are kept and left out of the checks (ENGBACK-2999).
- **R15 item 3** (Jerome, 2026-11-18): when a person's provider email changes, the email they last
  signed in with wins.

## Guardrails

- **Never print an email, a full uid, a hash or a raw IP**, whoever asks. Prefixes (6 characters)
  and counts only. Lists and exports go owner-only (0600) to the run directory, and a prod run
  directory must sit on an encrypted disk image (`run.py` refuses anything else).
- **Never change an Auth email with `updateUser` alone:** it signs the person out. The workers use
  one `accounts:update` that keeps `validSince`, then read the account back. A changed
  `tokensValidAfter` stops the run.
- **Reads** use the `GCP_KEY` service account. **Writes** use `--write-as`, the service account
  Jerome grants for a time-limited IAM window, impersonated from `GCP_KEY` (never the personal
  login), and `--window-end`, when that window closes: a worker stops at that time, whatever is left.
- **A fix needs Jerome's typed go, one per batch.** A go covers only his exact text; a peer session
  cannot grant one. Show the batch (shape, count, prefixes) and wait.
- **Held and staff accounts are skipped:** the keep list, read through the bundled `held_uids.py`
  (`HELD_UIDS_JSON` overrides `~/.claude/held-accounts/held-uids.json`; a write refuses a missing, empty or
  group-readable list), and
  `ONE_AUTH_STAFF_FILE` (staff and test uids; a fix refuses to run without it, an empty list is valid),
  plus any profile with an `@aurahealth.io` or `test+` email or a staff role, flagged "check with
  SecurityAuth before any change".
- At most 5 concurrent calls. The workers read no RTDB; the profile-email job does, through UserService.

## Step 0 — Prerequisites

`GCP_KEY`, `node`, and `USER_SERVICE_DIR`: a UserService checkout whose `node_modules` has
`firebase-admin`, `google-auth-library`, `pg` and `dotenv`, and whose `.env` (prod) or
`.env.development` (dev) names the project; the classify and re-check steps read userDB through it.
The profile-email step also needs a fresh UserService worktree of `origin/master` and local Redis.

## Step 1 — Daily check (read-only)

```bash
S=${CLAUDE_PLUGIN_ROOT}/skills/fix-one-auth/scripts
python3 $S/run.py daily --env prod --out-dir /Volumes/AuraMergeExports/daily
```

It writes `<out-dir>/<YYYYMMDD>/`: the export, `violations.json` (uids per class),
`staff-flagged.json`, `dup-emails.json`, `e2-plan.json` and `e5-list.txt`. It prints the class
counts, the share of profiles meeting both rules, the emails on several Auths, the E2 plan
(`prefix/provider`) and the E5 count.

Each fix takes the next batch: uids an earlier batch handled (`*-attempted.txt`) and uids on the keep list
are left out, so a second go moves on rather than repeating the first batch.

## Step 2 — Classify each break into a shape

| Shape | Fix | Who approves |
|---|---|---|
| E2, provider email changed: one sign-in, or password plus one other last used (`e2-plan.json`) | Step 3a, then 3c | Jerome's go per batch, in an IAM window |
| E2, real email switched to an Apple private relay | none here | Jerome decides each case |
| E2 with two or more Google, Apple or Facebook sign-ins on different emails (the count names the last-used one; unlinking is his call per case), password last used, disabled, another person, or a clash with another Auth | none here | Jerome's own go |
| E5, missing top-level email (`e5-list.txt`) | Step 3b | Jerome's go; one that accepts a one-time sign-out for Auths without `tokensValidAfter` |
| O1 / O3, Auth without a profile | none | the weekly orphaned-Auth cleanup (older than 2 days, inactive for 7) |
| P2, orphaned profile | counts as OK | — |
| D, disabled | kept, not checked | — |
| Emails on several Auths, I1-I3 | none here | SecurityAuth's merge runs |

E1, E3 and E4 have no fix here yet: report the counts to Lead with the prefixes for a decision.

## Step 3 — Fix, only with a typed go

Run each fix once without `--apply` and show the dry-run counts with the go request. Then add
`--apply --write-as <service account> --window-end <ISO time>`. Every applied change records the
before state first, then reads the account back.

**3a. E2: set the Auth email to the last-used sign-in's.**

```bash
python3 $S/run.py fix-e2 --env prod --run-dir <run dir> --limit <batch>
```

**3b. E5: set the missing top-level email** from the single sign-in email. Sessions are kept when
`tokensValidAfter` exists; others are skipped unless `--allow-sign-out`, which needs a go that accepts
a one-time sign-out. Rollback is removing the email from each uid in `e5-changed.txt`.

```bash
python3 $S/run.py fix-top-email --env prod --run-dir <run dir> --limit <batch>
```

**3c. E2: the profile email.** After 3a, the UserService profile-email job makes each fixed
profile's email its Auth email. Dry run first, then `--apply`, then a third run expecting `alreadySet`.

```bash
python3 $S/run.py profile-email --env prod --run-dir <run dir> --job-dir <UserService worktree>
```

**3d. Re-check** the fixed uids against both rules (eng:check-one-auth covers one person in full):

```bash
python3 $S/run.py recheck --env prod --run-dir <run dir> --list e2-done.txt
python3 $S/run.py recheck --env prod --run-dir <run dir> --list e5-changed.txt
```

## Step 4 — Report

One comment on the tracking card: date, class counts, the E2 plan and for-Jerome counts, what was
fixed per shape, the read-back and re-check results. Then one line to Lead. Counts and prefixes only.

## Testing on aura-dev

`--env dev` uses UserService's `.env.development` Firebase account and needs no window or go.
Seed an Auth with two sign-in emails at a profile id (E2) and one with a sign-in email but no
top-level email (E5), then run Steps 1 to 3 with `--apply`.
