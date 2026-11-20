---
name: detect-attacks
description: >
  Find hacking attempts in prod load-balancer logs: scanners and probes (Nuclei, sqlmap,
  /.env, /.git, wp-admin), brute force on auth routes, mostly-refused traffic, UA rotation, odd
  non-GET bodies, id scraping. Logs each attack group (type + outcome) to the Security
  Incident Log with attacker IPs, user agents and a severity; Slack gets one message per group at
  or above a threshold (default Medium). Reports Cloud Armor: edge blocks vs backend, denies by
  rule, IP bans, saturated throttles, own-caller false positives, policy coverage. Exports ban
  candidates. Read-only on GCP. Trigger on "detect attacks", "any hacking attempts", "who is
  attacking us", "scan the logs for attackers", "log hacking attempts", "check for scanners", "run
  the attack detector", "what did Cloud Armor block", "export the attacker blocklist", or the
  hourly detect-attacks routine. Do NOT use for error health checks (eng:analyze-gcp-logs), one
  error's root cause (eng:investigate-gcp-logs), or Firebase RTDB probes (eng:monitor-rtdb-probes).
metadata:
  version: "1.6.1"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/GCP            # Cloud Logging access, GCP_KEY auth ladder
    - Tools/Notion         # Security Incident Log writes
    - Tools/Slack          # alert post
    - Compliance/Privacy   # attacker IPs and UAs only; never user ids, emails or tokens
---

# detect-attacks

You find attempts to break into Aura and record **who, when and what**: the attacking IPs, their user
agents, the time window, the routes they hit and what the servers answered. The log is the
[Security Incident Log](https://app.notion.com/p/40175e23af208180aeced339a099f573) (AI Knowledge →
Compliance → Security), shared with `eng:monitor-rtdb-probes`. Alerts go to Slack `C0C72QYDJQK`.

Slow down on two things. A flagged IP is evidence, not a verdict: say "flagged" and show the
reasons, and correct a false positive by tuning, never by deleting rows silently. And the log holds
**attacker** data only — IPs, user agents, paths. Never write a user id, email, token or request
body into Notion or Slack (Compliance/Privacy).

| | |
|---|---|
| Source | `resource.type="http_load_balancer"`, project from `GCP_KEY` (prod `firebase-auratech16`) |
| Rules | [`references/rules.md`](references/rules.md): detection, path families, Group key, severity, Slack groups (the shared spec) |
| Log | Notion database `40175e23-af20-8180-aece-d339a099f573`, one row per open group |
| Alert | Slack channel `C0C72QYDJQK`, Medium and High only by default |
| State | `~/.cache/detect-attacks/`: `groups.json` (group → row, minutes counted), `history.json` (types seen, Slack message per group); mode 600 |

## Workflow

### 1. Detect (read-only)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/detect-attacks/scripts/detect.py \
  --minutes 60 --plan /tmp/detect-attacks-plan.json
```

`--since`/`--until` (ISO UTC) set an exact window; for a routine, use a window slightly longer than
the schedule (65 min for hourly) so nothing falls between runs — `apply.py` counts by minute, so the
overlap is never counted twice. Exit `10` means something was flagged, `0` means clean. A query or
auth failure is a failure: report it as such, never as a quiet hour.

Show the user the report as printed: one `FLAG` block per campaign with its severity floor and Group
key (`[INFO] Web|forced-browsing|refused`), its reasons, path families, top user agents, status
counts, top routes and attack paths. `apply.py` may raise Info to Low for a type not seen in 14 days.
A web page answering a probe with its usual page (a soft 404, rules.md) stays refused; the stderr
`# usual 200 size for …` lines show the size baselines used.
Our own service traffic (the LiveKit voice agent) is exempt and only counted: `exempt as our own
traffic`.

Each `FLAG` block ends with an `edge:` line: how many of its requests Cloud Armor blocked (and by
which rules) and how many reached the backend. After the flags comes the **Cloud Armor** section:
`ALERT` lines for an enforced IP ban or a saturated throttle, enforced and preview denies by rule
and signature, rate-limit counts, `own caller?` lines (a likely false positive only when the
address also succeeded elsewhere), incident block rules joined to the Blocklist, and policy coverage
per backend. Counts and masked paths only; never a matched value, rate-limit key or Range. Fields,
rules and flags: [`references/rules.md`](references/rules.md) → "Cloud Armor". A `read FAILED` line
means that section is missing, not clean.

