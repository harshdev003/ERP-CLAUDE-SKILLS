"""Renderers for the analyze-bugsnag-releases skill.

render_release_rollup    — org view: latest release per project (markdown).
render_project_releases  — one project's recent releases table (markdown).

Read-only: only release-level fields are shown (version, time, stage, errors
introduced/seen). No event/user/request payloads. Crash-free stability is
omitted on purpose — the aura-health projects don't report sessions, so the
session/user-stability fields are null.
"""
from __future__ import annotations

from typing import Any

_STABILITY_NOTE = (
    "_Crash-free stability omitted — Bugsnag session tracking is off for "
    "these projects (sessions/user-stability fields are null)._"
)


def _date(ts: str) -> str:
    """Compact date from an ISO timestamp ('2026-05-22T22:22:56+00:00')."""
    return (ts or "").split("T")[0] or "?"


def render_release_rollup(rows: list[dict[str, Any]], *,
                          stage: str | None = None) -> str:
    scope = "latest release per project"
    if stage:
        scope += f" (stage~`{stage}`)"
    if not rows:
        return (
            f"# Bugsnag release rollup — {scope}\n\n"
            "No releases found for any project"
            f"{f' at stage ~`{stage}`' if stage else ''}.\n"
            "→ Try without --stage, or check a single --project."
        )

    lines = [f"# Bugsnag release rollup — {scope}", "",
             "Sorted by errors introduced in the latest release (worst first):", ""]
    for r in rows:
        flag = " ⚠️" if r["introduced"] else ""
        lines.append(
            f"- `{r['slug']}` [{r['family']}]: **v{r['version']}** "
            f"({_date(r['time'])}, {r['stage']}) — "
            f"introduced={r['introduced']}{flag}  seen={r['seen']}"
        )
    lines.append("")
    lines.append(
        "→ Drill into one app's release history: "
        "`analyze bugsnag releases --project <slug>`"
    )
    lines.append(_STABILITY_NOTE)
    return "\n".join(lines)


def render_project_releases(slug: str, releases: list[dict[str, Any]], *,
                            stage: str | None = None) -> str:
    header = f"# Release health — `{slug}`"
    if stage:
        header += f" (stage~`{stage}`)"
    if not releases:
        return (
            f"{header}\n\nNo releases found"
            f"{f' at stage ~`{stage}`' if stage else ''}.\n"
            "→ Try without --stage, or a different project."
        )

    # Flag the release that introduced the most new errors.
    worst_idx = max(range(len(releases)),
                    key=lambda i: _int(releases[i].get("errors_introduced_count")))
    worst_has_intro = _int(releases[worst_idx].get("errors_introduced_count")) > 0

    lines = [f"{header} — last {len(releases)} releases", ""]
    for i, rel in enumerate(releases):
        intro = _int(rel.get("errors_introduced_count"))
        seen = _int(rel.get("errors_seen_count"))
        version = rel.get("app_version") or "?"
        st = rel.get("release_stage")
        st_name = st.get("name") if isinstance(st, dict) else (st or "?")
        flag = "  ⚠️ most regressions" if (i == worst_idx and worst_has_intro) else ""
        lines.append(
            f"- **v{version}** — {_date(rel.get('release_time'))}  [{st_name}]  "
            f"introduced={intro}  seen={seen}{flag}"
        )
    lines.append("")
    lines.append(
        f"→ See the errors currently active in this project: "
        f"`investigate bugsnag --project {slug}`"
    )
    lines.append(_STABILITY_NOTE)
    return "\n".join(lines)


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0
