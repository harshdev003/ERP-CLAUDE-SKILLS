"""Render a Braintrust prompt template against a resolved context payload.

Braintrust prompts use mustache-style `{{placeholder}}` substitution. Three
placeholder shapes matter for our prompts:

- `{{input}}` — the chat messages array. When a template message's content is
  *exactly* `{{input}}` (or `{{input}}` with surrounding text), the entire
  message gets replaced by the user-provided messages spliced in at that
  position. We support both shapes for safety.
- `{{metadata.<dotted.path>}}` — a string lookup into the metadata dict.
  Substituted inline as plain text into the surrounding content.
- `{{<top_level_var>}}` — for structured-input prompts (Memory Sorting,
  classifiers, routers), each top-level key on the dataset row's `input`
  dict becomes a template variable. Non-string values (dict, list, number)
  are JSON-encoded into the rendered string.

This intentionally does NOT implement full mustache (no sections, partials,
inverted blocks). Our prompts don't use those.

`production_layout` turns a rendered conversation-prompt template into the
message order Intelligence Service sends from 1.40. Two template shapes exist
in Braintrust and both are handled:

- **delimited** (the standard-layout prompters' [Production] / [Development]
  templates since 2026-10-20 -- Coach, QP, QP Sentinel, QP Done, Done Prompt,
  Guardrails Handler): system
  body, system `---`, user `---`, system `{{metadata.contextInstructions}}`,
  system `## Conversation\n{{input}}`. Already in production order.
- **appended** (old eval and experiment copies): the context is appended
  to the system body after a `---` separator, then `## Conversation\n{{input}}`.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Match `{{ anything.with.dots_and_under }}` — whitespace inside is allowed.
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")


# Intelligence Service's CONTEXT_DELIMITER (utils/buildContextMessages.js).
CONTEXT_DELIMITER = "---"
_INSTRUCTION_ROLES = ("system", "developer")
_APPENDED_CONTEXT = re.compile(r"\S[\s\S]*?\{\{\s*metadata\.contextInstructions\s*\}\}\s*$")
# IS parseMessages ends a logged turn's input with this developer message.
_TIME_HEADING = "### Current Time"


class TemplateError(RuntimeError):
    pass


def _text(msg: dict[str, Any]) -> str | None:
    content = msg.get("content")
    return content if isinstance(content, str) else None


def _delimiter_index(messages: list[dict[str, Any]]) -> int | None:
    """Index of the system/developer `---` that opens a `---` (user) pair."""
    for i in range(len(messages) - 1):
        a, b = messages[i], messages[i + 1]
        if (a.get("role") in _INSTRUCTION_ROLES and (_text(a) or "").strip() == CONTEXT_DELIMITER
                and b.get("role") == "user" and (_text(b) or "").strip() == CONTEXT_DELIMITER):
            return i
    return None


def template_shape(template_messages: list[dict[str, Any]]) -> str:
    """`delimited`, `appended`, or `none` for a raw (unrendered) template."""
    if _delimiter_index(template_messages) is not None:
        return "delimited"
    for m in template_messages:
        if m.get("role") in _INSTRUCTION_ROLES and _APPENDED_CONTEXT.match(_text(m) or ""):
            return "appended"
    return "none"


def _ends_with_time(messages: list[dict[str, Any]], dev: str) -> bool:
    if not messages:
        return False
    last = messages[-1]
    text = (_text(last) or "").strip()
    return last.get("role") in _INSTRUCTION_ROLES and (text == dev.strip() or text.startswith(_TIME_HEADING))


def production_layout(
    messages: list[dict[str, Any]],
    metadata: dict[str, Any],
    instruction_role: str = "developer",
    append_developer_instructions: bool = True,
) -> list[dict[str, Any]]:
    """A rendered template in the layout Intelligence Service sends from 1.40.

    Production sends the static prompt as the system message, an instruction
    `---` + user `---` cache-delimiter pair, the context as an instruction
    message, the conversation, then the developer instructions (`### Current
    Time ...`, `metadata.developerInstructions`). `instruction_role` is the role
    for the delimiter, context and time messages: `developer` as production
    sends them, or `system` for callers whose transports only know system.

    A delimited template already renders in that order, so only the roles are
    set. An appended template has its context split off the system message.
    The time message is appended only when `append_developer_instructions` and
    the conversation does not already end with one (logged inputs from 1.40 on
    do). Messages are returned unchanged when neither shape is recognised.
    """
    metadata = metadata or {}
    out: list[dict[str, Any]] | None = None
    i = _delimiter_index(messages)
    if i is not None:
        out = [dict(m) for m in messages]
        out[i]["role"] = instruction_role
        if i + 2 < len(out) and out[i + 2].get("role") in _INSTRUCTION_ROLES:
            out[i + 2]["role"] = instruction_role
    else:
        ctx = metadata.get("contextInstructions")
        if isinstance(ctx, str) and ctx.strip():
            for j, m in enumerate(messages):
                content = _text(m)
                if m.get("role") != "system" or content is None or not content.rstrip().endswith(ctx.strip()):
                    continue
                body = re.sub(r"\s*(?:---\s*)?$", "", content.rstrip()[: -len(ctx.strip())])
                if not body:
                    continue
                out = messages[:j] + [{**m, "content": body},
                                      {"role": instruction_role, "content": CONTEXT_DELIMITER},
                                      {"role": "user", "content": CONTEXT_DELIMITER},
                                      {"role": instruction_role, "content": ctx}] + messages[j + 1:]
                break
    if out is None:
        return messages
    dev = metadata.get("developerInstructions")
    if append_developer_instructions and isinstance(dev, str) and dev.strip() and not _ends_with_time(out, dev):
        out.append({"role": instruction_role, "content": dev})
    return out


def _lookup(path: str, scope: dict[str, Any]) -> Any:
    """Resolve a dotted path like `metadata.contextInstructions` against scope."""
    parts = path.split(".")
    cur: Any = scope
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def _substitute_text(content: str, scope: dict[str, Any], warnings: list[str]) -> str:
    """Replace `{{var}}` occurrences inside a string with values from scope.

    Missing keys are replaced with empty string and warned (mirrors prod
    `${contextInstructions || ''}` behavior).
    """
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key == "input":
            value = scope.get("input", [])
            if isinstance(value, dict):
                # Structured-input prompt — `{{input}}` resolves to the full
                # template_vars dict, JSON-encoded into the surrounding text.
                # No warning: this is the expected shape for prompts whose
                # template references `{{input}}` as a whole-payload paste.
                return json.dumps(value, indent=2, ensure_ascii=False, default=str)
            # Chat-messages flavor: `{{input}}` embedded inline (rather than
            # as a whole-message placeholder) is unusual; warn and stringify.
            warnings.append(
                f"Found `{{{{input}}}}` embedded inside a longer string content; "
                f"stringifying the messages array. Most prompts use a dedicated "
                f"message whose content is just `{{{{input}}}}`."
            )
            return str(value)
        value = _lookup(key, scope)
        if value is None:
            warnings.append(
                f"Template references {{{{{key}}}}} but the resolved context has no "
                f"value for it — substituting empty string. Mirrors prod prompter "
                f"behavior (`${{contextInstructions || ''}}`)."
            )
            return ""
        # Non-string structured values (dicts, lists) — JSON-encode rather than
        # using Python's repr, so prompts that template in `{{candidates}}` or
        # `{{similarStoredMemories}}` get readable JSON. Primitives (str, int,
        # float, bool) still str() to match prior behavior.
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)

    return _PLACEHOLDER.sub(replace, content)


def render_messages(
    template_messages: list[dict[str, Any]],
    context: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Render a Braintrust template into a concrete OpenAI messages array.

    Args:
        template_messages: the raw messages from `prompt_data.prompt.messages`,
            with `{{...}}` placeholders intact.
        context: a dict with `input` (chat messages list) and `metadata` (dict).
            Matches the Context dataclass shape from braintrust_context.py.

    Returns:
        (rendered messages, warnings)
    """
    input_messages = context.get("input") or []
    metadata = context.get("metadata") or {}
    template_vars = context.get("template_vars") or {}
    is_structured = bool(template_vars)
    # The substitution scope is the context dict itself — `{{input}}` references
    # `context.input` and `{{metadata.contextInstructions}}` references
    # `context.metadata.contextInstructions`. For structured-input prompts,
    # each top-level key on `template_vars` becomes a `{{<key>}}` reference,
    # but `input` and `metadata` stay reserved so the original ergonomics
    # never break. For structured rows `{{input}}` resolves to the full
    # template_vars dict (JSON-encoded inline by _substitute_text), matching
    # Braintrust's playground behavior — many structured prompts template
    # the whole payload via `{{input}}` rather than per-key.
    scope: dict[str, Any] = dict(template_vars)
    scope["input"] = template_vars if is_structured else input_messages
    scope["metadata"] = metadata

    warnings: list[str] = []
    rendered: list[dict[str, Any]] = []

    for msg in template_messages:
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, str):
            # Multi-part content (e.g. image+text) — pass through unchanged.
            # v1 doesn't expect this for our text prompts.
            rendered.append(dict(msg))
            continue

        stripped = content.strip()
        # Whole-message input placeholder: content is just `{{input}}` (with
        # optional surrounding whitespace) OR a small wrapper like
        # `## Conversation\n{{input}}` where the placeholder is on its own line
        # and represents the full conversation injection point. We detect both
        # by checking if the only non-placeholder content is whitespace-or-
        # markdown-heading prefix.
        only_input = re.fullmatch(
            r"\s*(?:#+\s*[^\n]*\n+)?\s*\{\{\s*input\s*\}\}\s*",
            content,
        )
        if only_input:
            if is_structured:
                # Structured-input prompt with a whole-message `{{input}}`
                # placeholder — render the payload as one JSON-content
                # message in this role rather than splicing chat messages.
                payload = json.dumps(template_vars, indent=2, ensure_ascii=False, default=str)
                rendered.append({**msg, "content": payload})
            else:
                # Splice the actual conversation messages in at this position.
                # We drop the wrapper template message itself because the
                # splice already carries its own roles.
                rendered.extend(input_messages)
            continue

        # Otherwise substitute `{{...}}` inline.
        new_content = _substitute_text(content, scope, warnings)
        rendered.append({**msg, "content": new_content})

    return rendered, warnings


def system_prompt_preview(rendered: list[dict[str, Any]], max_chars: int = 600) -> str:
    """Return a short preview of the system messages for the report header."""
    system_chunks = [m.get("content", "") for m in rendered if m.get("role") == "system"]
    joined = "\n---\n".join(system_chunks)
    if len(joined) <= max_chars:
        return joined
    return joined[:max_chars] + f"\n... [truncated; full length {len(joined)} chars]"
