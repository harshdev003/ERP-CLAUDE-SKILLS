---
name: release-ios
description: Cut an iOS release of the Aura app — bump the version, archive and upload to TestFlight via fastlane, land the bump on master through a PR, then tag the squash-merge commit and publish it as a GitHub release. Use when the user says "release iOS", "cut a release", "ship a build to TestFlight", "release 3.19.149", "bump the build number and release", "do a dev TestFlight build", or asks to resume or check a release that half-finished.
---

# Release iOS

## Goal

Get a version onto TestFlight **and** onto `master` as a tag and a published
GitHub release, in an order where a failed build never leaves a stranded version
bump.

Two halves, split across two tools deliberately:

| | Owner | Why |
|---|---|---|
| Archive, sign, upload, Bugsnag symbols | **fastlane** (`ios/fastlane/Fastfile`) | It already does this correctly and is the thing release engineers test |
| Version bump, PR, merge, tag, GitHub release | **this skill** | All git-history operations on a protected branch — fastlane cannot push to `master`, and a tag placed on a local bump commit points at a commit that squash-merge replaces |

The Fastfile does **not** tag. That was removed when this skill landed; see
[the Fastfile header](../../../ios/fastlane/Fastfile).

## Inputs

- **env** — `dev` or `prod`. Required. Selects `release_dev` / `release_prod`, which
  is what points `src/cask` at that env's Firebase config.
- **version + build** — explicit: `--version 3.19.149 --build 1`.
- **or level** — `--level major|minor|patch|build`. `major`/`minor`/`patch` bump the
  version and **reset the build to 1**; `build` holds the version and increments the
  build, which is how you re-ship a version whose binary was rejected or superseded.

The baseline is read from **`origin/master`'s** `project.pbxproj`, not the working
tree, so a stale checkout cannot propose a version below what shipped.

## Ordering — and the guard that makes it safe

```
branch → install deps → bump → commit → ARCHIVE + UPLOAD → push branch → PR → squash-merge → tag → gh release
                                        └──────────────┘
                                      build happens BEFORE the PR
```

Building first means a failed archive costs a local branch and nothing else — no
version bump sits merged on `master` for a release that never shipped.

The cost is that **the tagged commit is not the built commit**: a squash merge
creates a new one. The script closes that gap with two checks, and neither is
optional:

1. **Before merging** — re-fetch and confirm `origin/master` has not moved since the
   branch was cut. If it has, the squash-merge commit would carry a different tree
   than the binary on TestFlight. Stops, and tells you to cut a new build.
2. **After merging, before tagging** — confirm the merge commit's tree is
   byte-identical to the archived commit's tree, and that the merged commit really
   declares the version being tagged.

`--allow-drift` overrides both. It means "tag a commit that was never built" — only
reach for it when you have looked at the diff and know why it is immaterial.

## The GitHub release

Created after the tag is pushed, **on by default**. `--no-github-release` opts out.

The shape deliberately matches the `eng:release-version` skill (in the AINative
plugin repo, not this one), which cuts every backend repo's release — so the
fleet has one recognisable release body:

| | |
|---|---|
| Title | `Release <tag>` — e.g. `Release 3.20.0-1` |
| Body | release-it's default changelog, byte-for-byte: `git log --pretty=format:"* %s (%h)" <last-tag>...<commit>` |
| Mechanic | `--notes-file`, so a long changelog is never an argv problem |

Two deliberate deltas from the backend shape, both carrying information a
backend release has no equivalent for:

- **One line above the changelog** naming the version, build, and the cask env
  the binary was archived from. `3.20.0-1` alone does not say whether it was cut
  against dev or prod Firebase config, and nothing else in the release does.
- **`--prerelease` on a dev release.** dev and prod share one tag namespace, so
  without it a dev build becomes GitHub's "Latest release" and misrepresents what
  users are on.

`--verify-tag` is passed on every create. Without it `gh` would invent a tag at
the default branch's tip if the push had silently not landed — a release pointing
at a commit nobody built.

