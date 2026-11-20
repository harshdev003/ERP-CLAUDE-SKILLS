"""Emit a side-by-side comparison report from two qa-prompt variant state files.

Used by the orchestrator after a comparison drive ends. Both variants must
have already been driven against the SAME user-turn sequence (the parent
appends each user turn to both states; subagents per variant advance each
state by one assistant turn at a time). Both should also have had
self_critique.py run against them, so `state.critique` is populated for each.

The report has these sections:

  1. Header  — both variants side-by-side (name, model, params, scenario).
  2. Setup parity check — flags differences in context source / scenario / tool defaults.
  3. Conversation transcript — per-turn user message, then both variants' replies stacked.
  4. Tool defaults per variant — table with the chosen kind per tool.
  5. Token usage — per-variant prompt/cached/completion/reasoning/total + cache hit rate.
  6. GPT critique scores — both variants' rubric scores side-by-side.
  7. Claude comparison analysis (when --analysis-file is provided).
  8. Sources.

Usage:

    python3 write_comparison_report.py \\
        --state-file-a path/to/a/state.json \\
        --state-file-b path/to/b/state.json \\
        [--label-a "v3"] [--label-b "v4"] \\
        [--analysis-file path/to/claude-comparison.md] \\
        --output-dir outputs
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
    p = argparse.ArgumentParser(description="Write a side-by-side QA comparison report.")
    p.add_argument("--state-file-a", required=True)
    p.add_argument("--state-file-b", required=True)
    p.add_argument("--label-a", default=None, help="Short label for variant A (defaults to prompt name).")
    p.add_argument("--label-b", default=None, help="Short label for variant B (defaults to prompt name).")
    p.add_argument("--analysis-file", default=None, help="Optional Claude comparison analysis markdown.")
    p.add_argument("--output-dir", default="outputs")
    return p.parse_args()


def _safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")[:80] or "unknown"


def _default_label(state: dict[str, Any]) -> str:
    prompt = state.get("prompt") or {}
    return prompt.get("name") or prompt.get("slug") or prompt.get("id") or "(unknown)"


def _as_blockquote(label: str, body: str) -> str:
    """Render `> **label:** <body>` with embedded newlines re-prefixed with `> `."""
    if not body:
        return f"> **{label}:** _(none)_"
    first, _, rest = body.partition("\n")
    head = f"> **{label}:** {first}"
    if not rest:
        return head
    tail = "\n".join(f"> {line}" if line else ">" for line in rest.split("\n"))
    return head + "\n" + tail


def _walk_user_assistant(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Walk OpenAI-shape messages and pair user → next assistant-text turn.

    Mirrors write_report._format_transcript's pairing logic — kept independent
    so the comparison writer stays self-contained.
    """
    pairs: list[tuple[str, str]] = []
    pending_user: str | None = None
    for m in messages:
        role = m.get("role")
        if role in ("system", "tool"):
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = json.dumps(content, default=str)
        content = (content or "").strip()
        if role == "user":
            if pending_user is not None:
                pairs.append((pending_user, ""))
            pending_user = content
        elif role == "assistant":
            if not content:
                continue
            pairs.append((pending_user or "", content))
            pending_user = None
    if pending_user is not None:
        pairs.append((pending_user, ""))
    return pairs


def _format_paired_transcript(
    pairs_a: list[tuple[str, str]],
    pairs_b: list[tuple[str, str]],
    label_a: str,
    label_b: str,
) -> tuple[str, list[str]]:
    """Build the per-turn comparison transcript. Returns (markdown, warnings)."""
    warnings: list[str] = []
    if len(pairs_a) != len(pairs_b):
        warnings.append(
            f"Variant transcripts have different length ({len(pairs_a)} vs {len(pairs_b)} turns). "
            f"Pairing by index; missing turns rendered as _(missing)_."
        )

    n = max(len(pairs_a), len(pairs_b))
    if n == 0:
        return "_(no user/assistant turns recorded)_", warnings

    chunks: list[str] = []
    for i in range(n):
        user_a = pairs_a[i][0] if i < len(pairs_a) else "_(missing)_"
        user_b = pairs_b[i][0] if i < len(pairs_b) else "_(missing)_"
        asst_a = pairs_a[i][1] if i < len(pairs_a) else "_(missing)_"
        asst_b = pairs_b[i][1] if i < len(pairs_b) else "_(missing)_"

        chunks.append(f"### Turn {i+1}\n")
        if user_a == user_b:
            chunks.append(_as_blockquote("User", user_a) + "\n")
        else:
            warnings.append(
                f"Turn {i+1}: variants saw different user messages — comparison is not apples-to-apples for that turn."
            )
            chunks.append(_as_blockquote(f"User → {label_a}", user_a) + "\n")
            chunks.append(_as_blockquote(f"User → {label_b}", user_b) + "\n")

        chunks.append(f"**{label_a}**\n")
        chunks.append(_as_blockquote("Assistant", asst_a) + "\n")
        chunks.append(f"**{label_b}**\n")
        chunks.append(_as_blockquote("Assistant", asst_b) + "\n")

    return "\n".join(chunks), warnings


