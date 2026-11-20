#!/usr/bin/env python3
"""Fail a PR whose SKILL.md `description` breaks either plugin-loader rule.

Two rules, both enforced by the loader that syncs the marketplace:

  1. `description` is at most 1024 characters.
  2. `description` contains no XML-looking tags. Angle-bracket placeholders
     (`<uid>`, `<name>`, `<userId>`) read as markup to the loader — write them
     as `{uid}` / `{name}` instead.

The limit itself is stated once, in the authoring best-practices — this script
reads it from there rather than carrying a second copy (see `_read_limit`), the
same "point, don't restate" rule `check-skill-length.py` follows for the line
budget.

**Threshold, not ratchet** — deliberately unlike the line gate next door. That
one grandfathers today's over-budget files because a plain threshold would block
every PR touching one, and a blocked-by-default gate gets switched off. Here
there is nothing to grandfather: ENG-1236 brought all 144 offenders into
compliance in one pass, so the repo starts clean and a hard line costs nobody
anything. A ratchet would also be strictly weaker — it would let a description
sit at 1500 forever as long as nobody grew it, while the loader kept warning.

**Repo-wide, not just changed files.** A description can start failing without
its own file changing — the rules live in the loader, not here. Sweeping all 204
skills costs milliseconds and means the gate reports the true state of the repo
rather than the state of one diff.

Why a gate at all: descriptions are the only skill registry (CLAUDE.md,
Architecture principle 3). The union of every SKILL.md `description` is what the
model matches user intent against, so an invalid one is not cosmetic — it is the
routing surface going unvalidated. 144 skills drifted over the line before
anything was watching.

    python3 scripts/check-skill-description.py

Exit: 0 clean · 1 a description violates a rule · 2 the check could not run.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

# The limit lives in the authoring best-practices; this is where to find it, not
# what it is.
LIMIT_SOURCE = "plugins/shared/skills/create-aura-skill/references/best-practices.md"
LIMIT_PATTERN = re.compile(
    r"`description`\s+(?:field\s+)?(?:is\s+)?at\s+most\s+(\d+)\s+characters", re.I
)
LIMIT_FALLBACK = 1024

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)

# What the loader reads as markup. Deliberately matches an opening, closing or
# self-closing tag whose name starts with a letter — so `<500` (a tier label) and
# a bare `<` in prose are not flagged, but `<uid>` and `</name>` are.
TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9_.:-]*\s*/?>")


def _die(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _read_limit(root: pathlib.Path) -> int:
    """Parse the character limit out of the best-practices file.

    Falls back rather than dying, for the same reason the line gate does: a
    reworded source should run the gate on the last known number, never take it
    offline. A silently disabled gate is the failure this script exists to stop.
    """
    try:
        text = (root / LIMIT_SOURCE).read_text(encoding="utf-8")
    except OSError:
        print(f"warning: {LIMIT_SOURCE} unreadable — using {LIMIT_FALLBACK}",
              file=sys.stderr)
        return LIMIT_FALLBACK
    m = LIMIT_PATTERN.search(text)
    if not m:
        print(f"warning: no character limit found in {LIMIT_SOURCE} — "
              f"using {LIMIT_FALLBACK}", file=sys.stderr)
        return LIMIT_FALLBACK
    return int(m.group(1))


def extract_description(text: str):
    """Return the description as the loader sees it, or None if there is none.

    Handles the three YAML scalar forms a SKILL.md uses: a folded block (`>`),
    a literal block (`|`), and a plain inline scalar. Folded blocks join their
    lines with spaces and keep a blank line as a newline, which is what makes
    the character count match the loader's.
    """
    m = FRONTMATTER.match(text)
    if not m:
        return None
    fm = m.group(1)

    dm = re.search(r"^description:[ \t]*(.*)$", fm, re.M)
    if not dm:
        return None

    head = dm.group(1).strip()
    rest = fm[dm.end():].split("\n")[1:]

    body_lines = []
    for line in rest:
        if line.strip() == "" or line.startswith(("  ", "\t")):
            body_lines.append(line)
        else:
            break

    if head.startswith((">", "|")):
        body = "\n".join(l[2:] if l.startswith("  ") else l.strip()
                         for l in body_lines)
        if head.startswith("|"):
            return body.strip()
        paragraphs = re.split(r"\n\s*\n", body)
        return "\n".join(
            " ".join(part.strip() for part in para.split("\n")).strip()
            for para in paragraphs
        ).strip()

    joined = [head] + [l.strip() for l in body_lines]
    return " ".join(part for part in joined if part).strip()


def violations(desc: str, limit: int):
    """Every rule this description breaks, as human-readable reasons."""
    out = []
    if len(desc) > limit:
        out.append(f"description is {len(desc)} characters, over the {limit} limit "
                   f"(trim {len(desc) - limit})")
    tags = sorted(set(TAG.findall(desc)))
    if tags:
        shown = ", ".join(tags[:6]) + ("…" if len(tags) > 6 else "")
        out.append(f"description contains XML-looking tags: {shown} — "
                   f"write placeholders as {{name}}, not <name>")
    return out


def check(root: pathlib.Path) -> int:
    limit = _read_limit(root)

    skills = sorted((root / "plugins").glob("*/skills/*/SKILL.md"))
    if not skills:
        _die(f"no SKILL.md found under {root / 'plugins'} — run from the repo root.")

    failures = []
    for path in skills:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            _die(f"{rel} could not be read ({exc})")
        desc = extract_description(text)
        if desc is None:
            failures.append((rel, ["no `description` in the SKILL.md frontmatter"]))
            continue
        reasons = violations(desc, limit)
        if reasons:
            failures.append((rel, reasons))

    print(f"SKILL.md description limit: {limit} characters, no XML tags "
          f"(from {LIMIT_SOURCE})")
    print(f"Checked {len(skills)} skills.")

    if not failures:
        print("\nEvery description is within the limit and free of XML tags.")
        return 0

    print(f"\nFAIL — {len(failures)} description(s) violate a loader rule:\n",
          file=sys.stderr)
    for rel, reasons in failures:
        print(f"  {rel}", file=sys.stderr)
        for reason in reasons:
            print(f"      {reason}", file=sys.stderr)
    print(
        "\n  Trim by cutting mechanism detail, rationale, ticket links and output\n"
        "  format — all of which belong in the SKILL.md body. Keep every distinctive\n"
        "  trigger phrase and the `Do NOT use for X (use Y)` disambiguation: those are\n"
        "  the registry the model routes on, and dropping one silently narrows what\n"
        "  the skill can be reached by.\n",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    return check(pathlib.Path(ap.parse_args().root).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
