---
name: cleanup-orphaned-auth
description: >
  Clean up orphaned Firebase auth credentials — accounts that authenticate at Firebase but
  have no usable Aura profile — without requiring a full Postgres dump. Confirms each
  candidate live and read-only, then deletes confirmed orphans via Firebase batchDelete. Three candidate-sourcing modes share one confirm/delete path: `recent`
  (the routine path), `harvest` (dump-free, from GCP logs), and `reconcile` (full exhaustive
  audit from an auth export plus Postgres dumps). Profileless accounts with no email at all get
  their own gate, `confirm-no-email`. Dry-run by default; never deletes without an explicit go. Trigger on "clean up orphaned auth", "delete orphaned firebase auth", "cleanup
  orphan credentials", "remove auth with no profile", "purge orphaned auth tokens", "find
  recent orphans", "orphans in the last N hours", "reconcile auth vs profiles", or "full
  orphan audit", "delete auth with no email", or "no-email orphans". Do NOT use for deleting real users — that is the UserService deleteUser flow.

metadata:
  version: "3.9.1"
knowledge:
  reads:
    - Guide/Index           # baseline routing aid (universal)
    - Tools/GCP             # SA / project / IAM / Cloud Logging query patterns
    - Tools/Firebase        # Firebase auth ↔ Aura profile model; Identity Toolkit accounts:query / lookup / batchDelete
    - Tools/Aura API        # GET /users/<uid> + POST /users/email/lookup (read-only orphan confirmation)
    - Compliance/Privacy    # PII rule — mask end-user emails (domain only) in any output
---

# Cleanup Orphaned Auth

Delete **orphaned Firebase auth credentials** — live auth accounts with an email but
**no usable Aura profile** — without a SQL dump. The full one-time reconciliation
(ENG-728) needed a Postgres dump to do 7M+ profile lookups in bulk; this skill instead
**harvests the small candidate set the service already surfaces in its logs** and
**confirms each one live through the API**, so it can run any time with no dump.

> **An orphan is the only thing deleted: the Firebase credential.** These accounts have
> no profile, no subscriptions, and no tokens — there is nothing else to clean, and the
> normal `deleteUser` flow does **not** apply to them (it requires a profile and returns
> `USER_NOT_FOUND`). Deleting the credential just forces a clean re-signup if the person
> ever returns.

## What counts as an orphan

A Firebase auth account is an orphan when **all** hold (confirmed read-only via two GETs):

1. Its UID is not the `id` of any Aura profile — `GET /users/<uid>` → `404`, **and**
2. Its email matches no **active** Aura profile — `POST /users/email/lookup` → `404`, **and**
3. It has an email. Accounts with no email at all take their own gate, Step 3C.

`POST /users/email/lookup` does the same `email → profile` (multi-account) resolution the
client's `verifyToken` flow does, so it never misclassifies an sso-resolvable account (one
whose email maps to an active profile under another uid) as an orphan. The email goes in the
request body (`{"email": …}`, a token whose role the route allows: service today, agent once UserService adds it), never in a URL. Both checks are read-only
lookups — they never sign in, so they never touch the account's `lastLoginAt`. (This matters: a
sign-in-based check like minting a token for `verifyToken` bumps `lastLoginAt` and would
silently defeat `confirm`'s inactivity gate.)

## Prerequisites

| Need | Env var | Used for |
|------|---------|----------|
| GCP service account (project `firebase-auratech16`) | `GCP_KEY` | Cloud Logging query, `accounts:query`, `accounts:lookup`, `accounts:batchDelete` |
| Aura API token | `AURA_API_TOKEN` (or `AURA_USER_TOKEN`) | the read-only `GET /users/<uid>` and `POST /users/email/lookup` lookups (`recent` + `confirm`) — UserService answers `403` without it; the lookup answers `403` to a role it doesn't allow (service today; agent once UserService adds it). Read-only, so they never sign in / never bump `lastLoginAt` |
| `node` + `AURA_CLI_DIR` (a checkout whose `node_modules` has `firebase-admin`) | *(env)* | `confirm-no-email`'s exact RTDB reads of `/users/{uid}`, through the bundled `scripts/rtdb_get.js` (Admin SDK realtime protocol, existence only) |
| Temporary `roles/firebaseauth.admin` on the SA | *(IAM, execute step only)* | `accounts:batchDelete` — the SA baseline is `firebaseauth.viewer` + `logging.viewer`, which is read-only |

