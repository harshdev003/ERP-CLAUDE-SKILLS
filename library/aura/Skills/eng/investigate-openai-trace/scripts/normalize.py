"""Completion normalization for investigate-openai-trace.

Migrated off Braintrust 2026-06-05 (PROD-2464, umbrella PROD-2456). The
Braintrust era flattened deeply-nested span JSON into a NormalizedSpan; the
OpenAI era flattens a stored chat-completion into a NormalizedCompletion.

Key shape differences vs the Braintrust span model:
  * There is no span hierarchy on OpenAI — each completion IS one LLM call.
    `root_span_id` / `span_id` / `type` / children have no analog; we keep an
    `id` (the chatcmpl-... completion id) only.
  * The structured guardrail envelope is the assistant message content:
    `choices[0].message.content` (a JSON string when the prompter emits one).
  * Metadata is a flat dict: `metadata.user` (Firebase UID; BT called this
    `userId`), `metadata.name` (the prompter — BT called this `prompterName`),
    `metadata.transaction`, `metadata.conversationId`, `metadata.dialogueId`,
    plus model/service/version/platform/premium flags.
  * `created` is unix seconds (BT used an ISO string).
  * There is NO timing/latency data on completions (only `created` + `usage`
    token counts).

`_extract_user_block` is reused verbatim from the migrated
`investigate-memory-ingestion-guardrail` skill (PROD-2463 / PR #346). It
captures the full multi-section `## User-Provided Summary` block to EOF — the
old same-level-heading extractor truncated at the first sub-heading; that bug
is deliberately not reintroduced.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# Matches any markdown heading of level 2 or deeper. Captures (hashes, title).
# We don't pin to a specific heading level because the memoryIngest prompt has
# churned between `### User-Provided Summary` and `## User-Provided Summary`
# (see ENGPROM-58). Section bounds are decided from the matched heading.
_HEADING_RE = re.compile(r"^(#{2,})\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class NormalizedCompletion:
    id: str | None              # chatcmpl-... completion id
    created: int | None         # unix seconds
    model: str | None
    prompter: str | None        # metadata.name  (BT: metadata.prompterName)
    transaction: str | None     # metadata.transaction
    user_id: str | None         # metadata.user  (BT: metadata.userId)
    conversation_id: str | None # metadata.conversationId
    dialogue_id: str | None     # metadata.dialogueId
    output_result: str | None   # parsed guardrail envelope `result`, when present
    serious_harm_flag: bool | None
    serious_harm_evidence: str | None
    usage: dict[str, Any]       # token counts
    metadata: dict[str, Any]    # full metadata dict
    raw: dict[str, Any]         # original completion doc, kept for trace mode


def assistant_content(completion: dict[str, Any]) -> Any:
    """The assistant message content from a completion doc."""
    choices = completion.get("choices") or []
    if not choices:
        return None
    return (choices[0] or {}).get("message", {}).get("content")


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return repr(value)


def parse_envelope(content: Any) -> dict[str, Any] | None:
    """Parse the structured guardrail envelope JSON from assistant `content`.

    Returns the parsed dict, or None when it isn't JSON (many prompters emit
    free-text completions with no envelope — that's expected, not an error).
    Tolerates a ```json fence.
    """
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    if not stripped:
        return None
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        stripped = "\n".join(inner).strip()
    if not stripped.startswith("{"):
        return None
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def normalize(completion: dict[str, Any]) -> NormalizedCompletion:
    """Flatten a raw stored-completion doc into a NormalizedCompletion."""
    metadata = completion.get("metadata") or {}
    envelope = parse_envelope(assistant_content(completion))

    output_result = None
    serious_harm_flag = None
    serious_harm_evidence = None
    if isinstance(envelope, dict):
        output_result = envelope.get("result")
        if "seriousHarmFlagSet" in envelope:
            serious_harm_flag = bool(envelope["seriousHarmFlagSet"])
        if "seriousHarmEvidence" in envelope:
            serious_harm_evidence = envelope["seriousHarmEvidence"]

    return NormalizedCompletion(
        id=completion.get("id"),
        created=completion.get("created"),
        model=completion.get("model"),
        prompter=metadata.get("name"),
        transaction=metadata.get("transaction"),
        user_id=metadata.get("user") or metadata.get("userId"),
        conversation_id=metadata.get("conversationId"),
        dialogue_id=metadata.get("dialogueId"),
        output_result=output_result,
        serious_harm_flag=serious_harm_flag,
        serious_harm_evidence=serious_harm_evidence,
        usage=completion.get("usage") or {},
        metadata=metadata,
        raw=completion,
    )


# ---------------------------------------------------------------------------
# memoryIngest user-summary extraction (reused verbatim from
# investigate-memory-ingestion-guardrail / PROD-2463)
# ---------------------------------------------------------------------------


def _find_heading(text: str, phrase: str) -> re.Match[str] | None:
    """First markdown heading (level 2+) whose title contains `phrase`
    (case-insensitive). Tolerates prompt revs that switch heading depth."""
    phrase_lower = phrase.lower()
    for m in _HEADING_RE.finditer(text):
        if phrase_lower in m.group(2).lower():
            return m
    return None


def _extract_user_block(text: str) -> tuple[str | None, str | None]:
    """Split the user-provided context out of the system prompt.

    IntelligenceService appends the runtime contextInstructions at the TAIL of
    the system prompt (after the static `## Output Requirements` section),
    starting with a `## User-Provided Summary` heading. The user's pasted
    summary is itself multi-section markdown with its own `##` headings
    (`## 1. Core Identity`, `## Who You Are`, …) — so the summary is NOT a
    single same-level section; it's everything from `## User-Provided Summary`
    to end-of-prompt.

    Returns (summary, user_info):
      * summary   — from the `## User-Provided Summary` heading to EOF, minus
                    a trailing `## User Information` block if one exists.
      * user_info — the `## User Information` block (to EOF) when present;
                    None otherwise. (Current prompt revs don't emit it; kept
                    for forward/backward compatibility.)
    """
    if not text:
        return None, None
    start = _find_heading(text, "User-Provided Summary")
    if start is None:
        return None, None
    tail = text[start.end():]
    ui = _find_heading(tail, "User Information")
    if ui is not None:
        summary = tail[:ui.start()].strip("\n").rstrip()
        user_info = tail[ui.end():].strip("\n").rstrip()
        return (summary or None), (user_info or None)
    return (tail.strip("\n").rstrip() or None), None


def message_text(msg: dict) -> str:
    """Extract plain text from a /messages entry (string or content-part list)."""
    content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for p in content:
            if isinstance(p, dict):
                t = p.get("text") or p.get("content")
                if t:
                    out.append(t)
        return "\n".join(out)
    return ""


def system_text_from_messages(messages: list[dict]) -> str:
    """Concatenate all system-role message text from a /messages list."""
    parts: list[str] = []
    for m in messages:
        if m.get("role") == "system":
            t = message_text(m)
            if t:
                parts.append(t)
    return "\n\n".join(parts)
