#!/usr/bin/env python3
"""Fail a PR that changes a skill or plugin without bumping its version.

Two independent levels, both enforced:

**Skill** — any change under ``plugins/<plugin>/skills/<skill>/`` must bump that
skill's ``metadata.version`` in its SKILL.md. This is the version that stamps Slack
footers and report artifacts, so an unbumped edit makes published output attribute
itself to a revision that no longer matches the behaviour that produced it.

**Plugin** — any change under ``plugins/<plugin>/`` must bump that plugin's
``version`` in all three manifest flavours. The manifest version is the install-cache
path (``~/.claude/plugins/cache/<marketplace>/<plugin>/<version>``): Claude Code
treats "that version is already installed" as satisfied and skips the copy, so
shipping under an unchanged version silently freezes every existing install — no
error anywhere, and the reload still reports success.

Most plugin edits never go through ``create-aura-skill`` / ``update-aura-skill``
(bug fixes, review fixes, doc corrections are hand edits), so their auto-bump covers
only part of the traffic. This gate covers the rest, turning a silent runtime failure
that surfaces days later into a loud pre-merge one.

Note the plugin version is **not** derivable as max(skill versions). Bumping a skill
that is not the current maximum would leave the max unchanged, so the manifest would
not move and the install would no-op — the exact failure this gate exists to prevent.
Max-of-skills is only how the versions were originally seeded.

Usage::

    python3 scripts/check-version-bumps.py [--base origin/main]

Exit 0 when everything touched bumped, or when no plugin files were touched.
Exit 1 with a per-item explanation otherwise. Exit 2 when the check itself could not
run (bad base ref, unreadable file) — never a silent pass.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

MANIFEST_DIRS = (".claude-plugin", ".codex-plugin", ".cursor-plugin")

ABSENT = object()      # file not present at that ref
NO_VERSION = object()  # file present, but carries no version


def _git(*args: str):
    """(ok, stdout)."""
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"error: git {' '.join(args)} failed: {exc}", file=sys.stderr)
        return False, ""
    return out.returncode == 0, out.stdout.strip()


def _die(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _label(v) -> str:
    return {ABSENT: "(absent)", NO_VERSION: "(none)"}.get(v, str(v))


def _semver(v: str):
    """(major, minor, patch) or None when it isn't a plain numeric semver."""
    parts = v.split(".")
    if len(parts) > 3 or not all(p.isdigit() for p in parts) or not parts:
        return None
    return tuple(int(p) for p in (parts + ["0", "0"])[:3])


def _went_up(before, after) -> bool:
    """Versions must only ever increase.

    Merely "different" is not enough. The plugin version *is* the install-cache
    path, so moving back to a version a user already installed finds an existing
    directory, skips the copy, and silently reinstates the stale files this whole
    check exists to prevent. Monotonicity also keeps the plugin version meaningful
    when a skill is deleted: removing the skill that set the current maximum still
    moves the plugin *up*, so it legitimately ends up higher than any skill's
    version — that is expected, not drift.
    """
    if before in (ABSENT, NO_VERSION):
        return True                       # nothing to compare against
    b, a = _semver(before), _semver(after)
    if b is None or a is None:
        return before != after            # non-numeric: fall back to "changed"
    return a > b


# ---------- version readers ---------------------------------------------------

def _manifest_version(text: str, where: str):
    try:
        return json.loads(text).get("version") or NO_VERSION
    except json.JSONDecodeError:
        _die(f"{where} is not valid JSON")


def _skill_version(text: str, _where: str):
    """``metadata.version`` from SKILL.md frontmatter.

    Scoped to the ``metadata:`` block so a ``version:`` token in the description
    can't shadow it — same rule the runtime footer resolver uses.
    """
    fm = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    fm = fm.group(1) if fm else text
    block = re.search(r"(?m)^metadata:[ \t]*\n((?:[ \t]+\S.*\n?)+)", fm)
    if not block:
        return NO_VERSION
    m = re.search(r'''(?m)^[ \t]+version:[ \t]*["']?([^"'\s]+)["']?[ \t]*$''', block.group(1))
    return m.group(1) if m else NO_VERSION


def version_at(ref: str, path: str, reader):
    ok, out = _git("show", f"{ref}:{path}")
    return reader(out, f"{path} at {ref}") if ok else ABSENT


