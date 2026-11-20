---
name: release-feature-branch-ios
description: Build the current feature branch of the Aurie app and upload it to TestFlight so the team can QA it — set the version, archive and upload via fastlane, then commit the bump to the branch. No PR, no tag, no GitHub release. Use when the user says "get this branch on TestFlight", "build this branch for QA", "upload a test build", "push a build from this branch", or wants a build of work that has not reached main. For an actual release, use `release-ios` instead.
---

# Release feature branch iOS

## Goal

Get **the branch you are on** in front of QA, and nothing else.

This is the sibling of [`release-ios`](../release-ios/SKILL.md), which is the only
way a real release is cut. Keep them apart:

| | `release-ios` | `release-feature-branch-ios` |
|---|---|---|
| Builds | `origin/main` | your local branch, as it is |
| Version baseline | `origin/main` | the branch itself |
| After upload | PR → squash-merge → tag → GitHub release | one commit on the branch |
| Plan mode | yes | no |

The split exists because the git half of a release is **wrong** for a feature
branch, not merely unnecessary. Tagging unmerged work and publishing a GitHub
release for it claims a release that never happened, and the tag then sits in the
same namespace the real releases use — which in this repo already holds 158 tags
that mean "this shipped".

## Usage

```bash
python3 .claude/skills/release-feature-branch-ios/scripts/release_feature_branch_ios.py \
  --env prod --version 1.30.0 --build 1
```

| Flag | |
|---|---|
| `--env dev\|prod` | Required. Passed to the `release` lane as `dev:true` / omitted, which is what `make env-<env> release` points at that env's Firebase + API |
| `--version` / `--level` / `--build` | The version to build. `--level` bumps relative to **the branch**, not main |
| `--branch` | A **local** branch to check out first. Never resolves `origin/<name>` |
| `--changelog` | TestFlight "What to Test". Defaults to branch + commit + env |
| `--allow-main` | Permit building from `main`. Prefer `release-ios` |

The archive runs well past the Bash tool's foreground limit, so **launch it in the
background and poll the log**:

```bash
python3 .claude/skills/release-feature-branch-ios/scripts/release_feature_branch_ios.py --env prod --level build > "/tmp/branch-build-$(basename "$PWD").log" 2>&1
```

## Ordering — build first, commit after

```
pre-flight → fastlane release (env swap → version → archive → upload → dSYM) → COMMIT
             └── on failure: project.yml restored, no commit ever created
```

**The bump is committed after a successful upload, not before.** The `release`
lane writes the version into `project.yml` itself as its step 1, so there is no
bump to commit until the build has run — and a failed archive then costs nothing
at all: `project.yml` and `Package.resolved` are restored and the branch is
exactly as you left it. This is the same argument `release-ios` makes for
building before it opens its PR.

> **This is the one place this skill deliberately departs from Aura's
> `release-feature-branch-ios`,** which commits the bump *before* the archive and
> `git reset --hard`s on failure. That ordering is forced there by
> `ensure_git_status_clean` in Aura's Fastfile, which refuses to run against a
> dirty tree. This Fastfile has no such guard — so the constraint that made
> commit-first necessary does not exist here, and the safer order is available.

Nothing is ever pushed. The bump commit stays local; push it yourself if the team
needs it on the remote.

### What happens if the tree is dirty after the build

Only `project.yml` and `Package.resolved` go into the commit. Anything else the
build left is **reported and left in the tree**, not committed and not fatal — the
binary is already on TestFlight by then, and failing there would strand a
successful upload with no record of it. `release-ios` raises in the equivalent
spot and is right to: its check runs *before* the archive, where stopping is free.

`Package.resolved` is in the commit rather than beside it because `make proj`
resolves Swift packages at the head of every build, and the resulting pins are
what this binary was actually built from. It is tracked despite living inside the
gitignored `.xcodeproj`, via a `!**/Package.resolved` un-ignore.

## Pre-flight

All of it before anything is written, so a miss costs seconds rather than a
20–45 minute archive:

