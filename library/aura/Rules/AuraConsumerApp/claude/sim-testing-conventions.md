---
paths:
  - "AuraConsumerAppSimTests/**"
  - ".claude/skills/sim-test/**"
  - ".claude/skills/screenshot-harvest/**"
  - ".claude/skills/aurie-screens-weekly/**"
---

# Sim Testing Conventions

Operational rules for the `/sim-test` skill and any agent driving a simulator via `sim_bridge.py`. These are workflow conventions — the facts about the app live in `AuraConsumerAppSimTests/knowledge/`, and the skill itself lives in `.claude/skills/sim-test/`. This file is the short checklist agents should read before running or debugging a sim test.

## Related Rules

- `.claude/rules/sim-testing.md` — fundamentals of the sim test harness
- `.claude/skills/sim-test/SKILL.md` — the full skill with modes and orchestration
- `AuraConsumerAppSimTests/knowledge/rules.md` — safety constraints + experiment/premium APIs
- `AuraConsumerAppSimTests/knowledge/workarounds.md` — app-behaviour quirks

## Runner strategy

### Single test → run in the main conversation
For a **single** sim test, drive the command loop directly in the main conversation. Do NOT spawn an agent. Users want live progress: current screen, action taken, findings as they happen. Spawned agents are silent until completion which is frustrating for a single run.

### Parallel tests → spawn one agent per session
For **multiple** concurrent runs, spawn one agent per simulator UDID. Silent execution is fine because users can't watch multiple streams anyway. Each agent owns its session ID end-to-end.

### One simulator per session — never a shared name
Each session drives its **own** simulator, `"<model> (<label>)"`, e.g. `iPhone 17 Pro (frosty-golick-68d358)`. The scripts create it on first use, and the label is the worktree name (or `$SIM_SESSION_NAME`). Don't pass `--sim "iPhone 17 Pro"`: that is the shared device, and another session may be using it. Use `--device-type` to pick a different model. Never `simctl shutdown|erase` a simulator by name, and never touch a UDID your session didn't start. See `sim-testing.md § One Simulator Per Session`.

### Always use session IDs (simulator UDID)
Session ID is mandatory regardless of single/multi mode. **Never fall back to `"default"`** — it routes all sessions to the same directory and breaks multi-session support.

Resolution order the test code follows: `SIM_SESSION_ID` env → `SIMULATOR_UDID` env → `AdHocTestConfig.defaultSessionId`. Always provide one explicitly.

### One long command per agent — they stall silently between commands

