# Gotchas — RTDB profiler

Learned running the profiler on prod during the October 2026 rules hardening (ENGBACK-1501).

## The CLI

- **`-o` is written only at the end.** Until then the CLI streams to `$TMPDIR/tmp-<pid>-*`, and at
  the end it re-opens that file **by name** to write `-o`. The temp file is never deleted by the CLI,
  and it holds IPs and keys. `delete-raw.js` removes only the `tmp-<pid>-*` files of this run's CLI
  pid (`run-profile.sh` runs the CLI in the background to capture it; `firebase` is
  `#!/usr/bin/env node`, so the pid is node's), modified after the run's marker file. Do not delete it
  with `rm $TMPDIR/$f` (a safety check blocks removals through unset variables, and for good reason).
- **Overlapping runs on one machine used to break each other** (fixed in ENG-1560). The old cleanup
  deleted *every* `tmp-*` newer than its marker, so the first run to finish deleted the other's live
  stream file. The survivor kept writing to the unlinked file, then could not re-open it: exit 2,
  "An unexpected error has occurred", no `-o`, 0 operations → `PROFILE FAILED`, and its own cleanup
  printed "Deleted 0 CLI temp file(s)". Seen 2026-10-13 when an ad-hoc 15-minute run ended inside the
  nightly 30-minute probe run. `run-profile.sh` now says so when its stream file vanished mid-run. A
  session still on the old script can still delete *your* stream file — update the plugin.
- **"Unexpected error from response stream: This operation was aborted"** at the end of a `-d` run is
  harmless: it is how the CLI stops the stream when the timer ends.
- **Size:** about 1 GB of raw log per hour on prod at daytime traffic. Keep runs to what you need.
- **Scratch files in `/private/tmp` get wiped.** The scripts live in this skill for that reason; never
  rely on copies in a session scratchpad.

- **The prod RTDB instance is `auratech16`**, URL `https://auratech16.firebaseio.com`, even though the
  project is `firebase-auratech16`. `https://firebase-auratech16.firebaseio.com` returns 404 (e.g. for
  Admin SDK `getRules()`), so anything comparing live rules must use the `auratech16` URL.

## Reading the output

- **A root operation has no `path` field.** The profiler logs a read of `/` without `path` (not `[]`), so any filter on `Array.isArray(d.path)` silently drops every root read. `root-attempts.js` treats a missing path as the root.
- **`querySet` of `{"orderBy":"$priority"}` or `$key` is a plain read, not a query.** Only an
  `orderByChild`/range is a real query.
- **Admin SDK traffic shows as allowed.** Backend functions and services (FirebaseDenormalizer,
  FirebaseAnalytics, ContentService, the CLI) bypass rules. A locked (`.read/.write: false`) node that
  still shows "allowed" operations is the backend writing it — expected, and a good sign it still works.
- **Our own Claude sessions send `AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)`.** That's a label, not
  trust. `root-attempts.js` calls a request `internal (<plugin>:<skill>)` (the person is dropped) only when it was also *allowed*,
  which on these paths takes admin credentials. It excludes Admin SDK user agents only on allowed requests:
  a denied request is never filtered out on its user agent.
- **An empty profile does not prove a node is unused.** A 15-minute window misses rare features and
  old app builds. Combine with a code search across all client repos (including old git history).
- **Profiler vs Cloud Monitoring counts.** Monitoring's `rules/evaluation_count` also counts rule
  re-evaluations that are not new operations (open listeners re-checked when a client's auth token
  refreshes or the user signs out), so it can show denies the profiler doesn't. Compare the trend with
  the same window on previous days rather than expecting the two numbers to match. Denies come in
  bursts; a short profile can fall between them.
- **Signed-out clients.** A burst of denied listens across many user nodes plus denied `users/{id}`
  updates from one app build is usually an app that lost its auth session but keeps running — not a
  rules regression, unless the denied nodes are ones whose rules just changed.

## What we have seen (prod, October 2026)

- Scanner REST reads of `/`, `/clients`, `/sms`, `/AllSms`, `/user_sms`, `/message_list` from desktop
  browsers: always denied (no rule for those names).
- Signed-out `users/{id}` updates from one Windows Chrome client rotating IPs (ENGBACK-1536).
- `/pushNotifications` listens from a 2016-era iOS build.
