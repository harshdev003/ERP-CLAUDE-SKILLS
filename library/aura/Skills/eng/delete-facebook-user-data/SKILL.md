---
name: delete-facebook-user-data
description: >
  Process Meta's "User Data Deletion Request for Aura" emails: take the app-scoped Facebook ID
  CSV(s) a human downloaded from the Meta App Dashboard, find each ID's Aura login in Firebase
  Auth, classify it (own account, secondary Facebook login on another live profile, orphan, or
  not in Aura), then delete through the right path — the UserService account delete for an own
  account, a Firebase login delete otherwise — with a plan and an explicit go first. Files one
  Linear sub-card per email batch (Aura UIDs and counts only) and deletes the local files after.
  Trigger on "Facebook data deletion request", "Meta data deletion email", "User Data Deletion
  Request for Aura", "delete these Facebook users", "process the Facebook deletion CSVs", or a
  pasted Meta deletion email with downloaded ID files. Do NOT use for a user asking Support to
  delete their account, orphaned-auth cleanup (`eng:cleanup-orphaned-auth`), or a GDPR
  access request (`eng:respond-to-sar`). This skill DELETES production data — every delete is gated.
metadata:
  version: "1.4.0"
knowledge:
  reads:
    - Guide/Index                 # baseline routing aid (universal)
    - Tools/GCP                   # GCP_KEY service account, IAM grant
    - Tools/Firebase              # Identity Toolkit accounts:lookup / batchDelete, multi-login model
    - Tools/Aura API              # GET /users/:id, POST /users/email/lookup, DELETE /users/:userId
    - Tools/Linear                # per-batch sub-cards
    - Compliance/Privacy          # Aura UIDs + email domains only on external surfaces
---

# Delete Facebook User Data

Meta emails a developer admin whenever people who used **Facebook Login** on Aura ask Meta to
delete their data. Each email points at a list of **app-scoped Facebook user IDs** (one ID
per person per app) that we must remove from our systems. IDs we don't hold can be ignored.
Each file expires 60 days after the email.

