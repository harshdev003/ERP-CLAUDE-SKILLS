---
name: run-apple-intelligence-prompt
description: >
  Iterate on an Apple Foundation Models prompt against one or more free-form inputs (a single
  conversation turn, a multi-turn conversation rendered as text, or a free-form description)
  and capture the on-device LLM outputs to a markdown results file. Each input is appended to
  the prompt under an `## Input` section and sent as the session's system instructions. Wraps
  a Swift Package CLI that calls Apple's on-device Foundation Models framework, so prompt
  iteration happens on the Mac with no device or Xcode UI required. Outputs are appended,
  never overwritten, to a per-prompt results file, so re-running builds up a history. Trigger
  on "run an Apple Intelligence prompt", "test this prompt against Foundation Models", "try
  this prompt on the Mac LLM", "iterate on the on-device prompt", "run the Foundation Models
  harness", or a pasted system prompt with a request to see what Apple's on-device model
  produces. Do NOT use for a Braintrust or OpenAI prompt (`eng:qa-prompt`).

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index           # baseline routing aid (universal)
---

# Run Apple Intelligence prompt

Fast inner loop for iterating on a prompt against Apple's on-device LLM
(Foundation Models framework, macOS 26 / iOS 26). The skill drives a Swift
Package CLI — `ApplePromptTester` — that calls
`LanguageModelSession.respond(...)` once per fixture and writes the outputs
back as JSON. Claude then appends a markdown section per run to a
prompt-scoped results file under the user's current project, so multiple
runs accumulate in one place for diffing.

Goal in one sentence: take a system prompt + zero-or-more user turns, run
them through Apple Foundation Models, and append the outputs to
`{cwd}/output/run-apple-intelligence-prompt/{prompt-slug}/results.md`.

## Prerequisites

The CLI runs the same framework iOS uses, so the host has to be capable:

- **macOS 26 (Tahoe)** on Apple Silicon, Apple Intelligence enabled in
  System Settings → Apple Intelligence & Siri.
- **Xcode 26+** installed from the App Store or developer.apple.com. The
  standalone Command Line Tools package is **not** enough — it lacks the
  Foundation Models framework.
- Active toolchain points at Xcode 26:
  `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`.

Quick verify (run once per machine; the skill prints these on first run):

```bash
sw_vers -productVersion              # 26.x
xcodebuild -version                  # Xcode 26.x
xcrun --show-sdk-path --sdk macosx   # ends in MacOSX26.sdk
```

If any check fails, surface the failure verbatim and stop — the swift build
will fail with an unhelpful error otherwise.

## Inputs

The skill collects two things from the user. Resolve both **before** running
swift:

1. **Prompt (required)** — the task description / instructions string. Value
   of the `INSTRUCTIONS` env var the swift binary reads. Ask the user if
   they didn't paste one. Accept multi-line content verbatim — do not
   summarise or reformat. Each input the user supplies will be appended to
   this prompt under a `## Input` section before being sent to the model.

