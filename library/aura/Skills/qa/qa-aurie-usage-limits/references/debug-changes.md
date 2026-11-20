# Debug changes required to QA usage limits

Four DEBUG-only changes in `AuraConsumerApp`. All of them stay **uncommitted**.
Without 1 the limiter does not run at all; without 3 the logs are unreadable.
2 and 4 set the caps so the round's target is reachable.

Save them before switching branches:

```bash
git diff > /tmp/qa-usage-limits-debug.patch
# later: git apply /tmp/qa-usage-limits-debug.patch
```

---

## 1. Make `-ENABLE_USAGE_LIMITS` persist (required)

**File:** `AuraConsumerApp/Tests/Services/UITest/UITestLaunchEnvironment.swift`,
in `shouldDisableUsageLimits`.

DEBUG builds disable usage limits by default and `ServiceContainer` substitutes
the inert `UITestUsageLimitsService`, which counts nothing. `-ENABLE_USAGE_LIMITS`
switches the real service in — but the stored choice is read with
`UserDefaults.bool(forKey:)`, which **cannot distinguish a stored `false` from
"never stored"**. Both read as `false`, so the check falls through to the
disabled default and every launch from the home screen silently reverts to the
stub.

Replace the stored-value read with an authoritative one:

```swift
// before
if UserDefaults.standard.bool(forKey: UserDefaultsKeys.debugDisableUsageLimits) {
    return true
}
return true

// after
if let stored = UserDefaults.standard.object(
    forKey: UserDefaultsKeys.debugDisableUsageLimits
) as? Bool {
    return stored
}
return true
```

With that read in place a single argument-carrying launch is enough, and later
home-screen launches keep limits on. Verify by reading `debugDisableUsageLimits`
out of the container — it should be `false`.

> This one is arguably a genuine bug rather than a debug aid. Check the current
> code first: if the stored-value read is already authoritative, skip the change.

## 2. Set the debug caps for this round (usually wanted)

**File:** `AuraConsumerApp/Services/UsageLimits/Models/UsageLimitsConfig.swift`

Three constants clamp the package caps so they are reachable in a short session.
`applyDebugCapOverrideIfNeeded` applies each to its daily **and** weekly bucket,
and is skipped under XCTest:

```swift
static let debugPackageMessageCap       = 20        // daily messages
static let debugPackageMessageCapWeekly = 60        // weekly messages
static let debugPackageVoiceCapMs       = 600_000   // 10 minutes, both buckets
```

Choose the values from the target in SKILL.md step 0, not from these defaults.
Three constraints shape the choice:

- **Park what you are not testing.** Every cap feeds one `isAnyLimitReached`, so a
  spent message cap blocks voice entry and vice versa. Put the others out of reach.
- **Size against the account's existing totals.** A QA account carries usage
  across sessions, so the value that gives 1–2 ticks of headroom depends on where
  the ledger already sits — read it before picking a number.
- **Keep the weekly cap above the daily one.** The weekly bucket accumulates
  across sessions, so a weekly value below the daily one blocks the surface at
  launch, before it can charge anything. Invert them only to test the weekly block
  itself, and only on an account still under it.

Verify the values landed by reading the limits back out of the container after
launch — these are compiled-in constants, so a stale build is silent.

## 3. Un-redact the logs (required if you want logs at all)

**File:** `AuraConsumerApp/Services/Log/Logger.swift`

Every field is interpolated as a `String` into `os.Logger`, and `os.Logger`
redacts dynamic strings to `<private>`. So a device capture shows
`<private>:24 <private> - …` and `#function` is unusable. Numeric interpolations
are public by default, which is why some values are visible and most text is not.

Route all four levels through one emit helper and make the fields public in DEBUG:

```swift
private func emit(
    _ level: OSLogType, file: String, function: String, line: Int, message: String
) {
    let fileName = URL(fileURLWithPath: file).lastPathComponent
    #if DEBUG
    osLogger.log(
        level: level,
        "\(fileName, privacy: .public):\(line) \(function, privacy: .public) - \(message, privacy: .public)"
    )
    #else
    osLogger.log(level: level, "\(fileName):\(line) \(function) - \(message)")
    #endif
}
```

Then `debug` → `emit(.debug, …)`, `info` → `.info`, `warn` → `.default`,
`error` → `.error`. The privacy specifier must be a literal in an `os.Logger`
interpolation, so the two builds need separate statements — hence one `#if`
rather than four.

### ⚠️ Security — why this must never ship

The redaction is the only thing hiding secrets that other log statements print.
With this change applied, a collected log archive contains **a live user auth
token in plaintext**. Known offenders:

- `APIService+AuthToken.swift` — logs the full Firebase JWT
- `AppDelegate+Notifications.swift` — logs the raw APNs device token

Consequences:

1. **Keep the change uncommitted.** It must not ship until those statements are
   removed, even DEBUG-only — DEBUG builds run on test devices and their archives
   get shared.
2. **Treat any collected `.logarchive` as a secret.** Never attach one to Linear,
   a PR, or Slack. Delete it when the round is done.
3. If you find further secret-shaped logging, raise it separately rather than
   working around it:
   `grep -rnE 'logger\.(info|debug|warn|error)\(.*\\(.*[Tt]oken' AuraConsumerApp/`

---

## 4. Apply the cap override unconditionally (needed for tiers that omit a cap)

**File:** `AuraConsumerApp/Services/UsageLimits/UsageLimitsService+PackageUsage.swift`,
in `applyDebugCapOverrideIfNeeded`.

The override clamps only the caps the backend actually sent, so a budget the
subscribed tier omits stays `nil` — unenforced, and its flow untestable. Which cap
QA can exercise then depends on which package the test account happens to be on.

Assign unconditionally so the tier cannot decide what is reachable:

```swift
state.message.dailyLimit  = UsageLimitsConfig.debugPackageMessageCap
state.message.weeklyLimit = UsageLimitsConfig.debugPackageMessageCapWeekly
state.voice.dailyLimit    = UsageLimitsConfig.debugPackageVoiceCapMs
state.voice.weeklyLimit   = UsageLimitsConfig.debugPackageVoiceCapMs
```

Skip this one when the point of the round is to confirm a tier's *real* caps as
the backend sends them.

> Note the asymmetry this creates against production: a `nil` cap ships as "no
> limit", so a flow reachable only under this change is not evidence that the flow
> is reachable for a real user on that tier.

## Reverting

```bash
git checkout -- \
  AuraConsumerApp/Tests/Services/UITest/UITestLaunchEnvironment.swift \
  AuraConsumerApp/Services/UsageLimits/Models/UsageLimitsConfig.swift \
  AuraConsumerApp/Services/UsageLimits/UsageLimitsService+PackageUsage.swift \
  AuraConsumerApp/Services/Log/Logger.swift
```

If you are on a branch with real work in progress, stage that work by explicit
path and never `git add -A` while these are applied.
