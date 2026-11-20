# Parallel Testing

Run multiple sim test sessions simultaneously on different simulators. Each session is fully independent — separate simulator, separate session directory, separate XCTest runner.

## When to Use

- **Cross-device validation** — run the same flow on iPhone 17 Pro and iPhone 15 Pro to catch device-specific layout or behavior issues.
- **Parallel flow coverage** — run onboarding on one simulator while exploring post-login features on another.
- **Time savings** — two 15-minute onboarding runs complete in ~15 minutes instead of ~30.

## Prerequisites

- Each session needs its own **simulator**. The scripts handle this: each session's is named `"<model> (<label>)"` and created on first use (see [Device Configuration](#device-configuration)).
- The app must be **built and installed on each simulator** independently.
- Enough system resources — each simulator + XCTest runner uses ~2-3 GB RAM.

## Architecture

```
Main conversation
├── Agent 1 (sim-17pro) ← "iPhone 17 Pro (run-a)"
│   ├── sim_test.sh --session $SID1
│   └── sim_bridge.py --session $SID1 act/wait/read
└── Agent 2 (sim-15pro) ← "iPhone 15 Pro (run-b)"
    ├── sim_test.sh --session $SID2
    └── sim_bridge.py --session $SID2 act/wait/read
```

Each agent owns one session end-to-end. Sessions are isolated by UDID — state.json, action.json, screenshots, and config all live in `Sessions/{UDID}/`.

## Step-by-Step

### 1. Boot simulators

Give each session its own label and pick the model with `--device-type`. Each call creates (or reuses) a simulator named `"<model> (<label>)"`, and never shuts down anyone else's. Use `--reset` for clean onboarding runs.

```bash
SID1=$(SIM_SESSION_NAME=run-a bash AuraConsumerAppSimTests/Scripts/sim_start.sh --reset | tail -1)
SID2=$(SIM_SESSION_NAME=run-b bash AuraConsumerAppSimTests/Scripts/sim_start.sh --device-type "iPhone 15 Pro" --reset | tail -1)
```

Everything after this takes `--session "$SIDn"`, which pins each script to exactly that device.

Parallel sessions in **separate worktrees** need no `SIM_SESSION_NAME`: the label defaults to the worktree name. Set it only when several sessions share one checkout.

### 2. Build and install on each

The build step compiles once; install targets each simulator by session ID.

```bash
# Build + install on first simulator
bash AuraConsumerAppSimTests/Scripts/sim_install.sh --session "$SID1"

# Install on second simulator (reuses the build artifact)
bash AuraConsumerAppSimTests/Scripts/sim_install.sh --session "$SID2"
```

**Note:** `sim_install.sh` runs `xcodebuild build-for-testing` which locks `DerivedData-CLI/build.db`. Run installs sequentially, not in parallel. The build output is reused for the second install.

### 3. Spawn one agent per session

**MANDATORY:** Always spawn one agent per session. Never orchestrate two command loops from a single conversation — the interleaved state reads and action writes will collide.

**MANDATORY: Keep agent prompts CONCISE.** This is the #1 cause of agent failure in parallel testing. Agents that read large reference files (knowledge/, actions.md, screen-guide.md) exhaust their context window after ~15-20 steps and die mid-onboarding, killing the XCTest runner.

**Concise prompt rules:**
- **Inline all critical rules** directly in the prompt (clearFirst, audio waits, iOS-specific quirks, quiz answers)
- **Inline the exact flow steps** (splash→welcome→value props→quiz→notification→signup→tutorial→home)
- **DO NOT tell agents to read** knowledge/, actions.md, screen-guide.md, or personas.md
- **Keep prompts under ~2000 words** — every word counts against the agent's working context
- **DO NOT read screenshots** unless stuck — each image consumes significant context

Each agent prompt must include (inlined, not by reference):
- **Session ID** (full UDID_timestamp)
- **Email and password** from config
- **Persona quiz answers** as exact option indices
- **iOS-version-specific rules** (e.g., handlePermission for iOS 18.x, clearFirst:false for iOS 17.2)
- **Goal and mode**

Example agent spawn (both in the same message for parallel launch):

```
Agent 1:
  name: sim-17pro
  description: Sim test onboarding iPhone 17 Pro
  run_in_background: true
  prompt: "Session D8707D14-..., iPhone 17 Pro, onboarding goal..."

Agent 2:
  name: sim-15pro
  description: Sim test onboarding iPhone 15 Pro
  run_in_background: true
  prompt: "Session 383E98F0-..., iPhone 15 Pro, onboarding goal..."
```

### 4. Each agent runs independently

Inside each agent, the workflow is identical to a single-session run:

