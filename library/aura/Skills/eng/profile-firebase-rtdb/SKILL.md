---
name: profile-firebase-rtdb
description: >
  Profile the Aura Firebase Realtime Database (prod firebase-auratech16 or dev aura-dev) for N minutes
  with the Firebase CLI profiler and print a privacy-safe summary: denied operations by path and client,
  every attempt to read the root or a whole top-level node (scanner probes such as /clients or /sms),
  allowed/denied counts on watched nodes after a rules change, signed-out writes to users/{id}, and the
  Cloud Monitoring deny counts for the same window. Deletes the raw logs, which hold IPs and keys.
  Trigger on "profile the firebase database", "run the RTDB profiler", "which paths are denied",
  "did the rules change break anything", "monitor the rules change", "who is reading /clients",
  "is someone scanning the database", "hourly firebase profile", or after deploying an RTDB rules change.
  Do NOT use for Cloud Logging errors (eng:investigate-gcp-logs) or to read RTDB data: this skill
  never reads data, only operation metadata.
metadata:
  version: "1.13.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Firebase       # RTDB rules section: change log, "Which paths are denied", safety rules
    - Tools/GCP            # Cloud Monitoring metrics and the claude@ service account
    - Compliance/Privacy   # profiler logs hold IPs and keys: counts only, never identifiers
---

# profile-firebase-rtdb

You are monitoring Aura's Firebase Realtime Database while its security rules are being hardened
(ENGBACK-1501). The profiler is the only source that names **which paths** are read, written or denied:
Cloud Monitoring's `rules/evaluation_count` has no path, and RTDB writes no request logs. Your job is to
run it, turn its output into a short, privacy-safe answer, and leave nothing sensitive behind.

Slow down on two things: never let an IP, key, uid or value reach the output, and never leave the raw
log on disk.

## Safety (non-negotiable)

- **Read-only.** The profiler records operation metadata; it does not read data. Never query RTDB
  data on prod to "check" something — not the root, not `/users`, not with `limitToFirst=1`.
- **Counts only.** Report op, path pattern (keys collapsed to `$id`), client (platform/os/SDK), counts,
  number of distinct IPs and time span. Never print an IP, a key, a uid, an email or a value. The one
  file with IPs and user agents is `--probe-detail`, which goes to the Security Incident Log only.
- **Delete the raw logs.** Both the `-o` file and the CLI's leftover `$TMPDIR/tmp-*` stream copy hold
  IPs and keys. `scripts/run-profile.sh` deletes both on exit, even on Ctrl-C — and only its own:
  overlapping runs on one machine are safe (see the gotchas).

## Prerequisites

- `firebase` CLI logged in with access to the project (`firebase login`), `node`, `jq`, `gcloud`.
- For the Cloud Monitoring comparison: `GCP_KEY` (the service account). `scripts/monitoring-denies.sh`
  gets its token through the bundled `scripts/gcloud_sa.py`, and uses your own gcloud login only when
  `GCP_KEY` is unset. See `Tools/GCP`.
- Optional: a checkout of AuraHealth/Firebase at `~/Development/Aura/Data/Firebase` (or
  `FIREBASE_RULES_REPO`). The script reads `origin/master:rules.json` to tell real nodes from guessed
  names. Without it, nodes are tagged `rule unknown`.

## Workflow

### 1. Pick the window and the watched nodes

- **Duration:** 10–15 minutes for a routine check, 30–60 minutes right after a rules deploy or to catch
  a bursty pattern. Busy prod traffic is about 1 GB per hour of raw log, so it lives on local disk
  only while the run lasts.
- **Watched nodes:** the top-level nodes whose rules changed recently, for `--watch`. Take them from
  the `Tools/Firebase` rules change log or the user.

### 2. Run it

Run in the background (it blocks for the whole duration) and wait for it to finish:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/skills/profile-firebase-rtdb/scripts/run-profile.sh \
  --project firebase-auratech16 --minutes 15 --watch users,emailUser,userReflections
