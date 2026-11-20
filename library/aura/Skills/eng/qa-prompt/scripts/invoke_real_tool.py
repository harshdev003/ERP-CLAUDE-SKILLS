"""Invoke a Braintrust tool function for real and emit its response JSON.

Used when the orchestrator picks "invoke real tool" in response to a tool
call from the model. Reads the tool's `function_id` from the run state and
posts the model's arguments verbatim (with `userId` from state.user_id
injected when the tool needs it) to the Braintrust /function/{id}/invoke
endpoint.

Usage:

    python3 invoke_real_tool.py --state-file path/to/state.json \\
        --tool-name <name> --arguments '<json-string>'

Prints the raw response from Braintrust on stdout. On failure, prints
{"ok": false, "error": "..."} so the run can continue.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make sibling modules importable when invoked as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from braintrust_prompt import BraintrustError, invoke_real_tool

_USER_ID_REQUIRED_TOOLS = {"recommendTracks"}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Invoke a Braintrust tool function for real.")
    p.add_argument("--state-file", required=True)
    p.add_argument("--tool-name", required=True)
    p.add_argument("--arguments", required=True, help="JSON string of arguments from the model.")
    return p.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    with open(args.state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    tool = (state.get("tool_registry") or {}).get(args.tool_name)
    if not tool:
        print(f"error: tool {args.tool_name!r} not in registry", file=sys.stderr)
        sys.stdout.write(json.dumps({"ok": False, "error": "tool not in registry"}) + "\n")
        return 0
    function_id = tool.get("function_id")
    if not function_id:
        print(
            f"error: tool {args.tool_name!r} has no function_id (likely inline); cannot invoke for real",
            file=sys.stderr,
        )
        sys.stdout.write(json.dumps({"ok": False, "error": "no function_id"}) + "\n")
        return 0

    user_id = state.get("user_id")
    if args.tool_name in _USER_ID_REQUIRED_TOOLS and not user_id:
        print(
            f"error: tool {args.tool_name!r} requires user_id; pass --user-id to setup_run.py",
            file=sys.stderr,
        )
        sys.stdout.write(json.dumps({"ok": False, "error": "missing user_id"}) + "\n")
        return 0

    try:
        arguments = json.loads(args.arguments)
    except json.JSONDecodeError as e:
        print(f"error: --arguments is not valid JSON: {e}", file=sys.stderr)
        sys.stdout.write(json.dumps({"ok": False, "error": "invalid arguments JSON"}) + "\n")
        return 0

    try:
        result = invoke_real_tool(function_id, arguments, user_id)
    except BraintrustError as e:
        print(f"error: Braintrust invoke failed: {e}", file=sys.stderr)
        sys.stdout.write(json.dumps({"ok": False, "error": f"invoke failed: {e}"}) + "\n")
        return 0

    sys.stdout.write(json.dumps(result, indent=2, default=str))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
