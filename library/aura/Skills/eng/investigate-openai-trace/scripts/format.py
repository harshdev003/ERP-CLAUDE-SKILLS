"""Per-mode digest renderers for investigate-openai-trace.

Migrated off Braintrust 2026-06-05 (PROD-2464, umbrella PROD-2456). Renders
OpenAI stored-completions instead of Braintrust spans. Each mode renders to a
single string ending in a `→ Drill in:` hint so the engineer can copy-paste
the next-step CLI command.

PII guardrail:
  - Triage (Mode 1) / focus (Mode 2) / user pivot echo only structured fields
    (completion id, prompter name, model, the parsed envelope result /
    seriousHarmFlagSet, transaction, user, created). They never echo the
    request `input` messages, the assistant `content`, or the system prompt.
  - Trace (Mode 3) echoes the full input + output + metadata by design — the
    engineer has explicitly drilled into one completion. Aura's traces
    routinely contain user conversation text — treat Mode 3 output as
    user-conversation-grade sensitive.
"""
from __future__ import annotations

import datetime
import json
from typing import Any

from normalize import (
    NormalizedCompletion,
    _extract_user_block,
    assistant_content,
    message_text,
    system_text_from_messages,
)
from openai_client import logs_url
from projects import Project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short(s: str | None, n: int = 12) -> str:
    return (s or "")[:n]


def _truncate(s: str, n: int = 4000) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + " …"


