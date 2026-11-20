---
name: check-postman-drift
description: >
  Weekly read-only check of each backend service's routes in code against the Aura Services
  Postman collection. Reads every service's Express router tree from origin's default branch, one
  repo at a time, and the collection with one Postman GET, then lists per service the routes
  missing from Postman, the requests Postman has that the code no longer serves, the stale ones
  (code needs auth, Postman sends none), and the requests named "[DELETED]" still waiting for an
  admin. Method and path only. Never edits Postman or a repo. Trigger on "check Postman drift",
  "is Postman up to date with the code", "which routes are missing from Postman", "postman drift
  report", or "run the weekly Postman check". Do NOT use to add or fix one entry
  (eng:update-postman-collection) or to call an endpoint (eng:call-aura-api).
metadata:
  version: "1.1.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Aura API       # the services, their route prefixes and the auth model
---

# check-postman-drift

You keep an honest list of where the **Aura Services** Postman collection and the services' code
disagree, so each owner can fix their own service. The check reads and reports; it never writes
to Postman or to a repo. Fixing an entry is `eng:update-postman-collection`, run by the owner.

## What it reads

| Side | How | Never |
|---|---|---|
| Code | each service's `src/http/router/index.js` on `origin`'s default branch and every router it mounts, through `git fetch` + `git show`, one repo at a time | a checkout, a grep, a worktree copy, two repos at once |
| Postman | `GET /collections` then `GET /collections/{uid}` with `POSTMAN_API_KEY` (a 429 or 5xx is retried, honouring `Retry-After`) | any write |

The service list is fixed in the script (`SERVICES`): the 13 backend repos, read from
`--services-dir`, else `AURA_SERVICES_DIR`, else `~/Development/Aura/Services`. A folder that
does not exist is a `services-dir` stop, never a run where every repo silently fails. A name that is not `<Name>Service` (a worktree copy such as
`PaymentService-wt-2234`) is refused.

A request belongs to the service whose top-level router mounts its first path segment
(`/coaching/…` → CoachService). Paths compare with every `:param` and `{{var}}` segment treated
as the same. Kubernetes probes (`/ready`, `/status`, the root `/`) are not routes.

## Run it

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-postman-drift/scripts/drift.py \
  --out "$SCRATCH/postman-drift-$(date +%F).md"
```

`--services CoachService,UserService` narrows the run; `--json` prints the report as JSON.

## What it reports

Per service, as `METHOD path` only (no bodies, tokens or example values):

- **missing**: in code, not in Postman;
- **extra**: in Postman, not in code;
- **stale**: same method and path, but the code requires auth (`authenticate`, `authorize`…) and
  the request, with what it inherits from its folders, sends none;
- **[DELETED]**: requests whose name starts with `[DELETED] `. An owner renames a removed route's
  request this way (description "Removed in vX.Y.Z") and a workspace admin deletes it by hand.
  They are listed so the admin sees what is waiting, and never counted as extra.

Across the collection it also lists the **folders whose requests are all `[DELETED]`** (the
outermost one, with nested folders counted), since a whole removed area should go as one folder
(Jerome, 2026-11-19). The owner renames such a folder `[DELETED] <name>` and a workspace admin
deletes it by hand. A folder not renamed yet is flagged **not renamed**, for its owner.

Also **unmapped** (a request whose first segment no scanned service mounts, `[DELETED]` ones
included and marked) and **router mounts not
followed** (a `use()` the parser could not resolve to a file). Both are listed, never dropped: a
mount it cannot follow would otherwise read as routes that do not exist.

Send the Markdown to Lead as the week's list; owners fix their own services.

## Stops

Every stop prints `FOLLOWUP stop <stage>: <why>` and the run goes on past a single repo:
`services-dir` (the checkout folder is missing), `token` (no `POSTMAN_API_KEY`), `collection` (the GET failed after retries, or the collection is
not found), `repo:<service>` (`git fetch` failed) and `router:<service>` (no routes read). Service
names are lowercased so the stage fits a follow-up key. Exit 0
means the run finished, drift or not; 3 means a stage stopped; 2 a bad argument.

**An unattended run raises one follow-up per stop line** with `shared:raise-followup`:
`--kind blocked`, `--key postman-drift:<YYYY-MM-DD>:<stage>`, the line as `--what`, and the ask
"fix the cause and re-run eng:check-postman-drift". It never ends the run with only a comment.
An interactive run with someone reading the report may skip it.