2. **Input(s) (optional)** — one or more **free-form input blobs**. Each
   input is a single test case and can be any of:

   - **A single conversation turn** (one user utterance — e.g. "I had a
     rough day at work").
   - **A multi-turn conversation** rendered as text. Use whatever format
     the user pastes (`User:` / `Assistant:` lines, `>`-quoted, JSON, raw
     transcript — preserve it verbatim). The whole convo is one input, sent
     as one model call.
   - **A free-form description** (e.g. "a user venting about insomnia,
     asking for a grounding exercise"). Used when the prompt's task is
     about a hypothetical rather than literal text.

   Each input is appended verbatim to the prompt under a `## Input`
   heading; the concatenated text is passed as the session's
   `instructions` (system prompt), and `respond(to:)` is called with a
   single-space user trigger. Pinning the input to a known position in
   the system prompt keeps on-device prompt-cache behaviour stable and
   matches the Phase 2 iPhone harness (`qa-apple-intelligence-prompt`).
   The prompt defines the task, the input is the data.

   Decide the run shape from what the user provides:

   | What the user provides | Mode |
   |---|---|
   | Multiple inputs up front (numbered / separated / pasted as a list) | **Batch** — one swift call, one fixture per input, all in one fixtures file |
   | A single input, with intent to try more after seeing the output | **Loop** — run, show output, ask "another input?" until done |
   | Nothing — just the prompt | **Default fixtures** — use `<package-dir>/fixtures/baseline.json` if present; else ask for at least one input |

   Treat boundaries between inputs explicitly — if the user pastes one
   long multi-turn convo, that is **one** input, not many. Ask if it's
   ambiguous.

3. **Prompt-slug** — derived, not asked. If the user named the prompt
   (e.g. "call it `softer-v3`"), use that. Otherwise auto-derive: take the
   first non-empty line of the prompt, kebab-case it, cap at 40 chars. The
   slug names the output directory and is shown back to the user so they
   can reuse it on the next run.

If the prompt is missing, stop and ask. Never invent a prompt.

## Workflow

### 1. Locate or scaffold the ApplePromptTester package

Default location: `~/AuraHealth/ApplePromptTester`. Search order before asking:

```bash
for dir in \
  "$HOME/AuraHealth/ApplePromptTester" \
  "$HOME/AuraHealth/prompt-tester" \
  "$HOME/Work/prompt-tester"; do
  if [ -f "$dir/Package.swift" ]; then
    PKG_DIR="$dir"; echo "found at $PKG_DIR"; break
  fi
done
```

If none of those have a `Package.swift`:

- Confirm with the user before creating: *"No ApplePromptTester package found.
  Scaffold one at `~/AuraHealth/ApplePromptTester`?"*
- On yes: run the scaffold script.

```bash
${CLAUDE_PLUGIN_ROOT}/skills/run-apple-intelligence-prompt/scripts/scaffold_package.sh \
  "$HOME/AuraHealth/ApplePromptTester"
```

The scaffold script is idempotent — running it on an existing partial
scaffold fills in any missing pieces and never overwrites existing files.

### 2. Resolve the output path

The output file lives in the **user's current working directory** — not the
package dir, not the skill dir:

```
$(pwd)/output/run-apple-intelligence-prompt/<prompt-slug>/results.md
```

Compute it from the resolved `prompt-slug`. Create parent dirs lazily — the
Python script handles that.

### 3. Build the fixture file

A fixture is `{"id": <str>, "input": <str>}` where `input` is the verbatim
free-form text — single turn, multi-turn convo, or description. Newlines
in `input` are preserved (encode as `\n` in JSON). Build a JSON array — one
entry per input the user is supplying for this run. Write it to a temp
location:

- Batch mode → write all N inputs to a temp file `<tmpdir>/fixtures-<ts>.json`.
- Loop mode → write a single-entry fixture per swift call, then ask if the
  user wants another input.
- Default-fixtures mode → use `<package-dir>/fixtures/baseline.json`
  directly (no temp file). If the baseline file is in the old
  `{id, userTurn}` shape, migrate it in-place to `{id, input}` before
  running (rename the key; preserve values).

Auto-generate `id` from the input (slug of the first ~30 chars of the
first non-empty line). If the user named an input, use the named id
verbatim. IDs must be unique within a single fixtures file.

### 4. Run the swift binary

Delegate to the Python runner — it owns env wiring, error surface, and the
results.md append:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/run-apple-intelligence-prompt/scripts/run_prompt.py \
  --package-dir "$PKG_DIR" \
  --prompt-file "$PROMPT_FILE" \
  --fixtures-file "$FIXTURES_FILE" \
  --output-file "$(pwd)/output/run-apple-intelligence-prompt/<prompt-slug>/results.md" \
  --label "<prompt-slug>" \
  --temperature 0
```

`PROMPT_FILE` is a temp file containing exactly the prompt text — write
once per skill invocation, reuse across loop iterations.

**Always run at `temperature=0`** while iterating — changes the user sees
must be from the prompt, not sampling noise. Only flip to a non-zero
temperature if the user explicitly asks ("does it feel varied?"). Surface
the temperature in the appended results section.

### 5. Append + summarise

The runner appends a `## Run <timestamp> · prompt <hash> · temperature N`
section with the prompt (collapsed) and per-input output blocks. It returns
JSON with the resolved file path and the prompt hash.

After each run, print:

- The path to the appended results file (clickable markdown link).
- The 8-char prompt hash (for matching the run later).
- The number of inputs included.

In loop mode, then ask: *"Run another input against the same prompt? (paste
the input, or say 'done')"*. Each follow-up appends a fresh `## Run …`
section to the same file.

## Output

```
{cwd}/output/run-apple-intelligence-prompt/{prompt-slug}/results.md
```

One file per prompt-slug. Re-running with the same slug appends; re-running
with a different slug creates a new file. The file is plain markdown
designed to be skimmed and diffed (`diff <(grep -A3 '^**Output**' a.md) ...`).
Each block renders the input verbatim (including multi-turn convos) followed by
the model's **Output**.

**Standing rule — always surface `results.md` as a clickable link.** End every
run by giving the user a clickable markdown link to the `results.md` file in your
final message — it is the full transcript of every input and the model's output.
The reviewer wants to read the actual inputs and outputs, not only a summary.
Never deliver only a summary of the outputs: the `results.md` link must always be
present, e.g.
`📄 Full results: [results.md]({cwd}/output/run-apple-intelligence-prompt/{prompt-slug}/results.md)`.

## Edge cases

- **Prompt missing** → ask, don't guess.
- **Inputs missing AND no `fixtures/baseline.json`** → ask for at least one
  input. Do not run swift with an empty fixture array.
- **`swift run` fails** → surface stderr verbatim. Common causes (in
  decreasing order of likelihood): macOS < 26, Xcode CLI tools only (full
  Xcode 26 not installed), Apple Intelligence disabled in System Settings,
  toolchain not pointed at Xcode 26 (`xcode-select -p` should end in
  `Xcode.app/Contents/Developer`).
- **Re-run on same prompt + same inputs** → not a no-op by design. Each
  invocation appends a new `## Run` section, even if outputs are identical
  — useful for confirming determinism across runs.
- **User wants to compare prompt A vs prompt B** → run the skill twice
  with different prompt-slugs. Diff the two `results.md` files manually,
  or extract just the `**Output**` blocks with `grep`.
- **Cowork sandbox** → not supported. Foundation Models is an on-host
  framework; the swift binary cannot run inside the Linux container. Tell
  the user this and stop.

## Files

- [`scripts/scaffold_package.sh`](scripts/scaffold_package.sh) — creates
  the ApplePromptTester package if absent (idempotent).
- [`scripts/run_prompt.py`](scripts/run_prompt.py) — runs `swift run` and
  appends a run section to `results.md`.
- [`assets/Package.swift.template`](assets/Package.swift.template) — the
  SwiftPM manifest the scaffold drops in.
- [`assets/main.swift.template`](assets/main.swift.template) — the
  `LanguageModelSession` call site. Reads `INSTRUCTIONS`, `FIXTURES`,
  `RESULTS_JSON`, `TEMPERATURE` from env.

## Reference

- [PROD-1944](https://linear.app/aura-health/issue/PROD-1944/foundation-models-phase-1-mac-cli-prompt-iteration-harness)
  — Phase 1 spec (host setup, package layout, inner-loop cycle).
- Parent: [PROD-1910](https://linear.app/aura-health/issue/PROD-1910/mobile-local-llm-integration).
- Apple Foundation Models framework — WWDC 2025 session + developer docs.
