"""Stage two of a QA run: send the current message history to the configured
provider once.

Reads the run-state JSON from --state-file, makes a single API call against
the provider+API pair recorded on the state (`state.provider` ∈
openai/google/anthropic, `state.api` ∈ chat/responses inside the OpenAI
branch), appends the resulting assistant message — translated back into
OpenAI Chat shape — to `state.messages`, and writes the updated state back.

Emits a brief summary on stdout for the orchestrator (Claude) to read:

    {
      "stop": true|false,
      "finish_reason": "stop|tool_calls|...",
      "assistant_text": "..."          # present when stop=true (or whenever the model emitted text)
      "tool_calls": [                  # present when stop=false
        {
          "id": "call_abc",
          "name": "recommendTracks",
          "arguments": {...parsed JSON...},
          "raw_arguments": "...raw string...",
          "tool": {                    # tool registry entry, embedded for convenience
            "description": "...",
            "parameters": {...},
            "returns_schema": {...} | null,
            "function_id": "..." | null,
            "has_real_impl": true|false
          }
        }
      ]
    }

`state.messages` stays in Chat Completions shape across all providers — the
Responses-, Gemini-, and Anthropic-shape translations happen only inside
`_run_responses`, `_run_gemini`, and `_run_anthropic` respectively. That keeps
the report writers, comparison aggregator, sweep aggregator, and scorers
untouched.

Gemini specifics (ENGPROM-53):
  * Chat tool-call ids are not transmitted to Gemini — Gemini matches function
    calls to their responses by `name` + position within the same turn. We
    synthesize ids on output (`call_<turn>_<8hex>`) so `apply_tool_default.py`
    still has something to anchor on.
  * Gemini 2.5+ models return a `thought_signature` (raw bytes) on parts that
    came from thinking. When the conversation continues, any function-call
    part must be replayed with its signature attached — Gemini 400s otherwise
    with "Function call is missing a thought_signature". We persist the
    signature as base64 on the assistant `tool_calls` entry under a non-
    standard `_gemini_thought_signature` key, and re-attach it during Chat
    → Gemini translation.

Anthropic specifics (ENGPROM-54):
  * Anthropic preserves arbitrary `tool_use.id` strings round-trip — verified
    live 2026-05-21. We pass the OpenAI-style `tool_calls[i].id` through as
    the Anthropic `tool_use.id` directly. No synthesis needed.
  * System messages route to a top-level `system` kwarg, NOT a `messages[]`
    entry (mirrors Gemini's `system_instruction` routing).
  * `max_tokens` is REQUIRED by Anthropic. If Braintrust didn't set one we
    default to 4096 and emit a render-warning so the choice is visible in the
    report.
  * `temperature` clamping: Anthropic accepts 0..1; OpenAI allows 0..2. Values
    > 1.0 are clamped to 1.0 with a render-warning.
  * `thinking` content blocks (extended-thinking models) are discarded from
    `state.messages` and captured on `state.turns[].reasoning`. The `signature`
    field is preserved when present.

Usage:

    python3 send_turn.py --state-file path/to/state.json

Reads/writes the state file in place.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# Make sibling modules importable when invoked as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openai import OpenAI, OpenAIError

import service_tier

# Braintrust stores a few keys in `prompt_data.options.params` that are UI-only
# hints, not OpenAI API params. Stripped for both Chat Completions and Responses.
#
# `verbosity` and `reasoning_effort` are NOT in this set — both are first-class
# OpenAI params on the GPT-5 family (verified 2026-05-21 against the live API:
# gpt-5.4 + verbosity=low and reasoning_effort=low both return 200 OK on Chat
# Completions; the model rejects unsupported values per-model). Earlier
# versions of this script stripped `verbosity`; that bug silently dropped
# load-bearing config on every QA run.
_BRAINTRUST_UI_ONLY_KEYS = {"use_cache", "store"}

# Chat-only params that responses.create() rejects with TypeError (not
# OpenAIError, so they'd escape the main() handler and crash). Stripped before
# calling Responses. `max_tokens` is semantically equivalent to Responses'
# `max_output_tokens` and translated rather than dropped.
_CHAT_ONLY_KEYS_TO_DROP = {
    "frequency_penalty",
    "presence_penalty",
    "stop",
    "logprobs",
    "top_logprobs",
    "n",
    "logit_bias",
}

# Param keys that the Responses SDK does not accept as flat kwargs. We map
# these to their nested form before calling client.responses.create.
#   reasoning_effort  →  reasoning={"effort": ...}
#   verbosity         →  text={"verbosity": ...}
#
# Chat Completions accepts both flat — no translation needed there.
#
# `"none"` sentinel handling (verified live 2026-05-21):
#   * `reasoning_effort: "none"` — accepted on both Chat (200 OK) and Responses
#     (`reasoning.effort: "none"` returns 200 OK). Pass through verbatim.
#   * `tool_choice: "none"` — real OpenAI value that disables tools; pass through.
#   * `verbosity: "none"` — rejected by both APIs with `invalid_value` (only
#     low/medium/high are supported). Braintrust uses "none" as a UI placeholder
#     for "unset" — strip it so the API call doesn't 400 on a UI artifact.


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Send one turn of a QA run against OpenAI."
    )
    p.add_argument(
        "--state-file",
        required=True,
        help="Path to the run-state JSON written by setup_run.py.",
    )
    service_tier.add_argument(p)
    return p.parse_args()


def _client() -> OpenAI:
    key = os.environ.get("OPENAI_EVALUATIONS_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_EVALUATIONS_API_KEY is not set. Add it to ~/.claude/settings.json `env` "
            "and restart Claude Code; inside a Cowork sandbox, paste-export it once per session."
        )
    return OpenAI(api_key=key)


def _sanitize_common(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (raw or {}).items():
        if k in _BRAINTRUST_UI_ONLY_KEYS:
            continue
        # The run's tier is the run's decision, and these params are spread into
        # the call kwargs — left in, a prompt-level `service_tier` would win and
        # the turn record would still report the tier we asked for, so a report
        # could claim flex while the request billed standard. Dropped, and said
        # out loud rather than dropped quietly.
        if k == "service_tier":
            print(
                f"warning: the prompt's params set service_tier={v!r}; ignoring it — "
                "the run's own tier (--service-tier / AINATIVE_OPENAI_SERVICE_TIER) wins.",
                file=sys.stderr,
            )
            continue
        # Drop the Braintrust UI placeholder for verbosity (only low/medium/high
        # are valid OpenAI values). Leave reasoning_effort: "none" alone — the
        # live API accepts it.
        if k == "verbosity" and v == "none":
            continue
        out[k] = v
    return out


def _params_for_chat(raw: dict[str, Any]) -> dict[str, Any]:
    return _sanitize_common(raw)


def _params_for_responses(raw: dict[str, Any]) -> dict[str, Any]:
    """Map Chat-flavoured params to the shape Responses.create() accepts.

    The Responses SDK does not shim flat `verbosity` or `reasoning_effort` —
    both raise TypeError. They get nested:
        reasoning_effort: "low"  →  reasoning={"effort": "low"}
        verbosity: "low"         →  text={"verbosity": "low"}

    Chat-only params (frequency_penalty, stop, etc.) are dropped — Responses
    raises TypeError on them, which is not an OpenAIError subclass and would
    escape the main() handler. `max_tokens` is renamed to `max_output_tokens`.
    """
    sanitized = _sanitize_common(raw)
    out: dict[str, Any] = {}
    for k, v in sanitized.items():
        if k in _CHAT_ONLY_KEYS_TO_DROP:
            continue
        if k == "max_tokens":
            out["max_output_tokens"] = v
        elif k == "reasoning_effort":
            out.setdefault("reasoning", {})["effort"] = v
        elif k == "verbosity":
            out.setdefault("text", {})["verbosity"] = v
        else:
            out[k] = v
    return out


def _strip_nones(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Chat-shape `state.messages` into Responses `input` items.

    Translation table (Chat → Responses input):
      system/developer   →  {role: "developer", content: [{type: "input_text", text}]}
      user               →  {role: "user", content: [{type: "input_text", text}]}
      assistant text     →  {role: "assistant", content: [{type: "output_text", text}]}
      assistant + calls  →  one {type: "function_call", call_id, name, arguments} item per call
                            (assistant text content, if any, becomes a preceding output_text item)
      tool               →  {type: "function_call_output", call_id, output}
    """
    items: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("system", "developer", "user"):
            text = content if isinstance(content, str) else json.dumps(content or "", default=str)
            # Responses API officially uses `role: "developer"` (system is a
            # deprecated alias the live API still accepts but could drop in a
            # future release). Rename at the boundary to stay forward-compatible
            # while keeping `state.messages` in canonical Chat shape.
            out_role = "developer" if role in ("system", "developer") else role
            items.append({
                "role": out_role,
                "content": [{"type": "input_text", "text": text or ""}],
            })
            continue
        if role == "assistant":
            text = content if isinstance(content, str) else ""
            tool_calls = m.get("tool_calls") or []
            if text:
                items.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                })
            for tc in tool_calls:
                fn = tc.get("function") or {}
                items.append({
                    "type": "function_call",
                    "call_id": tc.get("id") or "",
                    "name": fn.get("name") or "",
                    "arguments": fn.get("arguments") or "{}",
                })
            continue
        if role == "tool":
            output = content if isinstance(content, str) else json.dumps(content, default=str)
            items.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id") or "",
                "output": output or "",
            })
            continue
    return items


