"""Bugsnag Data Access API v2 client.

Loaded at runtime by investigate.py (and exported for direct use by
sibling scripts in this skill). Token from BUGSNAG_TOKEN env var.

No MCP fallback exists for Bugsnag today, so a missing token is a hard
fail with a clear message — same shape as BRAINTRUST_API_KEY failure.
See ../references/auth-setup.md and AINative/CLAUDE.md "Token inventory".
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import sys
import time
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

import requests

try:
    import fcntl  # POSIX; on Windows the cooldown update simply runs unlocked
except ImportError:  # pragma: no cover
    fcntl = None

API_ROOT = "https://api.bugsnag.com"
DEFAULT_PER_PAGE = 30
MAX_RETRIES = 5
DEFAULT_TIMEOUT = 30

# Every session and skill shares one BUGSNAG_TOKEN, and Bugsnag rate-limits per
# token: on 10-24 it answered 429 for ~20 minutes. So a 429 waits (Retry-After as
# sent, else doubling from 5 s, capped at 120 s) within a budget per request, then
# raises BugsnagRateLimited; and the wait is written to a cooldown file that
# every other process using the token reads first, so they wait too instead of
# adding to the limit.
RATE_LIMIT_BUDGET_S = 300
BACKOFF_START_S = 5.0
BACKOFF_MAX_S = 120.0
MIN_BACKOFF_S = 1.0
MAX_RATE_LIMITED = 30  # 429s per request, whatever the waits add up to
# Lookups that change on the order of days: the org, its projects and each
# project's filterable fields. Cached on disk for a day (BUGSNAG_CACHE=0 turns
# it off); error lists and events are never cached.
CACHE_TTL_S = 24 * 3600
_sleep = time.sleep
_now = time.time

# Field names that read like the obvious way to filter by app version but are
# not fields at all. Bugsnag drops an unknown `filters[...]` key silently and
# answers the unfiltered question with HTTP 200, so these have to be caught
# here or not at all. Canonical write-up: `Tools/Bugsnag` in AI Knowledge.
_FIELD_MYTHS = {
    "app.version": "version.seen_in",
    "event.app_version": "version.seen_in",
    "app.app_version": "version.seen_in",
}

# The two questions a release-scoped query can ask. They are different
# questions and neither is a safe default, so callers name one:
#   seen-in       -> the group has at least one event on that version
#                    (what a monitoring digest wants)
#   introduced-in -> the group first appeared in that version
#                    (what a regression report wants)
RELEASE_MODES = {
    "seen-in": "version.seen_in",
    "introduced-in": "version.introduced_in",
}


class BugsnagError(Exception):
    """Raised for any non-recoverable Bugsnag API failure."""


class BugsnagRateLimited(BugsnagError):
    """Still rate-limited after RATE_LIMIT_BUDGET_S of waiting. Report it as rate-limited."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _state_dir(token: str) -> str | None:
    """The cache and cooldown folder, or None when it can't be made (both are then skipped)."""
    base = os.environ.get("AINATIVE_BUGSNAG_CACHE_DIR") or os.path.expanduser("~/.cache/aura-bugsnag")
    d = os.path.join(base, hashlib.sha256(token.encode()).hexdigest()[:12])
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
    except OSError:
        return None
    return d


def _write_private(path: str, text: str) -> None:
    tmp = f"{path}.{os.getpid()}.part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _backoff_seconds(headers: Any, attempt: int) -> float:
    """Retry-After as sent; without one, 5 s doubling per attempt, capped at 120 s.

    Bugsnag's own Retry-After is never shortened: it goes into the shared
    cooldown, and cutting it would send every other process back early.
    """
    fallback = min(BACKOFF_START_S * (2 ** attempt), BACKOFF_MAX_S)
    # A Retry-After of 0 (or less) is legal; a floor keeps it from busy-looping.
    return max(_retry_after_seconds(headers, default=fallback), MIN_BACKOFF_S)


def _load_token() -> str:
    token = os.environ.get("BUGSNAG_TOKEN")
    if not token:
        sys.stderr.write(
            "BUGSNAG_TOKEN is not set.\n"
            "Bugsnag has no MCP fallback today, so this is a hard fail.\n"
            "  1. Generate a personal auth token at:\n"
            "     https://app.bugsnag.com/settings/my-account/auth-tokens\n"
            "  2. Add it to ~/.claude/settings.json under env.BUGSNAG_TOKEN\n"
            "  3. Restart Claude Code so the env value loads.\n"
        )
        raise BugsnagError("BUGSNAG_TOKEN missing")
    return token


