---
name: qa-apple-intelligence-prompt
description: >
  Validate an Apple Foundation Models prompt against one or more free-form inputs (a single
  conversation turn, a multi-turn conversation rendered as text, or a free-form description)
  on a real iPhone. Each input is appended to the prompt under an `## Input` section before
  being sent. Wraps the ApplePromptTesterIOS Xcode harness: builds the standalone test app,
  installs it on a paired Apple Intelligence-capable iPhone, runs the fixture set on device
  and appends a run section to a per-prompt results file so runs accumulate into a parity log.
  Captures first-call, p50 and p95 latency per run. Trigger on "qa the apple intelligence
  prompt on device", "validate this prompt on an iPhone", "run the Foundation Models device
  harness", "test this on-device prompt on iOS", or a pasted prompt with a request to see how
  Apple's on-device model behaves on a real phone. Do NOT use for Mac iteration
  (`eng:run-apple-intelligence-prompt`) or a Braintrust prompt (`eng:qa-prompt`). Also
  triggers on "phase 2 device validation".

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index           # baseline routing aid (universal)
---

# QA Apple Intelligence prompt (on-device, iPhone)

Phase 2 of the on-device LLM rollout from
[PROD-1945](https://linear.app/aura-health/issue/PROD-1945). The Mac
inner loop (`eng:run-apple-intelligence-prompt`) gives the right prompt
content; this skill confirms the prompt behaves the same — and within
the right latency envelope — on the smaller hardware the real app will
run on.

Goal in one sentence: take a system prompt + zero-or-more free-form
inputs, run them through Apple Foundation Models on a paired iPhone,
and append the outputs (plus per-fixture latency) to
`{cwd}/output/qa-apple-intelligence-prompt/{prompt-slug}/results.md`.

## Prerequisites

The on-device harness needs both Mac-side tooling and an iPhone:

- **Mac:** macOS 26 (Tahoe), Apple Silicon, Xcode 26+ installed and
  selected (`sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`).
- **iPhone:** iPhone 15 Pro / Pro Max OR any iPhone 16+ on iOS 26.x with
  Apple Intelligence enabled in Settings → Apple Intelligence & Siri
  (the ~3 GB model download must have completed over Wi-Fi). Older
  phones can't run the model — fail fast.
- **Device paired** with the Mac and trusted. Verify with:

  ```bash
  xcrun devicectl list devices
  ```

  The phone must appear with `available` state. If it's listed but
  unavailable, plug it in via USB-C, unlock, tap *Trust This Computer*.

- **`ApplePromptTesterIOS` Xcode project** at
  `~/AuraHealth/ApplePromptTester/ios/`. The Swift sources are
  scaffolded by this skill, but the wrapping `.xcodeproj` is created
  **once, manually**, in Xcode — see
  [`references/setup-xcode-project.md`](references/setup-xcode-project.md).
  First-run setup is ~3 minutes and only needed once per Mac.

If any check fails, surface the failure verbatim and stop. Do not
attempt to install Xcode, enable Apple Intelligence, or pair a device
on the user's behalf.

## Inputs

The skill collects two things from the user. Resolve both **before**
invoking `xcodebuild` or `devicectl`:

1. **Prompt (required)** — the task description / instructions string.
   Ask the user if they didn't paste one. Accept multi-line content
   verbatim — do not summarise or reformat. Each input the user
   supplies is appended to this prompt under a `## Input` section
   before being sent to the model (see "Runtime shape" below).

2. **Input(s) (optional)** — one or more **free-form input blobs**.
   Each input is a single test case and can be any of:

   - **A single conversation turn** (one user utterance — e.g. "I had
     a rough day at work").
   - **A multi-turn conversation** rendered as text. Use whatever
     format the user pastes (`User:` / `Assistant:` lines, `>`-quoted,
     JSON, raw transcript — preserve it verbatim). The whole convo is
     **one** input, sent as one model call.
   - **A free-form description** (e.g. "a user venting about insomnia,
     asking for a grounding exercise"). Used when the prompt's task is
     about a hypothetical rather than literal text.

   Decide the run shape from what the user provides:

   | What the user provides | Mode |
   |---|---|
   | Multiple inputs up front (numbered / separated / pasted as a list) | **Batch** — one device run, one fixture per input, all in one payload |
   | A single input, with intent to try more after seeing the output | **Loop** — run, show output + latency, ask "another input?" until done |
   | Nothing — just the prompt | **Default fixtures** — use `~/AuraHealth/ApplePromptTester/fixtures/baseline.json` from the Phase 1 corpus if present; else ask for at least one input |

   Treat boundaries between inputs explicitly — if the user pastes one
   long multi-turn convo, that is **one** input, not many. Ask if it's
   ambiguous.

3. **Prompt-slug** — derived, not asked. If the user named the prompt
   (e.g. "call it `softer-v3`"), use that. Otherwise auto-derive: take
   the first non-empty line of the prompt, kebab-case it, cap at 40
   chars. The slug names the output directory and is shown back to the
   user so they can reuse it on the next run.

If the prompt is missing, stop and ask. Never invent a prompt.

## Runtime shape

At runtime the on-device app builds, for each fixture, a combined
instructions string:

```
<prompt>

## Input
<input>
```

That combined string is passed as `instructions` to
`LanguageModelSession(...)`, and `respond(to:)` is called with a
minimal trigger so the model generates a response. The prompt and the
input are both visible to the model as one structured system prompt —
no separate user message contains the input. This matches the
"prompt defines the task, input is the data" pattern used by the
Phase 1 Mac skill, with the input pinned to a known position in the
system prompt for stable on-device prompt-cache behaviour.

## Workflow

### 1. Locate or scaffold the iOS sources

The Swift source files live at
`~/AuraHealth/ApplePromptTester/ios/ApplePromptTesterIOS/`. On first
run, scaffold them:

```bash
${CLAUDE_PLUGIN_ROOT}/skills/qa-apple-intelligence-prompt/scripts/scaffold_ios_sources.sh \
  "$HOME/AuraHealth/ApplePromptTester/ios"
```

The scaffold is idempotent — existing files are left in place.

**If the wrapping `.xcodeproj` does not exist yet**, the next step
will fail. Surface
[`references/setup-xcode-project.md`](references/setup-xcode-project.md)
verbatim and stop. The user creates the Xcode project once, signs it
with their development team, and re-invokes the skill.

### 2. Discover the target device

```bash
xcrun devicectl list devices --json-output -
```

- If exactly one iOS device is `available`, use it.
- If multiple are available, ask the user to pick by name.
- If none are available, surface the JSON output verbatim and stop —
  the user has to plug in, unlock, and trust.

Store the device's `identifier` for `devicectl` calls.

### 3. Resolve the output path

The results file lives under the **user's current working directory** —
not the project dir, not the skill dir:

```
$(pwd)/output/qa-apple-intelligence-prompt/<prompt-slug>/results.md
```

Compute it from the resolved `prompt-slug`. Parent dirs are created
lazily by the runner.

### 4. Build the fixture payload

A fixture is `{"id": <str>, "input": <str>}` where `input` is the
verbatim free-form text — single turn, multi-turn convo, or
description. Newlines in `input` are preserved (encode as `\n` in
JSON). Build the full pending-run payload the app reads on launch:

```json
{
  "instructions": "<prompt text>",
  "temperature": 0,
  "fixtures": [
    {"id": "groggy-morning", "input": "User just woke up groggy and stressed about work."},
    {"id": "multi-turn-tough-day", "input": "User: I had a tough day.\nAurie: I'm sorry. What happened?\nUser: My boss yelled at me."}
  ]
}
```

Auto-generate `id` from the input (slug of the first ~30 chars of the
first non-empty line). If the user named an input, use the named id
verbatim. IDs must be unique within a single payload.

- Batch mode → one payload with all inputs, one device run.
- Loop mode → one payload per device run; ask the user "another
  input?" after showing the output.
- Default-fixtures mode → load fixtures from
  `~/AuraHealth/ApplePromptTester/fixtures/baseline.json` if present,
  else ask for at least one input. If the baseline file is in the old
  `{id, userTurn}` shape, migrate it in-place to `{id, input}` before
  running (rename the key; preserve values).

### 5. Run on device + append to results.md

Delegate to the Python runner — it owns the build, install, launch,
poll, fetch, and append:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-apple-intelligence-prompt/scripts/run_prompt_ios.py \
  --project-dir "$HOME/AuraHealth/ApplePromptTester/ios" \
  --device-id "<resolved device identifier>" \
  --payload-file "<tmp pending-run.json>" \
  --output-file "$(pwd)/output/qa-apple-intelligence-prompt/<prompt-slug>/results.md" \
  --label "<prompt-slug>" \
  --temperature 0
```

What the runner does, in order:

1. `xcodebuild -project ApplePromptTesterIOS.xcodeproj -scheme ApplePromptTesterIOS -destination 'id=<device>' -derivedDataPath <tmp> build` — build for the paired iPhone.
2. `xcrun devicectl device install app --device <id> <built .app>` — install/replace the app.
3. `xcrun devicectl device copy to --device <id> --domain-type appDataContainer --domain-identifier <bundle-id> --source <payload.json> --destination /Documents/pending-run.json` — push the prompt + fixtures.
4. `xcrun devicectl device process launch --device <id> --terminate-existing --start-stopped=false <bundle-id>` — launch foreground.
5. Poll `Documents/results.json` on device (via `devicectl device info files`) until it appears or a timeout (default 180s).
6. `xcrun devicectl device copy from --device <id> --domain-type appDataContainer --domain-identifier <bundle-id> --source /Documents/results.json --destination <tmp results.json>` — pull results.
7. Parse `results.json` → append a `## Run <ts> · prompt <hash> · device <name> · temperature N` section to the output file with per-fixture id / input / output / latencyMs blocks plus a small latency summary (first-call ms, p50 ms, p95 ms, count). Clean up the device-side `pending-run.json` and `results.json`.

**Always run at `temperature=0`** while iterating — changes the user
sees must come from the prompt, not sampling noise. Surface the
temperature in the appended section. Only flip to non-zero if the user
explicitly asks.

### 6. Summarise

After each run, print:

- The path to the appended results file (clickable markdown link).
- The 8-char prompt hash.
- The number of inputs included.
- One-line latency line: `first-call N ms · p50 N ms · p95 N ms`.

In loop mode, then ask: *"Run another input against the same prompt?
(paste the input, or say 'done')"*. Each follow-up appends a fresh
`## Run …` section to the same file.

## Output

```
{cwd}/output/qa-apple-intelligence-prompt/{prompt-slug}/results.md
```

One file per prompt-slug. Re-running with the same slug appends; a
different slug creates a new file. Each per-fixture block renders the
input under an **Input** code block (which may contain multi-line
multi-turn text) followed by the model's **Output**. Designed to be
skimmed and diffed against a Phase 1 Mac results file (`diff <(grep
-A3 '^**Output**' mac.md) <(grep -A3 '^**Output**' ios.md)`) — that
diff is the Mac ↔ iOS parity check the Linear ticket calls for.

**Standing rule — always surface `results.md` as a clickable link.** End every
run by giving the user a clickable markdown link to the `results.md` file in your
final message — it is the full transcript of every input (verbatim, including
multi-turn convos) and the model's output. The reviewer wants to read the actual
inputs and outputs, not only a summary. Never deliver only a summary of the
outputs: the `results.md` link must always be present, e.g.
`📄 Full results: [results.md]({cwd}/output/qa-apple-intelligence-prompt/{prompt-slug}/results.md)`.

## Edge cases

- **Prompt missing** → ask, don't guess.
- **Inputs missing AND no fixtures/baseline.json** → ask for at least
  one input. Do not push an empty fixture array to the device.
- **Ambiguous input boundaries** → if the user pastes one long block
  that could be one multi-turn input *or* several separate inputs,
  ask before splitting. Default to treating it as one input.
- **`.xcodeproj` missing** → surface
  [`references/setup-xcode-project.md`](references/setup-xcode-project.md)
  and stop. Don't try to generate the project file ourselves.
- **No device available** → print `xcrun devicectl list devices`
  output verbatim and stop. Don't pick a random device.
- **`xcodebuild` fails on signing** → most likely cause is no
  development team set on the project. Tell the user to open the
  project in Xcode once, pick a team under *Signing & Capabilities*,
  re-invoke. Don't attempt to inject a team via `CODE_SIGN_IDENTITY` —
  it varies per developer.
- **`devicectl copy to` fails with "container not found"** → the app
  hasn't been launched at least once on this device. Launch once
  (step 4), then re-push (step 3). The runner already retries this
  pattern once.
- **`results.json` not produced within timeout** → surface the device
  console log path (`xcrun devicectl device console --device <id>`)
  and stop. Common causes: Apple Intelligence not enabled in Settings,
  model still downloading, app crashed on a malformed fixture.
- **Re-run on same prompt + same inputs** → not a no-op by design.
  Each invocation appends a new `## Run` section, even if outputs
  match — useful for confirming determinism across devices or across
  builds.
- **Cowork sandbox** → not supported. `xcodebuild` and `devicectl`
  are Mac-host-only tools; neither runs inside the Linux container.
  Tell the user this and stop.

## Files

- [`scripts/scaffold_ios_sources.sh`](scripts/scaffold_ios_sources.sh)
  — writes the Swift source templates into
  `<project-dir>/ApplePromptTesterIOS/` (idempotent).
- [`scripts/run_prompt_ios.py`](scripts/run_prompt_ios.py) — drives
  `xcodebuild` + `xcrun devicectl`, waits for results, appends the
  run section to `results.md`.
- [`assets/ApplePromptTesterIOSApp.swift.template`](assets/ApplePromptTesterIOSApp.swift.template)
  — SwiftUI app entry point.
- [`assets/ContentView.swift.template`](assets/ContentView.swift.template)
  — view + Foundation Models call site. Reads
  `Documents/pending-run.json`, builds combined `<prompt>\n\n## Input\n<input>`
  instructions per fixture, writes `Documents/results.json` with
  per-fixture `latencyMs`.
- [`references/setup-xcode-project.md`](references/setup-xcode-project.md)
  — one-time Xcode project setup walkthrough (signing team,
  deployment target, adding source files, paired-device build).

## Reference

- Phase 2 spec: [PROD-1945](https://linear.app/aura-health/issue/PROD-1945/foundation-models-phase-2-ios-device-validation-harness).
- Phase 1 sibling skill: `eng:run-apple-intelligence-prompt`
  (`plugins/eng/skills/run-apple-intelligence-prompt/SKILL.md`) —
  same input contract (free-form `input` per fixture, appended under
  `## Input`), Mac-side execution, faster iteration loop.
- Parent: [PROD-1910](https://linear.app/aura-health/issue/PROD-1910/mobile-local-llm-integration).
- Apple Foundation Models framework — WWDC 2025 session + developer
  docs.
