"""OpenAI stored-completions client + Logs-URL decoder for the
investigate-openai-trace skill.

Migrated off Braintrust 2026-06-05 (PROD-2464, umbrella PROD-2456). Reads
OpenAI's stored chat-completions endpoints instead of the Braintrust BTQL
endpoint. Access pattern documented in AI Knowledge `Tools/OpenAI` ->
"Reading stored completions (BT migration access pattern)".

Folder/file name is kept as the historical skill name (`investigate-braintrust-
trace`) for now — a rename to `investigate-openai-trace` is deferred to a
coordinated follow-up so the just-pushed guardrail PR (#346) that references
this path doesn't break cross-branch.

Loaded at runtime by investigate.py and sibling scripts. Two auth layers
(PROD-3572):
  * `/v1/organization/*` (the project-discovery call) uses OPENAI_ADMIN_API_KEY
    (the Admin key, sk-admin-..., Owner role) — still valid there.
  * `/v1/chat/completions` stored-completions reads use a PROJECT-SCOPED
    service-account key per project, named OPENAI_<PROJECT>_API_KEY (derived
    from the project's display name — "Memory [Production]" ->
    OPENAI_MEMORY_API_KEY). OpenAI now enforces a `model.request` scope on the
    completions endpoint that Admin keys cannot hold (by OpenAI design), so the
    admin key no longer works for these reads. The client resolves the right
    project key by project_id via a name map registered during discovery
    (`fetch_production_projects` calls `register_projects`).
No MCP fallback exists, so a missing token is a hard fail with a clear message.

Key access-pattern facts (verified empirically 2026-06-05):
  * Every request needs an `OpenAI-Project: <proj_id>` HEADER (not a query param).
  * Server-side metadata filters work and combine as AND:
    ?metadata[user]=, ?metadata[name]=, ?metadata[transaction]=,
    ?metadata[conversationId]=, ?metadata[dialogueId]=.
  * Pagination cap is 100. Passing >100 silently returns an EMPTY page — so we
    hard-cap at 100 and paginate via after=<last_id> / has_more / last_id.
  * No server-side time filter. For time-bounded queries we walk newest-first
    and stop when created < since_unix. `created` is unix seconds.
  * A completion can ONLY be retrieved with the CORRECT project header. A bare
    chatcmpl-... id does NOT encode its project — so resolving an id means
    fanning out across the production projects until one returns 200.
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

OPENAI_API = "https://api.openai.com/v1"
DEFAULT_TIMEOUT = 60
MAX_RETRIES = 5

# OpenAI list endpoint hard cap. Passing >100 silently returns an empty page.
PAGE_LIMIT = 100
# Runaway-walk backstop for time-bounded / fan-out walks.
MAX_PAGES = 100_000

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# OpenAI stored-completion ids look like `chatcmpl-<alnum>`.
_CHATCMPL_RE = re.compile(r"chatcmpl-[A-Za-z0-9]+")


class OpenAIError(Exception):
    """Raised for any non-recoverable OpenAI API failure."""


class AuthError(OpenAIError):
    """Raised when OPENAI_ADMIN_API_KEY is missing or rejected."""


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _load_token() -> str:
    token = os.environ.get("OPENAI_ADMIN_API_KEY")
    if not token:
        raise AuthError(
            "OPENAI_ADMIN_API_KEY is not set.\n"
            "It is needed for the /v1/organization/projects discovery call (still\n"
            "valid for org endpoints). Stored-completions reads additionally need\n"
            "a project-scoped key per project (OPENAI_<PROJECT>_API_KEY, PROD-3572).\n"
            "  1. Create an Admin API key (Owner role on the Aura OpenAI org):\n"
            "     https://platform.openai.com/settings/organization/admin-keys\n"
            "  2. Add it to ~/.claude/settings.json under env.OPENAI_ADMIN_API_KEY\n"
            "  3. Restart Claude Code so the env value loads.\n"
            "Cowork users — paste-export the value per session:\n"
            "  export OPENAI_ADMIN_API_KEY='<paste>'"
        )
    return token


def _project_completions_api_key_env(project_name: str) -> str:
    """Derive the project-scoped env var name from a project's display name
    (PROD-3572): "Memory [Production]" -> "OPENAI_MEMORY_API_KEY". Strips a
    trailing "[...]" qualifier and any non-alphanumeric separators."""
    base = re.sub(r"\[.*?\]", "", project_name).strip()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").upper()
    return f"OPENAI_{base}_API_KEY"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OpenAIClient:
    """Thin wrapper over OpenAI's stored chat-completions endpoints.

    All reads go through `_get`, which attaches the `OpenAI-Project` header
    (required — a completion can only be read against its owning project) and
    retries on 429 / 5xx. A 404 is returned to the caller as `None` so the
    project fan-out can try the next project rather than aborting.
    """

    def __init__(self, token: str | None = None) -> None:
        self.token = token or _load_token()  # admin key — /v1/organization/* only
        self.session = requests.Session()
        # project_id -> display name, populated by register_projects (called from
        # fetch_production_projects). Needed to derive the project-scoped
        # completions key (PROD-3572).
        self._project_names: dict[str, str] = {}
        self._project_key_cache: dict[str, str] = {}

    def register_projects(self, id_to_name: dict[str, str]) -> None:
        """Record project_id -> display name so completions reads can resolve
        the right project-scoped API key. Called during discovery."""
        self._project_names.update(id_to_name)

    def _completion_key(self, project_id: str) -> str | None:
        """Resolve the project-scoped stored-completions key for `project_id`
        (PROD-3572). Returns None (with a one-time stderr warning) when the
        project's key isn't set, so a fan-out over projects skips it rather than
        aborting. OPENAI_ADMIN_API_KEY is NOT a fallback — it lacks the
        model.request scope the completions endpoint requires."""
        if project_id in self._project_key_cache:
            return self._project_key_cache[project_id]
        name = self._project_names.get(project_id)
        if not name:
            # Discovery hasn't registered this id — we can't derive the env var
            # name from an opaque proj_ id. Warn and skip.
            print(
                f"WARNING: no display name registered for project {project_id}; "
                "cannot resolve its project-scoped completions key (PROD-3572). "
                "Skipping.",
                file=sys.stderr,
            )
            return None
        env_name = _project_completions_api_key_env(name)
        token = os.environ.get(env_name)
        if not token:
            print(
                f"WARNING: {env_name} not set — skipping stored-completions reads "
                f"for '{name}'. OPENAI_ADMIN_API_KEY no longer works for "
                f"/v1/chat/completions (PROD-3572); create a service-account key "
                f"for this project at https://platform.openai.com/api-keys and set "
                f"{env_name} in ~/.claude/settings.json.",
                file=sys.stderr,
            )
            return None
        self._project_key_cache[project_id] = token
        return token

    def _headers(self, project_id: str | None, token: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {token or self.token}"}
        if project_id:
            h["OpenAI-Project"] = project_id
        return h

    def _get(self, path: str, *, project_id: str | None = None,
             params: dict | None = None, allow_404: bool = False) -> dict | None:
        """GET an OpenAI endpoint with retry on 429/5xx.

        Returns the decoded JSON dict, or None when `allow_404` is set and the
        endpoint returns 404 (wrong/missing project header for a completion id).

        Auth (PROD-3572): calls scoped to a project (`project_id` set — i.e. the
        stored-completions endpoints) authenticate with that project's scoped key
        resolved via `_completion_key`; org-level calls (`project_id is None`)
        use the admin key. When a project's scoped key isn't set, the call is
        treated as a soft miss — returns None if `allow_404` (fan-out skips this
        project), else raises AuthError naming the missing env var.
        """
        token: str | None = None
        if project_id is not None:
            token = self._completion_key(project_id)
            if token is None:
                if allow_404:
                    return None
                name = self._project_names.get(project_id, project_id)
                raise AuthError(
                    f"No project-scoped key available for '{name}' "
                    f"({_project_completions_api_key_env(self._project_names.get(project_id, ''))}"
                    " unset). Stored-completions reads require a project-scoped "
                    "service-account key (OPENAI_ADMIN_API_KEY no longer works — "
                    "PROD-3572). Add it to ~/.claude/settings.json and restart."
                )
        url = f"{OPENAI_API}{path}"
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(url, headers=self._headers(project_id, token),
                                     params=params, timeout=DEFAULT_TIMEOUT)
            except requests.RequestException as e:
                last_exc = e
                time.sleep(2)
                continue
            if r.status_code == 401:
                if project_id is not None:
                    # Project-scoped stored-completions read: the failing key is
                    # this project's OPENAI_<PROJECT>_API_KEY, not the admin key
                    # (PROD-3572). Point remediation at the right key.
                    name = self._project_names.get(project_id, project_id)
                    env_name = _project_completions_api_key_env(
                        self._project_names.get(project_id, "")
                    )
                    raise AuthError(
                        f"OpenAI returned 401 Unauthorized for project '{name}'. The "
                        f"key must be a service-account key scoped to that project "
                        f"(env {env_name}) — OPENAI_ADMIN_API_KEY does NOT work for "
                        "stored-completions reads (PROD-3572). Regenerate at "
                        "https://platform.openai.com/api-keys (Owned by: Service "
                        f"account, Project: {name}), update "
                        f"~/.claude/settings.json env.{env_name}, and restart."
                    )
                raise AuthError(
                    "OpenAI returned 401 Unauthorized on an /v1/organization/* call. "
                    "The key must be an Admin key (sk-admin-...) with Owner role. "
                    "Regenerate at "
                    "https://platform.openai.com/settings/organization/admin-keys, "
                    "update ~/.claude/settings.json env.OPENAI_ADMIN_API_KEY, and restart."
                )
            if r.status_code == 404 and allow_404:
                return None
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", "2"))
                time.sleep(min(wait + 1, 30))
                continue
            if r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise OpenAIError(
                    f"OpenAI returned {r.status_code} on {path}: {r.text[:300]}"
                )
            try:
                return r.json()
            except ValueError as e:
                raise OpenAIError(f"OpenAI returned non-JSON: {e}") from e
        raise OpenAIError(
            f"OpenAI request to {path} failed after {MAX_RETRIES} attempts: {last_exc}"
        )

    # --- list / walk ------------------------------------------------------

    def list_completions(self, project_id: str, *, metadata: dict[str, str] | None = None,
                         since_unix: int | None = None,
                         until_unix: int | None = None,
                         max_pages: int = MAX_PAGES) -> Iterator[dict]:
        """Yield stored completions for one project, newest-first.

        `metadata` becomes server-side `metadata[<k>]=<v>` filters (AND-combined).
        `since_unix` / `until_unix` are applied client-side because the list
        endpoint has no native time filter — the walk stops once it pages past
        `since_unix` (completions come back newest-first).
        """
        after: str | None = None
        page = 0
        while page < max_pages:
            params: dict[str, Any] = {"limit": PAGE_LIMIT}
            for k, v in (metadata or {}).items():
                params[f"metadata[{k}]"] = v
            if after:
                params["after"] = after
            data = self._get("/chat/completions", project_id=project_id, params=params)
            assert data is not None  # /chat/completions list never 404s with a valid project
            items = data.get("data", [])
            if not items:
                break
            page += 1
            stop = False
            for it in items:
                created = it.get("created", 0) or 0
                if until_unix is not None and created >= until_unix:
                    continue
                if since_unix is not None and created < since_unix:
                    stop = True
                    continue
                yield it
            if stop or not data.get("has_more"):
                break
            after = data.get("last_id") or items[-1].get("id")
            if not after:
                break

    # --- single completion ------------------------------------------------

    def retrieve_completion(self, completion_id: str, project_id: str) -> dict | None:
        """GET /chat/completions/{id} against one project. None on 404."""
        return self._get(f"/chat/completions/{completion_id}",
                         project_id=project_id, allow_404=True)

    def fetch_messages(self, completion_id: str, project_id: str) -> list[dict]:
        """Return the input messages for a completion (system + user/assistant/tool).

        GET /chat/completions/{id}/messages, paginated. These are the request
        messages — the system prompt (incl. any `## User-Provided Summary`
        block) lives in the system-role message here.
        """
        out: list[dict] = []
        after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": PAGE_LIMIT}
            if after:
                params["after"] = after
            data = self._get(f"/chat/completions/{completion_id}/messages",
                             project_id=project_id, params=params, allow_404=True)
            if data is None:
                break
            items = data.get("data", [])
            out.extend(items)
            if not data.get("has_more") or not items:
                break
            after = data.get("last_id") or items[-1].get("id")
            if not after:
                break
        return out


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def is_uuid(s: str) -> bool:
    return bool(_UUID_RE.match(s or ""))


def parse_since(since: str) -> int:
    """'4h' -> seconds. Maps a compact duration to a number of seconds.

    Raises ValueError on bad input. Units: s/m/h/d.
    """
    m = re.match(r"^(\d+)([smhd])$", since or "")
    if not m:
        raise ValueError(f"--since must be like 30m, 4h, 2d (got {since!r})")
    n, unit = int(m.group(1)), m.group(2)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return n * mult


def logs_url(completion_id: str) -> str:
    return f"https://platform.openai.com/logs/{completion_id}"


# ---------------------------------------------------------------------------
# OpenAI Logs URL decoder (replaces the Braintrust UI URL decoder)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecodedUrl:
    completion_id: str | None
    legacy_braintrust: bool  # True when a braintrust.dev URL was pasted.


def decode_logs_url(value: str) -> DecodedUrl:
    """Parse an OpenAI Logs URL or a bare chatcmpl id.

    Accepts:
      * https://platform.openai.com/logs/<chatcmpl-id>
      * https://platform.openai.com/logs?... (id anywhere in the URL)
      * a bare `chatcmpl-...` id

    A legacy `braintrust.dev` URL sets `legacy_braintrust=True` so the caller
    can print the migration redirect message instead of guessing.
    """
    v = (value or "").strip()

    parsed = urlparse(v)
    if "braintrust.dev" in parsed.netloc:
        return DecodedUrl(completion_id=None, legacy_braintrust=True)

    # A bare id, or an id embedded anywhere in the URL.
    m = _CHATCMPL_RE.search(v)
    if m:
        return DecodedUrl(completion_id=m.group(0), legacy_braintrust=False)

    return DecodedUrl(completion_id=None, legacy_braintrust=False)
