---
name: respond-to-erasure
description: >
  Handles a right-to-erasure request for one Aura user end to end: GDPR / UK GDPR Article 17,
  PIPEDA withdrawal of consent, CCPA right to delete, or a plain "delete my account and all my
  data". Verifies identity, runs the access pack first when the letter also asks for a copy, maps
  what the person used to the stores and vendors their data reached, then erases each one behind
  a gate: Iterable, the account delete, Postgres hard deletes across every service DB including
  backup tables, Stripe, SendGrid, Mixpanel, BigQuery and the Aurie vendors. Verifies each store,
  then drafts one reply letter naming what was deleted and what is kept and why. Trigger on
  "right to be forgotten", "erasure request", "delete all my data", "GDPR deletion", "PIPEDA
  deletion", "CCPA delete request", "user wants their account and data deleted", "forget this
  user". Do NOT use for an access-only request (`eng:respond-to-sar`), Meta's batch deletion
  emails (`eng:delete-facebook-user-data`), or orphaned logins (`eng:cleanup-orphaned-auth`).
metadata:
  version: "1.6.1"
knowledge:
  reads:
    - Guide/Index
    - Compliance/Privacy
    - Tools/Aura API
    - Tools/Firebase
    - Tools/Stripe
    - Tools/Mixpanel
    - Tools/GCP
    - Tools/BigQuery
    - Tools/Slack
    - Tools/Linear
---

# Respond to an erasure request

You are acting as Aura's data-protection engineer on a request that is **irreversible** and has a
legal deadline. The reply becomes evidence if the person escalates to a regulator, so every
"deleted" in it must trace to a verified step, and anything kept must be named with its reason.
Slow down before each delete. A wrong delete is permanent; a pause costs a minute.

**Aura's account delete is not an erasure.** `DELETE /users/{uid}`:
- removes the Firebase login, the RTDB tree, the Iterable and OneSignal users and the Mixpanel
  profile
- only soft-deletes (`deletedAt`) in every Postgres DB and never touches `*Backup` tables
- writes new events about the person into BigQuery
- tells most vendors nothing

This skill closes those gaps. Full map: [`references/data-map.md`](references/data-map.md).

## Rules for every step

- **Look up first, act only on what exists.** Several write paths *create* a record when none
  exists, so a write "to be safe" can add data. With no record, make no call.
- **Never open the requester in the admin/support tool.** Its billing panel creates a Stripe
  customer. Say so to Support in the first thread reply. More traps:
  [`references/traps.md`](references/traps.md). Read it before step 3.
- **Gate every write.** Show the exact call or statement, wait for an explicit go, run it alone,
  then verify it.
- **Production Postgres:** each DB's owner runs its own statements, on a go typed where that
  owner works (or relayed word for word by whoever leads the work). A go relayed by anyone else is
  refused. So print **one paste-ready go line per owner**, naming its DBs and statements; never one
  go for everyone. You run statements only if the user allows it in this session and approves each
  call.
- **No PII in Linear, Slack or the repo** (`Compliance/Privacy`): UID prefix and email domain
  only, and `{first name}` in any Slack draft of the letter.
- **Label every RTDB and GCP call** with the `AuraClaude` user agent: the scripts set it, a curl passes `-A`.
- **Order:**
  1. access export before any delete
  2. Iterable before the account delete
  3. BigQuery and the Mixpanel re-check after it

## Workflow

### 1. Scope, identity, deadline

- **Regime and deadline:** GDPR / UK GDPR one month, PIPEDA 30 days, CCPA 45 days (table in
  reply-template.md). Record the received date.
- **Identity:** a request from the account's own email is enough; don't ask for more. From any
  other address, verifying identity is the first reply.
