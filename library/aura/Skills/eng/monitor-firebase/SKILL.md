---
name: monitor-firebase
description: >
  Run a read-only health and security pass over prod Firebase (firebase-auratech16): profile the
  Realtime Database for N minutes (denies on watched or changed nodes, /users read surges, signed-out
  writes, root and guessed-name probes, REST reads by path shape), check RTDB database load, check Cloud Functions and Cloud
  Run errors against a baseline (including authUserClaim sign-in timeouts), and flag Firebase console
  or admin reads from networks missing from the Known Team Networks list. Logs probes to the Security
  Incident Log, posts to #alerts-ai only when someone needs to act, and stays silent on a clean pass.
  Trigger on "monitor firebase", "is firebase healthy", "check firebase", "watch the rules deploy",
  "watch the functions release", "monitor prod firebase for N hours", or after deploying RTDB
  rules or Firebase Functions. Do NOT use for
  the daily Probe Log update (eng:monitor-rtdb-probes), a single profile with full detail
  (eng:profile-firebase-rtdb), or to read RTDB data: this skill never reads data.
metadata:
  version: "1.23.1"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Firebase       # RTDB rules change log (what to --watch), safety rules
    - Tools/GCP            # Cloud Monitoring and Logging as the claude@ service account
    - Tools/Notion         # Known Team Networks and Security Incident Log (REST, NOTION_TOKEN)
    - Tools/Slack          # #alerts-ai channel and the footer spec
    - Compliance/Privacy   # counts only; IPs and user agents only in the Security Incident Log
    - Compliance/Security/Known Team Networks  # network rows and the console-account allow-list
    - Tools/ipgeolocation  # city, region and ISP of a console read (IPGEOLOCATION_API_KEY)
  writes:
    - Compliance/Security/Known Team Networks  # Last seen, blank Country/Region/City/ISP, an added console account, or a new Likely row
---

# monitor-firebase

You watch prod Firebase while it changes: rules deploys, Functions releases, or a quiet day someone
wants checked. The pass answers one question, *does anyone need to act?*, and stays out of the way
when the answer is no.

Slow down on three things: an IP or user agent goes to the Security Incident Log and nowhere else, a
failed check is reported as a failure and never as a quiet window, and #alerts-ai only gets a post
that someone has to act on.

## Safety (non-negotiable)

- **Read-only on prod.** The profiler records operation metadata. The other checks read Cloud
  Monitoring, Cloud Logging and Notion. The one write is to the Known Team Networks DB, for allow-listed
  console reads: network, country, region, city, ISP and the account, never an address. Only the IP
  itself goes to ipgeolocation.io. Never read RTDB data to check something: not the root, not
  `/users`, no shallow reads, not even `limitToFirst=1`.
- **Counts only** in chat, Slack and Linear: path patterns, ops, clients, counts, times, ASN and
  country. Never an IP, uid, key, email or value. One exception: the network check names a console
  read's account by its local part (`console read by rose@`), from the RTDB admin audit log. `detail.jsonl` and `rest-bursts.jsonl` hold IPs and user
  agents; they feed the Security Incident Log and are deleted with the run dir.
- **Our own calls carry the AuraClaude user agent** (`scripts/gcloud_sa.py`), so they're never
  mistaken for outside traffic.
- **No schedule unless asked.** Run passes when the user asks. Set up a loop or cron only when they ask for
  one, and stop it when they say so.

## Prerequisites

- `firebase` CLI logged in with access to `firebase-auratech16`, plus `node`, `jq`, `gcloud`, `whois`.
- `GCP_KEY` (service account with Logs Viewer and Monitoring Viewer). `NOTION_TOKEN` for Known Team
  Networks and the Security Incident Log. `SLACK_BOT_TOKEN` for #alerts-ai.
- A checkout of AuraHealth/Firebase at `~/Development/Aura/Data/Firebase` (or `FIREBASE_RULES_REPO`),
  so the profiler can tell real nodes from guessed names.
