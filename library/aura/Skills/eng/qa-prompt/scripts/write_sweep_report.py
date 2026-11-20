"""Final stage of a SWEEP QA run: aggregate per-scenario reports into one.

Reads the sweep manifest at `<run-dir>/sweep.json`, walks each
scenario's `<label>/state.json` + critique, and emits a single
cross-scenario markdown report at
`<output-dir>/qa-prompt-sweep-<slug>-<timestamp>.md`.

Report shape (matches the hand-rolled aggregated report from the
Quick Processing QA pass that motivated this script — see
ENGBACK-1029):

  1. Header — prompt URL, model, mode, run_dir, scenario count.
  2. Scoreboard — table (label, turns, stop reason, tools called,
     4-axis critique scores, ship verdict if present in Claude
     analysis).
  3. Overall ship verdict + cross-cutting findings (folded from
     --analysis-file, if provided).
  4. Per-scenario sections — each scenario's transcript, tool summary,
     critique, and per-scenario Claude analysis (read from
     `<label>/claude-analysis.md` if present).
  5. Method notes — tool defaults applied, env (auto-mock vs real),
     user-simulation caveat.
  6. Sources — list of per-scenario state files + analyses.

Per-scenario deep-dive reports are still produced by `write_report.py`
(invoke with `--label <label>` so the filenames don't collide).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from write_report import _render_standalone_transcript  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Aggregate a sweep run's per-scenario states into one cross-scenario report."
    )
    p.add_argument(
        "--run-dir",
        required=True,
        help="Sweep run directory (contains sweep.json + per-scenario subdirs).",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="Where to write the aggregated report. Default: same as --run-dir.",
    )
    p.add_argument(
        "--analysis-file",
        default=None,
        help="Path to a markdown file with Claude's cross-scenario qualitative "
             "analysis. Folded in verbatim under 'Overall ship verdict + "
             "cross-cutting findings'.",
    )
    return p.parse_args()


def _safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")[:80] or "unknown"


def _read_state(state_path: Path) -> dict | None:
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"warn: could not read {state_path}: {e}", file=sys.stderr)
        return None


def _count_user_turns(state: dict) -> int:
    return sum(1 for m in state.get("messages", []) if m.get("role") == "user")


def _final_assistant_text(state: dict) -> str:
    for m in reversed(state.get("messages", [])):
        if m.get("role") == "assistant" and m.get("content"):
            c = m["content"]
            return c if isinstance(c, str) else json.dumps(c, default=str)
    return ""


def _tools_called(state: dict) -> list[str]:
    names: list[str] = []
    for m in state.get("messages", []):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = (tc.get("function") or {}).get("name")
                if fn:
                    names.append(fn)
    return names


def _critique_for(state: dict, scorer: str = "aurie-conversation") -> dict | None:
    # New shape: state.critiques[<scorer>].
    crits = state.get("critiques") or {}
    if scorer in crits:
        return crits[scorer]
    # Back-compat: state.critique (single critique, no slug).
    return state.get("critique")


def _format_scoreboard(scenarios: list[dict], states: dict[str, dict]) -> str:
    lines = [
        "| Label | Turns | Stop reason | Tools called | in_char | tool | coherence | brand |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in scenarios:
        label = entry["label"]
        state = states.get(label)
        if state is None:
            lines.append(f"| {label} | — | (no state) | — | — | — | — | — |")
            continue
        turns = _count_user_turns(state)
        tools = _tools_called(state)
        tools_str = ", ".join(tools) if tools else "none"
        # Stop reason is not currently stored explicitly — infer from state shape.
        stop = state.get("stop_reason") or "—"
        crit = _critique_for(state) or {}
        in_char = (crit.get("in_character") or {}).get("score", "—")
        tool_score = (crit.get("tool_usage") or {}).get("score", "—")
        coherence = (crit.get("coherence") or {}).get("score", "—")
        brand = (crit.get("brand_voice") or {}).get("score", "—")
        lines.append(
            f"| {label} | {turns} | {stop} | {tools_str} | "
            f"{in_char} | {tool_score} | {coherence} | {brand} |"
        )
    return "\n".join(lines)


def _format_transcript(state: dict, max_chars: int = 600) -> str:
    """Render user/assistant messages as paired turns. Skip system.

    Per-message text is capped at `max_chars` for the at-a-glance aggregated
    report body only. The FULL untruncated conversation for each scenario is
    written separately as `<run-dir>/<label>/transcript.md` (see main()).
    """
    lines: list[str] = []
    turn = 0
    for m in state.get("messages", []):
        role = m.get("role", "")
        if role == "system":
            continue
        content = m.get("content") or ""
        if isinstance(content, list):
            content = json.dumps(content, default=str)
        if role == "user":
            turn += 1
            preview = content if len(content) <= max_chars else content[:max_chars] + " […]"
            lines.append(f"**U{turn}:** {preview}")
        elif role == "assistant":
            tool_calls = m.get("tool_calls") or []
            for tc in tool_calls:
                fn = (tc.get("function") or {}).get("name", "?")
                args = (tc.get("function") or {}).get("arguments", "{}")
                args_preview = args if len(args) <= 200 else args[:200] + " […]"
                lines.append(f"**A (tool):** `{fn}({args_preview})`")
            if content:
                preview = content if len(content) <= max_chars else content[:max_chars] + " […]"
                lines.append(f"**A:** {preview}")
    return "\n\n".join(lines)


def _format_per_scenario_section(
    label: str, scenario_text: str, state: dict | None, claude_analysis: str | None
) -> str:
    if state is None:
        return f"### {label}\n\n*State file not found.*"
    crit = _critique_for(state) or {}
    parts = [f"### {label}"]
    parts.append(f"**Scenario:** {scenario_text}")
    prov = state.get("provider") or "openai"
    if prov == "google":
        parts.append("**Provider:** `google` (Gemini)")
    elif prov == "anthropic":
        parts.append("**Provider:** `anthropic` · **API:** `anthropic-messages`")
    else:
        parts.append(f"**Provider:** `openai` · **API:** `{state.get('api') or 'chat'}`")
    parts.append(f"**Turns:** {_count_user_turns(state)}")
    tools = _tools_called(state)
    parts.append(f"**Tools called:** {', '.join(tools) if tools else 'none'}")
    if crit:
        parts.append(
            f"**GPT critique:** "
            f"in_character={(crit.get('in_character') or {}).get('score', '—')} · "
            f"tool_usage={(crit.get('tool_usage') or {}).get('score', '—')} · "
            f"coherence={(crit.get('coherence') or {}).get('score', '—')} · "
            f"brand_voice={(crit.get('brand_voice') or {}).get('score', '—')}"
        )
        summary = crit.get("summary")
        if summary:
            parts.append(f"**Critique summary:** {summary}")
    parts.append("**Final assistant message:**")
    parts.append(f"> {_final_assistant_text(state) or '*(none)*'}")
    parts.append("**Transcript:**\n\n" + _format_transcript(state))
    if claude_analysis:
        parts.append("**Claude analysis:**\n\n" + claude_analysis)
    return "\n\n".join(parts)


def _render_report(
    manifest: dict,
    states: dict[str, dict],
    per_scenario_analyses: dict[str, str],
    cross_analysis: str | None,
    run_dir: Path,
) -> str:
    prompt = manifest.get("prompt", {})
    scenarios = manifest.get("scenarios", [])
    out: list[str] = []
    out.append(f"# QA Report — {prompt.get('name', '?')}, {len(scenarios)}-scenario sweep")
    out.append("")
    out.append(f"**Prompt:** [{prompt.get('name', '?')}]({prompt.get('url', '#')})")
    if prompt.get("version"):
        pin = " *(pinned)*" if prompt.get("version_pinned") else ""
        out.append(f"**Version:** `{prompt['version']}`{pin}")
    sample_state = next(iter(states.values()), None)
    if sample_state:
        out.append(f"**Model:** `{sample_state.get('model', '?')}`")
    # Per-scenario state.provider is mirrored from the manifest. Surface
    # provider + (for openai) api once at the run level when all scenarios
    # agree; otherwise mark mixed so the scoreboard can be inspected
    # per-scenario.
    providers = {s.get("provider") or "openai" for s in states.values()}
    apis = {s.get("api") or "chat" for s in states.values()}
    if len(providers) == 1:
        prov = next(iter(providers))
        if prov == "google":
            out.append("**Provider:** `google` (Gemini)")
        elif prov == "anthropic":
            out.append("**Provider:** `anthropic` · **API:** `anthropic-messages`")
        elif len(apis) == 1:
            out.append(f"**Provider:** `openai` · **API:** `{next(iter(apis))}`")
        else:
            out.append(f"**Provider:** `openai` · **API:** mixed ({', '.join(sorted(apis))})")
    else:
        out.append(f"**Provider:** mixed ({', '.join(sorted(providers))})")
    out.append(f"**Mode:** Interactive (Claude simulating user)")
    out.append(f"**Run dir:** `{run_dir}`")
    out.append(f"**Scenarios:** {len(scenarios)}")
    out.append("")
    out.append("## Scoreboard")
    out.append("")
    out.append(_format_scoreboard(scenarios, states))
    out.append("")
    out.append("## Overall ship verdict + cross-cutting findings")
    out.append("")
    if cross_analysis:
        out.append(cross_analysis)
    else:
        out.append("*(no --analysis-file provided)*")
    out.append("")
    out.append("## Per-scenario reports")
    out.append("")
    for entry in scenarios:
        label = entry["label"]
        section = _format_per_scenario_section(
            label=label,
            scenario_text=entry.get("scenario", ""),
            state=states.get(label),
            claude_analysis=per_scenario_analyses.get(label),
        )
        out.append(section)
        out.append("")
    out.append("## Method notes")
    out.append("")
    out.append(
        "- Each scenario is an independent interactive drive against the same "
        "prompt version. Claude composed user turns from the scenario description.\n"
        "- Tool defaults were mirrored across scenarios so behavioural differences "
        "trace to the prompt's response, not to tool-response variance.\n"
        "- The 4-axis critique scores come from the `aurie-conversation` scorer "
        "(see `references/scorers/aurie-conversation.md`).\n"
        "- Per-scenario deep-dive reports — full rendered messages, run timeline, "
        "token usage — are written separately by `write_report.py --label <label>`."
    )
    out.append("")
    out.append("## Sources")
    out.append("")
    out.append(f"- Sweep manifest: `{run_dir / 'sweep.json'}`")
    for entry in scenarios:
        label = entry["label"]
        out.append(f"- `{label}` state: `{run_dir / label / 'state.json'}`")
        if label in per_scenario_analyses:
            out.append(f"- `{label}` Claude analysis: `{run_dir / label / 'claude-analysis.md'}`")
    out.append("")
    return "\n".join(out)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()

    run_dir = Path(args.run_dir)
    manifest_path = run_dir / "sweep.json"
    if not manifest_path.exists():
        print(f"error: sweep manifest not found at {manifest_path}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenarios = manifest.get("scenarios", [])
    if not scenarios:
        print("error: sweep manifest has no scenarios", file=sys.stderr)
        return 2

    states: dict[str, dict] = {}
    per_scenario_analyses: dict[str, str] = {}
    for entry in scenarios:
        label = entry["label"]
        state_path = run_dir / label / "state.json"
        state = _read_state(state_path)
        if state is not None:
            states[label] = state
        analysis_path = run_dir / label / "claude-analysis.md"
        if analysis_path.exists():
            try:
                per_scenario_analyses[label] = analysis_path.read_text(encoding="utf-8")
            except OSError as e:
                print(f"warn: could not read {analysis_path}: {e}", file=sys.stderr)

    cross_analysis: str | None = None
    if args.analysis_file:
        try:
            cross_analysis = Path(args.analysis_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"warn: could not read --analysis-file: {e}", file=sys.stderr)

    out_dir = Path(args.output_dir) if args.output_dir else run_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    slug = (manifest.get("prompt") or {}).get("slug") or "unknown"
    fname = f"qa-prompt-sweep-{_safe_filename(slug)}-{ts}.md"
    out_path = out_dir / fname
    out_path.write_text(
        _render_report(manifest, states, per_scenario_analyses, cross_analysis, run_dir),
        encoding="utf-8",
    )
    print(str(out_path))

    # Write the FULL untruncated conversation for each scenario as its own
    # transcript file, so every scenario's dialogue is always available on its
    # own as a clickable link (the aggregated body above is capped per message).
    for entry in scenarios:
        label = entry["label"]
        state = states.get(label)
        if state is None:
            continue
        tpath = run_dir / label / "transcript.md"
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(_render_standalone_transcript(state), encoding="utf-8")
        print(f"TRANSCRIPT: {tpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