The delete grant is a **privilege escalation** (bulk-delete any user). It is requested
only for the execute step and **must be removed afterward** (the skill reminds you).

Run all Python with `PYTHONSAFEPATH=1` — a stray module on the path (e.g. a scratch file
in `/tmp`) can otherwise shadow the stdlib and break `google.*` imports.

## Held accounts

`delete` first reads the keep list (`scripts/held_uids.py`): `~/.claude/held-accounts/held-uids.json`,
or `HELD_UIDS_JSON`. It is a JSON array of uid strings, mode 600. It lists accounts that must
never be deleted, such as an incident's accounts.

- **Refused** (exit 2, nothing deleted, dry runs too): no file, an empty list, or a file that is
  unreadable, not a JSON array of uid strings, or readable by group or others. A broken or missing
  list never counts as an empty one.
- **Skipped:** `delete` drops every listed uid, in dry runs too, printing `skip <prefix>…: keep list`
  per uid and `kept: N`.
- **Who writes it:** whoever owns the hold, never this skill. Don't create, edit or relax it
  to get a run through; report the refusal instead.

## Inputs (flags on `scripts/orphan_cleanup.py`)

| Flag | Default | Meaning |
|------|---------|---------|
| `--hours` | *(required)* | `recent`: lookback — pull auth created in the last N hours |
| `--min-age-minutes` | `10` | `recent`: exclude orphans younger than this (likely signup in-flight) |
| `--since` | `7d` | `harvest`: log window (`24h`, `7d`, `30d`, …) |
| `--container` | `user-service` | `harvest`: GKE container to scan |
| `--min-age-days` | `2` | Skip auth **created** within N days (avoids signup-race / snapshot-skew) |
| `--min-inactive-days` | `7` | Skip auth **active** (login *or* refresh) within N days (avoids currently-active people) |
| `--min-inactive-days` (`confirm-no-email`) | `365` | Skip a no-email account signed in, refreshed or created within N days. 365 is the decided default; the window is a flag |
| `--pause` (`confirm-no-email`) | `0.1` | Seconds between UserService GETs (about 10 a second) |
| `--batch` | `100` | `accounts:batchDelete` batch size (max 1000). Each deleted Auth fires the auth `onDelete` Functions, so keep batches small |
| `--pause` (`delete`) | `5` | Seconds between delete batches |
| `--counts-only` (`delete`) | off | Print counts only: no uid prefix, no masked-email sample |

**Decided defaults, and one past run.** 365 days of inactivity is the decided default for no-email
accounts; the first run (2026-11-13) used it. That same night's run of the with-email set turned the
2-day creation gate off (`--min-age-days 0`) by a decision **for that run only**. The default stays
`2`: turning a gate off is a decision made per run, never a standing setting.

## Workflow

### 1. Check prerequisites

```bash
test -n "$GCP_KEY" && echo "GCP_KEY ok" || echo "MISSING GCP_KEY"
test -n "$AURA_API_TOKEN" -o -n "$AURA_USER_TOKEN" && echo "AURA token ok" || echo "MISSING AURA_API_TOKEN (or AURA_USER_TOKEN)"
```

If either is missing, stop and point the user at `update-config` (see `/CLAUDE.md` →
"Secrets and environment"). For the **execute** step only, confirm the temporary
`firebaseauth.admin` grant is in place (Step 4).

Pick a candidate source — **2A** (recent: fastest, last N hours, no dump — the routine path
for the daily drip), **2B** (harvest: log-based, dump-free), or **2C** (reconcile: full
audit). All feed Step 3.

### 2A. Recent — last N hours, dump-free (fastest, routine)

Find orphans created in the last N hours without a full export, in two stages:

