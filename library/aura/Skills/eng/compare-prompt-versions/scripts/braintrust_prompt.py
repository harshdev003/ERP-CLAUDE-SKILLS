"""Fetch and parse a Braintrust prompt definition into a shape we can run against OpenAI.

Reads `GET /v1/prompt/{id}` and `GET /v1/function/{id}` (for each tool_functions
reference). Returns a `PromptBundle` dataclass with everything `run_qa.py` needs
to render the template, build the OpenAI tool list, and route tool calls.

See PROD-1724 design notes for the field-by-field mapping.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

import requests

BRAINTRUST_API = "https://api.braintrust.dev/v1"

# Project IDs we know about — used to surface helpful context in errors.
# Source: Tools/Braintrust in AI Knowledge.
KNOWN_PROJECT_IDS = {
    "3bee3818-a9b4-40c4-ac14-9e274c52c560": "Conversation",
}

# Prefixes that route to a non-OpenAI provider we support. Google Gemini was
# added in ENGPROM-53; Anthropic in ENGPROM-54. Models that match get the
# corresponding `provider` value on the bundle and route through `_run_gemini`
# or `_run_anthropic` in send_turn.py respectively. Anything not matching
# falls through to `openai` (and the API call will fail loudly if it's wrong).
_GOOGLE_PREFIXES = ("gemini-", "google/")
_ANTHROPIC_PREFIXES = ("claude-", "anthropic/")


def _detect_provider(model: str) -> str:
    """Return the provider for a Braintrust model string.

    Returns one of `"openai" | "google" | "anthropic"`. Default is `openai`.
    """
    m = model.lower()
    if m.startswith(_GOOGLE_PREFIXES):
        return "google"
    if m.startswith(_ANTHROPIC_PREFIXES):
        return "anthropic"
    return "openai"


class BraintrustError(RuntimeError):
    pass


@dataclass
class ToolEntry:
    """One OpenAI-compatible tool definition + its `returns` schema for mocking.

    Two flavors:
    - Inline (from `prompt_data.prompt.tools` JSON string) — no `returns` schema,
      no `function_id`. The auto-mocker will fall back to the generic stub.
    - Referenced (from `prompt_data.tool_functions[]` → `GET /v1/function/{id}`)
      — has both `parameters` and `returns` schemas plus a `function_id` we can
      use for `--real-tools` passthrough.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    returns_schema: dict[str, Any] | None  # None for inline tools
    function_id: str | None  # None for inline tools; required for --real-tools

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_openai_responses_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_gemini_function_declaration(self) -> dict[str, Any]:
        """Return the Gemini-shape function declaration for this tool.

        Gemini wraps tools as `{"function_declarations": [<this>, ...]}` at the
        top level (see `PromptBundle.gemini_tools`). We pass the JSON Schema
        through `parameters_json_schema` rather than `parameters` so the full
        JSON Schema dialect is accepted — Gemini's `parameters` field uses a
        stricter OpenAPI v3 subset that rejects `additionalProperties`,
        array-typed `type`, etc. Verified live 2026-05-21 on `gemini-3.5-flash`.
        """
        decl: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
        }
        # Only set the schema slot when we actually have properties to pass;
        # Gemini accepts the field omitted for zero-arg tools.
        if self.parameters:
            decl["parameters_json_schema"] = self.parameters
        return decl

    def to_anthropic_tool(self) -> dict[str, Any]:
        """Return the Anthropic-shape tool definition for this tool.

        Anthropic's `tools[]` entries are flat: `{name, description,
        input_schema}` — no `type: "function"` and no `function: {...}`
        wrapper. The JSON Schema in `input_schema` is the same shape the
        OpenAI Chat path uses for `function.parameters` so no further
        translation is needed.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters or {"type": "object", "properties": {}},
        }


@dataclass
class PromptBundle:
    prompt_id: str
    slug: str
    name: str
    project_id: str
    project_name: str | None
    model: str
    # Inferred from `model` prefix at fetch time. `"openai"` for Chat/Responses,
    # `"google"` for Gemini (ENGPROM-53), `"anthropic"` for Claude
    # (ENGPROM-54). Used by setup_run.py to default `state.provider` and by
    # send_turn.py to pick which provider branch to run.
    provider: str
    params: dict[str, Any]
    messages: list[dict[str, Any]]  # raw template, with {{...}} placeholders
    tools: list[ToolEntry]
    # Braintrust's transaction id for the version actually fetched. None only
    # if the response didn't include `_xact_id` (older API responses). When
    # `--version` was passed, this echoes that value so the report shows
    # which version was tested.
    version: str | None
    raw_response: dict[str, Any] = field(repr=False)

    @property
    def tools_by_name(self) -> dict[str, ToolEntry]:
        return {t.name: t for t in self.tools}

    def openai_tools(self) -> list[dict[str, Any]]:
        return [t.to_openai_tool() for t in self.tools]

    def openai_responses_tools(self) -> list[dict[str, Any]]:
        return [t.to_openai_responses_tool() for t in self.tools]

    def gemini_tools(self) -> list[dict[str, Any]]:
        """Return the Gemini-shape tools blob for this prompt.

        Gemini wraps all function declarations in a single `Tool` with a
        `function_declarations` list (vs OpenAI's per-tool wrapping). When
        the prompt declares no tools, return an empty list so callers can
        do `if tools: kwargs["tools"] = tools` symmetrically with the
        OpenAI branches.
        """
        if not self.tools:
            return []
        return [{
            "function_declarations": [t.to_gemini_function_declaration() for t in self.tools],
        }]

    def anthropic_tools(self) -> list[dict[str, Any]]:
        """Return the Anthropic-shape `tools[]` blob for this prompt.

        Flat list of `{name, description, input_schema}` entries (one per
        tool). Empty list when the prompt declares no tools so callers can
        do `if tools: kwargs["tools"] = tools` symmetrically with the other
        provider branches.
        """
        return [t.to_anthropic_tool() for t in self.tools]


def parse_prompt_url(url: str) -> str:
    """Extract the prompt UUID from a Braintrust prompt URL.

    Accepts:
      https://www.braintrust.dev/app/<org>/p/<project>/prompts/<uuid>
      https://www.braintrust.dev/app/Aura%20Health%201/p/Conversation/prompts/ceb98a13-...
      <bare UUID>
    """
    s = unquote(url).strip()
    # If it's already a bare UUID, accept it.
    uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
    if uuid_re.match(s):
        return s.lower()

    try:
        parsed = urlparse(s)
    except Exception as e:
        raise BraintrustError(f"Could not parse prompt URL: {url!r}") from e

    if "braintrust.dev" not in (parsed.netloc or ""):
        raise BraintrustError(
            f"Expected a Braintrust prompt URL (https://www.braintrust.dev/app/.../prompts/<uuid>); "
            f"got netloc={parsed.netloc!r}"
        )

    parts = [p for p in parsed.path.split("/") if p]
    # Expected shape: ['app', '<org>', 'p', '<project>', 'prompts', '<uuid>']
    if len(parts) >= 6 and parts[-2] == "prompts":
        candidate = parts[-1]
        if uuid_re.match(candidate):
            return candidate.lower()

    raise BraintrustError(
        f"Could not extract a prompt UUID from URL: {url!r} "
        f"(path parts: {parts})"
    )


def _api_key() -> str:
    key = os.environ.get("BRAINTRUST_API_KEY")
    if not key:
        raise BraintrustError(
            "BRAINTRUST_API_KEY is not set. Add it to ~/.claude/settings.json `env` "
            "and restart Claude Code; inside a Cowork sandbox, paste-export it once per session."
        )
    return key


def _bt_get(path: str) -> dict[str, Any]:
    url = f"{BRAINTRUST_API}{path}"
    r = requests.get(url, headers={"Authorization": f"Bearer {_api_key()}"}, timeout=30)
    if r.status_code != 200:
        raise BraintrustError(f"GET {path} returned {r.status_code}: {r.text[:300]}")
    return r.json()


def fetch_prompt(prompt_id: str, version: str | None = None) -> PromptBundle:
    """Fetch a prompt by UUID and resolve all its tool definitions.

    Args:
        prompt_id: the prompt's UUID (as it appears in the Braintrust URL).
        version: optional Braintrust transaction id (`_xact_id`) pinning a
            specific historical version. Find this in the Braintrust UI on
            the prompt page → version dropdown. When omitted, the latest
            published version is fetched.

    Returns a PromptBundle ready for rendering + execution.
    """
    path = f"/prompt/{prompt_id}"
    if version:
        # Braintrust uses `?version=<xact_id>` to pin a historical version.
        # An invalid version returns HTTP 500 today (not 404) — let that error
        # surface verbatim so the caller can correct the value.
        path = f"{path}?version={version}"
    raw = _bt_get(path)
    prompt_data = raw.get("prompt_data") or {}
    options = prompt_data.get("options") or {}
    prompt_def = prompt_data.get("prompt") or {}

    model = options.get("model")
    if not model:
        raise BraintrustError(
            f"Prompt {prompt_id} has no `prompt_data.options.model`. "
            f"Cannot determine which provider to call."
        )

    # Provider sniff. OpenAI (Chat + Responses), Google Gemini, and Anthropic
    # are all supported. _detect_provider returns "openai" for anything that
    # doesn't match a Google or Anthropic prefix — the API call will fail
    # loudly if the model isn't actually OpenAI.
    provider = _detect_provider(model)

    # `messages` is the template list; we leave the {{...}} placeholders intact
    # and let render_template.py handle substitution.
    if prompt_def.get("type") != "chat":
        raise BraintrustError(
            f"Prompt {prompt_id} has type {prompt_def.get('type')!r}; only 'chat' is supported in v1."
        )
    messages = prompt_def.get("messages") or []

    # Inline tools live as a JSON-encoded string on prompt_def.tools.
    inline_tools: list[ToolEntry] = []
    raw_inline = prompt_def.get("tools")
    if raw_inline:
        try:
            parsed = json.loads(raw_inline) if isinstance(raw_inline, str) else raw_inline
        except json.JSONDecodeError as e:
            raise BraintrustError(f"prompt_data.prompt.tools is not valid JSON: {e}") from e
        for entry in parsed:
            fn = (entry or {}).get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            inline_tools.append(ToolEntry(
                name=name,
                description=fn.get("description", ""),
                parameters=fn.get("parameters") or {"type": "object", "properties": {}},
                returns_schema=None,
                function_id=None,
            ))

    # Referenced tool_functions live as [{id, type}, ...] — we fetch each one
    # to recover its name + parameters + returns schemas.
    referenced_tools: list[ToolEntry] = []
    for ref in prompt_data.get("tool_functions") or []:
        fid = (ref or {}).get("id")
        if not fid:
            continue
        try:
            fn_raw = _bt_get(f"/function/{fid}")
        except BraintrustError as e:
            # Don't fail the whole run for one missing tool — log a placeholder.
            referenced_tools.append(ToolEntry(
                name=f"unresolved_{fid[:8]}",
                description=f"Failed to fetch tool function {fid}: {e}",
                parameters={"type": "object", "properties": {}},
                returns_schema=None,
                function_id=fid,
            ))
            continue
        schema = fn_raw.get("function_schema") or {}
        referenced_tools.append(ToolEntry(
            name=fn_raw.get("name") or fn_raw.get("slug") or f"fn_{fid[:8]}",
            description=fn_raw.get("description") or "",
            parameters=schema.get("parameters") or {"type": "object", "properties": {}},
            returns_schema=schema.get("returns"),
            function_id=fid,
        ))

    # Inline tools win on name conflict (the prompt explicitly chose to include them inline).
    seen_names: set[str] = set()
    merged: list[ToolEntry] = []
    for t in inline_tools + referenced_tools:
        if t.name in seen_names:
            continue
        seen_names.add(t.name)
        merged.append(t)

    project_id = raw.get("project_id") or ""
    return PromptBundle(
        prompt_id=prompt_id,
        slug=raw.get("slug") or "",
        name=raw.get("name") or "",
        project_id=project_id,
        project_name=KNOWN_PROJECT_IDS.get(project_id),
        model=model,
        provider=provider,
        params=options.get("params") or {},
        messages=messages,
        tools=merged,
        version=raw.get("_xact_id"),
        raw_response=raw,
    )


def invoke_real_tool(function_id: str, arguments: dict[str, Any], user_id: str | None) -> Any:
    """Call a Braintrust tool function for real and return its result.

    Used by mock_tools.py when a tool is in `--real-tools`. We pass the model's
    arguments verbatim, optionally injecting `userId` from `--user-id`.
    """
    payload: dict[str, Any] = {"input": dict(arguments)}
    if user_id and "userId" not in payload["input"]:
        payload["input"]["userId"] = user_id
    url = f"{BRAINTRUST_API}/function/{function_id}/invoke"
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    if r.status_code != 200:
        raise BraintrustError(
            f"POST /function/{function_id}/invoke returned {r.status_code}: {r.text[:300]}"
        )
    # Braintrust returns the function output directly (whatever shape the function
    # produces). Could be a primitive, dict, or list.
    try:
        return r.json()
    except json.JSONDecodeError:
        return r.text
