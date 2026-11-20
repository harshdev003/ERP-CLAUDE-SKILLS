# Traps

Each of these happened on the first erasure handled with this skill (ENGSUP-160). Each one fails
quietly: the step looks done, and the data is still there, or new data was created.

## Writes disguised as reads

- **The admin/support tool creates Stripe customers.** Opening a user calls
  `POST /payments/stripe/getCustomerId/{uid}` with `/subscriptions/list/{uid}`. `getCustomerId`
  (PaymentService `src/models/stripe.js`) creates a customer when none exists, copying email,
  country and UID to Stripe. On ENGSUP-160 that happened 3 minutes before the request reached
  engineering, for a user who never paid. **Never open the requester in the admin tool.** Tell
  Support this in the first thread reply. Compare Stripe `customer.created` with the request date.
  Fix tracked in ENGBACK-1874.
- **Iterable unsubscribe creates the profile.** `POST /iterable/users/unsubscribe/{uid}` syncs an
  unsynced user (`synchedAt` null) before unsubscribing. Iterable's own `updateSubscriptions` also
  creates a missing user. Look the person up first; with no profile, make no call. Fix tracked in
  ENGBACK-1875.
- **The account delete writes new events about the person** into BigQuery `firebase.raw_events`
  (`UserUpdated`, `UserDeleted`, permission events). Clean them up after the streaming buffer
  flushes.

## Postgres

- **Repo schema ≠ production.** `create.sql` missed `receiptBackup-02042022` and
  `journal.coachId`, and showed `referral.userId` and `charge.familyPlanPrimary`, which don't exist.
  Build every count and delete from production `information_schema` (sql.md → Discovery).
- **One server, many databases.** A query in the wrong DB fails with `relation does not exist`.
  Start every statement with `current_database()`, or check it first.
- **Sweep every database**, not the three you expect. `emailBackup` and `notificationBackup` held
  22 rows of this person's emails and push messages, and the account delete never touches
  `*Backup` tables.
- **CTE column `id` collides with `"user".id`.** Name the person's id `pid` and alias the table.
- **Multi-statement blocks don't run as written** in a human's SQL client. DBeaver ran only the
  first statement of a `BEGIN; …; COMMIT;` block, then left the delete in an open transaction:
  their own count read 0 while the row persisted and stayed locked. Give one statement at a time,
  and verify from a separate session. `pg_stat_activity` shows `idle in transaction`. Use
  `SET lock_timeout='5s'` on deletes so a held lock fails fast.
- **Some deletes are slow.** The feedDB `"user"` delete took ~2 minutes because of foreign-key
  checks on unindexed `feedBackup.actorId` (30 GB) and `user.familyPlanPrimary` (4.5 GB). Use a
  long timeout; it's not a hang.
- **Export before delete.** When access is also requested, `SELECT *` every row found into the pack
  *before* deleting. On ENGSUP-160 the push messages were deleted first, so the access answer
  could only give a count and dates.

## Vendors

- **Aurie's Iterable project has non-unique emails.** Email calls return 400; use `userId`.
- **Stripe key is read-only for customers.** A human deletes in the dashboard. `customers/search`
  needs a `Stripe-Version` header of 2020-08-27 or later (the account default is older).
- **RTDB deletion is confirmed by reading the nodes, with the audit line deciding what is safe to
  read.** `scripts/check_rtdb_gone.py` reads `users/{uid}`, every node FirebaseDenormalizer's
  `userDeleted` removes (its list, kept in the script), the per-user nodes it never touches and
  the `/deletedUsers/{uid}` tombstone (90 nodes, Firebase's ENGSUP-171 list), in one batch
  through `rtdb_get.js`: Admin SDK realtime protocol, exact paths, never REST, no query or shallow
  read, at most 20 in flight. A deleted node reads as nothing, so `null` really means gone, for a
  leaf value as well as a list.
  - **The helper caps its output, not its download.** It prints at most a child count, but a list
    node whose delete failed comes down whole (`/userListened` can hold 17k entries). So list
    nodes are read only when FirebaseDenormalizer's log vouches for them: its audit line ("Deleted
    secondary nodes for user '{uid}'", always kept, `removed` and `failed` counts as floats) and,
    when `failed` > 0, the run's error lines, which share its `transaction` and carry the failed
    `reference`. `scripts/check_rtdb_audit.py` reads both.
  - Failed list nodes named by the error lines: not read, reported present by name. No summary
    line (a crash or the 540 s timeout), a log read error, or failed nodes not all named: list
    nodes are `unverified` and not read; small nodes (`users`, `deletedUsers`, `userAddress`,
    `userShirtSize` and the few settings nodes listed in the script) are read anyway. Reading list
    nodes after a crash was declined (Jerome, 2026-11-11): the Firebase session handles those.
  - The read wins over the log: present on a read is present, whatever the log said.
  - **Nodes `userDeleted` never removes** (`userEvents`, `customPlaylist`,
    `downloadedTracks`, `familyPlanInvites`, `referralFriends`, …) have no audit line, so their
    list nodes are never read here; the Firebase owner checks them (`for_firebase`). Nodes keyed by
    entry id with a `userId` field (moods, goals, feedback, emotionStates, trackPlays, receipts)
    and reverse indexes keyed by content id can't be read by exact path; checking them is a query,
    which needs the user's yes first.
  - **The `/deletedUsers/{uid}` tombstone** (`deletedAt` only) stops late writes from recreating
    the user. It reads present after every account delete; the erasure removes it last.
  - **`users/{uid}`: standing OK (Jerome, 2026-10-24).** One exact read of that request's uid, exists
    / gone only. Never the `/users` root, never `?shallow=true`, never listing or querying it, never
    any other user.
  - Ask the person running the skill first: `--dry-run` lists what will be read. Erasures are rare;
    there are no unattended RTDB reads.
  - Never print or keep a returned value or path: it is the data subject's data. The script prints
    node names and counts only.
  - Never list a large node's keys, even with an OK; use a batch job (export or batch builder) run by the code owner with Jerome's go.
- **Firebase Storage: read yes, delete no.** `GCP_KEY` has Storage Object Viewer on
  `firebase-auratech16.appspot.com` (since 2026-10-24), so listing the person's prefixes works.
  Deleting objects still needs Object Admin; until then the human deletes them in the Firebase console. A `picture` on graph.facebook.com is Facebook's, not a file of ours.
- **Mixpanel export lags a few hours.** An empty result right after the delete isn't final;
  re-check before the reply goes out.
- **GCP log sweep times out** (504) on a busy day. Use 6-hour chunks that split on failure, and
  record any window that still fails.
- **Label every RTDB and GCP call** with the `AuraClaude` user agent (the scripts do it).
  Unlabelled reads look like Support traffic in load alerts.
