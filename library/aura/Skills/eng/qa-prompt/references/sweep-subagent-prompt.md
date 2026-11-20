# Sweep subagent prompt template

The orchestrator (the parent Claude running the qa-prompt skill in sweep mode) uses this template to brief each `qa-prompt-sweep-runner` subagent. Fill in the four `<<placeholders>>` per scenario. Spawn all N subagents in parallel — one `Agent` tool call per scenario in a single message.

This template is the load-bearing piece of sweep reliability. Earlier ad-hoc subagent briefs sometimes bailed after one tool use because they were ambiguous about looping. This template states the loop explicitly, hard-caps it, and tells the runner exactly what to return.

## When to use

- Sweep mode (`scripts/setup_sweep.py` was just run).
- You have the `<run_dir>` from `setup_sweep.py` stdout.
- Per-scenario state files exist at `<run_dir>/<label>/state.json` (verified by reading `<run_dir>/sweep.json`).
- Tool defaults are set on every state file (mirrored across scenarios — typically auto-mock for all tools).

## Template

```
You are driving ONE interactive QA scenario of a qa-prompt sweep against the Aura Braintrust prompt under test. You play the simulated user; the qa-prompt helpers handle OpenAI + Braintrust plumbing.

STATE FILE: <<absolute-path-to-state.json>>
LABEL: <<scenario-label>>
SCENARIO: <<full-scenario-description>>

The state already has the system prompt rendered but NO user message yet. You compose the opener, then loop until natural close or 6 user turns total.

Tool defaults: all tools auto-mock (apply_tool_default.py returns shape-valid stubs; exit code 0 every call — you don't ask anyone anything).

## Scripts (paths have spaces — quote them)

- APPEND_USER: <<repo-root>>/plugins/eng/skills/qa-prompt/scripts/append_user_message.py — content via STDIN. NO --content arg. Use: echo 'text' | python3 "$APPEND_USER" --state-file "$STATE_FILE"
- SEND_TURN:   <<repo-root>>/plugins/eng/skills/qa-prompt/scripts/send_turn.py
- APPLY_TOOL:  <<repo-root>>/plugins/eng/skills/qa-prompt/scripts/apply_tool_default.py
- SELF_CRITIQUE: <<repo-root>>/plugins/eng/skills/qa-prompt/scripts/self_critique.py

## Loop (run MANY iterations — DO NOT stop after one tool call or one turn)

1. Compose the opener in the voice of the scenario. Append via APPEND_USER (stdin).
2. Call SEND_TURN. Parse JSON stdout.
3. If stop:false, iterate tool_calls. For each: APPLY_TOOL with id/name/arguments. Exit 0 → loop back to SEND_TURN. Exit 10 should not happen — if it does, stop with pending_ask_per_call:true.
4. If stop:true, read assistant_text. If it signals natural close (insight-card affirmation, "rest well" / "go enjoy your kids" / "carry this with you" sign-off, recommendation followed by clean exit), the drive is done.
5. Otherwise compose the next user turn from scenario + history. Stay in character — react authentically, don't over-articulate. Go to step 1.

## Stop conditions

- natural-close: assistant signaled a clean structurally-bounded close (see step 4).
- turn-cap: 6 user turns total (opener counts as turn 1) without closing — that's a finding worth reporting.
- loop: assistant keeps asking variations of the same question — also a finding.

CRITICAL: do NOT stop after one tool call or one turn. The drive only ends when one of the three stop conditions above is true.

## After driving

Run SELF_CRITIQUE: python3 "$SELF_CRITIQUE" --state-file "$STATE_FILE"

## Report back (and ONLY this — print one JSON object on stdout, no prose, no markdown)

{
  "label": "<<scenario-label>>",
  "turns": <count of user turns total, including opener>,
  "stop_reason": "natural-close | turn-cap | loop",
  "tools_called": ["<name>", "<name>"],
  "assistant_final_text": "<full final assistant message verbatim>",
  "critique": {
    "in_character": <n>,
    "tool_usage": <n>,
    "coherence": <n>,
    "brand_voice": <n>,
    "summary": "<one paragraph from state.critique>"
  },
  "notes": "<one paragraph — what felt right, what felt off, did the close feel earned>",
  "pending_ask_per_call": null,
  "error": null
}

Stay in character throughout. Do not surface turn output to chat (you're a subagent — the parent reads the structured return). On failure: stop and return error + partial state.
```

## Parent-side wiring

The parent fills the four placeholders from the sweep manifest:

```python
import json
manifest = json.loads(open(f"{run_dir}/sweep.json").read())
for entry in manifest["scenarios"]:
    prompt = TEMPLATE.replace("<<absolute-path-to-state.json>>", entry["state_file"]) \
                     .replace("<<scenario-label>>", entry["label"]) \
                     .replace("<<full-scenario-description>>", entry["scenario"]) \
                     .replace("<<repo-root>>", REPO_ROOT)
    # spawn one Agent tool call per entry, all in a single message
```

All N `Agent` tool calls should go in a single message so they run concurrently. Each call uses `subagent_type: "qa-prompt-sweep-runner"`.

## Why this template exists

- Two subagents in the QA pass that motivated sweep mode bailed after a single tool use because the prompt was ambiguous about looping. The explicit "DO NOT stop after one tool call or one turn" line + the hard 6-turn cap fixed it.
- The structured JSON return format means the parent can parse it without re-reading state files. The parent renders the scoreboard + per-scenario sections directly from the N returned objects (cross-checking against the state files only when it needs full transcript detail).
- The "stay in character" reminder isn't fluff. Without it, runners drift toward over-articulate user replies that don't actually test the prompt against the persona — and the resulting report is grading the prompt against a Claude-flavoured user, not the scenario being tested.
