#!/usr/bin/env python3
"""Fail a PR that pushes a SKILL.md over the line budget, or grows one already over.

The budget itself is stated once, in the authoring best-practices — this script
reads it from there rather than carrying a second copy (see `_read_budget`).

**Ratchet, not threshold.** The check compares each changed SKILL.md against its
own length at the base ref, not against the budget alone:

  * under budget at base  -> may not cross it
  * over budget at base   -> may not grow; shrinking or holding is fine
  * new file              -> must be under budget

That shape is deliberate. A plain "must be under N" gate would block every PR
touching a file that is already over, which in practice means the gate gets
disabled. An allowlist of today's offenders would rot — a file could sit at 594
forever and the list would quietly bless it. Comparing to base needs no list: the
allowance shrinks on its own as files are trimmed, and reaches zero when the last
one crosses under.

Why a gate at all: the budget has been documented since the authoring guide
existed and drifted anyway, because no single PR's few added lines look
unreasonable. Nobody is holding the line, so the line has to hold itself.

    python3 scripts/check-skill-length.py [--base origin/main]

Exit: 0 clean (or no SKILL.md touched) · 1 a file regressed · 2 the check could
not run.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

# The budget lives in the authoring best-practices; this is where to find it, not
# what it is. Keeping the number in one place is the same "point, don't restate"
# rule the repo applies to every other shared fact.
BUDGET_SOURCE = "plugins/shared/skills/create-aura-skill/references/best-practices.md"
BUDGET_PATTERN = re.compile(r"Keep\s+`SKILL\.md`\s+under\s+~?(\d+)\s+lines", re.I)
BUDGET_FALLBACK = 500

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


def _read_budget() -> int:
    """Parse the budget out of the best-practices file.

    Falls back rather than dying: a missing or reworded source should not take
    the gate offline, it should just run on the last known number. A silently
    disabled gate is the failure this whole script exists to prevent.
    """
    try:
        text = pathlib.Path(BUDGET_SOURCE).read_text(encoding="utf-8")
    except OSError:
        print(f"warning: {BUDGET_SOURCE} unreadable — using {BUDGET_FALLBACK}",
              file=sys.stderr)
        return BUDGET_FALLBACK
    m = BUDGET_PATTERN.search(text)
    if not m:
        print(f"warning: no budget statement found in {BUDGET_SOURCE} — "
              f"using {BUDGET_FALLBACK}", file=sys.stderr)
        return BUDGET_FALLBACK
    return int(m.group(1))


def _count(text: str) -> int:
    """Lines, matching `wc -l` — i.e. trailing newline does not add one."""
    return text.count("\n") if text.endswith("\n") else len(text.splitlines())


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


def verdict(before, after, budget: int):
    """(failed, reason) for one file. `after` is never ABSENT here (deleted files
    are filtered out before this point)."""
    if before is ABSENT:
        if after > budget:
            return True, f"new file is {after} lines, over the {budget} budget"
        return False, ""
    if before > budget:
        # Already over — the only rule is that it must not get worse.
        if after > before:
            return True, (f"already over budget at {before} lines and grew to {after}; "
                          f"an over-budget file may only shrink")
        return False, ""
    if after > budget:
        return True, f"crossed the {budget}-line budget: {before} -> {after}"
    return False, ""


def check(base: str) -> int:
    budget = _read_budget()
    ok, out = _git("diff", "--name-only", f"{base}...HEAD")
    if not ok:
        _die(f"could not diff against {base!r}. Fetch it first "
             f"(`git fetch origin main`) or pass --base.")

    paths = [
        ln.strip() for ln in out.splitlines()
        if ln.strip().startswith("plugins/") and ln.strip().endswith("/SKILL.md")
    ]
    if not paths:
        print("No SKILL.md touched — nothing to check.")
        return 0

    failures, rows = [], []
    for path in sorted(set(paths)):
        after = length_now(path)
        if after is ABSENT:
            continue  # deleted in this PR
        before = length_at(base, path)
        failed, reason = verdict(before, after, budget)
        shown = "(new)" if before is ABSENT else str(before)
        rows.append(f"  {'FAIL' if failed else 'ok  '}  {path}  {shown} -> {after}")
        if failed:
            failures.append((path, reason))

    print(f"SKILL.md line budget: ~{budget} (from {BUDGET_SOURCE})")
    print("\n".join(rows))

    if not failures:
        print("\nEvery touched SKILL.md is within budget, or no worse than it was.")
        return 0

    print("\nFAIL — over the line budget:\n", file=sys.stderr)
    for path, reason in failures:
        print(f"  {path}\n      {reason}", file=sys.stderr)
    print(
        "\n  Move the overflow into `references/<topic>.md` and leave a short pointer.\n"
        "  Schemas, edge cases, recipe detail and long examples are what belongs there;\n"
        "  the procedure a reader follows step-by-step is what stays.\n"
        "  A SKILL.md is loaded in full every time the skill runs — length is a cost\n"
        "  paid on every invocation, not just at review time.\n",
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
