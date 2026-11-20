"""OpenAI Admin API transport for `eng:report-prompt-testing-cost`.

Self-contained per Architecture principle 1 — this skill imports nothing from a
sibling skill. Kept deliberately small: only the three org-level reads this
skill needs (`/organization/costs`, `/organization/usage/completions`,
`/organization/projects` + its per-project `/api_keys`).

Every HTTP call in this skill funnels through `request()`. That is the single
chokepoint the test suite stubs, so the pagination, discovery, and costing code
above it all runs for real under test. Do not add a second place that opens a
socket.
"""
from __future__ import annotations

import time
from typing import Iterator

import requests

OPENAI_API = "https://api.openai.com/v1"

COSTS_PATH = "/organization/costs"
USAGE_COMPLETIONS_PATH = "/organization/usage/completions"
PROJECTS_PATH = "/organization/projects"

# Retry budget for 429 / 5xx. The org-level endpoints are slow and rate-limited;
# a 30-day window walks ~30 buckets x N projects, so give it room.
MAX_ATTEMPTS = 6
MAX_BACKOFF_SECONDS = 30


class OpenAIAdminError(RuntimeError):
    """A request failed in a way the caller may want to degrade around."""


def request(
    path: str,
    params: dict,
    api_key: str,
    *,
    project_id: str | None = None,
) -> dict:
    """GET `path`, retrying on 429 and 5xx. Raises `OpenAIAdminError` otherwise.

    Raises rather than `sys.exit`-ing so a single unreadable project can be
    skipped without killing a whole-org walk.
    """
    url = f"{OPENAI_API}{path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    if project_id:
        headers["OpenAI-Project"] = project_id

    last_err = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=60)
        except requests.RequestException as exc:
            last_err = str(exc)
            time.sleep(min(2 ** attempt, MAX_BACKOFF_SECONDS))
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            # Retry-After can be fractional; float() then clamp.
            try:
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
            except (TypeError, ValueError):
                wait = float(2 ** attempt)
            last_err = f"{resp.status_code}: {resp.text[:200]}"
            time.sleep(min(wait, MAX_BACKOFF_SECONDS))
            continue

        if not resp.ok:
            raise OpenAIAdminError(
                f"OpenAI {resp.status_code} on {path}: {resp.text[:300]}"
            )
        return resp.json()

    raise OpenAIAdminError(f"OpenAI {path}: gave up after retries — last error: {last_err}")


def paged(path: str, params: dict, api_key: str, *, project_id: str | None = None) -> Iterator[dict]:
    """Yield every bucket from a `next_page`-paginated org endpoint."""
    page: str | None = None
    while True:
        query = dict(params)
        if page:
            query["page"] = page
        data = request(path, query, api_key, project_id=project_id)
        for bucket in data.get("data", []):
            yield bucket
        page = data.get("next_page")
        if not page:
            break


def fetch_projects(api_key: str) -> dict[str, str]:
    """Map project_id -> project name, including archived projects.

    Archived projects are included on purpose: a key that ran traffic inside a
    since-archived project still spent real money in the window.
    """
    projects: dict[str, str] = {}
    after: str | None = None
    while True:
        params: dict = {"limit": 100, "include_archived": "true"}
        if after:
            params["after"] = after
        data = request(PROJECTS_PATH, params, api_key)
        rows = data.get("data", [])
        for proj in rows:
            pid = proj.get("id")
            if pid:
                projects[pid] = proj.get("name") or pid
        if not data.get("has_more") or not rows:
            break
        last_id = rows[-1].get("id")
        if not last_id or last_id == after:
            break  # no forward progress — stop rather than spin
        after = last_id
    return projects


def fetch_project_api_keys(project_id: str, api_key: str) -> list[dict]:
    """Return every api_key row for one project (empty list if unreadable).

    Paginated on `has_more` / `after`, like `fetch_projects`. Stopping at the
    first page would drop any key past the 100th **with no error**, and a
    dropped `Claude` key is a silent undercount of derived spend — the same
    class of quiet wrongness this skill exists to surface.

    A project the admin key cannot enumerate is skipped rather than fatal — the
    org has projects that predate the current key's grants.
    """
    keys: list[dict] = []
    after: str | None = None
    while True:
        params: dict = {"limit": 100}
        if after:
            params["after"] = after
        try:
            data = request(f"{PROJECTS_PATH}/{project_id}/api_keys", params, api_key)
        except OpenAIAdminError:
            return keys  # keep whatever paged in before access failed
        rows = data.get("data", [])
        keys.extend(rows)
        if not data.get("has_more") or not rows:
            break
        last_id = rows[-1].get("id")
        if not last_id or last_id == after:
            break  # no forward progress — stop rather than spin
        after = last_id
    return keys
