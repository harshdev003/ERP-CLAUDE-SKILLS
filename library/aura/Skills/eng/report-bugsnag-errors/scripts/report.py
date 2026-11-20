"""CLI entrypoint for the report-bugsnag-errors skill.

Proactive Bugsnag error digest across all 12 aura-health projects (or one):

  - Headline rollup — error signatures / events / users, split web vs mobile
  - New error classes — first seen inside the window
  - Top errors by event count — cross-project, loudest signatures
  - Per-project breakdown — counts grouped by app family

Read-only against the Data Access API v2. Complements
eng:investigate-bugsnag-errors, which is the reactive drill-into-one-error
side; every error row here ends with the investigate command so the two chain.

Run with --help for the full flag list.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import format as fmt
from bugsnag import RELEASE_MODES, BugsnagClient, BugsnagError
from projects import fuzzy_match

_DURATION_RE = re.compile(r"^(\d+)([hd])$")

# Per-project fetch cap. The errors endpoint allows up to 100 per page; one
# page per project keeps the cross-project fan-out to a single call each while
# still surfacing the loudest signatures and any new classes in the window.
_PER_PROJECT_CAP = 100


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="report-bugsnag",
        description="Recurring Bugsnag error digest via the Data Access API.",
    )
    p.add_argument("--project",
                   help="Limit the digest to one project slug (fuzzy match). "
                        "Default: all projects.")
    p.add_argument("--all-projects", action="store_true",
                   help="Digest across all 12 projects (the default when "
                        "--project is omitted).")
    p.add_argument("--since", default="7d",
                   help="Window as a Bugsnag duration shorthand: '1h', '24h', "
                        "'7d', '30d'. Default: '7d' (weekly).")
    p.add_argument("--limit", type=int, default=10,
                   help="Cap each section (new / top) at N rows (default 10).")
    p.add_argument("--release",
                   help="Scope the digest to an app version: '1.29.0' for that "
                        "version alone, or '>=1.28.0' for it and everything "
                        "released after it. Requires --release-mode. Omit for "
                        "an all-releases digest.")
    p.add_argument("--release-mode", choices=sorted(RELEASE_MODES),
                   help="Which release question to ask (required with "
                        "--release, no default): 'seen-in' = the error occurred "
                        "on that version (monitoring); 'introduced-in' = it "
                        "first appeared there (regression). They return "
                        "different sets, so the choice is always explicit.")
    p.add_argument("--slack", action="store_true",
                   help="Emit a Slack mrkdwn payload (JSON) for #alerts-bugsnag "
                        "instead of the chat digest. Claude posts it via the "
                        "Slack MCP after you confirm the channel.")
    return p


def _resolve_project(slug: str, projects: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve a slug (possibly fuzzy) to a live project record."""
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


def _parse_since(since: str) -> timedelta:
    """Parse Bugsnag duration shorthand ('24h', '7d') into a timedelta.

    Used to compute the "new error" cutoff client-side; the same string is
    passed straight to the API as the `event.since` filter.
    """
    m = _DURATION_RE.match(since or "")
    if not m:
        raise BugsnagError(f"--since must look like '24h' or '7d' (got {since!r})")
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(hours=n) if unit == "h" else timedelta(days=n)


# A release scoped to a version shipped within this many days is likely to be
# TestFlight-weighted — see _resolve_release_scope.
_TESTFLIGHT_WINDOW_DAYS = 14

# How far back the release walk looks when resolving a --release spec. The
# endpoint caps per_page at 10, so this is 20 pages; `list_releases` derives its
# page budget from it, so the two cannot drift apart.
_RELEASE_LOOKBACK = 200


