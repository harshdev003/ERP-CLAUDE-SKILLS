---
name: preserve-prod-logs
description: >
  Preserve prod logs for a legal hold: copy each project's Cloud Logging buckets (_Default, _Required)
  for a time window into private Coldline GCS buckets (uniform access, no public access, versioning,
  400-day retention left unlocked), optionally with the Firebase Analytics events, the RTDB Monitoring
  metrics and a focused bundle for named accounts (Auth records without password hashes, plus the log
  entries that mention them). Plans first; each create, grant, copy and export runs after Jerome's go,
  one copy per project at a time, then verifies and removes the temporary grants. Never changes log
  retention or locks a bucket. Trigger on "preserve the logs", "legal hold", "copy prod logs to GCS",
  "export the logs for counsel", or "hold the logs for the incident". Do NOT use to search logs
  (eng:investigate-gcp-logs) or for one user's data (eng:export-user-conversations, eng:respond-to-sar).
metadata:
  version: "1.6.1"
knowledge:
  reads:
    - Guide/Index
    - Tools/GCP          # the GCP_KEY service account, projects
    - Compliance/Privacy # counts only in chat and Slack
---

# preserve-prod-logs

## Goal

Copy prod evidence for a time window into private storage that outlives the logs' own retention, so it
can be handed to counsel. The skill **plans**, a person **approves each step**, and the skill then runs
that step and only that step.

Safety, before anything else:

- **Only for a recorded incident, on Jerome's go.** This is never a routine or scheduled export: no
  loop, no cron, no daily metrics copy (Jerome, 2026-11-03). Standing retention decisions belong to
  PROD-8205 (Data Retention), not here.
- **Plan first.** Nothing is created, granted, copied or exported until Jerome's go for that step. Each
  step is run with its own `--apply`.
- **Never change log retention.** This skill copies; it doesn't touch a log bucket's settings (Jerome,
  2026-11-03).
- **Never lock a retention policy.** Locking is irreversible. The buckets' 400-day retention stays
  unlocked unless counsel asks, and then a person locks it by hand.
- **Counts only** in chat, Slack and Linear: bucket names, object counts, bytes, entry counts. Never a log
  line, a uid or an address. The uid list stays in its file and travels as a path.
- **GCP as `GCP_KEY`** through `scripts/gcloud_sa.py`. IAM grants and removals are PERSON steps, run with
  a person's own gcloud and marked `# user-gcloud:`.

Sources, routing, sizes and the lessons from the first hold (ENGBACK-2176):
[`references/sources.md`](references/sources.md). Read it before planning.

## Prerequisites

- **Jerome creates the hold buckets** (create and versioning are PERSON steps), because `GCP_KEY` holds no
  bucket-create role in the hold project (`aura-main` by default; ENGBACK-2333). Only on a run where it is
  granted, plan with `--create-as sa`.
- `GCP_KEY` able, for the options, to read BigQuery, Monitoring and Identity Toolkit. A missing
  permission is a REFUSED line naming the credential. Report the exact role, project and command, and
  never fall back to a personal login.
- A person with IAM rights on the hold buckets for the PERSON steps.
- **The copy runs as Jerome** (`logging.buckets.copyLogEntries` on each source project), because
  `GCP_KEY` doesn't hold it (ENGBACK-2333). The plan makes each copy a PERSON step. Only when the
  service account has been granted the permission for this run, plan with `--copy-as sa`, and remove
  the grant afterwards.
- With `--uids-file`: a file of uids, one per line, written by whoever holds them (for example
  `~/.claude/security/<incident>_uids.txt`). Pass the path; never paste uids into chat.

## Step 1 — Plan

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/preserve-prod-logs/scripts/plan_hold.py \
  --project firebase-auratech16 --start 2026-09-20T00:00Z --end 2026-10-08T16:05Z \
  --card <card> --member user:<redacted-email> \
  [--analytics-dataset analytics_153603506] [--metrics] [--uids-file <path>] \
  --out ~/.claude/legal-hold/<card>/plan.json
