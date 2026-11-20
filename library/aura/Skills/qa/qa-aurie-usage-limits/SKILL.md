---
name: qa-aurie-usage-limits
description: >
  Verify the AuraConsumerApp usage limiter on a physical iPhone — that voice time and messages
  are counted accurately on every surface that ccharges, that caps bind and modals fire, and
  above all that nothing accrues once a call ends. Applies the DEBUG-only changes the limiter
  needs, installs to the device, reads the persisted ledgers out of the app container rather
  than the UI, and cross-checks the backend Trigger on "QA usage limits", "verify usage
  limits", "test the voice cap", "test the message cap", "check the usage limiter on device",
  "did the accrual stop after hang-up", "test the usage limit modal", "verify the voice
  accrual fix", "does Quick Processing count towards limits", or a request to reproduce
  phantom or leaking voice usage. Do NOT use for a production user's historical cap hits
  (`eng:analyze-user-usage-limits`), cost or quota reporting (`eng:report-quota`), or driving
  conversations (`qa:chat-with-aurie`, `qa:talk-to-aurie`). Also triggers on "why does the
  limit modal appear before a call".

metadata:
  version: "1.2.3"
knowledge:
  reads:
    - Guide/Index
    - Tools/Aura API
    - Compliance/Privacy
---

# QA Aurie Usage Limits (device)

Verifies the iOS usage limiter end to end on real hardware against the real
backend. The limiter's counters live in `UserDefaults` inside the app container,
so this skill reads them **directly** rather than trusting the UI — the UI can
be correct while the persisted ledger is wrong, and the interesting failures are
in the ledger.

**Why a dedicated skill:** the limiter is inert in DEBUG by default and its logs
are redacted, so without the prerequisite changes below a QA pass sees a limiter
that looks broken when it is merely switched off — and reads logs that say
`<private>`. Both failure modes are silent and cost hours.

## What you need before starting

- `AuraConsumerApp` checked out, buildable (`make proj` succeeds).
- A physical iPhone paired and unlocked. `xcrun devicectl list devices` shows it.
  Keep it unlocked for the whole round — every `devicectl process launch` against a
  locked phone is refused (see step 3).
- `ios-deploy` installed (`brew install ios-deploy`) — used to read the app
  container. `idevicesyslog` is **not** useful here (see step 7).
- **A valid Apple Development signing certificate.** Check before building —
  a device build otherwise fails at the signing step *after* a full compile:

  ```bash
  security find-identity -v -p codesigning | grep "Apple Development" \
    || echo "NO valid Apple Development cert — see remediation below"
  ```

  If it is missing or expired, the user must sign in to Xcode themselves
  (Xcode → Settings → Accounts, with an Apple ID on the team); it needs their
  password, so you cannot do it for them. Once an account is present,
  `-allowProvisioningUpdates` on the build mints the certificate and profile.
  A `Developer ID Application` or `Apple Distribution` identity does **not**
  substitute — neither signs a development build for a device.
- A test account signed in on the device, **on a paid package if voice caps are in
  scope at all**. A free account cannot exercise a voice cap: the free cap ships at
  `0` and setting a non-zero one means editing a production constant that is then
  clobbered before it reaches the record (see step 4). Package caps are overridden
  from inside `#if DEBUG`, which is why they are the tier to test on.

## Step 0 — Choose the surfaces, and park the caps you are not testing

Read [`references/surfaces-and-flows.md`](references/surfaces-and-flows.md) and
pick the surfaces for this round. Six reach the limiter and none of them shares a
gate, so a pass on one is not a pass on another — the reference carries the
surface/gate matrix, per-surface recipes, and which tier each surface can exercise.

Two things from it decide the cap values before you build anything:

- **Every cap feeds one `isAnyLimitReached`.** An exhausted *message* cap refuses
  a voice call at the entry gate, so the voice paths become unreachable. Park the
  caps you are not testing far out of reach.
- **Size the cap under test against the account's existing period totals**, not as
  a round allowance. A QA account carries usage across sessions, so 1–2 ticks of
  headroom is a deliberate calculation rather than a default.

