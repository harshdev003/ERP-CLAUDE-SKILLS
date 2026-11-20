"""Final stage of a QA run: emit a Markdown transcript from the run state.

Called by the orchestrator after the model finishes the conversation and any
self-critique pass has run. Reads the run state from --state-file, optionally
folds in a Claude-written qualitative analysis from --analysis-file, and
writes a single Markdown report to
<output-dir>/qa-prompt-<slug>-<timestamp>.md.

Report sections:

  1. Header  — prompt name, slug, model, params, mode, scenario, context source, user_id.
  2. Resolved context — message count + contextInstructions presence + warnings.
  3. Conversation transcript — paired user/assistant turns in a readable format.
  4. Tool registry + chosen defaults — which tools the prompt declared and how they were answered this drive.
  5. Rendered messages — full OpenAI-shape history (system + tool messages included).
  6. Run timeline — turn-by-turn assistant text, tool call/response pairs, simulated user turns.
  7. Token usage — per-turn prompt/cached/completion/reasoning/total + cache hit rate.
  8. Final assistant message.
  9. GPT scorer grades (one block per scorer recorded under state.critiques —
     or state.critique for back-compat with older runs).
  10. Claude analysis (when --analysis-file is provided).
  11. Sources.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Write the Markdown QA report from run state.")
    p.add_argument("--state-file", required=True)
    p.add_argument("--output-dir", default="outputs")
    p.add_argument(
        "--analysis-file",
        default=None,
        help="Path to a Markdown file with Claude's qualitative analysis. Folded in verbatim.",
    )
    p.add_argument(
        "--label",
        default=None,
        help="Optional scenario/variant label inserted into the output filename "
             "(e.g. 'stressed' for one scenario in a sweep). Without it, parallel "
             "scenario runs written into the same --output-dir overwrite each "
             "other because the deterministic filename is "
             "`qa-prompt-<slug>-<timestamp>.md`. With it, the filename becomes "
             "`qa-prompt-<slug>-<label>-<timestamp>.md`. setup_sweep.py sets "
             "this automatically per scenario.",
    )
    return p.parse_args()


def _safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")[:80] or "unknown"


def _truncate(s: str, limit: int = 800) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n... [truncated; full length {len(s)} chars]"


def _format_messages(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content")
        if content is None and m.get("tool_calls"):
            tc_names = ", ".join((tc.get("function") or {}).get("name", "?") for tc in m["tool_calls"])
            lines.append(f"### {role} (tool_calls: {tc_names})\n")
            continue
        if isinstance(content, list):
            content = json.dumps(content, default=str, indent=2)
        content = content or ""
        if len(content) > 800:
            lines.append(
                f"### {role}\n\n<details><summary>show ({len(content)} chars)</summary>\n\n"
                f"```\n{_truncate(content)}\n```\n</details>\n"
            )
        else:
            lines.append(f"### {role}\n\n{content}\n")
    return "\n".join(lines)


def _format_timeline(turns: list[dict[str, Any]]) -> str:
    if not turns:
        return "_(no turns recorded)_"
    chunks: list[str] = []
    for i, turn in enumerate(turns, start=1):
        if turn.get("kind") == "user_turn":
            chunks.append(f"### Turn {i} — simulated user\n\n{turn.get('user_text', '')}\n")
            continue
        chunks.append(f"### Turn {i} — assistant (finish_reason: `{turn.get('finish_reason', '?')}`)\n")
        text = turn.get("assistant_text")
        if text:
            chunks.append(f"**Assistant text:**\n\n{text}\n")
        for call in turn.get("tool_calls") or []:
            args_json = json.dumps(call.get("arguments", {}), indent=2, default=str)
            chunks.append(
                f"**Tool call:** `{call.get('name')}` (id `{call.get('id')}`)\n\n"
                f"```json\n{args_json}\n```\n"
            )
        for resp in turn.get("tool_responses") or []:
            resp_body = resp.get("response")
            body_str = resp_body if isinstance(resp_body, str) else json.dumps(resp_body, indent=2, default=str)
            if len(body_str) > 4000:
                body_str = body_str[:4000] + f"\n... [truncated; full length {len(body_str)} chars]"
            chunks.append(
                f"**Tool response:** `{resp.get('name')}` "
                f"(source: `{resp.get('source')}`, id `{resp.get('tool_call_id')}`)\n\n"
                f"```json\n{body_str}\n```\n"
            )
    return "\n".join(chunks)


def _inline_tool_args(raw_args: Any, limit: int = 300) -> str:
    """Collapse a tool call's `arguments` (a JSON string, or already-parsed
    dict) to a single compact line for inline display in the transcript."""
    if isinstance(raw_args, str):
        s = raw_args
    elif raw_args is None:
        s = ""
    else:
        s = json.dumps(raw_args, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    if len(s) > limit:
        s = s[:limit] + f"… [+{len(s) - limit} chars]"
    return s


def _inline_tool_response(content: str, limit: int = 500) -> str:
    """Collapse a tool response body to a single compact line. The full,
    untruncated body still lives in the report's Run timeline section."""
    s = " ".join((content or "").split())
    if len(s) > limit:
        s = s[:limit] + f"… [+{len(s) - limit} chars]"
    return s or "_(empty)_"


