#!/usr/bin/env python3
"""Read and verify a skill's metadata.version, and bump its plugin's manifests.

A skill edit moves two independent versions, and both are enforced pre-merge by
`scripts/check-version-bumps.py`: the skill's `metadata.version`, and the owning
plugin's `version` in all three manifest flavours. The plugin manifest version is
the install-cache path, so a skill shipped under an unchanged plugin version never
reaches an existing install while the reload still reports success.

Several skills document the frontmatter convention by example, so their SKILL.md
body carries extra `metadata:` / `version: "..."` blocks that are content, not
configuration. A whole-file grep/sed hits whichever comes first and silently
reads or edits the wrong one. The skill-side subcommands here are scoped to the
block between the first two `---` fences.

  read        <SKILL.md>                    print the frontmatter version
  assert      <SKILL.md> [--base <ref>]     fail unless exactly one version line
                                            changed, and it is inside the frontmatter
  plugin-read <plugins/PLUGIN>              print the three manifest versions
  plugin-bump <plugins/PLUGIN> --level L    bump `version` in all three manifests

Stdlib only. See references/version-bump.md for the rationale.
"""
import argparse
import json
import os
import re
import subprocess
import sys

MANIFEST_DIRS = (".claude-plugin", ".codex-plugin", ".cursor-plugin")


def frontmatter(path):
    """Return (frontmatter_text, closing_fence_line_number)."""
    lines = open(path, encoding="utf-8").read().split("\n")
    fences = [i for i, l in enumerate(lines, 1) if l.strip() == "---"]
    if len(fences) < 2:
        sys.exit(f"FAIL: {path} has no `---` frontmatter block")
    return "\n".join(lines[fences[0]:fences[1] - 1]), fences[1]


def read_version(path):
    fm, _ = frontmatter(path)
    m = re.search(r'version:\s*"([^"]+)"', fm)
    if not m:
        sys.exit(f"FAIL: {path} frontmatter has no metadata.version")
    return m.group(1)


def changed_version_lines(path, base):
    """New-file line numbers of every added `version:` line in the diff."""
    diff = subprocess.run(
        ["git", "diff", "-U0", base, "--", path],
        capture_output=True, text=True,
    ).stdout
    ln, hits = 0, []
    for line in diff.split("\n"):
        m = re.match(r"@@ -\S+ \+(\d+)", line)
        if m:
            ln = int(m.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if re.match(r"\s*version:", line[1:]):
                hits.append(ln)
            ln += 1
    return hits


def manifest_versions(plugin_dir):
    """{path: version} for all three manifest flavours; exits when one is unusable."""
    out = {}
    for d in MANIFEST_DIRS:
        path = os.path.join(plugin_dir, d, "plugin.json")
        if not os.path.exists(path):
            sys.exit(f"FAIL: {path} is missing — every plugin carries all three flavours")
        try:
            data = json.loads(open(path, encoding="utf-8").read())
        except (OSError, ValueError) as exc:
            sys.exit(f"FAIL: {path} is not readable JSON: {exc}")
        version = data.get("version")
        if not isinstance(version, str) or not version:
            sys.exit(f"FAIL: {path} carries no version")
        out[path] = version
    return out


def next_version(cur, level):
    parts = cur.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        sys.exit(f"FAIL: {cur!r} is not a plain numeric semver — bump it by hand")
    major, minor, patch = (int(p) for p in parts)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def bump_plugin(plugin_dir, level, dry_run):
    """Move all three manifests to the next version at `level`."""
    versions = manifest_versions(plugin_dir)
    distinct = set(versions.values())
    if len(distinct) != 1:
        detail = "\n".join(f"  {p} — {v}" for p, v in versions.items())
        sys.exit(
            "FAIL: the three manifests disagree, so there is no single version to "
            f"bump from — reconcile them first:\n{detail}"
        )
    cur = distinct.pop()
    new = next_version(cur, level)
    for path in versions:
        text = open(path, encoding="utf-8").read()
        patched, hits = re.subn(
            r'(?m)^(\s*"version"\s*:\s*)"[^"]*"', rf'\g<1>"{new}"', text, count=1
        )
        if hits != 1:
            sys.exit(f'FAIL: no "version" line to rewrite in {path}')
        if not dry_run:
            open(path, "w", encoding="utf-8").write(patched)
    verb = "would bump" if dry_run else "bumped"
    print(f"{verb} {plugin_dir}: {cur} -> {new} ({level}, all three manifests)")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="print the frontmatter version")
    r.add_argument("skill_md")
    a = sub.add_parser("assert", help="verify the bump landed in the frontmatter")
    a.add_argument("skill_md")
    a.add_argument("--base", default="origin/main", help="diff base (default: origin/main)")
    pr = sub.add_parser("plugin-read", help="print the three manifest versions")
    pr.add_argument("plugin_dir", help="path to plugins/<plugin>")
    pb = sub.add_parser("plugin-bump", help="bump `version` in all three manifests")
    pb.add_argument("plugin_dir", help="path to plugins/<plugin>")
    pb.add_argument("--level", required=True, choices=("patch", "minor", "major"),
                    help="same level as the skill bump")
    pb.add_argument("--dry-run", action="store_true", help="print the move, write nothing")
    args = ap.parse_args()

    if args.cmd == "read":
        print(read_version(args.skill_md))
        return

    if args.cmd == "plugin-read":
        for path, version in manifest_versions(args.plugin_dir).items():
            print(f"{version}\t{path}")
        return

    if args.cmd == "plugin-bump":
        bump_plugin(args.plugin_dir, args.level, args.dry_run)
        return

    _, fm_end = frontmatter(args.skill_md)
    hits = changed_version_lines(args.skill_md, args.base)
    bad = [h for h in hits if h > fm_end]
    if bad:
        sys.exit(
            f"FAIL: edited a body example at line(s) {bad}, not the frontmatter "
            f"(ends L{fm_end}) — the bump landed in documentation, not configuration"
        )
    if len(hits) != 1:
        sys.exit(f"FAIL: expected exactly 1 frontmatter version change, got {len(hits)}")
    print(f"OK: one version change at L{hits[0]}, inside the frontmatter")


if __name__ == "__main__":
    main()
