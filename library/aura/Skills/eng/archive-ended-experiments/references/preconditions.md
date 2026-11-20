# Preconditions — all five, re-checked per tranche

None of these are inherited from a previous tranche. The node moves, releases ship, and a
manifest is evidence about the node as it was when the manifest was built.

## 1. Re-run the reconciliation against a fresh export

```bash
aura firebaseExport /tmp/experiments-$(date +%F).json /experiments
python3 scripts/reconcile.py --export /tmp/experiments-$(date +%F).json
```

`reconcile.py` aborts if AI Knowledge marks any candidate `Running` or `In flight`.

## 2. Every id still ends with a single-letter `active`

`archive.py` preflights this against the fresh export and drops anything that went back to
splitting. Do not bypass it — an experiment that was restarted looks identical in an old
manifest.

## 3. Zero intersection with current code

All four matcher passes, across every client in `CLIENTS`. If a repo in that map is missing
from disk, `reconcile.py` warns and **does not scan it** — a missing clone silently widens
the candidate set, so treat that warning as a stop, not a note.

## 4. The Bugsnag version-share floor

The one precondition no script can evaluate, and the only one protecting users on old builds.

For every app version that ever named an id in the tranche, the share of sessions still on
that version must be under an agreed floor. Pull the release/session distribution for the
Aura RN app from Bugsnag (`eng:analyze-bugsnag-releases`) and compare against the checkpoint
at which each id was last referenced.

The matcher tells you which versions *could* ask for a name. Only this tells you whether
anyone still runs one. Skipping it turns the whole exercise into a guess, and the failure is
silent.

## 5. `/backup` is not client-readable and nothing listens on it

Two separate checks:

- RTDB security rules must not grant clients read on `/backup`. If they do, the bytes move
  but never leave — the node shrinks and nothing gets cheaper.
- No client subscribes to `/backup`. Grep the clients for the path.

## Blast radius, for sizing the throttle

Each removal:

- fans a `child_removed` delta to every client holding `keepSynced(true)` on `/experiments`;
- fires `activeExperiments` (`onValueWritten` at `/experiments/{experimentId}`), which for an
  ended row is a no-op `remove()` on an already-absent node — harmless, but one invocation
  per id.

Default throttle is 0.5s. Use `--limit` for a canary batch before the full tranche.

## Verify, then soak

- `/experiments` key count drops by exactly the number moved.
- Spot-restore one id, confirm it reappears, re-archive it. This exercises rollback for real
  rather than assuming it works.
- Soak before the next tranche: watch Bugsnag and paywall conversion. The failure mode is a
  metric moving, not an error, so nothing will page.

## Rollback

```bash
python3 scripts/archive.py --manifest manifest-<year>-<stamp>.json --rollback --execute
```

Per-id and exact. Keep the manifest — without it there is no record of what moved.
