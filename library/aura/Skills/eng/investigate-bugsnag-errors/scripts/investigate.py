"""CLI entrypoint for the investigate-bugsnag-errors skill.

Modes:
  --error <id>                    Mode 2 — focus on one error
  --event <id>                    Mode 3 — trace one event (needs --project)
  --project <slug>                Mode 1 — triage one project (default --since 24h)
  --all-projects                  Mode 1 — triage everything; sorted by event count
  --url <url>                     Auto-detect mode from the URL shape
  --prepare-code-context <id>     Mode 2.5 — build the structured prompt for
                                  eng:code-investigator. Output is consumed by
                                  Claude, which spawns the subagent and folds
                                  the report into the focus digest.
  --prepare-linear-card <id>      Mode 4a — build a Linear card payload (JSON);
                                  Claude composes with shared:add-linear-issues
                                  after explicit user confirmation.
  --prepare-slack-reply <id>      Mode 4b — build a Slack reply draft (JSON);
                                  Claude posts via the Slack MCP after explicit
                                  user confirmation.

Both Mode 4 flags accept an optional --code-context-file <path> so the
caller can include a Mode 2.5 report in the payload.

Run with --help for the full flag list.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import action_handoffs
import code_investigation
import format as fmt
from bugsnag import BugsnagClient, BugsnagError, parse_bugsnag_url
from projects import fuzzy_match


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="investigate-bugsnag",
        description="Triage / focus / trace Bugsnag errors via the Data Access API.",
    )
    p.add_argument("--url",
                   help="A Bugsnag error/project URL — auto-detects the mode.")
    p.add_argument("--error", dest="error_id",
                   help="Error ID (Mode 2 — focus).")
    p.add_argument("--event", dest="event_id",
                   help="Event ID (Mode 3 — trace; pair with --project).")
    p.add_argument("--project",
                   help="Project slug (Mode 1 — triage; or context for --event).")
    p.add_argument("--all-projects", action="store_true",
                   help="Mode 1: triage across all 12 projects.")
    p.add_argument("--since",
                   help="Time window for triage as a Bugsnag duration "
                        "shorthand: '1h', '24h', '7d', '30d'. Default: '24h'.")
    p.add_argument("--limit", type=int, default=10,
                   help="Mode 1: cap the digest at N error signatures (default 10).")
    p.add_argument("--prepare-code-context", dest="code_context_id",
                   metavar="ERROR_ID",
                   help="Mode 2.5: build the structured prompt for "
                        "eng:code-investigator. Claude reads the output, "
                        "spawns the subagent, and merges the report into the "
                        "focus digest.")
    p.add_argument("--prepare-linear-card", dest="linear_card_id",
                   metavar="ERROR_ID",
                   help="Mode 4a: build a Linear card payload (JSON). Claude "
                        "composes with shared:add-linear-issues after user "
                        "confirmation.")
    p.add_argument("--prepare-slack-reply", dest="slack_reply_id",
                   metavar="ERROR_ID",
                   help="Mode 4b: build a Slack reply draft (JSON). Claude "
                        "posts via the Slack MCP after user confirmation.")
    p.add_argument("--code-context-file",
                   help="Optional Mode 2.5 report file to splice into the "
                        "Mode 4 payload (Linear card description / Slack "
                        "reply). Pass the path the orchestrator wrote the "
                        "subagent's report to.")
    return p


def _resolve_project(slug: str | None,
                     projects: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve a slug (possibly fuzzy) to a live project record."""
    if not slug:
        raise BugsnagError("--project is required for this mode")
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


def _decorate_with_slug(errors: list[dict[str, Any]],
                        projects: list[dict[str, Any]]) -> None:
    """Attach project_slug to each error record in place.

    Bugsnag returns project_id on errors but not the slug, so the renderers
    enrich at the edge.
    """
    by_id = {p["id"]: p["slug"] for p in projects}
    for e in errors:
        if "project_id" in e and "project_slug" not in e:
            e["project_slug"] = by_id.get(e["project_id"])