def _format_setup_parity(state_a: dict[str, Any], state_b: dict[str, Any]) -> str:
    rows: list[str] = []
    rows.append("| Field | A | B | Match |")
    rows.append("|---|---|---|---|")
    fields = [
        ("Prompt id", lambda s: (s.get("prompt") or {}).get("id") or "(unknown)"),
        ("Version", lambda s: (s.get("prompt") or {}).get("version") or "(unspecified)"),
        ("Mode", lambda s: s.get("mode") or "dataset"),
        ("Scenario", lambda s: s.get("scenario") or "(none)"),
        ("Context source", lambda s: (s.get("context") or {}).get("source", "(none)")),
        ("Model", lambda s: s.get("model")),
        ("Provider", lambda s: s.get("provider") or "openai"),
        ("API", lambda s: s.get("api") or "chat"),
        ("Params", lambda s: json.dumps(s.get("params") or {}, sort_keys=True)),
    ]
    for label, fn in fields:
        va = fn(state_a)
        vb = fn(state_b)
        match = "✓" if va == vb else "✗"
        rows.append(f"| {label} | `{va}` | `{vb}` | {match} |")
    return "\n".join(rows)


def _format_tool_defaults(state_a: dict[str, Any], state_b: dict[str, Any]) -> str:
    tools_a = set((state_a.get("tool_registry") or {}).keys())
    tools_b = set((state_b.get("tool_registry") or {}).keys())
    tools = sorted(tools_a | tools_b)
    if not tools:
        return "_No tools declared by either variant._"
    rows: list[str] = ["| Tool | A default | B default | Match |", "|---|---|---|---|"]
    da = state_a.get("tool_defaults") or {}
    db = state_b.get("tool_defaults") or {}
    for t in tools:
        ka = (da.get(t) or {}).get("kind") or ("ask-per-call" if t in tools_a else "_(not declared)_")
        kb = (db.get(t) or {}).get("kind") or ("ask-per-call" if t in tools_b else "_(not declared)_")
        match = "✓" if ka == kb else "✗"
        rows.append(f"| `{t}` | {ka} | {kb} | {match} |")
    return "\n".join(rows)


def _aggregate_usage(turns: list[dict[str, Any]]) -> dict[str, int]:
    """Sum the normalized usage block across all assistant turns in a variant.

    Falls back to the legacy nested Chat Completions field names so reports
    against pre-normalization state files still render.
    """
    totals = {"input": 0, "cached": 0, "output": 0, "reasoning": 0, "total": 0}
    for t in turns:
        if t.get("kind") == "user_turn":
            continue
        u = t.get("usage") or {}
        if "input_tokens" in u or "output_tokens" in u or "cached_input_tokens" in u:
            input_t = int(u.get("input_tokens") or 0)
            cached = int(u.get("cached_input_tokens") or 0)
            output = int(u.get("output_tokens") or 0)
            reasoning = int(u.get("reasoning_tokens") or 0)
            total = int(u.get("total_tokens") or 0) or (input_t + output)
        else:
            prompt_details = u.get("prompt_tokens_details") or {}
            completion_details = u.get("completion_tokens_details") or {}
            input_t = int(u.get("prompt_tokens") or 0)
            cached = int(prompt_details.get("cached_tokens") or 0)
            output = int(u.get("completion_tokens") or 0)
            reasoning = int(completion_details.get("reasoning_tokens") or 0)
            total = int(u.get("total_tokens") or 0) or (input_t + output)
        totals["input"] += input_t
        totals["cached"] += cached
        totals["output"] += output
        totals["reasoning"] += reasoning
        totals["total"] += total
    return totals


def _format_token_usage_side_by_side(state_a: dict[str, Any], state_b: dict[str, Any], label_a: str, label_b: str) -> str:
    a = _aggregate_usage(state_a.get("turns") or [])
    b = _aggregate_usage(state_b.get("turns") or [])
    if not a["total"] and not b["total"]:
        return "_(no token usage recorded for either variant — turns predate this field, or the model returned no usage block)_"

    def _cache_pct(t: dict[str, int]) -> str:
        return f"{(100.0 * t['cached'] / t['input']):.1f}%" if t["input"] else "—"

    rows = [
        f"| Metric | {label_a} | {label_b} |",
        "|---|---:|---:|",
        f"| Input tokens | {a['input']} | {b['input']} |",
        f"| Cached tokens | {a['cached']} | {b['cached']} |",
        f"| Output tokens | {a['output']} | {b['output']} |",
        f"| Reasoning tokens | {a['reasoning']} | {b['reasoning']} |",
        f"| **Total tokens** | **{a['total']}** | **{b['total']}** |",
        f"| Cache hit rate | {_cache_pct(a)} | {_cache_pct(b)} |",
    ]
    return "\n".join(rows)


