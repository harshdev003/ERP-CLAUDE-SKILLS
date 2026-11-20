"""Builds the structured prompt for eng:code-investigator (Mode 2.5).

Mode 2.5 is orchestrated by Claude (the model), not by Python — the
CLI can only build the prompt and print it to stdout. The Claude model
reads this prompt, spawns:

    Agent({ subagent_type: "eng:code-investigator", prompt: <prompt> })

…and folds the agent's structured report into the focus digest before
returning to the user.

This module:
1. Fetches the error + latest event via the Bugsnag REST client.
2. Resolves the suspect repo from the app-family table.
3. Builds the structured prompt the agent expects (per the input
   schema in plugins/eng/agents/code-investigator.md).
4. Returns it. Or, if the project has no repo mapping (TBD entry in
   the app-family table), returns a `[Mode 2.5 skipped]` message
   that Claude relays as-is.
"""
from __future__ import annotations

from typing import Any

from bugsnag import BugsnagClient
from normalize import parse_frames
from projects import lookup as project_lookup


SKIP_PREFIX = "[Mode 2.5 skipped]"


def _skip(reason: str) -> str:
    return f"{SKIP_PREFIX} {reason}"


def build_code_context_prompt(error: dict[str, Any],
                              event: dict[str, Any] | None,
                              project_slug: str | None) -> str:
    """Render the structured prompt for eng:code-investigator.

    Returns either the prompt body, or a `[Mode 2.5 skipped]` line
    when there's nothing actionable to investigate.
    """
    family = project_lookup(project_slug)
    if not family.repo:
        return _skip(
            f"No repo mapping for project `{project_slug or '?'}` "
            f"(family: {family.family}). To enable code investigation "
            f"for this project, add the repo to scripts/projects.py and "
            f"references/app-family-routing.md."
        )

    cls = error.get("error_class") or "<unknown>"
    msg = error.get("message") or ""
    first_seen = error.get("first_seen") or "?"
    last_seen = error.get("last_seen") or "?"

    frames = parse_frames((event or {}).get("stacktrace"), family.stack)
    # Prefer in-project frames (skip vendor/node_modules); fall back to all.
    in_project_frames = [f for f in frames if f.in_project] or frames
    top_frames = in_project_frames[:5]

    if not top_frames:
        return _skip(
            "No usable stacktrace frames in the latest event. "
            "(The event may be old enough that Bugsnag has pruned frame detail, "
            "or sourcemaps haven't resolved.)"
        )

    lines: list[str] = [
        "Investigate the source code around a runtime error and return a short "
        "structured report. You are spawned with all the context you need below.",
        "",
        f"repo: {family.repo}",
        f"error_summary: {cls}: {msg} (first_seen={first_seen}, last_seen={last_seen})",
        "",
        "focus_frames:",
    ]
    for f in top_frames:
        line_part = f":{f.line}" if f.line else ""
        lines.append(f"  - path: {f.file}{line_part}")
        lines.append(f"    function: {f.method}")
    lines.append("")
    lines.append("time_window:")
    lines.append(f"  since: {first_seen}")
    lines.append(f"  until: {last_seen}")
    lines.append("")
    lines.append(
        f"extra_context: app family is `{family.family}` (stack: {family.stack}). "
        f"This is a {'high-PII' if family.high_pii else 'standard'} surface."
    )
    lines.append("")
    lines.append("max_words: 200")
    lines.append("")
    lines.append(
        "Per your read-only contract, do not modify any code. Return the report "
        "shape defined in your system prompt (Repo / Local / Top frame / Last "
        "touch / Recent commits / Callers / Hypothesis)."
    )
    return "\n".join(lines)


def fetch_and_build(client: BugsnagClient, *, error_id: str,
                    project_slug: str | None) -> str:
    """Fetch the error + latest event, then build the prompt.

    Used by investigate.py --prepare-code-context. Passes project_slug
    through so we can route via the app-family table.
    """
    error = client.get_error(error_id)
    if not project_slug:
        project_slug = error.get("project_slug")
    events = client.list_events(error_id, per_page=1)
    event = events[0] if events else None
    return build_code_context_prompt(error, event, project_slug)
