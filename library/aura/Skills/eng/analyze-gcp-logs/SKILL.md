---
name: analyze-gcp-logs
description: >
  Broad warning/error health-check sweep of Aura's prod GKE cluster (aura-prod-cluster-a /
  aura-services) with the team's standard known-noise exclusion filter baked in, rendered as a
  ranked breakdown of distinct message signatures and their occurrence counts. The broad-scan
  counterpart to `investigate-gcp-logs`: use this to answer "what is warning or erroring right
  now across the namespace, minus the chatter we've already triaged away?", then hand off to
  drill into one signature. Trigger on "analyze gcp logs", "analyze cloud logging", "what
  warnings/errors are happening across aura services", "gcp log health check", "summarize prod
  warnings", "warning breakdown", "what's warning right now", "show me the warning signatures
  with counts", "sign-in error breakdown", or a pasted severity>=WARNING aura-services filter
  asking what appears and how often. Do NOT use to trace ONE transaction or user, or to drill
  into one error message — that's `investigate-gcp-logs`. Not for alerts or log-based metrics.

metadata:
  version: "3.6.2"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/GCP     # SA, project, IAM, log schema cheatsheet, Identity Platform auth logs, RTDB scraping detection, paste-export workaround
---

# analyze-gcp-logs

Broad **health-check sweep** of Aura's prod GKE cluster (`aura-prod-cluster-a` in project `firebase-auratech16`, namespace `aura-services`). Runs a `severity>="WARNING"` query with the team's curated **known-noise exclusion filter baked in**, then renders a ranked breakdown of distinct message signatures with occurrence counts. Read-only; no writes anywhere.

## When to use this vs `investigate-gcp-logs`

| You want… | Skill |
|---|---|
| "What is warning/erroring right now across the namespace?" (signal-only overview) | **analyze-gcp-logs** (this skill) |
| "Tell me everything about THIS one error / transaction / user" (drill-down) | **investigate-gcp-logs** |

The two **compose**: analyze-gcp-logs ends every run with copy-paste `investigate logs …` handoff hints, so a broad sweep flows directly into a deep dive. They share the same auth + log-normalization machinery (each skill carries its own copy per AINative self-containment).

The key difference from `investigate-gcp-logs`' triage mode is the **baked-in noise filter**: triage is deliberately unfiltered (you want to see everything when drilling), so a raw triage returns thousands of known-noise entries. analyze-gcp-logs strips that chatter by default so the breakdown is signal out of the box.

## Goal

Give the on-call engineer a one-command answer to "what's the warning/error picture right now?" without re-pasting a 25-line exclusion filter every time. The known-noise list is maintained in one place ([`references/default-filter.md`](references/default-filter.md)) and edited as new noise appears.

## Inputs

The user provides (all optional):

- A **time window**: "last 4 hours" (default), "last hour", "last day".
- Optionally a **service narrow**: "…in payment-service" → `--container payment-service`.
- Optionally a **severity bump**: "errors only" → `--severity ERROR`.
- Sometimes a request to **see the unfiltered picture**: "what is the noise filter hiding?" → `--no-default-filter`.

Defaults when the user is vague: `severity=WARNING`, `since=4h`, `namespace=aura-services`, baked-in filter on. Confirm with the user before scanning **>24h** or running `--no-default-filter` over a wide window — both are slow and noisy.

## Workflow

### 1. Resolve the window and any narrows

Map the user's intent to flags:

| User said… | Flags |
|---|---|
| "analyze gcp logs" / "what's warning right now" | (defaults: `--since 4h`) |
| "last hour" / "last 24h" | `--since 1h` / `--since 24h` |
| "errors only" | `--severity ERROR` |
| "…in payment-service" | `--container payment-service` |
| "all namespaces" | `--namespace ''` |
| "what is the filter hiding?" / "raw" | `--no-default-filter` |
| "use this filter instead: …" / pastes a custom exclusion list | write it to a temp file → `--filter-file <tmp>` (see "The baked-in noise filter") |

