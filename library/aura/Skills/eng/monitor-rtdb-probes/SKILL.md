---
name: monitor-rtdb-probes
description: >
  Keep the Notion RTDB Probe Log (AI Knowledge, Compliance, Security) current with every read of the
  root or a whole top-level node of the prod Firebase Realtime Database (routine app reads marked
  routine) and every denied operation at any depth: guessed names such as /clients or /sms, root reads,
  REST downloads of real nodes, denied reads and writes below a node. Runs eng:profile-firebase-rtdb for
  30 minutes, plans row writes with a script, applies them via the Notion MCP, logs probes to the
  Security Incident Log by type, comments signed-out users/{id} write counts on ENGBACK-1536. IPs and
  user agents go to the Incident Log only. Trigger on
  "update the RTDB probe log", "log access attempts on firebase", "daily RTDB probe check",
  "who is scanning the database", "run the probe log", "record the firebase scanner probes",
  "any new probes on the database", or the daily-rtdb-probe-profile scheduled task. Do NOT use for a
  one-off profile, a rules-change check or watched-node counts (use eng:profile-firebase-rtdb).
metadata:
  version: "1.25.1"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Firebase       # RTDB rules section: change log, denied paths, the Probe Log pointer
    - Tools/Notion         # MCP query and page-update formats for the Probe Log writes
    - Tools/Linear         # comment on ENGBACK-1536 through the Linear MCP
    - Compliance/Privacy   # IPs and UAs only in the Security Incident Log; never uids, keys or values
---

# monitor-rtdb-probes

You keep the **RTDB Probe Log** current: one row per path, operation and verdict for every read of `/`
or a whole top-level node of the prod Realtime Database (the apps' routine reads included, Kind
`Routine app read`), every denied operation at any depth, and allowed REST reads below a node from
outside the apps. Paths below a node are templated (`/users/$id/email`), so no key is ever written.
What is in and out, and each Kind: `references/probe-log-schema.md`. The log is how Aura tracks who is
probing the database while its rules are hardened (ENGBACK-1501). The profiler finds the attempts, a
script decides the writes, and you apply them and report. The same run logs each probing address to
the **Security Incident Log**, the table `eng:flag-suspicious-traffic` writes for the load balancer, so every
access attempt on Aura is in one place.

Slow down on two things: an address or user agent goes to the Security Incident Log and nowhere else,
and a failed profile is reported as a failure, never as a quiet day.

The Probe Log is a Notion database, not a `Learnings/` or `Signals/` path, so it is written directly
with the Notion MCP rather than through `record-knowledge`. The Security Incident Log is written over the Notion
REST API (`NOTION_TOKEN`) by `scripts/incident-log.js`, which checks the live schema before every read
or write.

| | |
|---|---|
| Probe Log | https://app.notion.com/p/61cadff749084dd7838222de6188bf29 |
| Data source | `collection://96b6746d-d7a1-402f-9b9b-0c625d6c8621` |
| Prod project | `firebase-auratech16` |
| Prod RTDB instance | `auratech16` — `https://auratech16.firebaseio.com` (the `firebase-auratech16` URL 404s for live-rules reads such as Admin SDK `getRules()`) |
| Signed-out writes card | ENGBACK-1536 |
| Security Incident Log | https://app.notion.com/p/40175e23af208180aeced339a099f573 (data source `40175e23-af20-8197-8d7e-000b96ac48bf`) |

Columns and matching rules: [`references/probe-log-schema.md`](references/probe-log-schema.md) (Probe
Log) and [`references/incident-log-schema.md`](references/incident-log-schema.md) (Security Incident Log).

## Safety (non-negotiable)

- **Read-only on prod.** The profiler records operation metadata. Never read RTDB data to check
  something — not the root, not a node a scanner hit, not with `limitToFirst=1`.
- **Counts in Linear, Slack, the Probe Log and the reply.** They carry path, op, client label,
  counts, IP *counts* and times.