**`--latest=false` when back-filling.** Publishing a tag that a later release has
already superseded must not take "Latest" off it. `gh`'s default is *automatic, by
date and version* — genuinely ambiguous for an old version published today — so the
script decides itself: if the newest release tag is not an ancestor of the commit
being published, it passes `--latest=false` and says so.

The notes range is anchored the same way. It resolves the previous release tag
**relative to the commit being described**, not to master's tip — otherwise
back-filling `3.19.139-1` would list the 42 commits that shipped *after* it, headed
by `chore(ios): release 3.20.0-1`.

Aura's four existing GitHub releases (up to `3.19.140-1`) use GitHub's own
`## What's Changed` auto-notes, hand-made in the web UI. Those are not
reproduced: they came from a lapsed manual practice, not from tooling, and
matching them would mean diverging from every other repo in the fleet.

## Node comes from `.nvmrc`, not from your shell

Xcode's "Bundle React Native code and images" phase builds the JS bundle with the
`NODE_BINARY` that `ios/.xcode.env` and `ios/.xcode.env.local` resolve between them.
Absent a pin in `.xcode.env.local`, that is **whatever Node is on PATH in the shell
that launched xcodebuild** — this script's. A release host sitting on a stale Node
would ship a bundle built by it, and nothing downstream would say so.

So **`nvm install` runs every time**, regardless of what your shell is on. With no
arguments it reads `.nvmrc`, no-ops when that version is already present, and selects
it — one command covering install and use, and resolving aliases like `lts/*` that
this script could not resolve on its own.

| Situation | What happens |
|---|---|
| nvm available, version present | Selected; its `bin/` is prepended to PATH for every subprocess |
| nvm available, version absent | `run` installs it. **`plan` does not** — it reports "`run` will `nvm install` it", because `plan` writes nothing and installing a toolchain is a write |
| nvm selects a version `.nvmrc` doesn't pin | **Blocks** — the two disagree, don't build on that |
| No nvm, active Node already matches `.nvmrc` | Proceeds — nvm was never needed |
| No nvm, active Node does not match | **Blocks** in pre-flight |

Two mechanics worth knowing, because both are easy to get wrong:

- **`nvm` is a shell function, not a binary.** `which nvm` finds nothing even where
  nvm works perfectly, and `subprocess(["nvm", …])` raises. The skill locates
  `nvm.sh` (under `$NVM_DIR`, `~/.nvm`, or `brew --prefix nvm`) and sources it in a
  `bash -c` — not `bash -lc`, which would drag in your whole profile and can fail or
  print noise for reasons unrelated to the release.
- **`nvm use` dies with the shell that ran it,** so it can't be fired once and left to
  affect later commands. The sourced shell is asked where `node` landed, and *that*
  directory is prepended to PATH. Prepended rather than invoked by absolute path,
  because `npm` shebangs `env node` — an absolute `npm` would pick the wrong Node
  straight back up off PATH.

Your own shell and `nvm alias default` are never touched; the change lives only in
the subprocesses this run starts. `plan` reports which row you're in, and `run` logs
the exact Node it bundles with.

### PATH alone is not enough — `.xcode.env.local`

Pinning PATH gets you most of the way and **would still ship the wrong Node** on some
hosts. The bundle phase sources `.xcode.env`, then `.xcode.env.local`, and its
fallbacks only fire `if [ -z "$NODE_BINARY" ]`. So an absolute `NODE_BINARY` in
`ios/.xcode.env.local` — gitignored, per-developer — wins over everything the skill
arranges. `npm install` regenerates that file from the interpreter that ran it, so it
can no longer sit stale for months the way a hand-written `v18.15.0` pin once did; a
release still has to resolve it rather than assume it, because `run` reinstalls
*after* pre-flight and the file it finds first is whatever the last install left.

Pre-flight therefore resolves `NODE_BINARY` **the way the bundle phase will**, by
replaying the same steps in the same order, and blocks when the result isn't the
`.nvmrc` version. `run` logs that resolved binary rather than `node --version` on its
own PATH, so the release log can't claim a Node the archive didn't use.

