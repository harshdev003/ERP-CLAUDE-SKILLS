---
description: Configuration and dependency injection conventions for AuraConsumerApp
globs:
alwaysApply: true
---

# Config & Dependency Injection Conventions

## ServiceContainer

`ServiceContainer` is the central DI container. All services are defined as lazy properties, alphabetically sorted:

```swift
// ✅ GOOD - Lazy service in ServiceContainer
lazy var analyticsManager: AnalyticsManagerProtocol = {
    AnalyticsManager(firebaseService: firebaseService, mixpanelService: mixpanelService)
}()
```

- Never access `ServiceContainer.shared` from within a ViewModel — inject via `init`.
- When adding a new service: add it as a `lazy var`, alphabetically positioned, with a protocol type.

## Protocol-Based Injection

All injected dependencies must use protocol types, not concrete types:

```swift
// ✅ GOOD - Protocol type
let apiService: APIServiceProtocol

// ❌ BAD - Concrete type
let apiService: APIService
```

### Protocol Composition for Lifecycle Services

- When a service is always consumed through lifecycle arrays (`AuthLifecycleProtocol`, `SceneLifecycleProtocol`, `UserLifecycleProtocol`), prefer declaring those conformances on the main service protocol itself.
- Prefer protocol composition over relying only on concrete-type extension conformance plus repeated casts at call sites.
- If a service protocol intentionally does NOT inherit a lifecycle protocol, use a consistent `as?` cast pattern in all lifecycle registration sites.

```swift
// ✅ GOOD - lifecycle guaranteed by protocol
protocol AdEventsManagerProtocol: AuthLifecycleProtocol, SceneLifecycleProtocol, UserLifecycleProtocol, Sendable {}

// ⚠️ Acceptable - only when protocol intentionally avoids lifecycle inheritance
if let lifecycle = adEventsManager as? SceneLifecycleProtocol {
    services.append(lifecycle)
}
```

## ServiceContainer Lifecycle Registration

- Keep registration style consistent across `getAuthLifecycleServices()`, `getSceneLifecycleServices()`, and `getUserLifecycleServices()`.
- Add a short rationale comment for conditional registration blocks (for example, feature flag gating like `Config.adEventsEnabled`).
- Avoid mixing direct append and cast-based append for the same service unless there is an explicit protocol-design reason.

## Environment Configuration

- Use `Config/` directory for build-time environment values (scheme-specific `.xcconfig` files).
- Access via `Bundle.main.infoDictionary` — never hardcode environment-specific URLs or keys.
- Secrets (API keys, tokens) must be stored in the `.xcconfig` files, never in source code.

## Remote Config

- Use `RemoteConfigService` for runtime feature flags.
- Remote config keys are defined as constants — never use raw string literals at call sites.

## Git Worktree Setup (MANDATORY)

When running in a git worktree (e.g., `.claude/worktrees/`), gitignored files are **not** present. The build and tests will crash without them.

**Before building or testing in a worktree**, copy or symlink these files from the main repo.

**IMPORTANT: `GoogleService-Info.plist` must be a real file copy, not a symlink.** xcodebuild does not follow symlinks when copying bundle resources — a symlinked plist will be silently omitted from the `.app` bundle, causing a Firebase crash at launch. The `.xcconfig` files are read at build time (not bundled), so symlinks work fine for those.

```bash
# The main checkout is the first entry in the worktree list:
MAIN_REPO="$(git worktree list --porcelain | head -1 | cut -d' ' -f2)"

# GoogleService-Info.plist — MUST be a real copy (not symlink)
cp "$MAIN_REPO/AuraConsumerApp/Config/GoogleService-Info.plist" AuraConsumerApp/Config/GoogleService-Info.plist

# xcconfig secrets — symlinks are fine (build-time only, not bundled)
ln -sf "$MAIN_REPO/AuraConsumerApp/Config/Secrets.xcconfig" AuraConsumerApp/Config/Secrets.xcconfig
ln -sf "$MAIN_REPO/AuraConsumerApp/Config/Secrets.debug.xcconfig" AuraConsumerApp/Config/Secrets.debug.xcconfig
ln -sf "$MAIN_REPO/AuraConsumerApp/Config/Secrets.release.xcconfig" AuraConsumerApp/Config/Secrets.release.xcconfig

# Per-env config for `make env-<env>` and the release skills: copy only the envs you build
cp "$MAIN_REPO"/AuraConsumerApp/Config/GoogleService-Info_*.plist AuraConsumerApp/Config/
for f in "$MAIN_REPO"/AuraConsumerApp/Config/Secrets.*.xcconfig; do ln -sf "$f" AuraConsumerApp/Config/; done

# Release credentials — only needed if you cut a TestFlight build from this worktree
ln -sf "$MAIN_REPO/fastlane/.env" fastlane/.env
```

