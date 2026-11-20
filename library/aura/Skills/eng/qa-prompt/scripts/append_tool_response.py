"""Append a tool response to a run's message history.

Reads the response body as JSON from stdin (any shape — dict, list, string,
number, null). Appends a `{"role": "tool", ...}` message to state.messages
and a record to the last turn's `tool_responses` list. The orchestrator
calls this once per tool_call after collecting the response (auto-mock, real
invoke, user-pasted JSON, or error).

Usage:

    echo '{"tracks": [...]}' | python3 append_tool_response.py \\
        --state-file path/to/state.json \\
        --tool-call-id call_abc \\
        --tool-name recommendTracks \\
        --source auto-mock

`--source` is one of: auto-mock | real | user | error. It's recorded in the
transcript so the final report shows which path was used for each call.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_VALID_SOURCES = {"auto-mock", "real", "user", "error"}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Append a tool response to a run's message history.")
    p.add_argument("--state-file", required=True)
    p.add_argument("--tool-call-id", required=True)
    p.add_argument("--tool-name", required=True)
    p.add_argument("--source", required=True, choices=sorted(_VALID_SOURCES))
    return p.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    raw = sys.stdin.read().strip()
    if not raw:
        print("error: no response body on stdin", file=sys.stderr)
        return 2
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"error: response on stdin is not valid JSON: {e}", file=sys.stderr)
        return 2

    state_path = Path(args.state_file)
    state = json.loads(state_path.read_text(encoding="utf-8"))

    # The tool message OpenAI expects: role=tool, tool_call_id, content (string).
    content = response if isinstance(response, str) else json.dumps(response, default=str)
    tool_msg = {
        "role": "tool",
        "tool_call_id": args.tool_call_id,
        "name": args.tool_name,
        "content": content,
    }
    state["messages"].append(tool_msg)

    # Record the response on the last turn for the final transcript.
    turns = state.setdefault("turns", [])
    if turns:
        turns[-1].setdefault("tool_responses", []).append({
            "tool_call_id": args.tool_call_id,
            "name": args.tool_name,
            "source": args.source,
            "response": response,
        })

    state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    print(f"appended {args.source} response for {args.tool_name} (call {args.tool_call_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
