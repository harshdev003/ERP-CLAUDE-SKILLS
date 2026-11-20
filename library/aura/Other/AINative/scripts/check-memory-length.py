#!/usr/bin/env python3
"""Fail a PR that pushes CLAUDE.md / AGENTS.md past the harness's context limit.

An agent memory file is loaded in full at the start of every session. Past the
limit the harness stops loading it — and says so once, in the session that
crosses. Nothing in the repo fails, no output changes shape, and every later
session simply runs without the project memory everyone assumes is in context.
That is the failure this gate exists to make loud: the file does not break, it
quietly stops being memory.

**Characters, not bytes or lines.** The harness counts characters, so this script
does too. The difference is not cosmetic at this size: CLAUDE.md at 154,044
characters was 155,113 bytes, and this file's prose is arrow- and em-dash-heavy
enough that a byte count would fire about a thousand characters early.

**Three bands, and a ratchet in the middle one.**

    < WARN_AT          fine — no rule
    WARN_AT .. LIMIT   warn, and the file may not grow
    >= LIMIT           fail

The middle band is the part that holds the line. A bare "must be under LIMIT"
gate gives no runway: it passes at 149,999 and fails on the next paragraph, by
which point the fix is an extraction under time pressure. A warning alone would
be ignored — that is the documented lesson of this repo's own SKILL.md budget,
which drifted for months precisely because no single PR's few added lines look
unreasonable. So inside the warn band the file may shrink or hold but not grow,
which needs no allowlist of today's offenders and no scheduled cleanup: the
allowance shrinks on its own.

When this fails, the fix is the one already used on CLAUDE.md: move lookup
tables (registries, inventories, schemas) to `docs/` and leave a pointer plus
whatever rule a reader needs *before* they know to open the file. What stays is
the procedure and the rules; what moves is what you consult for one task.

    python3 scripts/check-memory-length.py [--base origin/main]

Exit: 0 clean (warnings still exit 0) · 1 a file is over or grew inside the warn
band · 2 the check could not run.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

# The harness's limit, in characters. This is an external fact about the tool,
# not a repo convention, so there is no in-repo canonical source to point at —
# it is stated here and referenced from CLAUDE.md rather than restated there.
# Observed 2026-09-21: a 154,044-character CLAUDE.md reported as "over the
# 150.0k-char limit (154.0k chars)".
LIMIT = 150_000

# Where the ratchet starts. 10% of runway is enough to land an extraction as
# ordinary work rather than as an emergency.
WARN_AT = 135_000

# Both agent-memory filenames, at the repo root and nested. Kept as names rather
# than paths so a memory file added in a subdirectory is covered the day it
# lands, without editing this list.
MEMORY_NAMES = ("CLAUDE.md", "AGENTS.md")

ABSENT = object()  # file not present at that ref


def _git(*args: str):
    """(ok, stdout)."""
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"error: git {' '.join(args)} failed: {exc}", file=sys.stderr)
        return False, ""
    return out.returncode == 0, out.stdout


def _die(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _count(text: str) -> int:
    """Characters, matching what the harness reports."""
    return len(text)


def length_at(ref: str, path: str):
    ok, out = _git("show", f"{ref}:{path}")
    return _count(out) if ok else ABSENT


def length_now(path: str):
    try:
        return _count(pathlib.Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ABSENT
    except OSError as exc:
        _die(f"{path} could not be read ({exc})")


def verdict(before, after):
    """(failed, warned, reason) for one file.

    `after` is never ABSENT here — deleted files are filtered out before this
    point. `before` is ABSENT for a file added in this PR.
    """
    if after >= LIMIT:
        return True, False, (
            f"{after:,} chars is over the {LIMIT:,}-char limit — the harness will "
            f"stop loading it"
        )
    if after < WARN_AT:
        return False, False, ""
    # In the warn band. The ratchet binds only on a file that was ALREADY in the
    # band: the PR that first crosses in should hear about it, not be blocked —
    # otherwise nothing may ever legitimately enter the band, and the warning
    # has no PR to appear on. A new file has no `before` to ratchet against.
    if before is ABSENT or before < WARN_AT or after <= before:
        return False, True, (
            f"{after:,} chars — within {LIMIT - after:,} of the {LIMIT:,}-char "
            f"limit; it may hold or shrink from here, but not grow"
        )
    return True, False, (
        f"grew from {before:,} to {after:,} chars while inside the warn band "
        f"({WARN_AT:,}+); an agent-memory file this close to the {LIMIT:,}-char "
        f"limit may only shrink"
    )


def check(base: str) -> int:
    ok, out = _git("diff", "--name-only", f"{base}...HEAD")
    if not ok:
        _die(f"could not diff against {base!r}. Fetch it first "
             f"(`git fetch origin main`) or pass --base.")

    paths = [
        ln.strip() for ln in out.splitlines()
        if pathlib.PurePosixPath(ln.strip()).name in MEMORY_NAMES
    ]
    if not paths:
        print("No agent-memory file touched — nothing to check.")
        return 0

    failures, warnings, rows = [], [], []
    for path in sorted(set(paths)):
        after = length_now(path)
        if after is ABSENT:
            continue  # deleted in this PR
        before = length_at(base, path)
        failed, warned, reason = verdict(before, after)
        shown = "(new)" if before is ABSENT else f"{before:,}"
        mark = "FAIL" if failed else ("warn" if warned else "ok  ")
        pct = 100 * after / LIMIT
        rows.append(f"  {mark}  {path}  {shown} -> {after:,} chars ({pct:.0f}% of limit)")
        if failed:
            failures.append((path, reason))
        elif warned:
            warnings.append((path, reason))

    print(f"Agent-memory limit: {LIMIT:,} chars · warn band from {WARN_AT:,}")
    print("\n".join(rows))

    for path, reason in warnings:
        print(f"\nwarning: {path}\n    {reason}", file=sys.stderr)

    if not failures:
        print("\nEvery touched memory file is within the limit.")
        return 0

    print("\nFAIL — agent-memory file over the limit:\n", file=sys.stderr)
    for path, reason in failures:
        print(f"  {path}\n      {reason}", file=sys.stderr)
    print(
        "\n  Move a lookup table — a registry, an inventory, a schema, an ID table —\n"
        "  into `docs/<topic>.md` and leave a pointer plus whatever rule a reader\n"
        "  needs BEFORE they know to open the file. The procedure and the rules stay;\n"
        "  what you consult for one specific task is what moves.\n"
        "  Repoint relative links one level up, and re-word any cross-reference that\n"
        "  says \"above\" or \"below\" about the text you moved.\n"
        "  Worked example: docs/canonical-sources.md, docs/token-inventory.md.\n",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main", help="base ref (default origin/main)")
    return check(ap.parse_args().base)


if __name__ == "__main__":
    raise SystemExit(main())