### 2. Run the sweep

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-gcp-logs/scripts/analyze.py <flags>
```

The script handles auth (`GCP_KEY` → `GOOGLE_APPLICATION_CREDENTIALS` → ADC), loads the exclusion fragments from [`references/default-filter.md`](references/default-filter.md), builds the filter, queries Cloud Logging, normalizes both log formats (Format A nested + Format B `LEVEL - {…}` stringified JSON), groups by `(message, service)`, and prints the ranked breakdown. The resolved filter is echoed to stderr (`# query: …`) so the exact LQL is auditable. Pass the stdout digest through to the user verbatim — it ends with the `→ Drill into a signature` handoff block.

### 3. Hand off, don't auto-chain

The skill never picks which signature to drill into. The engineer reads the breakdown, decides which row is interesting, and pastes the suggested `investigate logs …` command (into the `investigate-gcp-logs` skill). The handoff block always offers a `--message` drill and, when the top signature has a transaction, a `--transaction` trace.

### 4. PII guardrail

Same contract as `investigate-gcp-logs` triage: the breakdown **never echoes the raw `jsonPayload`** — only the canonical short message, service, counts, version, and a truncated tx-slice / user-id prefix sample. Some Aura services dump full user profiles inline; raw output would be a PII leak. (If you then drill in with `investigate-gcp-logs --transaction/--user`, that skill's trace mode shows full entries by design, because the user has explicitly narrowed to one subject.)

## The baked-in noise filter — on by default, overridable

**Filtering is the default.** With no flags, every run applies the curated exclusion list in [`references/default-filter.md`](references/default-filter.md) — a `text`-tagged fenced block, one complete Cloud Logging filter fragment per line, each a negation (`-…`), `AND`-ed onto the base query. Signal-only out of the box is the whole point of the skill.

**But the user can always override it for a run.** Four ways, lightest-touch first:

| Goal | How |
|---|---|
| **Custom list for one run** — user pastes their own exclusions in chat | Write the pasted fragments to a temp file, run `--filter-file <tmp>`. Repo unchanged. (See below.) |
| **A list that already lives in a file** | `--filter-file <path>` — replaces the default list wholesale for that run. |
| **No filter at all** (raw sweep) | `--no-default-filter` — also handy to audit what the default is currently hiding. |
| **Change the default durably** | Edit `references/default-filter.md` and open a PR. |

**When the user pastes their own filter in chat, treat it as a one-off overwrite.** Write the pasted exclusion lines (or a whole pasted filter) to a temp file and run with `--filter-file <tmp>` — `analyze.py` reads the first ` ```text ` fenced block, falling back to the whole file when there's no fence, so a bare list of `-…` lines works fine. Do **not** edit `references/default-filter.md` for a one-off. Only offer to persist the change (edit the file + open a PR) if the user says it should stick "going forward."

**Editing the default list** (durable changes only):

- **Add an exclusion** when a signature is confirmed-noise and is drowning the breakdown: append a `-jsonPayload.message="…"` (exact) or `-(jsonPayload.message=~"…")` (RE2 regex, escape `.` as `\.`) line inside the fenced block.
- Each line must be a single, self-contained filter term — the parser does not join lines.

## Auth sign-in errors — outside this sweep

The sweep reads `k8s_container` logs in the cluster, so it **never sees** Firebase Auth / Identity Platform traffic (`resource.type="identitytoolkit_project"`, both projects since 2026-10-06) or the `authUserClaim` blocking function — a clean breakdown says nothing about sign-ins. For the auth leg of a health check, tally errors by method and error code:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-gcp-logs/scripts/gcloud_sa.py logging read 'resource.type="identitytoolkit_project" AND severity>=WARNING' \
  --project firebase-auratech16 --freshness=1h \
  --format='value(jsonPayload.methodName,jsonPayload.status.code,jsonPayload.status.message)' \
  | awk -F'\t' '{n=split(\$1,m,"."); split(\$3,e,/[ :]/); print m[n], \$2, e[1]}' | sort | uniq -c | sort -rn
```

Swap `--project aura-dev` for dev. Empty output means no failed auth calls in the window. Method names, the error shape, the uid paths and the `authUserClaim` log location are canonical on **`Tools/GCP` → "Identity Platform user activity logs"**; drill into one user with `eng:search-gcp-by-user --user <uid> --auth`.

**PII — non-negotiable.** These payloads carry end-user emails, names, photo URLs, federated ids and caller IPs. Print field names, counts, method / error-code tallies or a uid prefix only — never a value. Never use `--format=json` output in a reply, and never paste an email into Linear or Slack. The `awk` above keeps only the method, the numeric code and the leading error enum for that reason.

## Realtime Database traffic — outside this sweep

The Realtime Database writes no per-request logs, so a clean breakdown says nothing about RTDB scraping or key enumeration. For that leg of a health check, read the built-in Cloud Monitoring metric `firebasedatabase.googleapis.com/io/database_load` grouped by `type` and look for a `get_shallow` spike against the same hours on previous days. Don't look for it in request counts: one shallow read of `/users` returns every key in a single request, so `rest_shallow_get` on `network/api_hits_count` barely moves. Live alert policies already watch for it (`RTDB … Check on Firebase Policy`, posting to #alerts-security); the current list is on `Tools/GCP`. **`Tools/GCP` → "RTDB scraping and enumeration detection" is canonical** for the metric table, the label values, the query recipe and how to read a spike — read it rather than relying on this summary.

**Safety — non-negotiable.** Never query the RTDB root or `/users` on prod to check, not even with `limitToFirst=1` or a shallow read, and never fetch other users' data. Metrics and the profiler only.

## Setup (one-time, per user)

Identical to `investigate-gcp-logs` (shared auth model). Two paths — pick one.

**Recommended (works in Cowork too):** inline service-account JSON in `~/.claude/settings.json`:

```json
"env": {
  "GCP_KEY": "{\"type\":\"service_account\",\"project_id\":\"firebase-auratech16\",...}"
}
```

The skill derives the project ID from `GCP_KEY["project_id"]`. Pass `--project <id>` for one-off cross-project queries.

**Alternative (most secure on a personal laptop):** `gcloud auth application-default login` — picked up via `google.auth.default()`.

**Python deps:** `python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/analyze-gcp-logs/scripts/requirements.txt` once (just `google-cloud-logging`). Use `python3 -m pip`, not bare `pip`.

**Cowork caveat ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)):** `mcp__workspace__bash` strips host env. Inside Cowork, paste-export `GCP_KEY` once at session start:

