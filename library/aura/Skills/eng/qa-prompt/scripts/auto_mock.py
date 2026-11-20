"""Generate an auto-mock response for one tool call from the prompt's tool schema.

Used by the orchestrator when the user picks "auto-mock from schema" in
response to a tool call. Reads the tool's `returns_schema` from the run state,
walks it, and emits a plausible JSON value.

Usage:

    python3 auto_mock.py --state-file path/to/state.json --tool-name <name>

Prints the mock JSON on stdout. If the tool has no `returns_schema` (likely an
inline tool), falls back to {"ok": true, "mocked": true} and emits a warning
on stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make sibling modules importable when invoked as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_tools import SchemaGenerationError, generate_from_schema


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate an auto-mock response from a tool's returns schema.")
    p.add_argument("--state-file", required=True)
    p.add_argument("--tool-name", required=True)
    return p.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    with open(args.state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    tool = (state.get("tool_registry") or {}).get(args.tool_name)
    if not tool:
        print(
            f"warn: tool {args.tool_name!r} not found in registry; emitting generic stub",
            file=sys.stderr,
        )
        sys.stdout.write(json.dumps({"ok": True, "mocked": True}) + "\n")
        return 0

    schema = tool.get("returns_schema")
    if not schema:
        print(
            f"warn: tool {args.tool_name!r} has no returns schema (likely inline tool); "
            f"emitting generic stub",
            file=sys.stderr,
        )
        sys.stdout.write(json.dumps({"ok": True, "mocked": True}) + "\n")
        return 0

    try:
        generated = generate_from_schema(schema)
    except SchemaGenerationError as e:
        print(f"warn: auto-mock generation failed: {e}; emitting generic stub", file=sys.stderr)
        sys.stdout.write(json.dumps({"ok": True, "mocked": True}) + "\n")
        return 0

    sys.stdout.write(json.dumps(generated, indent=2, default=str))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
