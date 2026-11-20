# Aura log schema cheatsheet

What `scripts/normalize.py` knows about Aura's structured logs in GCP.

## TL;DR

Aura's structured logs land under `jsonPayload`, **but the format is not uniform across services.** Two known shapes — Format A (Node services) and Format B (Python services that emit `<LEVEL> - {<stringified-JSON>}`). The normalizer collapses both into a canonical shape with provenance tracking.

## GCP envelope (always present)

| Field | Source |
|---|---|
| `timestamp` (top-level) | RFC3339 nanos — authoritative ordering key |
| `severity` (top-level) | GCP severity (`ERROR`, `WARNING`, `INFO`, ...) |
| `insertId` | Dedup key |
| `resource.labels.cluster_name` | Always `aura-prod-cluster-a` for prod |
| `resource.labels.namespace_name` | `aura-services` (general), `aura-ai` (ML) |
| `resource.labels.container_name` | **Service identifier** for filtering |
| `resource.labels.pod_name` | Useful for "is this just one bad pod?" |
| `resource.labels.location` | GKE zone, e.g. `us-central1-c` |
| `logName` | `projects/firebase-auratech16/logs/stdout` |

## Format A — standard nested (most Node services)

```json
{
  "jsonPayload": {
    "level": "error",
    "message": "Invalid user id from decoded token",
    "transaction": "619D571B-7328-47DF-89CC-C66F70C35540",
    "transactionSlice": "619D571B",
    "userId": "...",
    "verifiedToken": {"verified": true, "tokenUserId": "bH635L4f..."},
    "context": {"userId": "..."},
    "component": "authenticateMiddleware",
    "method": "authenticate",
    "serviceContext": {"service": "@aura/user-service", "version": "2.74.5"},
    "error": {}
  }
}
```

Notes:
- `userId` may live at `jsonPayload.userId`, the **flat** `jsonPayload.signInUid` (on older entries the flat `jsonPayload.tokenUserId`), `jsonPayload.verifiedToken.tokenUserId`, or `jsonPayload.context.userId`. The normalizer walks all five in order and records which path matched. The flat `signInUid` is what user-service's `verifyToken` sets (it was `tokenUserId` until ENGBACK-2797 renamed it, because the log redaction hides any key containing "token"), so its high-volume orphan signal `No profile for the token` only resolves a UID via that path — miss it and that whole signal reports no userId.
- `error` may be `{}`, populated `{message, stack, code}`, or absent entirely.
- `serviceContext.service` is npm-style (`@aura/user-service`) — use `resource.labels.container_name` for filtering instead (`user-service`), no `@` to escape.

## Format B — stringified-JSON-in-message (Python services)

Spotted in `neural-model`, possibly other Python ML services.

```json
{
  "jsonPayload": {
    "message": "CRITICAL - {\"message\": \"Returning recommendations\", \"meta\": {\"transaction\": \"6af31e95-de03-4de9-a515-49f801510cd9\", \"transactionSplit\": \"6af31e95\", \"userId\": \"...\", \"component\": \"Predictor\", \"method\": \"predict\"}, \"recommendations\": \"[...]\", \"severity\": \"DEFAULT\", \"serviceContext\": {\"service\": \"neural-model\", \"version\": \"1.5.44\"}}"
  }
}
```

Critical to handle correctly because:

1. **Per-call params make every line unique.** Naive grouping by `jsonPayload.message` produces 1,000 groups of 1 instead of 1 group of 1,000. The normalizer detects the `<LEVEL> - {...}` prefix, parses the embedded JSON, and uses `inner.message` ("Returning recommendations") as the canonical message.
2. **Field names differ** — `transactionSplit` vs `transactionSlice`. The normalizer's `tx_slice` extractor accepts both.
3. **PII risk** — these payloads include full user profiles (gender, age group, persona, locale, topic preferences, recommendation IDs). Modes 1 (triage) and 2 (focus) **never** echo `jsonPayload.message` raw — only the extracted canonical fields. Mode 3 (trace) shows the message with a hard 120-char truncation.

If the embedded JSON re-parse fails, the normalizer marks the entry as Format `B-fallback` and truncates the message to 200 chars defensively before any rendering.

## Canonical fields after normalization

| Canonical | Format A source | Format B source |
|---|---|---|
| `message` | `jsonPayload.message` | `inner.message` |
| `transaction` | `jsonPayload.transaction` | `inner.meta.transaction` |
| `tx_slice` | `jsonPayload.transactionSlice` | `inner.meta.transactionSplit` (or transactionSlice) |
| `user_id` | `jsonPayload.userId` → `signInUid` (flat) → `tokenUserId` (flat, older entries) → `verifiedToken.tokenUserId` → `context.userId` | `inner.meta.userId` |
| `component` | `jsonPayload.component` | `inner.meta.component` |
| `method` | `jsonPayload.method` | `inner.meta.method` |
| `service_name` | `jsonPayload.serviceContext.service` | same |
| `service_version` | `jsonPayload.serviceContext.version` | same |
| `format` | `"A"` | `"B"` or `"B-fallback"` |
| `user_id_path` | provenance label, e.g. `verifiedToken.tokenUserId` | prefixed `meta.` for inner-meta paths |