An agent given a numbered list of long commands ("each with `run_in_background`, wait for
it to finish before the next") reliably runs the first, says "I'll wait for the
notification", and never fires the rest. It does not know it is stuck, so it never reports
a problem — the failure is silent, and the only visible symptom is that nothing is
happening on the simulator. Measured twice in one run on 2026-09-28.

- Give each agent **one** long command. A multi-step sequence is either several agents in
  turn, or driven from the main session where each completion re-invokes the caller.
- **Silence is not progress.** Check the work, not the worker: `ps aux | grep -c
  "[s]im_capture.py"` at zero while agents report "waiting" means stalled, not busy. Mind
  the gap between a single agent's own commands before calling it finished.
- When one stalls, `TaskStop` it and run the remainder from the main session.

### Agent prompts must be concise
Keep sim-test agent prompts short. Inline the essential info. Do NOT tell agents to read large reference files (`knowledge.json`, `actions.md`, `screen-guide.md`) — reading big files exhausts context, which is the #1 cause of agent failures on long flows.

### Spawn an agent per session even for single runs (when parallelising is likely)
If the intent is "I'll scale this to parallel later", spawn the agent now so the pattern is consistent. The orchestrator logs per session ID; each agent drives one simulator independently via the MCP tools.

### Prefer MCP tools over raw scripts
When the `sim-bridge` MCP server is connected, use `mcp__sim-bridge__sim_start` / `sim_act` / `sim_wait` / `sim_read` / etc. Cleaner tool integration and native MCP UX. Fall back to `sim_bridge.py` / `sim_start.sh` via Bash only when the MCP server isn't loaded.

### Shut down simulators on `goalAchieved`
When a sim test reaches `goalAchieved`, shut that simulator down immediately to free resources. Don't wait for sibling agents. Tear down as each one finishes.

## Scripts-only rule

Only use the pre-approved scripts (`sim_start.sh`, `sim_bridge.py`, `sim_cleanup.sh`, etc.) and the Read tool for sim test operations. Do NOT use raw `cat` / `echo` / variable assignments / piped shell commands to read or write sim-test files.

- Read config/state files → Read tool
- Interact with a running test → `python3 AuraConsumerAppSimTests/Scripts/sim_bridge.py`
- Start a test → `bash AuraConsumerAppSimTests/Scripts/sim_start.sh`
- Clean up → `bash AuraConsumerAppSimTests/Scripts/sim_cleanup.sh`

Raw bash commands trigger permission prompts and aren't pre-approved. The scripts and Read tool are pre-approved in `.claude/settings.json`.

## Action writing

### Use `tap` first, `tapScreen` only as fallback
Always try standard `tap` on an element identifier first. Only fall back to `tapScreen` with coordinates if the standard tap provably fails, AND you've validated the coordinates against a screenshot first. Coordinate taps without that validation are the source of many flaky and wrong-target bugs.

Process:
1. `tap` with the element identifier
2. If it fails or times out, take a screenshot to verify what's on screen
3. Only then use `tapScreen` with coordinates confirmed from `frames` output AND matching the screenshot

### Write defensive XCTest action code
Avoid patterns that crash XCTest. Known examples:
- `app.keys["delete"].tap()` can crash with `AXErrorCannotComplete` when the keyboard key isn't scrollable in XCTest context — use select-all + type-over instead of select + delete + type.
- Prefer atomic operations. Always consider what happens if an element exists but isn't interactable.
- Use coordinate-based taps as fallback for elements that report `isHittable == false`.

### Escape `!` as `\u0021` in sim_bridge JSON
When passing JSON to `sim_bridge.py` that contains an exclamation mark (passwords, text values), replace literal `!` with the Unicode escape `\u0021`. Literal `!` causes `Invalid \escape` errors from Python's JSON decoder through the shell argument parser.

```json
// ❌ breaks
{"value":"SimTest123!"}

// ✅ works
{"value":"SimTest123\u0021"}
```

### Wait for `audio_stopped` before advancing past audio-playing slides
The app exposes `audio_playing` and `audio_stopped` hidden accessibility elements via `.audioStatusAccessibility(ttsState:)`. Always wait for `audio_stopped` before tapping "Next" on value-prop slides, or you hit timing issues mid-playback.

## Explore mode

### Default goal = deep feature testing, not just navigation
When running `/sim-test` in explore mode, the default goal should exercise features, not just visit screens. Just navigating screens misses real bugs — the value is toggling settings and verifying persistence, typing invalid input and checking error messages, testing state after navigation.

Default explore goal:

> Explore the entire app with deep feature testing. Don't just visit screens — interact with every element, toggle settings and verify persistence, test input validation, check state after navigation. Report bugs, UX issues, and accessibility concerns.

Override only when the user specifies a narrower goal.

## Debugging failed taps / navigation

### Hierarchy dump first, source code second
When a tap, navigation, or element lookup fails unexpectedly, consult the raw XCTest hierarchy **before** reading SwiftUI source.

The hierarchy reveals the actual accessibility tree at runtime — element positions in scroll-content space, identifier propagation bugs, duplicate elements, off-screen coordinates. Source code alone can't show you what the runtime tree looks like.

Process:
1. Read `AuraConsumerAppSimTests/Sessions/<session>/Screenshots/adhoc_hierarchy_<step>.txt` for the failing step (or `sim_bridge.py hierarchy <step>`)
2. Check element frames (x, y, w, h) against screen height to detect off-screen issues
3. THEN read the relevant SwiftUI view source to understand why the tree looks that way
4. Only after both: propose the fix

## Post-run report format

Every sim test run generates a report with:

1. **Header** — result, steps, date, session ID, email, password (for reproducibility)
2. **Step table** — step #, screen name, action taken, description of what was seen/tapped
3. **Key findings** — what was verified, bugs/issues found, analyzer findings if any
4. **Observations** — A/B experiments hit, overlays, timing quirks, anything notable

Sub-agents driving parallel runs each produce this same report; the main agent aggregates them.

## Premium redemption bookkeeping

Whenever you call `POST /codes/QA_AURIE_SIM_TEST_1MONTH/redeem` (or any premium-grant API), IMMEDIATELY edit `AuraConsumerAppSimTests/users.json` to add the `premium` block (`active`, `grantedVia`, `grantedAt`, `expiresAt`, `productId`) for that user. Do not defer to end-of-run cleanup — if the test crashes before the write, the grant is effectively orphaned because we have no local record identifying the user as premium for future runs.

See `knowledge/workarounds.md § Persist users.json immediately on premium redemption`.

## Tracked-in-git files reminder

`AuraConsumerAppSimTests/users.json` and `AuraConsumerAppSimTests/simulators.json` are **tracked in git** (not gitignored). Any sim-test change that updates them must include them in the commit.