def _scorer_heading(slug: str) -> str:
    return slug.replace("-", " ").title()


def _four_axis_table(ca: dict[str, Any], cb: dict[str, Any], label_a: str, label_b: str) -> str:
    rows: list[str] = [f"| Dimension | {label_a} | {label_b} |", "|---|---|---|"]
    for key in ("in_character", "tool_usage", "coherence", "brand_voice"):
        sa = (ca.get(key) or {}).get("score", "—")
        sb = (cb.get(key) or {}).get("score", "—")
        rows.append(f"| **{key}** | {sa} | {sb} |")
    table = "\n".join(rows)
    summary_a = ca.get("summary", "_(no summary)_")
    summary_b = cb.get("summary", "_(no summary)_")
    return f"{table}\n\n**{label_a} summary.** {summary_a}\n\n**{label_b} summary.** {summary_b}\n"


def _single_score_table(ca: dict[str, Any], cb: dict[str, Any], label_a: str, label_b: str) -> str:
    sa = ca.get("score", "—")
    sb = cb.get("score", "—")
    tags_a = ", ".join(f"`{t}`" for t in (ca.get("failure_tags") or [])) or "_(none)_"
    tags_b = ", ".join(f"`{t}`" for t in (cb.get("failure_tags") or [])) or "_(none)_"
    rationale_a = ca.get("rationale", "_(no rationale)_")
    rationale_b = cb.get("rationale", "_(no rationale)_")
    rows = [
        f"| Field | {label_a} | {label_b} |",
        "|---|---|---|",
        f"| Score | **{sa}** | **{sb}** |",
        f"| Failure tags | {tags_a} | {tags_b} |",
    ]
    return (
        "\n".join(rows)
        + f"\n\n**{label_a} rationale.** {rationale_a}\n\n**{label_b} rationale.** {rationale_b}\n"
    )


def _format_scorer_block(ca: dict[str, Any], cb: dict[str, Any], label_a: str, label_b: str) -> str:
    """Pick a rubric renderer based on which fields are present in either grade."""
    merged_keys = set(ca.keys()) | set(cb.keys())
    if {"in_character", "tool_usage", "coherence", "brand_voice"} & merged_keys:
        return _four_axis_table(ca, cb, label_a, label_b)
    if "score" in merged_keys:
        return _single_score_table(ca, cb, label_a, label_b)
    return (
        f"```json\n// {label_a}\n{json.dumps(ca, indent=2, default=str)}\n\n"
        f"// {label_b}\n{json.dumps(cb, indent=2, default=str)}\n```\n"
    )


def _format_critique_side_by_side(state_a: dict[str, Any], state_b: dict[str, Any], label_a: str, label_b: str) -> str:
    crits_a = state_a.get("critiques") if isinstance(state_a.get("critiques"), dict) else None
    crits_b = state_b.get("critiques") if isinstance(state_b.get("critiques"), dict) else None
    if crits_a or crits_b:
        slugs = sorted(set((crits_a or {}).keys()) | set((crits_b or {}).keys()))
        chunks: list[str] = []
        for slug in slugs:
            ca = (crits_a or {}).get(slug) or {}
            cb = (crits_b or {}).get(slug) or {}
            chunks.append(f"### {_scorer_heading(slug)}\n")
            chunks.append(_format_scorer_block(ca, cb, label_a, label_b))
        return "\n".join(chunks)

    ca = state_a.get("critique") or {}
    cb = state_b.get("critique") or {}
    if not ca and not cb:
        return "_No critique recorded for either variant. Run `self_critique.py` against both state files before generating the comparison report._"
    return _format_scorer_block(ca, cb, label_a, label_b)