## Step 1 — Apply the debug changes (mandatory)

Read [`references/debug-changes.md`](references/debug-changes.md) and apply them.
Without change 1 the limiter is a no-op stub; without change 3 every log line
reads `<private>`. Changes 2 and 4 set the cap values chosen in step 0.

Keep them **uncommitted**, and save them so they survive a branch switch:

```bash
cd <AuraConsumerApp>
git diff > /tmp/qa-usage-limits-debug.patch   # re-apply later with: git apply
```

**Never commit these.** Stage by explicit path when committing real work on the
same branch — never `git add -A`. Change 3 in particular must not ship: it
exposes secrets that other log statements print (see the security note in the
reference).

## Step 2 — Restore gitignored config if you are in a worktree

A git worktree has none of the gitignored config, and the app crashes at launch
without it. `GoogleService-Info.plist` must be a **real copy** — `xcodebuild`
silently drops symlinked bundle resources:

```bash
MAIN=<path-to-main-AuraConsumerApp-checkout>
cp "$MAIN/AuraConsumerApp/Config/GoogleService-Info.plist" AuraConsumerApp/Config/

# Link each secrets file to its own counterpart in the main checkout, so a Debug
# build gets whatever debug secrets that checkout uses. Point every one at
# Secrets.prod.xcconfig instead and a Debug build silently loads prod secrets.
# `Debug.xcconfig` pulls in Secrets.xcconfig and Secrets.debug.xcconfig via
# `#include?`, so a name the checkout does not have is simply skipped.
for f in Secrets.xcconfig Secrets.debug.xcconfig Secrets.release.xcconfig Secrets.prod.xcconfig; do
  src="$MAIN/AuraConsumerApp/Config/$f"
  [ -e "$src" ] && ln -sf "$src" "AuraConsumerApp/Config/$f"
done
make proj
```

**Confirm which backend the build points at.** The symlinks above inherit whatever
environment the main checkout was last switched to with `make env-dev|env-prod
debug|release` (`Scripts/switch-env.sh`), so a worktree can silently build against
dev while the test account lives in prod — the packages catalogue then comes back
empty and the limiter never arms, which reads exactly like a limiter bug:

```bash
# Firebase project actually bundled (prod is firebase-auratech16)
plutil -extract PROJECT_ID raw \
  DerivedData-CLI/Build/Products/Debug-iphoneos/AuraConsumerApp.app/GoogleService-Info.plist

# API the build will call
grep -E '^API_BASE_URL' AuraConsumerApp/Config/Secrets.debug.xcconfig
```

Both must match the environment the account belongs to. To switch, run
`make env-prod debug` (or `env-dev`) in the main checkout and re-copy the plist.

## Step 3 — Build, install, launch with limits enabled

```bash
DEV=$(xcrun devicectl list devices | awk '/available \(paired\)/ && /iPhone/ {print \$3; exit}')

xcodebuild -project AuraConsumerApp.xcodeproj -scheme AuraConsumerApp \
  -configuration Debug -destination "platform=iOS,id=$DEV" \
  -derivedDataPath DerivedData-CLI -allowProvisioningUpdates build

# Verify the plist actually landed — this is the worktree trap
ls DerivedData-CLI/Build/Products/Debug-iphoneos/AuraConsumerApp.app/GoogleService-Info.plist

xcrun devicectl device install app --device "$DEV" \
  DerivedData-CLI/Build/Products/Debug-iphoneos/AuraConsumerApp.app

xcrun devicectl device process launch --device "$DEV" --terminate-existing \
  com.aurahealth.AuraConsumerApp -ENABLE_USAGE_LIMITS