- **Full IPs and user agents in the Security Incident Log only** — they are the source's, and that
  log exists to hold them. Never a key, a uid, an email, a token or a data value, anywhere.
- **Raw logs are deleted by `run-profile.sh`** on exit, even on Ctrl-C. `$RUN` holds the probe
  detail, networks, rows and plans, which carry IPs: they are mode 600, and `rm -rf "$RUN"` ends
  every run — success, failure or refusal.

## Workflow

### 1. Profile

Load `eng:profile-firebase-rtdb` with the Skill tool and run its step-2 command with
`--minutes 30` (the default here; the caller may set another) and `--probe-json`, in the background,
since it blocks for the whole window:

```bash
RUN=$(mktemp -d "${TMPDIR:-/tmp}/rtdb-probes.XXXXXX")
bash ${CLAUDE_PLUGIN_ROOT}/skills/profile-firebase-rtdb/scripts/run-profile.sh \
  --project firebase-auratech16 --minutes 30 --probe-json "$RUN/probes.jsonl" \
  --probe-detail "$RUN/detail.jsonl"
```

Keep its full text output: steps 6 and 7 read it. Each run deletes only its own CLI stream file, so
an ad-hoc `eng:profile-firebase-rtdb` run on the same machine can overlap this one.

- **Exit 3 / `PROFILE FAILED`:** nothing was captured. Stop, report the failure and the CLI's last
  lines, and write nothing. Do not plan against an empty probe file.
- **`probes.jsonl` missing after a zero exit:** the run did not reach the summary. Same as a failure.

**Every stop ends with `rm -rf "$RUN"`**: a failed profile, a planner or schema refusal (exit 2 in
step 3 or 5), a failed write, or the end of the report. `$RUN` holds IPs and user agents; a run that
stops early must not leave them on disk.

### 2. Read the Probe Log

Query every row with `notion-query-data-sources` (SQL mode, read-only):

```sql
SELECT url, "Path", "Verdict", "Kind", "Op", "Client",
       "date:First seen:start", "date:Last seen:start",
       "Attempts seen", "Days seen", "Max IPs in a window"
FROM "collection://96b6746d-d7a1-402f-9b9b-0c625d6c8621"
```

Save the response as-is (`{"results": [...], "has_more": false}`) to `$RUN/rows.json` with the Write
tool. If `has_more` is true, page through until it is false and save all rows; the planner refuses a
partial set.

### 3. Plan

```bash
node ${CLAUDE_PLUGIN_ROOT}/skills/monitor-rtdb-probes/scripts/plan-probe-log.js \
  "$RUN/probes.jsonl" "$RUN/rows.json"
```

It prints `{creates, updates, new_paths, summary}`. `creates[].properties` and
`updates[].properties` are already in the Notion MCP property format. It exits 2, writing nothing, when:

- a probe line is tagged `rule unknown` — the profile ran without the AuraHealth/Firebase `rules.json`,
  so a guessed name can't be told from a real node. Fix the checkout and re-run from step 1;
- the rows file is partial or not JSON, or the probe file is missing.

Read `summary` before applying: `already_applied` > 0 means this run was planned and written before
(re-running the plan is a no-op for those rows); `duplicate_rows` names keys with more than one row,
which need a human merge — report them, don't pick one.

**Interactive run:** show the plan (one line per create and update) and wait for a yes. **Scheduled
run:** apply without asking. That is this skill's own override of plan-then-confirm, because nobody is
there to answer; it is safe because every write is confined to the Probe Log, matched on a
deterministic key, and idempotent.

### 4. Apply

- **Updates:** `notion-update-page` with `command: "update_properties"`, `page_id` from the plan and
  its `properties` unchanged.
- **Creates:** `notion-create-pages` with parent `{type: "data_source_id", data_source_id:
  "96b6746d-d7a1-402f-9b9b-0c625d6c8621"}` and each create's `properties`. Leave Notes and Card empty;
  a human fills them.
- A `Kind` value the select doesn't have yet (`Denied read of a real node`): if the create is
  rejected, add the option with `notion-update-data-source` and retry that create.

