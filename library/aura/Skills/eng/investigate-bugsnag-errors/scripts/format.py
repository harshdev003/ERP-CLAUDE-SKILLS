"""Per-mode digest renderers.

Mode 1 — triage: top-N error signatures across one project or all 12.
Mode 2 — focus: one error, with stacktrace + recent events + pivot tables.
Mode 3 — trace: one event, full payload (PII guardrail relaxed by intent).

Every digest ends with a `→ Drill in:` hint pointing at the next-step
CLI command, so the user never has to remember the syntax.
"""
from __future__ import annotations

from typing import Any

from normalize import (
    extract_error_class,
    extract_message,
    extract_stacktrace,
    format_frame,
    parse_breadcrumbs,
    parse_frames,
    request_breadcrumb_summary,
)
from projects import lookup as project_lookup


# --- helpers ---------------------------------------------------------

def _truncate(s: str, n: int = 100) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n - 1] + "…"


def _short(s: str | None, n: int = 8) -> str:
    return (s or "")[:n]


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# --- Mode 1: triage --------------------------------------------------

def render_triage(errors: list[dict[str, Any]], *,
                  project_slug: str | None = None,
                  cross_project: bool = False,
                  since: str | None = None) -> str:
    if not errors:
        scope = f"project `{project_slug}`" if project_slug else "all projects"
        since_str = f" since {since}" if since else ""
        return (
            f"0 errors in {scope}{since_str}.\n"
            "→ Done — try a wider --since window."
        )

    lines: list[str] = []
    header = (
        f"Top {len(errors)} errors"
        f"{' across all projects' if cross_project else f' in `{project_slug}`'}"
        f"{(' since ' + since) if since else ''}:"
    )
    lines.append(header)
    lines.append("")

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
        "(copy any ID above)"
    )
    return "\n".join(lines)


# --- Mode 2: focus ---------------------------------------------------

def render_focus(error: dict[str, Any], events: list[dict[str, Any]],
                 *, event_id_in_url: str | None = None) -> str:
    slug = error.get("project_slug") or "?"
    family = project_lookup(slug)
    cls = error.get("error_class") or "<unknown>"
    msg = error.get("message") or ""
    status = error.get("status") or "open"
    severity = error.get("severity") or "?"
    stage = error.get("release_stage") or "?"
    events_count = _safe_int(error.get("events") or error.get("events_count"))
    users_count = _safe_int(error.get("users") or error.get("users_count"))
    first_seen = error.get("first_seen") or "?"
    last_seen = error.get("last_seen") or "?"
    error_id = error.get("id") or ""

    repo = family.repo or "TBD"
    lines: list[str] = [
        f"Error: {cls}",
        f"  {msg}",
        "",
        f"App family:    {family.family}  (stack={family.stack}, repo={repo})",
        f"Status:        {status}  (severity={severity}, stage={stage})",
        f"Counts:        events={events_count}  users={users_count}",
        f"Window:        first={first_seen}  last={last_seen}",
        f"Project:       {slug}",
        f"Bugsnag URL:   https://app.bugsnag.com/aura-health/{slug}/errors/{error_id}",
        "",
    ]

    # Stacktrace from the latest event (or the explicit event_id pulled from the URL)
    target_event: dict[str, Any] | None = None
    if event_id_in_url:
        target_event = next((e for e in events if e.get("id") == event_id_in_url), None)
    if target_event is None and events:
        target_event = events[0]

    if target_event:
        eid = target_event.get("id") or "?"
        lines.append(f"Stacktrace (event {_short(eid)}):")
        frames = parse_frames(extract_stacktrace(target_event), family.stack)
        for f in frames[:8]:
            lines.append(format_frame(f))
        if len(frames) > 8:
            lines.append(f"  … {len(frames) - 8} more frames (use Mode 3 to see them all)")
        lines.append("")

        crumbs = parse_breadcrumbs(target_event.get("breadcrumbs"))
        if crumbs:
            lines.append("Breadcrumbs (last 8):")
            for c in crumbs:
                summary = request_breadcrumb_summary(c["metaData"]) if c["type"] == "request" else ""
                tail = f"  ({summary})" if summary else ""
                lines.append(f"  {c['ts']}  [{c['type']}]  {c['name']}{tail}")
            lines.append("")

    # Recent events summary
    if events:
        lines.append(f"Recent events ({len(events)}):")
        for ev in events[:5]:
            ts = ev.get("received_at") or ev.get("timestamp") or "?"
            ctx = ev.get("context") or ""
            eid = _short(ev.get("id"))
            lines.append(f"  {ts}  {eid}  {ctx}")
        lines.append("")

    # Drill hints
    lines.append("→ Drill in:")
    if target_event:
        lines.append(
            f"   investigate bugsnag --event {target_event.get('id')} "
            f"--project {slug}  (full event payload — Mode 3)"
        )
    if family.repo:
        lines.append(
            f"   investigate bugsnag --prepare-code-context {error_id}  "
            f"(spawn eng:code-investigator on {family.repo} — Mode 2.5)"
        )
    else:
        lines.append(
            f"   Mode 2.5 unavailable for `{slug}` — no repo mapping "
            f"in the app-family table yet."
        )
    lines.append(
        f"   investigate bugsnag --prepare-linear-card {error_id}  "
        "(file via shared:add-linear-issues — Mode 4a)"
    )
    lines.append(
        f"   investigate bugsnag --prepare-slack-reply {error_id}  "
        "(post via Slack MCP — Mode 4b)"
    )

    return "\n".join(lines)


