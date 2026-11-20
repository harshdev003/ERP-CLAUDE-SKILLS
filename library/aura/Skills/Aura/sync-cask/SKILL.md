---
name: sync-cask
description: Switch the app between the dev and prod Firebase/config env. Use when the user wants to build or release a specific env, says the app is pointing at the wrong Firebase project, or a fresh worktree can't build because src/cask is empty.
---

# Sync Cask (dev/prod env switching)

Wraps `scripts/sync-cask.sh` — the deterministic single source of truth for the
dev/prod env swap. Do NOT hand-symlink these files; the script does the linking
and the verification.

## Why this exists

The dev-vs-prod difference is decided entirely by 3 gitignored files in
`src/cask/`:

| File | Consumer |
| -- | -- |
| `GoogleService-Info.plist` | Xcode Copy Bundle Resources (`project.pbxproj` references `../src/cask/GoogleService-Info.plist`) |
| `meals.js` | Metro — imported by ~73 call sites as `./cask/meals.js` |
| `google-services.json` | the `com.google.gms.google-services` Gradle plugin, via a symlink at `android/app/google-services.json` |

Both envs live side by side, and those three paths are **symlinks** into the
active one. The env is stamped into every filename, so a file is unambiguous even
when you're looking at it alone:

```
src/cask/dev/{GoogleService-Info.dev.plist, google-services.dev.json, meals.dev.js}
src/cask/prod/{GoogleService-Info.prod.plist, google-services.prod.json, meals.prod.js}
src/cask/GoogleService-Info.plist     -> {dev|prod}/GoogleService-Info.{dev|prod}.plist
src/cask/google-services.json         -> {dev|prod}/google-services.{dev|prod}.json
src/cask/meals.js                     -> {dev|prod}/meals.{dev|prod}.js
```

Each native config is reached by its build through a path the switch never
moves, so an env change is only ever a flip inside `src/cask`:

```
iOS      project.pbxproj references ../src/cask/GoogleService-Info.plist
Android  android/app/google-services.json -> ../../src/cask/google-services.json
```