```

**Check the build's own exit status, not a pipeline's.** Piping `xcodebuild` into
`tail`/`grep` replaces its exit code with the filter's, so a failed build reports
success and the round proceeds against a stale `.app`. Redirect to a log and read
the status directly:

```bash
xcodebuild … build > /tmp/devbuild.log 2>&1; echo "BUILD_EXIT=$?"
grep 'error:' /tmp/devbuild.log | sort -u | head
```

The launch argument is what arms the real limiter. With debug change 1 applied it
persists, so later launches from the home screen keep limits on.

A locked phone refuses the launch with `FBSOpenApplicationErrorDomain error 7`
(`BSErrorCodeDescription = Locked`) — the install itself succeeds, so only the
launch needs retrying. Ask the user to unlock and retry rather than rebuilding.

## Step 4 — Establish a baseline

Read the ledgers twice, at least two tick intervals apart (the accrual commits
every 45s), with **no call running**:

```bash
python3 scripts/read_usage_ledgers.py --udid <IOS_UDID>
```

Records are stored per user (`usageLimits_<userId>_…`) and a device keeps the
records of every account that has signed in on it, so the read pins itself to one
user: it resolves the signed-in `userId` and reports any other UIDs it found as
`stale-uids` rather than folding them in. Pass `--uid <userId>` to pin explicitly.
**If the output names `stale-uids`, check that the numbers you are reading belong to
the account under test** — a logout-then-signup leaves a full set of another user's
counters behind, and they can look like a plausible baseline.

Get `<IOS_UDID>` — the iOS UDID, not the devicectl identifier — from
`ios-deploy -c --timeout 5`. The two are different values for the same phone and
neither tool accepts the other's; `devicectl` prints the identifier
(`7143AB9C-…`), `ios-deploy` and `idevice*` want the UDID (`00008150-…`).

Without `ios-deploy`, `devicectl` reads the same file — useful as a fallback, and
it takes the identifier rather than the UDID:

```bash
xcrun devicectl device copy from --device "$DEV" \
  --domain-type appDataContainer --domain-identifier com.aurahealth.AuraConsumerApp \
  --user mobile --source Library/Preferences/com.aurahealth.AuraConsumerApp.plist \
  --destination /tmp/aurie-prefs.plist
