"""Append a user message to state.messages.

Used in interactive mode after Claude has composed the next simulated user
turn based on the scenario and the conversation so far. Reads the message
text from stdin to avoid argv length and shell-escaping issues.

Usage:

    echo 'I just woke up and feel anxious' | python3 append_user_message.py \\
        --state-file path/to/state.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Append a (simulated) user message to a run's message history.")
    p.add_argument("--state-file", required=True)
    return p.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    raw = sys.stdin.read()
    if not raw.strip():
        print("error: no user message text on stdin", file=sys.stderr)
        return 2

    state_path = Path(args.state_file)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["messages"].append({"role": "user", "content": raw.rstrip("\n")})
    # Mark this as a user-turn boundary in the turns log so the report can
    # render the simulated user's contributions distinctly from tool replies.
    state.setdefault("turns", []).append({
        "kind": "user_turn",
        "user_text": raw.rstrip("\n"),
    })
    state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    print(f"appended user message ({len(raw)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