**`plan` checks this to a shallower depth than `run`, deliberately.** When the pinned
Node isn't installed yet, PATH is not what the archive will see — `run` installs
first, `plan` won't (it writes nothing). Asserting a version then would measure the
host's *current* Node, which is exactly the one this feature routes around, and
report it as an `.xcode.env` fault. So while the Node is pending install, `plan`
**warns** if `.xcode.env.local` sets `NODE_BINARY` at all and leaves the verdict to
`run`, which installs and then refuses the archive if it's wrong. The two shapes —
an absolute path (wins over the install) and `$(command -v node)` (follows PATH,
resolves correctly after it) — are indistinguishable until that install happens.

The two env files are sourced by absolute path rather than by invoking
`with-environment.sh`, which resolves them as `$PODS_ROOT/../.xcode.env` — that form
needs `ios/Pods/` to exist for the `..` to resolve, and pre-flight runs *before*
`pod install`. Invoking the real script would source nothing on a fresh checkout and
report a false all-clear.

## Dependencies are reinstalled every release

The branch is cut from `origin/master`, whose lockfiles may not be what is installed
in the checkout. Rather than deciding whether that matters, `run` reinstalls both
trees unconditionally:

```
npm ci --legacy-peer-deps
bundle exec pod install
```

**`npm ci`, not `npm install`** — the two decisions go together, because paying for a
reinstall every release only buys something if the reinstall is exact:

| | Behavior |
|---|---|
| `npm ci` | Wipes `node_modules`, installs precisely what `package-lock.json` says, refuses to run when `package.json` and the lock disagree, **never writes the lock** |
| `npm install` | Reconciles *toward* the lockfile, may amend it, leaves extraneous packages in place |

So running `npm install` every time would cost the same and guarantee less. Measured
at **~26s** for this tree (1828 packages) — noise against a 20-45 minute archive.

Why unconditional beats a staleness check, for each:

- **npm** — nothing downstream catches a stale tree. Metro bundles whatever is in
  `node_modules`, so the binary's JS would silently not match the lockfile the tag
  points at.
- **Pods** — a staleness check can only compare `Podfile.lock` against
  `Pods/Manifest.lock`. A `Pods/` that is corrupt but whose `Manifest.lock` still
  matches reads as healthy and isn't.

`plan` still reports what state each tree is in (`in sync`, `ios/Pods/ is absent`), so
you know whether the release is starting cold — it just no longer gates on it.

**Both installs must leave their lockfile untouched.** `npm ci` cannot write
`package-lock.json`, so that assertion confirms a guarantee rather than checking a
risk; if it ever fires, npm did not behave as documented. `pod install` genuinely can
rewrite `Podfile.lock` or `project.pbxproj`, and either stops the run: the archived
tree would no longer be what master's lockfile describes, and a release PR must carry
only the version bump. (A `project.pbxproj` diff from `pod install` usually means a
different RN-CLI or CocoaPods version than whoever committed it last — `native-ios.md`
says to coordinate on that, not fold it into a release.)

No `--repo-update` on `pod install`: `Podfile.lock` pins exact versions, so the spec
repo only needs refreshing when a pin is newer than the local copy — and CocoaPods
says so explicitly when that happens. A network spec refresh on every release to cover
the rare case is the wrong trade.

The install runs **after** the branch checkout, so the lockfiles are master's, and
**before** `set_version`, so the only tracked change left afterwards is the version
bump. `--skip-install` opts out.

## Prerequisites

Checked by `plan` in seconds, so none of them can surface 30 minutes into an archive:

- `gh` installed and authenticated
- **Clean working tree** — the branch is cut in this checkout and the fastlane lane
  refuses to build a tree that does not match a commit
- `bundle exec fastlane` runs in `ios/` (run `bundle install` per checkout — gems must
  land in the Ruby named by `.ruby-version`)
- **nvm** — the skill runs `nvm install` itself, so the `.nvmrc` Node needs neither to
  be installed nor active beforehand. Without nvm, your shell's Node must already
  match `.nvmrc`. `node_modules` and `ios/Pods` need not exist either — both are
  reinstalled from the branch's lockfiles on every release