```

Add `--probe-json <file>` to also append JSON lines for the RTDB Probe Log, one per group: every
read of the root or a whole top-level node (routine app reads included, as `Routine app read`), every
denied operation at any depth, and allowed REST reads below a node from outside the apps (`rest-reads.js`);
a path below a node is templated (`$id`): `{path, verdict, kind, op, client, tag, n, ips, t0, t1}`. `ips` is a count, `t0`/`t1` are ISO
times, and nothing in a line identifies a user or an address. `eng:monitor-rtdb-probes` reads this file
to keep the Notion RTDB Probe Log current. Put it outside the run's temp dir, which is deleted on exit.
The file exists after any run that got as far as the summary, even with no groups.

`--probe-detail <file>` appends one line per probe attempt **with its IP and full user agent**:
`{t, node, op, verdict, kind, tag, client, ua, ip, bytes}` — every denied root, whole-node or guessed-name
read except the Aura apps' own SDK (`platform` `native`: signed-out users), every allowed read of
the root or a guessed name, and every allowed `rest-read` or `listener-listen` of a whole real node that
is not public by rule, from outside the apps (the console's data viewer on one node; an SDK listen stays
out of the probe counts). It is written mode 600 and is input for the Security Incident Log only
(`eng:monitor-rtdb-probes`); the caller deletes it once that log is written. Nothing else this skill
prints changes: stdout stays counts-only.

`--rest-detail <file>` writes allowed REST reads per IP + user agent + UTC day, **with the IP**:
`{ip, ua, client, day, t0, t1, n, shallow, listing, root, bytes, sized, shapes}` (mode 600). It is the only
record of a REST burst's caller, because the raw log is deleted when the run ends; `eng:monitor-firebase`
keeps a burst's sources from it for the Security Incident Log.

`--seconds N` replaces `--minutes` for short test runs.

To look at a run that is still going, without stopping it:
`bash ${CLAUDE_PLUGIN_ROOT}/skills/profile-firebase-rtdb/scripts/peek.sh --pid <pid> --watch …`, with the
`Stream owner pid` the run printed (without `--pid` it reads the newest stream file, which may be
another run's).

If the run prints `PROFILE FAILED`, nothing was captured (not logged in, no access to the project, wrong
project id, or its stream temp file deleted mid-run). Report it as a failure — never as a clean result.

### 3. Read the summary and report

The script prints seven sections. Report them in this order, in a few lines:

1. **Total denies** (`Permission denied: X of Y rule-checked operations`) and the denied groups.
2. **Root / whole top-level node read attempts.** Always report every line here, even if all were
   denied — Jerome asked for it on every run. Lines tagged `ROOT` or `NO RULE (guessed name)` are
   scanner probes (`/clients`, `/sms`, `/AllSms` seen so far); REST reads of whole nodes are worth
   naming too. Give count, client, IP count and time span.
3. **Watched nodes.** Any deny on a watched node points to app breakage from a rules change: name the
   node, op and client, and say the rollback is the pre-deploy rules via `setRules`, only with the
   rules owner's OK. Each node's line ends `(from N ids, M IPs)`: denies from one or two of each are
   one client retrying, not a breakage.
4. **Signed-out writes to `users/{id}`** (ENGBACK-1536): counts only. If present, add the counts as a
   comment on ENGBACK-1536.
5. **Allowed REST reads by path shape:** the total (and how many ops were marked shallow), then the top 10
   groups by op, path shape (`/users/$id/profile`), client, distinct ids, IP count and time span. Exact-path
   reads below the top level appear nowhere else, and RTDB keeps no request log, so this is the only
   record that can attribute a REST burst (the GCP read-volume alerts) after the fact. Report the total
   and any group from an unfamiliar client. **The marked-shallow count is not the shallow count:** the
   raw ops don't carry `?shallow=true` (at 13:00Z on 2026-11-09 a 103-get shallow burst was marked 0).
   Read Cloud Monitoring `network/api_hits_count`, `operation_type=rest_shallow_get`, for the same
   minutes (`eng:monitor-firebase` does this every pass).
6. **Allowed realtime reads by path shape:** the same for realtime gets, queries and listens: the total
   (and how many came from the Admin SDK), then the top 10 groups by op, path shape, auth (`admin` for Admin
   SDK user agents, the same pattern section 2 uses to set them aside; `client` otherwise), client, bytes sent, distinct ids, IP count and time
   span. It is the only record that can attribute a realtime read surge (the "RTDB realtime read surge"
   alert) after the run, because the raw stream is deleted (ENG-1765).
7. **Cloud Monitoring** DENY/ALLOW per 10 minutes for the same window. It can be higher than the
   profiler's count — see the gotchas.

Say "OK" when there are no denies on watched nodes and no new root-level attempts.

### 4. Hourly mode (optional)

For monitoring after a batch of rule changes, schedule the run with `CronCreate` at an off-minute
(`13 * * * *`), with a prompt that runs step 2 and reports step 3. Jobs are session-only and expire
after 7 days; delete the job once the change has a clean day.

## Gotchas

See [`references/gotchas.md`](references/gotchas.md) — read it before interpreting a surprising number.

## Scripts

All in `${CLAUDE_PLUGIN_ROOT}/skills/profile-firebase-rtdb/scripts/`.

| Script | What it does |
|---|---|
| `scripts/run-profile.sh` | Runs the profiler, prints all summaries, deletes the raw logs on exit |
| `scripts/peek.sh` | Summarizes a run still in progress from the CLI's temp file, then deletes the copy |
| `scripts/summarize-denied.js` | Denied ops by op, path pattern and client (from AuraHealth/FirebaseExporter `src/profiler.js`) |
| `scripts/root-attempts.js` | Reads of `/` or a whole top-level node (routine ones too) and denied operations at any depth; optional JSON lines (`--probe-json`), per-attempt detail with IPs (`--probe-detail`) |
| `scripts/watched-nodes.js` | Allowed/denied counts on the `--watch` nodes, and how many distinct ids and IPs the denies came from |
| `scripts/signedout-users.js` | Denied writes to `users/{id}`: counts of ids, IPs and time span |
| `scripts/realtime-reads.js` | Allowed realtime reads (get, query, listen) at any depth by op, path shape, auth (admin or client) and client: counts of ops, bytes, ids and IPs |
| `scripts/rest-reads.js` | Allowed REST reads at any depth by op, path shape and client, ops marked shallow counted apart: counts of ops, ids and IPs; per-source detail with IPs (`--source-detail`, from `--rest-detail`) |
| `scripts/monitoring-denies.sh` | Cloud Monitoring `rules/evaluation_count` by result and method |
| `scripts/delete-raw.js` | Deletes this run's leftover `$TMPDIR/tmp-<cli-pid>-*` stream file, never another run's |

## Related

- `eng:monitor-rtdb-probes` — the daily run that turns `--probe-json` output into Notion RTDB Probe Log rows
  and `--probe-detail` output into Security Incident Log rows.
- `eng:investigate-gcp-logs` — enumeration detection from Cloud Monitoring (`get_shallow` load) and
  Cloud Logging; this skill names the paths.
- AuraHealth/Firebase `CLAUDE.md` — how to change a rule (step 7 points here for monitoring).
- `Tools/Firebase` → "Realtime Database security rules" — change log and the denied-paths section.