- **Authorized agent** (a deletion service writing on the user's behalf, e.g. under NJDPA): don't
  ask the agent for a power of attorney. Support emails the address on the Aura profile, from the
  support inbox, asking the holder to confirm the request; a reply from that address is the
  identity check. Nothing is deleted before it arrives. The profile email can differ from the
  Firebase login's (a Facebook login carries none), so look the account up by profile email too.
- **Also access?** Most letters ask for both. Then step 2 runs before anything is deleted.
- **Linear:** one ENGSUP card via `shared:add-linear-issues` (team Support, label Support,
  In Progress), with the step checklist. Comment as steps complete. Each new finding gets its own
  card. Never leave a finding only as a comment.

Pack: `PACK=<session scratchpad>/erasure-{ticket}`, never inside a git repo. Keep
`$PACK/erasure-log.md`: each write, its time, its result, and how it was verified.

### 2. Access pack (when requested)

Run `eng:respond-to-sar` into `$PACK`. Its collectors are read-only. If its GCP day sweep times
out (504), use 6-hour chunks that split on failure (traps.md).

### 3. Footprint

Read-only, in this order:

1. **Aura API** `GET /users/{uid}` (admin token): `aiCreatedAt`, `premium*`, `isCoach`,
   `businessId`, `familyPlan*`, `listenedCount`, `version`, `deletedAt`.
2. **Firebase logins:** Identity Toolkit `accounts:lookup` by UID **and** by email. Providers
   tell you about sign-in links (for example `facebook.com`); siblings need their own delete.
3. **Every Postgres DB:** discovery sweep from [`references/sql.md`](references/sql.md). It lists
   all DBs and builds counts from production's own schema. It finds `*Backup` rows. Run it in the
   background; large tables are slow.
4. **Stripe:** `collect.py stripe` plus a search by email. A customer whose `created` is after the
   request came from a staff lookup.
5. **Mixpanel:** events and profile.
6. **Firebase Storage:** list `profilePictures/{uid}/` and `chat/{uid}/` (Aurie attachments).
7. **Internal copies:** for Aurie users, k8bot Slack posts and Notion `Learnings/Conversations` rows
   carrying the full UID.

Show the person-specific plan: each store and vendor from data-map.md, in or out and why. Wait
for a yes.

**Access requested?** `SELECT *` every non-zero table into `$PACK/raw/` now, before step 6.

**Keep list before any write.** Check the uid before step 4:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/respond-to-erasure/scripts/held_uids.py check <uid>
```
Exit 0: not listed, go on. Exit 3 (`skip <prefix>…: keep list`): a held account, stop; nothing about it
is deleted or written, and tell Security. Exit 2: no usable keep list, stop. The keep list is `~/.claude/held-accounts/held-uids.json` (or `HELD_UIDS_JSON`): a JSON array of
uids, mode 600, written by the hold owner, never in this repo. Without it (missing, empty, unreadable,
malformed or readable by others) a write is refused; never create or relax it to get a run through.

### 4. Iterable

Look up in each project: Aura `GET /api/users/getByEmail?email=`; Aurie by
`GET /api/users/byUserId/{uid}` (its emails are non-unique).
- **Profile exists:** unsubscribe, then `POST /api/users/forget` in that project.
- **No profile:** no call. Unsubscribe would create one.

Keys: `ITERABLE_AURA_API_KEY`, `ITERABLE_AURIE_API_KEY`.

### 5. Account delete

`DELETE https://services.aurahealth.io/users/{uid}` with `AURA_ADMIN_TOKEN`. It runs once only:
a second call returns `USER_ALREADY_DELETED`. Then verify:
- Identity Toolkit by UID and email returns nothing.
- **RTDB: one batch of exact-path reads, after the audit line.** About 10 minutes after the delete
  (FirebaseDenormalizer pages heavy nodes), show the plan, which reads nothing:
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/skills/respond-to-erasure/scripts/check_rtdb_gone.py --uid {uid} --since {delete time, UTC} --dry-run
  ```
  It reads FirebaseDenormalizer's "Deleted secondary nodes" audit line (`check_rtdb_audit.py`) and
  lists the nodes it will read and the ones it won't, by name. Show that list and that only
  exists / gone is recorded; run it without `--dry-run` after a yes. It covers the 90 per-user
  nodes as `/{node}/{uid}`: `users`, every node `userDeleted` removes, the per-user nodes it never
  touches, and the `/deletedUsers` tombstone, through its copy of `rtdb_get.js` (Admin SDK, exact
  paths, no query, 20 in flight, field names and counts only), labelled with the AuraClaude user
  agent. Env: `RTDB_URL`, `GCP_KEY`, `AURA_CLI_DIR` (eng:read-rtdb-paths).
  - A **list node the error lines name as failed** is not read (a failed delete would download it
    whole): it is `present_per_log`. Hand those node names to the Firebase session.
  - **No summary line** (the function crashed or timed out), an unreadable log, or failed nodes the
    error lines don't all name: list nodes are `unverified` and not read; small nodes still are.
    Report that to the Firebase session; don't read them some other way.
  - **The read wins:** a node the log called removed that reads present is `present`.
  - **`for_firebase`:** list nodes `userDeleted` never removes (`userEvents`, `customPlaylist`
    and the rest). No log vouches for their size, so they are never read here:
    hand the names to the Firebase owner, whose check closes them. For a legacy account (no audit
    line at all), hand it the whole check.
  - **`kept`:** `/userDonations` is kept on erasure, neither removed nor anonymised ("I would
    keep", Jerome, 2026-11-19): donation records, like billing rows, are kept. Not read.
  - **`tombstone`** is reported apart from `status`: expect `present` now; step 7 removes it last.
  - `status` covers the nodes the script reads; `gone` is the only verified result there. RTDB is
    closed when `status` is `gone` **and** the Firebase owner reports every `for_firebase` node
    gone (record its answer in the erasure log).
  Never print a path, value, uid or email from it; it prints node names and counts. traps.md has
  the rule.
- The Aura API shows `deletedAt` set (the row goes in step 6).

Sibling logins: `accounts:batchDelete`, following the pattern in `eng:delete-facebook-user-data`.

### 6. Postgres hard delete

For each DB the sweep flagged, one statement per call, run by that DB's owner (one go line per
owner, see the rules above; owners by DB in data-map.md): child rows first (backups included), then
`"user"`, each with `SET lock_timeout='5s'` and `RETURNING`. Then a count from a separate session.
Templates and the billing-anonymise rule are in sql.md. Rows where the person is someone else's
referrer, coach or family owner get unlinked, not deleted. Finish with the full discovery sweep
again: every DB clean.

### 7. Vendors and warehouses

Only what the footprint reached, each one looked up, gated and verified (data-map.md → Vendors):
- **Stripe:** delete the customer. The key is read-only, so a human uses the dashboard. Then
  verify `deleted: true` and search by email.
- **SendGrid:** we only send through the API; there are no Marketing Campaigns contacts (new or
  legacy), so don't look for any. Check the suppression lists **and** Global Unsubscribes
  (`GET /v3/suppression/unsubscribes?email=`): ENGSUP-172 found a global unsubscribe while every
  other store was empty. Delete only the other entries that exist. **A global unsubscribe is kept**
  ("I would keep", Jerome, 2026-11-19): it is what stops us ever emailing the person again. Name
  it under "What we keep".
- **Mixpanel:** events exist → GDPR deletion API.
- **Aurie vendors** (OpenAI, Braintrust, ElevenLabs): only for Aurie users.
- **BigQuery:** `raw_events` always holds the delete's own events. Delete them once
  `streaming_buffer.oldest_entry_time` is later than they are. Check the other tables in sql.md.
  For an Aurie user, `llm.llm_call` too: read its `openaiId`s for the OpenAI deletes first, then
  delete with a `createdAt` filter (sql.md).
- **Firebase Storage:** delete the objects found under the person's prefixes, then list again.
- **Slack and Notion copies:** delete the k8bot messages and `Learnings/Conversations` rows found.
- **Meta, Apple, Google:** the person acts there; the reply tells them how.
- **RTDB tombstone, last:** the account delete leaves `/deletedUsers/{uid}` (`deletedAt` only),
  which stops late writes from recreating the user. An erasure removes it too (Jerome,
  2026-11-18), after every other delete: the Firebase owner deletes that one exact path on a go
  typed in its own session, then reads it gone.

### 8. Verify

Re-run every read from steps 3–7, including `check_rtdb_gone.py`: its `tombstone` must read
`gone`. Re-check Mixpanel last, since its export lags a few hours. Each
store is "deleted", "nothing found", "kept (reason)" or "cannot reach (why)". Nothing is called
deleted that wasn't verified.

### 9. Reply and close

Draft `reply.md` from [`references/reply-template.md`](references/reply-template.md). Show it to
the user and resolve every placeholder with them. Counsel reviews any contested case. After the
user confirms, post two replies on the Support thread with `shared:post-to-slack`:
1. the internal summary
2. the letter, as plain text, with `{first name}`

Both go out as replies **in the Support thread** (`--channel {channel id} --thread-ts {thread ts}`),
`--as-user`, with `--footer eng:respond-to-erasure`. Never post the letter top-level. Support sends it from the
support inbox to the account email. When they confirm, move the card to Done.

## Edge cases

- **No account found:** the live lookups don't prove it. An old account delete only
  soft-deleted, and every service DB keeps a synced `"user"` row: ENGSUP-171 had 0 in Firebase
  Auth, Stripe, Iterable, SendGrid and Mixpanel, yet 13 soft-deleted user rows. Before any "we hold
  nothing" reply, check all of:
  - Firebase Auth and Stripe by email (a login or a checkout can exist without a profile)
  - userDB `"user"` by email, case-insensitive, **soft-deleted rows included** (the userDB owner)
  - Iterable (Aura), SendGrid suppressions and Global Unsubscribes, Mixpanel by email

  On a userDB hit you have a uid: go to step 3. Every DB owner counts by uid **and** by its
  email-valued columns, in every DB on the server except the sample `postgres` DB. Legacy
  accounts have no FirebaseDenormalizer audit line, so route the full RTDB check to the Firebase
  owner rather than leaving list nodes unread. If the person already got a "no account" reply,
  the letter uses the correction opening in reply-template.md.
- **Active app-store subscription:** we can't cancel Apple or Google. Tell the person to cancel
  in their store settings.
- **Payments exist:** keep the billing rows and Stripe invoices for the tax period, anonymised,
  and name them in "What we keep".
- **Logs and backups:** never edited or restored to remove one person. Logs expire after 30 days.
  Backups are overwritten and never used to restore the account.