The Android link exists because the gms plugin resolves `google-services.json` by
convention relative to the module and offers no way to point it elsewhere. That
path is gitignored, so `switch` maintains the link — which keeps the script the
single writer of both native configs. See [The Android
config](#the-android-config).

**`src/cask/{dev,prod}/` is the source of truth for a build.** Nothing here
reaches out to a network or an external drive — switching is a symlink flip, and
a cache missing a build-critical file is an error rather than something to go
fetch. Those directories are maintained by hand (or seeded from another
checkout).

## Commands

Always show the user the output.

**Check current state (start here):**
```bash
bash scripts/sync-cask.sh status
```
Reports the active env by **reading the symlink target**, then what each env holds
— `meals.js`'s declared `mode` and each file's Firebase project id — flagging a
missing build-critical file in red.

**Switch env:**
```bash
bash scripts/sync-cask.sh switch prod
bash scripts/sync-cask.sh prod          # same thing, shorthand
```
Repoints the three symlinks at `src/cask/prod/`. Takes about a second.

**Seed a fresh worktree:**
```bash
bash scripts/sync-cask.sh seed --apply     # dry run first: drop --apply
bash scripts/sync-cask.sh switch prod
```
`src/cask` is gitignored, so a new worktree has no config and the iOS build dies
on `Build input file cannot be found: src/cask/GoogleService-Info.plist`. Run
from a worktree, `seed` copies both env dirs out of the checkout that worktree
was created from — it finds that itself via `git rev-parse --git-common-dir`, so
no path is needed. `--from <path>` overrides it (any relative or absolute path);
run in the main checkout with no `--from`, it refuses rather than guessing.

## The checks that matter

Byte-comparing files proves nothing about *which env* the bytes are — a prod dir
holding a DEV `meals.js` is the failure that ships dev Firebase config to
production. So:

- **`switch` refuses** if `src/cask/<env>/meals.<env>.js` declares a `mode` other
  than that env's, and fails **before touching any link** if a build-critical
  file is absent. A half-switched `src/cask` is worse than an unswitched one.
- **`status` and `switch` warn** if both env dirs declare the same Firebase
  project — one of them holds the wrong env's plist.
- **`switch` refuses** when a canonical path holds a real file instead of a
  symlink. That's a leftover from the pre-symlink layout, and linking would
  delete it — possibly the only copy of that config on the machine.
- **`switch` refuses** if `meals.<env>.js` is missing any key that
  `src/cask/meals.sample.js` declares, or carries one with an empty value — an
  empty value counts as absent. The other checks catch the *wrong* env; this one
  catches an *incomplete* one, which nothing downstream would: a missing key
  doesn't fail the build, it ships a feature that quietly does nothing. `status`
  reports the same for both envs, so a half-filled cache shows up before someone
  switches to it. A `meals.<env>.js` that doesn't parse is refused too — Metro
  imports that file, so the build would fail on it anyway.
- **`status` and `switch` warn** about a key in `meals.<env>.js` that the sample
  doesn't declare. Not fatal — the app already reads it — but it means the
  same-commit mirroring rule was missed. Add it to the sample.
- **`switch` refuses** if `google-services.<env>.json` names a different Firebase
  project than `GoogleService-Info.<env>.plist` beside it, or carries no client
  for the build's `applicationId`. Unlike `meals.js`, a `google-services.json`
  has no dev/prod marker of its own — but the two native configs for one env
  describe one Firebase project, so a disagreement means one of them is the wrong
  env's file. This is the check that makes a wrong-env Android config fail the way
  a wrong-env `meals.js` does. `status` reports it for both envs.
- Every write **unlinks its destination first**, so a copy can never travel
  through a symlink and overwrite the other env's file.

Key validation needs `node` on `PATH` and a committed sample. Without either it
warns and proceeds rather than blocking — a machine that can't run `node` can't
build the app either, so there is nothing to protect at that point.

## Workflow

1. Run `status` and show it.
2. `switch <env>`. That's the whole job.
3. Remind them to clean caches before building: `bundle exec fastlane ios
   cask_<env>` (switch + clean Metro/Watchman/DerivedData). Stale DerivedData is
   the usual cause of a wrong-env build even after a correct switch.

Every developer is expected to have both env dirs populated. If a file is
missing the script names it and stops — put the real file in `src/cask/<env>/`
(from whoever owns it, or `seed` from a checkout that has it) rather than working
around the error.

## The Android config

`switch` maintains `android/app/google-services.json` as a symlink into
`src/cask`, so an env change switches Android's native Firebase config the way it
already switches iOS'. Until PROD-4870 it did not: the gms plugin read an
unmanaged file that on every machine named **production**, so an Android dev build
authenticated against production Firebase Auth and registered for production FCM
while its REST calls (`code.firebaseURL` from `meals.js`) went to dev.

Three consequences worth knowing:

**A missing Android config does not abort a switch.** `switch` is what the iOS
release lane runs, and an iOS archive never reads this file — so a cache without
one is reported, not refused. What `switch` will never do is leave the link
dangling: with nothing to point at, it removes the link entirely, because a
dangling link reaches the gms plugin as a plain "file missing" naming a path
nobody maintains by hand. An **Android** build in that state fails; iOS is fine.

**An unmanaged real file at `android/app/google-services.json` is reported, never
overwritten.** The path is gitignored and nothing has ever managed it, so a real
file there may be the only copy of that config on the machine. `switch` prints
the `mv` into the env dir it belongs to and leaves the file alone. Run that, then
switch again.

**The Android build asserts this itself.** `android/app/build.gradle` fails at
configure time if the file the plugin will read is not the cask's, because the
plugin can't: it validates the package name and nothing else, so a config for the
wrong Firebase *project* builds perfectly. The split is on the project, not on
bytes: a copy naming the **same** project as the active env only warns — correct
today, stale after the next switch — because two exports of one project differ
byte-for-byte all the time (a newer download picks up an added OAuth client or
SHA-1) and none of that makes the build wrong.

## Guardrails

- Only ever touches the 3 canonical symlinks, the `android/app/google-services.json`
  link, and the two env dirs. It never edits, moves, or deletes a config file you
  put there.
- Never runs a build (`npm run ios`, `xcodebuild`). The user builds in Xcode.
- Never commits these files — `src/cask/` is gitignored; that's intentional. The two
  **samples** are the exception and *are* committed: `src/cask/meals.sample.js` and
  `src/cask/GoogleService-Info.sample.plist`, every value redacted as `****`.
- If a command exits non-zero, STOP — do not proceed to any build step.

## Adding a new cask key

`src/cask/` is gitignored, so the sample is the only record in the repo of what a cask
must contain. **A new key in `meals.js` must be mirrored into `src/cask/meals.sample.js`
in the same commit** — same key, alphabetical position, value redacted as `****`. Never
the real value: the sample is committed, so a real value there is a committed secret.
Same rule for `GoogleService-Info.sample.plist`. See `.claude/rules/security.md`.

## Relationship to other tooling

- `scripts/sync-cask.sh` — the script this skill runs. It resolves paths relative
  to itself, so it always acts on its own checkout; run a worktree's copy to set
  up that worktree.
- `ios/fastlane/Fastfile` — `switch_cask` is step 2 of the release lane and what
  the `cask_dev` / `cask_prod` lanes run, so **a release needs no drive and no
  network for its config**. It calls this same script, so fastlane and this skill
  can never disagree on the layout or the verification.
- CI never runs this script — `src/cask/` is gitignored, so a CI checkout has no
  env dirs to switch to. `ci_release_prod` restores the two build-critical files
  from base64 env vars and the lane's `verify_cask!` asserts the `mode` instead.
- Global `switch-aura-env` skill — **overlaps this one and should be retired.** It
  hardcodes stale paths, but it also does the heavier cache clean (Metro,
  Watchman, Pods). Until it's folded in, use this skill for the switch and borrow
  its cache-clean steps. Two skills covering one job is a known wart, not a design.
