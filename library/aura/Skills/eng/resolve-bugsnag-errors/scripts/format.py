"""Renderers for the resolve-bugsnag-errors skill.

render_dryrun  — current state + the exact request(s) that WOULD be sent.
render_result  — before/after state after a confirmed write.

Read posture: only the error signature + status is shown — no event / user /
request / metadata payloads. The expected post-operation status is shown so a
reviewer can sanity-check a mutation before (dry run) and after (result).
"""
from __future__ import annotations

import json
from typing import Any

# operation -> the status Bugsnag moves the error into.
_STATUS_AFTER = {"fix": "fixed", "open": "open", "ignore": "ignored"}


def _target_lines(error: dict[str, Any], slug: str | None, family: Any) -> list[str]:
    cls = error.get("error_class") or "<unknown>"
    msg = (error.get("message") or "")
    if len(msg) > 100:
        msg = msg[:99] + "…"
    status = error.get("status") or "?"
    eid = error.get("id") or ""
    fam = family.family if family else "Unknown"
    lines = [
        f"Error: {cls}",
        f"  {msg}" if msg else "",
        f"  project=`{slug or '?'}` [{fam}]  status={status}",
    ]
    # Only build the UI URL when the slug resolved — otherwise it would point
    # at /None/. Without a slug, the error id alone still identifies it.
    if slug:
        lines.append(f"  https://app.bugsnag.com/aura-health/{slug}/errors/{eid}")
    else:
        lines.append(f"  error id: {eid}")
    return [l for l in lines if l]


def render_dryrun(error: dict[str, Any], slug: str | None, family: Any,
                  operation: str | None, comment: str | None) -> str:
    eid = error.get("id") or ""
    cur = error.get("status") or "?"
    lines = ["DRY RUN — nothing was sent.", ""]
    lines += _target_lines(error, slug, family)
    lines += ["", "Would send:"]
    if operation:
        after = _STATUS_AFTER.get(operation, "?")
        # json.dumps so the shown body is byte-for-byte what requests sends via
        # json=, even when the operation/comment contains quotes or newlines.
        body = json.dumps({"operation": operation})
        lines.append(f"  PATCH /errors/{eid}  {body}   (status {cur} → {after})")
    if comment:
        body = json.dumps({"message": comment})
        lines.append(f"  POST /errors/{eid}/comments  {body}")
    lines += [
        "",
        "→ To apply: re-run the exact command above with --confirm appended.",
    ]
    return "\n".join(lines)


def render_result(before: dict[str, Any], after: dict[str, Any],
                  slug: str | None, family: Any,
                  operation: str | None, comment: str | None) -> str:
    lines = ["✓ Applied.", ""]
    lines += _target_lines(after, slug, family)
    lines += [""]
    if operation:
        lines.append(
            f"  operation={operation}: status "
            f"{before.get('status') or '?'} → {after.get('status') or '?'}"
        )
    if comment:
        lines.append("  comment added")
    return "\n".join(lines)
