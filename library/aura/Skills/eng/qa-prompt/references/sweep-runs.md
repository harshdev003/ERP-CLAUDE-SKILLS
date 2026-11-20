# Sweep runs — one prompt, N scenarios in parallel

When you want to QA a single prompt against N scenarios (one prompt version,
many personas), run them as a sweep. Each scenario gets its own state file;
the orchestrator parent spawns N parallel `qa-prompt-sweep-runner` subagents
that drive their scenario end-to-end and return structured summaries; the
parent aggregates them into a cross-scenario report.

This is the right shape for pre-ship QA of a conversational prompt across
emotional registers (stressed / anxious / grieving / depleted / etc.). It's
different from comparison mode (which is one scenario across two variants —
see [`comparison-runs.md`](comparison-runs.md)) and different from a single
drive (one prompt, one scenario — see the main SKILL.md).

## Setup (Phase 0 + 1)

`setup_sweep.py` fans the prompt out across N scenarios in one call —
resolves the prompt + context once, mirrors them across all scenarios so the
comparison is fair, and writes per-scenario state files plus a sweep
manifest:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/setup_sweep.py \
  <braintrust-prompt-url> \
  --scenario "stressed=Mid-30s parent, hard day at work, colleague took credit..." \
  --scenario "anxious=Late 20s, 10pm, ruminating about an awkward conversation..." \
  --scenario "grieving=User in their 40s, processing loss of a complicated parent..." \
  --scenario "lowenergy=Exhausted user, terse one-liners, 'had a day' opener..." \
  --no-context \
  --run-dir outputs/qa-prompt-sweep-<slug>-<ts>
```

Inline `--scenario label=description` is repeatable. For larger sweeps
or anything that contains quotes, use `--scenarios-file path/to/file.json`
with a JSON array of `{label, scenario}` objects.

Output: the run directory path on stdout. Inside it:

- `sweep.json` — the manifest (prompt + version + scenarios + state-file
  paths). Single source of truth for the parent + the sweep report writer.
- `<label>/state.json` per scenario — same shape as a single-drive state
  file, all interactive mode, all the same context.

## Tool defaults (Phase 2, mirrored across scenarios)

Decide tool defaults once and mirror them across all N state files —
identical strategies on every scenario so behavioural differences trace
to the prompt, not to tool-response variance. Loop:

```bash
for sc in stressed anxious grieving lowenergy; do
  for tool in handoffToGuardrails createInsightCard recommendTracks findSimilarInMemory storeInMemory; do
    python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/set_tool_default.py \
      --state-file "$RUN_DIR/$sc/state.json" --tool-name "$tool" --kind auto-mock
  done
done
```

Auto-mock is the default choice for sweeps — fastest, deterministic, isolates
prompt behaviour from tool integration concerns.

## Drive (Phase 3, parallel subagents)

For each scenario in `sweep.json`, the orchestrator spawns one
`qa-prompt-sweep-runner` subagent — all N spawned in a single message so
they run concurrently. Each subagent:

1. Composes the opener in the voice of its scenario.
2. Loops `send_turn` → `apply_tool_default` per tool call → next user turn,
   until natural close or a hard 6-user-turn cap.
3. Runs `self_critique.py` against its state file.
4. Returns a compact JSON summary (label, turns, stop reason, tools called,
   final assistant message, critique scores, brief notes).

The exact subagent prompt template — load-bearing for sweep reliability —
is at [`sweep-subagent-prompt.md`](sweep-subagent-prompt.md). The agent
definition is at
[`plugins/eng/agents/qa-prompt-sweep-runner.md`](../../../agents/qa-prompt-sweep-runner.md).

**Spawn all N in one message.** Sweep parallelism is the whole point — N
sequential drives is just N single-drive runs in a trench coat. Use the
`Agent` tool with `subagent_type: "qa-prompt-sweep-runner"` and put every
scenario's call in the same message.

If a subagent returns `pending_ask_per_call: true`, the parent resolves the
tool call via `AskUserQuestion`, pipes the resolution through
`append_tool_response.py` against that scenario's state file, then re-spawns
that scenario's subagent to continue.

## Score (Phase 4, already done by subagent + cross-scenario report)

Self-critique was already run by each `qa-prompt-sweep-runner` against its
own state file. The parent then writes the aggregated report:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/write_sweep_report.py \
  --run-dir <run-dir> \
  --analysis-file <run-dir>/claude-cross-analysis.md
```

The report includes a scoreboard (label / turns / stop reason / tools called
/ 4-axis critique scores per scenario), the cross-scenario Claude analysis
(passed via `--analysis-file`), and per-scenario sections with transcripts +
per-scenario critiques + per-scenario Claude analyses (read from
`<label>/claude-analysis.md` if present). The per-scenario transcript shown
inside the aggregated body is capped per message for skimmability.

`write_sweep_report.py` also writes the **full, untruncated conversation for
each scenario** to `<run-dir>/<label>/transcript.md`, and prints a
`TRANSCRIPT: <path>` line per scenario after the report path. (The cap only
applies to the at-a-glance body — these standalone files are complete.)

Per-scenario deep-dive reports are still useful for the individual readouts —
write them with `write_report.py --label <label>` so the filenames don't
collide.

**Standing rule — always surface the transcripts.** End a sweep by surfacing
the full per-scenario transcript files as clickable markdown links in your final
chat message, next to the cross-scenario findings — one link per scenario. The
reviewer wants to read each scenario's conversation in full, not only the
scoreboard or a summary. Never deliver only results: the per-scenario transcript
links must always be present, e.g.
`📄 Transcripts: [stressed](<run-dir>/stressed/transcript.md) · [grieving](<run-dir>/grieving/transcript.md) · …`.

## Stop conditions per scenario

- **natural-close** — the assistant signaled a structurally bounded close
  (insight-card affirmation + send-off, recommendation + clean exit).
- **turn-cap** — 6 user turns total without closing. This is itself a
  finding: the prompt failed to close the arc.
- **loop** — assistant kept asking variations of the same question. Also a
  finding.

The hard turn cap exists because earlier ad-hoc sweep orchestration sometimes
ran forever when a prompt couldn't close a low-input scenario. 6 turns is
right for Quick Processing / Quick Coaching / journal-reflection-shaped
prompts. Adjust the per-scenario cap via the `max_user_turns` field in the
subagent prompt if your prompt has a different intended arc length.

## Why a separate agent (not variant-runner)

Comparison runs and sweep runs spawn subagents for related-but-distinct
reasons:

- **`qa-prompt-variant-runner`** (comparison mode): advances ONE turn at a
  time. Parent owns the conversation arc, mirrors it across both variants.
  Spawned per user turn, per variant — typically many short-lived
  invocations.
- **`qa-prompt-sweep-runner`** (sweep mode): drives ONE scenario end-to-end.
  Composes its own user turns from its scenario, loops until close or cap.
  Spawned once per scenario — N parallel long-running invocations.

Same concept (isolated state, structured return), opposite shape. Kept
separate so each stays simple.
