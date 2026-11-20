---
name: verify-service-accounts
description: >
  Health-check every Aura service identity in dev or prod and report whether each can still
  do its job. Asserts each stored SERVICE_TOKEN resolves to the expected account AND that the
  account still carries its `service` role — an account whose role has gone null authenticates
  fine and authorizes nothing. Emits a per-environment table and exits non-zero on failure, so
  it can run unattended. Never prints a token value. Trigger on "verify service accounts",
  "check the service accounts", "are the service tokens still valid", "health check the service
  identities", "did a service account lose its role", "check service account roles", "sweep the
  service tokens", "is intelligence-service's token still good", "service account health", or
  "audit the SERVICE_TOKENs". Do NOT use for deleting orphaned end-user auth
  (`eng:cleanup-orphaned-auth`), for rotating or resealing a secret (a human AuraInfra change),
  or for general Aura API calls (`eng:call-aura-api`).
metadata:
  version: "1.2.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Aura API       # /users/verifyToken and GET /users/me contracts
    - Compliance/Privacy   # the run touches the /users/{id} family
---

# Verify service accounts

Sweeps every service identity in one environment and answers one question per
identity: **can this principal still do its job?**

That is deliberately not the same question as "does this token parse". In
[PROD-6468](https://linear.app/aura-health/issue/PROD-6468) the support-service
token decoded correctly and resolved to a real, live, non-deleted account for
the entire 14h45m outage. What was broken was `role: null`. A bare
`verifyToken` check would have reported all-green throughout — which is why
this skill asserts the role too, and why a run is not green without it.

## What it checks

Two read-only probes per identity, both against the environment's user-service:

| Probe | Answers |
|---|---|
| `POST {host}/users/verifyToken` | does the stored token resolve to an account, and which |
| `GET {host}/users/me` (Bearer) | does that account still carry its role, and is it live |

The second goes through the same `userModel.retrieve` call the `authorize`
middleware reads `user.role` from, so it reads the value every authorization
decision in the product is actually made on.

An identity is **OK** only when all of these hold:

- `verifyToken` returns `verified: true` with a non-empty `id`
- `GET /users/me` returns 200
- `role` equals the role expected **for that identity** (see below)
- `deletedAt` is null
- both probes agree on the UID
- the account's email local-part matches the directory the token is stored
  under — this is what catches a token copied into the wrong service's env file

**WARN** covers drift that does not stop the identity working: a missing
committed sealed secret, or a live cluster Secret that differs from the `.env`
file. **FAIL** is anything in the list above.

### The expected role is per identity

Roles differ between identities, so there is no single right answer to hold all
fourteen to. Aura files each service identity under the sub-domain its account
belongs to, and the role follows the sub-domain: `@services.*` accounts carry
`service`, `@systems.*` carry `system`. Resolution, highest first:

1. `--expected-role`, which applies to every identity when passed
2. the `role` pinned for that identity in `--baseline`
3. the account address sub-domain — `services` to `service`, `systems` to `system`
4. `service`

The address is the right thing to derive from because it is not the field that
breaks: a role going `null` leaves the email untouched, so the check still
fires. The resolved expectation is shown per row in the `Expected` column.

### Two traps, both load-bearing

**`tokenUserId` is usually absent, and that is healthy.** user-service persists
a token on first successful verification and answers from its token table
forever after; that branch returns `id` without `tokenUserId`. Twenty-six of
the twenty-eight real identities come back that way. Asserting
`tokenUserId == id` unconditionally fails almost every service account while
nothing is wrong — so the pair is compared only when the server sent both.

**A past `exp` is not a fault.** Every stored `SERVICE_TOKEN` is a Firebase ID
token with a 1-hour `exp`, and almost all of them expired years ago and still
authenticate, because the persisted path never re-checks it. Expiry is reported
and never counted as a failure.

Both follow from what the credential actually is. Read
[`references/service-token.md`](references/service-token.md) before changing
any check — it has the resolution order in user-service, why the tokens are
read rather than minted, and which of the three storage sources can be compared
at all.

## Prerequisites

- An **AuraInfra checkout** — the skill auto-discovers it (cwd, then
  `~/Development/Aura/Infra/AuraInfra` and the usual clone locations), or pass
  `--infra-dir`.
- Network access to the environment's user-service. No token of your own is
  needed: each identity is probed with its own stored credential.
- `kubectl`, with a context for the target cluster, **only** for the optional
  `--check-cluster` leg.

## Workflow

### 1. Pick the environment explicitly

`--env` is required and has no default, so a sweep never silently hits
production. Ask the user which environment if they did not say.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/verify-service-accounts/scripts/verify.py \
  --env dev
```

`--env both` sweeps dev then prod and reports them as separate tables.

### 2. Read the result

The script prints one table per environment, then a detail block for every
identity that is not OK, then a one-line summary. Exit codes: **0** every
identity OK or WARN, **1** at least one FAIL, **2** the sweep could not run.

Report the table as-is. The identities are enumerated from the manifests, not
from a list in this skill — the set is whatever declares `SERVICE_TOKEN` under
`k8s/apps`, which is how `ai/emotion-system` is covered despite sitting outside
`services/` and running in the `aura-ai` namespace.

### 3. Triage the failures

For each FAIL, say what broke and what fixes it. The failure modes and what
each one means:

| Failure | Reading |
|---|---|
| `role` is not the expected one | the PROD-6468 fault — the identity authenticates but authorizes nothing. Restore the role on the account. |
| `verified=false` + `/users/me` 403 | the stored credential resolves to nothing. It needs re-minting and resealing. |
| identity mismatch | the token in this service's env file belongs to another service. |
| UID changed vs baseline | the account behind this service was replaced. |
| probe unreachable | a transport fault, not a verdict — say so rather than reporting the identity as broken. |

**A dead credential is not automatically an outage.** A FAIL says the stored
token does not work, which is a real defect worth fixing — but whether
anything is *currently* failing depends on the routes that service actually
calls. A consumer whose endpoints carry no `authenticate` middleware never
presents the credential meaningfully, so a dead token there has no runtime
effect. Before escalating a FAIL as an incident, read the consuming service's
router and confirm the routes it calls are authenticated. Report the credential
as broken either way; report an outage only once you have checked.

Rotating or resealing a secret is a human AuraInfra change. This skill reports;
it does not write.

### 4. Optional legs

**Cluster cross-check.** `--check-cluster` fingerprints each live cluster
Secret and compares it to the `.env` value, catching a service running
something other than what the repo says. It shells out to `kubectl` and needs a
context for the target cluster.

```bash
python3 .../verify.py --env prod --check-cluster
```

The committed `*-secret.json` is checked for **presence only** — a SealedSecret
is ciphertext under a random per-seal key, so its value can never be compared
to the plaintext. Do not add a comparison that pretends otherwise.

**UID baseline.** Pin the expected UIDs once, then have later runs flag any
change:

```bash
python3 .../verify.py --env both --baseline ~/.config/aura/service-uids.json --write-baseline
python3 .../verify.py --env both --baseline ~/.config/aura/service-uids.json
```

Without a baseline the identity assertion still holds — it is the email
local-part match, which needs nothing pinned.

**Machine output.** `--json` emits the full result, including per-probe status
codes and the decoded claims, for a scheduled run that posts elsewhere.

## Flags

| Flag | Default | Purpose |
|---|---|---|
| `--env` | *required* | `dev`, `prod`, or `both`. No default, by design. |
| `--infra-dir` | auto-discover | AuraInfra checkout containing `k8s/apps`. |
| `--expected-role` | `service` | The role every identity must carry. |
| `--check-cluster` | off | Also fingerprint the live cluster Secret. |
| `--baseline` | — | JSON of expected UIDs, keyed by env then service. |
| `--write-baseline` | off | Write the observed UIDs to `--baseline`. |
| `--json` | off | Raw result instead of the table. |
| `--timeout` | `30` | Per-request seconds. |
| `--concurrency` | `6` | Parallel probes per environment. |

## Handling secrets

**A token value is never printed, logged, or written.** Identification is by
`sha256[:12]` fingerprint throughout — in the table, in the detail blocks, in
`--json`, and in the cluster comparison, which fingerprints in-process. Keep it
that way: a fingerprint is enough to tell two credentials apart, which is all
any check here needs.

Output is service-account identities only — UIDs and `@services.aurahealth.io`
addresses, never an end-user record. Nothing in a run reaches a real user's
data, so the `Compliance/Privacy` UID-prefix rule has nothing to redact; keep
it that way by not widening the probes to other accounts.

## Running it unattended

The exit code is the whole contract: non-zero means at least one identity
cannot do its job, and stderr carries a one-line `FAILING:` list naming each
one. That is enough for a cron wrapper or `eng:configure-cron-job` to alert on
without parsing the table.

Re-running is safe and idempotent. Both probes are reads; neither signs in, so
neither touches `lastLoginAt` — which matters, because
`eng:cleanup-orphaned-auth` gates on an inactivity window that a sign-in-based
check would silently defeat. See
[`references/service-token.md`](references/service-token.md) → *Why the tokens
are read, never minted*.

## Tests

[`tests/test_service_account_verdict.py`](../../../../tests/test_service_account_verdict.py)
injects the branches live data cannot reach — a null role, a soft-deleted
account, a cross-wired token, a mismatched `tokenUserId`, a UID change against
a baseline — plus the two negative assertions that matter most: a long-expired
token stays OK, and an absent `tokenUserId` stays OK. Run the suite after any
change to the verdict rules:

```bash
python3 -m unittest discover -s tests -q
```