- **Functions baseline, once per machine:** a normal day's per-hour error counts. Rebuild it after a
  release that changes log messages on purpose.

  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-firebase/scripts/check_functions.py baseline --date 2026-10-20
  ```

  State lives in `~/.cache/aura/monitor-firebase/` (or `MONITOR_FIREBASE_STATE`).

## Workflow

### 1. Pick the window and the watched nodes

- **Window:** 10 minutes for a routine pass; 30 minutes right after a deploy.
- **Watched nodes:** the top-level nodes whose rules changed recently (the `Tools/Firebase` rules
  change log, or the user). `users` is always watched, for read surges.

### 2. Run the pass

Run it in the background, since it blocks for the whole window:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/skills/monitor-firebase/scripts/run_pass.sh --minutes 10 --watch users,emailUser
```

It prints one line per finding, `FAIL` first, then `ALERT`, then `NOTE`. Next comes `PASS OK` or the
count of alerts and failed checks, then `SCRIPTS <dir>` and last `RUN_DIR <dir>`. The pass runs from a
copy of its scripts in the run dir, so a plugin update or prune mid-pass can't remove them. Use
`SCRIPTS=<dir>` for the `decide.py` and `slack_post.py` steps below. A check that doesn't finish is a
`FAIL run … exited N: its check did not run` line, never a silent `PASS OK`. What makes a finding an
alert:

| Check | Alert when | Note otherwise |
|---|---|---|
| `profile` | denies on a watched node from 3 or more distinct ids or IPs: `users` ≥ 100 / 10 min, any other ≥ max(20 / 10 min, 0.5% of its allowed ops); `/users` > 20,000 allowed ops / 10 min; more than 300 allowed REST reads / 10 min (`REST burst`, with the top path shapes and clients) | total denies, probes (not allowed reads of nodes public by rule), signed-out writes, smaller watched-node denies, one client retrying (under 3 ids and IPs), allowed REST and realtime reads by path shape (realtime with admin vs client) |
| `db-load` | `io/database_load` ≥ 70% | peak load |
| `shallow-gets` | never (GCP's shallow-read alert fires on the same metric) | shallow REST gets from `network/api_hits_count` `rest_shallow_get`, every pass |
| `functions` | `authUserClaim` timeouts ≥ 3; a new or 3× ERROR template with ≥ 5 entries; Cloud Run 5xx ≥ 200 | other new or high templates |
| `networks` | a console read by an account not on the allow-list (the DB's `Console accounts` options) or by no attributable account; any read from a `Not a teammate` network; a non-browser read from a network not marked Confirmed or Likely | an allow-listed console account (`known: rose@, <city>`, or its row updated or created), a teammate read (0 bytes, no console account, a Confirmed network), a probable console reconnect (a 0-byte root listen and nothing else from a desktop browser, no console account, on a Confirmed or Likely row that lists an allow-listed console account), the console data viewer (a whole-node listener within an hour after a `GetFirebaseTokens` call from the same address), our own sessions |

**`fail` is not a clean result.** Examples: `PROFILE FAILED` (the CLI isn't logged in, or another run
deleted its stream), no Functions baseline, or a Notion or Monitoring read error. Say so in the report.

### 3. Log probes and REST bursts to the Security Incident Log

If `RUN_DIR/detail.jsonl` or `RUN_DIR/rest-bursts.jsonl` is not empty, log them with `eng:monitor-rtdb-probes`'s scripts (its step 5).
The writes are confined to `Firebase RTDB` rows, keyed and idempotent, so apply without asking.
`verdicts.json` is the network check's verdict: a read it attributed to an allow-listed account on a
Confirmed or Likely row is never an incident, and a lone root listen with a console account in the
audit log is the console. A whole-node read the check raised an ALERT on (`alert`) or could not
classify (`fail`) is logged: an unconfirmed read stays in the log with all its information (Jerome,
2026-10-30), but an address on a Confirmed row (`confirmed`) is written as `see Known Team Networks`
(Jerome, 2026-11-09). A row a person
set to Resolved is never written again: a new sweep in its group opens a new row, listed in the plan's
`reopened`. A REST burst's sources (each IP + user agent with 100+
reads per 10 min) join one row per path family and outcome while it has a hit within 2 days, unless the network check found them on a
Confirmed network (`rest_teammate`); the rules are in `monitor-rtdb-probes`'
[`references/incident-log-schema.md`](../monitor-rtdb-probes/references/incident-log-schema.md):

```bash
RUN=<RUN_DIR>; S=${CLAUDE_PLUGIN_ROOT}/skills/monitor-rtdb-probes/scripts
node $S/lookup-networks.js "$RUN/detail.jsonl" "$RUN/networks.json" --verdicts "$RUN/verdicts.json" \
  --rest-bursts "$RUN/rest-bursts.jsonl"
node $S/incident-log.js rows "$RUN/incident-rows.json"
python3 $SCRIPTS/reclassify.py --rows "$RUN/incident-rows.json" --out "$RUN/reclassify.json" \
  >> "$RUN/findings.jsonl"
node $S/plan-incident-log.js "$RUN/detail.jsonl" "$RUN/incident-rows.json" "$RUN/networks.json" \
  --verdicts "$RUN/verdicts.json" --rest-bursts "$RUN/rest-bursts.jsonl" \
  --reclassify "$RUN/reclassify.json" > "$RUN/incident-plan.json"
node $S/incident-log.js apply "$RUN/incident-plan.json" --apply
```

An exit 2 (schema check, missing `NOTION_TOKEN`) means nothing was written: report it as a failed
step. Report the plan's `alerts` (Medium / High groups) with the pass's alerts, and say how many Resolved rows got a new row (`summary.resolved_reopened`).
The network check retries a Notion 429 (the `NOTION_TOKEN` integration is shared), honouring
`Retry-After`. If Known Team Networks still can't be read, the reads it would have judged are logged
with their addresses, each line tagged `(unclassified: <reason>)`: report `summary.unclassified_reads`
and its reason with the pass. `reclassify.py` judges those lines on the next pass where Known Team
Networks answers, and the plan rewrites each tag to `(classified <date>: <verdict>)`: a teammate
network, one marked Not a teammate (an ALERT finding, appended to `findings.jsonl` so step 4 posts it), or
an unknown network. Report `summary.reclassified` and that finding with the pass. Lines logged before the tag existed get the same treatment with
`--untagged-since <UTC>` (allowed whole-node lines from then on), run once by hand, for example
`--untagged-since 2026-11-03T02:30Z` for that night's Notion rate-limit.

### 4. Alert, only when someone must act

```bash
python3 $SCRIPTS/decide.py --findings "$RUN/findings.jsonl"
```

It prints the alert findings not already posted in the last 6 hours, or nothing. **If it prints
nothing, post nothing.** Otherwise post **one** message to #alerts-ai (`C0C9P5K069F`), with one line
per alert, what the reader should do, and the footer. Then record the post:

```bash
python3 $SCRIPTS/slack_post.py post --channel C0C9P5K069F \
  --text-file "$RUN/alert.txt" --footer eng:monitor-firebase
python3 $SCRIPTS/decide.py --findings "$RUN/findings.jsonl" --record
```

Message shape (mrkdwn, counts only):

```
*Firebase prod: <n> issue(s) need a look* (<window>Z)
• <finding text> → <action: e.g. "roll back the last rules deploy?", "who is on AS…?", "check authUserClaim">
```

A failed check is not an alert. Report it in the conversation; post only if the same check has
failed for an hour of passes, because then nobody is watching.

### 5. Report and clean up

Reply in 3–6 lines: the window, `PASS OK` or each alert, any failed check, the Incident Log result
(sweeps logged, Medium / High groups), and whether #alerts-ai was posted. Then always:

```bash
rm -rf "$RUN"
```

## Watching for hours

When the user asks to monitor for a period, for example around a deploy, run passes back to back:
`/loop` with this skill, or one pass per 15 minutes with `CronCreate`. Each pass is independent, and
`decide.py` keeps alerts from repeating. Stop the moment the user says so. Report only alerts and
failures between passes, and a one-line summary at the end.

## Gotchas

- **Denied probes are routine.** Root reads about once an hour, SMS-forwarder name lists, `/clients`,
  `/config`. They go to the Incident Log at Low severity and never to Slack.
- **Signed-out `users/{id}` writes** come from old app builds and the web sign-out loop
  (ENGBACK-1536); they stay notes.
- **The console looks like an allowed root or whole-node read** from a desktop browser. The audit log
  names the account. An allow-listed account is a note whatever the row's Status: `known: …` when its
  network row lists it, otherwise the account is added to that row, or a `Likely` row is created. Rows
  match on network (ASN) + country + City + Region, from ipgeolocation.io (bundled
  `scripts/ip_location.py`, canonical at `eng:lookup-ip-location`): the same network in a new place
  gets a new row, never an edit of the old one. A blank field matches and is filled; a value a person
  set is never overwritten (geo-IP often places an ISP at its head office: AS9541 Faisalabad read as
  Karachi). Country comes from the same lookup, not whois. A row whose Country is either one matches,
  and a new row notes a differing whois country. Without `IPGEOLOCATION_API_KEY` rows match on network + country. `Not a teammate` always alerts.
  The teammate checks (a Confirmed row; a row listing a console account) take a **Confirmed** row on
  the ASN alone, in any country: whois can place an address outside the country its network serves
  (a Nayatel PK address read as SC, ENG-1857). Every other row still needs the country.
  A whois failure is retried once, then the network comes from the ipgeolocation ASN (the line says
  so); only both failing is a FAIL.
- **The console's data viewer makes no `ListDatabaseInstances` call.** It fetches a token
  (`mobilesdk-pa.googleapis.com` `SettingsService.GetFirebaseTokens`, in the always-on Activity audit
  log), then opens whole-node listeners, and an open tab reconnects on that token (valid an hour) with
  no new call. A listener within an hour after such a call from the same address is
  a console note with no account named, unless the network is `Not a teammate` (AS153596, ENG-1706).
  The token fetch can also land just **after** the viewer's first read: on 2026-11-06 at 14:30Z the
  guessed names `/genders` and `/guidances` were read 30 s before it (ENGBACK-2377). So a
  console-shaped read (a desktop browser, allowed, 0 or few bytes, listens and reads only), on any
  node, also matches a fetch up to 5 minutes after it. A fetch for a read in the pass's last minutes
  may not be in the audit log yet when the check runs (2026-11-09, 14:45Z and 16:24Z: a staff console
  logged as an incident), so when a browser read under 5 minutes old has no token call and no console
  account, the check waits 150 s and reads both once more before deciding.
  On an Incident Log row, a Known Team Networks teammate makes Actor `Staff`, with
  `likely teammate <name>, not confirmed` in Notes and the name in What they did (Jerome, 2026-11-10);
  an Actor already set is kept.
- **Profiler runs overlap safely,** but busy prod traffic is about 1 GB/hour of raw log on local disk
  while a run lasts. `run-profile.sh` deletes it on exit.
- **GCP's own alerts are separate:** RTDB shallow, REST and realtime read-volume policies post on
  their own. This skill doesn't duplicate them, but its `allowed REST reads` note (or `REST burst`
  alert) is what attributes one: op, path shape, client and IP count for the same window, counts only.
  **The shallow count comes from the metric, not the profile.** The profiler's raw ops don't carry
  `?shallow=true`: at 13:00Z on 2026-11-09 the metric counted 103 shallow gets and the profile 0. So
  `check_shallow_gets.py` reads `rest_shallow_get` for the pass window, and the REST note shows a
  shallow count only when an op was marked.
  RTDB keeps no request log, so read the pass that covered the alert's minutes (ENG-1720).