```

**Both reads must be identical.** If voice climbs while idle, a previous session
leaked and you are already in the bug; note it and restart the app before
proceeding.

The read also reports `debugDisableUsageLimits`. **It must be `false`.** `true` or
unset means the inert stub is in play and nothing below counts anything.

Confirm the **limits** in the record match the values chosen in step 0 — they are
compiled-in constants, so a stale build is otherwise silent, and every subsequent
number would be measured against the wrong cap. Two ways this check legitimately
fails to show your value:

- **No `packageUsage` record at all** → the limiter is not armed because the account
  is not premium. `configurePackageLimits` is only reached for a paid package, so it
  never runs and the DEBUG clamp never applies. The absence *is* the diagnostic, not
  a bug — but it means no package cap can be exercised until the account is upgraded.
- **A free `voiceLimitSeconds` that stays at its stored value** however you set
  `UsageLimitsConfig.voiceCapSeconds` → `setChatMessageLimit` re-reads the persisted
  record and writes it back, clobbering the cap `loadUsageLimits()` had just
  rewritten from config
  ([PROD-4663](https://linear.app/aura-health/issue/PROD-4663)). Reaching a free
  voice cap requires making config authoritative in `FreeUsageState.init(from:)`
  first. Prefer package caps, which do not go through that path.

Record `voice.dailyUsed`, `voice.weeklyUsed` and their limits — the *smaller*
remaining headroom (daily vs weekly) is what will bind, and it is easy to plan a
5-minute call against a daily cap while the weekly one has 30 seconds left. Do the
same for `message` if a message cap is in scope.

## Step 5 — Start the sampler, then run a scenario

```bash
bash scripts/poll_usage_ledgers.sh --udid <IOS_UDID> --count 26 --interval 45
```

Then drive **one** scenario per run, and write down the wall-clock time of every
transition — you cannot reconstruct these afterwards.

**Confirm which surface was actually driven before you record a verdict.** When
someone else is holding the phone, "started a voice call" does not say whether it
was the live session or Quick Processing, and those run different entry gates and
different mid-call observers — so the same ledger series supports a different
conclusion depending on which one it was. Confirm it from the logs (the view-model
file names in step 7) or ask outright, and label the scenario with the surface. A
verdict recorded against the wrong surface is worse than no verdict, because it
reads as coverage the code never got.

### Voice / teardown scenarios

| Scenario | Steps | Expected |
|---|---|---|
| Idle | No call, 2+ intervals | No change at all |
| **Back-button hang-up** | Call ~2 min, exit via **back**, wait 5 min | A *partial* charge, then permanently flat |
| End-button hang-up | Call ~2 min, tap End, wait 5 min | Same |
| Cap-terminated | Run until the cap binds | Modal, call ends, ledger flat |
| Backgrounded mid-call | Swipe home, wait, foreground | Keeps accruing — `audio`/`voip` background modes keep the call alive, so this is *correct* |
| Killed mid-call | Force-quit during a call | Uncommitted remainder is dropped; next launch reconciles |
| Reconnect | Toggle airplane mode briefly mid-call | Accrual continues unbroken across the reconnect |

Prioritise the back-button path: it reaches teardown through a different code
path from the End button, so passing one says nothing about the other. A
cap-terminated call ends through the mid-call observer, which is a third path
again — it does not stand in for either hang-up.

### Message / enforcement scenarios

Per surface, since the gates are per-surface. One send at a time, reading between
sends.

| Scenario | Steps | Expected |
|---|---|---|
| Charge fan-out | One send, well under the cap | Package daily, package weekly and the free counter each move by exactly **one**; both voice buckets unchanged |
| Crossing send | Send up to the cap | Limit modal on the send that *reaches* it, not the one after |
| Over-cap send | Dismiss the modal, send again | Refused: counter flat, message not delivered, modal returns. On QP chat it is charged and delivered — PROD-4437 |
| Soft warning | Cross 80% of a cap | Heads-up modal, once per session, on a surface that presents it |
| Voice entry while over | Attempt a call while any cap is reached | Refused before connecting |

All three message counters moving together is the check that the single-commit
fan-out reaches the surface under test; a surface that charges only the free
counter is instrumented for the wrong tier.

## Step 6 — Cross-check against the backend

The local ledger only gates the user *between* fetches; the backend owns the
authoritative total. Compare them once the call has ended and a reconcile has run:

```
GET /ai/usage/stats/:userId
```

Use `eng:call-aura-api` for the call — it resolves auth and the exact request
shape from `Tools/Aura API`, so do not hand-roll curl or handle tokens here.

Establish what the two numbers mean before reading anything into a gap:

- The **free** counter is a lifetime total; `/ai/usage/stats` reports **daily and
  weekly** buckets. They are only comparable on an account whose entire history
  falls inside the current period — otherwise compare against `packageUsage`, which
  is bucketed the same way.
- The backend credits a voice session on **disconnect**, so mid-call it is
  legitimately behind. Only compare after teardown.
- The client bills from **agent-ready**; the backend measures its own session
  lifecycle. Connect and agent-join time therefore sits outside the client's total,
  which is a real and variable source of disagreement, not rounding.

Sub-second agreement on a multi-minute call is the healthy case. Measured spreads
have ranged from the client 760 ms *higher* to the backend ~28 s higher across two
calls on one account, so treat a single-digit-second gap as "explain it", not
"fail it".

The signature that does matter: a local total **higher** than the backend that does
not come down once a reconcile has run. Verify a reconcile actually ran — a
package-only change landing on a round whole-second value is its fingerprint. On an
account with no `packageUsage` record that check is unavailable, because
`refreshPackageUsageStats()` only runs with the package limiter armed and the free
counter has no backend to reconcile against; say so rather than reporting agreement
you could not test.

## Step 7 — Read the logs (optional, and know the traps)

Device unified logs need root to collect, so this is a step **the user runs**, and
the device must be attached over **USB** — on a Wi-Fi-only connection the collect
fails with `failed to create archive: Device not configured (6)`:

```bash
sudo log collect --device-udid <IOS_UDID> --last 20m --output ~/Desktop/qa.logarchive
```

`--device-name "<iPhone name>"` is the fallback when the UDID form cannot reach the
device. Then query without root, via the **absolute path** — `log` is a zsh builtin
and a bare `log show` fails with `too many arguments`:

```bash
/usr/bin/log show ~/Desktop/qa.logarchive \
  --predicate 'subsystem == "com.aurahealth.AuraConsumerApp"' --info --debug --style compact
