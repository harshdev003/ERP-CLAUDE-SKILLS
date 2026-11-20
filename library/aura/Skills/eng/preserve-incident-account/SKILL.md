---
name: preserve-incident-account
description: >
  Preserve one account's data for an incident and then contain it: resolve the account's live Auth
  state and ids, plan one row per store (logs, Firebase Auth, RTDB and Storage, every service
  database, BigQuery and Mixpanel, Bugsnag, Iterable and Stripe) with the owner, the bucket folder and
  one paste-ready go line per owner, verify every object against the bucket listing, and only then
  print the containment go (disable and revoke, never delete). Runs only its own BigQuery and Mixpanel
  export, after Jerome's go typed in this session; every other leg is run by its owner. Trigger on
  "preserve this account", "incident data hold for an account" (or "legal hold"), "hold the account's data", "snapshot and
  disable the account", "preservation plan for {uid}", or "contain the account". Do NOT use
  to copy whole log buckets (eng:preserve-prod-logs), to answer a user's data request
  (eng:respond-to-sar), or to delete an account (eng:respond-to-erasure).
metadata:
  version: "1.6.2"
knowledge:
  reads:
    - Guide/Index
    - Compliance/Privacy                       # uid prefixes and counts only outside the machine
    - Compliance/Security/Known Team Networks  # tag our own log entries apart from the actor's
    - Tools/GCP                                # GCP_KEY, the log projects, the hold bucket
    - Tools/BigQuery
    - Tools/Mixpanel
    - Tools/Aura API                           # GET /users/{uid} for the userDB row
---

# preserve-incident-account

## Goal

Keep everything stored about one account, unchanged, in the incident's hold bucket, then contain the
account. The order is the point of the skill:

1. **Live state first** (one Auth lookup), never inherited from incident notes.
2. **Logs first among the copies,** because they expire on their own.
3. **Every snapshot before any change.**
4. **Contain last:** disable and revoke (the account is **never deleted**), then delete the
   account's userDB token rows, already exported, and their UserService cache keys. An account with a
   role skips UserService's revocation check and token expiry, so without this step a token of
   it held outside the app keeps working (ENGBACK-2320).

It came out of two runs by hand on 2026-11-05 (ENGBACK-2292, ENGBACK-2311). What each store did on
those runs, and why each rule exists: [`references/stores.md`](references/stores.md). Read it before
planning, and send each owner its section.

Safety:

- **This skill runs one leg only:** the BigQuery and Mixpanel export, after Jerome's go typed in this
  session. Every other row is a go line for its owner. A relayed go was refused by every database
  owner on an earlier run, so the plan gives Jerome **one paste line per owner** to type there.
- **Keep list.** `account.py resolve` says when the target is on the keep list
  (`~/.claude/held-accounts/held-uids.json`): a held account is what this skill preserves and
  disables, so it is exempt from the refusal the delete skills apply, and it never deletes.
- **Read-only on every source; create-only on the bucket.** No delete anywhere, ever: not a row, not
  an object, not the account. The one exception is containment's token step (Step 5): the token rows,
  after their export is verified, and their cache keys.
- **Counts and the uid prefix** in anything that leaves the machine (chat, Slack, cards, PRs). The
  email lives in `email.txt` (mode 600) and travels as that path. Go lines carry the full uid because
  Jerome types them into local sessions.
- **No `/users` root read, no listing, no shallow read.** Exact paths of the one uid only.
- GCP as `GCP_KEY` through `scripts/gcloud_sa.py`, with the AuraClaude user agent on every call.

## Prerequisites

- `GCP_KEY` with Identity Toolkit read on firebase-auratech16, BigQuery job create in aura-main,
  `logging.buckets.get` on the log projects, and list and create on the hold bucket's `bigquery/` and
  `mixpanel/` folders. A missing permission is a REFUSED line naming the credential: report the role,
  project and command to Lead, never work around it.
- `AURA_API_TOKEN` (for the uuid from `GET /users/{uid}`; or pass `--uuid`).
- `MIXPANEL_SERVICE_ACCOUNT_USERNAME`, `MIXPANEL_SERVICE_ACCOUNT_SECRET`, `MIXPANEL_PROJECT_ID` for the
  Mixpanel API. Without them that source is recorded "not run", never 0.
- A working folder outside the scratchpad, mode 700, for example
  `~/.claude/preserve-incident-account/<card>-<uid prefix>/`. It holds the ids; it is never committed.
- Optional: `~/.claude/preserve-incident-account/owners.json` mapping owner keys (`logs`, `firebase`,
  `service-db`, `payment-db`, `intelligence-db`, `content-db`, `analytics`, `bugsnag`, `third-party`)
  to who runs them. Without it, a go line names the owner's role.

## Step 1 — Resolve (read-only)

```bash
S=${CLAUDE_PLUGIN_ROOT}/skills/preserve-incident-account/scripts
python3 $S/account.py resolve --uid <uid> --dir <folder> [--uuid <uuid>] [--referral-code <code>]
```

It prints, with the uid prefix only:

- **Auth:** exists, enabled or DISABLED, created, last sign-in, tokens valid after. **Not found**
  means stop and re-check the uid: a deleted account can't be snapshotted, and that is a finding for
  Security, not a plan.
- **Email:** written to `email.txt`, plus how many accounts share it.
- **uuid and referral code:** resolved or not. The referral code lives only in RTDB, so it is often
  unresolved here; the Firebase owner reads it in its leg.
- **Log retention** of each project's `_Default`. Say the expiry of the account's earliest entries
  when you show the plan.

