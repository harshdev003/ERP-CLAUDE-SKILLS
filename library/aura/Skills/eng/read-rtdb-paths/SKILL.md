---
name: read-rtdb-paths
description: >
  Read exact prod Firebase RTDB paths the safe way: through the Admin SDK realtime protocol, never
  REST, at most 20 reads in flight, refusing the root, top-level nodes, /users itself and any shallow
  or query read, and printing field names and counts rather than values. Owns the canonical
  rtdb_get.js that other skills copy. Trigger on "read this RTDB path", "check whether this RTDB
  node exists", "look up {uid} in RTDB", "read prod RTDB", "what fields does /coaches/{id} have",
  or any one-off script that would otherwise curl firebaseio.com or use Python firebase_admin.db.
  Do NOT use for data that lives in a service's Postgres (ask that service's owner to run SQL),
  for listing or counting a large node (a batch job run by the code owner), or for writes.
metadata:
  version: "1.0.1"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/Firebase     # "Reading prod data": Postgres first, then the Admin SDK
    - Tools/GCP          # GCP_KEY service account
    - Compliance/Privacy # field names and counts, uid prefixes only
---

# Read RTDB Paths

## Goal

Read a few exact prod RTDB paths without setting off the RTDB REST read alert and without pulling
more than the paths named. The rule this follows is `Tools/Firebase` → "Reading prod data: the
owning service's Postgres first, then the Admin SDK" in AI Knowledge; read it rather than this
summary when they differ.

## Before reading RTDB at all

1. **Is the data in a service's Postgres?** A coach list with emails, a user's subscription, a
   payment: those are read with SQL on the owning service's database, run by that service's owner.
   Look at the schema, write the query, and hand it over. Stop here.
2. **Only then RTDB**, through this skill's `rtdb_get.js`. Never `https://<db>.firebaseio.com/<path>.json`
   (REST), and never Python `firebase_admin.db`, which reads over REST too.

## Run

```bash
export RTDB_URL=<the prod database URL, from Tools/Firebase>
export AURA_CLI_DIR=<a checkout whose node_modules has firebase-admin, such as the Aura CLI>
node ${CLAUDE_PLUGIN_ROOT}/skills/read-rtdb-paths/scripts/rtdb_get.js /coaches/<id> /users/<uid>/email
```

Paths can also come one per line on stdin. Credentials are `GCP_KEY` (the service account).

A read gives up after 30 seconds (`TIMEOUT`). The SDK can't cancel it, so after the first timeout
no new read starts and the rest come back as `NOT_READ_AFTER_TIMEOUT`: the in-flight cap holds.

What it prints, one JSON line per path: `{path, exists, type, count, fields}`. Field names longer
than 15 characters (usually ids) are cut to 6 characters. More than 50 fields gives the count only.
`--values` prints the values; use it only when the task needs them, and never paste values from
`/users` or emails into Slack, Linear or a PR (uid prefixes and masked emails only).

## What it refuses

Every path is checked before anything connects, and one refusal stops the whole run:

| Refused | Why |
|---|---|
| `/`, any top-level node (`/coaches`), `/users` itself | a root or whole-node read is what the alerts flag, and `/users` is never read |
| a `?`, a query parameter such as `&shallow=` or `&orderBy=`, or a path ending `.json` | shallow and query reads are not allowed on prod |
| a key with `. # $ [ ]` | not a valid RTDB key, usually a pasted URL |
| `--concurrency` above 25 | default 20 in flight |
| more than 200 paths without `--bulk` | a bulk read: announce it first |

This is a cooperative guard, not enforcement: admin keys bypass security rules, so it only keeps
our own reads in line. `node rtdb_get.js --self-test` checks every refusal and the no-values rule
offline.

## The user agent

The run is labelled `AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)`, the same format
as `gcloud_sa.py` (`<who>` is the git email's local part, `AINATIVE_UA_WHO` overrides it). A Python
caller passes its own `user_agent()` in `AINATIVE_USER_AGENT`. A realtime connection sends no custom
user agent, so the label names the Firebase app and is printed in the run summary on stderr; the
profiler sees these reads as admin reads from our IP. A label, not proof: anyone can send it.

## Using it from another skill

A skill that reads RTDB ships a byte-identical copy of `scripts/rtdb_get.js` in its own `scripts/`
(canonical source and drift check: `docs/canonical-sources.md`). Call the CLI, or `require` it and
pass an open database to `readPaths(paths, {db})`.
