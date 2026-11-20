---
name: investigate-gcp-logs
description: >
  Investigate Google Cloud logs for the Aura prod GKE cluster via a chained workflow: triage
  (what's broken?), focus (tell me more about this one error), and trace (what actually
  happened for one transaction?). Also follows Logs Explorer share links (cloudlogging.app.goo.gl
  or console.cloud.google.com) and runs the decoded query via the API, and covers Firebase Auth /
  Identity Platform sign-in logs. Every digest ends with the next-step command. Trigger on "investigate gcp logs", "investigate cloud logging",
  "look at cloud logging", "what errors are happening", "warnings in the last hour", "errors
  in the last 4 hours", "follow this logs explorer link", "find logs for transaction {id}",
  "what happened in this transaction", or a pasted Logs Explorer URL. To search by Firebase
  user id, use `eng:search-gcp-by-user`. Also "are sign-ins failing", "identity platform logs",
  "authUserClaim logs", "is someone scraping RTDB". Do NOT use for setting up alerts or log-based metrics
  — those go through the GCP Console.

metadata:
  version: "3.6.2"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/GCP     # SA, project, IAM, log schema cheatsheet, Identity Platform auth logs, RTDB scraping detection, paste-export workaround
---

# investigate-gcp-logs

Read-side debugging skill for Aura's prod GKE cluster (`aura-prod-cluster-a` in project `firebase-auratech16`). Chains three modes — **triage → focus → trace** — and follows Logs Explorer share links. Read-only; no writes anywhere.

## Goal

Replace the manual loop of "open Logs Explorer → eyeball errors → copy a transaction ID → paste it back into a new query → repeat for each service" with a chained set of prompts that hand off to each other. Every output ends with the exact command for the next step, so investigation never stalls because the syntax was forgotten.

## Inputs

The user provides one of:

- A **time-window scan** intent: "what errors in the last hour", "warnings in the last 4 hours", optionally narrowed to a service ("…in user-service") or namespace ("…in aura-ai")
- A **specific message to focus on**: "tell me more about 'Invalid user id from decoded token'"
- A **transaction UUID** to trace
- A **Logs Explorer URL** — short (`cloudlogging.app.goo.gl/...`) or full (`console.cloud.google.com/logs/query;query=...`)

To search the logs for a specific Firebase **user id**, use the sibling skill [`eng:search-gcp-by-user`](../search-gcp-by-user/SKILL.md) (`search.py --user <uid>`); its results chain back here via an `investigate logs --transaction <id>` hint.

## Workflow

### 1. Resolve mode and inputs

Before any query, decide which mode the user wants:

| User said… | Mode | CLI flags |
|---|---|---|
| "errors in the last 4h", "what's broken" | **Triage (Mode 1)** | `--severity ERROR --since 4h` |
| "tell me more about 'X'", "drill into 'X'" | **Focus (Mode 2)** | `--message "X" --container <name>` |
| "transaction 619D571B-...", "trace this tx" | **Trace (Mode 3)** | `--transaction <uuid>` |
| "find logs for user <uid>", "what's user X doing" | **→ use `eng:search-gcp-by-user`** | (separate skill) `search.py --user <uid>` |
| Paste of `cloudlogging.app.goo.gl/...` or full Logs Explorer URL | **Link follow** | `--url "<url>"` |

Defaults when the user is vague: `severity=WARNING`, `since=1h`, no container/namespace filter. Confirm with the user before running anything that would scan **>24h** or **all severities** — those are slow and noisy.

### 2. Run the query