def version_now(path: str, reader):
    try:
        with open(path, encoding="utf-8") as fh:
            return reader(fh.read(), path)
    except FileNotFoundError:
        return ABSENT
    except OSError as exc:
        _die(f"{path} could not be read ({exc})")


# ---------- the check ---------------------------------------------------------

def check(base: str) -> int:
    ok, out = _git("diff", "--name-only", f"{base}...HEAD")
    if not ok:
        _die(f"could not diff against {base!r}. Fetch it first "
             f"(`git fetch origin main`) or pass --base.")
    files = [ln for ln in out.splitlines() if ln.strip()]

    plugins, skills = set(), set()
    for f in files:
        parts = f.split("/")
        if parts[0] != "plugins" or len(parts) < 3:
            continue
        plugins.add(parts[1])
        if len(parts) > 4 and parts[2] == "skills":
            skills.add((parts[1], parts[3]))

    if not plugins:
        print("No plugin files changed — nothing to check.")
        return 0

    failures, lines = [], []

    for plugin, skill in sorted(skills):
        path = f"plugins/{plugin}/skills/{skill}/SKILL.md"
        before = version_at(base, path, _skill_version)
        after = version_now(path, _skill_version)
        if after is ABSENT and before is not ABSENT:
            # Genuinely deleted or renamed away — nothing left to bump. The plugin
            # gate still applies, so the removal itself must move the plugin version.
            lines.append(f"  skill   {plugin}:{skill} {_label(before)} (removed)")
        elif after is ABSENT:
            # No SKILL.md at either end: files were added under a skill folder that
            # has no manifest. Without this branch a plugin bump alone would satisfy
            # CI and let a SKILL.md-less skill folder land.
            failures.append(f"skill {plugin}:{skill} — no SKILL.md; every skill folder "
                            f"needs one with metadata.version")
        elif after is NO_VERSION:
            failures.append(f"skill {plugin}:{skill} — {path} has no metadata.version; add one")
        elif before == after:
            failures.append(f"skill {plugin}:{skill} — still at {after}; bump metadata.version")
        elif not _went_up(before, after):
            failures.append(f"skill {plugin}:{skill} — {before} -> {after} goes backwards; "
                            f"versions must only increase")
        else:
            lines.append(f"  skill   {plugin}:{skill} {_label(before)} -> {after}")

    for plugin in sorted(plugins):
        for d in MANIFEST_DIRS:
            path = f"plugins/{plugin}/{d}/plugin.json"
            before = version_at(base, path, _manifest_version)
            after = version_now(path, _manifest_version)
            if after is ABSENT:
                failures.append(f"plugin {plugin} — {path} is missing")
            elif after is NO_VERSION:
                failures.append(f"plugin {plugin} — {path} has no \"version\" key; add one")
            elif before == after:
                failures.append(f"plugin {plugin} — {path} still at {after}; bump it")
            elif not _went_up(before, after):
                failures.append(f"plugin {plugin} — {path} {before} -> {after} goes backwards; "
                                f"the version is the install-cache path, so reusing an older "
                                f"one silently restores a stale install")
            else:
                lines.append(f"  plugin  {plugin} {d:<16} {_label(before)} -> {after}")

    print(f"Plugins touched: {', '.join(sorted(plugins))}")
    if skills:
        print(f"Skills touched:  {', '.join(f'{p}:{s}' for p, s in sorted(skills))}")
    print()
    if lines:
        print("\n".join(lines))
    if not failures:
        print("\nEverything touched bumped its version.")
        return 0

    print("\nFAIL — changed without a version bump:\n", file=sys.stderr)
    for f in failures:
        print(f"  {f}", file=sys.stderr)
    print(
        "\nWhy this is enforced:\n"
        "  skill  — metadata.version stamps Slack footers and report artifacts, so an\n"
        "           unbumped edit makes published output cite a revision that no longer\n"
        "           matches what produced it.\n"
        "  plugin — the manifest version is the install-cache path. Shipping under an\n"
        "           unchanged version means every existing install silently keeps the\n"
        "           old files, and the reload still reports success.\n"
        "\nPatch for a fix, minor for new capability, major for a breaking change.\n"
        "All three plugin manifest flavours must move together.",
        file=sys.stderr)
    return 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="origin/main", help="base ref (default origin/main)")
    return check(ap.parse_args().base)


if __name__ == "__main__":
    sys.exit(main())