def _format_transcript(messages: list[dict[str, Any]]) -> str:
    """Render the conversation as readable turns, with tool calls/responses inline.

    Walks `state.messages` (the full OpenAI-shape history populated by
    send_turn + append_user_message + append_tool_response). System messages
    are skipped; everything after a user message is grouped into that user's
    turn and rendered in chronological order: assistant text, the model's
    `tool_calls` (which otherwise ride content-less assistant frames), and the
    matching `tool` role responses. Used by both dataset mode (seed-rendered
    user message + assistant reply) and interactive mode (every simulated user
    turn + its reply), and shared with the standalone transcript artifact.
    """
    # A turn opens at each user message and absorbs every assistant/tool
    # message until the next user message. Each event is a (label, body) pair.
    turns: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = json.dumps(content, default=str)
        content = (content or "").strip()

        if role == "user":
            if cur is not None:
                turns.append(cur)
            cur = {"user": content, "events": []}
            continue

        if cur is None:
            # Assistant/tool frame before any user message (rare) — keep it
            # rather than drop it on the floor.
            cur = {"user": "", "events": []}

        if role == "assistant":
            if content:
                cur["events"].append(("Assistant", content))
            for tc in m.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = fn.get("name") or tc.get("name") or "?"
                raw_args = fn.get("arguments")
                if raw_args is None:
                    raw_args = tc.get("arguments")
                cur["events"].append(("Tool call", f"`{name}({_inline_tool_args(raw_args)})`"))
        elif role == "tool":
            name = m.get("name") or "?"
            cur["events"].append(("Tool response", f"`{name}` → {_inline_tool_response(content)}"))

    if cur is not None:
        turns.append(cur)

    if not turns:
        return "_(no user/assistant turns recorded)_"

    def _as_blockquote(label: str, body: str) -> str:
        """Render `> **label:** <body>` with every embedded newline re-prefixed
        with `> ` so multi-paragraph replies stay inside the quote.
        """
        if not body:
            return f"> **{label}:** _(none)_"
        first, _, rest = body.partition("\n")
        head = f"> **{label}:** {first}"
        if not rest:
            return head
        tail = "\n".join(f"> {line}" if line else ">" for line in rest.split("\n"))
        return head + "\n" + tail

    chunks: list[str] = []
    for i, turn in enumerate(turns, start=1):
        chunks.append(f"**Turn {i}**\n")
        segs = [_as_blockquote("User", turn["user"])]
        segs.extend(_as_blockquote(label, body) for label, body in turn["events"])
        chunks.append("\n>\n".join(segs) + "\n")
    return "\n".join(chunks)


