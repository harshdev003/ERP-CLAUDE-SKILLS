"""Coach-call and de-identification helpers shared by the prompt-improvement flows.

Canonical copy: `data:analyze-conversation-guardrails` (guardrail alerts). Copied
into `data:investigate-user-rating` (conversation ratings); see
`docs/canonical-sources.md`. Both flows turn a production coach call into an
eval-dataset row, so they must agree on what the request is, where the per-turn
context sits, and what de-identification replaces.

Coach request layouts (Responses `input`, oldest first), by Intelligence Service version:

    <= 1.34   system(prompt + "\n\n\n## Context..." incl. time lines) · history · user
    1.35      as above, time moved out · ... · user · developer("### Current Time ...")
    1.36-1.39 system(prompt) · developer("## Context...") · history · user · developer(time)
    1.40+     system(prompt) · developer("---") · user("---") · developer("## Context...")
              · history · user · developer(time)

The `---` pair is a prompt-cache delimiter (IS `utils/buildContextMessages.js`), not
conversation. `coach_parts` reads any of the four; `with_parts` swaps in a new context
and conversation while keeping everything else where production sent it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

import requests

OPENAI_RESPONSES = "https://api.openai.com/v1/responses"

CONTEXT_MARKER = "\n\n\n## Context"
# IS CONTEXT_DELIMITER: the developer + user message pair sent before the context (1.40+).
DELIMITER = "---"
MESSAGE_ROLES = ("system", "developer", "user", "assistant")
# Request fields copied from the stored coach response. Everything else on the
# response object is output or bookkeeping.
REQUEST_KEYS = ("model", "parallel_tool_calls", "reasoning", "temperature", "text", "tool_choice", "top_p",
                "truncation", "tools", "frequency_penalty", "presence_penalty", "max_output_tokens",
                "prompt_cache_retention")
# The subset eng:compare-prompt-versions takes as --params-json.
PARAM_KEYS = ("reasoning", "text", "temperature", "top_p", "parallel_tool_calls", "truncation", "prompt_cache_retention")
NAMES = ["Alex", "Sam", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Avery", "Quinn",
         "Drew", "Rowan", "Sky", "Blake", "Reese"]
PII_PROMPT = (
    "List every piece of personally identifying information in the text: people's names (first or last, "
    "including nicknames), specific places (cities, towns, streets, states, countries only if paired with a city), "
    "employers, schools, emails, phone numbers, usernames, exact birth dates. Do NOT list generic words, "
    "pronouns, relationship words (husband, son), product names, or the assistant's name Aurie. "
    'Return JSON only: {"spans":[{"text":"<exact substring>","type":"person|place|org|contact|date"}]}'
)
PLACEHOLDER = {"place": "the city", "org": "the company", "contact": "[contact]", "date": "[date]"}


def item_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if isinstance(p, dict))


def split_system(system: str, marker: str = CONTEXT_MARKER) -> tuple[str, str]:
    """(prompt body, per-turn context block) of a system message carrying its context (<= 1.35)."""
    i = system.find(marker)
    if i < 0:
        raise ValueError("system message has no context block")
    lead = len(marker) - len(marker.lstrip("\n"))
    return system[:i], system[i + lead:]


def coach_messages(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """(messages in send order as {role, content}, other items left out).

    Tool calls and their outputs from an earlier hop in the same turn are the
    items left out.
    """
    messages = [{"role": i["role"], "content": item_text(i)} for i in items if i.get("role") in MESSAGE_ROLES]
    return messages, sum(1 for i in items if i.get("role") not in MESSAGE_ROLES)


def _delimiters_at(messages: list[dict[str, Any]], i: int) -> bool:
    return (len(messages) > i + 1 and messages[i]["role"] == "developer" and messages[i]["content"] == DELIMITER
            and messages[i + 1]["role"] == "user" and messages[i + 1]["content"] == DELIMITER)


def conversation_of(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The user/assistant messages, without a `---` delimiter pair right after the system message.

    For a call whose context layout doesn't matter (the guardrails handler's
    input). A user typing `---` later in the conversation is kept.
    """
    skip = {1, 2} if _delimiters_at(messages, 1) and messages[0]["role"] in ("system", "developer") else set()
    return [dict(m) for j, m in enumerate(messages) if j not in skip and m["role"] in ("user", "assistant")]


