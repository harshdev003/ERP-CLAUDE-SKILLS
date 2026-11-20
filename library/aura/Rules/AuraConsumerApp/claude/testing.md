# Testing Validation

## Related Rules

- `project-rules.md` for overall quality gates and build validation.
- `pr-process.md` for PR submission workflow.
- `accessibility.md` for accessibility identifier requirements in Views (needed by UI tests).

## When to Write Tests (MANDATORY)

**Always consider adding tests when making code changes.** Tests are not optional — they are part of the deliverable.

### Unit Tests — Add When:

- **New ViewModel logic**: Any new ViewModel method that transforms data, handles errors, or manages state must have unit tests covering happy path, error path, and edge cases.
- **New Service logic**: Business logic in services (parsing, validation, state machines, data transformation) must be unit tested.
- **New utility/extension methods**: All new helper functions, extensions, and utility methods require unit tests.
- **Bug fixes**: Every bug fix must include a regression test that would have caught the bug.
- **New domain models**: `Codable` conformance, computed properties, and custom initializers need tests.

### UI Tests — Add When:

- **New screens/features**: Every new screen must have a feature-level UI test file (`{Feature}FeatureUITests.swift`) verifying the screen loads and key elements exist.
- **New user flows**: Multi-screen flows (e.g., onboarding, sign-up, journal creation) need a flow test file (`{Feature}FlowUITests.swift`) covering the end-to-end journey.
- **New interactive elements**: Buttons, tabs, toggles, and inputs that drive navigation or state changes must be exercised in UI tests.
- **New page objects**: When adding a new screen's UI tests, create a corresponding `{Feature}PageObject.swift` to encapsulate element queries and reusable actions.

### When NOT to Add Tests:

- Pure layout/styling changes with no logic.
- Comment-only or documentation-only changes.
- Config file changes (`project.yml`, `.xcconfig`) that are validated by the build itself.

### Test Structure Conventions

**Unit tests** (`AuraConsumerAppTests/`):
```
AuraConsumerAppTests/
├── Shared/
│   ├── Extensions/       # Extension tests (StringExtensionsTests.swift)
│   └── Utils/            # Utility tests (UtilsTests.swift)
├── Features/{Feature}/
│   └── ViewModels/       # ViewModel tests ({Feature}ViewModelTests.swift)
└── Services/{Domain}/    # Service tests ({Service}Tests.swift)
```

- File naming: `{TypeUnderTest}Tests.swift`
- Class: `final class {Type}Tests: XCTestCase`
- Import the app module with `@testable import AuraConsumerApp`
- Use `// MARK: -` sections to group related test methods
- Each test method must have at least one `XCTAssert*` call
- To test a request path, stub the network, not the service: `APIService` takes an optional `session: URLSession`, so a test passes a session whose configuration registers a `URLProtocol` stub. See `APIServiceAuthRetryTests`.

**UI tests** (`AuraConsumerAppUITests/`):
```
AuraConsumerAppUITests/
├── Base/                 # BaseUITestCase (shared setup, helpers)
├── Features/             # Feature-level tests ({Feature}FeatureUITests.swift)
├── Flows/                # Multi-screen flow tests ({Feature}FlowUITests.swift)
├── PageObjects/          # Reusable element accessors ({Feature}PageObject.swift)
└── Helpers/              # Test utilities (TestRecorder, etc.)
```

- All UI test classes extend `BaseUITestCase`
- Use `XCTSkip` for conditions that prevent the test from running (e.g., auth required)
- Use `guard wait(for:, timeout:) else { throw XCTSkip(...) }` for preconditions
- Every test method must have at least one unconditional `XCTAssert*` (screenshots alone are not tests)
- Use `waitForAny(elements:, timeout:)` instead of sequential `wait()` calls to avoid timeout accumulation
- Wrap `UITestLaunchEnvironment` checks in `#if DEBUG` in production code

## Mandatory After Code Changes

SwiftLint and unit tests **must** run after every code change. Do not commit or open a PR without passing both.