### 2. Check before writing

Read each flag. Drop it from the plan (edit the JSON) only when it is clearly our own traffic — CI,
an internal tool, a load test the user names — and say which IP you dropped and why. Recurring
own traffic belongs in the allow-list instead (rules.md, "Our own traffic").

### 3. Write the log and alert

Dry run first, then apply:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/detect-attacks/scripts/apply.py /tmp/detect-attacks-plan.json
python3 ${CLAUDE_PLUGIN_ROOT}/skills/detect-attacks/scripts/apply.py /tmp/detect-attacks-plan.json --apply
```

Campaigns with the same Group key update the group's open row: IPs and user agents unioned with
counts and last seen, requests added, Ended moved, severity only raised. A new row only for a new key,
or after 2 days without hits. The dry run prints each group's row action, severity and Slack action
(`skip`, `post`, or `update`, which is a threaded reply under the group's message) with the message it
would send. A posted message is never edited or deleted.

Slack: `--slack-min-severity` / `DETECT_ATTACKS_SLACK_MIN_SEVERITY` (`info`, `low`, `medium`,
`high`; default **medium**, a trial) sets what reaches Slack; everything below stays in the log
only. `--mention-user` / `DETECT_ATTACKS_MENTION` is the Slack user id @mentioned on Medium and
High; unset means no mention. Find an id with Slack's `users.lookupByEmail`
(`curl -H "Authorization: Bearer $SLACK_BOT_TOKEN" "https://slack.com/api/users.lookupByEmail?email=…"`,
needs `users:read.email`).

Exit codes: `2` the Incident Log lacks `Group key` or `Severity` (nothing written; someone with
access adds the column in Notion — the script never changes the schema), `3` a Slack post or threaded
reply failed (retried next run, never posted twice), `4` a Notion write failed (the other groups and the
Slack message still went out; the next run retries).

### 4. Report

A few lines: window, requests and IPs scanned, campaigns flagged with severity and Group key, rows
created vs updated, what Slack got. Add the Cloud Armor headline: any `ALERT`, how much of the
flagged traffic the edge blocked, and any incident-rule or own-caller line with a `!`. Escalate
**High** and any Cloud Armor `ALERT` to the user directly, not just in the log.

`apply.py` also posts each Cloud Armor `ALERT` (an enforced ban or saturated throttle) as one
Medium message per rule and UTC day; it has no Incident Log row.

## Ban candidates (Cloud Armor)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/detect-attacks/scripts/blocklist.py --min-severity Low --format csv
```

Read-only export from the open group rows (Web and Firebase RTDB): IPs with counts and last seen,
/24 CIDRs, user agents with counts. Filters: `--min-severity`, `--group-key` (substring, e.g.
`Web|` or `|refused`), `--min-requests`, `--exclude-ip`. It never creates or changes a Cloud Armor
policy; our own user agents are always left out.

## Running as a routine

Hourly: `detect.py --minutes 65 --plan …` then `apply.py … --apply`. When nothing is flagged,
`apply.py` writes nothing and posts nothing. Keep `~/.cache/detect-attacks/` between runs.

## Setup

- `GCP_KEY` (service-account JSON) with Logs Viewer on the project, or `gcloud auth application-default login`.
- `python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/detect-attacks/scripts/requirements.txt`
- `NOTION_TOKEN` with access to the Security page; `SLACK_BOT_TOKEN` with the bot in `C0C72QYDJQK`
  (`chat:write`). The skill only posts: it never edits (`chat.update`) or deletes (`chat.delete`) a
  message, by counsel's instruction (2026-11-06); a group's later counts are threaded replies.
- The Incident Log needs `Group key` (rich text) and `Severity` (select: Info, Low, Medium, High).
  Both exist on the live database; a copy without them makes `apply.py` exit 2.
