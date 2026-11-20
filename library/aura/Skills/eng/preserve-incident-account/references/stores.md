# Stores: how each one behaved on a real run

From the first two incident-account preservations. Each owner reads its own section before running
its go.

## Order, and why

1. **Live state first.** One `accounts:lookup` by `localId` gives exists, disabled, `lastLoginAt`,
   `lastRefreshAt` and `validSince` (tokens valid after). One run's notes said the account was
   deleted. It was live and enabled, and an analysis built on that note had to be withdrawn.
2. **Logs first among the copies,** because they are the only store that expires on its own. Read the
   real retention (`account.py resolve` prints `_Default`'s `retentionDays`; it was 90 days from 11-02,
   not the 30 days once assumed) rather than hard-coding an urgency date. Before copying, list the
   existing `eng:preserve-prod-logs` hold buckets: on one run they already held the account's whole
   window, so only the focused bundle was needed.
3. **Every snapshot before any change.** One run disabled the account before snapshotting it,
   so its "before" snapshot already shows `disabled`.
4. **Containment last,** after `account.py verify` passes: disable (never delete), revoke refresh
   tokens, read back, post-containment snapshot; then the token step below.

## Owners and gos

- **Each owner runs its own leg on a go Jerome types in that owner's session.** On one run, a
  relayed "GO" was refused by every database owner. So the plan prints one paste line per owner.
- The go lines carry the full uid (they are typed into local sessions) but never the email: they
  name the mode-600 `email.txt` path instead. Pass that path to the owner, never the value.
- Owner keys map to sessions in the personal file `~/.claude/preserve-incident-account/owners.json`
  (`{"firebase": "<session>", …}`); without it, a line names the owner's role.

## Firebase (owner: firebase)

- **Auth:** Identity Toolkit `accounts:lookup` by `localId`. Drop `passwordHash`, `salt`,
  `passwordUpdatedAt` and `validSince` from the stored record (same set as `preserve-prod-logs`'s
  `run_step.AUTH_DROP`). Look up by email too, to find other accounts sharing it.
- **RTDB:** the uid-keyed nodes come from `rules.json`: every top-level `$wildcard` that compares
  `auth.uid` or `auth.token.userId` (82 on 11-05). Read each as `<node>/{uid}`, exact path; never the
  root, never shallow. `emailUser/{base64(lowercased email)}`, plus the as-stored case if it differs; the
  key encodes the email, so keep it out of output. Referrer: `users/{uid}/referrer` →
  `referralFriends/{rid}/{uid}`.
- **The referral code lives only in RTDB** (`users/{uid}/referralCode`). paymentDB had no row for it,
  even though raw_events logs `ReferralCodeCreated` from payment-service.
- **Storage:** `gs://firebase-auratech16.appspot.com/profilePictures/{uid}/` (0 recorded too).

## Service databases (owners: service-db, payment-db, intelligence-db, content-db)

- **Every service keeps its own `user` copy,** created at signup even when the account never used the
  service: userDB, coachDB, paymentDB, intelligenceDB and contentDB each had one. One row per DB.
