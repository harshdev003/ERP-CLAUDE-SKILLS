"""Digest renderer for the search-bugsnag-by-user skill.

The error signatures a given user id hit, newest first. Read-only: only
extracted signature fields appear (class, message, counts, last-seen, project).
No request / user / device / metadata blocks are echoed — same PII posture as
the investigate skill's triage/focus modes. user.id (a Firebase UID) is shown
because the user supplied it. Each row ends with an `investigate bugsnag
--error <id>` hint so it chains into eng:investigate-bugsnag-errors.
"""
from __future__ import annotations

from typing import Any

from projects import lookup as project_lookup


def _truncate(s: str, n: int = 100) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n - 1] + "…"


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def render_user_search(errors: list[dict[str, Any]], *,
                       user_id: str,
                       since: str | None = None,
                       project_slug: str | None = None,
                       project_count: int = 0) -> str:
    since_str = f" since {since}" if since else ""
    if not errors:
        scope = f"project `{project_slug}`" if project_slug else f"{project_count} projects"
        return (
            f"0 errors for user {user_id} in {scope}{since_str}.\n"
            "→ Done — try a wider --since window, or double-check the user id."
        )

    if project_slug:
        header = f"Errors for user {user_id} in `{project_slug}`{since_str}:"
    else:
        header = f"Errors for user {user_id} across {project_count} projects{since_str}:"

    lines: list[str] = [header, ""]

    for i, err in enumerate(errors, 1):
        slug = err.get("project_slug") or project_slug or "?"
        family = project_lookup(slug)
        cls = err.get("error_class") or "<unknown>"
        msg = _truncate(err.get("message") or "")
        events = _safe_int(err.get("events") or err.get("events_count"))
        users = _safe_int(err.get("users") or err.get("users_count"))
        last = err.get("last_seen") or ""
        eid = err.get("id") or ""
        lines.append(f"{i}. [{family.family}] {cls}")
        if msg:
            lines.append(f"   {msg}")
        lines.append(
            f"   project={slug}  events={events}  users={users}  last_seen={last}"
        )
        lines.append(f"   → Drill in: investigate bugsnag --error {eid}")
        lines.append("")

    lines.append(
        "→ Drill in: investigate bugsnag --error <error-id>  "
        "(copy any ID above — eng:investigate-bugsnag-errors)"
    )
    return "\n".join(lines)