def _fmt_created(created: int | None) -> str:
    if not created:
        return "—"
    try:
        dt = datetime.datetime.fromtimestamp(int(created), tz=datetime.timezone.utc)
        return f"{created} ({dt.strftime('%Y-%m-%d %H:%M:%SZ')})"
    except (TypeError, ValueError, OverflowError):
        return str(created)


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        try:
            return json.dumps(v, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return repr(v)
    return str(v)


def _result_label(c: NormalizedCompletion) -> str:
    if c.serious_harm_flag is True:
        return "seriousHarmFlagSet=TRUE"
    if c.output_result:
        return c.output_result
    if c.serious_harm_flag is False:
        return "seriousHarmFlagSet=false"
    return "—"


# ---------------------------------------------------------------------------
# Mode 1 — Triage
# ---------------------------------------------------------------------------


def render_triage(completions: list[NormalizedCompletion], *,
                  project: Project,
                  since_human: str,
                  status_filter: str,
                  root_only: bool,
                  prompter: str | None = None) -> str:
    lines: list[str] = []
    status_label = "" if status_filter == "all" else f" status={status_filter}"
    root_label = " (guardrail-envelope completions only)" if root_only else ""
    prompter_label = f" prompter=`{prompter}`" if prompter else ""
    lines.append(
        f"Recent completions in `{project.name}` since {since_human}"
        f"{status_label}{prompter_label}{root_label}: {len(completions)} completion(s)"
    )
    lines.append("")

    if not completions:
        lines.append(
            "0 completions matched. Try a wider `--since` window, drop `--status`, "
            "or confirm the `--prompter` name (server-side `metadata.name`)."
        )
        return "\n".join(lines)

    for i, c in enumerate(completions, 1):
        name = c.prompter or "?"
        result = _result_label(c)
        tx = c.transaction or "—"
        uid = c.user_id or "—"
        lines.append(f"{i}. {name}  model={c.model or '?'}  result={result}")
        lines.append(
            f"   created={_fmt_created(c.created)}  completion={c.id}"
        )
        lines.append(f"   transaction={tx}  user={uid}")
        if c.serious_harm_evidence:
            lines.append(f"   seriousHarmEvidence={_truncate(c.serious_harm_evidence, 160)}")
        lines.append(
            f"   → Drill in: investigate openai trace --span {c.id} "
            f"--project '{project.name}'"
        )
        lines.append("")

    lines.append(
        "→ Drill in: investigate openai trace --span <chatcmpl-id> --project "
        f"'{project.name}'   (copy any completion id above)"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode 2 — Focus (cross-project transaction fan-out)
# ---------------------------------------------------------------------------


def render_focus(transaction_uuid: str,
                 by_project: dict[Project, list[NormalizedCompletion]],
                 *, lookback_human: str | None = None) -> str:
    total = sum(len(v) for v in by_project.values())
    lines: list[str] = []
    lines.append(
        f"Transaction `{transaction_uuid}` — {total} completion(s) across "
        f"{len(by_project)} project(s):"
    )
    lines.append("")

    if total == 0:
        lines.append(
            "0 completions matched. Two likely causes:\n"
            "  1. The transaction predates OpenAI's metadata rollout (2026-05-12) "
            "or is older than OpenAI's stored-completion retention horizon.\n"
            "  2. The UUID is wrong — re-check the `*Transaction:*` line in the alert.\n"
            "Note: `metadata.transaction` is a server-side filter, so a 0-row result "
            "means OpenAI has no stored completion tagged with this transaction."
        )
        return "\n".join(lines)

    for project, completions in by_project.items():
        if not completions:
            continue
        lines.append(f"=== {project.full_name} — {len(completions)} completion(s) ===")
        # Chrono order within a project.
        for c in sorted(completions, key=lambda x: x.created or 0):
            name = c.prompter or "?"
            result = _result_label(c)
            lines.append(
                f"  [{name}]  model={c.model or '?'}  result={result}  "
                f"created={_fmt_created(c.created)}"
            )
            lines.append(f"          completion={c.id}  user={c.user_id or '—'}")
            if c.conversation_id:
                lines.append(f"          conversationId={c.conversation_id}")
            if c.dialogue_id:
                lines.append(f"          dialogueId={c.dialogue_id}")
            if c.serious_harm_flag is not None:
                lines.append(
                    f"          seriousHarm={'YES' if c.serious_harm_flag else 'no'}  "
                    f"evidence={_truncate(c.serious_harm_evidence or '—', 120)}"
                )
            lines.append(f"          OpenAI Logs: {logs_url(c.id)}")
            lines.append(
                f"          → Drill in: investigate openai trace --span {c.id} "
                f"--project '{project.name}'"
            )
        lines.append("")

    # Output contract note — still holds post-migration.
    lines.append(
        "Note (output contract): `conversationId` on a memory-ingest completion is the "
        "memory-ingestion run's own id, NOT the user's live coach conversation in the "
        "Conversation project. To find a plausibly-related live conversation, use "
        "`metadata.user` + the transaction's `created` time to bracket a search "
        "(e.g. `data:analyze-user-conversations --user-id <uid>`)."
    )
    lines.append("")
    lines.append(
        "→ Drill in: investigate openai trace --span <chatcmpl-id> --project <name>   "
        "(drill into any completion above)"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode 3 — Trace (one completion, full payload)
# ---------------------------------------------------------------------------


def render_trace(completion: NormalizedCompletion,
                 input_messages: list[dict],
                 *, project: Project) -> str:
    lines: list[str] = []
    lines.append(f"Completion [{completion.prompter or '?'}]  (id={completion.id})")
    lines.append(f"Project:      {project.full_name} ({project.project_id})")
    lines.append(f"Created:      {_fmt_created(completion.created)}")
    lines.append(f"Model:        {completion.model or '?'}")
    if completion.transaction:
        lines.append(f"Transaction:  {completion.transaction}")
    if completion.user_id:
        lines.append(f"User:         {completion.user_id}")
    if completion.conversation_id:
        lines.append(f"ConversationId: {completion.conversation_id}")
    if completion.dialogue_id:
        lines.append(f"DialogueId:   {completion.dialogue_id}")
    result = _result_label(completion)
    if result != "—":
        lines.append(f"Result:       {result}")
    if completion.serious_harm_evidence:
        lines.append(f"SeriousHarmEvidence: {completion.serious_harm_evidence}")
    usage = completion.usage or {}
    if usage:
        lines.append(
            f"Tokens:       prompt={usage.get('prompt_tokens')}  "
            f"completion={usage.get('completion_tokens')}  "
            f"total={usage.get('total_tokens')}"
        )
    lines.append(f"OpenAI Logs:  {logs_url(completion.id)}")
    lines.append("")

    # Input — the request messages from /messages.
    lines.append(f"--- Input ({len(input_messages)} message(s)) ---")
    if input_messages:
        rendered = []
        for m in input_messages:
            role = m.get("role", "?")
            body = message_text(m)
            if not body and m.get("tool_calls"):
                body = _stringify(m.get("tool_calls"))
            rendered.append(f"[{role}]\n{body}")
        lines.append(_truncate("\n\n".join(rendered), 8000) or "(empty)")
    else:
        lines.append("(no input messages returned by /messages)")
    lines.append("")

    # Output — the assistant message content.
    lines.append("--- Output ---")
    lines.append(_truncate(_stringify(assistant_content(completion.raw)), 8000) or "(empty)")
    lines.append("")

    # Metadata.
    if completion.metadata:
        lines.append("--- Metadata ---")
        lines.append(_truncate(_stringify(completion.metadata), 2000))
        lines.append("")

    # memoryIngest user-summary extraction — from the system message.
    system_text = system_text_from_messages(input_messages)
    summary, user_info = _extract_user_block(system_text)
    if summary:
        lines.append("--- User-Provided Summary (extracted from system prompt) ---")
        lines.append(_truncate(summary, 8000))
        lines.append("")
    if user_info:
        lines.append("--- User Information (extracted from system prompt) ---")
        lines.append(_truncate(user_info, 2000))
        lines.append("")

    lines.append("→ Done — last mode in the chain.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# User pivot
# ---------------------------------------------------------------------------


def render_user(by_project: dict[Project, list[NormalizedCompletion]],
                *, user_id: str, since_human: str) -> str:
    total = sum(len(v) for v in by_project.values())
    lines: list[str] = []
    lines.append(
        f"User `{user_id}` — {total} completion(s) across "
        f"{len(by_project)} project(s) (since {since_human}):"
    )
    lines.append("")
    if total == 0:
        lines.append(
            "0 completions matched. `metadata.user` is a server-side filter, so a "
            "0-row result means OpenAI has no stored completion tagged with this UID "
            "in the window — double-check the Firebase UID and widen `--since`."
        )
        return "\n".join(lines)
    for project, completions in by_project.items():
        if not completions:
            continue
        lines.append(f"=== {project.full_name} — {len(completions)} completion(s) ===")
        for c in completions:
            name = c.prompter or "?"
            result = _result_label(c)
            tx = c.transaction or "—"
            lines.append(
                f"  {name}  model={c.model or '?'}  result={result}  "
                f"created={_fmt_created(c.created)}"
            )
            lines.append(f"    completion={c.id}  transaction={tx}")
            if tx != "—":
                lines.append(f"    → Drill in: investigate openai trace --transaction {tx}")
            else:
                lines.append(
                    f"    → Drill in: investigate openai trace --span {c.id} "
                    f"--project '{project.name}'"
                )
        lines.append("")
    lines.append(
        "→ Drill in: investigate openai trace --transaction <uuid>   "
        "(fan a transaction out across projects)"
    )
    return "\n".join(lines)