- **The scan recipe** (Backend's, which found a `userNoProfile` row nobody had listed): every public
  table in each DB; text columns named `*id`, `*userId` or `*uid` compared to the uid; uuid columns
  compared to the uuid; jsonb top-level keys equal to the uid (this catches `live.reservations`).
- **Email-keyed scan:** every text column whose name contains "email", case-insensitive. Big tables
  time out on `lower()`; fall back to an exact match there and record that.
- **userDB `email` can be null** while Auth has it. Resolve the email from Auth, never from Postgres.
- **Not scanned is not 0:** paymentDB `notification.data` (store payloads keyed by store ids) timed
  out. Record "not scanned (keyed by store id)".
- **Without DB access,** the admin API `GET /users/{uid}` (`AURA_API_TOKEN`) returns the userDB row.
  Token rows and the other DBs still need the owner.
- IntelligenceService's `llmCall` ledger is BigQuery `llm.llm_call`, and 4U-engine writes only to
  BigQuery `aura-main.activation`: both are in the analytics leg.

## Analytics (owner: this skill, `export_analytics.py`)

- **BigQuery:** `firebase.raw_events` (uid fields, `$.email`, `$.paypalEmail`, `$.uuid`, the referral
  code anywhere in `data`), then every table in `firebase`, `llm`, `aurie_cost` and `activation` with a
  STRING user-id column. `llm.llm_call` refuses a query without a `createdAt` filter; the script adds a
  partition filter to every partitioned table.
- **Jobs run in aura-main** (GCP_KEY can't create jobs in firebase-auratech16). An EXPORT that fans out
  needs `storage.objects.delete` to clean up temp files, which the hold bucket never grants; the script
  writes one file per source (`ORDER BY 1 LIMIT n`). A source with 0 rows writes no object and is
  recorded as a zero.
- **Mixpanel:** the old `aura-main` `mixpanel` dataset stopped in 2022. The `mixpanel-prod-1` copy is
  current to within a few days and gives the device ids merged with the uid; the Export and Engage
  APIs cover the rest. Engage found nothing by the plain uid on one account and found the profile by
  the merged `$device:` ids. An Export API zero by email or uuid is weak evidence: events rarely carry
  either.

## Logs (owner: logs)

- **IP filter** (`httpRequest.remoteIp=`) is fast.
- **Free-text id search is slow:** a month of firebase-auratech16 `_Default` ran for 15+ minutes, and
  two in parallel hit `429 Read requests per minute`. Narrow it to the account's lifetime (created → now),
  one project at a time, with a 20 s pause between.
- **Tag ours vs the actor.** Most hits after an incident are our own: cleanup GETs
  (`AuraClaude/eng:cleanup-orphaned-auth`), BigQuery `data_access` lines from the investigation, the
  preservation's own reads. Tag by user agent (`AuraClaude/…`), principal (`@aurahealth.io`, the
  `claude@` service account) and the known team networks, and report "actor activity after the
  incident: N" apart from "ours: M". Our GKE node egress shows up as extra IPs on proxied calls; tag
  it, it isn't a second actor. A user-agent family (for example a `Claude-User` fetch) can identify the
  actor's tooling: list UA families in the summary.
- Useful log names for an account timeline: `requests` (LB), `identitytoolkit.googleapis.com/requests`,
  `firebase-auth`, user-service `stdout`, `firebase-producer`, `cloudsql postgres.log`.

## Other sources

- **Bugsnag:** `eng:search-bugsnag-by-user --user` for the uid and for the uuid.
- **Iterable:** both projects. `users/byUserId` 400 "No user with that id exists" means not found.
  The Aurie project refuses email lookups ("non-unique emails"): record "not searchable", not 0.
- **Stripe:** the account API version is pinned to 2020-03-02, so `customers/search` 400s unless the
  call sends `Stripe-Version: 2020-08-27`. `GET /v1/customers?email=` works on any version.
- **Not used:** RevenueCat (Jerome). Sendbird: no credentials; record "not checked".

## Writing to the hold bucket

- `claude@` can list and create there, not read or delete. Use create-only writes
  (`gcloud storage cp --no-clobber`, or `if_generation_match=0`). Take the crc32c on the uploading
  side (`google_crc32c`, or the upload response) and record path, bytes, crc32c and rows in a TSV
  manifest; `account.py verify` checks each path's size against the listing.
- **List each path on its own.** `gcloud storage ls` with several URLs fails as a whole when any one
  of them matches nothing.
- Object names carry the uid prefix and a UTC timestamp, never the full uid. No README objects: the
  bucket is data only, and each object is a row in the incident's inventory.
- The macOS host bash is 3.2, and long pipelines with arrays and nested `$(…)` get refused: ship steps
  as Python.

## Token step (owner: service-db)

UserService skips the Firebase revocation check, and token expiry, for **any account with a role**
(staff, coaches, business, machine roles; ENGBACK-2320). Disable and revoke don't stop an ID token
or session cookie of such an account that is held outside the app. Only removing what UserService validates against does:

1. The userDB `token` rows are exported in the snapshot phase and listed by `verify`.
2. In one transaction: export any row the snapshot doesn't hold (a token minted since), then delete
   the account's rows with `RETURNING id`.
3. Delete the UserService Redis keys (default db 0). Every key sits under the package and its minor
   version, `@aura/user-service:<major.minor>/`, which changes with each minor release, so match every
   live `<major.minor>`. Keys are sha256 hashes (since UserService v2.91.17), never a raw token or cookie:
   - `@aura/user-service:<major.minor>/userService/verifyToken/sha256:<hex>/withProfile/true` and
     `…/withProfile/false`, for **every id the delete returned** (the `token` table's id is the same
     `sha256:<hex>`), not only the exported ones: a token minted after the snapshot has a cache entry
     too. The cache is keyed by token hash, not by uid.
   - `@aura/user-service:<major.minor>/userService/sessionCookie/sha256:<hex>`: the hash of a session
     cookie that is in no table, so SCAN `@aura/user-service:*/userService/sessionCookie/*` and delete
     the keys whose cached value's `id` is the uid.
4. Read back 0 rows and 0 keys.

`verifyToken` reads the cache before the table, so a cached entry keeps validating until it is deleted
or its TTL ends: 24 h for a role account (`TOKEN_CACHE_EXPIRE_TIME` default), 2 h for a session
(`SESSION_CACHE_EXPIRE_TIME` default). The step runs for every account: for one without a role it
only shortens a window that would close within an hour anyway.

## Containment timeline

Kept beside the plan, mode 600, one row per action, times in UTC. It is an incident document, so its
first line is the label and it goes through `writeup.py check` (SKILL.md, Step 6):

**Draft — Privileged & Confidential** — <UTC date> — reflects information currently known.

| Time | Action | Who / go | Evidence |
|---|---|---|---|
| hh:mm:ssZ | Pre-containment Auth snapshot | Firebase owner, on Jerome's go | auth/…-pre/ (bytes, crc32c) |
| hh:mm:ssZ | userDB rows exported | service-db owner, on Jerome's go | incident-accessed/… (bytes, crc32c) |
| hh:mm:ssZ | **Auth disabled** (no delete), refresh tokens revoked | Firebase owner, on Jerome's go | read-back: disabled=true, tokensValidAfter |
| hh:mm:ssZ | Post-containment Auth snapshot | Firebase owner | auth/…-post-containment/ |
| hh:mm:ssZ | **userDB token rows deleted**, token and session cache keys deleted | service-db owner, on Jerome's go | read-back: 0 rows, 0 keys |

A console action leaves no server timestamp (Identity Toolkit data-write audit logging is off): record
the bounds that the snapshots give ("before 05:26:32Z").
