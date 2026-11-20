#!/usr/bin/env python3
"""The held-account keep list: uids no job may delete, rewrite or field-clean.

A JSON array of uid strings at ~/.claude/held-accounts/held-uids.json (HELD_UIDS_JSON overrides),
mode 600, written by the hold owner and kept outside the repo: no uid is ever written in a skill.

load() refuses a list that is missing, empty, unreadable, not a JSON array of uid strings, or
readable by group or others: a job that can't read the list does nothing. load(required=False)
returns an empty set for a missing file, for the one skill whose job is the held account itself.
Output names a uid by its first 6 characters only (after any `provider:` prefix; less for a short id).

  held_uids.py check UID    exit 0 not listed, 3 listed (prints the skip line), 2 refused

Canonical copy: eng:cleanup-orphaned-auth. Other skills ship a byte-identical copy (docs/canonical-sources.md).
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

DEFAULT = "~/.claude/held-accounts/held-uids.json"
REFUSED_EXIT = 2
HELD_EXIT = 3
PREFIX = 6


class Refused(Exception):
    pass


def path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("HELD_UIDS_JSON") or DEFAULT).expanduser()


def load(required: bool = True, p: pathlib.Path | None = None) -> frozenset:
    """The held uids. Refused (never read as empty) when the list can't be trusted."""
    p = pathlib.Path(p) if p else path()
    try:
        mode = p.stat().st_mode
    except FileNotFoundError:
        if required:
            raise Refused(f"no keep list at {p}: nothing is deleted or written without it")
        return frozenset()
    if mode & 0o077:
        raise Refused(f"keep list {p} is readable by group or others; chmod 600 it")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"keep list {p} is unreadable or not JSON")
    if not isinstance(data, list) or any(not isinstance(u, str) or not u.strip() for u in data):
        raise Refused(f"keep list {p} must be a JSON array of non-empty uid strings")
    if required and not data:
        raise Refused(f"keep list {p} is empty: nothing is deleted or written with an empty list")
    return frozenset(u.strip() for u in data)


def label(uid: str) -> str:
    """A uid as printed: any `provider:` prefix, then 6 characters, and never a short id in full."""
    head, sep, rest = str(uid).strip().rpartition(":")
    return f"{head}{sep}{rest[:min(PREFIX, len(rest) // 2)]}…"


def skip_line(uid: str) -> str:
    return f"skip {label(uid)}: keep list"


def split(uids, held) -> tuple[list, list]:
    """(the uids a job may act on, the listed ones it must skip), order kept."""
    todo, kept = [], []
    for u in uids:
        (kept if str(u).strip() in held else todo).append(u)
    return todo, kept


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "check":
        print(__doc__, file=sys.stderr)
        return REFUSED_EXIT
    try:
        held = load()
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return REFUSED_EXIT
    uid = argv[1].strip()
    if uid in held:
        print(skip_line(uid))
        return HELD_EXIT
    print(f"keep list: {len(held)} uid(s); {label(uid)} not listed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
