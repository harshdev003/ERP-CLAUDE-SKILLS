"""CLI entrypoint for the analyze-bugsnag-releases skill.

Per-release error / regression view from /projects/<id>/releases. Two modes:

  (default, no --project)  Org rollup — the latest release per project,
                           worst regressions (errors introduced) first.
  --project <slug>         Detailed recent-releases table for one project.

Crash-free stability is intentionally NOT shown: the aura-health projects
don't report sessions to Bugsnag, so total_sessions_count and the
user-stability fields are null. The per-release errors_introduced_count /
errors_seen_count ARE populated, and are what this skill surfaces — they
answer "did this release introduce new errors?". Read-only, Data Access API v2.

Run with --help for the full flag list.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import format as fmt
from bugsnag import BugsnagClient, BugsnagError
from projects import fuzzy_match, lookup as project_lookup


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="analyze-bugsnag-releases",
        description="Per-release error / regression view via the Data Access API.",
    )
    p.add_argument("--project",
                   help="One project slug (fuzzy match) for the detailed "
                        "recent-releases table. Omit for the org rollup "
                        "(latest release per project).")
    p.add_argument("--limit", type=int, default=10,
                   help="Detail mode: number of recent releases to show "
                        "(default 10).")
    p.add_argument("--stage",
                   help="Filter to a release stage (e.g. production, PROD, "
                        "staging). Case-insensitive substring match.")
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


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _stage_name(rel: dict[str, Any]) -> str:
    st = rel.get("release_stage")
    if isinstance(st, dict):
        return st.get("name") or ""
    return st or ""


def _stage_match(rel: dict[str, Any], stage: str | None) -> bool:
    if not stage:
        return True
    return stage.lower() in _stage_name(rel).lower()


def _row(p_slug: str, rel: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": p_slug,
        "family": project_lookup(p_slug).family,
        "version": rel.get("app_version") or "?",
        "time": rel.get("release_time") or "",
        "stage": _stage_name(rel) or "?",
        "introduced": _int(rel.get("errors_introduced_count")),
        "seen": _int(rel.get("errors_seen_count")),
    }


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 stdout so the digest's arrows/em-dashes/warning glyphs
    # (→ — ⚠️) don't raise UnicodeEncodeError on Windows consoles (cp1252).
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

    # When --stage filters, scan deeper before filtering: a project's most
    # recent releases may all be other stages, with matching ones further back.
    # Without --stage a shallow fetch is enough. The releases endpoint caps
    # per_page at 10, so pagination covers max_results across pages (cap 100).
    DEEP = 100

    try:
        # Detail mode — recent releases for one project
        if args.project:
            proj = _resolve_project(args.project, projects)
            cap = DEEP if args.stage else max(args.limit * 3, 30)
            rels = client.list_releases(proj["id"], per_page=10, max_results=cap)
            rels = [r for r in rels if _stage_match(r, args.stage)]
            rels.sort(key=lambda r: r.get("release_time") or "", reverse=True)
            rels = rels[:args.limit]
            print(fmt.render_project_releases(proj["slug"], rels, stage=args.stage))
            return 0

        # Org rollup — latest release per project, worst regressions first
        rows: list[dict[str, Any]] = []
        cap = DEEP if args.stage else 10
        for p in projects:
            try:
                rels = client.list_releases(p["id"], per_page=10, max_results=cap)
            except BugsnagError:
                # Per-project failure shouldn't kill the rollup — skip it.
                continue
            rels = [r for r in rels if _stage_match(r, args.stage)]
            if not rels:
                continue
            rels.sort(key=lambda r: r.get("release_time") or "", reverse=True)
            rows.append(_row(p["slug"], rels[0]))
        rows.sort(key=lambda r: -r["introduced"])
        print(fmt.render_release_rollup(rows, stage=args.stage))
        return 0
    except BugsnagError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
