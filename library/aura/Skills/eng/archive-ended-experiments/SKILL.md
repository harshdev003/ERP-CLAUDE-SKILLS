---
name: archive-ended-experiments
description: >
  Moves ended, no-longer-referenced A/B experiments out of the Firebase RTDB `/experiments`
  node into `/backup/experiments`, shrinking the payload every Aura client syncs on the
  paywall path. Data-side counterpart to `eng:sweep-ended-experiments`, which files cleanup
  cards for experiment code — this one moves rows and touches no code. Reconciles the live
  Firebase node, the Aura API catalog and the AI Knowledge experiments database, refuses to
  archive anything current code can still name, tranches the rest oldest-first, and drives
  the `aura firebaseBackup` CLI with a throttle, a manifest and per-row rollback. Dry-run by
  default. Trigger on "archive ended experiments", "back up the experiments node", "move old
  experiments to /backup", "shrink the experiments node", "run the {year} archive tranche",
  or "the experiments node is too big". Do NOT use to remove experiment code
  (`eng:sweep-ended-experiments`), end a live experiment, or assign a user to a bucket
  (`eng:set-user-experiment`).
metadata:
  version: "1.2.0"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/Firebase               # RTDB access, the CLI's credentials
    - Tools/Aura API               # GET /experiments catalog (endedAt/deletedAt)
    - Tools/Notion                 # Learnings/Experiments cross-check
    - Learnings/Experiments        # human record: Status + End Date per experiment
---

# Archive ended experiments

`/experiments` is read **in full** by every Aura client, on the conversion-critical path:
the RN app holds it with `keepSynced(true)`, and UserWeb/AIWeb fetch the whole node on
every webview paywall load. Most of it is experiments that finished years ago.

This skill moves those rows to `/backup/experiments`. It does not delete anything, and
every move is reversible with one command.

## The thing that makes this dangerous

**An ended experiment is still answering.** `ExperimentHelper.chooseAll` reads a
single-letter `active` as "ended — serve this winner to everyone". Archive a row that some
installed app version still asks for, and `chooseAll` finds nothing, writes nothing, and the
screen falls back to its default. Those users silently move to the control arm.

No error. No log. No Bugsnag event. The only symptom is a metric moving, which is why the
matcher over-keeps and why tranches soak.

## Workflow

### 1. Export the node (read-only)

```bash
cd <CLI repo> && set -a && . ./.env && set +a
aura firebaseExport /tmp/experiments-$(date +%F).json /experiments
```

The CLI owns the production Firebase credentials. Do not reimplement RTDB access here.

### 2. Reconcile three sources

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/archive-ended-experiments/scripts/reconcile.py \
  --export /tmp/experiments-$(date +%F).json \
  --repos-root ~/Development/Aura \
  --out /tmp/tranches.json
```

| Source | Says |
|---|---|
| Firebase `/experiments` | what clients actually read — `active` length 1 means ended |
| Aura API `GET /experiments` | when it was stamped ended (`endedAt` / `deletedAt`) |
| AI Knowledge `Learnings/Experiments` | what a human concluded (`Status`, `End Date`) |

**`active` decides what is ended, not `endedAt`** — that is the field the clients act on,
and the two disagree (collapsing an experiment in the Firebase console never stamps
Postgres). Dates come from whichever source has one, recorded per row so a later reader can
tell a stamp from a human record. A row with no date anywhere stays undated and is tranched
last; never invent one, because an invented date reads as measured.

The script **aborts** if AI Knowledge marks any candidate `Running`.

### 3. Confirm the exclusions before going further

**Feature flags are never archived.** A feature flag is stored as an experiment record with one
`active` letter, the "ended, winner" shape; archiving it removes the flag for everyone (for
`editEmailEnabled`, Edit email disappears). `reconcile.py` skips a record with `featureFlag: true`, or whose `notes` (or `description`) starts with "FEATURE FLAG", checking the catalogue's
row too, since the RTDB export may not carry `notes`, and prints `skipped.featureFlag N`. Without the
catalogue (`AURA_API_TOKEN` unset or unreadable) it refuses to build tranches.

Read the counts it prints. Two exclusion buckets, and they mean different things:

- **in current code** — out of scope for this skill entirely. That is
  `eng:sweep-ended-experiments`' job; these rows only become archivable after the code
  cleanup ships *and* those versions age out.
- **in old versions only** — not archivable yet, but the pool that grows the savings as old
  builds die out. Re-run the reconciliation periodically to reclaim it.

If a client repo is missing from disk the script warns and does not scan it. **Treat that as
a stop.** An unscanned client silently widens the candidate set in the one direction that
breaks users.

### 4. Check the five preconditions

All five, per tranche, re-checked rather than inherited:
[`references/preconditions.md`](references/preconditions.md).

Four are mechanical. The fifth — the **Bugsnag version-share floor** — is the only one no
script can evaluate and the only one that protects users on old builds. The matcher says
which versions *could* ask for a name; only session share says whether anyone still runs
one. Do not skip it.

### 5. Dry run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/archive-ended-experiments/scripts/archive.py \
  --tranches /tmp/tranches.json --year 2022 \
  --export /tmp/experiments-$(date +%F).json
```

