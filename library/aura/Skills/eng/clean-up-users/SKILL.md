---
name: clean-up-users
description: >
  Run the monthly all-users cleanup with the Aura CLI (`aura cleanupUsers`, then
  `cleanupDeletedUsers`): guide the emailDB uid export, run the Mixpanel people export,
  dry-run first, report counts only (including the count of profiles holding googleTokens),
  apply only on Jerome's go, then remove the private export files. Trigger on "run the
  monthly user cleanup", "clean up users", "run cleanupUsers", "monthly all-users cleanup",
  "dry run the user cleanup", "how many lite users this month". Do NOT use for one user's
  data (`eng:respond-to-erasure`), orphaned Firebase Auth accounts (`eng:cleanup-orphaned-auth`),
  or the other monthly tasks (`eng:setup-monthly-tasks`).
metadata:
  version: "1.6.2"
knowledge:
  reads:
    - Guide/Index
    - Tools/GCP                      # GCP_KEY service account for the BigQuery export
    - Tools/Mixpanel                 # the people view the export reads
---

# Clean Up Users

The monthly cleanup of every emailDB user that isn't deleted. The CLI (`Tools/CLI`) does the
work; this skill prepares its inputs, runs it, reports counts, and removes the files after.

```bash
SCRIPT="python3 ${CLAUDE_PLUGIN_ROOT}/skills/clean-up-users/scripts/clean_up_users.py"
```

Name the CLI checkout with `AURA_CLI_DIR` (or `--cli-dir`); there's no default, so a stray
checkout is never used by accident. It must be on a master that has the cleanupUsers export
paths. Use a clean worktree of the CLI at `origin/master` when the main checkout is on another
branch.

## Rules

- **Private files.** `users.json`, `mixpanel.jsonl`, the CLI's uid lists and the run logs
  live in the exports folder (`<cli>/data/cleanup-users/`, gitignored, or `CLEANUP_EXPORTS`).
  Tell the user before step 1: they hold user ids plus Mixpanel location and age, stay on this
  machine, are mode 600, and are deleted at the end. Never paste them anywhere.
- **Counts only.** Never print a uid, a log line or a file's content. The CLI logs uids, so `run`
  sends all CLI output to a mode-600 log and prints counts.
- **Dry run first, every time.** `run --apply` is a prod write (`DO_UPDATE=true`). It needs
  Jerome's go for that run, typed by him; a go for one run doesn't cover the next. Pass his exact
  text with `--go`.
- **RTDB:** the CLI reads each listed profile at its exact `/users/{uid}` path. Never read or
  list `/users`, never a shallow read, and don't add any read of your own.
- **Scale.** Prod has about 7.4M live users (2026-11-05). A full dry run is about 7.4M exact
  `/users/{uid}` reads in batches of 100, plus sub-reads for each existing profile: hours of
  prod RTDB read load. So a **sample dry run comes first**, always (Step 3).
- **Held accounts (the keep list).** Before any run, `exclude-held` removes the uids in the keep list
  (`~/.claude/held-accounts/held-uids.json`, or `HELD_UIDS_JSON`; mode 600, written by the hold
  owner) from `users.json`, logging `skip <prefix>…: keep list` per uid and `kept: N`. `run` refuses a
  `users.json`, or a follow-up `deleted.json`, that still names one. A missing or empty list refuses
  every command: never create or relax it to get a run through.

## Step 1 — prepare

```bash
$SCRIPT prepare
```

It creates the exports folder (700) and refuses a path inside the CLI repo that isn't
gitignored. If it reports `overdue`, files from an earlier run are older than 14 days: do Step 6
first.

## Step 2 — the two inputs

**emailDB uids.** Run this read-only query on prod emailDB and save the result as JSON at
`<exports>/users.json` (mode 600). Either Jerome runs it in DBeaver ("Export to JSON"), or the
service-database owner runs it on Jerome's exact go naming the source and the destination path,
for example `go export emailDB "user" ids where deletedAt is null to <exports>/users.json, mode 600,
counts only`:

```sql
select "id" from "user" where "deletedAt" is null
order by "updatedAt" desc NULLS LAST, "createdAt" desc NULLS LAST
```

**Mixpanel people** (the skill runs it, as the `GCP_KEY` service account, from `aura-main`):

```bash
$SCRIPT export-mixpanel
```

Then validate both. It sets them to 600 and prints counts only:

```bash
$SCRIPT check-inputs
```

Exit 1 names what's missing or malformed. Don't run the CLI until it exits 0.

Then remove the held accounts. It prints counts only (held, before, removed, after, kept) and logs them:

```bash
$SCRIPT exclude-held
```