```

Collect promptly, while the window is still live. `log stream` has **no device
option**, so there is no way to tail a phone's `os_log` as it happens and no
non-sudo alternative to `log collect` — an evicted window cannot be recovered later.

**Identifying which surface and engine ran** is what the log is most reliably good
for, because every line carries its source file:

- `LiveSessionViewModel*` vs `QPVoiceModeViewModel` / `QuickProcessingViewModel*`
  names the **surface**.
- `LiveKitVoiceService*` and `AudioSessionManager — Caller: LiveKitVoiceService` vs
  `ElevenLabsVoiceService*` names the **engine**; the router also logs
  `aiLiveKitIntegration<N> resolved: variant '<x>' → LiveKit|ElevenLabs` once per run.
  Grep the `aiLiveKitIntegration` prefix, not a pinned version — the experiment is
  re-cut under a new trailing number each restart. `references/surfaces-and-flows.md`
  → Engine coverage names the current one and how to confirm it.

Three traps, all of which have produced wrong conclusions before:

- **`idevicesyslog` does not carry `os_log`.** It reads the legacy syslog relay;
  app `os.Logger` output is not in it. Lines it shows mentioning the app are other
  processes.
- **Absence of a `debug`/`info` line proves nothing.** Those live in an evictable
  memory buffer — a busy process crowds them out within minutes. `error` level
  persists to disk, so an error line's absence *is* meaningful.
- **Do not attach a debugger** (`ios-deploy --debug`) to time-sensitive runs. It
  alters Task scheduling and app suspension, which is exactly what the 45s ticker
  depends on.

**Security:** a collected archive can contain secrets that other log statements
print in plaintext once debug change 3 is applied — a live user auth token has
been read out of one. Treat archives as sensitive: never attach one to a Linear
issue, a PR, or Slack, and delete them when done.

## Step 8 — Interpret

Read [`references/interpreting-results.md`](references/interpreting-results.md)
before drawing any conclusion. It covers the pass/fail signatures, the
whole-tick-versus-partial rule that distinguishes a real teardown flush from a
skipped one, and the sampling artefacts that make raw deltas untrustworthy.

The single most important rule: **never call it pass or fail off one flat sample.**
`UserDefaults` flushes to the plist lazily, so a flat read can be a stale snapshot
of a ledger that is still moving. Span at least two tick intervals.

## Step 9 — Report and clean up

Report per scenario: baseline, the transition timestamps, the ledger series, and
the verdict with its reasoning. Each scenario carries:

- **The surface it ran on**, and how that was established — a log line, or the
  tester's own account. Two scenarios on different surfaces are two results.
- **The engine**, and whether it was log-confirmed. The live
  `aiLiveKitIntegration<N>` experiment decides
  ElevenLabs vs LiveKit per user (variant `a` → LiveKit; a Firebase rollout of
  `active: "a"` applies it to everyone), the two have different teardown paths, and a
  pass on one is not a pass on the other.
- **Timestamps from the log where one exists.** A hand-noted transition time can be
  off by tens of seconds, which is enough to make the arithmetic disagree with the
  ledger; the logged teardown is what closes it.

State what you did **not** cover, including surfaces from
[`references/surfaces-and-flows.md`](references/surfaces-and-flows.md) left out of
this round, and any check that was attempted but unavailable.

When the round is worth a durable record, file it with `shared:add-linear-issues`
as a sub-issue of the usage-limits audit card, with the per-scenario tables as a
comment and any defect as its own issue. Ledger numbers and UID prefixes are fine
there; full user identifiers are not (`Compliance/Privacy`).

Then:

```bash
rm -rf DerivedData-CLI TestResults
git checkout -- <the three debug files>   # or: git stash
rm -f ~/Desktop/*.logarchive
```

Leave the debug patch at `/tmp/qa-usage-limits-debug.patch` for the next round.