Apply every write before reporting. If one fails, report which keys were written and which were not;
a re-run skips the written ones (`already_applied`).

### 5. Security Incident Log

One row per **group**: probe type and outcome, not IP: the same type with the same outcome is the same
group (Jerome, 2026-10-23). The `Group key` (`Firebase RTDB|root-read|denied`,
`…|guessed-name|denied`, `…|node-read|denied`, and `…|data-accessed` / `…|obtained` / `…|no-data` for allowed reads)
is shared with `eng:flag-suspicious-traffic`. A new IP or sweep updates its group's open row with a line per sweep
(an IP's attempts, split on a 30-minute gap). An allowed read joins a row only from the same network.
Severity is Low for denied, Medium for a flood, Low for an allowed read that returned 0 bytes and High
for any other allowed read, and it only goes up. The rules — groups, the 2-day close, severity, console and
internal exclusion — are in
[`references/incident-log-schema.md`](references/incident-log-schema.md); the planner implements them.

First `eng:monitor-firebase`'s network check, the same one its pass runs: it names the console account
behind each allowed read and writes `verdicts.json`, so a known teammate's read is never an incident.
It also keeps the Known Team Networks DB current by that skill's rules.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-firebase/scripts/check_networks.py \
  --detail "$RUN/detail.jsonl" --verdicts "$RUN/verdicts.json" > "$RUN/networks-findings.jsonl"
echo "network check exit $?"
python3 -c 'import json,sys; [print(r["level"].upper(), r["text"]) for r in map(json.loads, filter(str.strip, open(sys.argv[1]))) if r["level"] != "note"]' "$RUN/networks-findings.jsonl"
S=${CLAUDE_PLUGIN_ROOT}/skills/monitor-rtdb-probes/scripts
node $S/lookup-networks.js "$RUN/detail.jsonl" "$RUN/networks.json" \
  --verdicts "$RUN/verdicts.json"                                      # owner + ASN per IP (whois)
node $S/incident-log.js rows "$RUN/incident-rows.json"                 # checks the live schema first
node $S/plan-incident-log.js "$RUN/detail.jsonl" "$RUN/incident-rows.json" "$RUN/networks.json" \
  --verdicts "$RUN/verdicts.json" > "$RUN/incident-plan.json"
node $S/incident-log.js apply "$RUN/incident-plan.json"                # dry run: one line per write
node $S/incident-log.js apply "$RUN/incident-plan.json" --apply
```

- **The network check** writes one JSON finding per allowed read to `networks-findings.jsonl` (`level`
  `note`, `alert` or `fail`, never an address); the second command prints its `ALERT` and `FAIL` lines,
  which step 7 reports. It needs `NOTION_TOKEN` and `GCP_KEY` (the audit log); `IPGEOLOCATION_API_KEY` is
  optional. A non-zero exit or a `fail` finding is a failed step: report it and carry on, since an empty
  `verdicts.json` only means more is logged, never less.
- `incident-log.js` exits 2, writing nothing, when the live schema lacks a column or select option
  the planner writes, or `NOTION_TOKEN` is unset. Report it, don't write around it, and `rm -rf "$RUN"`.
- **`console`**: the Firebase console's data viewer (an allowed root `listener-listen` plus a 0-byte
  `rest-read` from a desktop browser, or the listen alone when `--verdicts` names the address
  `console`). From a `console` address, desktop-browser listens and data-less reads on any node count
  too, so the viewer opened on a mistyped node is not a guessed-name incident; a read that returned
  data is still logged. Never logged; mention it in the report.
- **`attributed`**: reads the network check attributed to an allow-listed account on a Confirmed or
  Likely Known Team Networks row. Never logged; count them in the report.
- **Staff addresses**: a read from a Confirmed Known Team Networks row that is still logged (allowed and
  returned data or alerted, or denied in the same pass) shows `see Known Team Networks` in place of its IP, in `IP addresses`
  and `What they did`; its network is still named. Counted in `summary.known_network_reads`. Staff reads
  go on their own `…|staff|<outcome>` row, with no Category and the matched network in Notes.
- **`alerts`**: one entry per row that reached **Medium or High** this run: group key, severity,
  attempts, IP count, `ips` (the readers' addresses, for the row writer only), times, row. Report and
  post the IP count, never an address: raw IPs stay in the Incident Log. These are the only part of the run that
  goes to Slack (Jerome's trial); Info and Low stay in the Incident Log. A High alert means rules allowed a
  read of non-public data by someone who is neither the console nor us: report it first, in bold.
- **`reopened`**: rows a person set to **Resolved** whose group had a new sweep this run
  (`{page_id, group_key}`, counted in `summary.resolved_reopened`). A Resolved row is never written
  again: the new sweep opens a new row. Say so in the report: "N Resolved row(s) got a new row".
- **`allowed_flags`**: allowed reads by our own Claude sessions (`internal: true`: allowed and the
  AuraClaude user agent; counted in `summary.internal_flags`). Never logged, always reported: a Claude
  session reading the root breaks the no-root-query rule.
- `summary.already_applied` counts sweeps already in the log: re-running a plan is a no-op.
- `summary.extended` counts logged sweeps that grew since (more attempts in the same sweep): their line is rewritten and only the new attempts are added.

Same approval rule as step 3: show the dry run and wait for a yes interactively; a scheduled run
applies without asking, because every write is confined to Firebase RTDB rows and keyed on IP + sweep
start.

### 6. Signed-out writes to `users/{id}`

If the profile's "Signed-out writes to users/{id}" section is not `none`, add one comment to
ENGBACK-1536 through the Linear MCP with the window and, per line, the op, client, write count, id
count, IP count, time span and path shapes. Counts only, as printed.

### 7. Report

Reply in 3–7 lines:

1. Window, and total denies (`Permission denied: X of Y`).
2. Root and top-level attempts: per path, op, verdict and count.
3. **NEW** paths (`new_paths`), flagged, with their kind.
4. Rows written: creates and updates, plus `already_applied` or `duplicate_rows` when non-zero.
5. Security Incident Log: sweeps, groups, rows created and updated, console and attributed reads; any
   `alerts` and `allowed_flags` first and in bold. Counts only here — the addresses are in the log.
   The network check's `alert` and `fail` lines go with them, in bold, as printed.
6. Cloud Monitoring denies for the window, and the ENGBACK-1536 comment if one was posted.

Say **OK** when `new_paths`, `alerts`, `allowed_flags` and the network check's alerts and fails are empty. Then `rm -rf "$RUN"`.

## Routine

The local scheduled task `daily-rtdb-probe-profile` runs this skill once a day, unattended
(step 3's scheduled path). A failure from step 1, 3 or 5 is its whole output for that day — never a
silent skip.

## Scripts

| Script | What it does |
|---|---|
| `scripts/plan-probe-log.js` | Pure planner: probe JSON lines + Probe Log rows → creates and updates. `--self-test` runs its checks |
| `scripts/plan-incident-log.js` | Pure planner: probe detail (and `eng:monitor-firebase`'s REST-burst sources, `--rest-bursts`) + Security Incident Log rows + networks → creates, updates, console reads, allowed flags. Tested by `tests/test_rtdb_incident_log.py` and `tests/test_rtdb_rest_burst_log.py` |
| `scripts/lookup-networks.js` | Owner and ASN per probing IP, via Team Cymru whois |
| `scripts/incident-log.js` | Security Incident Log REST access (`NOTION_TOKEN`): schema check, Firebase RTDB rows, apply a plan |

## Related

- `eng:profile-firebase-rtdb` — the profiler and its summaries; one-off and rules-change checks.
- `eng:flag-suspicious-traffic` — the same Security Incident Log, from the load-balancer logs.
- `Tools/Firebase` → "Realtime Database security rules" — change log and the denied-paths section.