# --- Mode 3: trace ---------------------------------------------------

def render_trace(event: dict[str, Any]) -> str:
    """Full event payload. PII guardrail relaxed by intent — engineer
    has explicitly drilled into one event."""
    eid = event.get("id") or "?"
    received = event.get("received_at") or event.get("timestamp") or "?"
    project = event.get("project_id") or "?"
    context = event.get("context") or ""
    error_class = extract_error_class(event)
    message = extract_message(event)
    severity = event.get("severity") or "?"

    lines: list[str] = [
        f"Event: {eid}",
        f"  received: {received}",
        f"  project:  {project}",
        f"  context:  {context}",
        f"  error:    {error_class}: {message}",
        f"  severity: {severity}",
        "",
    ]

    frames = parse_frames(extract_stacktrace(event))
    if frames:
        lines.append(f"Stacktrace ({len(frames)} frames):")
        for f in frames:
            lines.append(format_frame(f))
        lines.append("")

    crumbs = parse_breadcrumbs(event.get("breadcrumbs"), max_count=999)
    if crumbs:
        lines.append(f"Breadcrumbs ({len(crumbs)}):")
        for c in crumbs:
            summary = request_breadcrumb_summary(c["metaData"]) if c["type"] == "request" else ""
            tail = f"  ({summary})" if summary else ""
            lines.append(f"  {c['ts']}  [{c['type']}]  {c['name']}{tail}")
        lines.append("")

    user = event.get("user") or {}
    if user:
        lines.append("User:")
        for k in ("id", "email", "name"):
            if user.get(k):
                lines.append(f"  {k}: {user[k]}")
        lines.append("")

    device = event.get("device") or {}
    if device:
        # Bugsnag stores OS + browser as nested objects: device.os.name, device.os.version,
        # device.browser.name, device.browser.version (not flat os_name / browser_name).
        os = device.get("os") or {}
        browser = device.get("browser") or {}
        device_lines: list[str] = []
        if os.get("name"):
            ver = os.get("version") or ""
            device_lines.append(f"  os:           {os['name']} {ver}".rstrip())
        if browser.get("name"):
            ver = browser.get("version") or ""
            device_lines.append(f"  browser:      {browser['name']} {ver}".rstrip())
        for k, label in (("manufacturer", "manufacturer"),
                         ("model", "model"),
                         ("user_agent", "user_agent"),
                         ("locale", "locale"),
                         ("time_zone", "time_zone")):
            if device.get(k):
                device_lines.append(f"  {label}: {device[k]}")
        if device_lines:
            lines.append("Device:")
            lines.extend(device_lines)
            lines.append("")

    request = event.get("request") or {}
    if request:
        lines.append("Request:")
        for k in ("method", "url", "referer"):
            if request.get(k):
                lines.append(f"  {k}: {request[k]}")
        lines.append("")

    metadata = event.get("metaData") or event.get("metadata") or {}
    if metadata:
        lines.append("Metadata sections present:")
        for section in metadata:
            lines.append(f"  - {section}")
        lines.append("  (full payload visible in the Bugsnag UI)")
        lines.append("")

    lines.append("→ Done — last mode in the chain.")
    return "\n".join(lines)