Invoke the CLI:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-gcp-logs/scripts/investigate.py <flags>
```

The script handles auth (`GCP_KEY` → `GOOGLE_APPLICATION_CREDENTIALS` → ADC), log normalization (Format A standard nested + Format B `CRITICAL - {...}` stringified-JSON-in-message), and output formatting per mode. It prints the digest to stdout. Pass it through to the user verbatim — every output ends with a "Drill in:" hint that the user can copy-paste back as their next prompt.

### 3. Don't auto-chain

The skill never picks which row to drill into. The engineer reads the triage digest, decides which signature is interesting, and pastes the suggested Mode 2 command back. Same for Mode 2 → Mode 3.

### 4. PII guardrail

The script enforces this, but be aware: **Modes 1 and 2 never echo the raw `jsonPayload.message`** — only specifically extracted fields (canonical message, transaction, userId, component, method). Some Aura services (e.g. `neural-model`) dump full user profiles inline; raw output would be a PII leak. Mode 3 (trace) shows full entries by design, since the user has explicitly drilled into one transaction.

## Setup (one-time, per user)

Two paths — pick one. Full walkthrough in [`references/auth-setup.md`](references/auth-setup.md).

**Recommended (works in Cowork too):** Inline service-account JSON in `~/.claude/settings.json`:

```json
"env": {
  "GCP_KEY": "{\"type\":\"service_account\",\"project_id\":\"firebase-auratech16\",...}"
}
```

The skill derives the project ID from `GCP_KEY["project_id"]`. No separate `GCP_PROJECT_ID` needed. Pass `--project <id>` for one-off cross-project queries.

**Alternative (most secure on a personal laptop):** `gcloud auth application-default login`. The skill picks it up via `google.auth.default()`.

**Python deps:** `python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/investigate-gcp-logs/scripts/requirements.txt` once. Just `google-cloud-logging`. (Use `python3 -m pip` not bare `pip` — they often point to different Pythons on macOS.)

**Cowork caveat ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)):** `mcp__workspace__bash` strips host env. Inside Cowork, paste-export `GCP_KEY` once at session start:

```bash
# inside mcp__workspace__bash, at session start
export GCP_KEY='<paste from host ~/.claude/settings.json — single quoted to preserve JSON>'
```

## Output

Each mode produces a fixed-shape text digest. See the canonical examples in [Linear ENGBACK-1012](https://linear.app/aura-health/issue/ENGBACK-1012/add-investigate-gcp-logs-skill-eng-plugin) — they're stable contracts the script renders.

Every output ends with `→ Drill in: investigate logs <next-step-flags>` (or `→ Done — last mode in the chain`).

## Edge cases

- **No logs match the query** → print "0 entries matched" with the resolved filter shown, and suggest broadening (`--since 24h`, `--severity WARNING`, drop `--container`).
- **Auth fails** → print the specific failure (no `GCP_KEY`, key parse error, expired ADC, wrong project) with the one-line fix, not an opaque traceback.
- **Mode 3 transaction not found** → check both case-orientations (samples are mixed UUIDs); if still empty, suggest the user broaden the time window.
- **Format B re-parse fails** → fall back to using `jsonPayload.message` as-is for that entry, but **truncate to 200 chars in Modes 1/2** to preserve the PII guardrail.
- **Re-running the same query** → safe (read-only); no idempotency concern.

## Auth events — Identity Platform user activity logs

Sign-ins, sign-ups and account lookups are **not** in the GKE cluster, so none of the three modes see them. They live under `resource.type="identitytoolkit_project"` in both `firebase-auratech16` (prod) and `aura-dev` (dev), from 2026-10-06. **`Tools/GCP` → "Identity Platform user activity logs" is canonical** for the method table, which payload path carries the uid, the error shape, what is not logged, volume and the `authUserClaim` blocking-function logs — read it rather than relying on this summary.

- **Methods** (`jsonPayload.methodName`, prefix `google.cloud.identitytoolkit.v1.`): `AuthenticationService.SignInWithIdp` (Google / Apple / Facebook), `.SignInWithPassword`, `.SignInWithCustomToken`, `.SignUp`, `AccountManagementService.GetAccountInfo`, `ProjectConfigService.GetProjectConfig`.
- **Sign-in errors** are `severity>=WARNING` with `jsonPayload.status.code` + `.message` (an enum like `EMAIL_EXISTS`). Success has an empty `status`. Tally them without printing a payload:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-gcp-logs/scripts/gcloud_sa.py logging read 'resource.type="identitytoolkit_project" AND severity>=WARNING' \
  --project firebase-auratech16 --freshness=1h \
  --format='value(jsonPayload.methodName,jsonPayload.status.code,jsonPayload.status.message)' \
  | awk -F'\t' '{n=split(\$1,m,"."); split(\$3,e,/[ :]/); print m[n], \$2, e[1]}' | sort | uniq -c | sort -rn
```

- **One user's sign-ins** → `eng:search-gcp-by-user --user <uid> --auth`, which prints allowlisted fields only.
- **No event is not "never signed in".** Token refreshes are not logged and an app reusing its session emits nothing; to produce an event, have the user sign out and back in.