### 0. Regenerate Project (ALWAYS — Before Any Build or Test)

**CRITICAL: Always run `make proj` before building or testing**, especially after pulling latest code or switching branches. The `.xcodeproj` is generated from `project.yml` via xcodegen — new files added by other developers won't compile until the project is regenerated.

```bash
make proj
```

Skipping this step causes "cannot find X in scope" build failures for newly added types.

### 1. SwiftLint (Always Required)

```bash
swiftlint lint --strict <changed-files>
```

- Lint only the files you changed, not the entire project.
- Must have **zero violations** before proceeding.

### 2. Unit Tests (Always Required)

```bash
make proj
SID=$(bash AuraConsumerAppSimTests/Scripts/sim_start.sh | tail -1)   # this session's own simulator
xcodebuild test \
    -project AuraConsumerApp.xcodeproj \
    -scheme AuraConsumerApp \
    -destination "platform=iOS Simulator,id=$SID" \
    -parallel-testing-enabled NO \
    -only-testing:AuraConsumerAppTests \
    -derivedDataPath DerivedData-CLI \
    -resultBundlePath TestResults/UnitTests
```

- Use `-derivedDataPath DerivedData-CLI` to avoid locking conflicts with Xcode.
- Target this session's own simulator by id. A name-based destination (`name=iPhone 17 Pro,OS=latest`) can resolve to a connected physical iPhone, and a shared name can be another session's device.
- Keep `-parallel-testing-enabled NO`: with it on, xcodebuild boots up to five clones of the simulator.
- All unit tests must pass.

### 3. UI Tests (Ask First)

**Before running UI tests, ask the user**: "Should I run UI tests? They take ~10 minutes."

If yes:

```bash
xcodebuild test \
    -project AuraConsumerApp.xcodeproj \
    -scheme AuraConsumerApp \
    -destination "platform=iOS Simulator,id=$SID" \
    -parallel-testing-enabled NO \
    -only-testing:AuraConsumerAppUITests \
    -derivedDataPath DerivedData-CLI \
    -resultBundlePath TestResults/UITests
```

- UI tests that skip via `XCTSkip` (e.g. requiring auth) are expected — only actual failures count.

## Sequential Test Suite Execution (MANDATORY)

**Never run unit tests and UI tests in parallel.** Both targets share the same `-derivedDataPath`, and concurrent `xcodebuild` invocations will lock the build database (`build.db`), causing one or both runs to fail with:

> error: accessing build database "…/build.db": database is locked

**Always run test suites sequentially:** finish one `xcodebuild test` invocation before starting the next.

```
✅ Unit tests → wait → UI tests
✅ UI tests → wait → Unit tests
❌ Unit tests + UI tests at the same time
```

## Session-Specific Logs (MANDATORY)

Several sessions build and test on this Mac at once, and **`$TMPDIR` and `/tmp` are shared by all of them**. A log written to a fixed name like `$TMPDIR/unit.log` gets overwritten or interleaved by another session's run. That has happened: two sessions both wrote `$TMPDIR/unit.log`, and a `2062 tests, 0 failures` line could not be attributed to either run.

- **Write `xcodebuild` output inside your own worktree**, under the gitignored `TestResults/` (cleaned up with it below). A worktree belongs to one session, so the path is session-specific by construction.
- **Take pass/fail from your own `-resultBundlePath`** (or from CI), not from a log another session could have written.
- If a log has to live outside the worktree, put it in the session's scratchpad dir, or give it a unique name (`mktemp`). Never use a fixed name in `$TMPDIR` or `/tmp`.

```bash
# ✅ GOOD — inside this worktree, next to the result bundle
mkdir -p TestResults
xcodebuild test ... -resultBundlePath TestResults/UnitTests > TestResults/unit.log 2>&1

# ❌ BAD — shared by every session on the machine
xcodebuild test ... > "$TMPDIR/unit.log" 2>&1
```

## Cleanup

After all test runs complete, remove the temporary directories:

```bash
rm -rf DerivedData-CLI TestResults
```

Always clean up, whether tests pass or fail.

## Simulator Selection

- Prefer `iPhone 17 Pro,OS=26.2` (or latest available).
- If the destination is ambiguous (multiple OS versions), specify the OS explicitly.
- List available simulators with: `xcrun simctl list devices available | grep iPhone`
- **After an Xcode update, a test app that crashes at launch, before any test runs, is a runtime mismatch, not your change.** The build uses the new Xcode's simulator SDK, but the simulator still runs an older runtime build (`xcrun simctl list runtimes` shows the build number; xcodebuild also logs errors reading the simulator's OS build version). Download the matching runtime with `xcodebuild -downloadPlatform iOS`, create a simulator on it, and rerun. A new runtime may not offer every device type the old one did, so check `xcrun simctl list devicetypes` before assuming `iPhone 17 Pro` exists on it.
  **Why:** after the Xcode 27.1 update the unit-test host crashed on every launch on a simulator created under an older iOS 27.1 runtime build; the matching runtime fixed it with no code change.

## UI Test Synchronization (MANDATORY)

**Never use `Thread.sleep` in UI tests.** It is non-deterministic, slows tests, and hides timing bugs.

### Use Predicate-Based Waits

```swift
// ✅ GOOD — event-driven, reacts immediately when condition is met
func waitForElementToBeHittable(_ element: XCUIElement, timeout: TimeInterval = 10) -> Bool {
    let predicate = NSPredicate(format: "isHittable == true")
    let expectation = XCTNSPredicateExpectation(predicate: predicate, object: element)
    return XCTWaiter.wait(for: [expectation], timeout: timeout) == .completed
}

// ✅ GOOD — wait for existence
element.waitForExistence(timeout: 10)

// ✅ GOOD — wait for disappearance
let predicate = NSPredicate(format: "exists == false")
let expectation = XCTNSPredicateExpectation(predicate: predicate, object: element)
_ = XCTWaiter.wait(for: [expectation], timeout: timeout)

// ❌ BAD — polling with sleep
while !element.isHittable {
    Thread.sleep(forTimeInterval: 0.5)
}
```

### Wait for Audio State via Hidden Element

Onboarding views expose a hidden accessibility element reflecting `TTSState`.
Use `.audioStatusAccessibility(ttsState:)` on any view that plays audio, then
wait in tests:

```swift
// ✅ GOOD — deterministic audio sync
let playing = app.descendants(matching: .any)["audio_playing"].firstMatch
XCTAssertTrue(playing.waitForExistence(timeout: 10), "Audio did not start")

let stopped = app.descendants(matching: .any)["audio_stopped"].firstMatch
XCTAssertTrue(stopped.waitForExistence(timeout: 30), "Audio did not finish")

// ❌ BAD — guessing how long audio takes
Thread.sleep(forTimeInterval: 5)
```

Available identifiers: `audio_playing`, `audio_stopped`, `audio_loading`, `audio_error`.

### Base Class Helpers

Both `BaseUITestCase` and `BaseSimTestCase` provide:

| Method | Use case |
|--------|----------|
| `wait(for:timeout:)` | Element existence |
| `waitForElementToBeHittable(_:timeout:)` | Element interactable |
| `waitForDisappearance(of:timeout:)` | Element removed |
| `waitForAny(elements:timeout:)` | First of multiple elements |
| `tapElement(_:timeout:)` | Wait hittable + tap |

Always use these instead of writing custom polling loops.

## Interpreting Results

| Outcome | Action |
|---|---|
| SwiftLint violations | Fix all before proceeding |
| Unit test failure | Fix before committing |
| UI test failure (0 unexpected) | Pre-existing expected failure — note it, not a blocker |
| UI test skip (`XCTSkip`) | Expected when auth/backend unavailable — not a failure |
| Build DB locked | Use `-derivedDataPath DerivedData-CLI` to avoid Xcode conflicts |