```bash
export GCP_KEY='<paste from host ~/.claude/settings.json — single quoted to preserve JSON>'
```

## Output

A fixed-shape text digest:

```
GCP log sweep | ≥ WARNING | last 4h | aura-prod-cluster-a | namespace=aura-services | 405 entries, 132 signatures | 23 noise exclusions applied

  #  message                          service                  count  first                 last                  versions  sample
  1  Daily notification not built …   intelligence-service-…     100  2026-05-26T16:01:08Z  2026-05-26T19:02:28Z  0.246.2   tx=d8a5f435 u=STjWsF4…
  2  User deleted                     payment-service-premium     26  ...
  ...
... 102 more signatures (re-run with --top 132 to see all)

→ Drill into a signature (deep-dive via investigate-gcp-logs):
   investigate logs --message "Daily notification not built — no rows written" --container intelligence-service-daily-notification --since 4h
   investigate logs --transaction d8a5f435-28af-432e-86a6-01f8312a2b9d   # trace the latest occurrence of signature #1
   (swap in any message/service/tx from the rows above to chase a different one)
```

## Edge cases

- **0 entries after filter** → clean window; the digest says so and suggests `--since` wider, `--severity WARNING`, or `--no-default-filter` to confirm the filter isn't over-excluding.
- **Auth fails** → the script prints the specific failure (no `GCP_KEY`, key parse error, expired ADC, wrong project) with the one-line fix, not a traceback.
- **CAP HIT** (entries fetched = `--max-entries`) → the header flags it; raise `--max-entries` or narrow `--since`/`--container`.
- **Filter file missing / no fenced block** → the script warns on stderr and runs with no exclusions rather than crashing (falls back to first fenced block, then whole file).
- **Re-running the same sweep** → safe (read-only); no idempotency concern.

## References

- [`references/default-filter.md`](references/default-filter.md) — the baked-in noise-exclusion list (editable) + how the query is assembled.
- Sibling skill [`investigate-gcp-logs`](../investigate-gcp-logs/SKILL.md) — the drill-down counterpart this skill hands off to; see its `references/log-schema.md` for Format A vs B and the multi-path field extractors (shared normalization machinery).