- **On a branch, not `main`** — `main` belongs to `release-ios` (`--allow-main` overrides)
- **Clean tree** — the build writes `project.yml` and the commit picks it up, so
  pre-existing changes would be swept in; the failure path also restores
  `project.yml`, which would discard an edit of your own
- **`xcodegen` on PATH** — the `.xcodeproj` is generated from `project.yml`, so
  `make proj` runs at the head of the build (`make install-xcodegen`)
- **`bundle exec fastlane` runs in `fastlane/`** — the Gemfile lives there, not at
  the repo root, and gems must land in the Ruby named by `.ruby-version`
- **The env's config files exist** — `Secrets.<env>.xcconfig` and
  `GoogleService-Info_<Env>.plist`. `make env-<env> release` is step 0 of the lane
  and aborts without them. Both destinations it writes are gitignored, so the swap
  cannot dirty the tree
- **A coherent signing route** — all three `APP_STORE_CONNECT_*` values, or none.
  See [Two routes](#two-routes-same-rule-as-release-ios); a partial configuration
  is refused, and on the API route the certificate and the token are both verified
- **Neither tag form for this version+build exists** — see below

### The tag check reads both formats, and asks origin

`release-ios` cuts bare `1.30.0-1`; every tag before it carries a `v` prefix, and
there are 158 of them. Either form means that version+build already shipped, so
both are checked — and origin is asked directly with `git ls-remote` rather than
trusted from a local tag list that can be months stale on a feature branch. If
origin is unreachable that is a warning, not a block: the network is not a
prerequisite for a local build, and App Store Connect rejects a duplicate build
number anyway.

### In a git worktree

The gitignored config files are absent — `git worktree add` cannot bring them
across — and pre-flight detects the worktree and says so. Copy them from the main
checkout per [`.claude/rules/config.md`](../../rules/config.md); the plist must be
a **real copy, not a symlink**, or xcodebuild omits it from the bundle.

`fastlane/.env` is among them, and it is the one that changes the route rather than
breaking the build: without it all three App Store Connect values read as unset and
the build signs and uploads through Xcode.app's Apple ID instead of the API key the
main checkout is configured for — failing at the upload, after the archive. This
shares `release-ios`'s pre-flight, so the same check **blocks** here: linked worktree,
no `fastlane/.env`, main checkout has all three. A main checkout with no `.env` either
only warns.

## Two routes, same rule as `release-ios`

This runs the same `release` lane, so it takes the same fork on the same three
variables — see
[`release-ios` § Two routes](../release-ios/SKILL.md#two-routes-chosen-by-fastlaneenv)
for the mechanism and why the no-key route needs no certificate.

| `fastlane/.env` | Signing | Upload |
|---|---|---|
| All three `APP_STORE_CONNECT_*` set | API key; needs a distribution cert an installed profile contains | `upload_to_testflight` |
| None set | the Apple ID **Xcode.app** is signed into; **no certificate needed** | second `xcodebuild -exportArchive` |

Pre-flight delegates to `release-ios`'s `check_worktree_asc_env` /
`check_asc_partial` / `check_asc_token` / `check_signing`, so the two skills cannot
drift apart on a rule the lane enforces anyway. If the cert-in-profile check fires on the API route, the cheapest fix is
usually to unset the three values and take the Xcode route — see
[`release-ios` § Why "cert in a profile"](../release-ios/SKILL.md#why-cert-in-a-profile-not-just-cert-in-the-keychain).

## The TestFlight changelog

The lane falls back to `CHANGELOG.md`, which never carries an entry for a branch
version — so without help every branch build arrives in TestFlight with a blank
"What to Test" and nothing distinguishing it from the others in the list. This
skill passes one naming the version, env, branch and short SHA. Override it with
`--changelog`.

**The Xcode signing route drops it.** That route uploads through a second
`xcodebuild -exportArchive` rather than `upload_to_testflight`, and gives up the
changelog, the tester distribution and the build-processing wait along with it.
The build arrives either way; the script says so before it starts.

## The version trap

The baseline is **the branch**, not `main` — which is the point, but it also means
the branch can be behind. A feature branch cut before a release does not carry
that release's bump, so `--level build` there can propose a version *below* what
already shipped, and App Store Connect rejects it.

The skill warns when the target version trails `main`'s. It does not block: a
branch may legitimately be on its own version line, which is usually the right
answer for long-running work. Giving it its own line rather than a build bump on
`main`'s version also keeps its Bugsnag crash reports from merging with the
shipped release's.

## The version lives in `project.yml`, once per target

The `.xcodeproj` is generated and gitignored, so `project.yml` is the source of
truth — and it carries `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` for each
target: `AuraConsumerApp`, `AurieWidget` and `IterableNotificationServiceExtension`.
All must agree or App Store Connect rejects the upload. The read is delegated to
`release-ios`, so it comes with the assertion that they match rather than quietly
picking the app's.

## Prerequisites

Same local setup as `release-ios`:

- Gems in the Ruby named by `.ruby-version` — `bundle install` **in `fastlane/`**
- `xcodegen` — `make install-xcodegen`
- `AuraConsumerApp/Config/Secrets.<env>.xcconfig` and
  `GoogleService-Info_<Env>.plist` present for the env you are building
- **Xcode.app signed into team `9C8CA87JCB`** — on its own, the whole credential
  setup, via the no-key route above. A personal Apple ID makes xcodebuild reach
  for a development cert and fail during build planning, and pre-flight cannot
  detect that case
- **Optionally** all three App Store Connect values in `fastlane/.env` plus an
  `Apple Distribution` certificate the installed profiles contain. Only needed for
  the API route; a partial configuration is refused either way.
  **Do not expect an Xcode archive to create a certificate for you** — with the
  cloud-managed distribution role most of this team has, Apple keeps the private
  key and the keychain stays empty

## Exit codes

| Exit | Meaning |
|---|---|
| `0` | Uploaded (the bump commit may be skipped if the branch already declared that version) |
| `2` | Pre-flight refused, or the build failed — tree restored, nothing committed |
| `130` | Interrupted — tree restored, nothing committed |

There is no resume. Unlike `release-ios`, nothing here is half-done: either the
binary uploaded and the bump is committed, or the branch is untouched.

An interrupt restores the tree for the same reason a failed build does: the lane
writes the version into `project.yml` as its step 1, long before the archive
finishes, so Ctrl-C mid-build would otherwise strand an unshipped bump — and that
dirty tree would then block the `--branch` restore, leaving you on a branch you
did not ask to be on. The one case it cannot resolve is an interrupt *after* the
upload completed but before the commit: the bump is discarded and the build is on
TestFlight anyway, so the script says so and tells you to check TestFlight and
re-run with the explicit `--version`/`--build`.

## Don't

- **Don't use this to cut a release.** It produces no tag and no GitHub release,
  so nothing records what shipped.
- **Don't point it at `main` out of habit** — `release-ios` exists for that and
  does the bookkeeping this one deliberately skips.
- **Don't push the bump commit to a shared branch** without telling whoever owns it.
- **Don't call `bundle exec fastlane release` by hand** to do the same thing. It
  prompts interactively (hanging a backgrounded run), leaves the bump uncommitted,
  and skips every pre-flight check above — including the one that stops you
  re-using a build number App Store Connect will reject after a 30-minute archive.
- **Don't report the build as ready for QA** on the strength of a green run. App
  Store Connect still has to process it; check TestFlight. The Bugsnag dSYM upload
  now fails the build rather than warning, so a green run means symbols uploaded —
  but the verification step needs `BUGSNAG_TOKEN` set to confirm they processed.

## Related

- [`release-ios`](../release-ios/SKILL.md) — the real release: PR, tag, GitHub release
- [`.claude/rules/config.md`](../../rules/config.md) — gitignored config, and the
  worktree restore procedure
- [`fastlane/.env.example`](../../../fastlane/.env.example) — the credentials to fill in
