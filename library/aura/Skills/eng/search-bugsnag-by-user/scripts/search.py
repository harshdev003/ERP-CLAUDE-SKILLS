"""CLI entrypoint for the search-bugsnag-by-user skill.

Search the Bugsnag error signatures a given Firebase user id hit, newest first,
org-wide across all 12 aura-health projects by default (or one with --project).
Read-only via the Data Access API v2.

This is the "which errors did this user see?" entrypoint. Each result row ends
with an `investigate bugsnag --error <id>` hint, so the engineer hands off to
eng:investigate-bugsnag-errors for the focus → trace → resolve flow.

Run with --help for the full flag list.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import format as fmt
from bugsnag import BugsnagClient, BugsnagError
from projects import fuzzy_match


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="search-bugsnag-by-user",
        description="Search Bugsnag error signatures by user id via the Data "
                    "Access API.",
    )
    p.add_argument("--user", dest="user", required=True, metavar="FIREBASE_UID",
                   help="Firebase user id to search the error signatures for.")
    p.add_argument("--project",
                   help="Limit to one project slug (fuzzy match). Default: all "
                        "projects (org-wide).")
    p.add_argument("--since", default="30d",
                   help="Window as a Bugsnag duration shorthand: '1h', '24h', "
                        "'7d', '30d'. Default: '30d'.")
    p.add_argument("--limit", type=int, default=10,
                   help="Cap the digest at N error signatures (default 10).")
    return p


def _resolve_project(slug: str, projects: list[dict[str, Any]]) -> dict[str, Any]:
    available = [p["slug"] for p in projects]
    if slug in available:
        match = slug
    else:
        match = fuzzy_match(slug, available)
        if not match:
            raise BugsnagError(
                f"Project `{slug}` not found. Available slugs:\n  "
                + "\n  ".join(sorted(available))
            )
    return next(p for p in projects if p["slug"] == match)


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 stdout so the digest's arrows/em-dashes (→ —) don't raise
    # UnicodeEncodeError on Windows consoles defaulting to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = _build_argparser().parse_args(argv)

    try:
        client = BugsnagClient()
        org_id = client.find_org_id("aura-health")
        projects = client.list_projects(org_id)
    except BugsnagError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        if args.project:
            scope_projects = [_resolve_project(args.project, projects)]
        else:
            scope_projects = projects

        user_errors: list[dict[str, Any]] = []
        for p in scope_projects:
            try:
                errs = client.list_errors(p["id"], since=args.since,
                                          user_id=args.user,
                                          per_page=args.limit,
                                          max_results=args.limit)
            except BugsnagError:
                # Per-project failure (rate-limit, transient) shouldn't kill the
                # org-wide fan-out — degrade and continue.
                continue
            for e in errs:
                e["project_slug"] = p["slug"]
            user_errors.extend(errs)
        user_errors.sort(key=lambda e: e.get("last_seen") or "", reverse=True)

        print(fmt.render_user_search(
            user_errors[:args.limit],
            user_id=args.user,
            since=args.since,
            project_slug=(scope_projects[0]["slug"] if args.project else None),
            project_count=len(scope_projects),
        ))
        return 0
    except BugsnagError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