def _resolve_release_scope(client: Any, project_id: str, spec: str,
                           mode: str) -> dict[str, Any]:
    """Turn a --release spec into the concrete version list to query.

    `>=X` means "X and every version released after it", resolved from the
    releases endpoint rather than by comparing version strings. Bugsnag has no
    range operator (`gte` on a version field is a 400) and no OR (repeating the
    filter pair silently keeps the last value), so the range has to become an
    explicit list of versions that the caller fans out over.

    Ordering comes from the endpoint, which returns newest-first by release
    time. That is deliberately *not* semver arithmetic: "and everything after"
    is a question about what shipped, and a hotfix cut from an old branch
    belongs in the answer even though its version string sorts low.
    """
    want_range = spec.startswith(">=")
    target = spec[2:].strip() if want_range else spec.strip()
    if not target:
        raise BugsnagError("--release needs a version, e.g. '1.29.0' or '>=1.28.0'")

    # per_page is capped at 10 on the releases endpoint — anything larger is a
    # flat HTTP 400, so the walk pages rather than asking for one big response.
    releases = client.list_releases(project_id, per_page=10,
                                    max_results=_RELEASE_LOOKBACK)
    # Newest-first, de-duplicated: one app_version can have several release
    # records (rebuilds), and querying the same version twice would double its
    # event counts in the union.
    #
    # Order is tracked in its own set rather than keyed off `stamp`, which only
    # gains an entry when a record carries a release_time — a version whose
    # first record is undated would otherwise be appended twice, over-counting
    # a `>=` slice and cutting the range at the wrong occurrence.
    ordered: list[str] = []
    seen: set[str] = set()
    stamp: dict[str, str] = {}
    for rel in releases:
        version = rel.get("app_version")
        if not version:
            continue
        if version not in seen:
            seen.add(version)
            ordered.append(version)
        released = rel.get("release_time")
        if released and released > stamp.get(version, ""):
            stamp[version] = released

    if target not in ordered:
        known = ", ".join(ordered[:12]) or "(none reported)"
        # Say so when the lookback was exhausted: "not found" and "not found in
        # the newest N" are different answers, and only one of them means the
        # version is wrong.
        capped = (" The search covered the most recent "
                  f"{_RELEASE_LOOKBACK} release records and hit that ceiling, "
                  "so an older version may exist beyond it."
                  if len(releases) >= _RELEASE_LOOKBACK else "")
        raise BugsnagError(
            f"Version {target!r} has no release record on this project, so a "
            f"scoped digest would be empty. Known versions (newest first): "
            f"{known}.{capped}"
        )

    versions = ordered[:ordered.index(target) + 1] if want_range else [target]

    cutoff = datetime.now(timezone.utc) - timedelta(days=_TESTFLIGHT_WINDOW_DAYS)
    recent = [v for v in versions
              if (_parse_ts(stamp.get(v)) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
    return {
        "spec": spec,
        "mode": mode,
        "field": RELEASE_MODES[mode],
        "versions": versions,
        "expanded": want_range,
        # TestFlight builds report release_stage=production, exactly like App
        # Store builds — the stage field cannot separate them. So a digest
        # scoped to a just-shipped version is TestFlight-weighted, and the
        # output has to say so rather than let a reader assume store traffic.
        "testflight_caveat": bool(recent),
        "recent_versions": recent,
    }


def _parse_ts(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp (Bugsnag uses a trailing Z) to aware UTC."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 stdout so the digest's arrows/em-dashes/emoji (→ — 🆕)
    # don't raise UnicodeEncodeError on Windows consoles defaulting to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = _build_argparser().parse_args(argv)

    if args.release and not args.release_mode:
        print("error: --release requires --release-mode. 'seen-in' asks whether "
              "the error occurred on that version (what a monitoring digest "
              "wants); 'introduced-in' asks whether it first appeared there "
              "(what a regression report wants). They return different sets, so "
              "there is no safe default.", file=sys.stderr)
        return 2
    if args.release_mode and not args.release:
        print("error: --release-mode has no effect without --release.",
              file=sys.stderr)
        return 2

    try:
        delta = _parse_since(args.since)
    except BugsnagError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    cutoff = datetime.now(timezone.utc) - delta

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
            scope_label = f"`{scope_projects[0]['slug']}`"
        else:
            scope_projects = projects
            scope_label = f"{len(scope_projects)} projects"

        if args.release and len(scope_projects) > 1:
            raise BugsnagError(
                "--release needs --project. A version string means different "
                "things in different projects (1.29.0 of the iOS app is not "
                "1.29.0 of UserWeb), so scoping a whole-org digest to one "
                "version would silently mix them."
            )

        release_scope: dict[str, Any] | None = None
        if args.release:
            release_scope = _resolve_release_scope(
                client, scope_projects[0]["id"], args.release, args.release_mode)

        all_errors: list[dict[str, Any]] = []
        failed = 0
        for p in scope_projects:
            try:
                if release_scope:
                    # One request per version, unioned in the helper — Bugsnag
                    # cannot OR them and silently keeps the last value if asked.
                    errs = client.list_errors_for_releases(
                        p["id"], release_scope["versions"],
                        mode=release_scope["mode"], since=args.since,
                        sort="events", per_page=_PER_PROJECT_CAP,
                        max_results=_PER_PROJECT_CAP)
                else:
                    errs = client.list_errors(p["id"], since=args.since, sort="events",
                                              per_page=_PER_PROJECT_CAP,
                                              max_results=_PER_PROJECT_CAP)
            except BugsnagError:
                # Per-project failure (rate-limit, transient) shouldn't kill the
                # fan-out — degrade and continue, but count it so the digest can
                # distinguish "couldn't fetch" from a genuinely quiet window.
                failed += 1
                continue
            for e in errs:
                e["project_slug"] = p["slug"]
            all_errors.extend(errs)
    except BugsnagError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    data = _aggregate(all_errors, since=args.since, scope=scope_label,
                      cutoff=cutoff, limit=args.limit)
    data["failed_projects"] = failed
    data["scope_count"] = len(scope_projects)
    data["release_scope"] = release_scope

    if args.slack:
        # Channel by name only — the script can't read AI Knowledge. Per SKILL.md,
        # Claude resolves the canonical #alerts-bugsnag ID from Tools/Slack and
        # posts with that ID (not name resolution), matching the eng report-skill
        # convention.
        payload = {
            "channel_suggestion": "#alerts-bugsnag",
            "text": fmt.render_report_slack(data),
        }
        print(json.dumps(payload, indent=2))
    else:
        print(fmt.render_report(data))
    return 0


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _first_seen(e: dict[str, Any]) -> str | None:
    """True first occurrence for new-detection — `first_seen_unfiltered` ONLY.

    The plain `first_seen` is clamped to the query window (a 7d query reports it
    as 7d-ago even for a months-old error), so falling back to it would mislabel
    legacy errors as new — the exact failure mode this skill exists to avoid.
    An error missing `first_seen_unfiltered` returns None and is treated as
    not-new, which is the safe default.
    """
    return e.get("first_seen_unfiltered")


def _surface(slug: str) -> str:
    """Coarse rollup bucket from the app family: web / mobile / other."""
    from projects import lookup as project_lookup
    fam = project_lookup(slug).family.lower()
    if "web" in fam:
        return "web"
    if "mobile" in fam:
        return "mobile"
    return "other"  # desktop / embed / unknown


def _aggregate(all_errors: list[dict[str, Any]], *, since: str, scope: str,
               cutoff: datetime, limit: int) -> dict[str, Any]:
    """Roll the raw error list into the digest's four sections."""
    rollup = {k: {"sig": 0, "events": 0, "users": 0}
              for k in ("web", "mobile", "other", "total")}
    per_proj: dict[str, dict[str, Any]] = {}

    for e in all_errors:
        slug = e.get("project_slug") or "?"
        surface = _surface(slug)
        events = _int(e.get("events") or e.get("events_count"))
        users = _int(e.get("users") or e.get("users_count"))
        for bucket in (rollup[surface], rollup["total"]):
            bucket["sig"] += 1
            bucket["events"] += events
            bucket["users"] += users
        from projects import lookup as project_lookup
        pp = per_proj.setdefault(slug, {"slug": slug,
                                        "family": project_lookup(slug).family,
                                        "sig": 0, "events": 0, "users": 0})
        pp["sig"] += 1
        pp["events"] += events
        pp["users"] += users

    # "New" must use first_seen_unfiltered — the true first occurrence. The
    # plain `first_seen` is clamped to the query window (a 7d query reports
    # first_seen as 7d-ago for an error that's months old), so it flags every
    # active error as new. first_seen_unfiltered is not clamped.
    new_errors = [e for e in all_errors
                  if (ts := _parse_ts(_first_seen(e))) and ts >= cutoff]
    new_errors.sort(key=lambda e: _first_seen(e) or "", reverse=True)

    top_errors = sorted(all_errors,
                        key=lambda e: -_int(e.get("events") or e.get("events_count")))
    per_project = sorted(per_proj.values(), key=lambda d: -d["events"])

    return {
        "since": since,
        "scope": scope,
        "rollup": rollup,
        "new_errors": new_errors[:limit],
        "new_total": len(new_errors),
        "top_errors": top_errors[:limit],
        "per_project": per_project,
    }


if __name__ == "__main__":
    sys.exit(main())