def _default_since() -> str:
    """Default triage window: 24 hours, in Bugsnag's duration-shorthand format.

    Bugsnag's `event.since` filter accepts shorthand strings like '1h', '24h',
    '7d', '30d' rather than absolute ISO timestamps with a comparison operator.
    """
    return "24h"


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 stdout so the digests' arrows/em-dashes/ellipses (→ — …)
    # don't raise UnicodeEncodeError on Windows consoles defaulting to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = _build_argparser().parse_args(argv)

    # URL takes precedence — extract whatever it carries
    url_event_id: str | None = None
    if args.url:
        try:
            parsed = parse_bugsnag_url(args.url)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        if parsed["error_id"] and not args.error_id:
            args.error_id = parsed["error_id"]
        if parsed["event_id"]:
            url_event_id = parsed["event_id"]
        if parsed["project"] and not args.project:
            args.project = parsed["project"]

    if not (args.error_id or args.event_id or args.project or args.all_projects
            or args.code_context_id or args.linear_card_id or args.slack_reply_id):
        print(
            "error: nothing to investigate. Provide --url, --error, --event "
            "(+ --project), --project, --all-projects, --prepare-code-context, "
            "--prepare-linear-card, or --prepare-slack-reply.",
            file=sys.stderr,
        )
        return 2

    # Load the optional Mode 2.5 report once, used by both Mode 4 actions.
    code_context: str | None = None
    if args.code_context_file:
        try:
            code_context = Path(args.code_context_file).read_text()
        except OSError as e:
            print(f"error: --code-context-file: {e}", file=sys.stderr)
            return 2

    try:
        client = BugsnagClient()
        org_id = client.find_org_id("aura-health")
        projects = client.list_projects(org_id)
    except BugsnagError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        # Mode 2.5: build the prompt for eng:code-investigator
        if args.code_context_id:
            error = client.get_error(args.code_context_id)
            _decorate_with_slug([error], projects)
            print(code_investigation.fetch_and_build(
                client,
                error_id=args.code_context_id,
                project_slug=error.get("project_slug"),
            ))
            return 0

        # Mode 4a: build a Linear card payload
        if args.linear_card_id:
            error = client.get_error(args.linear_card_id)
            _decorate_with_slug([error], projects)
            print(action_handoffs.fetch_and_build_linear_card(
                client,
                error_id=args.linear_card_id,
                project_slug=error.get("project_slug"),
                code_context=code_context,
            ))
            return 0

        # Mode 4b: build a Slack reply draft
        if args.slack_reply_id:
            error = client.get_error(args.slack_reply_id)
            _decorate_with_slug([error], projects)
            print(action_handoffs.fetch_and_build_slack_reply(
                client,
                error_id=args.slack_reply_id,
                project_slug=error.get("project_slug"),
                code_context=code_context,
            ))
            return 0

        # Mode 3: trace one event
        if args.event_id:
            project = _resolve_project(args.project, projects)
            event = client.get_event(project["id"], args.event_id)
            print(fmt.render_trace(event))
            return 0

        # Mode 2: focus on one error
        if args.error_id:
            error = client.get_error(args.error_id)
            _decorate_with_slug([error], projects)
            events = client.list_events(args.error_id, per_page=5)

            # Hydrate the target event with its full payload — `list_events`
            # returns summaries that don't include the `exceptions` array
            # (and thus no stacktrace). The full stacktrace + breadcrumbs
            # live at `/projects/<id>/events/<event-id>`. Without this,
            # render_focus prints an empty Stacktrace section.
            target_event_id = url_event_id or (events[0]["id"] if events else None)
            if target_event_id and error.get("project_id"):
                try:
                    full_event = client.get_event(error["project_id"], target_event_id)
                except BugsnagError:
                    full_event = None  # pruned / unavailable — fall back to summary
                if full_event:
                    replaced = False
                    for i, e in enumerate(events):
                        if e.get("id") == target_event_id:
                            events[i] = full_event
                            replaced = True
                            break
                    if not replaced:
                        events.insert(0, full_event)

            print(fmt.render_focus(error, events, event_id_in_url=url_event_id))
            return 0

        # Mode 1: cross-project triage
        if args.all_projects:
            since = args.since or _default_since()
            all_errors: list[dict[str, Any]] = []
            for p in projects:
                try:
                    errs = client.list_errors(p["id"], since=since,
                                              per_page=10, max_results=10)
                except BugsnagError:
                    # Per-project failure (rate-limit, transient) shouldn't
                    # kill the run — just degrade and continue.
                    continue
                for e in errs:
                    e["project_slug"] = p["slug"]
                all_errors.extend(errs)
            all_errors.sort(
                key=lambda e: (
                    -((e.get("events") or e.get("events_count") or 0)),
                    e.get("last_seen") or "",
                ),
            )
            print(fmt.render_triage(all_errors[:args.limit],
                                    cross_project=True, since=since))
            return 0

        # Mode 1: triage one project
        project = _resolve_project(args.project, projects)
        since = args.since or _default_since()
        errors = client.list_errors(project["id"], since=since,
                                    per_page=args.limit, max_results=args.limit)
        _decorate_with_slug(errors, projects)
        print(fmt.render_triage(errors,
                                project_slug=project["slug"], since=since))
        return 0

    except BugsnagError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