```

Keep the plan outside the scratchpad (it is wiped on restart) and mode 600: a focus copy's filter holds
the uids. It prints one numbered line per step. For each source (project × log bucket) the steps are:

1. Create its own bucket (PERSON, unless `--create-as sa`). `logging copy` takes no path prefix, so two
   sources would collide.
2. Turn on versioning (PERSON, unless `--create-as sa`).
3. Grant the named members read access, and the admin (`--admin`, by default the first member)
   bucket-level `storage.admin` (PERSON). The admin is what keeps the bucket's IAM changeable later.
4. Add a temporary objectCreator for the source's Logging service agent, and a temporary write and
   read for the caller, who verifies (PERSON).
5. Copy (PERSON, unless `--copy-as sa`). Each copy waits for the previous copy of the same project,
   because Logging allows one active copy per project.
6. Verify.
7. Remove the temporary grants (PERSON).
8. Remove the hold project's legacy access (PERSON). Removing it before anyone could change the
   bucket's IAM locked everyone out on ENGBACK-2176.
9. Re-read the bucket's IAM, **last** (PERSON). Only the readers and the admin may remain: no legacy
   project binding and no temporary `objectCreator`. On ENGBACK-2176 the legacy bindings came back after
   the first removal, so the removal alone is not the end state.

The options add their own bucket and export: the Analytics events, the Monitoring metrics, and, with
`--uids-file`, a focused copy of each source plus the accounts' Auth records.

Show the plan to Jerome (via Lead), with the window, the sources, the estimated size from
`references/sources.md`, and what each option adds. **Monitoring metrics are kept 6 weeks only**: if the
window is near that edge, say so and propose running the metrics step first.

## Step 2 — Run, one step at a time

```bash
S=${CLAUDE_PLUGIN_ROOT}/skills/preserve-prod-logs/scripts
python3 $S/run_step.py --plan <plan.json> --step 01            # shows exactly what step 01 does
python3 $S/run_step.py --plan <plan.json> --step 01 --apply    # after the go for it
python3 $S/run_step.py --plan <plan.json> --step 03 --done     # after a person ran a PERSON step
python3 $S/run_step.py --plan <plan.json> status               # every step, and the copies' progress
```

- **REFUSED** means nothing ran. It's printed when a step it waits on isn't done, another copy of the
  project is running, or gcloud failed (with the credential named).
- **A PERSON step** prints the exact `gcloud` lines to run. Run them, then record the step with `--done`.
- **A copy** returns at once. A 1.5 TB `_Default` takes about 3 hours. As a PERSON step, the step prints
  the `gcloud logging copy` line and the `gcloud logging operations list` line to watch it. A focused
  copy's filter, which holds uids, is written to a mode-600 file beside the plan, and the line reads it
  from there. Record `--done` only when the operation has finished. With `--copy-as sa`, run `status`
  until the copy is `done`; it polls by listing the project's copy operations, because describing one by
  its short id returned NOT_FOUND.
- **Verify** counts the bucket's objects and bytes, then compares one sample hour (by default the
  window's last full hour): entries in the source against lines copied. A mismatch is `mismatch`. An
  empty hour, or a count at the read cap, is `unverified`: re-run with `--sample-hour YYYY-MM-DDTHH` on
  another hour. An empty bucket is REFUSED. None of them is a pass.
- **The Analytics step** waits for every extract job before it reports done, because the caller's
  write access is removed next.

## Step 3 — Close

- Post the counts on the card and to Security: per bucket, objects, bytes, and the sample hour's two
  counts.
- Confirm every temporary grant is removed (`status` shows every `revoke` done).
- Say plainly what the hold does **not** contain. RTDB reads and writes are never in Cloud Logging, and
  logs older than the source's retention were already gone (see `references/sources.md`).
- **A write-up about the incident** (summary, timeline, report) follows the house rules for incident documents:
  - It starts with the label line: write it to a file and run
    `python3 ${CLAUDE_PLUGIN_ROOT}/skills/preserve-prod-logs/scripts/writeup.py wrap <file>`, which adds "Draft — Privileged & Confidential — <date> —
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