def _is_cache_delimiter(text: str | None, emitted: list[Any]) -> bool:
    """The user `---` of IS 1.40's cache-delimiter pair, ahead of the conversation.

    It exists for OpenAI prompt caching. Gemini and Anthropic fold every
    system message into one system prompt, which would leave this `---` as a
    user turn right before the first real one — two user turns in a row.
    """
    return not emitted and (text or "").strip() == "---"


def _run_chat(client: OpenAI, state: dict[str, Any], tier: str) -> dict[str, Any]:
    """Execute one turn against Chat Completions. Returns the summary dict.

    Mutates state in place: appends the assistant message to state.messages
    and a turn record to state.turns.
    """
    sanitized = _params_for_chat(state.get("params") or {})
    kwargs: dict[str, Any] = {
        "model": state["model"],
        "messages": state["messages"],
        "service_tier": tier,
        **sanitized,
    }
    # Fall back to legacy `state.tools` (Chat-shape) for state files written
    # by older setup_run.py, mirroring the `state.api` default below.
    tools = state.get("tools_chat") or state.get("tools")
    if tools:
        kwargs["tools"] = tools
    else:
        # OpenAI rejects `tool_choice` when no tools are declared. Common for
        # structured-output prompts (memory-sorting plan, classifiers) whose
        # Braintrust params include a default `tool_choice` but no
        # tool_functions[]. Drop the field rather than 400.
        kwargs.pop("tool_choice", None)

    response = client.chat.completions.create(**kwargs)
    choice = response.choices[0]
    msg = choice.message
    assistant_dict = _strip_nones(msg.model_dump(exclude_unset=False))

    state["messages"].append(assistant_dict)

    tool_calls = msg.tool_calls or []
    summary: dict[str, Any] = {
        "stop": not tool_calls,
        "finish_reason": choice.finish_reason,
        "assistant_text": msg.content or "",
    }
    if tool_calls:
        tool_registry = state.get("tool_registry") or {}
        rendered: list[dict[str, Any]] = []
        for tc in tool_calls:
            raw_args = tc.function.arguments or "{}"
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed_args = {}
            rendered.append({
                "id": tc.id,
                "name": tc.function.name,
                "arguments": parsed_args,
                "raw_arguments": raw_args,
                "tool": tool_registry.get(tc.function.name) or {},
            })
        summary["tool_calls"] = rendered

    usage = _normalize_usage_chat(getattr(response, "usage", None))
    state.setdefault("turns", []).append({
        "finish_reason": choice.finish_reason,
        "assistant_text": msg.content or "",
        "tool_calls": summary.get("tool_calls", []),
        "usage": usage,
        "provider": "openai",
        "api": "chat",
        # Requested vs granted: a flex call can be served on another tier under
        # capacity pressure, so the report must not claim the tier it asked for.
        "service_tier": tier,
        "service_tier_granted": getattr(response, "service_tier", None),
    })
    return summary


def _run_responses(client: OpenAI, state: dict[str, Any], tier: str) -> dict[str, Any]:
    """Execute one turn against the Responses API. Returns the summary dict.

    Mutates state in place: appends the translated assistant message (and any
    function_call entries, in Chat shape) to state.messages and a turn record
    to state.turns. Reasoning items are discarded from state.messages (the
    Chat shape has no representation) but captured on the turn record.
    """
    sanitized = _params_for_responses(state.get("params") or {})
    kwargs: dict[str, Any] = {
        "model": state["model"],
        "input": _to_responses_input(state["messages"]),
        "service_tier": tier,
        **sanitized,
    }
    tools = state.get("tools_responses")
    if tools:
        kwargs["tools"] = tools
    else:
        # Same as Chat branch: drop tool_choice when no tools are declared
        # so structured-output prompts don't 400.
        kwargs.pop("tool_choice", None)

    response = client.responses.create(**kwargs)
    output_items = list(getattr(response, "output", []) or [])

    assistant_text = ""
    tool_calls_chat: list[dict[str, Any]] = []
    rendered_calls: list[dict[str, Any]] = []
    reasoning_items: list[dict[str, Any]] = []
    tool_registry = state.get("tool_registry") or {}

    for item in output_items:
        kind = getattr(item, "type", None)
        if kind == "message":
            for chunk in getattr(item, "content", []) or []:
                if getattr(chunk, "type", None) == "output_text":
                    text = getattr(chunk, "text", "") or ""
                    assistant_text = (assistant_text + text) if assistant_text else text
        elif kind == "function_call":
            call_id = getattr(item, "call_id", None) or getattr(item, "id", None) or ""
            name = getattr(item, "name", "") or ""
            raw_args = getattr(item, "arguments", "") or "{}"
            tool_calls_chat.append({
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": raw_args},
            })
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed_args = {}
            rendered_calls.append({
                "id": call_id,
                "name": name,
                "arguments": parsed_args,
                "raw_arguments": raw_args,
                "tool": tool_registry.get(name) or {},
            })
        elif kind == "reasoning":
            try:
                reasoning_items.append(item.model_dump(exclude_unset=False))
            except Exception:
                reasoning_items.append({"type": "reasoning"})

    assistant_msg: dict[str, Any] = {"role": "assistant"}
    if assistant_text:
        assistant_msg["content"] = assistant_text
    if tool_calls_chat:
        assistant_msg["tool_calls"] = tool_calls_chat
    if "content" not in assistant_msg and "tool_calls" not in assistant_msg:
        assistant_msg["content"] = ""
    state["messages"].append(assistant_msg)

    # Responses doesn't return a `finish_reason` field in the Chat sense; we
    # synthesize one so the rest of the pipeline (report writer, scoreboard)
    # keeps working.
    finish_reason = "tool_calls" if tool_calls_chat else "stop"
    summary: dict[str, Any] = {
        "stop": not tool_calls_chat,
        "finish_reason": finish_reason,
        "assistant_text": assistant_text,
    }
    if rendered_calls:
        summary["tool_calls"] = rendered_calls

    usage = _normalize_usage_responses(getattr(response, "usage", None))
    turn_record: dict[str, Any] = {
        "finish_reason": finish_reason,
        "assistant_text": assistant_text,
        "tool_calls": rendered_calls,
        "usage": usage,
        "provider": "openai",
        "api": "responses",
        "service_tier": tier,
        "service_tier_granted": getattr(response, "service_tier", None),
    }
    if reasoning_items:
        turn_record["reasoning"] = reasoning_items
    state.setdefault("turns", []).append(turn_record)
    return summary