class BugsnagClient:
    """Thin wrapper over Bugsnag's Data Access API v2.

    Handles auth, the mandatory `X-Version: 2` header, pagination via
    `Link: rel="next"`, and 429 backoff with `Retry-After`.
    """

    def __init__(self, token: str | None = None) -> None:
        self.token = token or _load_token()
        self._event_fields: dict[str, list[dict[str, Any]]] = {}
        self._state = _state_dir(self.token)
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {self.token}",
            "X-Version": "2",
            "Accept": "application/json",
        })

    # --- rate limit: shared cooldown + backoff ------------------------

    def _cooldown_path(self) -> str:
        return os.path.join(self._state or "", "cooldown-until")

    def _cooldown_until(self) -> float:
        if self._state is None:
            return 0.0
        try:
            with open(self._cooldown_path(), encoding="utf-8") as f:
                return float(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0.0

    def _wait_out(self, until: float, deadline: float, url: str) -> None:
        """Sleep until `until`, or raise BugsnagRateLimited when that passes the budget."""
        now = _now()
        if until <= now:
            return
        if until > deadline:
            raise BugsnagRateLimited(
                f"Bugsnag rate limit: the shared BUGSNAG_TOKEN is limited for another "
                f"{int(until - now)} s ({url}). Report this run as rate-limited, not failed.",
                retry_after=until - now)
        _sleep(until - now)

    def _on_429(self, r: Any, attempt: int, deadline: float, url: str) -> None:
        wait = _backoff_seconds(r.headers, attempt)
        until = _now() + wait
        try:
            self._extend_cooldown(until)
        except OSError:
            pass  # the cooldown is a courtesy to other processes; never fail on it
        self._wait_out(until, deadline, url)

    def _extend_cooldown(self, until: float) -> None:
        """Move the shared cooldown later, never earlier, under a lock.

        Without the lock two processes can both read the old value, and the one
        with the shorter wait writes last, sending everyone back early.
        """
        if self._state is None:
            return
        lock_path = os.path.join(self._state, "cooldown.lock")
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            if until > self._cooldown_until():
                _write_private(self._cooldown_path(), f"{until}\n")
        finally:
            os.close(fd)  # closing releases the lock

    # --- on-disk cache for slow-changing lookups ----------------------

    def _cached(self, key: str, fetch: Any) -> Any:
        if os.environ.get("BUGSNAG_CACHE", "1") == "0" or self._state is None:
            return fetch()
        path = os.path.join(self._state, hashlib.sha256(key.encode()).hexdigest()[:16] + ".json")
        try:
            if _now() - os.path.getmtime(path) < CACHE_TTL_S:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, ValueError):
            pass
        value = fetch()
        try:
            _write_private(path, json.dumps(value))
        except OSError:
            pass
        return value

    # --- low-level GET with retry on 429 -----------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        url = path if path.startswith("http") else f"{API_ROOT}{path}"
        last_exc: Exception | None = None
        deadline = _now() + RATE_LIMIT_BUDGET_S
        failures = limited = 0
        while failures < MAX_RETRIES:
            self._wait_out(self._cooldown_until(), deadline, url)
            try:
                r = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            except requests.RequestException as e:
                last_exc = e
                failures += 1
                _sleep(2)
                continue
            if r.status_code == 429:
                if limited >= MAX_RATE_LIMITED:
                    raise BugsnagRateLimited(
                        f"Bugsnag rate limit: {limited} 429s for {url}. Report this run as "
                        f"rate-limited, not failed.", retry_after=_backoff_seconds(r.headers, limited))
                self._on_429(r, limited, deadline, url)
                limited += 1
                continue
            if r.status_code == 401:
                raise BugsnagError(
                    "Bugsnag returned 401 Unauthorized. "
                    "Check BUGSNAG_TOKEN — it may be expired, revoked, or wrong."
                )
            if r.status_code == 404:
                raise BugsnagError(f"Bugsnag 404 (not found): {url}")
            try:
                r.raise_for_status()
            except requests.HTTPError as e:
                # Wrap 5xx/403/etc as BugsnagError so a fan-out over projects
                # (which catches BugsnagError) degrades gracefully instead of
                # crashing on one bad project response.
                raise BugsnagError(f"Bugsnag HTTP {r.status_code} for {url}") from e
            return r
        raise BugsnagError(f"Network failure after {MAX_RETRIES} retries: {last_exc}")

    # --- low-level write (PATCH / POST) with retry on 429 ------------

    def _write(self, method: str, path: str,
               body: dict[str, Any]) -> requests.Response:
        """PATCH/POST helper mirroring _get's auth + 429/401/404 handling.

        Used only by the explicit mutation methods below — every caller is
        gated behind the skill's dry-run/--confirm flow, so this is never
        reached without an intentional, confirmed write.
        """
        url = f"{API_ROOT}{path}"
        last_exc: Exception | None = None
        deadline = _now() + RATE_LIMIT_BUDGET_S
        failures = limited = 0
        while failures < MAX_RETRIES:
            self._wait_out(self._cooldown_until(), deadline, url)
            try:
                r = self.session.request(method, url, json=body,
                                         timeout=DEFAULT_TIMEOUT)
            except requests.RequestException as e:
                last_exc = e
                failures += 1
                _sleep(2)
                continue
            if r.status_code == 429:
                if limited >= MAX_RATE_LIMITED:
                    raise BugsnagRateLimited(
                        f"Bugsnag rate limit: {limited} 429s for {url}. Report this run as "
                        f"rate-limited, not failed.", retry_after=_backoff_seconds(r.headers, limited))
                self._on_429(r, limited, deadline, url)
                limited += 1
                continue
            if r.status_code == 401:
                raise BugsnagError(
                    "Bugsnag returned 401 Unauthorized. Check BUGSNAG_TOKEN — "
                    "it may lack write scope, or be expired/revoked/wrong."
                )
            if r.status_code == 403:
                raise BugsnagError(
                    "Bugsnag returned 403 Forbidden — the token cannot write to "
                    "this project. Use a token with error-update permission."
                )
            if r.status_code == 404:
                raise BugsnagError(f"Bugsnag 404 (not found): {url}")
            try:
                r.raise_for_status()
            except requests.HTTPError as e:
                # Surface 5xx/other non-2xx as BugsnagError so callers that
                # catch BugsnagError get a clean message, not a raw HTTPError.
                raise BugsnagError(f"Bugsnag HTTP {r.status_code} for {url}") from e
            return r
        raise BugsnagError(f"Network failure after {MAX_RETRIES} retries: {last_exc}")

    # --- pagination helper -------------------------------------------

    def _paginate(self, path: str, params: dict[str, Any] | None = None,
                  max_pages: int = 10) -> Iterator[dict[str, Any]]:
        """Yield items across pages. Bugsnag uses `Link: <url>; rel="next"`."""
        params = dict(params or {})
        params.setdefault("per_page", DEFAULT_PER_PAGE)
        next_url: str | None = path
        next_params: dict[str, Any] | None = params
        page = 0
        while next_url and page < max_pages:
            r = self._get(next_url, params=next_params)
            payload = r.json()
            if isinstance(payload, list):
                for item in payload:
                    yield item
            else:
                # Some endpoints return a single object — yield once.
                yield payload
                return
            next_url = _parse_next_link(r.headers.get("Link", ""))
            next_params = None  # the next URL is fully formed
            page += 1

    # --- public methods ----------------------------------------------

    def list_organizations(self) -> list[dict[str, Any]]:
        return self._cached("orgs", lambda: self._get("/user/organizations").json())

    def find_org_id(self, slug: str = "aura-health") -> str:
        for org in self.list_organizations():
            if org.get("slug") == slug:
                return org["id"]
        raise BugsnagError(
            f"Bugsnag org `{slug}` not found in your account. "
            "Check your token has access to the right organization."
        )

    def list_projects(self, org_id: str) -> list[dict[str, Any]]:
        return self._cached(f"projects:{org_id}", lambda: list(self._paginate(
            f"/organizations/{org_id}/projects",
            params={"per_page": 100},
            max_pages=2,
        )))

    # --- filter validation -------------------------------------------
    #
    # Bugsnag ignores an unknown `filters[...]` key rather than rejecting it:
    # the request returns 200, the body is well-formed, and the answer is the
    # *unfiltered* one. A misspelled field therefore widens the query with
    # nothing to notice. Every filter this client sends goes through
    # `build_filters` first, which checks the key against the project's own
    # `event_fields` and raises instead. Field semantics, the version fields,
    # and the repeated-pair behaviour are documented on `Tools/Bugsnag` in
    # AI Knowledge — the canonical home; they are deliberately not restated here.

    def event_fields(self, project_id: str) -> list[dict[str, Any]]:
        """Raw `event_fields` metadata for a project, cached per client.

        This is the only authoritative list of what is filterable — the UI's
        column names and the docs' prose both include things that are not
        fields.
        """
        if project_id not in self._event_fields:
            self._event_fields[project_id] = self._cached(
                f"event_fields:{project_id}",
                lambda: self._get(f"/projects/{project_id}/event_fields").json())
        return self._event_fields[project_id]

    def filter_fields(self, project_id: str) -> dict[str, list[str]]:
        """Filterable field name -> its supported match types.

        A field with `filter_options` but no `match_types` takes no comparison
        operator, which is what distinguishes the two wire shapes below.
        """
        out: dict[str, list[str]] = {}
        for field in self.event_fields(project_id):
            opts = field.get("filter_options")
            name = field.get("display_id")
            if isinstance(opts, dict) and name:
                out[name] = list(opts.get("match_types") or [])
        return out

    def validate_filter(self, project_id: str, key: str,
                        match_type: str | None = None) -> None:
        """Raise unless `key` is a filterable field accepting `match_type`."""
        known = self.filter_fields(project_id)
        if key not in known:
            hint = _FIELD_MYTHS.get(key)
            if hint and hint in known:
                suggest = f"Did you mean {hint!r}?"
            else:
                close = difflib.get_close_matches(key, sorted(known), n=3)
                suggest = f"Closest fields: {', '.join(close)}." if close else ""
            raise BugsnagError(
                f"{key!r} is not a filterable Bugsnag field on project "
                f"{project_id}. Bugsnag would ignore it silently and return "
                f"the UNFILTERED result set with HTTP 200. " + suggest +
                " Authoritative list: GET /projects/<id>/event_fields."
            )
        supported = known[key]
        if match_type is not None and supported and match_type not in supported:
            raise BugsnagError(
                f"Field {key!r} does not support match type {match_type!r} "
                f"(supported: {', '.join(supported)}). Bugsnag returns HTTP 400 "
                "for an unsupported comparison; a range has to be expanded into "
                "one query per value and unioned client-side."
            )

    def build_filters(self, project_id: str,
                      filters: dict[str, Any]) -> dict[str, Any]:
        """Validate `{field: value}` and render it as query params.

        Value forms:
          * a scalar -> `eq` (or the flat shape for a field with no match types)
          * `{"type": <match_type>, "value": <v>}` -> that comparison

        A list/tuple of values is refused. Repeating a filter pair does not OR
        the values — Bugsnag keeps the last one and returns 200, so a
        multi-value ask has to be N requests unioned by the caller.
        """
        known = self.filter_fields(project_id)
        params: dict[str, Any] = {}
        for key, raw in filters.items():
            if isinstance(raw, dict):
                match_type, value = raw.get("type"), raw.get("value")
            else:
                match_type, value = None, raw
            # Checked on the *resolved* value, so the typed form
            # {"type": ..., "value": [...]} cannot slip a list through. Left on
            # the bare value alone, `requests` would repeat the key and Bugsnag
            # would keep the last one — the silent failure this guard exists for.
            if isinstance(value, (list, tuple, set)):
                raise BugsnagError(
                    f"Filter {key!r} was given {len(value)} values. Repeated "
                    "filter pairs do not OR in Bugsnag — the last one wins and "
                    "the response looks legitimate. Issue one request per value "
                    "and union the results client-side."
                )
            self.validate_filter(project_id, key, match_type)
            if not known[key]:
                # No match types -> flat `filters[<field>]=<value>` shape.
                # `event.since` and `search` are the two in practice; the array
                # shape returns 400 for them.
                if match_type is not None:
                    raise BugsnagError(
                        f"Field {key!r} takes no match type — pass a bare value."
                    )
                params[f"filters[{key}]"] = value
            else:
                params[f"filters[{key}][][type]"] = match_type or "eq"
                params[f"filters[{key}][][value]"] = value
        return params

    def list_errors(self, project_id: str, *,
                    since: str | None = None,
                    user_id: str | None = None,
                    filters: dict[str, Any] | None = None,
                    sort: str = "last_seen",
                    per_page: int = DEFAULT_PER_PAGE,
                    max_results: int = DEFAULT_PER_PAGE) -> list[dict[str, Any]]:
        """Error signatures for a project.

        `since` and `user_id` are the two common filters; `filters` takes any
        other field as `{field: value}` or `{field: {"type": ..., "value": ...}}`.
        Every key is validated against the project's `event_fields` before the
        request goes out — see `build_filters`.
        """
        params: dict[str, Any] = {"sort": sort, "per_page": per_page}
        wanted: dict[str, Any] = dict(filters or {})
        if since:
            wanted["event.since"] = since
        if user_id:
            wanted["user.id"] = user_id
        if wanted:
            params.update(self.build_filters(project_id, wanted))
        out: list[dict[str, Any]] = []
        for item in self._paginate(f"/projects/{project_id}/errors", params,
                                   max_pages=10):
            out.append(item)
            if len(out) >= max_results:
                break
        return out

    def list_errors_for_releases(
            self, project_id: str, versions: list[str], *,
            mode: str = "seen-in",
            since: str | None = None,
            user_id: str | None = None,
            sort: str = "last_seen",
            per_page: int = DEFAULT_PER_PAGE,
            max_results: int = DEFAULT_PER_PAGE) -> list[dict[str, Any]]:
        """Errors scoped to one or more app versions, unioned client-side.

        Bugsnag offers no OR and no range on the version fields — `gte` is a
        400, and repeating the filter pair keeps only the last value — so a
        multi-version ask is N requests deduped by error id here.

        **How the union accounts for counts**, since Bugsnag scopes them per
        version and the two figures do not combine the same way:

        * `events` is **summed**. An event happens on exactly one version, so
          the per-version counts partition cleanly.
        * `users` is the **maximum** across versions, and the row is flagged
          `users_is_lower_bound` when more than one version contributed. One
          person hitting two versions is one user, and separate queries give
          no way to tell that from two people — summing would overstate. The
          flag exists so a renderer can say so rather than imply precision.

        Rows also carry `release_versions` (which versions returned them) and
        `events_by_version`, so a caller can report per-version instead.
        """
        field = RELEASE_MODES.get(mode)
        if field is None:
            raise BugsnagError(
                f"Unknown release mode {mode!r}. "
                f"Choose one of: {', '.join(sorted(RELEASE_MODES))}. "
                "'seen-in' asks whether the error occurred on that version; "
                "'introduced-in' asks whether it first appeared there."
            )
        if not versions:
            raise BugsnagError("list_errors_for_releases needs at least one version.")

        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        seen_versions: set[str] = set()
        for version in versions:
            # A version queried twice would double its own event counts in the
            # union — the releases endpoint can report one app_version across
            # several release records.
            if version in seen_versions:
                continue
            seen_versions.add(version)
            rows = self.list_errors(
                project_id, since=since, user_id=user_id,
                filters={field: version},
                sort=sort, per_page=per_page, max_results=max_results,
            )
            for row in rows:
                key = row.get("id") or f"{row.get('error_class')}::{row.get('context')}"
                events = _int(row.get("events"))
                users = _int(row.get("users"))
                if key not in merged:
                    row = dict(row)
                    row["release_versions"] = [version]
                    row["events_by_version"] = {version: events}
                    row["users_by_version"] = {version: users}
                    row["users_is_lower_bound"] = False
                    merged[key] = row
                    order.append(key)
                    continue
                prev = merged[key]
                prev["release_versions"].append(version)
                prev["events_by_version"][version] = events
                prev["users_by_version"][version] = users
                prev["events"] = _int(prev.get("events")) + events
                prev["users"] = max(_int(prev.get("users")), users)
                prev["users_is_lower_bound"] = True
                prev["first_seen"] = _earlier(prev.get("first_seen"),
                                              row.get("first_seen"))
                prev["last_seen"] = _later(prev.get("last_seen"),
                                           row.get("last_seen"))
        out = [merged[k] for k in order]
        out.sort(key=lambda r: _int(r.get("events")), reverse=True)
        return out[:max_results] if max_results else out

    def list_releases(self, project_id: str, *,
                      per_page: int = 10,
                      max_results: int = 30) -> list[dict[str, Any]]:
        """Releases for a project, newest first.

        Each record carries app_version, release_time, release_stage,
        errors_introduced_count, and errors_seen_count. Session/stability
        fields (total_sessions_count, accumulative_daily_users_*) are present
        in the shape but null unless the SDK reports sessions — which the
        aura-health projects don't today, so callers rely on the error counts.
        """
        # Page budget is derived from what the caller asked for rather than
        # fixed, so `max_results` cannot quietly exceed the walk: at the
        # endpoint's per_page cap of 10, a hardcoded 10 pages silently stopped
        # at 100 records, and a version past that read as a version that does
        # not exist.
        max_pages = max(1, -(-max_results // max(1, per_page)))
        out: list[dict[str, Any]] = []
        for item in self._paginate(f"/projects/{project_id}/releases",
                                   params={"per_page": per_page},
                                   max_pages=max_pages):
            out.append(item)
            if len(out) >= max_results:
                break
        return out

    def get_error(self, error_id: str) -> dict[str, Any]:
        return self._get(f"/errors/{error_id}").json()

    # --- mutations (write) -------------------------------------------

    # Operations accepted by PATCH /errors/<id>. The skill exposes the three
    # everyday ones; snooze/discard are intentionally left out of the MVP.
    OPERATIONS = ("fix", "open", "ignore")

    def update_error(self, error_id: str, operation: str) -> dict[str, Any]:
        """Apply an operation to one error: fix (resolve), open (reopen), ignore.

        Maps to PATCH /errors/<id> with {"operation": <op>}. Callers must gate
        this behind explicit confirmation — it mutates production Bugsnag state.
        """
        if operation not in self.OPERATIONS:
            raise BugsnagError(
                f"Unsupported operation {operation!r}. "
                f"Supported: {', '.join(self.OPERATIONS)}."
            )
        r = self._write("PATCH", f"/errors/{error_id}", {"operation": operation})
        try:
            return r.json()
        except ValueError:
            return {}

    def add_comment(self, error_id: str, message: str) -> dict[str, Any]:
        """Add a comment to an error. Maps to POST /errors/<id>/comments."""
        r = self._write("POST", f"/errors/{error_id}/comments", {"message": message})
        try:
            return r.json()
        except ValueError:
            return {}

    def list_events(self, error_id: str, per_page: int = 5) -> list[dict[str, Any]]:
        return list(self._paginate(
            f"/errors/{error_id}/events",
            params={"per_page": per_page},
            max_pages=1,
        ))

    def get_event(self, project_id: str, event_id: str) -> dict[str, Any]:
        return self._get(f"/projects/{project_id}/events/{event_id}").json()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _earlier(a: str | None, b: str | None) -> str | None:
    """Min of two ISO-8601 stamps, tolerating None. Lexical order is safe here —
    Bugsnag emits a fixed-width UTC form with a trailing Z."""
    return min([v for v in (a, b) if v], default=None)


def _later(a: str | None, b: str | None) -> str | None:
    return max([v for v in (a, b) if v], default=None)


def _retry_after_seconds(headers: Any, default: float = 5.0) -> float:
    """Parse a `Retry-After` header into seconds.

    Uses float() — Bugsnag can send fractional values (e.g. "5.301") that
    int() would choke on. Falls back to `default` for an absent or non-numeric
    value (e.g. the HTTP-date form), so the retry loop never crashes here.
    """
    try:
        return float(headers.get("Retry-After", default))
    except (TypeError, ValueError):
        return default


def _parse_next_link(link_header: str) -> str | None:
    """Parse a `Link: <url>; rel="next"` header. Returns None if no next page."""
    if not link_header:
        return None
    for part in link_header.split(","):
        seg = part.strip()
        if 'rel="next"' in seg:
            l, r = seg.find("<"), seg.find(">")
            if l != -1 and r != -1:
                return seg[l + 1:r]
    return None


# --- URL parsing -----------------------------------------------------

def parse_bugsnag_url(url: str) -> dict[str, str | None]:
    """Parse a Bugsnag UI URL and extract org slug, project slug, error id, event id.

    Supported shapes:
      https://app.bugsnag.com/<org>/<project>/errors/<error-id>?event_id=<id>
      https://app.bugsnag.com/<org>/<project>/errors
      https://app.bugsnag.com/settings/<org>/projects

    Raises ValueError if the host is not Bugsnag.
    """
    p = urlparse(url)
    if p.netloc not in ("app.bugsnag.com", "bugsnag.com"):
        raise ValueError(f"Not a Bugsnag URL: {url}")
    parts = [seg for seg in p.path.split("/") if seg]
    out: dict[str, str | None] = {
        "org": None, "project": None, "error_id": None, "event_id": None,
    }
    if not parts:
        return out
    if parts[0] == "settings":
        out["org"] = parts[1] if len(parts) > 1 else None
        return out
    out["org"] = parts[0]
    if len(parts) >= 2:
        out["project"] = parts[1]
    if len(parts) >= 4 and parts[2] == "errors":
        out["error_id"] = parts[3]
    qs = parse_qs(p.query)
    if "event_id" in qs:
        out["event_id"] = qs["event_id"][0]
    return out