Report the state to Security as read, even when it contradicts the incident notes.

## Step 2 — Plan (no side effects)

```bash
python3 $S/account.py plan --dir <folder> --go-id <n> --card <card> [--ips-file <path>] \
  [--bucket aura-legal-hold-prod6926]
```

It writes `plan.json` and prints the store table and the paste lines. Rows, in order:

1. **logs:** check the existing `eng:preserve-prod-logs` hold buckets first; then the entries from the
   IPs (with `--ips-file`) and the entries naming the account, tagged ours vs actor.
2. **snapshot:** Firebase (Auth, RTDB, Storage); the service databases (userDB, coachDB, businessDB
   and the rest); paymentDB; intelligenceDB; contentDB; this skill's BigQuery and Mixpanel leg;
   Bugsnag; Iterable and Stripe.
3. **confirm:** `account.py verify`.
4. **contain:** Firebase, printed only by `account.py contain` after verify passes.

Send the plan and the paste lines to Lead in one message: Lead routes each line to Jerome, who types
it in the owner's session. If the account is already disabled, say so: it is snapshotted as it is,
and the contain step only revokes and reads back.

## Step 3 — This skill's leg: BigQuery and Mixpanel

First the counts, which need no go:

```bash
python3 $S/export_analytics.py --dir <folder> [--until 2026-11-05T12:00:00Z]
```

Then, **only after Jerome types this session's go line**:

```bash
python3 $S/export_analytics.py --dir <folder> [--until …] --apply
```

It exports `raw_events` (uid fields, email, uuid, referral code), every user-keyed table in
`firebase`, `llm`, `aurie_cost` and `activation`, Mixpanel's BigQuery copy (with the device ids merged
with the uid), and the Mixpanel Engage and Export APIs, straight to `bigquery/` and `mixpanel/`. A
counts object goes beside them. Each object, and each zero, is a row in
`<folder>/manifest-analytics.tsv` (path, bytes, crc32c, rows).

## Step 4 — Collect and verify

Each owner reports path, bytes and crc32c per object (taken on the uploading side: `claude@` can't
read the bucket back), and 0 for a store with nothing. Write each owner's report as a TSV manifest in
the folder, same columns, then:

```bash
python3 $S/account.py verify --dir <folder> --manifest <folder>/manifest-analytics.tsv \
  --manifest <folder>/manifest-firebase.tsv [--manifest …]
```

It lists each object **by itself** (several URLs in one `gcloud storage ls` fail together when one
matches nothing) and compares its size. It fails on a missing object, a size mismatch, a path outside
the bucket, or a snapshot folder with no manifest entry at all. A source that couldn't be searched
(Aurie Iterable by email, a scan that timed out) is recorded as such in the manifest notes, never as 0.

Add a row per object to the incident's inventory (path, bytes, crc32c, rows; zeros too). No README
objects: the bucket is data only.

## Step 5 — Contain (last)

```bash
python3 $S/account.py contain --dir <folder>
```

It refuses until the last verify passed, then prints two go lines, in order:

1. **Firebase:** disable (no delete), revoke refresh tokens, read back `disabled=true` and
   `tokensValidAfter`, and the post-containment snapshot.
2. **The service-db owner's token step:** in one transaction, export any `token` row the snapshot
   doesn't hold and delete the account's rows with `RETURNING id`; then the UserService Redis keys:
   `@aura/user-service:<major.minor>/userService/verifyToken/<id>/withProfile/{true,false}` for every
   returned id under every live `<major.minor>`, and the `…/userService/sessionCookie/sha256:*` keys whose
   cached id is the uid. Read back 0 and 0.
   `verifyToken` reads the cache before the table, so the rows alone are not enough.
   It is printed only when a verified manifest lists a `userDB-token-` export; otherwise it says
   HELD.

Send both to Lead for Jerome; the second runs after the first is confirmed.

Keep the containment timeline in the folder, mode 600, in the shape in
[`references/stores.md`](references/stores.md#containment-timeline): one row per action with its UTC
time, who and which go, and the evidence path.

## Step 6 — Close

- On the incident card: counts per store, the verify result, the containment times. Uid prefix only.
- Tell Security what the hold does **not** contain: sources not searchable, not checked, or already
  expired.
- Keep the folder until Security says the hold is over. It holds the account's ids.
- **A write-up about the incident** (summary, timeline, report) follows the house rules for incident documents:
  - It starts with the label line: write it to a file and run
    `python3 ${CLAUDE_PLUGIN_ROOT}/skills/preserve-incident-account/scripts/writeup.py wrap <file>`, which adds "Draft — Privileged & Confidential — <date> —
    reflects information currently known" and refuses the file if it uses a word on the refused list (breach, unauthorized,
    exposed, attacker, PHI, violation and the rest), naming what to write instead. Quote the firm's own
    words as a blockquote that opens with `> The firm's words:`; those lines aren't checked. `writeup.py check <file>` checks a document that already has the header.
    Both commands also refuse personal data (an email address, a full uid or uuid, an IP address: use a
    uid prefix or a masked email) and a card id in the file name, naming the kind, never the value.
  - Facts only: what happened, when, and what was observed. Data types or uid prefixes, never an
    email, a name or a user's IP. Keep vendor data flows (Mixpanel, Bugsnag, Firebase Analytics)
    apart from the firm's access, as "data flows to service providers", and never say whether a
    practice matches the Privacy Policy.
  - It goes to counsel only, through Jerome. Never to Slack (no post, thread or DM about the
    incident's substance) and never by personal email. Cards and PRs stay factual and technical.