# -- Gemini ------------------------------------------------------------------
#
# Param translation (Braintrust/OpenAI shape → Gemini config). Verified live on
# `gemini-3.5-flash` 2026-05-21:
#   temperature, top_p       → passthrough on config
#   max_tokens / max_completion_tokens → max_output_tokens
#   reasoning_effort         → thinking_config.thinking_budget (low: 128,
#                              medium: 1024, high: 8192; Gemini 2.5+ only;
#                              "none" disables thinking → 0)
#   tool_choice              → tool_config.function_calling_config.{mode,
#                              allowed_function_names}
#   verbosity                → dropped (no Gemini equivalent; warn on stderr)
#   presence/frequency_penalty → dropped (no Gemini equivalent; warn on stderr)
#   use_cache, store         → dropped (Braintrust UI hints, already in
#                              _BRAINTRUST_UI_ONLY_KEYS)
_GEMINI_THINKING_BUDGET_BY_EFFORT = {
    "none": 0,
    "low": 128,
    "medium": 1024,
    "high": 8192,
}


def _params_for_gemini(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Map Chat-flavoured params to Gemini's `config` shape.

    `raw` is `state.params` (i.e. what Braintrust stored under
    `prompt_data.options.params`). Returns `(config_partial, warnings)` so
    the caller can append `render_warnings` (visible in the report) —
    matches the `_params_for_anthropic` contract.
    """
    sanitized = _sanitize_common(raw)
    out: dict[str, Any] = {}
    warnings: list[str] = []
    for k, v in sanitized.items():
        if k in ("temperature", "top_p"):
            out[k] = v
        elif k in ("max_tokens", "max_completion_tokens"):
            out["max_output_tokens"] = v
        elif k == "reasoning_effort":
            budget = _GEMINI_THINKING_BUDGET_BY_EFFORT.get(v)
            if budget is None:
                warnings.append(
                    f"reasoning_effort={v!r}: no Gemini thinking_budget mapping; dropping"
                )
                continue
            out.setdefault("thinking_config", {})["thinking_budget"] = budget
        elif k == "tool_choice":
            tc = _gemini_tool_config_from_tool_choice(v)
            if tc is not None:
                out["tool_config"] = tc
        elif k == "stop":
            # `stop` maps to Gemini's `stop_sequences` (same shape as
            # Anthropic). Accept either bare string or list.
            out["stop_sequences"] = v if isinstance(v, list) else [v]
        elif k == "verbosity" or k in _CHAT_ONLY_KEYS_TO_DROP:
            warnings.append(f"{k}={v!r}: no Gemini equivalent; dropping")
        else:
            # Unknown key — pass through. Gemini will 400 if it doesn't
            # accept the kwarg, which is the same failure mode OpenAI has.
            out[k] = v
    return out, warnings


def _gemini_tool_config_from_tool_choice(tc: Any) -> dict[str, Any] | None:
    """Translate OpenAI `tool_choice` to Gemini's tool_config shape.

    OpenAI:
      "auto"                                 → mode AUTO
      "none"                                 → mode NONE
      {"type": "function", "function": {"name": "x"}} → mode ANY +
                                                allowed_function_names=["x"]
      "required"                             → mode ANY (any function, any name)
    """
    if tc is None:
        return None
    if isinstance(tc, str):
        if tc == "auto":
            return {"function_calling_config": {"mode": "AUTO"}}
        if tc == "none":
            return {"function_calling_config": {"mode": "NONE"}}
        if tc == "required":
            return {"function_calling_config": {"mode": "ANY"}}
        return None
    if isinstance(tc, dict):
        fn = (tc.get("function") or {}).get("name")
        if fn:
            return {
                "function_calling_config": {
                    "mode": "ANY",
                    "allowed_function_names": [fn],
                },
            }
    return None


def _to_gemini_contents(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    """Translate Chat-shape `state.messages` into `(contents, system_instruction)`.

    Gemini takes the system prompt as a top-level `config.system_instruction`
    string, NOT a contents[] entry. We strip system messages out of the contents
    list and concatenate their content into the returned system_instruction
    (joined with double newlines if there are multiple).

    Translation table (mirrors the design table in this skill's docstring):
      system            → top-level system_instruction (NOT contents)
      user              → {role: "user", parts: [{text}]}
      assistant text    → {role: "model", parts: [{text}]}
      assistant + calls → {role: "model", parts: [{function_call: {name, args}},
                                                  ... + signature re-attached]}
      tool              → {role: "user", parts: [{function_response:
                                                  {name, response: dict}}]}
    """
    contents: list[dict[str, Any]] = []
    system_chunks: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("system", "developer"):
            # `developer` arrives in replayed logged inputs (IS 1.40's
            # `### Current Time` message); it is a system instruction here.
            text = content if isinstance(content, str) else json.dumps(content or "", default=str)
            if text:
                system_chunks.append(text)
            continue
        if role == "user":
            text = content if isinstance(content, str) else json.dumps(content or "", default=str)
            if _is_cache_delimiter(text, contents):
                continue
            contents.append({"role": "user", "parts": [{"text": text or ""}]})
            continue
        if role == "assistant":
            parts: list[dict[str, Any]] = []
            text = content if isinstance(content, str) else ""
            if text:
                parts.append({"text": text})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                raw_args = fn.get("arguments") or "{}"
                try:
                    parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    parsed_args = {}
                fc_part: dict[str, Any] = {
                    "function_call": {
                        "name": fn.get("name") or "",
                        "args": parsed_args if isinstance(parsed_args, dict) else {},
                    },
                }
                # Re-attach the thought_signature when present. Gemini 3.5+
                # rejects function_call parts without it on subsequent turns
                # ("Function call is missing a thought_signature"). The
                # signature was captured at the API boundary in
                # `_from_gemini_output` and stashed as base64 on this tool_call
                # entry under `_gemini_thought_signature`.
                sig_b64 = tc.get("_gemini_thought_signature")
                if sig_b64:
                    try:
                        fc_part["thought_signature"] = base64.b64decode(sig_b64)
                    except (ValueError, TypeError):
                        pass
                parts.append(fc_part)
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue
        if role == "tool":
            name = m.get("name") or ""
            raw_resp = content
            # Gemini wants the response as a JSON object; coerce strings.
            if isinstance(raw_resp, str):
                try:
                    parsed = json.loads(raw_resp)
                except json.JSONDecodeError:
                    parsed = {"value": raw_resp}
                response_obj = parsed if isinstance(parsed, dict) else {"value": parsed}
            elif isinstance(raw_resp, dict):
                response_obj = raw_resp
            else:
                response_obj = {"value": raw_resp}
            part = {"function_response": {"name": name, "response": response_obj}}
            # Gemini strictly alternates user/model roles. When the model emits
            # multiple parallel function_call parts in one model turn,
            # apply_tool_default appends one `role: "tool"` entry per call to
            # state.messages. Naively translating each to its own
            # `{role: "user"}` content produces consecutive user turns and a
            # 400 ("contents must alternate between user and model"). Merge
            # into the preceding user content when its parts are all
            # function_response.
            prev = contents[-1] if contents else None
            if (
                prev is not None
                and prev.get("role") == "user"
                and isinstance(prev.get("parts"), list)
                and all(
                    isinstance(p, dict) and "function_response" in p
                    for p in prev["parts"]
                )
            ):
                prev["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
            continue
    system_instruction = "\n\n".join(system_chunks) if system_chunks else None
    return contents, system_instruction


def _from_gemini_output(response: Any, turn_index: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    """Translate one Gemini response into Chat-shape state appendices.

    Returns `(assistant_msg, rendered_calls, reasoning_items, finish_reason)`:
      * assistant_msg — to be appended to `state.messages`
      * rendered_calls — what `send_turn`'s summary surfaces to the orchestrator
      * reasoning_items — captured under `turn_record.reasoning` (NOT persisted
        in state.messages)
      * finish_reason — synthesized "stop"|"tool_calls" so the rest of the
        pipeline (report writer, scoreboard) keeps working
    """
    assistant_text = ""
    tool_calls_chat: list[dict[str, Any]] = []
    rendered_calls: list[dict[str, Any]] = []
    reasoning_items: list[dict[str, Any]] = []

    candidates = list(getattr(response, "candidates", []) or [])
    if not candidates:
        return ({"role": "assistant", "content": ""}, [], [], "stop")
    content = getattr(candidates[0], "content", None)
    parts = list(getattr(content, "parts", []) or []) if content else []

    call_idx = 0
    for p in parts:
        # Thinking parts: text=null, thought=True (older SDK) or non-null
        # thought field. Capture as reasoning, do NOT add to assistant_text.
        is_thought = bool(getattr(p, "thought", None))
        fc = getattr(p, "function_call", None)
        text = getattr(p, "text", None)
        sig = getattr(p, "thought_signature", None)

        if is_thought and text:
            reasoning_items.append({"type": "thinking", "text": text})
            continue
        if fc is not None and getattr(fc, "name", None):
            args = dict(getattr(fc, "args", {}) or {})
            synth_id = f"call_{turn_index}_{uuid.uuid4().hex[:8]}"
            raw_args_json = json.dumps(args, default=str)
            tc_chat: dict[str, Any] = {
                "id": synth_id,
                "type": "function",
                "function": {"name": fc.name, "arguments": raw_args_json},
            }
            if sig:
                tc_chat["_gemini_thought_signature"] = base64.b64encode(sig).decode()
            tool_calls_chat.append(tc_chat)
            rendered_calls.append({
                "id": synth_id,
                "name": fc.name,
                "arguments": args,
                "raw_arguments": raw_args_json,
                # tool registry lookup happens in `_run_gemini` (it has state).
                "tool": {},
            })
            call_idx += 1
            continue
        if text:
            assistant_text = (assistant_text + text) if assistant_text else text

    assistant_msg: dict[str, Any] = {"role": "assistant"}
    if assistant_text:
        assistant_msg["content"] = assistant_text
    if tool_calls_chat:
        assistant_msg["tool_calls"] = tool_calls_chat
    if "content" not in assistant_msg and "tool_calls" not in assistant_msg:
        assistant_msg["content"] = ""

    finish_reason = "tool_calls" if tool_calls_chat else "stop"
    return assistant_msg, rendered_calls, reasoning_items, finish_reason


def _run_gemini(state: dict[str, Any]) -> dict[str, Any]:
    """Execute one turn against Gemini. Returns the summary dict.

    Mutates state in place: appends the translated assistant message (and any
    function_call entries, in Chat shape with `_gemini_thought_signature`
    sidecar) to state.messages and a turn record to state.turns. Thinking
    parts are discarded from state.messages and captured on the turn record.

    Deferred-imports `google-genai` so this module imports cleanly when the
    SDK isn't installed and the user is only running OpenAI flows.
    """
    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError(
            "google-genai is not installed but state.provider == 'google'. "
            "Install with `uv pip install --python ~/.claude/python-env/bin/python google-genai>=2.5.0` "
            "and re-run."
        ) from e

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to ~/.claude/settings.json `env` "
            "and restart Claude Code; inside a Cowork sandbox, paste-export it "
            "once per session. Obtain at https://aistudio.google.com/apikey."
        )
    client = genai.Client(api_key=key)

    contents, sys_instr = _to_gemini_contents(state["messages"])
    cfg, warnings = _params_for_gemini(state.get("params") or {})
    # Surface translation warnings in the final report (matches Anthropic).
    if warnings:
        state.setdefault("render_warnings", []).extend(
            f"gemini: {w}" for w in warnings
        )
    if sys_instr:
        cfg["system_instruction"] = sys_instr
    tools = state.get("tools_gemini")
    if tools:
        cfg["tools"] = tools

    turn_index = len(state.get("turns") or []) + 1
    response = client.models.generate_content(
        model=state["model"],
        contents=contents,
        config=cfg,
    )

    assistant_msg, rendered_calls, reasoning_items, finish_reason = (
        _from_gemini_output(response, turn_index)
    )

    # Backfill tool registry entries on rendered_calls now that we have state.
    tool_registry = state.get("tool_registry") or {}
    for rc in rendered_calls:
        rc["tool"] = tool_registry.get(rc["name"]) or {}

    state["messages"].append(assistant_msg)

    summary: dict[str, Any] = {
        "stop": not rendered_calls,
        "finish_reason": finish_reason,
        "assistant_text": assistant_msg.get("content", "") if isinstance(assistant_msg.get("content"), str) else "",
    }
    if rendered_calls:
        summary["tool_calls"] = rendered_calls

    usage = _normalize_usage_gemini(getattr(response, "usage_metadata", None))
    turn_record: dict[str, Any] = {
        "finish_reason": finish_reason,
        "assistant_text": summary["assistant_text"],
        "tool_calls": rendered_calls,
        "usage": usage,
        "provider": "google",
        "api": "gemini",
    }
    if reasoning_items:
        turn_record["reasoning"] = reasoning_items
    state.setdefault("turns", []).append(turn_record)
    return summary


def _normalize_usage_chat(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    try:
        d = usage.model_dump(exclude_unset=False)
    except Exception:
        d = dict(usage) if isinstance(usage, dict) else {}
    prompt_details = d.get("prompt_tokens_details") or {}
    completion_details = d.get("completion_tokens_details") or {}
    return {
        "input_tokens": int(d.get("prompt_tokens") or 0),
        "cached_input_tokens": int(prompt_details.get("cached_tokens") or 0),
        "output_tokens": int(d.get("completion_tokens") or 0),
        "reasoning_tokens": int(completion_details.get("reasoning_tokens") or 0),
        "total_tokens": int(d.get("total_tokens") or 0),
        "provider": "openai",
        "api": "chat",
    }


def _normalize_usage_responses(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    try:
        d = usage.model_dump(exclude_unset=False)
    except Exception:
        d = dict(usage) if isinstance(usage, dict) else {}
    input_details = d.get("input_tokens_details") or {}
    output_details = d.get("output_tokens_details") or {}
    return {
        "input_tokens": int(d.get("input_tokens") or 0),
        "cached_input_tokens": int(input_details.get("cached_tokens") or 0),
        "output_tokens": int(d.get("output_tokens") or 0),
        "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
        "total_tokens": int(d.get("total_tokens") or 0),
        "provider": "openai",
        "api": "responses",
    }


def _normalize_usage_gemini(usage: Any) -> dict[str, Any] | None:
    """Normalize Gemini's `usage_metadata` block to the shared shape.

    Gemini's field names use a different vocabulary than OpenAI:
      prompt_token_count        → input_tokens
      cached_content_token_count → cached_input_tokens
      candidates_token_count    → output_tokens (the visible completion)
      thoughts_token_count      → reasoning_tokens (2.5+ thinking models)
      total_token_count         → total_tokens

    Note `total_token_count` already sums prompt + candidates + thoughts +
    tool_use_prompt (per the Gemini schema), so we surface it verbatim.
    """
    if usage is None:
        return None
    try:
        d = usage.model_dump(exclude_unset=False)
    except Exception:
        d = dict(usage) if isinstance(usage, dict) else {}
    return {
        "input_tokens": int(d.get("prompt_token_count") or 0),
        "cached_input_tokens": int(d.get("cached_content_token_count") or 0),
        "output_tokens": int(d.get("candidates_token_count") or 0),
        "reasoning_tokens": int(d.get("thoughts_token_count") or 0),
        "total_tokens": int(d.get("total_token_count") or 0),
        "provider": "google",
        "api": "gemini",
    }


# -- Anthropic ---------------------------------------------------------------
#
# Param translation (Braintrust/OpenAI shape → Anthropic Messages config).
# Verified live on `claude-sonnet-4-6` 2026-05-21:
#   temperature              → passthrough; clamp to ≤1.0 with warning if >1.0
#                              (Anthropic range 0..1; OpenAI allows 0..2)
#   top_p, top_k             → passthrough
#   max_tokens /             → max_tokens (REQUIRED by Anthropic — default to
#     max_completion_tokens    4096 with warning if absent)
#   reasoning_effort         → thinking: {type: "enabled", budget_tokens: N}
#                              (low: 1024, medium: 4096, high: 16384;
#                              "none" disables thinking)
#   tool_choice "auto"       → tool_choice: {type: "auto"}
#   tool_choice "none"       → tools omitted from request entirely
#   tool_choice "required"   → tool_choice: {type: "any"}
#   tool_choice {function:   → tool_choice: {type: "tool", name: <n>}
#     {name: <n>}}
#   verbosity                → dropped (no Anthropic equivalent; warn)
#   presence/frequency       → dropped (no Anthropic equivalent; warn)
#     _penalty
#   use_cache, store         → dropped (Braintrust UI hints, already in
#                              _BRAINTRUST_UI_ONLY_KEYS)
_ANTHROPIC_THINKING_BUDGET_BY_EFFORT = {
    # "none" disables thinking entirely (mirrors Gemini's mapping).
    "none": 0,
    "low": 1024,
    "medium": 4096,
    "high": 16384,
}
# Default when Braintrust didn't set max_tokens and the prompt resolved to
# Anthropic. Surfaced as a render-warning so the choice isn't invisible.
_ANTHROPIC_DEFAULT_MAX_TOKENS = 4096
# Anthropic's documented temperature ceiling (verified live 2026-05-21 — a
# `temperature=1.5` request returns 400 with "temperature: range: 0..1").
_ANTHROPIC_TEMPERATURE_MAX = 1.0


def _anthropic_supports_enabled_thinking(model: str) -> bool:
    """Models that accept `thinking: {type: "enabled", budget_tokens}`.

    Verified live 2026-05-21 on `claude-sonnet-4-6`, `claude-opus-4-6`, and
    `claude-sonnet-4-5-20250929`. `claude-opus-4-7` rejects the `enabled`
    form and requires `thinking.type=adaptive` + `output_config.effort`
    instead — qa-prompt doesn't emit that shape today, so opus-4-7 is
    excluded from the allowlist. Match by prefix so dated variants
    (`claude-sonnet-4-5-20250929`, future `-YYYYMMDD` suffixes) work.
    Strips the `anthropic/` route-prefix first so Braintrust prompts
    declared as `anthropic/claude-sonnet-4-6` (the OpenRouter-style
    naming `_detect_provider` also matches) hit the same allowlist as
    the bare `claude-...` form.
    """
    m = model.lower()
    if m.startswith("anthropic/"):
        m = m[len("anthropic/"):]
    return m.startswith((
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-sonnet-4-5",
    ))


def _params_for_anthropic(raw: dict[str, Any], model: str) -> tuple[dict[str, Any], list[str], bool]:
    """Map Chat-flavoured params to Anthropic Messages-API kwargs.

    Returns `(translated_kwargs, warnings, omit_tools)`:
      * translated_kwargs — direct kwargs for `client.messages.create`.
      * warnings — caller appends to `state.render_warnings` (visible in the
        final report).
      * omit_tools — True when `tool_choice: "none"` resolved; the caller
        skips the `tools=` kwarg entirely. Anthropic does accept
        `{type: "none"}` for tool_choice (verified live), but the ticket
        spec asks us to omit tools to keep the request minimal.

    Notable rules:
    - `max_tokens` is REQUIRED by Anthropic. Defaults to
      `_ANTHROPIC_DEFAULT_MAX_TOKENS` with a warning if absent.
    - `temperature` is clamped to `_ANTHROPIC_TEMPERATURE_MAX` with a warning.
    - `thinking: {type: "enabled"}` is gated to known-compatible models (the
      4.5 series, `claude-opus-4-6`, `claude-sonnet-4-6`). `claude-opus-4-7`
      requires the `adaptive` form which qa-prompt doesn't emit; for any
      model not on the allowlist we drop the thinking config with a warning
      rather than letting the request 400.
    """
    sanitized = _sanitize_common(raw)
    out: dict[str, Any] = {}
    warnings: list[str] = []
    omit_tools = False

    for k, v in sanitized.items():
        if k == "temperature":
            try:
                tval = float(v)
            except (TypeError, ValueError):
                # Unparseable temperature — pass through and let Anthropic 400.
                out["temperature"] = v
                continue
            if tval > _ANTHROPIC_TEMPERATURE_MAX:
                warnings.append(
                    f"temperature={v!r} > Anthropic max {_ANTHROPIC_TEMPERATURE_MAX}; "
                    f"clamped to {_ANTHROPIC_TEMPERATURE_MAX}"
                )
                out["temperature"] = _ANTHROPIC_TEMPERATURE_MAX
            else:
                out["temperature"] = tval
        elif k in ("top_p", "top_k"):
            out[k] = v
        elif k in ("max_tokens", "max_completion_tokens"):
            out["max_tokens"] = v
        elif k == "reasoning_effort":
            budget = _ANTHROPIC_THINKING_BUDGET_BY_EFFORT.get(v)
            if budget is None:
                warnings.append(
                    f"reasoning_effort={v!r}: no Anthropic thinking budget "
                    f"mapping; dropping"
                )
                continue
            if budget == 0:
                # Don't emit thinking config — Anthropic treats absence as off.
                continue
            if not _anthropic_supports_enabled_thinking(model):
                warnings.append(
                    f"reasoning_effort={v!r} requested for model {model!r} but only "
                    f"the `adaptive` form is accepted on this model (qa-prompt emits "
                    f"`thinking.type=enabled`); dropping thinking config. Use a 4.5- "
                    f"or 4.6-series model for thinking QA today."
                )
                continue
            out["thinking"] = {"type": "enabled", "budget_tokens": budget}
        elif k == "tool_choice":
            tc = _anthropic_tool_choice_from_chat(v)
            if tc == "_omit_tools":
                omit_tools = True
            elif tc is not None:
                out["tool_choice"] = tc
        elif k == "stop":
            # `stop` maps cleanly to Anthropic's `stop_sequences`. Accept
            # either a bare string (rare) or a list — Anthropic only takes
            # a list.
            out["stop_sequences"] = v if isinstance(v, list) else [v]
        elif k == "verbosity" or k in _CHAT_ONLY_KEYS_TO_DROP:
            warnings.append(f"{k}={v!r}: no Anthropic equivalent; dropping")
        else:
            # Unknown key — pass through. Anthropic will 400 if it doesn't
            # accept the kwarg, which is the same failure mode the other
            # providers have.
            out[k] = v

    # max_tokens is mandatory. If Braintrust didn't set one (or set it to a
    # falsy value), inject the default and surface the choice in the report.
    if not out.get("max_tokens"):
        warnings.append(
            f"max_tokens missing from Braintrust params; defaulting to "
            f"{_ANTHROPIC_DEFAULT_MAX_TOKENS} (Anthropic Messages API requires it)"
        )
        out["max_tokens"] = _ANTHROPIC_DEFAULT_MAX_TOKENS

    # Anthropic requires `max_tokens > thinking.budget_tokens` — verified live
    # 2026-05-21 (the API 400s with "max_tokens must be greater than
    # thinking.budget_tokens" otherwise). When `reasoning_effort` is set but
    # the Braintrust prompt's `max_tokens` is too small, raise max_tokens to
    # budget + 1024 (a buffer for the visible response) and warn so the choice
    # is visible in the report.
    thinking = out.get("thinking") or {}
    budget = int(thinking.get("budget_tokens") or 0)
    if budget and int(out["max_tokens"]) <= budget:
        new_max = budget + 1024
        warnings.append(
            f"max_tokens ({out['max_tokens']}) ≤ thinking.budget_tokens "
            f"({budget}); raised to {new_max} (Anthropic requires "
            f"max_tokens > budget_tokens)"
        )
        out["max_tokens"] = new_max

    return out, warnings, omit_tools


def _anthropic_tool_choice_from_chat(tc: Any) -> Any:
    """Translate OpenAI `tool_choice` to Anthropic's tool_choice shape.

    OpenAI:
      "auto"                                 → {"type": "auto"}
      "none"                                 → sentinel "_omit_tools" (caller
                                                drops the `tools=` kwarg)
      "required"                             → {"type": "any"}
      {"type": "function", "function": {"name": "x"}} → {"type": "tool",
                                                "name": "x"}
    """
    if tc is None:
        return None
    if isinstance(tc, str):
        if tc == "auto":
            return {"type": "auto"}
        if tc == "none":
            return "_omit_tools"
        if tc == "required":
            return {"type": "any"}
        return None
    if isinstance(tc, dict):
        fn = (tc.get("function") or {}).get("name")
        if fn:
            return {"type": "tool", "name": fn}
    return None


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    """Translate Chat-shape `state.messages` into `(messages, system)`.

    Anthropic takes the system prompt as a top-level `system` kwarg, not a
    `messages[]` entry. We strip system messages out of the messages list and
    concatenate their content into the returned `system` string (joined with
    double newlines if there are multiple).

    Translation table (mirrors the design table in this skill's docstring):
      system         → top-level system kwarg (NOT messages)
      user (str)     → {role: "user", content: [{type: "text", text}]}
      assistant text → {role: "assistant", content: [{type: "text", text}]}
      assistant +    → {role: "assistant", content: [
        tool_calls       {type: "tool_use", id, name, input}, ...
                     ]}
                     (multiple tool_calls collapse into multiple content blocks
                      within one assistant message)
      tool (Chat)    → {role: "user", content: [
                         {type: "tool_result", tool_use_id, content}
                       ]}
    """
    out_msgs: list[dict[str, Any]] = []
    system_chunks: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("system", "developer"):
            # `developer` arrives in replayed logged inputs (IS 1.40's
            # `### Current Time` message); it is a system instruction here.
            text = content if isinstance(content, str) else json.dumps(content or "", default=str)
            if text:
                system_chunks.append(text)
            continue
        if role == "user":
            text = content if isinstance(content, str) else json.dumps(content or "", default=str)
            # Anthropic rejects empty text content blocks ("text content blocks
            # must be non-empty"). When the Chat-shape message has no text
            # (e.g. an empty user seed from an unfilled template variable),
            # skip it rather than send an empty block. The conversation arc
            # still works because the next non-empty user turn will carry the
            # real content.
            if not text or _is_cache_delimiter(text, out_msgs):
                continue
            out_msgs.append({
                "role": "user",
                "content": [{"type": "text", "text": text}],
            })
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            # Re-prepend thinking blocks if this assistant turn captured any
            # in `_from_anthropic_content`. Anthropic requires thinking blocks
            # to appear FIRST in the content array and to round-trip verbatim
            # (with their cryptographic `signature`) when the same turn also
            # contains tool_use — otherwise the next call 400s. Same pattern
            # as the Gemini `_gemini_thought_signature` round-trip.
            for tb in m.get("_anthropic_thinking") or []:
                if not isinstance(tb, dict):
                    continue
                btype = tb.get("type")
                if btype == "thinking":
                    block: dict[str, Any] = {
                        "type": "thinking",
                        "thinking": tb.get("thinking") or "",
                    }
                    sig = tb.get("signature")
                    if sig:
                        block["signature"] = sig
                    blocks.append(block)
                elif btype == "redacted_thinking":
                    data = tb.get("data")
                    if data is not None:
                        blocks.append({"type": "redacted_thinking", "data": data})
            text = content if isinstance(content, str) else ""
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                raw_args = fn.get("arguments") or "{}"
                try:
                    parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    parsed_args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or "",
                    "name": fn.get("name") or "",
                    "input": parsed_args if isinstance(parsed_args, dict) else {},
                })
            if blocks:
                out_msgs.append({"role": "assistant", "content": blocks})
            continue
        if role == "tool":
            tool_call_id = m.get("tool_call_id") or ""
            raw_resp = content
            if isinstance(raw_resp, str):
                result_content = raw_resp
            else:
                # Anthropic accepts both a string and a list[content block] for
                # tool_result.content; a JSON-serialized dict as a plain string
                # is the simplest portable shape.
                result_content = json.dumps(raw_resp, default=str)
            block = {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": result_content,
            }
            # Anthropic requires alternating user/assistant turns. When the
            # model emits multiple parallel tool_use blocks in one assistant
            # message, apply_tool_default appends one `role: "tool"` entry per
            # call to state.messages. Naively translating each to its own
            # `{role: "user"}` Anthropic message produces consecutive user
            # turns and a 400. Merge into the preceding user message when its
            # content is all tool_result blocks.
            prev = out_msgs[-1] if out_msgs else None
            if (
                prev is not None
                and prev.get("role") == "user"
                and isinstance(prev.get("content"), list)
                and all(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in prev["content"]
                )
            ):
                prev["content"].append(block)
            else:
                out_msgs.append({"role": "user", "content": [block]})
            continue
    system = "\n\n".join(system_chunks) if system_chunks else None
    return out_msgs, system


def _from_anthropic_content(response: Any) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    """Translate one Anthropic response into Chat-shape state appendices.

    Returns `(assistant_msg, rendered_calls, reasoning_items, finish_reason)`:
      * assistant_msg — to be appended to `state.messages`. Carries the
        non-standard `_anthropic_thinking` field when the model emitted any
        thinking blocks, so the next turn can replay them verbatim.
      * rendered_calls — what `send_turn`'s summary surfaces to the orchestrator
      * reasoning_items — captured under `turn_record.reasoning` (NOT persisted
        in state.messages). Anthropic's usage object doesn't break thinking
        tokens out today (verified 2026-05-21), so `reasoning_tokens` stays
        at 0 in the normalized usage — usage normalization owns that field.
      * finish_reason — `tool_calls` when any tool_use blocks present,
        else the response's `stop_reason` (`end_turn`, `max_tokens`,
        `stop_sequence`, etc.) mapped through to Chat's vocabulary where
        possible.
    """
    assistant_text = ""
    tool_calls_chat: list[dict[str, Any]] = []
    rendered_calls: list[dict[str, Any]] = []
    reasoning_items: list[dict[str, Any]] = []
    # Thinking blocks to round-trip on the next turn. Anthropic requires the
    # original `{type: "thinking", thinking, signature}` blocks to be echoed
    # back verbatim in conversation history when the model emits tool_use
    # alongside thinking — otherwise the next call 400s with
    # "thinking blocks must come first in the message" or refuses the
    # signature. We stash them on a non-standard `_anthropic_thinking` field
    # on the assistant message so `_to_anthropic_messages` can re-prepend them.
    # Same pattern as `_gemini_thought_signature` from ENGPROM-53.
    anthropic_thinking_blocks: list[dict[str, Any]] = []

    content_blocks = list(getattr(response, "content", []) or [])
    for block in content_blocks:
        btype = getattr(block, "type", None)
        if btype == "text":
            t = getattr(block, "text", "") or ""
            assistant_text = (assistant_text + t) if assistant_text else t
        elif btype == "tool_use":
            block_id = getattr(block, "id", "") or ""
            name = getattr(block, "name", "") or ""
            tinput = getattr(block, "input", None) or {}
            if not isinstance(tinput, dict):
                tinput = {}
            raw_args_json = json.dumps(tinput, default=str)
            tool_calls_chat.append({
                "id": block_id,
                "type": "function",
                "function": {"name": name, "arguments": raw_args_json},
            })
            rendered_calls.append({
                "id": block_id,
                "name": name,
                "arguments": tinput,
                "raw_arguments": raw_args_json,
                "tool": {},  # backfilled in _run_anthropic once state is in scope
            })
        elif btype == "thinking":
            t = getattr(block, "thinking", None) or ""
            sig = getattr(block, "signature", None)
            entry: dict[str, Any] = {"type": "thinking"}
            if t:
                entry["text"] = t
            if sig:
                entry["signature"] = sig
            reasoning_items.append(entry)
            # Also keep a verbatim copy for the next-turn round-trip.
            replay_block: dict[str, Any] = {"type": "thinking", "thinking": t}
            if sig:
                replay_block["signature"] = sig
            anthropic_thinking_blocks.append(replay_block)
        elif btype == "redacted_thinking":
            # Anthropic returns redacted_thinking blocks when the API can't
            # surface the underlying thought; they must still round-trip
            # verbatim on the next turn or the API 400s.
            data = getattr(block, "data", None)
            if data is not None:
                anthropic_thinking_blocks.append({"type": "redacted_thinking", "data": data})

    assistant_msg: dict[str, Any] = {"role": "assistant"}
    if assistant_text:
        assistant_msg["content"] = assistant_text
    if tool_calls_chat:
        assistant_msg["tool_calls"] = tool_calls_chat
    if anthropic_thinking_blocks:
        assistant_msg["_anthropic_thinking"] = anthropic_thinking_blocks
    if "content" not in assistant_msg and "tool_calls" not in assistant_msg:
        assistant_msg["content"] = ""

    raw_stop = getattr(response, "stop_reason", None) or ""
    if tool_calls_chat:
        finish_reason = "tool_calls"
    elif raw_stop == "max_tokens":
        finish_reason = "length"
    elif raw_stop in ("end_turn", "stop_sequence"):
        finish_reason = "stop"
    else:
        finish_reason = raw_stop or "stop"

    return assistant_msg, rendered_calls, reasoning_items, finish_reason


def _run_anthropic(state: dict[str, Any]) -> dict[str, Any]:
    """Execute one turn against Anthropic Messages API. Returns the summary dict.

    Mutates state in place: appends the translated assistant message (and any
    tool_use entries, in Chat shape) to state.messages and a turn record to
    state.turns. Thinking content blocks are discarded from state.messages and
    captured on the turn record.

    Deferred-imports `anthropic` so this module imports cleanly when the SDK
    isn't installed and the user is only running OpenAI/Gemini flows.
    """
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic is not installed but state.provider == 'anthropic'. "
            "Install with `uv pip install --python ~/.claude/python-env/bin/python anthropic>=0.104` "
            "and re-run."
        ) from e

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to ~/.claude/settings.json "
            "`env` and restart Claude Code; inside a Cowork sandbox, paste-"
            "export it once per session. Obtain at "
            "https://console.anthropic.com/settings/keys."
        )
    client = Anthropic(api_key=key)

    translated, warnings, omit_tools = _params_for_anthropic(
        state.get("params") or {}, state["model"]
    )
    # Surface translation warnings in the final report.
    if warnings:
        state.setdefault("render_warnings", []).extend(
            f"anthropic: {w}" for w in warnings
        )

    msgs, system = _to_anthropic_messages(state["messages"])
    kwargs: dict[str, Any] = {
        "model": state["model"],
        "messages": msgs,
        **translated,
    }
    if system:
        kwargs["system"] = system
    if not omit_tools:
        tools = state.get("tools_anthropic")
        if tools:
            kwargs["tools"] = tools

    # Let APIError propagate verbatim — main() catches it via the generic
    # Exception handler for the anthropic provider so the request_id and
    # message land on stderr.
    response = client.messages.create(**kwargs)

    assistant_msg, rendered_calls, reasoning_items, finish_reason = (
        _from_anthropic_content(response)
    )

    # Backfill tool registry entries on rendered_calls now that we have state.
    tool_registry = state.get("tool_registry") or {}
    for rc in rendered_calls:
        rc["tool"] = tool_registry.get(rc["name"]) or {}

    state["messages"].append(assistant_msg)

    summary: dict[str, Any] = {
        "stop": not rendered_calls,
        "finish_reason": finish_reason,
        "assistant_text": assistant_msg.get("content", "") if isinstance(assistant_msg.get("content"), str) else "",
    }
    if rendered_calls:
        summary["tool_calls"] = rendered_calls

    usage = _normalize_usage_anthropic(getattr(response, "usage", None))
    turn_record: dict[str, Any] = {
        "finish_reason": finish_reason,
        "assistant_text": summary["assistant_text"],
        "tool_calls": rendered_calls,
        "usage": usage,
        "provider": "anthropic",
        "api": "anthropic-messages",
    }
    if reasoning_items:
        turn_record["reasoning"] = reasoning_items
    state.setdefault("turns", []).append(turn_record)
    return summary


def _normalize_usage_anthropic(usage: Any) -> dict[str, Any] | None:
    """Normalize Anthropic's `usage` block to the shared shape.

    Anthropic's field names use a different vocabulary than OpenAI:
      input_tokens               → input_tokens (matches)
      cache_read_input_tokens    → cached_input_tokens
      cache_creation_input_tokens → (dropped — not counted as cached on read)
      output_tokens              → output_tokens (matches)
      thinking tokens            → reasoning_tokens (NOT exposed in usage today;
                                    left at 0. Verified 2026-05-21 — the
                                    extended-thinking models still don't break
                                    out thinking tokens in usage.)

    `total_tokens` is computed since Anthropic doesn't return one. We sum
    input + output (the Anthropic-billable shape) rather than including cache
    creation/read since those are already covered by input_tokens semantics
    in the Anthropic billing model.
    """
    if usage is None:
        return None
    try:
        d = usage.model_dump(exclude_unset=False)
    except Exception:
        d = dict(usage) if isinstance(usage, dict) else {}
    input_t = int(d.get("input_tokens") or 0)
    output_t = int(d.get("output_tokens") or 0)
    cached = int(d.get("cache_read_input_tokens") or 0)
    return {
        "input_tokens": input_t,
        "cached_input_tokens": cached,
        "output_tokens": output_t,
        "reasoning_tokens": 0,
        "total_tokens": input_t + output_t,
        "provider": "anthropic",
        "api": "anthropic-messages",
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    state_path = Path(args.state_file)
    state = json.loads(state_path.read_text(encoding="utf-8"))

    # Default to openai/chat for back-compat with state files written by an
    # older setup_run.py (pre-ENGPROM-52 → no `api`; pre-ENGPROM-53 → no
    # `provider`).
    provider = state.get("provider") or "openai"
    if provider not in ("openai", "google", "anthropic"):
        print(
            f"error: unknown state.provider {provider!r}; expected 'openai', "
            f"'google', or 'anthropic'",
            file=sys.stderr,
        )
        return 2

    try:
        if provider == "google":
            summary = _run_gemini(state)
        elif provider == "anthropic":
            summary = _run_anthropic(state)
        else:
            try:
                client = _client()
            except RuntimeError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            api = state.get("api") or "chat"
            if api not in ("chat", "responses"):
                print(f"error: unknown state.api {api!r}; expected 'chat' or 'responses'", file=sys.stderr)
                return 2
            run = _run_responses if api == "responses" else _run_chat
            # Flag > env > state (chosen once at setup) > flex.
            try:
                tier = service_tier.wire_value(
                    args.service_tier or state.get("service_tier"),
                    policy=args.on_tier_unavailable,
                )
            except service_tier.ServiceTierError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            try:
                summary = run(client, state, tier)
            except OpenAIError as e:
                reason = service_tier.flex_retry_reason(tier, e)
                if reason is None:
                    raise
                # OpenAI's documented flex remediation: retry on `auto`. Say so —
                # a silent upgrade doubles the bill with nothing in the output.
                print(
                    f"warning: {reason}; retrying on "
                    f"{service_tier.FLEX_FALLBACK_TIER} (standard rate).",
                    file=sys.stderr,
                )
                state["service_tier_degraded"] = reason
                summary = run(client, state, service_tier.FLEX_FALLBACK_TIER)
    except OpenAIError as e:
        print(f"error: OpenAI call failed: {e}", file=sys.stderr)
        return 3
    except RuntimeError as e:
        # _run_gemini / _run_anthropic raise RuntimeError for SDK install /
        # key-missing issues — these are env problems, exit 2 to match the
        # other env-error paths.
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        # Deferred-import isinstance check so genuine programming errors
        # (KeyError from a translation bug, TypeError from a missed param,
        # AttributeError if the response shape changes) re-raise with the
        # original traceback instead of being masked as "API call failed".
        # Only true API errors from each provider's SDK exit with code 3
        # — matches the OpenAIError handling above.
        if provider == "anthropic":
            try:
                from anthropic import APIError as _AnthropicAPIError
            except ImportError:
                _AnthropicAPIError = ()  # type: ignore[assignment,misc]
            if isinstance(e, _AnthropicAPIError):
                print(f"error: Anthropic call failed: {e}", file=sys.stderr)
                return 3
        if provider == "google":
            try:
                from google.genai import errors as _genai_errors
                _GenaiAPIError: Any = _genai_errors.APIError
            except ImportError:
                _GenaiAPIError = ()  # type: ignore[assignment,misc]
            if isinstance(e, _GenaiAPIError):
                print(f"error: Gemini call failed: {e}", file=sys.stderr)
                return 3
        raise

    state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    sys.stdout.write(json.dumps(summary, indent=2, default=str))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
