"""OpenAI production-project discovery + fuzzy resolution for
investigate-openai-trace.

Migrated off Braintrust 2026-06-05 (PROD-2464, umbrella PROD-2456). The
Braintrust era hard-coded a name<->id map for the org `Aura Health 1`. On
OpenAI the live set of production projects is auto-discovered at runtime from
`GET /v1/organization/projects` (Admin key), keeping only those whose name
ends in ` [Production]`. This is the same discovery used by
`eng:report-openai-user-cost` -> `fetch_production_projects()`.

Why auto-discover rather than hard-code: the production project set churns
(~13-17 projects today) as new pipelines ship, and the OpenAI project ids are
opaque `proj_...` strings nobody memorises. Discovering keeps the skill correct
without a code change on every new pipeline.

A few well-known ids are kept below purely as documentation / a sanity anchor —
they are NOT the source of truth (the live org list is). Key ids for reference:
  Conversation [Production]  proj_esFyd4eq2VIAjKUn69X1ZFfk
  Memory [Production]        proj_f0PpUSyYnJNifUZj44FrjUhr
  Journal [Production]       proj_qXZ9CZnjp4mL5PVsW43JyOpj
"""
from __future__ import annotations

from dataclasses import dataclass

# Documentation anchors only — the live org list is the source of truth.
KNOWN_PROJECT_IDS = {
    "Conversation": "proj_esFyd4eq2VIAjKUn69X1ZFfk",
    "Memory": "proj_f0PpUSyYnJNifUZj44FrjUhr",
    "Journal": "proj_qXZ9CZnjp4mL5PVsW43JyOpj",
}

PRODUCTION_SUFFIX = " [Production]"


@dataclass(frozen=True)
class Project:
    name: str        # short name, e.g. "Memory" (the [Production] suffix stripped)
    project_id: str  # OpenAI proj_... id
    full_name: str   # e.g. "Memory [Production]"


def fetch_production_projects(client) -> list[Project]:
    """Auto-discover the org's production projects (name suffix ` [Production]`).

    `client` is an OpenAIClient. Paginates `/v1/organization/projects` and keeps
    active projects whose display name ends in ` [Production]`. Returns Projects
    sorted by short name.
    """
    projects: list[Project] = []
    after: str | None = None
    while True:
        params: dict = {"limit": 100, "include_archived": "false"}
        if after:
            params["after"] = after
        data = client._get("/organization/projects", params=params)
        assert data is not None
        items = data.get("data", [])
        for p in items:
            name = p.get("name") or p.get("id", "")
            if name.endswith(PRODUCTION_SUFFIX):
                short = name[: -len(PRODUCTION_SUFFIX)]
                projects.append(Project(name=short, project_id=p["id"], full_name=name))
        if not data.get("has_more") or not items:
            break
        after = data.get("last_id") or items[-1].get("id")
        if not after:
            break
    projects.sort(key=lambda p: p.name.lower())
    # Register id -> display name so the client can resolve each project's
    # scoped stored-completions key (PROD-3572).
    register = getattr(client, "register_projects", None)
    if callable(register):
        register({p.project_id: p.full_name for p in projects})
    return projects


def resolve(name_or_id: str, projects: list[Project]) -> Project | None:
    """Resolve a user input to a Project from the live list.

    Tries: exact id -> exact short name (case-insensitive) -> exact full name ->
    single substring match. Returns None when ambiguous or unknown.
    """
    if not name_or_id:
        return None
    key = name_or_id.strip()
    # Exact id.
    for p in projects:
        if p.project_id == key:
            return p
    lowered = key.lower()
    # Exact short or full name.
    for p in projects:
        if p.name.lower() == lowered or p.full_name.lower() == lowered:
            return p
    # Single substring match (e.g. 'mem' -> 'Memory').
    hits = [p for p in projects if lowered in p.name.lower()]
    if len(hits) == 1:
        return hits[0]
    return None


def suggest(name: str, projects: list[Project], limit: int = 5) -> list[str]:
    """Suggestion list when `resolve` returns None. Pure substring + prefix
    overlap — the user is correcting a typo, not searching semantically."""
    lowered = (name or "").strip().lower()
    scored: list[tuple[int, str]] = []
    for p in projects:
        n = p.name.lower()
        score = 0
        if lowered[:3] and n.startswith(lowered[:3]):
            score += 5
        if lowered[:3] and lowered[:3] in n:
            score += 2
        if lowered and lowered in n:
            score += 3
        scored.append((score, p.name))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [n for _, n in scored[:limit]]