1. Load `knowledge/` (split into `rules.md`, `flows.md`, `workarounds.md`, `screens/*.md`) + reference docs
2. Launch `sim_test.sh` in background with `--session`
3. `sim_bridge.py --session $SID wait 0` for initial state
4. Run the observe → plan → act loop via `sim_bridge.py --session $SID act <step> '<json>'`
5. Continue until terminal state (`goalAchieved`, `loopDetected`, `crashed`, `timeoutGraceful`)
6. Report results

### 5. Collect results

When both agents complete, the main conversation receives their results. Compare:

- **Step counts** — did one device take more steps? (may indicate layout differences)
- **Findings** — device-specific issues (e.g., text truncation on smaller screen)
- **Failures** — did one device hit a different error?

### 6. Cleanup

To abort one run early, kill only that session's processes — `sim_kill.sh` and
`sim_record.sh stop` match by the session's UDID and never touch the other agents' runs:

```bash
bash AuraConsumerAppSimTests/Scripts/sim_kill.sh --session "$SID1" all
bash AuraConsumerAppSimTests/Scripts/sim_record.sh stop --session "$SID1"
```

Shut down and clean each session:

```bash
bash AuraConsumerAppSimTests/Scripts/sim_shutdown.sh --session "$SID1"
bash AuraConsumerAppSimTests/Scripts/sim_shutdown.sh --session "$SID2"
bash AuraConsumerAppSimTests/Scripts/sim_cleanup.sh
```

## Session Isolation

Each session is fully isolated by UDID:

```
Sessions/
├── D8707D14-1F7A-4C11-98CD-D007861CCD80/   ← iPhone 17 Pro
│   ├── config.json
│   ├── state.json
│   ├── action.json
│   └── Screenshots/
└── 383E98F0-52C3-4CFE-AF17-E8F3252BCD06/   ← iPhone 15 Pro
    ├── config.json
    ├── state.json
    ├── action.json
    └── Screenshots/
```

No shared files between sessions. Each `sim_bridge.py --session $SID` reads/writes only within its own session directory.

## Device Configuration

Every session gets its **own** simulator, named `"<model> (<label>)"`, e.g. `iPhone 17 Pro (frosty-golick-68d358)`. The scripts create it with `xcrun simctl create` on the pinned runtime (`SIM_OS_VERSION`, default 26.2) the first time, then reuse it.

| Input | Effect |
|-------|--------|
| *(nothing)* | This session's `iPhone 17 Pro (<label>)` (`DEFAULT_DEVICE_TYPE` in `sim_common.sh`) |
| `SIM_SESSION_NAME=<label>` | Overrides the label (default: worktree name, `ci` on CI, `main` in the primary checkout) |
| `--device-type "iPhone 15 Pro"` | This session's `iPhone 15 Pro (<label>)` |
| `--session <UDID>` | Exactly that device. Its name comes from the device, and nothing else is looked up |
| `--sim "<exact name>"` | That exact device. A shared name like `"iPhone 17 Pro"` may be another session's |

`shutdown_stale_sims` only shuts down stale copies of the session's **own** simulator, never a device another session could be using. The device name is recorded in `config.json` for traceability. `simulators.json` still tracks coverage per **model** (`device`), so per-session names don't split its entries.

Delete throwaway simulators when you are done with them: `xcrun simctl delete <UDID>`, for UDIDs you created only.

## Common Pitfalls

| Pitfall | Fix |
|---------|-----|
| Orchestrating two loops from one conversation | Always spawn one agent per session |
| Running `sim_install.sh` in parallel | Run sequentially — shared build DB lock |
| Passing `--sim "iPhone 17 Pro"` or grepping `Booted` for a UDID | Both pick up a device another session may own. Use the session's own simulator (the default) and its `$SID` |
| Two sessions in one checkout with no `SIM_SESSION_NAME` | Both get the same label, so they share a simulator. Set a distinct `SIM_SESSION_NAME` for each |
| Forgetting `--session` on bridge calls | Agent reads wrong session's state.json |
| `pkill -f xcodebuild`, `kill` by pattern, or `ps … \| grep testmanagerd \| xargs kill` | Kills every session's runner. Use `sim_kill.sh --session "$SIDn" test\|record\|log\|all\|testmanagerd`, which only matches that session's processes and refuses when it can't tell which session is yours |
| Not enough RAM for two simulators | Close Xcode IDE, other apps; need ~6 GB free |
| Same email on both sessions | Each `sim_test.sh` auto-generates a unique email with timestamp |
| Comparing results without device context | `config.json` records the `device` field for each session |
| Agent exhausts context reading docs | Keep agent prompts concise — include key workarounds inline, don't tell agent to read 3+ large files first. If agent runs out of context, the XCTest runner times out waiting for the next command and the session dies. |
| Agent exhausts context from verbose state | Compact mode is now the default. Use `--verbose` only for debugging. Without compact, agents exhaust context after ~15–20 steps. |
| Agent reads screenshots wasting context | In compact mode (default), `screenshotPath` is stripped. Only read screenshots when stuck — each image consumes significant context. |
| XCTest runner dies while agent is slow | XCTest has a `timeoutPerStep` (default 120s). If the agent takes too long between actions (reading docs, analyzing screenshots), the runner may timeout. Spawned continuation agents must act quickly. |
| Agent output file too large to read | Agent JSONL output files can exceed 60K+ tokens. Use `tail -5 <file>` + `python3 json.loads()` or `grep` to extract final results — never read the full file. |
| Sub-agent wastes tokens on verbose output | Always minimize token usage in sub-agent conversations: use compact mode (default), avoid reading screenshots unless stuck, avoid reading large reference files, use `tail`/`grep` instead of full file reads for results. |