**PII — non-negotiable.** These payloads carry end-user emails, names, photo URLs, federated ids and caller IPs. Print field names, counts, method / error-code tallies or a uid prefix only — never a value. Never use `--format=json` output in a reply, and never paste an email into Linear or Slack. The `awk` above keeps only the method, the numeric code and the leading error enum for that reason.

## Realtime Database scraping / enumeration — metrics, not logs

The Realtime Database writes no per-request entries to Cloud Logging, so none of the three modes can see a scrape or a key enumeration. Detection uses its built-in Cloud Monitoring metrics. **`Tools/GCP` → "RTDB scraping and enumeration detection" is canonical** for the metric table, the label values, the query recipe and how to read a spike — read it rather than relying on this summary.

- **Primary signal:** `firebasedatabase.googleapis.com/io/database_load` grouped by `metric.label.type`. Enumeration is a `get_shallow` spike. Don't look for it in request counts: one shallow read of `/users` returns every key in a single request, so `rest_shallow_get` on `network/api_hits_count` barely moves. Live alert policies already watch for it (`RTDB … Check on Firebase Policy`, posting to #alerts-security); the current list is on `Tools/GCP`. Read egress (`network/sent_*bytes_count`) and `rules/evaluation_count` `result=DENY` alongside it, and compare with the same hours on previous days. Flat egress means keys were listed, not profiles downloaded.
- **Which paths:** the RTDB profiler (`firebase database:profile`). It attaches to prod, so a human runs it.

**Safety — non-negotiable.** Never query the RTDB root or `/users` on prod to check, not even with `limitToFirst=1` or a shallow read, and never fetch other users' data. Metrics and the profiler only.

## Ad-hoc follow-up queries (beyond the three modes)

When the chained modes can't answer a question (e.g. "which app version / endpoint produced these errors?"), drop into `scripts/gcp.py` directly — `get_client()` + `list_entries(auth, filter_str)` — and apply two rules learned the hard way (PROD-2581 investigation, 2026-06-10):

- **Attribute via the transaction join.** ERROR/WARNING entries don't carry `clientHeaders` or `originalUrl`. To get the endpoint and app version for an error, join on `jsonPayload.transaction` to the same transaction's `Ending transaction` entry, which carries `originalUrl` as a **route pattern** (`/users/email/:email`, or `<baseUrl>/:unmatched`) plus `statusCode`. `clientHeaders.user-agent` (e.g. `AuraConsumerApp/1.21.1`) is on both `Starting transaction` and `Ending transaction`. Logs from before the route-pattern change carry the raw `originalUrl` on `Starting transaction` instead: see [`references/log-schema.md`](references/log-schema.md) → "Recovering `originalUrl`". A free-text term (`AND "<uid-or-tx>"`) searches all payload fields when you don't know which field carries the value.
- **Batch joins — the Cloud Logging read quota is 60 read requests/min/project.** A per-row join loop (one query per error) hits `RESOURCE_EXHAUSTED: ReadRequestsPerMinutePerProject` after ~60 rows and aborts mid-sweep. Combine lookups into one query with an OR-filter (`jsonPayload.transaction="A" OR jsonPayload.transaction="B" OR …`) over a shared time window; if the quota still trips, wait ~60s before retrying.
- **Orphaned-auth / lockout cross-referencing — strip your own probe traffic.** The orphan signal `No profile for the token` carries its Firebase UID in the **flat `jsonPayload.signInUid`** field (`jsonPayload.tokenUserId` on older entries), and is logged by *any* `verifyToken` caller — so diagnostics that POST to `/users/verifyToken` emit entries indistinguishable from a real lockout (`user-agent=Python-urllib`, `originalUrl` containing `/users/verifyToken`). Exclude those (via the transaction join above) before counting "active" orphans, or self-inflicted probes badly inflate the count. The middleware signal `Invalid user id from decoded token` is real-user-only by contrast. See [`references/log-schema.md`](references/log-schema.md) → "Orphaned-auth / lockout signals" for the full table and a worked example.

## References

- [`references/log-schema.md`](references/log-schema.md) — Format A vs Format B, the canonical fields, the multi-path extractors, the PII rationale.
- [`references/auth-setup.md`](references/auth-setup.md) — One-time SA creation walkthrough (GCP Console + `gcloud` CLI paths), JSON-to-single-line conversion, rotation guidance.
- [Linear ENGBACK-1012](https://linear.app/aura-health/issue/ENGBACK-1012/add-investigate-gcp-logs-skill-eng-plugin) — Full design spec, test plan, sample log entries used to lock the schema.