Nothing is written without `--execute`; the CLI is a no-op unless `DO_UPDATE=true`, which
this script sets only in execute mode. The preflight re-verifies every id against the fresh
export and drops anything that went back to splitting.

### 6. Canary, then the tranche

```bash
# canary first -- the full run below reads this tranche's manifests and skips these ten
python3 .../archive.py --tranches /tmp/tranches.json --year 2022 \
  --export /tmp/experiments-$(date +%F).json --execute --limit 10
# verify, then the rest
python3 .../archive.py --tranches /tmp/tranches.json --year 2022 \
  --export /tmp/experiments-$(date +%F).json --execute
```

Oldest tranche first. Each removal fans a `child_removed` delta to every synced client and
fires the `activeExperiments` Cloud Function, so the loop is throttled (0.5s default).

A manifest is written on every execute run. **Keep it — rollback needs it.**

### 7. Verify, then soak

- `/experiments` key count drops by exactly the number moved.
- Spot-restore one id, confirm it reappears, re-archive it — exercise rollback rather than
  assuming it.
- Soak before the next tranche, watching Bugsnag and paywall conversion. Nothing will page.

### 8. Rollback if needed

```bash
python3 .../archive.py --manifest manifest-2022-<stamp>.json --rollback --execute
```

Exact inverse, one command per row.

## How "still referenced" is decided

Against each client's **current** tree, four passes — property read, registration array,
quoted literal in an experiment-touching file, and **constant resolution**. Each exists
because a real experiment slipped past the ones before it; the constant pass alone pulled
20 rows back out of an already-reviewed set, including the whole web `pricing*` family
behind `PRICING_*` constants.

Against **history**, an exact `git log -p` scan per client — never a sample. Sampling the
Aura app every 6 months for 4 years missed 490 rows that a shipped build could still name.
It costs ~31 seconds across all five clients to do properly.

Both, plus two known false-negative limits (computed names, single-hop constants):
[`references/matcher.md`](references/matcher.md). Read it before trusting a candidate set —
a bare token scan, the obvious first implementation, is wrong by roughly 2x because
`paywall`, `sleep`, `series` and `mood` are both experiment names and ordinary strings in
this app.

## Scope

- **In:** rows in `/experiments` with a single-letter `active` that no client can name.
- **Out:** live experiments; anything current code references; experiment *code*
  (`eng:sweep-ended-experiments`); `/activeExperiments` hygiene; ending a live experiment.
- Moves rows only. Never edits app code, never deletes, never touches `/users/*/experiments`.

## Related

- `eng:sweep-ended-experiments` — the code side. Run it first; its cleanups are what move
  rows from "in current code" into "archivable" over time.
- `eng:set-user-experiment`, `eng:remove-package-experiment` — per-user and per-package
  experiment operations, unrelated to node size.