def coach_parts(messages: list[dict[str, Any]], marker: str = CONTEXT_MARKER,
                fired: bool = True) -> dict[str, Any]:
    """Body, context, developer instructions and conversation of a coach request, any layout.

    `messages` is the request's `input` in send order (`coach_messages`). Returns
    `body` (the static prompt), `context` (the `## Context` block), `developer`
    (the developer messages after the message that fired — the `### Current Time`
    lines from 1.35 on — joined, or ""), `conversation` (user/assistant messages
    without the `---` delimiters), `layout` and the indexes `with_parts` needs.
    `fired=False` drops the check that the request ends with a user message.
    """
    if not messages or messages[0]["role"] not in ("system", "developer"):
        raise ValueError("coach call has no system message")
    system = messages[0]["content"]
    skip: set[int] = set()
    if marker in system:
        body, context = split_system(system, marker)
        layout, context_index = "system-context", 0
    else:
        body = system
        start = 1
        if _delimiters_at(messages, 1):
            skip, start = {1, 2}, 3
        heading = marker.lstrip("\n")
        context_index = next((j for j in range(start, len(messages)) if messages[j]["role"] == "developer"
                              and messages[j]["content"].startswith(heading)), None)
        if context_index is None:
            raise ValueError("coach call has no context block")
        context = messages[context_index]["content"]
        layout = "delimited-developer-context" if skip else "developer-context"
    skip.add(context_index)
    conv_idx = [j for j in range(1, len(messages)) if j not in skip and messages[j]["role"] in ("user", "assistant")]
    if fired and (not conv_idx or messages[conv_idx[-1]]["role"] != "user"):
        raise ValueError("coach call input does not end with the user message that fired")
    after = conv_idx[-1] + 1 if conv_idx else context_index + 1
    developer = [m["content"] for m in messages[after:] if m["role"] == "developer"]
    return {"layout": layout, "body": body, "context": context, "developer": "\n\n".join(developer),
            "conversation": [dict(messages[j]) for j in conv_idx],
            "context_index": context_index, "conversation_index": conv_idx, "after_conversation": after}