**Why this is manual today:** no Data Deletion Callback URL is registered, so Meta sends emails
instead of calling us. Registering one stops the emails — tracked on
[PROD-7536](https://linear.app/aura-health/issue/PROD-7536/facebook-user-data-deletion-request-for-aura).
Once it's live, this skill only serves any backlog.

## Step 0 — The human part (cannot be automated)

The user downloads the IDs: Meta App Dashboard (app **Aura**) → Settings → Advanced →
**Download User Identifiers**. Login needs phone 2FA, so if they're away from their phone,
another admin or developer on the app can do it. One CSV per email; name each after its email
date (e.g. `2026-10-13.csv`). They hand you the paths.

Never ask the user to share the Meta login, and never drive the Meta dashboard yourself.

## Step 1 — Parent card

Find or create the parent card under **Security and Privacy**
([PROD-6926](https://linear.app/aura-health/issue/PROD-6926)) via `shared:add-linear-issues`,
reusing PROD-7536 while it's open. Create **one sub-card per CSV** titled
`Facebook data deletion request — <date> batch (<n> IDs)`: Backlog, label `Security & Safety`,
estimate 1, Meta file expiry = email date + 60 days.

**Never put Facebook IDs in Linear** — a deletion request must not leave a durable record of
who asked. Aura UIDs and counts only.

## Step 2 — Plan (read-only)

```bash
PYTHONSAFEPATH=1 python3 ${CLAUDE_PLUGIN_ROOT}/skills/delete-facebook-user-data/scripts/fb_deletion.py \
  plan --csv <file1.csv> <file2.csv> … --out <scratchpad>/fb_plan.json
```

Needs `GCP_KEY` and `AURA_API_TOKEN`. Run from the `python3` that has `google-auth` — the
user's shell often has neither the library nor `GCP_KEY` (they live in Claude's settings env),
so run it from your session rather than handing them the command.

Each Facebook ID gets one class:

| Class | Meaning | Delete path |
|---|---|---|
| `account` | The Facebook login **is** the profile's own UID (`GET /users/:uid` → 200) | Full account delete (Step 4) |
| `secondary` | No profile under the UID, but its email resolves to a live profile under **another** UID | Delete only the Facebook login; keep the main profile |
| `orphan` | No profile, nothing resolves (often no email at all) | Delete the Facebook login |
| `not_found` | No Aura login holds this Facebook ID | Nothing — Meta says ignore |
| `error` | A lookup returned something other than 200/404 | Stop and investigate; never delete on uncertainty |

**Why `secondary` exists:** one Aura profile can sit behind several Firebase logins — UserService
resolves a login with no profile by email (user table, then the email-mapping table). A person
who tapped "Continue with Facebook" once while already having an account gets a separate
Facebook login. The full account delete only removes the login whose UID equals the profile id,
so it returns `USER_NOT_FOUND` for these and deletes **nothing**.

**Secondary → keep the main profile (default).** Meta asks for the data we got *from Facebook*;
the main profile came from a different login. Deleting it would wipe an account the person may
still use or pay for. Surface the count and let the user override.

**Keep list.** `delete-accounts` and `delete-logins` drop every uid on the keep list before any
backup or delete, dry runs included (`skip <prefix>…: keep list`, `kept: N`). The keep list is `~/.claude/held-accounts/held-uids.json` (or `HELD_UIDS_JSON`): a JSON array of
uids, mode 600, written by the hold owner, never in this repo. Without it (missing, empty, unreadable,
malformed or readable by others) a write is refused; never create or relax it to get a run through.

## Step 3 — Present and gate

Show the plan table (batch, class, Aura UID, email domain only, main profile) and the counts.
Then **stop** and ask for an explicit go. A vague "looks right" is not a go.

## Step 4 — Delete accounts (`account` rows)

```bash
PYTHONSAFEPATH=1 python3 …/fb_deletion.py delete-accounts --plan <scratchpad>/fb_plan.json            # dry run
PYTHONSAFEPATH=1 python3 …/fb_deletion.py delete-accounts --plan <scratchpad>/fb_plan.json --execute
```

Calls UserService `DELETE /users/:userId` with `AURA_ADMIN_TOKEN` (admin or service role).
`204` means it ran the whole flow: cancelled subscriptions, deleted the Firebase login and data,
soft-deleted the Postgres row (`deletedAt`), removed tokens, logged to Iterable, and deletes the
Mixpanel profile after a delay. Any other status means **not deleted** — report it as such.

## Step 5 — Delete logins (`secondary` + `orphan` rows)

The user adds a temporary grant first (an IAM change is theirs to make):

```bash
# user-gcloud: an IAM grant to the service account is the user's own action
gcloud projects add-iam-policy-binding firebase-auratech16 --member="serviceAccount:<sa-email>" --role="roles/firebaseauth.admin"
```

```bash
PYTHONSAFEPATH=1 python3 …/fb_deletion.py delete-logins --plan <scratchpad>/fb_plan.json --backup <scratchpad>/fb_backup.ndjson            # dry run
PYTHONSAFEPATH=1 python3 …/fb_deletion.py delete-logins --plan <scratchpad>/fb_plan.json --backup <scratchpad>/fb_backup.ndjson --execute
```

It backs up the records, runs `accounts:batchDelete`, and verifies none remain. Then remind the
user to remove the grant (`remove-iam-policy-binding`, same arguments).

## Step 6 — Cards and cleanup

1. Comment on each sub-card: matched / deleted / not found, with the Aura UIDs and what
   happened to each (account deleted, Facebook login deleted with main profile kept, orphan
   deleted). No Facebook IDs, no full emails.
2. Move each finished sub-card to **Done**.
3. Delete every local file holding Facebook IDs or login records: the CSVs, the plan JSON, the
   backup NDJSON. The backup exists for a botched run, not as an archive — keeping it keeps the
   very data Meta asked us to delete.

## Gotchas

- **The CSV starts with a BOM.** Read as plain UTF-8 it glues onto the first ID, which then comes
  back "not found" — silently, once per file. The script reads `utf-8-sig`.
- **`USER_NOT_FOUND` from the account delete means nothing was deleted**, not "already gone".
  Re-plan; it's a `secondary` or `orphan`.
- **The account delete leaves sibling logins behind.** If an `account` row's person also has
  other logins sharing their email, those survive the delete. Not in scope for Meta's request,
  but worth noting on the card.