def _render_report(
    state_a: dict[str, Any],
    state_b: dict[str, Any],
    label_a: str,
    label_b: str,
    analysis_text: str | None,
) -> str:
    prompt_a = state_a.get("prompt") or {}
    prompt_b = state_b.get("prompt") or {}

    lines: list[str] = []
    lines.append(f"# QA comparison — {label_a} vs {label_b}\n")

    def _version_tag(p: dict[str, Any]) -> str:
        if not p.get("version"):
            return ""
        pin = "pinned" if p.get("version_pinned") else "latest at fetch time"
        return f" · version `{p['version']}` ({pin})"

    def _stack_label(state: dict[str, Any]) -> str:
        # Render `provider/api` for openai (e.g. `openai/chat`); a flat
        # provider name for the single-API providers (`google` for Gemini,
        # `anthropic` for Claude). A cross-provider comparison (e.g.
        # gpt vs claude vs gemini) should make the divergence loud — same
        # `provider:` row gets a `✗` in the parity table.
        prov = state.get("provider") or "openai"
        if prov == "google":
            return "google"
        if prov == "anthropic":
            return "anthropic"
        return f"openai/{state.get('api') or 'chat'}"

    lines.append("## Header\n")
    lines.append(f"- **{label_a}** · [{prompt_a.get('name')} ({prompt_a.get('slug')})]({prompt_a.get('url')}){_version_tag(prompt_a)}")
    lines.append(f"  · model `{state_a.get('model')}` · provider `{_stack_label(state_a)}` · params `{json.dumps(state_a.get('params') or {})}`")
    lines.append(f"- **{label_b}** · [{prompt_b.get('name')} ({prompt_b.get('slug')})]({prompt_b.get('url')}){_version_tag(prompt_b)}")
    lines.append(f"  · model `{state_b.get('model')}` · provider `{_stack_label(state_b)}` · params `{json.dumps(state_b.get('params') or {})}`")

    lines.append("\n## Setup parity\n")
    lines.append(_format_setup_parity(state_a, state_b))

    pairs_a = _walk_user_assistant(state_a.get("messages") or [])
    pairs_b = _walk_user_assistant(state_b.get("messages") or [])
    transcript, warnings = _format_paired_transcript(pairs_a, pairs_b, label_a, label_b)

    if warnings:
        lines.append("\n> ⚠️ " + "\n> ⚠️ ".join(warnings))

    lines.append("\n## Conversation transcript\n")
    lines.append(transcript)

    lines.append("\n## Tool defaults per variant\n")
    lines.append(_format_tool_defaults(state_a, state_b))

    lines.append("\n## Token usage\n")
    lines.append(_format_token_usage_side_by_side(state_a, state_b, label_a, label_b))

    lines.append("\n## GPT critique scores\n")
    lines.append(_format_critique_side_by_side(state_a, state_b, label_a, label_b))

    if analysis_text:
        lines.append("\n## Claude comparison analysis\n")
        lines.append(analysis_text.rstrip() + "\n")

    lines.append("\n## Sources\n")
    if prompt_a.get("url"):
        lines.append(f"- {label_a}: [{prompt_a.get('name')}]({prompt_a.get('url')})")
    if prompt_b.get("url"):
        lines.append(f"- {label_b}: [{prompt_b.get('name')}]({prompt_b.get('url')})")
    lines.append("- [Tools/Braintrust](https://www.notion.so/34c75e23af2081968f7bf7ee43e1442a)")
    lines.append("- [Tools/OpenAI](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e)")
    return "\n".join(lines) + "\n"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    state_a_path = Path(args.state_file_a)
    state_b_path = Path(args.state_file_b)
    state_a = json.loads(state_a_path.read_text(encoding="utf-8"))
    state_b = json.loads(state_b_path.read_text(encoding="utf-8"))

    label_a = args.label_a or f"A — {_default_label(state_a)}"
    label_b = args.label_b or f"B — {_default_label(state_b)}"

    analysis_text: str | None = None
    if args.analysis_file:
        try:
            analysis_text = Path(args.analysis_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"warn: could not read --analysis-file {args.analysis_file!r}: {e}", file=sys.stderr)

    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug_a = _safe_filename((state_a.get("prompt") or {}).get("slug") or "a")
    slug_b = _safe_filename((state_b.get("prompt") or {}).get("slug") or "b")
    fname = f"qa-prompt-comparison-{slug_a}-vs-{slug_b}-{ts}.md"
    out_path = out_dir / fname
    out_path.write_text(_render_report(state_a, state_b, label_a, label_b, analysis_text), encoding="utf-8")
    print(str(out_path))

    # Also write a standalone conversation transcript per variant, so each
    # side's dialogue is always available on its own as a clickable link.
    ta = out_dir / f"transcript-{_safe_filename(args.label_a or 'A')}.md"
    tb = out_dir / f"transcript-{_safe_filename(args.label_b or 'B')}.md"
    ta.write_text(_render_standalone_transcript(state_a), encoding="utf-8")
    tb.write_text(_render_standalone_transcript(state_b), encoding="utf-8")
    print(f"TRANSCRIPT: {ta}")
    print(f"TRANSCRIPT: {tb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