1. **Pull recent auth** via the Identity Toolkit `accounts:query` (sorted `CREATED_AT desc`,
   paging until the window cutoff — ~1–2 pages for 24h) and drop any UID that already has a
   profile under its own id (fast `GET /users/<uid>` → `200` = healthy, `404` = no profile).
2. **Resolve the survivors** read-only via `POST /users/email/lookup` (`email → profile`, the same
   answer the client's `verifyToken` flow gives). An existing user re-logging-in with another
   provider (multi-account per email — e.g. signed up with Google months ago, taps "Sign in with
   Apple" today) has no profile under the *new* uid but their email resolves to their account, so
   it's reported as **`sso-resolvable`, not a true orphan**. The check is a read-only lookup — it never signs
   in, so it never bumps `lastLoginAt` (a sign-in-based check would silently defeat `confirm`'s
   inactivity gate). Skipping the email step over-counts re-logins as orphans.

This is the routine path for the daily web-signup leak (auth created, profile never written —
see [PROD-2721](https://linear.app/aura-health/issue/PROD-2721)); ~3 min for a 24h window, no
dump and no log scan.

```bash
PYTHONSAFEPATH=1 python3 scripts/orphan_cleanup.py recent \
  --hours 24 --min-age-minutes 10 --out /tmp/orphan_candidates.txt
```

It prints `last Nh: X new auth | healthy H | orphans O | sso-resolvable R` and writes the
**true** orphan UIDs (one per line) for Step 3. Both stages need `AURA_API_TOKEN` (or
`AURA_USER_TOKEN`) on top of the read-only `firebaseauth.viewer` baseline — UserService answers
`401` to an unauthenticated `GET /users/<uid>`, so `recent` exits early without a token, and exits
non-zero if every stage-1 lookup errors rather than reporting an empty result. `--min-age-minutes` (default 10) excludes accounts too new to judge
(profile may still be in flight). **Note:** Step 3's `confirm` age gates still apply, so a short
window's orphans (all freshly created) mostly won't clear `--min-age-days 2` — intended (a fresh
abandoned signup may still complete). For cleanup, use a wider window (e.g. `--hours 168`) so
orphans aged past the gate are deletable; for pure monitoring of the drip, just read the summary.

### 2B. Harvest candidates — log-based, dump-free

Query GCP prod logs for the orphan signals UserService emits and extract the distinct
UIDs. No Postgres needed.

```bash
PYTHONSAFEPATH=1 python3 scripts/orphan_cleanup.py harvest \
  --since 7d --container user-service --out /tmp/orphan_candidates.txt
```

It searches `aura-prod-cluster-a` / project `firebase-auratech16` for
`"No profile for the token"`, `"Invalid user id from decoded token"`, and
`"Stored user no profile in database"`, then pulls each entry's
`jsonPayload.signInUid` (older entries: `jsonPayload.tokenUserId`) / `jsonPayload.userId` / `verifiedToken.tokenUserId`. Output is
a deduped UID list + a count.

### 2C. Reconcile — full exhaustive audit (needs a Postgres dump)

For a complete audit of *all* accounts (including dormant orphans that never authenticate),
reconcile a full Firebase auth export against Postgres dumps you provide. Export every auth
account to NDJSON (Admin SDK / Identity Toolkit `accounts:batchGet`, one JSON object per
line), and dump `user` (`id,email,deletedAt`) + `emailUser` (`email,userId`) from Postgres.

```bash
PYTHONSAFEPATH=1 python3 scripts/orphan_cleanup.py reconcile \
  --export /tmp/firebase_auth_export.ndjson \
  --user-csv /tmp/user.csv --emailuser-csv /tmp/emailuser.csv --out-dir /tmp
```

It classifies every account into buckets (healthy / sso_resolvable / orphan /
anonymous-or-no-email / deleted-with-live-auth), reports the **locked-out** count (active
profile, no live auth — **not** a delete candidate), and writes two orphan candidate lists:
`orphan_reconcile_profileless.txt` (truly profile-less) and
`orphan_reconcile_deleted_profile.txt` (email matches a deleted profile). Either feeds Step 3.
It also writes `reconcile_claim_candidates.txt`: the secondary logins of a live profile
(sso-resolvable + emailUser-mapped). **Never a delete candidate** — it is the input of the
FirebaseAuth `functions/scripts/backfillUserClaim.js` userId-claim backfill, which re-resolves
each uid. A candidate is an account with no own profile whose emails (all provider emails,
lowercased) resolve — via `user.email` or an `emailUser` row pointing at an **active**
profile — to **exactly one** active profile. The claim outputs:

| File | Contents |
|---|---|
| `reconcile_claim_candidates.txt` | every single-profile match |
| `reconcile_claim_new_candidates.txt` | candidates whose export line does not already carry `userId` == that profile (stale claims included) |
| `reconcile_claim_ambiguous.txt` | emails resolving to **>1** active profile — excluded from both lists above; needs a human, never the backfill |

**Soft-deleted profile, live Auth** (`deleted_with_live_auth`): the user asked for the delete and
UserService soft-deleted the profile, but the Auth account was left behind (PROD-7883). These
uids go to `reconcile_deleted_with_live_auth.txt`. They are not orphans, so `confirm` is the
wrong gate for them. Re-check them with **Step 3B**, then they take Step 4 and Step 5 like any
other list.

The run ends with a **userId claim coverage** section (counts only): no own profile with
email, already stamped, stale claim, new candidates, ambiguous, unresolvable. Claims are read
from `customClaims.userId` (an object, or `null` — the export's shape), falling back to
`customAttributes` (a JSON string); an export without either reports every candidate as new.
Heavyweight: the export is ~1–2h for millions of accounts — `recent` is the routine path.

### 3. Confirm each candidate live + apply safety gates

```bash
PYTHONSAFEPATH=1 python3 scripts/orphan_cleanup.py confirm \
  --in /tmp/orphan_candidates.txt \
  --min-age-days 2 --min-inactive-days 7 \
  --out /tmp/orphan_confirmed.txt
```

For each UID this does: Firebase `accounts:lookup` (must still exist; capture email,
`createdAt`, `lastLoginAt`, `lastRefreshAt`, `disabled`, providers) → apply the
age/activity gates → **read-only confirm**: `GET /users/<uid>` (still `404`) and
`POST /users/email/lookup` for each address (`Authorization: Bearer $AURA_API_TOKEN`). A UID
is a **confirmed orphan** only if no profile exists under the uid **and** no email resolves
to a live profile. The live re-check auto-spares signup-races that have since gained a
profile, and sso-resolvable re-logins (email maps to an existing account).

> **Confirm read-only — never by signing in.** Use read-only lookups only (`GET /users/<uid>` +
> `POST /users/email/lookup`). Do **not** mint a token to call `verifyToken`:
> `signInWithCustomToken` is a sign-in, so it bumps the account's `lastLoginAt`/`lastRefreshAt`
> — which contaminates the activity record and defeats the `--min-inactive-days` gate (every
> account would look "active just now" the moment it's checked). `POST /users/email/lookup`
> gives the same `email → profile` verdict without signing in.

### 3B. Confirm a soft-deleted profile's leftover Auth account

Only for `reconcile_deleted_with_live_auth.txt` from Step 2C. Read-only:

```bash
PYTHONSAFEPATH=1 python3 scripts/orphan_cleanup.py confirm-deleted \
  --in /tmp/reconcile_deleted_with_live_auth.txt --grace-days 1 \
  --out /tmp/deleted_confirmed.txt
```

A uid is kept only when **all** of these hold now:

- the Auth account still exists and isn't disabled (`accounts:lookup`);
- `GET /users/<uid>` still returns the profile with `deletedAt` set. It is re-read live, not
  taken from the dump;
- `deletedAt` is older than `--grace-days`, so a self-delete still in flight finishes on its
  own first.

Anything else drops the uid, counted per reason: `auth_gone`, `auth_disabled`, `profile_gone`,
`restored` (`deletedAt` back to null), `in_grace`, and `errored` (any lookup failure, never a
confirm). Deleting is safe because UserService has no restore path for a deleted profile. The
Auth account is the last piece left. `/tmp/deleted_confirmed.txt` then goes to Step 4 and Step 5
unchanged.

### 3C. Confirm a profileless Auth account with no email

For accounts with no email on the Auth record or any provider (mostly old Facebook sign-ins
without email, plus old anonymous ones). `confirm` skips them because nothing can resolve an
email. `reconcile` writes them to `orphan_reconcile_no_email.txt` (no own profile in the dump, and
no userId claim to a live profile in it). Read-only:

```bash
PYTHONSAFEPATH=1 python3 scripts/orphan_cleanup.py confirm-no-email \
  --in /tmp/orphan_reconcile_no_email.txt --min-inactive-days 365 \
  --out /tmp/no_email_confirmed.txt
```

A uid is kept only when **all** of these hold now:

- it is not on the keep list (the list is required, as for `delete`);
- the Auth account exists, isn't disabled, and has no email on it or any provider;
- its last sign-in, refresh or creation is older than `--min-inactive-days`. An account with no
  timestamp at all is kept back;
- `GET /users/<uid>` is `404`;
- an exact RTDB read of `/users/{uid}` finds nothing. It goes through `scripts/rtdb_get.js`
  (canonical in `eng:read-rtdb-paths`): exact paths only, 200 per call, existence only, never
  `/users` itself, never a shallow read or REST;
- a `userId` claim, if any, reaches no live profile (`GET /users/<claim>` is `404` or has
  `deletedAt`).

Anything else drops the uid, counted per reason: `held`, `auth_gone`, `disabled`, `has_email`,
`recent_activity`, `live_profile`, `rtdb_profile`, `claim_live`, `errored` (any failed lookup,
never a confirm). The report is **counts only**: the confirmed total by provider (Facebook,
Apple, Google, none), by created year and by inactive age. No uid or email is printed; the uids
go only to the output file, mode 600. `/tmp/no_email_confirmed.txt` then goes to Step 4 and
Step 5, with `delete --counts-only` for the dry run.

### 4. Present and gate (dry-run is the default)

**Always show the confirmed-orphan set before deleting** — count, provider/age breakdown,
and a masked-email sample (domain only, per `Compliance/Privacy`). Then **stop** and ask
for an explicit go. Do not proceed to deletion on a vague "ok the analysis looks right" —
require a clear "delete them".

When the user confirms, have them add the temporary grant (they run it — modifying IAM is
their action, not the skill's):

```bash
# user-gcloud: an IAM grant to the service account is the user's own action
gcloud projects add-iam-policy-binding firebase-auratech16 \
  --member="serviceAccount:<gcp-sa-email>" \
  --role="roles/firebaseauth.admin"
```

IAM is eventually-consistent — allow ~1–2 min to propagate (the delete step retries
permission errors with backoff).

### 5. Execute the deletion

`delete` is **dry-run unless `--execute` is passed** — without it the command just reports
how many it would delete (with a masked sample) and exits, touching nothing. Preview first,
then re-run with `--execute`:

```bash
# preview (no deletion) — prints "DRY RUN — would delete N …"
PYTHONSAFEPATH=1 python3 scripts/orphan_cleanup.py delete \
  --in /tmp/orphan_confirmed.txt

# counts only (no uid prefix, no sample): the no-email path's dry run
PYTHONSAFEPATH=1 python3 scripts/orphan_cleanup.py delete \
  --in /tmp/no_email_confirmed.txt --counts-only

# real deletion (start small to confirm the grant works)
PYTHONSAFEPATH=1 python3 scripts/orphan_cleanup.py delete \
  --in /tmp/orphan_confirmed.txt --execute --limit 10
PYTHONSAFEPATH=1 python3 scripts/orphan_cleanup.py delete \
  --in /tmp/orphan_confirmed.txt --execute --batch 100 --pause 5
```

**Pace.** Each deleted Auth account fires the auth `onDelete` Functions, so a large run goes in
batches of 100 (the default) with `--pause` seconds between them. Before a run of thousands, tell
the Firebase owner the count and pace first: they can say which Functions fire and whether the
pace is safe.

With `--execute` it first backs up the confirmed accounts' full auth records to a sidecar
file (mode 600) (Firebase deletion is irreversible — the backup is merged against the full input set so
a rerun never deletes a UID that wasn't backed up), then deletes via `accounts:batchDelete`
(`force=true`), **resumable** (each deleted UID is appended to a log and skipped on rerun),
with backoff/retry on `INSUFFICIENT_PERMISSION` / `429` / `5xx`, and verifies a spread of
deleted UIDs are gone via `accounts:lookup`.

### 6. Report and clean up the grant

Report deleted / error / skipped counts, then **remind the user to remove the grant** —
the AI's SA must not retain bulk-delete power:

```bash
# user-gcloud: an IAM grant to the service account is the user's own action
gcloud projects remove-iam-policy-binding firebase-auratech16 \
  --member="serviceAccount:<gcp-sa-email>" \
  --role="roles/firebaseauth.admin"
```

## Safety (non-negotiable)

- **Dry-run by default — enforced in code.** `delete` does nothing unless `--execute` is
  passed, so a stray invocation can't bulk-delete. No deletion without an explicit "delete
  them" after the Step 4 preview.
- **Only Firebase credentials are deleted.** Never call the UserService `deleteUser` flow here.
- **No-email accounts take `confirm-no-email`, never `confirm`.** It adds the exact RTDB
  `/users/{uid}` check and the claim check, because no email lookup can vouch for them.
- **Age/activity gates protect real people.** Default skips accounts created within 2 days
  (signup-race) or active within 7 days (currently-trying users). A months/years-old account
  still actively signing in may be a *deleted-then-reauth* case — surface it for review rather
  than auto-deleting if restoration could matter.
- **The delete grant is temporary** and must be removed after.
- **Deletes are batched, resumable, and verified;** records are backed up first.
- **PII:** mask end-user emails to the domain in any output; full UIDs are fine.

## Edge cases

- **Harvest returns nothing** → no orphans authenticated in the window; widen `--since` or
  report a clean result.
- **`POST /users/email/lookup` returns `200`** for a candidate → not an orphan (its email
  resolves to a live profile — e.g. an existing user re-logging-in with another provider);
  both `recent` stage 2 and `confirm` drop it automatically (reported as `sso-resolvable`).
- **Grant vanishes mid-run** (org IAM auto-remediation can revert SA roles within minutes) →
  the resumable log means you re-add the grant and rerun to continue from where it stopped.
- **Need a full exhaustive audit** (dormant orphans that never authenticate) → use the
  `reconcile` mode (Step 2C): full Firebase export + Postgres dumps → bucket report + candidate
  lists. `recent` only sees the last N hours; `harvest` only sees orphans that authenticated in
  the log window.
- **`reconcile`: locked-out accounts (active profile, no live auth)** are reported but are
  **not** delete candidates — they're users who lost their auth, a different problem.

## Scripts

- [`scripts/orphan_cleanup.py`](scripts/orphan_cleanup.py) — `recent` (sorted
  `accounts:query` for the last N hours → `GET /users/<uid>` pre-filter → read-only
  `POST /users/email/lookup` resolution → true orphan UIDs, sso-resolvable re-logins reported
  separately; no dump), `harvest` (GCP log query → candidate UIDs), `reconcile` (full Firebase
  export + Postgres dumps → bucket report + candidate lists), `confirm` (lookup + gates →
  read-only email resolution → confirmed orphans), `confirm-deleted` (soft-deleted profile
  with live Auth → live re-check → confirmed uids), `confirm-no-email` (profileless, no email →
  live re-check incl. exact RTDB `/users/{uid}` → confirmed uids, counts-only report), `delete` (resumable `accounts:batchDelete` + verify,
  dry-run unless `--execute`, batches of 100 paced by `--pause`).
- [`scripts/rtdb_get.js`](scripts/rtdb_get.js) — byte-identical copy of `eng:read-rtdb-paths`'s reader.
