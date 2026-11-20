---
name: release-feature-branch-ios
description: Build the current feature branch and upload it to TestFlight so the team can QA it — set the version, reinstall dependencies, archive and upload via fastlane, then commit the bump to the branch. No PR, no tag, no GitHub release. Use when the user says "get this branch on TestFlight", "build this branch for QA", "upload a test build", "push a build from this branch", or wants a build of work that has not reached master. For an actual release, use `release-ios` instead.
---

# Release feature branch iOS

## Goal

Get **the branch you are on** in front of QA, and nothing else.

This is the sibling of [`release-ios`](../release-ios/SKILL.md), which is the only
way a real release is cut. Keep them apart:

| | `release-ios` | `release-feature-branch-ios` |
|---|---|---|
| Builds | `origin/master` | your local branch, as it is |
| Version baseline | `origin/master` | the branch itself |
| After upload | PR → squash-merge → tag → GitHub release | one commit on the branch |
| Plan mode | yes | no |

The split exists because the git half of a release is **wrong** for a feature
branch, not merely unnecessary. Tagging unmerged work and publishing a GitHub
release for it claims a release that never happened, and the tag then sits in the
same namespace the real releases use.

Agreed with rohan in
[#eng](https://auramobile.slack.com/archives/C0232AD4CS1/p1787842586948929)
after `--base-branch` on `release-ios` turned out to be the wrong shape: releases
should always come from master, and a branch build is a different workflow rather
than a flag on that one.

## Usage

```bash
python3 .claude/skills/release-feature-branch-ios/scripts/release_feature_branch_ios.py \
  --env prod --version 3.21.0 --build 1
```

| Flag | |
|---|---|
| `--env dev\|prod` | Required. Picks `release_dev` / `release_prod`, which is what points `src/cask` at that env's Firebase config |
| `--version` / `--level` / `--build` | The version to build. `--level` bumps relative to **the branch**, not master |
| `--allow-trial-fonts` | Passes `allow_trial_fonts:true`. Trial fonts are not licensed for distribution — internal builds only |
| `--skip-install` | Skip `npm ci` / `pod install` |
| `--allow-master` | Permit building from master. Prefer `release-ios` |

## Ordering

```
pre-flight → npm ci + pod install → set_version → COMMIT → archive + upload
                                                     └── rolled back if the upload fails
```

**The bump is committed before the archive, not after.** fastlane's
`ensure_git_status_clean` will not run against a dirty tree, so it cannot sit
uncommitted while the build runs. Committing first and resetting on failure lands
in the same place as "commit only after a successful upload" without fighting the
lane: a failed build leaves the branch exactly as it was.

Nothing is ever pushed. The bump commit stays local; push it yourself if the team
needs it on the remote.

## Pre-flight

All of it before anything is written, so a miss costs seconds rather than a
20-45 minute archive:

- **On a branch, not master** — master belongs to `release-ios` (`--allow-master` overrides)
- **Clean tree** — the bump is committed, so pre-existing changes would be swept into that commit
- **`npm` on PATH**, and **`bundle exec fastlane` runs in `ios/`**
- **A coherent signing route** — all three `APP_STORE_CONNECT_*` values, or none. See
  [Two routes](#two-routes-same-rule-as-release-ios) below; a partial configuration is
  refused, and on the API route the certificate and the key are both verified
- **The tag for this version+build does not already exist** — App Store Connect rejects a re-used build number

## Two routes, same rule as `release-ios`

This runs the same `deploy` lane, so it takes the same fork on the same three
variables — see
[`release-ios` § Two routes](../release-ios/SKILL.md#two-routes-chosen-by-iosfastlaneenv)
for the mechanism and why the no-key route needs no certificate.

| `ios/fastlane/.env` | Signing | Upload |
|---|---|---|
| All three `APP_STORE_CONNECT_*` set | API key; needs a distribution cert an installed profile contains | `upload_to_testflight` |
| None set | the Apple ID **Xcode.app** is signed into; **no certificate needed** | second `xcodebuild -exportArchive` |

Pre-flight delegates to `release-ios`'s `check_asc_partial` / `check_asc_token` /
`check_signing`, so the two skills cannot drift apart on a rule the lane enforces
anyway.

Two checks were removed here when the Xcode route landed, both actively wrong:

- **"No App Store Connect credentials"** treated absent credentials as a missing
  prerequisite and told you to fill in `.env`. Absence is a *route*, and blocking it
  locked out exactly the cloud-managed accounts that cannot hold a certificate. It
  also only fired when `ios/fastlane/.env` was **absent**, so a `.env` with every
  value commented out sailed straight past it.
- **The keychain check ran only under the old `--skip-upload` flag** — demanding a
  certificate when *not* uploading and never checking when uploading, though the
  archive needs signing either way. Its advice ("one Xcode archive with automatic
  signing creates it") is also false for a cloud-managed account: Apple keeps the
  private key and the keychain stays empty.

`--skip-upload` itself is gone. It existed to archive without an App Store Connect
key so you could send the `.ipa` from Xcode Organizer by hand — which is exactly what
the no-key route now does for you, symbols and all. Keeping it would have left two
ways to do the same thing, one of them worse: it skipped the Bugsnag dSYM and
source-map upload, so builds shipped that way arrived unsymbolicated. To archive
without uploading, call the lane directly:
`cd ios && bundle exec fastlane ios release_prod skip_testflight:true`.

## The version trap

The baseline is **the branch**, not master — which is the point, but it also means
the branch can be behind. A feature branch cut before a release does not carry that
release's bump, so `--level build` there can propose a version *below* what already
shipped, and App Store Connect rejects it.

The skill warns when the target version trails master's. It does not block: a branch
may legitimately be on its own version line, which is usually the right answer.
Giving the 4.0 builds their own line rather than a build bump on master's version
also keeps their Bugsnag crash reports from merging with the shipped release's.

## Node, dependencies, diagnosis

Imported from `release_ios.py` rather than reimplemented — same problems, and a
second copy would drift silently. That covers `.nvmrc` resolution (including the
`.xcode.env.local` trap, where an absolute `NODE_BINARY` beats anything PATH
arranges), the `npm ci` + `pod install` reinstall, and the build-failure diagnosis
that finds the real error above fastlane's ~60-line changelog banner.

If `release-ios/scripts/release_ios.py` is missing or moves, this skill fails
immediately and says so.

## Prerequisites

Same local setup as `release-ios`:

- `nvm`, or the `.nvmrc` Node already on PATH
- Gems in the Ruby named by `.ruby-version` — `bundle install` (rbenv needs
  `eval "$(rbenv init - zsh)"` in your shell, or you get the system Ruby)
- `src/cask/{dev,prod}` populated, via `scripts/sync-cask.sh seed`
- **Xcode.app signed into team `9C8CA87JCB`** — on its own, the whole credential
  setup, via the no-key route above
- **Optionally** all three App Store Connect values in `ios/fastlane/.env` plus an
  Apple Distribution certificate the installed profiles contain. Only needed if you
  want the API route; a partial configuration is refused either way.
  **Do not expect an Xcode archive to create a certificate for you** — with the
  cloud-managed distribution role most of this team has, Apple keeps the private key
  and the keychain stays empty. See
  [`release-ios` § Getting a distribution certificate](../release-ios/SKILL.md#getting-a-distribution-certificate)

## Don't

- Don't use this to cut a release. It produces no tag and no GitHub release, so
  nothing records what shipped.
- Don't point it at master out of habit — `release-ios` exists for that and does
  the bookkeeping this one deliberately skips.
- Don't push the bump commit to a shared branch without telling whoever owns it.