## Cross-service correlation

**No GCP `trace` field** is present in any sample. Aura services don't emit OTel trace IDs into Cloud Logging today. Cross-service correlation is by `jsonPayload.transaction` (or its inner-JSON equivalent) only — case-insensitive (samples are mixed-case UUIDs: some uppercase like `619D571B-...`, some lowercase like `fded4d3b-...`).

## Orphaned-auth / lockout signals — and filtering your own probes

When cross-referencing logs to find users with a valid Firebase token but no Aura profile (orphaned auth), user-service emits two distinct signals:

| Signal (message) | Logged by | UID field | Who triggers it |
|---|---|---|---|
| `No profile for the token` | `userModel.verifyToken` | flat `jsonPayload.signInUid` (older entries: `tokenUserId`) | **Any** caller of `verifyToken` — real authenticated requests *and* anyone POSTing to `/users/verifyToken` (other services, or our own orphan diagnostics) |
| `Invalid user id from decoded token` | `authenticateMiddleware.authenticate` | `jsonPayload.verifiedToken.tokenUserId` | **Real users only** — fires only when a request flows through the `authenticate` middleware (`/users/verifyToken` does not) |

**Watch out for self-inflicted entries.** The old orphan-cleanup diagnostic (`signInWithCustomToken` → `POST /users/verifyToken`) produces `No profile for the token` entries that are byte-identical to a real user being locked out, with `tokenUserId=<probed uid>`. In one real 48h sample, **92 of 244** `No profile` entries were our own probes, which made **34 dormant orphans look "actively erroring"** (the true count from real traffic was 2). To get a clean count of *actually* locked-out users, drop probe traffic:

- **`user-agent == "Python-urllib"`** → a script (our diagnostics), not the app. Real Aura clients report `user-agent` starting with `Aura`.
- **`originalUrl` contains `/users/verifyToken`** (match by substring: the logged route is a pattern and may carry the method) → a direct token-verification call, not a real app endpoint. Real lockouts come in on endpoints like `/buttons/recommend`, `/scheduling/appointments/list`, `/users/session/cookie`.

**Recovering `originalUrl` / `user-agent`.** The error entry itself carries neither. Join on `jsonPayload.transaction` and read them off the same transaction's other lines:

- **`originalUrl`** is on the **`Ending transaction`** entry (audit severity), as the **route pattern**, e.g. `/users/email/:email`, or `<baseUrl>/:unmatched` when no route matched, with `statusCode` beside it. It is never the raw path, so it names the endpoint, not the user or record. The 4xx/5xx request line and the error line log the same pattern. `Starting transaction` carries no `originalUrl`.
- **`clientHeaders.user-agent`** is on both `Starting transaction` and `Ending transaction`.
- **Older logs** (before the services switched to route patterns in ENGBACK-2399, rolling out no earlier than 2026-11-08) carry the raw `originalUrl` on `Starting transaction` and none on `Ending transaction`. For a window spanning the rollout, read whichever line has it. Batch the joins with an OR-filter (`jsonPayload.transaction="A" OR ...`) to stay under the 60-read/min quota (see SKILL.md → ad-hoc follow-up queries).

## Severity convention

The skill defaults to `severity>=WARNING`, not `>=ERROR`. Aura services log real user-facing problems at WARNING (Firebase retry exhaustion, locale-update failures, etc.), so the ERROR filter misses important triage signal. Pass `--severity ERROR` explicitly when needed.

## Source samples used to lock this schema

Captured 2026-05-06 — see [Linear ENGBACK-1012](https://linear.app/aura-health/issue/ENGBACK-1012/add-investigate-gcp-logs-skill-eng-plugin) for the full JSON of each:

1. **Format A WARN** — `firebaseService.updateUserBrowserInfo` "Firebase could not update user locale: Error: maxretry" (top-level `userId`, empty `error: {}`, has `browserInfo` with cookies)
2. **Format A ERROR** — `authenticateMiddleware.authenticate` "Invalid user id from decoded token" (transaction `619D571B-7328-47DF-89CC-C66F70C35540`, `userId` only at `verifiedToken.tokenUserId`, no `error` field at all)
3. **Format B ERROR** — `neural-model-14` "CRITICAL - {...}" (Returning recommendations, transaction `6af31e95-de03-4de9-a515-49f801510cd9`, `transactionSplit` not `transactionSlice`, full user profile inline → PII concern)

When you spot a third format in the wild, add it here and extend `normalize.py`.
