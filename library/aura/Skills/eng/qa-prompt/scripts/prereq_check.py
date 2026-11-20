"""Prerequisite check for qa-prompt runs.

Verifies that the env vars qa-prompt's downstream scripts will read are
actually present BEFORE the orchestrator (Claude) burns time on Phase 1
setup and Phase 2 tool-default decisions. Without this, the first failure
surfaces in Phase 3 at the first `send_turn.py` call — by which point the
user has already spent N seconds clicking through tool-default questions
and (in a sweep) the parent has already spawned subagents that will all
fail identically.

Usage:

    python3 prereq_check.py            # check both keys, exit 2 if either missing
    python3 prereq_check.py --json     # machine-readable status (for SKILL.md gates)

Exit codes:

    0 — both keys present
    2 — at least one key missing (stderr prints what + how to fix)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REMEDIATION = (
    "Add the missing key(s) under the `env` block in ~/.claude/settings.json "
    "(use the `update-config` skill or `/update-config`), then restart Claude "
    "Code so the new env values load.\n\n"
    "Inside a Cowork sandbox, `mcp__workspace__bash` strips host env "
    "(PROD-1305) — paste-export each key once per session instead."
)

REQUIRED = [
    (
        "BRAINTRUST_API_KEY",
        "fetch the prompt definition, fetch tool function schemas, and invoke "
        "real Braintrust tools. Obtain at "
        "https://www.braintrust.dev/app/aurahealth/settings/api-keys",
    ),
    (
        "OPENAI_EVALUATIONS_API_KEY",
        "drive the prompt against OpenAI and run the self-critique scorers. "
        "Obtain at https://platform.openai.com/api-keys under the Aura "
        "OpenAI org.",
    ),
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Verify qa-prompt env prerequisites are present.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON on stdout instead of human-readable lines.",
    )
    return p.parse_args()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()

    status = []
    missing = []
    for name, purpose in REQUIRED:
        present = bool(os.environ.get(name))
        status.append({"name": name, "present": present, "purpose": purpose})
        if not present:
            missing.append((name, purpose))

    if args.json:
        print(json.dumps({"ok": not missing, "keys": status, "missing": [m[0] for m in missing]}, indent=2))
    else:
        for entry in status:
            mark = "OK" if entry["present"] else "MISSING"
            print(f"{mark}\t{entry['name']}")

    if missing:
        print("", file=sys.stderr)
        print("qa-prompt cannot run — required env var(s) missing:", file=sys.stderr)
        for name, purpose in missing:
            print(f"  - {name}: {purpose}", file=sys.stderr)
        print("", file=sys.stderr)
        print(REMEDIATION, file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