After copying/symlinking, run `make proj` to regenerate the `.xcodeproj` so xcodegen picks up the real plist in Copy Bundle Resources.

**Required gitignored files:**
- `AuraConsumerApp/Config/GoogleService-Info.plist` — Firebase will crash at launch without this (**copy**, not symlink)
- `AuraConsumerApp/Config/Secrets.xcconfig` — Build config with API keys (symlink OK)
- `AuraConsumerApp/Config/Secrets.debug.xcconfig` — Debug build secrets (symlink OK)
- `AuraConsumerApp/Config/Secrets.release.xcconfig` — Release build secrets (symlink OK)
- `AuraConsumerApp/Config/Secrets.<env>.xcconfig` and `GoogleService-Info_<Env>.plist` — per-env config that `make env-<env>` and the release skills swap in (plist: **copy**)
- `fastlane/.env` — App Store Connect credentials, needed only for a release from this worktree (symlink OK)

**Symptom if missing:** App crashes on launch with `+[FIRApp configure]` exception during unit/UI tests.

### `fastlane/.env` changes the signing route, silently

`fastlane/.env` is not a build prerequisite — the app compiles and runs without it — so it is easy to skip. But `release-ios` and `release-feature-branch-ios` pick their signing **and** upload route from whether `APP_STORE_CONNECT_API_KEY`, `APP_STORE_CONNECT_ISSUER_ID` and `APP_STORE_CONNECT_KEY_ID` are set. With no `.env` in the worktree all three read as unset, so a release cut there takes the **Xcode Apple-ID route** instead of the API-key route the main checkout is configured for. The archive and the export both succeed on the wrong route; the run then dies at the upload with `error: exportArchive Failed to Use Accounts` — roughly fourteen minutes in.

Both release skills' pre-flight now **blocks** on this combination (linked worktree, no `fastlane/.env`, main checkout has all three) and prints the exact `cp`, so it costs seconds instead of a full rebuild. It only warns when the main checkout has no `.env` either — that host is legitimately on the Xcode route.

If `APP_STORE_CONNECT_API_KEY` in the main `.env` is a **relative** path, copy the `AuthKey_XXXXXXXXXX.p8` across too: the path resolves against the worktree's own `fastlane/`, so the copied `.env` would point at a file that isn't there. An absolute path needs nothing extra.

### Pre-push hook fails to resolve packages in a fresh worktree

**Symptom:** `git push` aborts with `xcodebuild: error: Could not resolve package dependencies`, a list of `Couldn't check out revision …` lines, then `❌ Build failed (xcodebuild exit 74). Push aborted.` This is not a code break. The same change builds and tests cleanly with `-derivedDataPath DerivedData-CLI`.

**Why:** the hook builds into the **default** DerivedData (`~/Library/Developer/Xcode/DerivedData/AuraConsumerApp-<hash>`), not `DerivedData-CLI`, and SPM's git checkouts there fail:

- inside the Claude Code Bash sandbox, always;
- in a brand-new worktree, sometimes even with the sandbox off.

**Fix:** run the push with the sandbox disabled. If it still fails, resolve packages once, let it finish (`Resolved source packages: …`), then push again:

```bash
xcodebuild -project AuraConsumerApp.xcodeproj -scheme AuraConsumerApp \
    -destination 'generic/platform=iOS Simulator' -resolvePackageDependencies
```

Don't bypass the hook with `--no-verify`. It's the only build check that runs before the branch reaches CI.

## AppStore (Global State)

`AppStore` holds global reactive state (user session, app lifecycle flags). Access via injection, not as a singleton at call sites.