## Step 3 — dry run: a sample first, then the full run

Two separate gos from Jerome, because both put real read load on prod RTDB:
`go cleanupUsers dry run sample`, then `go cleanupUsers dry run full`.

**Sample** (for example 50,000 rows): the load limits, and when to stop, are in
the CLI README's **RTDB load** paragraph (under "Monthly user cleanup"). Ask the Firebase owner to
watch RTDB load for the run's start and end.

```bash
$SCRIPT run cleanupUsers --sample 50000
```

It runs the CLI on the first rows of `users.json` (written to `users-sample-<rows>.json`, mode
600; outputs in `output-sample/`). The export is ordered by `updatedAt` desc, so the head is
the most active profiles: the worst case per user. Report:

- from `timing`: duration, users per second, and the projected hours of a full run;
- from the Firebase owner: reads per second, and peak against baseline load.

**Full run,** only on its own go, sized from the sample:

```bash
$SCRIPT run cleanupUsers
```

It runs for a long time (one exact read per uid). Run it in the background. The output is the
CLI's summary: `counts` (users, updated, deleted, lite, premiumTrial, premiumNoSubscription,
invalidLocale, googleTokens). `googleTokens` counts the profiles holding Google tokens; their uids
go to the mode-600 `googleTokens.json`, never to the output.

Every run also appends a start and an end line to the durable run log
`~/.claude/clean-up-users/runs.jsonl` (or `CLEAN_UP_USERS_RUN_LOG`; mode 600): UTC times, host,
command, verdict and counts, never a uid. The CLI's Firebase Admin traffic can't carry the
AuraClaude user agent, so the RTDB profiler uses this log to label a run's reads. Keep it when
the exports folder is removed.

| verdict | meaning |
|---|---|
| `ok` | exit 0 with a summary: report it |
| `unverified` | exit 0 without a summary: the run can't be trusted; read the log's tail for the error and report that, not counts |
| `stopped` | cleanupUsers exited 143 (SIGTERM) or 130 (Ctrl-C) with a summary holding `resumeRow`: the window was ended on purpose and its uid lists are written. Report the counts and `resumeRow`, the row the next window starts at; it is not a complete run, so nothing follows it |
| `failed` | any other non-zero exit, or a stop without a summary: say so and stop |

## Step 4 — report and ask for the go

Report the counts as a short table, plus the token report. Ask for Jerome's go for the apply,
through Lead when you aren't in his session. Name the run (the dry run's log time) and the
counts it will act on.

## Step 5 — apply (only on the go)

Check the dry run's `deleted.json` first; `run` refuses the follow-up anyway when it names a held
uid:

```bash
$SCRIPT check-held
```

```bash
$SCRIPT run cleanupUsers --apply --go "<Jerome's exact text>"
$SCRIPT run cleanupDeletedUsers --apply --go "<Jerome's exact text>"
```

`cleanupUsers --apply` adds `googleRevokes` (revoked, failed, none). `cleanupDeletedUsers`
reads the deleted list cleanupUsers wrote, so it runs second, and only when the first run is
`ok`. It prints a summary (`counts`: users, existing, invalid, lite, errors), writes the uid lists
to mode-600 files (`deletedExisting.json`, `deletedInvalid.json`, `deletedLite.json`) and exits 1
on any error, so its verdict is read like cleanupUsers'. A CLI checkout that prints no summary
exits 0 even on a caught error: that run is `unverified`, its counts come from the log, and any
non-zero `errors` is a failure.

## Step 6 — remove the files

```bash
$SCRIPT remove
```

It checks the path, prints a manifest of every file (name, rows, sha256, size, modified time;
no content), and prints the `rm`. It never runs the `rm`. Log the deletion with that manifest
and the time it was run (on the card), so the removal of each export is on record. Give that command to the user to run
(auto mode may block a bulk delete).

- **When:** keep `users.json` and `mixpanel.jsonl` (mode 600) until the whole cleanup has
  finished, `cleanupDeletedUsers` included; then remove the folder. With no apply, remove it
  within 14 days of the export.
- **Log first:** record the manifest (one row per file: name, rows, sha256, UTC time) before
  the `rm` runs.
- Then run `prepare` again to confirm the folder is gone or empty. This is Jerome's rule for the
  inputs, as in the CLI README: kept at mode 600 until the cleanup finishes, then deleted with a
  log entry per file.

## Sources

- CLI README, "Monthly user cleanup (`cleanupUsers`)": inputs, outputs, env overrides.
- `src/tasks/user/cleanupUsers.js` and `cleanupDeletedUsers.js` in `Tools/CLI`: the summary
  object, and the log lines `run` counts.
