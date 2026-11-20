---
name: reclaim-disk
description: >
  Reports and reclaims the disk an iOS build and test loop leaves behind on a
  developer Mac: simulator clones xcodebuild test makes and never deletes, unused
  simulator runtimes, simulators whose worktree is gone, recordings inside
  shut-down simulators, Xcode DerivedData whose workspace is gone, result bundles,
  per-worktree caches, old sim-test sessions and merged worktrees, and lists dev
  caches (npm, uv, nvm, Gradle, Docker) with the command that clears each. Reports
  by default, deletes nothing without --confirm, and never takes what a booted
  device, existing workspace or running xcodebuild uses. check-space pre-flights a
  long job against a free-space floor. Trigger on "reclaim disk", "the disk
  is full", "clean up my disk", "clean up Xcode junk", "what is eating my disk",
  "XCTestDevices", "clean DerivedData", "remove old simulator runtimes", or before
  a long capture or test sweep. Do NOT use to clean a CI runner (ephemeral
  already), to remove source, or to shut down another session's simulator.
metadata:
  version: "1.2.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
---

# Reclaim disk

## Why this exists

On 2026-09-29 a Mac running the Aurie sim-test harness reached **235 MB free on a
926 GB volume**. That is worse than slow: the Claude Code Bash tool writes its
output file before it executes, so at that point the agent could not run *any*
command — including the one that would have cleaned up. A three-sky screenshot
harvest died with it.

A manual sweep recovered **233 GB**, almost all of it from places no script touched:

| Source | Reclaimed | What it is |
|---|---|---|
| `~/Library/Developer/XCTestDevices` | **124 GB** (96 clones) | `xcodebuild test` clones a simulator per run and never deletes it |
| Simulator devices | ~88 GB | one per sim-test session, kept forever |
| Global Xcode DerivedData | ~48 GB | 510 dirs; 10 belonged to worktrees that no longer existed |
| `iOS DeviceSupport` | 11 GB | symbol caches, regenerated on demand |
| Merged worktrees + their `DerivedData-CLI` | ~5 GB | branches already in main |

**`XCTestDevices` is the one nobody knows about.** It is not `CoreSimulator/Devices`
and it is not DerivedData, so no existing cleanup script goes near it.

A second Mac, on 2026-10-02, was the other half of the lesson: this tool reported
**nothing reclaimable** and a hand sweep then freed ~190 GB.

| Source | Reclaimed | What it is |
|---|---|---|
| Simulator runtimes | **112 GB** + 55 GB dyld cache | 20 installed (every iOS, watchOS and visionOS Xcode ever pulled), 2 in use |
| Simulators of removed worktrees | 19 GB | the per-session device outlives its worktree |
| Recordings inside simulators | 6 GB | QuickTime movies in shut-down devices |
| Dev caches | ~45 GB | uv, npm, 29 Node versions, Gradle, CocoaPods, Docker, app updaters |

A report that says "nothing" on a disk that is two-thirds tooling is a wrong answer,
so every one of those is now in it.

## Use it

```bash
S=${CLAUDE_PLUGIN_ROOT}/skills/reclaim-disk/scripts/reclaim_disk.py

python3 $S report  --repo ~/path/to/checkout          # writes nothing
python3 $S reclaim --repo ~/path/to/checkout          # dry run: says what it would take
python3 $S reclaim --repo ~/path/to/checkout --confirm
```

**`$CLAUDE_PLUGIN_ROOT` is only set while Claude Code runs a plugin's own script.**
In an ordinary shell call — which is most of them, and all of them when the disk is
full and nothing else works — it reads empty and the command becomes
`python3 /skills/...`. Resolve it from the install cache instead, version-agnostic
so it survives every plugin bump:

```bash
S=$(ls -d ~/.claude/plugins/cache/aura-tools/eng/*/skills/reclaim-disk/scripts/reclaim_disk.py \
    | sort -V | tail -1)
```

`--repo` is repeatable and optional — with none, only the machine-wide sources are
scanned, which is every Xcode project on the Mac at once. `report` is the default
verb, so `reclaim_disk.py --repo X` is a report.

**Show the report and get a yes before `--confirm`.** That is the whole protocol;
there is nothing else an agent adds here, which is why this is a script you can
also type yourself. That matters: in the failure mode this exists for, the agent
cannot run at all.

## What it takes, and what it holds back

Taken by default — each has a liveness test that is evidence, not a guess:

| Source | Taken when |
|---|---|
| `xctest-clones` | the clone's own state from CoreSimulator is not `Booted` |
| `derived-data` | its `info.plist` `WorkspacePath` no longer exists |
| `result-bundles` | no `xcodebuild` is running in that worktree, and it is over an hour old |

Held back. There are **two** kinds, and they are not the same question:

- **`!` — a policy hold.** This needs a human to decide. `--only <source>` is how
  that human says yes.
- **`x` — in use right now.** Evidence, not preference: a live `xcodebuild` in the
  same worktree, a simulator that is booting, a directory written minutes ago. **No
  flag takes it**, because the answer is "wait", not "are you sure". `--only` clears
  a policy hold and nothing else — otherwise `--only result-bundles --confirm` would
  delete the output of a build in progress.