- **Xcode.app signed into team `9C8CA87JCB`.** On its own, this is the whole
  credential setup for a local release — see
  [Two routes](#two-routes-chosen-by-iosfastlaneenv). A personal Apple ID makes
  xcodebuild reach for a development cert and fail during build planning; pre-flight
  cannot detect the wrong-team case, it only shows up in the archive
- **Either** no App Store Connect variables at all (the Xcode route — nothing else to
  set up), **or** all three of them in `ios/fastlane/.env` plus an **`Apple
  Distribution`** identity in the keychain **that an installed provisioning profile
  actually contains**, for all three bundle IDs the archive embeds (`com.AuraMobile`,
  `.Aura-Widget`, `.OneSignalNotificationServiceExtension`). A partial configuration
  is refused. Holding the certificate is not sufficient — see below
- `src/cask/<env>/` populated — the lane aborts in step 2 if not, before the archive

### Why "cert in a profile", not just "cert in the keychain"

`exportArchive` re-signs with a distribution profile. If no installed profile contains
your certificate, automatic signing has to mint one — and **an App Store Connect API
key cannot create cloud-managed signing assets at any role**, App Manager included.
The archive succeeds, then export dies ten minutes later with:

```
error: exportArchive Cloud signing permission error
error: exportArchive Provisioning profile "…" doesn't include signing certificate "…"
```

That is what happened cutting `3.20.0-1`: the host held a distribution cert created
after the profiles were issued, so **0 of 13** local `com.AuraMobile*` profiles
contained it. Pre-flight now compares each profile's embedded certs (by SHA-1, which
is exactly what `security find-identity` prints) against the keychain and fails in
about a second instead. It also warns when a covering profile is within 30 days of
expiry.

To fix it when it fires: import a `.p12` for a cert the existing profiles already
contain, or — **only if your own Apple ID holds a real distribution certificate** —
archive-and-export once from Xcode.app signed into an Apple ID with Admin on the team,
which regenerates the profiles around your cert. Creating another certificate is not a
fix — it would be in no profile either, and the team is at Apple's cap of 3 anyway.

### Two routes, chosen by `ios/fastlane/.env`

There is one question, and it picks signing **and** upload together:

> Are `APP_STORE_CONNECT_API_KEY`, `APP_STORE_CONNECT_ISSUER_ID` and
> `APP_STORE_CONNECT_KEY_ID` all set?

| | Signing | Upload |
|---|---|---|
| **All three set** | xcodebuild gets `-authenticationKey*`; needs a distribution certificate in the keychain that an installed profile contains | `upload_to_testflight` |
| **None set** | xcodebuild authenticates as the Apple ID **Xcode.app** is signed into; **no certificate needed** | a second `xcodebuild -exportArchive` with `destination: upload` |

Nothing to pass, no flag, nothing to decide. `asc_configured?` in the Fastfile and
`asc_route()` in the pre-flight test the same three variables, so the two halves
cannot disagree and leave a build signed one way and uploaded another.

**A partial configuration is refused**, rather than quietly demoted to the Xcode
route. The demotion would be invisible and the halves fail differently:
`xcode_asc_auth_args` needs all three and would silently drop the API-key flags, so
signing would succeed via Xcode and only the upload would break — 20-45 minutes in.

#### Why the no-key route needs no certificate

`exportArchive` re-signs with a distribution profile, and Apple's **cloud-managed**
distribution certificates — where Apple holds the private key and nothing lands in
your keychain — can only be used by an **Apple ID**. Apple bars App Store Connect
API keys from them at every role, which is what `Cloud signing permission error`
reports.

fastlane is not the constraint and never was: it does not sign anything. `gym` shells
out to xcodebuild and has no `api_key` option at all (no `-authenticationKey*`
anywhere in `gym/` in 2.237.0), so the only API-key flags xcodebuild ever sees are the
ones `xcode_asc_auth_args` puts in `xcargs` — which `gym` forwards to the
`-exportArchive` invocation as well. Remove the key and you remove API-key auth from
both, and xcodebuild falls through to the Xcode account: the same account, and the
same cloud signing, that makes Archive → Distribute → Custom → Export work by hand.

