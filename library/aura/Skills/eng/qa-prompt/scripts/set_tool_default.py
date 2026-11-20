"""Persist a tool's default response strategy into state.tool_defaults.

Called once per declared tool at the start of a drive, after Claude has asked
the user "how should `<tool>` respond?". Four kinds:

  - auto-mock    : generate from the tool's returns schema on every call
  - real         : call POST /v1/function/{id}/invoke for every call
  - canned       : return the same JSON for every call (read from stdin)
  - ask-per-call : pause and ask the user on each call (no persistence)

Usage:

    python3 set_tool_default.py --state-file path/to/state.json \\
        --tool-name recommendTracks \\
        --kind auto-mock

    # canned: pipe the response JSON via stdin
    echo '{"tracks": [...]}' | python3 set_tool_default.py --state-file ... \\
        --tool-name recommendTracks --kind canned
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_VALID_KINDS = {"auto-mock", "real", "canned", "ask-per-call"}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Set a tool's default response strategy for this drive.")
    p.add_argument("--state-file", required=True)
    p.add_argument("--tool-name", required=True)
    p.add_argument("--kind", required=True, choices=sorted(_VALID_KINDS))
    return p.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    state_path = Path(args.state_file)
    state = json.loads(state_path.read_text(encoding="utf-8"))

    if args.tool_name not in (state.get("tool_registry") or {}):
        print(
            f"error: tool {args.tool_name!r} is not declared by the prompt's tool registry",
            file=sys.stderr,
        )
        return 2

    entry: dict = {"kind": args.kind}
    if args.kind == "canned":
        raw = sys.stdin.read().strip()
        if not raw:
            print("error: --kind canned requires a JSON response body on stdin", file=sys.stderr)
            return 2
        try:
            entry["canned_response"] = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"error: canned response is not valid JSON: {e}", file=sys.stderr)
            return 2

    if args.kind == "real":
        # Surface the missing-function-id case early so the user knows the choice
        # will degrade at call time.
        tool = state["tool_registry"][args.tool_name]
        if not tool.get("function_id"):
            print(
                f"warn: tool {args.tool_name!r} has no function_id (likely inline); "
                f"real invocation will fail and fall back to error response.",
                file=sys.stderr,
            )

    state.setdefault("tool_defaults", {})[args.tool_name] = entry
    state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    print(f"set default for {args.tool_name}: {args.kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