| Source | Why it waits for a human |
|---|---|
| `derived-data-cli` | every one costs a rebuild, so `--only` is required. One holding a built `.app` costs a *cold* rebuild and needs `--include-built` **as well** — `--no-build` reinstalls from it in seconds, which on the day every simulator had to be recreated was worth far more than the 5 GB |
| `sim-sessions` | a session is the only place a capture's screenshot and dump exist; `--days` sets the keep window (7) |
| `sim-runtimes` | a runtime is a multi-GB re-download. Offered only with no simulator on it, unused for `--runtime-days` (30), and not the newest of a platform anyone uses — Xcode builds against that one. Its dyld cache goes with it. Deletion finishes in the background, so free space lags the report by a minute or two |
| `orphan-simulators` | the sim-test harness labels each device with its worktree's directory name; a label no `--repo` worktree owns is offered. A custom `SIM_SESSION_NAME` or a session from a repo you did not scan looks identical from here, so the report shows the last-use date for you to judge. Needs at least one `--repo`; pass every checkout whose sessions share the Mac |
| `sim-attachments` | screen recordings and test attachments in a shut-down simulator. Emptying the folder keeps the device and its installed app, unlike `simctl erase` |
| `device-support` | regenerated when a device is next attached — which is while you are trying to debug something |
| `simulators` | only ever offers devices whose *runtime* is unavailable |

Reported, never removed — **`-`** in the report, with the command that clears each on
its line. These are somebody's tool rather than build junk, so the tool's own command
is the right way to clear them:

| Source | What it lists |
|---|---|
| `merged-worktrees` | `git worktree remove` is the only correct way, and the worktree belongs to whoever is in it |
| `dev-caches` | npm, uv, pip, Homebrew, Gradle, CocoaPods, nvm's cache, Puppeteer and Codex downloads, app updater (`*.ShipIt`) leftovers, Docker's VM disk at its real size, live DerivedData, and Node versions that are neither nvm's default nor pinned by an `.nvmrc` within four levels of `~` |

## Three things it gets right that the obvious version does not

Each was found by running this against a live, busy Mac, and each had already
produced a wrong answer before it was fixed.

**Deleting a simulator clone means `simctl delete`, not `rm -rf`.** The clones live
in their own device *set*, and `device_set.plist` registers every one. Removing the
directories by hand reclaims the bytes and leaves CoreSimulator naming devices that
no longer exist — this machine was left with **27 such ghosts** by exactly that
sweep. The tool reports them (`registered but no directory`) and deregisters them
properly.

**`git branch --merged` is the wrong test for a finished worktree**, in both
directions. It misses a squash-merged branch entirely — the branch tip is a commit
that never reached main, so `--merged` says no while every line of it is in main —
and it fires on a branch that has no commits at all, which is somebody's freshly
created workspace. Proven on a scratch repo: after a squash merge, `--merged`
lists only `main`. The test used instead is *the branch has commits of its own*
**and** *`git diff origin/main <branch>` is empty* **and** *the tree is clean*. All
three are load-bearing: a worktree here was merged but held five uncommitted files
including a new test.

**A build's working directory is not its containment boundary.** The paths
`xcodebuild` is given are relative — `-derivedDataPath DerivedData-CLI` resolves
against the process's own cwd — so matching on the argument's basename calls every
worktree's cache live the moment any one of them builds. And plain path containment
is worse: linked worktrees sit at `<repo>/.claude/worktrees/<name>`, *inside* the
main checkout, so one build in the main repo claims every worktree on the machine.
The boundary used is git's own: a candidate is in use when `git rev-parse
--show-toplevel` for it matches that of a live build's cwd.

## Pre-flight before a long job

```bash
python3 $S check-space --need-gb 60 --repo ~/path/to/checkout
```

Exit `0` when there is room, `2` when there is not — and on `2` it prints what is
reclaimable instead of just refusing. One `df`, and it turns a dead session into a
warning: a three-sky parallel harvest exhausted 44 GB of headroom, which is where
the 60 GB default comes from.

Wire it into anything that runs for an hour. In this fleet that is
`screenshot-harvest` and `aurie-screens-weekly`.

## Exit codes

| Exit | Meaning |
|---|---|
| `0` | reported, or reclaimed cleanly |
| `1` | something could not be removed — the path is named on stderr |
| `2` | `check-space` only: under the floor |

A failed `simctl delete` counts as a failure and reaches this exit code. That needs
saying because the obvious implementation loses it: a helper that reports failure as
an empty string cannot be told apart from a command that succeeded quietly, so the
delete is counted as reclaimed and the sweep exits 0 — on a wedged CoreSimulator,
which is exactly the state a nearly full disk produces.

## Don't

- **Don't `rm -rf ~/Library/Developer/XCTestDevices`.** It reclaims the bytes and
  corrupts the device set's registry. Use this tool, or `simctl --set <path> delete`.
- **Don't delete a simulator by name alone.** Several sessions share the Mac and the
  naming convention is `"<model> (<label>)"`; the one you are about to remove may be
  driving someone else's test. `orphan-simulators` offers one only when no scanned
  worktree owns its label, and holds it for you to confirm.
- **Don't `simctl runtime delete` the runtime your simulators run on.** Every device
  on it goes unavailable. `sim-runtimes` never offers one that has a device.
- **Don't take a `DerivedData-CLI` holding a built `.app`** without knowing you are
  buying a ~20 minute rebuild.
- **Don't skip the report.** `--confirm` on an unread report is how a cleanup
  becomes an incident.
