"""Apply a tool call's pre-set default response and append it to state.messages.

Looks up the tool in state.tool_defaults and dispatches:

  - auto-mock    : generate_from_schema(returns_schema) → append
  - real         : POST /v1/function/{function_id}/invoke → append
  - canned       : use the stored canned_response → append
  - ask-per-call : exit with a non-zero status so the orchestrator knows to
                   pause and ask the user (also when no default is set yet)

The orchestrator passes the model's tool call (id + name + arguments) so this
script can wire the response back to the right tool_call_id.

Usage:

    python3 apply_tool_default.py --state-file path/to/state.json \\
        --tool-call-id call_abc --tool-name recommendTracks \\
        --arguments '<json>'

Exit codes:
  0   default applied successfully (response appended)
  10  ask-per-call (or no default set) — orchestrator should prompt the user
  2   error (e.g. tool not declared) — see stderr
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make sibling modules importable when invoked as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from braintrust_prompt import BraintrustError, invoke_real_tool
from mock_tools import SchemaGenerationError, generate_from_schema

_USER_ID_REQUIRED_TOOLS = {"recommendTracks"}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Apply a tool's pre-set default response.")
    p.add_argument("--state-file", required=True)
    p.add_argument("--tool-call-id", required=True)
    p.add_argument("--tool-name", required=True)
    p.add_argument("--arguments", required=True, help="JSON string of arguments from the model.")
    return p.parse_args()


def _append(state: dict, *, tool_call_id: str, tool_name: str, response, source: str) -> None:
    content = response if isinstance(response, str) else json.dumps(response, default=str)
    state["messages"].append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": tool_name,
        "content": content,
    })
    turns = state.setdefault("turns", [])
    if turns:
        turns[-1].setdefault("tool_responses", []).append({
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "source": source,
            "response": response,
        })


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    state_path = Path(args.state_file)
    state = json.loads(state_path.read_text(encoding="utf-8"))

    registry = state.get("tool_registry") or {}
    tool = registry.get(args.tool_name)
    if not tool:
        print(f"error: tool {args.tool_name!r} not in registry", file=sys.stderr)
        return 2

    default = (state.get("tool_defaults") or {}).get(args.tool_name)
    if not default or default.get("kind") == "ask-per-call":
        # Orchestrator handles this case by prompting the user directly.
        print("ask-per-call", file=sys.stderr)
        return 10

    kind = default.get("kind")

    if kind == "canned":
        response = default.get("canned_response")
        if response is None:
            print(
                f"error: tool {args.tool_name!r} default is canned but no canned_response stored",
                file=sys.stderr,
            )
            return 2
        _append(state, tool_call_id=args.tool_call_id, tool_name=args.tool_name, response=response, source="canned")
        state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        print(f"applied canned response for {args.tool_name}")
        return 0

    if kind == "auto-mock":
        schema = tool.get("returns_schema")
        if not schema:
            response = {"ok": True, "mocked": True}
        else:
            try:
                response = generate_from_schema(schema)
            except SchemaGenerationError as e:
                print(f"warn: auto-mock failed ({e}); using generic stub", file=sys.stderr)
                response = {"ok": True, "mocked": True}
        _append(state, tool_call_id=args.tool_call_id, tool_name=args.tool_name, response=response, source="auto-mock")
        state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        print(f"applied auto-mock for {args.tool_name}")
        return 0

    if kind == "real":
        function_id = tool.get("function_id")
        if not function_id:
            error_resp = {"ok": False, "error": "no function_id (likely inline tool)"}
            _append(state, tool_call_id=args.tool_call_id, tool_name=args.tool_name, response=error_resp, source="error")
            state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            print(f"warn: tool {args.tool_name!r} has no function_id; appended error response", file=sys.stderr)
            return 0

        user_id = state.get("user_id")
        if args.tool_name in _USER_ID_REQUIRED_TOOLS and not user_id:
            error_resp = {"ok": False, "error": "missing user_id"}
            _append(state, tool_call_id=args.tool_call_id, tool_name=args.tool_name, response=error_resp, source="error")
            state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            print(f"warn: tool {args.tool_name!r} needs user_id; appended error response", file=sys.stderr)
            return 0

        try:
            arguments = json.loads(args.arguments)
        except json.JSONDecodeError as e:
            error_resp = {"ok": False, "error": f"invalid arguments JSON: {e}"}
            _append(state, tool_call_id=args.tool_call_id, tool_name=args.tool_name, response=error_resp, source="error")
            state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            return 0

        try:
            response = invoke_real_tool(function_id, arguments, user_id)
            _append(state, tool_call_id=args.tool_call_id, tool_name=args.tool_name, response=response, source="real")
            state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            print(f"invoked real {args.tool_name}")
            return 0
        except BraintrustError as e:
            error_resp = {"ok": False, "error": f"invoke failed: {e}"}
            _append(state, tool_call_id=args.tool_call_id, tool_name=args.tool_name, response=error_resp, source="error")
            state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            print(f"warn: real invoke of {args.tool_name} failed: {e}; appended error", file=sys.stderr)
            return 0

    print(f"error: unrecognized tool_default kind {kind!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