## Token Budget Management (MANDATORY)

**Always minimize token usage** in sub-agent conversations and when reading agent output. Agents have finite context windows — every wasted token brings them closer to dying mid-flow.

### Agent Output Files

Agent JSONL output files grow large (60K+ tokens) and **cannot be read in full**. Extract results with:

```bash
# Get the final result from an agent's output file
tail -5 /path/to/agent_output.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        obj = json.loads(line.strip())
        if obj.get('type') == 'result':
            print(obj.get('result', {}).get('text', ''))
    except: pass
"

# Search for specific content
grep -i "goalAchieved\|SUCCESS\|FAIL\|steps" /path/to/agent_output.jsonl | tail -5
```

### Sub-Agent Token Discipline

- **Compact mode** is the default for `sim_bridge.py` — never use `--verbose` in agent prompts
- **Never tell agents to read** knowledge/, actions.md, screen-guide.md, or personas.md — inline the critical rules
- **Never read screenshots** unless stuck — each image consumes significant context
- **Keep agent prompts under ~2000 words** — every word counts against the agent's working context
- **Shut down simulators immediately** when goal is achieved — don't wait for other agents

## iOS Version Requirements

**The app requires iOS 17.0+** as its deployment target. Sim tests have been validated across iOS 17.2, 18.3, and 26.2 in parallel (6 devices, all passed onboarding). Older iOS versions have known workarounds documented in knowledge/.

**iOS version quirks:**
- **iOS 17.2**: `isHittable == false` for all elements (cosmetic), `clearFirst:false` required for all text fields, secure field tap-dismiss-retap workaround needed, tutorial overlay tap hazard near CTA/arrow buttons
- **iOS 18.3**: `handlePermission` action required after notification permission tap (springboard dialog blocks XCTest)
- **iOS 26.x**: Cleanest experience — system dialogs auto-handled, all elements hittable

**Known issue:** iPhone 15 Pro with iOS 17.2 — value props `CircularIconButton` with `.glassBackgroundCircle()` reports `hittable: false` for all buttons. Coordinate taps report success but don't trigger SwiftUI actions. Root cause: UIKit video layer (`SeekableVideoView`) interaction with SwiftUI button hit testing on older iOS.

**Before running parallel tests**, verify simulator iOS versions:
```bash
xcrun simctl list devices available -j | python3 -c "
import json, sys
data = json.load(sys.stdin)
for rt, devs in data.get('devices', {}).items():
    for d in devs:
        if d.get('isAvailable') and 'iPhone' in d.get('name', ''):
            version = rt.split('iOS-')[-1].replace('-', '.')
            print(f'{d[\"name\"]}: iOS {version} ({d[\"state\"]})')
"
```

If only iOS 17.x simulators are available for a device, **do not use that device** for sim tests. Download a newer runtime via Xcode > Settings > Platforms.

## Supported Device Combinations

Any available simulators can run in parallel. Up to 6 simultaneous sessions validated (requires ~12-18 GB free RAM).

**Validated 6-device parallel run (2026-04-07):**

| Device | iOS | Steps | Result |
|--------|-----|-------|--------|
| iPhone 15 Pro | 17.2 | 37 | ✅ SUCCESS |
| iPhone 16 Pro | 18.3 | 42 | ✅ SUCCESS |
| iPhone 16e | 26.2 | 54 | ✅ SUCCESS |
| iPhone 17 | 26.2 | 34 | ✅ SUCCESS |
| iPhone 17 Pro | 26.2 | 37 | ✅ SUCCESS |
| iPad Pro M5 | 26.2 | 40 | ✅ SUCCESS + landscape |

Common pairs:

| Device 1 | Device 2 | Use case |
|----------|----------|----------|
| iPhone 17 Pro | iPhone 16 Pro | Current vs previous gen |
| iPhone 17 Pro | iPhone 15 Pro | iOS 26.x vs 17.2 quirks |
| iPhone 17 Pro | iPad Pro M5 | Phone vs tablet + landscape |

List available simulators:
```bash
xcrun simctl list devices available | grep iPhone
```
