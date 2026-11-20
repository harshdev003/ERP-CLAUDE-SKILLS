# Comparison runs — two prompt variants in parallel

When you want to compare two prompts (or the same prompt at two versions)
against the same conversation, run them as a comparison drive. Each variant
gets its own state file; the orchestrator parent owns the shared user-turn
arc; a subagent (`qa-prompt-variant-runner`) advances each variant by one
assistant turn at a time, in parallel.

## Setup (Phase 1 + 2, twice)

Run `setup_run.py` once per variant with **the same** `--mode`, `--scenario`,
and `--context`. The two variants can be:

- **Two different prompt URLs** — just pass the two URLs to two `setup_run.py`
  calls.
- **Same prompt URL at two different versions** — pass the same URL twice and
  use `--version <xact_id>` on each call to pin a different historical
  version. The `_xact_id` is the long integer shown next to each version in
  the Braintrust UI's version dropdown (e.g. `1000197167843336649`). Omit
  `--version` to use the latest.

```bash
RUN_DIR=outputs/qa-prompt-compare-<ts>
mkdir -p "$RUN_DIR/a" "$RUN_DIR/b"

# Example: same prompt, two versions
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/setup_run.py \
  <prompt-url> --version 1000197000000000000 \
  --mode interactive --scenario "..." --context dataset:<slug>:<row-id> \
  --state-file "$RUN_DIR/a/state.json"

python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/setup_run.py \
  <prompt-url> --version 1000197167843336649 \
  --mode interactive --scenario "..." --context dataset:<slug>:<row-id> \
  --state-file "$RUN_DIR/b/state.json"
```

The resolved `_xact_id` (whichever was fetched, pinned or latest) is stored
in `state.prompt.version` and surfaced in both the single-variant report and
the comparison report header + setup-parity table — so the report always
records exactly which version was tested.

Then set tool defaults — usually **the same kinds on both variants** so the
comparison is apples-to-apples on tool behavior too. Mirror each
`set_tool_default.py` call across both state files.

## Drive (Phase 3, in lockstep)

For each user turn:

1. **Parent composes one user message.** Compose it from the scenario plus
   the shared history *up to the last user turn*. Do NOT react to either
   variant's last assistant reply — that would bias one arm. The user turn
   needs to be the same across both variants so the comparison is fair.
   (Dataset-mode comparisons skip this step entirely; the seeded `input`
   messages are identical because the same dataset row was loaded into both
   states.)
2. **Parent appends the same user message to both state files.**
   ```bash
   echo '<user-text>' | python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/append_user_message.py --state-file "$RUN_DIR/a/state.json"
   echo '<user-text>' | python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/append_user_message.py --state-file "$RUN_DIR/b/state.json"
   ```
3. **Parent spawns two `qa-prompt-variant-runner` subagents in parallel** —
   one per variant. Each subagent is briefed with `state_file`, `label`,
   `repo_root`, and `max_tool_turns`. The subagent calls `send_turn.py` and
   any `apply_tool_default.py` calls needed for its variant, and returns a
   compact JSON with the assistant text + tool-call summary. It does NOT
   surface anything to chat — the parent does.
4. **Parent surfaces both replies side-by-side** to the user:
   ```
   **Turn N — user:** <text>

   **A — <label>:** <variant A assistant text>
   **B — <label>:** <variant B assistant text>
   ```
5. **If either subagent returns `pending_ask_per_call: true`**, the parent
   pauses, asks the human how to respond via `AskUserQuestion`, pipes the
   resolution through `append_tool_response.py` against the right state
   file, then re-spawns that variant's subagent to continue. The other
   variant keeps its progress.
6. **Loop or stop.** Same stop conditions as single-variant interactive
   mode — user interrupts, scenario completes naturally, or enough material
   has been collected for the comparison.

## Score (Phase 4, per variant)

Run `self_critique.py` against each state file independently. This gives
the comparison report a side-by-side rubric for the GPT grade.

## Comparison report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/write_comparison_report.py \
  --state-file-a "$RUN_DIR/a/state.json" \
  --state-file-b "$RUN_DIR/b/state.json" \
  --label-a "v3 (current prod)" --label-b "v4 (candidate)" \
  --analysis-file "$RUN_DIR/claude-comparison.md" \
  --output-dir outputs
```

The comparison report has a **Setup parity** check (flags if context source,
scenario, or model differ), the conversation transcript with both variants'
replies stacked per turn, tool defaults per variant, GPT critique scores
side-by-side, and Claude's qualitative comparison analysis (which should
focus specifically on *where the variants diverge*, not generic strengths
that apply to either).

You can still emit the single-variant reports too via `write_report.py` per
state file if you want individual deep-dives in addition to the comparison.

`write_comparison_report.py` also writes a **standalone conversation transcript
per variant** into `--output-dir` — `transcript-<label-a>.md` and
`transcript-<label-b>.md` (falling back to `transcript-A.md` / `transcript-B.md`
when no labels are given) — and prints a `TRANSCRIPT: <path>` line for each,
after the report path.

**Standing rule — always surface the transcripts.** End a comparison run by
surfacing **both** variants' full transcript files as clickable markdown links in
your final chat message, next to the side-by-side findings. The reviewer wants to
read each side's conversation in full, not only the scores or a summary. Never
deliver only results: the per-variant transcript links must always be present,
e.g. `📄 Transcripts: [control](outputs/.../transcript-control.md) · [treatment](outputs/.../transcript-treatment.md)`.