def _format_tool_table(registry: dict[str, dict[str, Any]], defaults: dict[str, dict[str, Any]]) -> str:
    if not registry:
        return "_No tools declared._"
    lines = ["| Tool | Origin | Has returns schema | Real impl | Chosen default |", "|---|---|---|---|---|"]
    for name, entry in registry.items():
        origin = "tool_functions[]" if entry.get("function_id") else "inline"
        has_schema = "yes" if entry.get("returns_schema") else "no"
        has_real = "yes" if entry.get("has_real_impl") else "no"
        default = defaults.get(name) or {}
        kind = default.get("kind") or "(unset → ask-per-call)"
        lines.append(f"| `{name}` | {origin} | {has_schema} | {has_real} | {kind} |")
    return "\n".join(lines)


def _format_token_usage(turns: list[dict[str, Any]]) -> str:
    """Per-turn token usage table with a totals row.

    Reads the normalized `usage` block recorded on each assistant turn by
    send_turn.py — shape is the same regardless of which OpenAI API
    produced the call (chat vs responses):

        {input_tokens, cached_input_tokens, output_tokens,
         reasoning_tokens, total_tokens, api}

    For back-compat with state files written before this normalization,
    fall back to the old nested Chat Completions field names.
    """
    rows: list[tuple[int, dict[str, Any]]] = []
    assistant_index = 0
    for t in turns:
        if t.get("kind") == "user_turn":
            continue
        assistant_index += 1
        usage = t.get("usage") or {}
        rows.append((assistant_index, usage))
    if not rows or not any(u for _, u in rows):
        return "_(no token usage recorded — turns predate this field, or the model returned no usage block)_"

    def _norm(u: dict[str, Any]) -> dict[str, int]:
        if "input_tokens" in u or "output_tokens" in u or "cached_input_tokens" in u:
            return {
                "input": int(u.get("input_tokens") or 0),
                "cached": int(u.get("cached_input_tokens") or 0),
                "output": int(u.get("output_tokens") or 0),
                "reasoning": int(u.get("reasoning_tokens") or 0),
                "total": int(u.get("total_tokens") or 0),
            }
        prompt_details = u.get("prompt_tokens_details") or {}
        completion_details = u.get("completion_tokens_details") or {}
        return {
            "input": int(u.get("prompt_tokens") or 0),
            "cached": int(prompt_details.get("cached_tokens") or 0),
            "output": int(u.get("completion_tokens") or 0),
            "reasoning": int(completion_details.get("reasoning_tokens") or 0),
            "total": int(u.get("total_tokens") or 0),
        }

    lines = [
        "| Turn | Input | Cached | Output | Reasoning | Total |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    totals = {"input": 0, "cached": 0, "output": 0, "reasoning": 0, "total": 0}
    for idx, u in rows:
        n = _norm(u)
        total = n["total"] or (n["input"] + n["output"])
        for k in ("input", "cached", "output", "reasoning"):
            totals[k] += n[k]
        totals["total"] += total
        lines.append(f"| {idx} | {n['input']} | {n['cached']} | {n['output']} | {n['reasoning']} | {total} |")
    lines.append(
        f"| **Total** | **{totals['input']}** | **{totals['cached']}** | "
        f"**{totals['output']}** | **{totals['reasoning']}** | **{totals['total']}** |"
    )
    if totals["input"]:
        cache_pct = 100.0 * totals["cached"] / totals["input"]
        lines.append(f"\n_Cache hit rate (cached / input): {cache_pct:.1f}%_")
    return "\n".join(lines)


def _format_four_axis(grade: dict[str, Any]) -> str:
    rows = []
    for key in ("in_character", "tool_usage", "coherence", "brand_voice"):
        entry = grade.get(key) or {}
        score = entry.get("score", "—")
        evidence = entry.get("evidence", "")
        rows.append(f"| **{key}** | {score} | {evidence} |")
    table = "| Dimension | Score | Evidence |\n|---|---|---|\n" + "\n".join(rows)
    summary = grade.get("summary", "")
    return f"{table}\n\n**Summary.** {summary}\n"


def _format_single_score(grade: dict[str, Any]) -> str:
    score = grade.get("score", "—")
    rationale = grade.get("rationale", "")
    tags = grade.get("failure_tags") or []
    tag_str = ", ".join(f"`{t}`" for t in tags) if tags else "_(none)_"
    # `valid` is emitted by judgment-graded scorers (structured-plan-match);
    # absent for the others, in which case the column is dropped.
    if "valid" in grade:
        valid_str = "✅ yes" if grade.get("valid") else "❌ no"
        header = "| Score | Valid? | Failure tags |\n|---|---|---|\n"
        row = f"| **{score}** | {valid_str} | {tag_str} |\n\n"
    else:
        header = "| Score | Failure tags |\n|---|---|\n"
        row = f"| **{score}** | {tag_str} |\n\n"
    return header + row + f"**Rationale.** {rationale}\n"


def _format_critique(grade: dict[str, Any]) -> str:
    """Pick the renderer based on the grade's shape."""
    if any(k in grade for k in ("in_character", "tool_usage", "coherence", "brand_voice")):
        return _format_four_axis(grade)
    if "score" in grade:
        return _format_single_score(grade)
    return f"```json\n{json.dumps(grade, indent=2, default=str)}\n```\n"


def _scorer_heading(slug: str) -> str:
    return slug.replace("-", " ").title()


def _render_standalone_transcript(state: dict[str, Any]) -> str:
    """A clean, conversation-only transcript: short header + paired turns.

    Shared by write_report.py (which always emits it alongside the full report)
    and write_transcript.py (which emits it on its own).
    """
    prompt = state.get("prompt") or {}
    lines: list[str] = []
    lines.append(f"# Conversation transcript — {prompt.get('name', '(unknown prompt)')}\n")
    if prompt.get("name"):
        lines.append(f"- Prompt: [{prompt.get('name')} ({prompt.get('slug')})]({prompt.get('url')})")
    if prompt.get("version"):
        pin = " (pinned)" if prompt.get("version_pinned") else " (latest at fetch time)"
        lines.append(f"- Version: `{prompt['version']}`{pin}")
    lines.append(f"- Model: `{state.get('model')}` · provider: `{state.get('provider') or 'openai'}`")
    if state.get("scenario"):
        lines.append(f"- Scenario: {state['scenario']}")
    lines.append(f"- Generated: `{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}`")
    lines.append("\n---\n")
    lines.append(_format_transcript(state.get("messages") or []))
    return "\n".join(lines) + "\n"


def _render_report(state: dict[str, Any], analysis_text: str | None) -> str:
    prompt = state.get("prompt") or {}
    context = state.get("context") or {}
    lines: list[str] = []
    lines.append(f"# QA report — {prompt.get('name', '(unknown prompt)')}\n")

    lines.append("## Header\n")
    lines.append(f"- Prompt: [{prompt.get('name')} ({prompt.get('slug')})]({prompt.get('url')})")
    if prompt.get("version"):
        pin = " (pinned)" if prompt.get("version_pinned") else " (latest at fetch time)"
        lines.append(f"- Version: `{prompt['version']}`{pin}")
    lines.append(f"- Model: `{state.get('model')}` · params: `{json.dumps(state.get('params') or {})}`")
    provider = state.get("provider") or "openai"
    if provider == "google":
        lines.append(f"- Provider: `google` (Gemini)")
    elif provider == "anthropic":
        lines.append(f"- Provider: `anthropic` · API: `anthropic-messages`")
    else:
        lines.append(f"- Provider: `openai` · API: `{state.get('api') or 'chat'}`")
    lines.append(f"- Mode: `{state.get('mode') or 'dataset'}`")
    if state.get("scenario"):
        lines.append(f"- Scenario: {state['scenario']}")
    lines.append(f"- Context source: `{context.get('source')}`")
    lines.append(f"- User id: `{state.get('user_id') or '(unset)'}`")

    lines.append("\n## Resolved context\n")
    lines.append(f"- Seed messages: `{context.get('input_message_count', 0)}`")
    lines.append(f"- `metadata.contextInstructions` present: `{context.get('has_context_instructions', False)}`")
    if context.get("input_discarded"):
        lines.append("> ⚠️ Interactive mode: the context source's `input` messages were discarded; only `metadata.contextInstructions` was used.")
    for w in context.get("warnings") or []:
        lines.append(f"> ⚠️ {w}")
    for w in state.get("render_warnings") or []:
        lines.append(f"> ⚠️ render: {w}")

    lines.append("\n## Conversation transcript\n")
    lines.append(_format_transcript(state.get("messages") or []))

    lines.append("\n## Tool registry + chosen defaults\n")
    lines.append(_format_tool_table(
        state.get("tool_registry") or {},
        state.get("tool_defaults") or {},
    ))

    lines.append("\n## Rendered messages\n")
    lines.append(_format_messages(state.get("messages") or []))

    lines.append("\n## Run timeline\n")
    lines.append(_format_timeline(state.get("turns") or []))

    lines.append("\n## Token usage\n")
    lines.append(_format_token_usage(state.get("turns") or []))

    # Final assistant message — last assistant turn that produced text.
    final = ""
    for turn in reversed(state.get("turns") or []):
        if turn.get("kind") == "user_turn":
            continue
        if turn.get("assistant_text"):
            final = turn["assistant_text"]
            break
    lines.append("\n## Final assistant message\n")
    lines.append(final or "_(none)_")

    critiques = state.get("critiques") if isinstance(state.get("critiques"), dict) else None
    if critiques:
        lines.append("\n## GPT scorer grades\n")
        for slug, grade in critiques.items():
            lines.append(f"### {_scorer_heading(slug)}\n")
            lines.append(_format_critique(grade or {}))
    elif state.get("critique"):
        lines.append("\n## GPT self-critique\n")
        lines.append(_format_critique(state["critique"]))

    if analysis_text:
        lines.append("\n## Claude analysis\n")
        lines.append(analysis_text.rstrip() + "\n")
        state["claude_analysis"] = analysis_text  # persist so the state file remains authoritative

    lines.append("\n## Sources\n")
    lines.append(f"- [Braintrust prompt {prompt.get('id')}]({prompt.get('url')})")
    lines.append("- [Tools/Braintrust](https://www.notion.so/34c75e23af2081968f7bf7ee43e1442a)")
    lines.append("- [Tools/OpenAI](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e)")
    lines.append("- [ENGBACK-1029](https://linear.app/aura-health/issue/ENGBACK-1029)")
    return "\n".join(lines) + "\n"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    state_path = Path(args.state_file)
    state = json.loads(state_path.read_text(encoding="utf-8"))

    analysis_text: str | None = None
    if args.analysis_file:
        try:
            analysis_text = Path(args.analysis_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"warn: could not read --analysis-file {args.analysis_file!r}: {e}", file=sys.stderr)

    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = (state.get("prompt") or {}).get("slug") or (state.get("prompt") or {}).get("id") or "unknown"
    if args.label:
        fname = f"qa-prompt-{_safe_filename(slug)}-{_safe_filename(args.label)}-{ts}.md"
    else:
        fname = f"qa-prompt-{_safe_filename(slug)}-{ts}.md"
    out_path = out_dir / fname
    out_path.write_text(_render_report(state, analysis_text), encoding="utf-8")

    # Persist any added analysis text into the state for completeness.
    if analysis_text:
        state["claude_analysis"] = analysis_text
        state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

    # Also write the standalone conversation transcript so the dialogue is
    # always available as its own artifact, not just buried in the report.
    tname = f"transcript-{_safe_filename(args.label)}.md" if args.label else "transcript.md"
    tpath = out_dir / tname
    tpath.write_text(_render_standalone_transcript(state), encoding="utf-8")

    print(str(out_path))
    print(f"TRANSCRIPT: {tpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