def with_parts(messages: list[dict[str, Any]], marker: str = CONTEXT_MARKER, context: str | None = None,
               conversation: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """`messages` with the context and/or conversation replaced, everything else kept in place.

    The replacement conversation must have as many messages as the original: a
    de-identified conversation is the same messages with their text rewritten.
    """
    parts = coach_parts(messages, marker)
    out = [dict(m) for m in messages]
    if context is not None:
        i = parts["context_index"]
        if parts["layout"] == "system-context":
            lead = len(marker) - len(marker.lstrip("\n"))
            out[i]["content"] = parts["body"] + "\n" * lead + context
        else:
            out[i]["content"] = context
    if conversation is not None:
        if len(conversation) != len(parts["conversation_index"]):
            raise ValueError("replacement conversation has a different number of messages")
        for j, m in zip(parts["conversation_index"], conversation):
            out[j] = {"role": m["role"], "content": m["content"]}
    return out


def exact_request(coach_resp: dict[str, Any], coach_items: list[dict[str, Any]],
                  marker: str = CONTEXT_MARKER) -> tuple[dict[str, Any], int]:
    """(request as sent, other items left out). Items are oldest-first.

    `input` keeps every system, developer, user and assistant message in
    production's order — the `---` delimiters and the trailing time message
    included — so a replay is the call production made. Read its parts with
    `coach_parts`. Tool calls and their outputs from an earlier hop in the same
    turn are left out and counted. Raises ValueError when the call has no
    context block or does not end with the user message that fired.
    """
    messages, others = coach_messages(coach_items)
    coach_parts(messages, marker)
    req = {k: coach_resp[k] for k in REQUEST_KEYS if coach_resp.get(k) is not None}
    req["input"] = messages
    return req, others


def production_config(coach_resp: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(tools, params) for eng:compare-prompt-versions --tools-json / --params-json."""
    return coach_resp.get("tools") or [], {k: coach_resp[k] for k in PARAM_KEYS if coach_resp.get(k) is not None}


def build_mapping(spans: list[dict[str, Any]]) -> dict[str, str]:
    """Span text → replacement. Longest first, so a full name wins over its first name."""
    mapping: dict[str, str] = {}
    people = 0
    for text, kind in sorted({(s.get("text", "").strip(), s.get("type")) for s in spans}, key=lambda x: -len(x[0])):
        if len(text) < 2 or text in mapping:
            continue
        if kind == "person":
            mapping[text] = NAMES[people % len(NAMES)]
            people += 1
        else:
            mapping[text] = PLACEHOLDER.get(kind, "[redacted]")
    return mapping


def apply_mapping(text: str, mapping: dict[str, str]) -> str:
    for old in sorted(mapping, key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(old) + r"(?!\w)", mapping[old], text)
    return text


def residue(text: str, account: list[Any], spans: list[str]) -> int:
    """How many identifiers still appear. Counts only, never the values.

    The account's own identifiers are checked word by word, case-insensitively: a
    first name alone is still identifying. The conversation cache holds only the
    given name and the email, so the email's local part is split on separators
    and digits too: `dana.reyes88@` yields `reyes`, the closest thing to a last
    name the cache can offer. Replaced spans are checked whole and
    case-sensitively, because their parts ("New" of "New York") are ordinary words.
    """
    # An email's domain ("gmail") is not identifying and would block ordinary rows.
    # The whole local part counts too: `_` is a word character, so `reyes` alone
    # would never match inside a leftover `dana_reyes88`.
    words = lambda k: [k.split("@")[0], *re.split(r"[\s._+\-\d]+", k.split("@")[0])]  # noqa: E731
    parts = {p for k in account if isinstance(k, str) for p in [k, *words(k)] if len(p) > 2}
    hits = sum(1 for p in parts if re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", text, re.I))
    return hits + sum(1 for sp in set(spans) if re.search(r"(?<!\w)" + re.escape(sp) + r"(?!\w)", text))


def deidentify(post: Callable[[dict[str, Any]], dict[str, Any]], context: str,
               conversation: list[dict[str, Any]], account: list[Any], model: str = "gpt-5.4") -> dict[str, Any]:
    """Replace identifying details in a context block and its messages; count what survives.

    `post` sends one Responses request (the caller owns the key). `account` is the
    user's own identifiers (given name, email), checked by `residue`.
    """
    blob = context + "\n\n" + "\n".join(m["content"] for m in conversation)
    o = post({"model": model, "service_tier": "flex", "reasoning": {"effort": "low"}, "store": False,
              "text": {"format": {"type": "json_object"}},
              "input": [{"role": "system", "content": PII_PROMPT}, {"role": "user", "content": blob}]})
    reply = "".join(c.get("text", "") for x in o["output"] if x["type"] == "message" for c in x["content"])
    mapping = build_mapping(json.loads(reply).get("spans") or [])
    new_context = apply_mapping(context, mapping)
    messages = [{"role": m["role"], "content": apply_mapping(m["content"], mapping)} for m in conversation]
    left = residue(new_context + " ".join(m["content"] for m in messages), account, list(mapping))
    return {"context": new_context, "input": messages, "replaced": len(mapping), "residue": left}


def flex_fallback(status: int, text: str) -> bool:
    """Whether a flex request should be retried on `auto`.

    Only the two flex-specific refusals: no flex capacity (429
    `resource_unavailable`) and a model that doesn't offer flex (400 on
    `service_tier`). An ordinary rate-limit or quota 429 keeps failing.
    """
    return (status == 429 and "resource_unavailable" in text) or (status == 400 and "service_tier" in text)


def post_responses(key: str, body: dict[str, Any], timeout: int = 300) -> dict[str, Any]:
    """One Responses call; a flex request falls back to `auto` on a flex-specific refusal."""
    headers = {"Authorization": f"Bearer {key}"}
    r = requests.post(OPENAI_RESPONSES, json=body, timeout=timeout, headers=headers)
    if body.get("service_tier") == "flex" and flex_fallback(r.status_code, r.text):
        r = requests.post(OPENAI_RESPONSES, json={**body, "service_tier": "auto"}, timeout=timeout, headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:300]}")
    return r.json()