**That is why this is the local default.** Most engineers on this team have the
`CLOUD_MANAGED_APP_DISTRIBUTION` role and *cannot* hold a certificate — the team is
also at Apple's cap of 3 `Apple Distribution` certs, so a new one means revoking a
live one. Being signed in to Xcode is the whole setup.

#### Why a second `exportArchive`, not `destination: upload` in gym

The export gym runs must still produce an `.ipa`: gym calls
`ErrorHandler.handle_empty_ipa` when it cannot find one
(`package_command_generator_xcode7.rb:79`), and xcodebuild is not documented to leave
an `.ipa` behind when it uploads. That would fail the lane **after the build had
shipped** — which inverts the signal this skill depends on. `run` pushes the release
branch only once the upload succeeds and treats a pushed branch as proof of it, so a
lane that ships and *then* reports failure strands a released build with no PR and no
way to re-run: App Store Connect rejects a second upload of the same version+build.

So gym exports normally — `.ipa` and dSYMs land where the Bugsnag steps expect them —
and `upload_via_xcodebuild!` ships that same archive as a discrete step whose failure
cannot be mistaken for a build failure. It costs one extra export, on the local route
only.

#### What the no-key route gives up

The transfer happens; the bookkeeping around it does not. `upload_to_testflight`'s
build-processing wait, changelog and tester distribution are all skipped. The build
arrives in TestFlight either way.

#### CI must use the key

There is no signed-in Xcode account on a CI runner, so the Xcode route does not
exist there. The lane errors immediately if `ci?` and no key is configured, rather
than discovering it at the upload. CI keeps its own certificate via
`setup_ci_signing!` and a `.p12`.

### The 401 pre-flight

The key id, issuer id and `.p8` have to authenticate *together*, and Apple reports
any mismatch as a bare `401` at upload — after the archive. So on the API route,
pre-flight mints the token exactly as spaceship does
(`connect_api/token.rb#refresh!`) and calls `GET /v1/apps?limit=1`. About a second.

- **401 blocks.** The credentials do not work together; the release would have built
  and then failed to upload.
- **403 / offline / no `openssl` warn** instead: the token authenticated, or the check
  could not run, and neither justifies refusing a release.
- **A missing `.p8` file blocks** too; the Fastfile only caught that mid-lane.
- **Skipped** on the Xcode route (no token is sent) and when
  `APP_STORE_CONNECT_API_KEY_BASE64` is set (the CI shape, where the lane
  materializes the key itself).

### Getting a distribution certificate

Only needed on the **API route**. If `plan` reports no covering certificate and you
do not have one, the cheaper answer is usually to unset the three variables and take
the Xcode route instead.

When the API route is required — CI, or a host with no Xcode account — the
certificate has to be shared, because:

- Most of this team has `CLOUD_MANAGED_APP_DISTRIBUTION`, where Apple keeps the
  private key. `/v1/certificates` returns 401 and an Xcode archive+export leaves the
  keychain empty.
- Apple caps a team at **3** `Apple Distribution` certificates and all 3 are issued.

The hand-off:

1. Whoever holds a distribution identity opens **Keychain Access**, selects the
   `Apple Distribution: Aura Health Inc. (9C8CA87JCB)` row **and its private key**
   (expand the disclosure triangle — the certificate alone is useless), then
   **Export → Personal Information Exchange (.p12)** with a passphrase.
2. Deliver it and the passphrase through **1Password**, never Slack or email. It is a
   private key that can sign as the company.
3. Import, then confirm with `security find-identity -v -p codesigning` and a `plan`
   run — pre-flight fails in about a second if the profiles do not cover it.

Prefer the **longest-lived** certificate; two of the three expire well before the
third. Note a distribution certificate is a *company* asset (its common name is
`Aura Health Inc.`, unlike the per-person development certs), which is why sharing one
is the normal shape and why **`match`** — the same model, automated, encrypted in a
private repo — is the durable answer. There is no `match` or certs repo today.


## Workflow

