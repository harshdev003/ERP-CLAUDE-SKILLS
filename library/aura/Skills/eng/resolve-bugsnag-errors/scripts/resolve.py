"""CLI entrypoint for the resolve-bugsnag-errors skill.

WRITE-SIDE. Resolve (fix), reopen (open), or ignore a Bugsnag error, and/or
add a comment, via the Data Access API:

  PATCH /errors/<id>           {"operation": "fix"|"open"|"ignore"}
  POST  /errors/<id>/comments  {"message": "..."}

Safe by construction: a run is a **DRY RUN unless --confirm is passed**. Without
--confirm the script prints the current error state and the exact request(s) it
WOULD send, and mutates nothing. SKILL.md additionally requires Claude to show
the user the before-state and get explicit approval before any --confirm run.

Run with --help for the full flag list.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import format as fmt
from bugsnag import BugsnagClient, BugsnagError
from projects import lookup as project_lookup


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="resolve-bugsnag",
        description="Resolve / reopen / ignore a Bugsnag error (write-side).",
    )
    p.add_argument("--error", dest="error_id", required=True,
                   help="Error ID to operate on.")
    p.add_argument("--operation", choices=["fix", "open", "ignore"],
                   help="fix = resolve, open = reopen, ignore = mute.")
    p.add_argument("--comment",
                   help="Optional comment to add to the error.")
    p.add_argument("--confirm", action="store_true",
                   help="Actually send the write(s). WITHOUT this flag the run "
                        "is a DRY RUN that mutates nothing.")
    return p


def _slug_and_family(error: dict[str, Any], projects: list[dict[str, Any]]):
    by_id = {p["id"]: p["slug"] for p in projects}
    slug = by_id.get(error.get("project_id"))
    return slug, project_lookup(slug)


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 stdout so the digest's arrows/check marks (→ ✓) don't raise
    # UnicodeEncodeError on Windows consoles defaulting to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = _build_argparser().parse_args(argv)

    if not args.operation and not args.comment:
        print("error: nothing to do — pass --operation (fix|open|ignore) "
              "and/or --comment.", file=sys.stderr)
        return 2

    try:
        client = BugsnagClient()
        org_id = client.find_org_id("aura-health")
        projects = client.list_projects(org_id)
        error = client.get_error(args.error_id)
    except BugsnagError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    slug, family = _slug_and_family(error, projects)

    # Dry run — the default. Show the current state + exactly what WOULD be sent.
    if not args.confirm:
        print(fmt.render_dryrun(error, slug, family, args.operation, args.comment))
        return 0

    # Confirmed — perform the write(s). Track what actually landed so a failure
    # on a later step is never reported as "nothing happened": each mutation is
    # its own try, and on error we say exactly what was already applied.
    applied: list[str] = []
    try:
        if args.operation:
            client.update_error(args.error_id, args.operation)
            applied.append(f"operation={args.operation}")
        if args.comment:
            client.add_comment(args.error_id, args.comment)
            applied.append("comment")
    except BugsnagError as e:
        done = ", ".join(applied) if applied else "nothing"
        print(f"error: write failed after applying [{done}]: {e}\n"
              f"  Some changes may have landed — re-check the error state in "
              f"Bugsnag before retrying.", file=sys.stderr)
        return 1

    # Re-read for the after-state. A failure here does NOT mean the writes
    # failed — they already succeeded above — so report success either way.
    try:
        after = client.get_error(args.error_id)
    except BugsnagError:
        print(f"✓ Applied [{', '.join(applied)}], but could not re-read the "
              f"error to show the after-state. The write(s) succeeded.")
        return 0

    print(fmt.render_result(error, after, slug, family,
                            args.operation, args.comment))
    return 0


if __name__ == "__main__":
    sys.exit(main())
