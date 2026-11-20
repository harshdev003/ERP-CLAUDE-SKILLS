---
name: search-gcp-by-user
description: >
  Find the Aura prod GCP (GKE) log entries for one Firebase user id, oldest→newest, across the
  prod cluster. Read-only. This is the "what has this user been doing / what did this user
  hit?" entrypoint; results chain into eng:investigate-gcp-logs for tracing a specific
  transaction. Trigger when the user says "GCP logs for user {uid}", "search gcp by user",
  "what is user {uid} doing in the logs", "find cloud logs for firebase uid {uid}", or pairs a
  Firebase UID with GCP / cloud logs. With `--auth`, lists the user's Firebase Auth / Identity
  Platform sign-in events instead: "did user {uid} sign in", "auth logs for {uid}", "sign-in
  history for {uid}", "why can't {uid} log in". Do NOT use for time-window triage, focusing on one
  message, tracing a transaction, or following a Logs Explorer link — those are
  eng:investigate-gcp-logs.

metadata:
  version: "3.3.2"
knowledge:
  reads:
    - Guide/Index         # baseline routing aid (universal)
    - Tools/GCP           # SA, project, IAM, log-schema cheatsheet, Identity Platform auth logs
    - Compliance/Privacy  # user-scoped log output can contain PII
---

# search-gcp-by-user

Find the GCP log entries for one Firebase user id in Aura's prod GKE cluster (`aura-prod-cluster-a` in project `firebase-auratech16`). Read-only against Cloud Logging.

## Goal

Answer *"what has this user been doing in the logs / what did this user hit?"* in one command. It's the user-pivot companion to [`eng:investigate-gcp-logs`](../investigate-gcp-logs/SKILL.md) (triage / focus / trace-by-transaction): the output ends with an `investigate logs --transaction <id>` hint so you pivot from a user into a specific transaction trace.

## Inputs

| Flag | Default | Meaning |
|---|---|---|
| `--user <firebase-uid>` | *(required)* | The Firebase user id to search for |
| `--since <dur>` | `24h` | Time window (`30m`, `4h`, `2d`, …) |
| `--container <name>` | — | Filter by `resource.labels.container_name` (e.g. `user-service`) |
| `--namespace <name>` | — | Filter by `resource.labels.namespace_name` |
| `--cluster <name>` | `aura-prod-cluster-a` | Filter by `resource.labels.cluster_name` |
| `--project <id>` | from SA key | Override the project derived from the SA key |
| `--auth` | off | Search the Identity Platform auth logs instead of the GKE logs (see below). `--container` / `--namespace` / `--cluster` are ignored |
| `--max-entries <n>` | `2000` | Cap entries fetched |

## Run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/search-gcp-by-user/scripts/search.py --user <firebase-uid> [--since <dur>] [--container <name>]
```

## How it works

- **userId lives under more than one path.** The query ORs the two that are logged — `jsonPayload.userId` (the caller's uid from the request context) and `jsonPayload.signInUid` (set by user-service `verifyToken`). No service logs `verifiedToken` today, so the query doesn't filter on it — and a client-side check drops any post-normalize false positives.
- **Output** is the chronological entry list (oldest→newest, same `render_trace` shape as investigate-gcp-logs' trace mode), ending with a `→ Drill in: investigate logs --transaction <id>` hint built from the most recent ERROR/WARNING entry that carries a transaction.
- **Format A + B.** The normalizer handles both standard nested `jsonPayload` and the `CRITICAL - {...}` stringified-JSON-in-message form, same as the parent skill.

## Auth events (`--auth`)

`--auth` queries `resource.type="identitytoolkit_project"` for the uid and prints one line per event: time, severity, method, and only `provider`, `new_user`, `email_verified` and `status=<code> <ERROR_ENUM>`. The renderer ([`scripts/auth_logs.py`](scripts/auth_logs.py)) reads those fields by name and never walks the payload, so the emails, names, photo URLs and caller IPs these entries carry cannot reach the output. Pass `--project aura-dev` for dev if your credential can read it.

What it can and cannot find, from where the producer puts the uid (canonical detail on `Tools/GCP` → "Identity Platform user activity logs"):

- **Found:** IdP / password sign-ins and sign-ups (`response.localId`), token-bearing calls such as `GetAccountInfo` and `DeleteAccount` (`metadata.tokenInfo.claims.user_id`). `GetAccountInfo` dominates a busy user's list.
- **Never found:** `SignInWithCustomToken` (its log has no uid), failed calls (no `response`), token refreshes (not logged). An empty result does not mean the user never signed in — have them sign out and back in, then re-run.

When `--max-entries` is hit, the newest events are kept and the header says `CAP HIT`. Logging started 2026-10-06; nothing earlier exists.

## PII guardrail

This is a **trace-level** view: it surfaces the user's actual log entries (the user explicitly scoped to one uid), so output can contain user data — some Aura services (e.g. `neural-model`) dump full profiles inline. The digest is for the engineer's terminal. Per `Compliance/Privacy`, **do not paste raw output into an external surface** (Slack, Notion, Linear) without redacting to a UID prefix + email domain — never full email / givenName / familyName / free-text user content.

## Setup (one-time, per user)

1. **GCP service-account key** for the dedicated least-privilege logs-reader SA (`<redacted-email>`), stored as `GCP_KEY` (single-line JSON) in `~/.claude/settings.json` env. Auth chain: `GCP_KEY` → `GOOGLE_APPLICATION_CREDENTIALS` → ADC. Restart Claude Code after adding.
2. **Python deps:** `python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/search-gcp-by-user/scripts/requirements.txt` once. Just `google-cloud-logging`.

Full SA-creation walkthrough: [`eng:investigate-gcp-logs` → references/auth-setup.md](../investigate-gcp-logs/references/auth-setup.md).

## Edge cases

- **No entries for the user** → prints "No entries matched User `<uid>`. Try widening --since." rather than erroring.
- **Auth fails** → prints the specific failure (no `GCP_KEY`, parse error, expired ADC, wrong project) with the one-line fix, not a traceback.
- **Re-running** → safe (read-only).

## References

- Companion (drill-in target): [`eng:investigate-gcp-logs`](../investigate-gcp-logs/SKILL.md) — triage / focus / trace-by-transaction / Logs Explorer links.
- `Tools/GCP` (AI Knowledge) — SA, project, IAM, log-schema cheatsheet, Identity Platform user activity logs.