### 1. Plan — always first

```bash
python3 .claude/skills/release-ios/scripts/release_ios.py plan --env prod --level patch
```

Writes nothing. Prints the resolved version transition, tag, branch, base commit,
last release, the derived phase, every step `run` would take, and any blocking
pre-flight problem. Exit `2` if blocked.

Read three things rather than skimming:

- **The version transition.** `3.19.148 (1) → 3.19.149 (1)` — confirm the baseline
  matches what is actually live.
- **The phase.** `fresh` is a new release. Anything else means a previous run stopped
  part-way and this is a resume — see below.
- **Warnings.** Particularly the `env=dev` one: a dev release still bumps `master` and
  cuts a tag in the shared namespace.

### 2. Show the plan and get approval

**Not optional.** `run` uploads a binary to TestFlight, merges to `master`, and
publishes a GitHub release. Read the consequence sentence `plan` prints at the
bottom and state it back verbatim — it is built from the phase, so on a resume it
names only what that run will actually do:

> *"This uploads `3.19.149-1` to TestFlight, merges a version bump to `master`,
> tags `3.19.149-1` and publishes the `3.19.149-1` GitHub release on
> AuraHealth/Aura."*

Wait for a clear yes.

### 3. Run

The archive takes 20–45 minutes, well past the Bash tool's foreground limit — so
**launch it in the background and poll the log**:

```bash
python3 .claude/skills/release-ios/scripts/release_ios.py run --env prod --level patch \
  > /tmp/release-ios.log 2>&1
```

