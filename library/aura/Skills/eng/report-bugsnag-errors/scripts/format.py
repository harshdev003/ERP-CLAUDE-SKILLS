"""Digest renderers for the report-bugsnag-errors skill.

render_report       — full markdown digest for the chat (stable contract).
render_report_slack — compact Slack mrkdwn payload text for #alerts-bugsnag.

Read-only posture, same as the investigate skill's triage/focus modes: only
extracted error-signature fields appear (class, message, counts, first/last
seen, project). No request / user / device / metadata blocks are echoed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _skill_version() -> str:
    """Read this skill's semver from the sibling SKILL.md `metadata.version` frontmatter.

    Matches the Agent Skills spec shape (version lives under the `metadata:` map,
    quoted). Scoped to the `metadata:` block inside the opening frontmatter, so a
    `version:` token elsewhere (e.g. in the multiline `description`) can't shadow the
    real value. Single source of truth is the frontmatter, so the footer follows a
    version bump automatically. Falls back to an empty segment if it can't be read.
    """
    try:
        text = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    fm = fm_match.group(1) if fm_match else text
    # the metadata: map is the block of indented lines following a `metadata:` line
    block = re.search(r"(?m)^metadata:[ \t]*\n((?:[ \t]+\S.*\n?)+)", fm)
    if not block:
        return ""
    ver = re.search(r'''(?m)^[ \t]+version:[ \t]*["']?([^"'\s]+)["']?[ \t]*$''', block.group(1))
    return ver.group(1) if ver else ""


def _truncate(s: str, n: int = 100) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n - 1] + "…"


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _error_line(err: dict[str, Any]) -> str:
    """One-line label for an error signature (no message)."""
    slug = err.get("project_slug") or "?"
    cls = err.get("error_class") or "<unknown>"
    events = _int(err.get("events") or err.get("events_count"))
    users = _int(err.get("users") or err.get("users_count"))
    return f"{cls} — events={events} users={users} project={slug}"


# --- chat markdown ---------------------------------------------------

def _release_line(data: dict[str, Any], *, slack: bool = False) -> str | None:
    """One line naming the release scope actually applied, or None.

    A digest that is release-scoped and does not say so is the failure this
    exists to prevent: the numbers look like the project's, not the version's.
    """
    scope = data.get("release_scope")
    if not scope:
        return None
    versions = scope.get("versions") or []
    mode = scope.get("mode", "seen-in")
    question = ("errors seen on" if mode == "seen-in"
                else "errors first introduced in")
    if len(versions) == 1:
        which = versions[0]
    else:
        which = f"{len(versions)} versions ({versions[-1]} → {versions[0]})"
    bold = "*" if slack else "**"
    line = f"{bold}Release scope:{bold} {question} {which} (`{scope.get('field')}`)."
    if scope.get("testflight_caveat"):
        recent = scope.get("recent_versions") or []
        named = ", ".join(recent[:3])
        line += (
            f" ⚠️ {named} shipped within the last two weeks — TestFlight builds "
            "report `release_stage=production` exactly like App Store builds, so "
            "this is TestFlight-weighted and not a store-only picture."
        )
    return line


def render_report(data: dict[str, Any]) -> str:
    since = data["since"]
    scope = data["scope"]
    rollup = data["rollup"]
    total = rollup["total"]

    failed = data.get("failed_projects", 0)
    scope_count = data.get("scope_count", 0)

    release_line = _release_line(data)

    if total["sig"] == 0:
        head = f"# Bugsnag error digest — {scope}, last {since}\n\n"
        if release_line:
            head += release_line + "\n\n"
        if failed and scope_count and failed >= scope_count:
            # Every project failed — this is NOT a quiet window, it's no data.
            return (
                head + f"⚠️ Could not fetch any project — all {failed} failed "
                "(rate-limited or transient). This is a fetch failure, not a "
                "quiet window. Re-run shortly."
            )
        suffix = ""
        if failed:
            suffix = (f" ⚠️ Note: {failed}/{scope_count} project(s) failed to "
                      "fetch, so this may be incomplete.")
        return (
            head + f"0 errors active in {scope} since {since}.{suffix}\n"
            "→ Try a wider --since window."
        )

    lines: list[str] = [f"# Bugsnag error digest — {scope}, last {since}", ""]
    if release_line:
        lines += [release_line, ""]
    if failed:
        lines.append(
            f"⚠️ {failed}/{scope_count} project(s) failed to fetch "
            "(rate-limited/transient) — the counts below are partial."
        )
        lines.append("")

    # 1. Headline rollup
    lines.append("## Headline")
    for key, label in (("web", "Web"), ("mobile", "Mobile"), ("other", "Desktop/embed")):
        b = rollup[key]
        if b["sig"]:
            lines.append(
                f"- **{label}**: {b['sig']} error signatures · "
                f"{b['events']} events · ~{b['users']} users (Σ across signatures)"
            )
    lines.append(
        f"- **Total**: {total['sig']} signatures · {total['events']} events"
    )
    lines.append("")

    # 2. New error classes
    new = data["new_errors"]
    lines.append(f"## 🆕 New error classes ({data['new_total']})")
    if not new:
        lines.append("- None first seen in this window.")
    else:
        for i, err in enumerate(new, 1):
            lines.extend(_error_block(i, err, first_seen=True))
    lines.append("")

    # 3. Top errors by event count
    top = data["top_errors"]
    lines.append(f"## Top errors by event count (top {len(top)})")
    for i, err in enumerate(top, 1):
        lines.extend(_error_block(i, err, first_seen=False))
    lines.append("")

    # 4. Per-project breakdown
    lines.append("## Per-project breakdown")
    for pp in data["per_project"]:
        lines.append(
            f"- `{pp['slug']}` [{pp['family']}]: "
            f"{pp['sig']} signatures · {pp['events']} events · ~{pp['users']} users"
        )
    lines.append("")

    lines.append(
        "→ Drill into any error: `investigate bugsnag --error <error-id>` "
        "(eng:investigate-bugsnag-errors)"
    )
    return "\n".join(lines)


def _error_block(i: int, err: dict[str, Any], *, first_seen: bool) -> list[str]:
    slug = err.get("project_slug") or "?"
    cls = err.get("error_class") or "<unknown>"
    msg = _truncate(err.get("message") or "")
    events = _int(err.get("events") or err.get("events_count"))
    users = _int(err.get("users") or err.get("users_count"))
    eid = err.get("id") or ""
    # For new errors show the true (unfiltered) first occurrence — the plain
    # first_seen is clamped to the query window and would always look recent.
    stamp = (err.get("first_seen_unfiltered") or err.get("first_seen")) \
        if first_seen else err.get("last_seen")
    stamp_label = "first_seen" if first_seen else "last_seen"
    out = [f"{i}. **{cls}** — events={events} users={users}  ({stamp_label}={stamp})"]
    if msg:
        out.append(f"   {msg}")
    out.append(f"   project=`{slug}`  → Drill in: investigate bugsnag --error {eid}")
    return out


# --- Slack mrkdwn ----------------------------------------------------

def render_report_slack(data: dict[str, Any], *, top_n: int = 5) -> str:
    """Compact Slack-mrkdwn summary (not the full digest — a glanceable post)."""
    since = data["since"]
    scope = data["scope"]
    total = data["rollup"]["total"]
    failed = data.get("failed_projects", 0)
    scope_count = data.get("scope_count", 0)
    warn = (f"\n⚠️ {failed}/{scope_count} project(s) failed to fetch — partial."
            if failed else "")
    release_line = _release_line(data, slack=True)
    rel = f"\n{release_line}" if release_line else ""

    if total["sig"] == 0:
        if failed and scope_count and failed >= scope_count:
            body = (f"*Bugsnag digest* — {scope}, last {since}{rel}\n⚠️ Could not "
                    f"fetch any project (all {failed} failed) — fetch failure, "
                    "not a quiet window.")
        else:
            body = (f"*Bugsnag digest* — {scope}, last {since}{rel}\n"
                    f"0 errors active in this window.{warn}")
        return body + "\n" + _slack_footer()

    rollup = data["rollup"]
    parts = []
    for key, label in (("web", "Web"), ("mobile", "Mobile"), ("other", "Desktop/embed")):
        b = rollup[key]
        if b["sig"]:
            parts.append(f"{label} {b['sig']} sig / {b['events']} ev")
    headline = "  ·  ".join(parts) if parts else "—"

    lines = [
        f"*Bugsnag digest* — {scope}, last {since}",
        f"{total['sig']} signatures · {total['events']} events  ({headline})",
    ]
    if release_line:
        lines.insert(1, release_line)
    if failed:
        lines.append(f"⚠️ {failed}/{scope_count} project(s) failed to fetch — partial.")

    new = data["new_errors"][:top_n]
    lines.append(f"\n*🆕 New ({data['new_total']}):*")
    if not new:
        lines.append("• none")
    else:
        for err in new:
            lines.append(f"• {_error_line(err)}")

    lines.append("\n*Top by events:*")
    for err in data["top_errors"][:top_n]:
        lines.append(f"• {_error_line(err)}")

    return "\n".join(lines) + "\n" + _slack_footer()


def _slack_footer() -> str:
    # Date is stamped by the caller (Claude) at post time; keep the skill name
    # + version so readers know the source revision. Matches the Tools/Slack
    # Post-format spec (`<plugin>:<skill>:<version>`).
    ver = _skill_version()
    ident = f"eng:report-bugsnag-errors:{ver}" if ver else "eng:report-bugsnag-errors"
    return f"_Generated by `{ident}`_"