Use `run_in_background: true`, then `tail` the log. The run is resumable — but see
[Resuming](#resuming) for the one case where "re-run the identical command" is not
enough: once the bump has merged, `--level` computes the *next* version rather than
finishing this one.

The archive's own output is also mirrored to `/tmp/release-ios-<tag>.log`, and on a
failed archive or export the script scans it and prints a **"What actually failed"**
block naming the failing command. Read that instead of scrolling: fastlane ends a
failed run with ~60 lines of its own changelog, so the real error is far above the
last thing on screen.

> **Ignore `fatal error: module 'RCTDeprecation' in precompiled file …`.** Clang emits
> it on *every* archive of this project and immediately recovers — the module is
> rebuilt and the build continues. It is never the cause of a failure, and deleting
> `~/Library/Developer/Xcode/DerivedData/ModuleCache.noindex` does not stop it
> (verified — it regenerates within the same build). It is also usually the last `❌`
> before the real failure, which is exactly why it misleads. The diagnosis block
> filters it out.

Useful flags:

| Flag | Use |
|---|---|
| `--skip-build` | The binary is already on TestFlight; do the git half only |
| `--skip-install` | Don't run `npm ci` / `pod install` — risks bundling JS the tag doesn't describe |
| `--allow-drift` | Merge/tag despite a tree mismatch — see the ordering section |
| `--no-github-release` | Tag without publishing a GitHub release. The release is **on by default**; this opts out |

### 4. Confirm

The script reports the tag, the merge commit, the PR, and the GitHub release URL.
It does **not** confirm App Store Connect finished processing the build — check that in App Store Connect,
and say so plainly rather than reporting the release as fully done.

## Resuming

State is derived from git and GitHub every run — there is no state file — so re-running
the identical command picks up wherever it stopped.

⚠️ **With one exception, and it bites exactly where it matters.** The baseline comes
from what `origin/master` declares, so the moment the bump PR merges, master declares
the version being released. A `--level` re-run then computes the version *after* it and
starts a fresh release, walking straight past the unfinished one — which is every phase
from `merged-untagged` onward, including a failed publish. **Resume those with the
explicit version**, which `plan` now prints for you as a warning whenever it sees a tag
on origin with no release behind it:

```bash
python3 .claude/skills/release-ios/scripts/release_ios.py run --env prod --version 3.20.0 --build 1
```

```bash
python3 .claude/skills/release-ios/scripts/release_ios.py status --env prod --level patch
```

| Phase | Means | `run` does |
|---|---|---|
| `fresh` | Nothing started | Everything |
| `bumped` | Local branch with the bump; not pushed → **the build did not succeed** | Rebuild, then the rest |
| `branch-pushed` | Branch on origin, no PR → **the binary uploaded** | PR → merge → tag → release |
| `pr-open` | PR awaiting merge | Merge → tag → release |
| `merged-untagged` | Merged, not tagged | Tag → release |
| `tagged-unreleased` | Tag on origin, no GitHub release | `gh release create` only — touches no git state |
| `pr-closed` | PR closed without merging | Blocked — `plan` and `run` both refuse (exit 2). Reopen the PR, or delete the branch and re-run to cut a fresh build |
| `released` | Tag on origin **and** a GitHub release for it | Nothing (exit 0) |

The load-bearing detail: **the branch is pushed only after a successful upload.** That
is what makes "build succeeded, PR failed" distinguishable from "never built" without
querying App Store Connect. Do not push the release branch by hand.

**Resuming never rebuilds.** From `branch-pushed` or `pr-open` the build step is
skipped by phase, so `--skip-build` is redundant there — passing it changes nothing,
and omitting it does **not** trigger a second upload. That flag only matters at
`fresh` / `bumped`, where a binary already on TestFlight would otherwise be rebuilt.

### Waiting at the merge gate

GitHub reports `mergeStateStatus: BLOCKED` both for "a required check failed" and for
"a required check has not finished", so the status alone is not actionable. The script
now separates them: a required check still running is waited out (bounded by the
15-minute merge deadline), while a failed required check or a missing required
approval aborts immediately with the specific reason.

Before this, any `BLOCKED` aborted the run — which killed the `3.20.0-1` release
*after* its binary was on TestFlight, over a check that went green two minutes later.

⚠️ **This repo gates `master` with a ruleset, not classic branch protection.** The
`repos/{owner}/{repo}/branches/master/protection` endpoint reports no required checks
and no required reviews, which is simply wrong. The truth lives at
`repos/{owner}/{repo}/rules/branches/master` — currently `ci/circleci: test` and 0
approving reviews. Anything reasoning about merge requirements must read **rules**.

Because the bump commit lives only in this checkout until then, a `bumped` phase is
machine-bound — resume on the same host, or delete the branch and start over.

## CircleCI is disabled

`.circleci/config.yml` defines an `ios_testflight` job that archives and uploads on a
release-tag push. **It is commented out of the workflow.** Two independent reasons, both
in the config's own comment: none of its six required environment variables are set on
the project, and it would double-build every release this skill cuts — App Store Connect
rejects the second upload of the same version+build.

**If you re-enable it, this skill must stop building locally.** The two cannot both run.

## Exit codes

| Exit | Meaning |
|---|---|
| `0` | Released, or already released (a re-run on a finished release is a no-op) |
| `2` | Blocked or failed — pre-flight problem, drift, closed PR, refused tag |
| `130` | Interrupted; re-run to resume |

## Don't

- **Don't pass `version:` / `build:` to `release_prod` / `release_dev`.** The lane
  refuses them. Version writes go through `set_version`, which this skill calls.
- **Don't tag by hand after a fastlane run.** The tree checks are the reason the
  build-before-merge ordering is safe; tagging manually skips them.
- **Don't hand-write the GitHub release in the web UI.** That is how the practice
  lapsed after `3.19.140-1`. A tag with no release resumes into `gh release create`
  and nothing else — just re-run the same command.
- **Don't push the release branch by hand** — it is the record that the upload
  succeeded.
- **Don't reach for `--allow-drift` to get past a stopped run.** It answers "the tree
  differs and I know why", not "I want this to proceed".

## Related

- `eng:release-version` — the backend equivalent; its release-notes renderer is
  the one this skill matches
- [`.claude/rules/native-ios.md`](../../rules/native-ios.md) — fastlane lanes, signing, cask
- [`.claude/skills/sync-cask/SKILL.md`](../sync-cask/SKILL.md) — the dev/prod config swap the lane runs
- [`ios/fastlane/.env.sample`](../../../ios/fastlane/.env.sample) — the credentials to fill in
