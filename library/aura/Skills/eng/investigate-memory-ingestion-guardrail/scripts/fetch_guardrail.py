"""Fetch and parse one memory-ingestion guardrail trace from OpenAI stored completions.

Migrated off Braintrust 2026-06-05 (PROD-2463, umbrella PROD-2456). Reads OpenAI's
`Memory [Production]` project via the stored chat-completions endpoints. Access
pattern documented in AI Knowledge `Tools/OpenAI` -> "Reading stored completions
(BT migration access pattern)".

Auth: `OPENAI_MEMORY_API_KEY`, a service-account key scoped to the Memory
[Production] project — NOT `OPENAI_ADMIN_API_KEY`. OpenAI enforces a
`model.request` scope on `GET /v1/chat/completions` that Admin keys cannot
hold, so a project-scoped key is required per project (PROD-3572).

The memory-ingest guardrail is the `memory-ingest-prompter` call: a single
LLM completion whose JSON output carries `{memories, seriousHarmFlagSet,
seriousHarmEvidence}`. We locate it by server-side metadata filter:

    GET /v1/chat/completions?metadata[transaction]=<uuid>&metadata[name]=memory-ingest-prompter

The metadata filter is a case-sensitive exact match; the transaction id is
tried as-given, lower-, and upper-cased so a cosmetic case difference between
the alert and the stored value doesn't force a needless Braintrust fallback.

The guardrail outcome is `choices[0].message.content` (JSON). The user's pasted
text lives in the system message under a `## User-Provided Summary` section
(fetched via the /messages endpoint) — in the Braintrust era this was read from
`metadata.contextInstructions`, which OpenAI does not carry; the same content is
present in the system prompt instead (contextInstructions is concatenated into
the system message at request time).

Identity enrichment: the report also carries an **Aura user record** section
fetched from `GET /users/:userId` (givenName / familyName / email / countryCode
/ timezone). This is the PRIMARY source for the account holder's first name,
country, and CS email address; the system-prompt profile block and pasted
summary are secondary fallbacks used only when a field is blank. The lookup is
best-effort — a missing UID, a 404, or an error degrades cleanly to those
fallbacks and never blocks the readout.

Output: a markdown report on stdout with structured fields ready for the LLM
to synthesise. Errors and the underlying queries go to stderr. This report is
analysis-only — the pasted summary and user profile it contains are read to
confirm the flag and detect country/name, and must NOT be reposted to Slack.
Slack replies carry only the category + the non-graphic `seriousHarmEvidence`
phrase, plus — on the active-risk draft path only — the account holder's email
(from the Aura record) so the reviewer can send the CS draft without a further
lookup (see SKILL.md Step 6).

**Braintrust fallback.** OpenAI stored-completions is the source of truth post-
migration, but it has been observed to silently drop a transaction (e.g. the
memory-ingest call fired and the guardrail tripped, yet no completion was
persisted — confirmed 2026-06-23 for one transaction). Braintrust's
`Memory [Production]` project is still logging the same spans in parallel, so
when OpenAI returns 0 matches we fall back to a Braintrust BTQL query before
giving up. The fallback needs `BRAINTRUST_API_KEY`; if it's absent we degrade to
the original 0-match message. The BTQL endpoint is `https://api.braintrust.dev/btql`
(no `/v1` — that path returns the web app's 404 HTML).

Bundled with the `investigate-memory-ingestion-guardrail` skill; not shared
with siblings (folder isolation per AINative Architecture principle 1).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
import pathlib
from typing import Any

import requests


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPENAI_API = "https://api.openai.com/v1"

# Aura backend — GET /users/:userId returns the full Postgres user record
# (givenName / familyName / email / countryCode / timezone, among ~108 fields).
# This is the PRIMARY source for the account holder's first name, country, and
# the CS email address; the system-prompt profile block and pasted summary are
# secondary fallbacks used only when a field is blank here. The endpoint is
# UNAUTHENTICATED on prod today (registered without the `authenticate`
# middleware — see AI Knowledge `Tools/Aura API/Users/Get User By Id`), so it
# works for any UID and does NOT depend on AURA_API_TOKEN being present or
# broadly scoped. We still send the token for parity when it's set.
AURA_BASE = "https://services.aurahealth.io"
# resource_map.md keys its tables US/CA/UK/AU/NZ/IE. ISO-3166 alpha-2 matches
# all of them except the UK, whose ISO code is GB. Map only the mapped-country
# set; any other code passes through as-is and takes the country-not-in-map path.
_COUNTRY_CODE_TO_KEY = {
    "US": "US", "CA": "CA", "GB": "UK", "UK": "UK",
    "AU": "AU", "NZ": "NZ", "IE": "IE",
}

# Memory [Production] — the OpenAI project that stores memory-prompter family
# calls. See AI Knowledge `Tools/OpenAI` -> project inventory.
MEMORY_PROJECT_ID = "proj_f0PpUSyYnJNifUZj44FrjUhr"
MEMORY_PROJECT_NAME = "Memory [Production]"
# The prompter whose output is the memory-ingest guardrail envelope.
INGEST_PROMPTER = "memory-ingest-prompter"

# Braintrust fallback — the pre-migration logging path, still running in
# parallel. Endpoint is the bare /btql (NOT /v1/btql, which 404s to the web app).
BRAINTRUST_BTQL = "https://api.braintrust.dev/btql"
# Braintrust `Memory [Production]` project id (project_logs source).
BRAINTRUST_MEMORY_PROJECT_ID = "16a73852-9f3e-4c65-9427-5f4e3fc38d8e"

PAGE_LIMIT = 100
# How many pages to walk while collecting transaction matches before giving up.
# The server-side metadata filter makes the match set tiny, so this is a safety
# bound, not a performance lever.
MAX_PAGES = 20
DEFAULT_TIMEOUT = 60
MAX_RETRIES = 5

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# Matches any markdown heading of level 2 or deeper. Captures (hashes, title).
# We don't pin to a specific heading level because the memoryIngest prompt has
# churned between `### User-Provided Summary` (older revs) and `## User-Provided
# Summary` (5/28+ revs) — see ENGPROM-58. Section bounds are decided dynamically
# from whatever level the matched start heading sits at.
_HEADING_RE = re.compile(r"^(#{2,})\s+(.+?)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GuardrailFetchError(Exception):
    """Raised for any non-recoverable failure."""


class AuthError(GuardrailFetchError):
    """Missing or rejected OPENAI_MEMORY_API_KEY."""


class NotFoundError(GuardrailFetchError):
    """A stored object doesn't exist at OpenAI (404). Distinct from an outage:
    for a by-id read a 404 means the object is genuinely absent (e.g. past the
    retention horizon), not that OpenAI is unreachable."""


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------


def _load_token() -> str:
    token = os.environ.get("OPENAI_MEMORY_API_KEY")
    if not token:
        raise AuthError(
            "OPENAI_MEMORY_API_KEY is not set.\n"
            "This skill reads stored completions from the Memory [Production]\n"
            "project; OpenAI now enforces a `model.request` scope on\n"
            "GET /v1/chat/completions that Admin keys (OPENAI_ADMIN_API_KEY) cannot\n"
            "hold, so a project-scoped key is required (PROD-3572). There is no MCP\n"
            "fallback, so this is a hard fail.\n"
            "  1. Create a service-account API key scoped to the Memory [Production]\n"
            "     project: https://platform.openai.com/api-keys\n"
            "     (Create new secret key -> Owned by: Service account -> Project:\n"
            "     Memory [Production])\n"
            "  2. Add it to ~/.claude/settings.json under env.OPENAI_MEMORY_API_KEY\n"
            "  3. Restart Claude Code so the env value loads.\n"
            "Cowork users — paste-export the value per session:\n"
            "  export OPENAI_MEMORY_API_KEY='<paste>'"
        )
    return token


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "OpenAI-Project": MEMORY_PROJECT_ID,
    }


def _get(token: str, path: str, params: dict | None = None) -> dict:
    """GET an OpenAI endpoint with retry on 429/5xx."""
    url = f"{OPENAI_API}{path}"
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=_headers(token), params=params,
                             timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as e:
            last_exc = e
            time.sleep(2)
            continue
        if r.status_code == 401:
            raise AuthError(
                "OpenAI returned 401 Unauthorized. The key must be a service-account "
                "key (sk-svcacct-... / sk-proj-...) scoped to the Memory [Production] "
                "project — Admin keys lack the model.request scope this endpoint now "
                "requires (PROD-3572). Regenerate at "
                "https://platform.openai.com/api-keys, "
                "update ~/.claude/settings.json env.OPENAI_MEMORY_API_KEY, and restart."
            )
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", "2"))
            time.sleep(min(wait, 10))
            continue
        if r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 404:
            # Authoritative absence, not an outage — a by-id read of an object
            # that isn't stored (e.g. past retention). Callers distinguish this
            # from a transport/5xx failure.
            raise NotFoundError(f"OpenAI returned 404 on {path} (object not found).")
        if r.status_code != 200:
            raise GuardrailFetchError(
                f"OpenAI returned {r.status_code} on {path}: {r.text[:300]}"
            )
        try:
            return r.json()
        except json.JSONDecodeError as e:
            raise GuardrailFetchError(f"OpenAI returned non-JSON: {e}") from e
    raise GuardrailFetchError(
        f"OpenAI request to {path} failed after {MAX_RETRIES} attempts: {last_exc}"
    )


def _walk_ingest_page(token: str, transaction: str) -> list[dict]:
    """Page the metadata-filtered completions for one exact transaction string."""
    matches: list[dict] = []
    after: str | None = None
    for _ in range(MAX_PAGES):
        params = {
            "metadata[transaction]": transaction,
            "metadata[name]": INGEST_PROMPTER,
            "limit": PAGE_LIMIT,
        }
        if after:
            params["after"] = after
        data = _get(token, "/chat/completions", params)
        items = data.get("data", [])
        matches.extend(items)
        if not data.get("has_more") or not items:
            break
        after = data.get("last_id") or items[-1].get("id")
        if not after:
            break
    return matches


def fetch_ingest_completions(token: str, transaction: str) -> list[dict]:
    """Return memory-ingest completions for one transaction, newest-first.

    Uses the server-side metadata filter (verified 2026-06-05 — see
    `Tools/OpenAI` -> "Metadata field reference"): both `metadata[transaction]`
    and `metadata[name]` filter server-side and combine as AND. The match set
    for one transaction is tiny (memory-ingest fires once per ingestion), so
    this rarely walks more than one page.

    OpenAI's metadata filter is a case-sensitive exact match, and the alert's
    transaction id (x-request-id) is not guaranteed to be the same case as the
    stored `metadata.transaction`. We try the id as-given, then lower-, then
    upper-cased and return the first non-empty set — same dual-case guard the
    Braintrust fallback already uses. Prevents a silent 0-match that would
    otherwise fall through to Braintrust for a purely cosmetic case difference
    (ENG-910).
    """
    tried: set[str] = set()
    for tx in (transaction, transaction.lower(), transaction.upper()):
        if tx in tried:
            continue
        tried.add(tx)
        matches = _walk_ingest_page(token, tx)
        if matches:
            break
    else:
        return []
    matches.sort(key=lambda c: c.get("created") or 0, reverse=True)
    return matches


def fetch_system_message(token: str, completion_id: str) -> str:
    """Return the concatenated system-prompt text for a completion.

    memory-ingest is a single-shot call: /messages returns one system message
    carrying the full prompt with the user's pasted summary appended at the
    tail (the `## User-Provided Summary` section). We concatenate any
    system-role messages defensively in case a future rev splits them.
    """
    parts: list[str] = []
    after: str | None = None
    while True:
        params = {"limit": PAGE_LIMIT}
        if after:
            params["after"] = after
        data = _get(token, f"/chat/completions/{completion_id}/messages", params)
        items = data.get("data", [])
        for m in items:
            if m.get("role") == "system":
                t = _msg_text(m)
                if t:
                    parts.append(t)
        if not data.get("has_more") or not items:
            break
        after = items[-1].get("id")
        if not after:
            break
    return "\n\n".join(parts)


def fetch_aura_user(uid: str | None) -> dict | None:
    """Fetch the Aura Postgres user record for `uid` — the PRIMARY source for
    the account holder's first name, country, and the CS email address.

    `GET /users/:userId` is unauthenticated on prod today (no auth middleware —
    see AI Knowledge `Tools/Aura API/Users/Get User By Id`), so it resolves any
    UID and does not require AURA_API_TOKEN. We still send the bearer token for
    parity when it's set.

    Returns a dict with a `_status` key plus the fields we use, or None when no
    UID is available to query. On any non-200 (404 USER_NOT_FOUND, transient
    error, non-JSON) it returns `{"_status": <reason>}` with no fields — the
    caller degrades to the profile block / pasted summary for name + country and
    has no email to surface. Never raises: identity enrichment is best-effort and
    must not block the guardrail readout.
    """
    if not uid:
        return None
    token = os.environ.get("AURA_API_TOKEN", "")
    headers = {"User-Agent": user_agent()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(f"{AURA_BASE}/users/{uid}", headers=headers,
                         timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        return {"_status": f"request error: {e}"}
    if r.status_code == 404:
        return {"_status": "not_found"}
    if r.status_code != 200:
        return {"_status": f"http {r.status_code}"}
    try:
        d = r.json()
    except json.JSONDecodeError:
        return {"_status": "non-JSON response"}
    return {
        "_status": "ok",
        "givenName": d.get("givenName"),
        "familyName": d.get("familyName"),
        "email": d.get("email"),
        "countryCode": d.get("countryCode"),
        "timezone": d.get("timezone"),
    }


def _msg_text(msg: dict) -> str:
    """Extract plain text from a /messages entry (string or content-part list)."""
    content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for p in content:
            if isinstance(p, dict):
                t = p.get("text") or p.get("content")
                if t:
                    out.append(t)
        return "\n".join(out)
    return ""


# ---------------------------------------------------------------------------
# Braintrust fallback
# ---------------------------------------------------------------------------


def _iso_to_unix(value: Any) -> Any:
    """Convert a Braintrust ISO-8601 `created` (e.g. '2026-06-23T18:01:15.165Z')
    to a unix int so the report's `(unix)` label stays accurate. Returns the
    original value unchanged if it isn't a parseable ISO string."""
    if not isinstance(value, str):
        return value
    try:
        iso = value.replace("Z", "+00:00")
        return int(datetime.datetime.fromisoformat(iso).timestamp())
    except (ValueError, OverflowError):
        return value


def _bt_query(token: str, query: str) -> list[dict]:
    """Run one BTQL query against Braintrust, with retry on 429/5xx."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = json.dumps({"query": query})
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(BRAINTRUST_BTQL, headers=headers, data=body,
                              timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as e:
            last_exc = e
            time.sleep(2)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise GuardrailFetchError(
                f"Braintrust BTQL returned {r.status_code}: {r.text[:300]}"
            )
        try:
            return r.json().get("data", []) or []
        except json.JSONDecodeError as e:
            raise GuardrailFetchError(f"Braintrust returned non-JSON: {e}") from e
    raise GuardrailFetchError(
        f"Braintrust BTQL request failed after {MAX_RETRIES} attempts: {last_exc}"
    )


def fetch_from_braintrust(
    token: str, transaction: str, expected_user: str | None = None
) -> tuple[dict, str] | None:
    """Locate the memory-ingest span for a transaction in Braintrust's
    `Memory [Production]` logs and adapt it into the OpenAI-shaped dict that
    `_render` consumes, plus the system-prompt text.

    Returns (completion_like, system_text) or None when nothing is found.
    Tries the transaction as given, then lower-cased (BTQL string match is
    case-sensitive and stored casing has varied across client versions).
    When `expected_user` is given and several ingest spans share the
    transaction, the span whose `metadata.user` matches is preferred — mirrors
    the OpenAI path's `--user` disambiguation.
    """
    rows: list[dict] = []
    seen_cases = []
    for tx in (transaction, transaction.lower()):
        if tx in seen_cases:
            continue
        seen_cases.append(tx)
        query = (
            "select id, span_id, span_attributes, metadata, input, output, created "
            f"from project_logs('{BRAINTRUST_MEMORY_PROJECT_ID}') "
            f"where metadata.transaction = '{tx}' limit 50"
        )
        rows = _bt_query(token, query)
        if rows:
            break
    if not rows:
        return None

    def _md(r: dict) -> dict:
        return r.get("metadata") or {}

    def _span_type(r: dict) -> Any:
        return (r.get("span_attributes") or {}).get("type")

    ingest = [r for r in rows if _md(r).get("prompterName") == INGEST_PROMPTER]
    if not ingest:
        # The transaction exists in Braintrust but carries no memory-ingest
        # span (e.g. only the sorting prompter ran). Nothing to classify.
        return None

    # When several ingest spans share the transaction and --user was supplied,
    # narrow to that user first (mirrors the OpenAI path's disambiguation).
    if expected_user:
        user_ingest = [
            r for r in ingest
            if (_md(r).get("user") or _md(r).get("userId")) == expected_user
        ]
        if user_ingest:
            ingest = user_ingest

    # Prefer the `llm` span — its `output` is the guardrail JSON object directly.
    # The `task` span wraps the same JSON as a string under `output.content`.
    chosen = next((r for r in ingest if _span_type(r) == "llm"), None) or ingest[0]
    md = _md(chosen)

    out = chosen.get("output")
    content: Any = out
    if isinstance(out, dict) and "memories" not in out and "content" in out:
        content = out.get("content")

    completion = {
        "id": chosen.get("id") or chosen.get("span_id") or "",
        "created": _iso_to_unix(chosen.get("created")),
        "model": md.get("model", ""),
        "metadata": {"user": md.get("user") or md.get("userId") or ""},
        "usage": {},
        "choices": [{"message": {"content": content}}],
    }

    # System prompt text (carries the appended `## User-Provided Summary`).
    # Any ingest span's input has it; take the first that yields a system message.
    system_text = ""
    for r in ingest:
        inp = r.get("input")
        msgs = inp.get("messages") if isinstance(inp, dict) else (
            inp if isinstance(inp, list) else [])
        parts = [
            _msg_text(m)
            for m in (msgs or [])
            if isinstance(m, dict) and m.get("role") == "system"
        ]
        parts = [p for p in parts if p]
        if parts:
            system_text = "\n\n".join(parts)
            break

    return completion, system_text


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, sort_keys=True)


def _parse_guardrail_output(content: Any) -> tuple[dict[str, Any] | None, str]:
    """Parse the guardrail envelope JSON from the assistant `content`.

    Returns (parsed_dict, raw_content). parsed_dict is None when JSON parse
    fails — raw_content is preserved for the schema-drift edge case so the
    caller can still show what shape was seen. Tolerates a markdown ```json
    fence in case a future rev wraps the output.
    """
    raw = content if isinstance(content, str) else _coerce_str(content)
    if isinstance(content, dict):
        return content, raw
    if not isinstance(content, str):
        return None, raw
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        stripped = "\n".join(inner).strip()
    try:
        parsed = json.loads(stripped)
        return (parsed if isinstance(parsed, dict) else None), raw
    except json.JSONDecodeError:
        return None, raw


def _find_heading(text: str, phrase: str) -> re.Match[str] | None:
    """First markdown heading (level 2+) whose title contains `phrase`
    (case-insensitive). Tolerates prompt revs that switch heading depth."""
    phrase_lower = phrase.lower()
    for m in _HEADING_RE.finditer(text):
        if phrase_lower in m.group(2).lower():
            return m
    return None


def _extract_user_block(text: str) -> tuple[str | None, str | None]:
    """Split the user-provided context out of the system prompt.

    IntelligenceService appends the runtime contextInstructions at the TAIL of
    the system prompt (after the static `## Output Requirements` section),
    starting with a `## User-Provided Summary` heading. The user's pasted
    summary is itself multi-section markdown with its own `##` headings
    (`## 1. Core Identity`, `## Who You Are`, …) — so the summary is NOT a
    single same-level section; it's everything from `## User-Provided Summary`
    to end-of-prompt.

    Returns (summary, user_info):
      * summary   — from the `## User-Provided Summary` heading to EOF, minus
                    a trailing internal profile block if one exists.
      * user_info — the internal `## User Information` profile block (to EOF)
                    when present; None otherwise. Current prompt revs usually
                    don't emit one — identity then comes from the summary via
                    `_extract_identity`, which scans the whole prompt and does
                    not depend on this split. We match only the exact
                    IntelligenceService heading here: broadening to paste-common
                    titles (`About You`, `User Profile`) would risk splitting on
                    a heading inside the user's own summary and truncating it.
    """
    if not text:
        return None, None
    start = _find_heading(text, "User-Provided Summary")
    if start is None:
        return None, None
    tail = text[start.end():]
    ui = _find_heading(tail, "User Information")
    if ui is not None:
        summary = tail[:ui.start()].strip("\n").rstrip()
        user_info = tail[ui.end():].strip("\n").rstrip()
        return (summary or None), (user_info or None)
    return (tail.strip("\n").rstrip() or None), None


# Identity extraction (compose-only). These two fields — the account holder's
# own first name and their location/country — are the ONLY things the skill may
# carry out of the paste, and they exist to fill the CS email (see SKILL.md ->
# "The paste-use rule"). Nothing here reads or emits risk content. The regexes
# are best-effort hints; the composing step still applies the account-holder-vs
# -third-party rules, and a human reviews every draft.
_NAME_PLACEHOLDER_RE = re.compile(r"\[USER_?NAME\]|\{\{?\s*user_?name\s*\}?\}", re.IGNORECASE)
# Two forms, both anchored to the start of a line: a pronoun-led phrase
# ("my/given/first/preferred name is X"), or a label that REQUIRES a `:`/`-`
# separator ("Name: X", "Given name - X"). The required separator is what keeps
# ordinary prose ("Name of the hospital: …") from matching as a first name.
_NAME_LINE_RE = re.compile(
    r"(?im)^\s*(?:(?:my|given|first|preferred)\s+name\s+is|"
    r"(?:given |first |preferred )?name\s*[:\-])\s*(.+?)\s*$"
)
# Same shape for location: pronoun-led phrase ("I live/reside/am based/am
# located in X", "I'm based in X"), or a bare label that REQUIRES a `:`/`-`
# ("Location: X", "Country - X"). Requiring the separator stops prose like
# "Country music was playing" from matching as a location.
_LOCATION_LINE_RE = re.compile(
    r"(?im)^\s*(?:i (?:live|reside|am based|am located)\s+in|i'm\s+based\s+in|"
    r"(?:current\s+)?(?:location|country|region|city|hometown|timezone|time zone)\s*[:\-])\s*(.+?)\s*$"
)
_PLACEHOLDER_NOTE = 'name is templated as a placeholder ([USER_NAME]) — use "Hi there,"'


def _extract_identity(
    summary: str | None, user_info: str | None, full_text: str | None
) -> dict[str, str | None]:
    """Best-effort pull of the two compose-only fields: the account holder's
    first name and their location string. Sources are tried profile-block first
    (most authoritative when present), then the summary, then the whole system
    prompt as a backstop (covers a profile block that sits before the summary).
    Returns {'name', 'name_note', 'location'} — all optional. Never extracts
    risk content."""
    name: str | None = None
    name_note = ""
    location: str | None = None
    for src in (user_info, summary, full_text):
        if not src:
            continue
        if name is None and not name_note:
            for m in _NAME_LINE_RE.finditer(src):
                cand = m.group(1).strip().strip(".").strip()
                if not cand:
                    continue
                if _NAME_PLACEHOLDER_RE.search(cand):
                    name_note = _PLACEHOLDER_NOTE
                    break
                if len(cand) <= 60:
                    name = cand.split()[0]
                    break
        if location is None:
            for m in _LOCATION_LINE_RE.finditer(src):
                cand = m.group(1).strip().strip(".").strip()
                if cand and not _NAME_PLACEHOLDER_RE.search(cand) and len(cand) <= 120:
                    location = cand
                    break
        if name and location:
            break
    if name is None and not name_note:
        blob = "\n".join(t for t in (user_info, summary, full_text) if t)
        if blob and _NAME_PLACEHOLDER_RE.search(blob):
            name_note = _PLACEHOLDER_NOTE
    return {"name": name, "name_note": name_note, "location": location}


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + " …", True


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _logs_url(completion_id: str) -> str:
    return f"https://platform.openai.com/logs/{completion_id}"


def _render(
    completion: dict[str, Any],
    transaction: str,
    expected_user: str | None,
    system_text: str,
    source: str = "openai",
    source_detail: str | None = None,
    aura: dict[str, Any] | None = None,
) -> str:
    cid = completion.get("id", "")
    created = completion.get("created", "")
    metadata = completion.get("metadata") or {}
    usage = completion.get("usage") or {}
    model = completion.get("model", "")

    actual_user = metadata.get("user", "")
    user_warning = ""
    if expected_user and actual_user and expected_user != actual_user:
        user_warning = (
            f"\n⚠️ UID mismatch — Slack alert said `{expected_user}` "
            f"but completion metadata.user is `{actual_user}`. "
            "Proceeding against the completion's UID; surface this to the caller.\n"
        )

    asst_content = (completion.get("choices") or [{}])[0].get("message", {}).get("content")
    guardrail, raw_content = _parse_guardrail_output(asst_content)

    summary_section, user_info_section = _extract_user_block(system_text)

    summary_block = (
        summary_section
        if summary_section
        else "(no User-Provided Summary section in the system prompt — heading not found)"
    )

    # --- header
    lines: list[str] = []
    if user_warning:
        lines.append(user_warning)
    is_bt = source == "braintrust"
    is_resp = source == "responses"
    lines.append(f"# Memory-ingestion guardrail trace — `{transaction}`")
    lines.append("")
    if is_bt:
        lines.append(
            "- **Source:** ⚠️ Braintrust fallback — "
            + (source_detail
               or ("OpenAI had no match for this transaction; recovered from "
                   f"Braintrust `{MEMORY_PROJECT_NAME}` logs."))
        )
    elif is_resp:
        lines.append(
            "- **Source:** OpenAI Responses API — resolved transaction → response id "
            "via the llmCall ledger (BigQuery `llm.llm_call`), then read `GET /v1/responses/{id}` "
            "(+ `/input_items` for the summary). memory-ingest runs on the Responses "
            "API, which OpenAI can't enumerate by metadata, so the chat-completions "
            "query never matches it. Authoritative — this is the expected path, not a fallback."
        )
    lines.append(f"- **Project:** {MEMORY_PROJECT_NAME} (prompter `{INGEST_PROMPTER}`)")
    lines.append(f"- **Completion:** `{cid}`")
    lines.append(f"- **Created:** {created} (unix)")
    lines.append(f"- **Model:** {model}")
    lines.append(f"- **User:** `{actual_user}`")
    if expected_user and expected_user != actual_user:
        lines.append(f"- **User (Slack alert):** `{expected_user}` ⚠️ mismatch")
    prompt_tok = usage.get("prompt_tokens")
    completion_tok = usage.get("completion_tokens")
    if prompt_tok is not None or completion_tok is not None:
        lines.append(
            f"- **Tokens:** prompt={prompt_tok}  completion={completion_tok}  "
            f"total={usage.get('total_tokens')}"
        )
    if not is_bt:
        lines.append(f"- **OpenAI Logs:** {_logs_url(cid)}")
    lines.append("")

    # --- guardrail outcome
    lines.append("## Guardrail outcome")
    lines.append("")
    if guardrail is None:
        lines.append("⚠️ assistant `content` did not parse as JSON. Raw content:")
        lines.append("")
        lines.append("```")
        snippet, _ = _truncate(raw_content, 2048)
        lines.append(snippet)
        lines.append("```")
    else:
        flag = guardrail.get("seriousHarmFlagSet")
        evidence = guardrail.get("seriousHarmEvidence")
        memories = guardrail.get("memories")
        n_memories = len(memories) if isinstance(memories, list) else None
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        lines.append(f"| `seriousHarmFlagSet` | `{flag}` |")
        lines.append(f"| `seriousHarmEvidence` | {evidence!r} |")
        lines.append(f"| `memories` (count) | `{n_memories}` |")
        extras = {
            k: v
            for k, v in guardrail.items()
            if k not in {"seriousHarmFlagSet", "seriousHarmEvidence", "memories"}
        }
        if extras:
            lines.append("")
            lines.append("Additional fields (unrecognised — schema may have drifted):")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(extras, indent=2, sort_keys=True))
            lines.append("```")
    lines.append("")

    # --- Aura user record (PRIMARY identity source: first name, country, email)
    lines.append("## Aura user record (primary identity source)")
    lines.append("")
    if aura is None:
        lines.append(
            "_No UID available to query the Aura API — fall back to the "
            "user-context profile block, then the pasted summary, for the "
            "first name and country. No email available for the Slack reply._"
        )
    elif aura.get("_status") != "ok":
        status = aura.get("_status")
        if status == "not_found":
            lines.append(
                "_No Aura user record (HTTP 404 `USER_NOT_FOUND`) for this UID — "
                "fall back to the profile block, then the pasted summary, for the "
                "first name and country. No email available for the Slack reply._"
            )
        else:
            lines.append(
                f"_Aura API lookup returned no usable record ({status}) — fall "
                "back to the profile block, then the pasted summary, for the "
                "first name and country. No email available for the Slack reply._"
            )
    else:
        given = aura.get("givenName") or "(blank — fall back to profile/paste)"
        family = aura.get("familyName") or "(blank)"
        email = aura.get("email") or "(blank — no email for the Slack reply)"
        cc = (aura.get("countryCode") or "").strip()
        tz = aura.get("timezone") or "(blank)"
        key = _COUNTRY_CODE_TO_KEY.get(cc.upper()) if cc else None
        if not cc:
            country_disp = "(blank — fall back to timezone/profile/paste)"
        elif key and key != cc.upper():
            country_disp = f"`{cc}` → resource_map key `{key}`"
        elif key:
            country_disp = f"`{cc}` (resource_map key `{key}`)"
        else:
            country_disp = f"`{cc}` (not a mapped country — country-not-in-map path)"
        lines.append(
            "**Primary source** for `{first_name}` (from `givenName`), the "
            "country (from `countryCode`, with `timezone` as confirmation), and "
            "the CS email address. Prefer these over the profile block and the "
            "pasted summary; fall back to those only for a field left blank here."
        )
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        lines.append(f"| `givenName` | {given} |")
        lines.append(f"| `familyName` | {family} |")
        lines.append(f"| `email` | {email} |")
        lines.append(f"| `countryCode` | {country_disp} |")
        lines.append(f"| `timezone` | `{tz}` |")
        lines.append("")
        lines.append(
            "_The email is surfaced so the Slack reviewer can send the CS draft "
            "without a further lookup. Per SKILL.md Step 6 it is written to the "
            "Slack reply **only on the active-risk draft path** — never on a "
            "no-active-risk or resources-in-place short post._"
        )
    lines.append("")

    # --- user-provided summary (the full appended user block)
    lines.append("## User-provided summary")
    lines.append("")
    lines.append("```")
    lines.append(summary_block)
    lines.append("```")
    lines.append("")

    # --- user context (only when a distinct profile block exists; current
    #     prompt revs usually fold everything into the summary above)
    if user_info_section:
        lines.append("## User context")
        lines.append("")
        lines.append("```")
        lines.append(user_info_section)
        lines.append("```")
        lines.append("")

    # --- identity for email (compose-only): the two fields authorized to leave
    #     the paste. Surfaced explicitly so composition never stalls on "is this
    #     allowed?" (it is) or "there's no profile block" (identity comes from
    #     the summary). Never carries risk content.
    identity = _extract_identity(summary_section, user_info_section, system_text)
    lines.append("## Identity for email (compose-only — fallback source)")
    lines.append("")
    lines.append(
        "> **Fallback only.** The Aura user record above is the primary source for "
        "the first name and country; use these paste-derived candidates only to fill "
        "a field the Aura record left blank (or when it was unavailable). The account "
        "holder's **first name** and **country/location** are the only fields that may "
        "be carried out of the paste, and they exist to fill the CS email. Reading them "
        "is authorized and expected — do NOT read anything else here for risk, and never "
        "echo the summary. A missing value is never a reason to withhold a draft (no "
        'name → "Hi there,"; unknown country → the country-not-in-map path).'
    )
    lines.append("")
    if identity["location"]:
        lines.append(
            f"- **Location candidate:** {identity['location']}  "
            "→ map to a country for resource routing (SKILL.md Step 5b)."
        )
    else:
        lines.append(
            "- **Location candidate:** none matched a location line — scan the summary "
            "above for a stated home city/region/country (reading it for the email is "
            "authorized); if none, use the country-not-in-map path."
        )
    if identity["name"]:
        lines.append(
            f"- **First-name candidate:** {identity['name']}  "
            '→ confirm it\'s the account holder\'s own (not a third party), else use "there".'
        )
    elif identity["name_note"]:
        lines.append(f"- **First name:** {identity['name_note']}.")
    else:
        lines.append('- **First-name candidate:** none found — use "Hi there,".')
    lines.append("")

    if is_bt:
        lines.append(
            "→ Drill in: open this span in the Braintrust UI "
            f"(Memory [Production] project, span `{cid}`), or check the OpenAI "
            "Logs to confirm whether the completion is genuinely missing there."
        )
    else:
        lines.append(
            f"→ Drill in: open the completion in the OpenAI Logs UI ({_logs_url(cid)})"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _resolve_aura_user(uid: str | None) -> dict | None:
    """Best-effort Aura user-record fetch with a stderr breadcrumb. Wraps
    `fetch_aura_user` so a lookup failure never interrupts the readout."""
    if not uid:
        print(
            "# no UID resolved — skipping Aura user-record lookup "
            "(name/country fall back to the profile block + pasted summary).",
            file=sys.stderr,
        )
        return None
    print(f"# fetching Aura user record: GET {AURA_BASE}/users/{uid}", file=sys.stderr)
    aura = fetch_aura_user(uid)
    if aura and aura.get("_status") != "ok":
        print(f"# Aura user-record lookup: {aura.get('_status')} "
              "(degrading to profile block + pasted summary).", file=sys.stderr)
    return aura


# ---------------------------------------------------------------------------
# Ledger (BigQuery llm.llm_call) + Responses-API resolution
# ---------------------------------------------------------------------------
#
# memory-ingest runs on the OpenAI Responses API, whose stored objects OpenAI
# does NOT let you enumerate by metadata — so the chat-completions query above
# never matches it. The IntelligenceService per-call LLM ledger (BigQuery
# `aura-main.llm.llm_call`, read through the bundled `llm_ledger.py`; semantics in AI
# Knowledge `Tools/Aura API/LLM Calls`) records every call across both APIs and
# is the queryable transaction → `openaiId` map. Resolve the id there, then read
# the stored object directly (`/v1/responses/{id}` or `/chat/completions/{id}`).


def fetch_ledger_row(uid: str, transaction: str) -> tuple[str, dict | None]:
    """Resolve a transaction to its memory-ingest ledger row.

    Returns `(status, row)` where status is `found` | `not_found` |
    `unreachable` | `no_token`. The read filters by `userId` + `prompterName` +
    `transaction`, trying the id as given, then lower- and upper-cased (the
    filter is exact-match and the alert's casing may differ from the stored one),
    with no row cap. A missing `GCP_KEY` short-circuits to `no_token` (a configuration gap,
    not an outage). `unreachable` (any BigQuery failure) stays DISTINCT from
    `not_found` (query succeeded, no matching row) so the caller can tell an
    outage from a genuine absence — the whole point of the ladder. Never raises.
    """
    if not os.environ.get("GCP_KEY"):
        print("# GCP_KEY unset — the llmCall ledger (BigQuery) can't be read, "
              "so rung 2 is skipped (config gap, not an outage).", file=sys.stderr)
        return "no_token", None
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    for tx in dict.fromkeys((transaction, transaction.lower(), transaction.upper())):
        try:
            rows = llm_ledger.rows({"userId": uid, "prompterName": INGEST_PROMPTER,
                                    "transaction": tx, "limit": 1})
        except llm_ledger.LedgerError as e:
            print(f"# llmCall ledger read failed: {e}", file=sys.stderr)
            return "unreachable", None
        if rows:
            return "found", rows[0]
    return "not_found", None


def fetch_completion_by_id(token: str, completion_id: str) -> dict:
    """Fetch one stored chat completion by id. Raises GuardrailFetchError on an
    unreachable/failed OpenAI request (distinct from an authoritative miss)."""
    return _get(token, f"/chat/completions/{completion_id}")


def fetch_response(token: str, resp_id: str) -> dict:
    """Fetch one stored Responses-API object by id. Raises GuardrailFetchError
    on an unreachable/failed OpenAI request (distinct from 'not found')."""
    return _get(token, f"/responses/{resp_id}")


def fetch_response_input_items(token: str, resp_id: str) -> str:
    """Concatenate the system-role input text for a Responses-API call.

    The Responses API keeps inputs separate from the response object, on
    `/responses/{id}/input_items`; the memory-ingest prompt (with the user's
    pasted summary appended) is the system message there — the Responses-API
    analogue of the chat `/messages` system message.
    """
    parts: list[str] = []
    after: str | None = None
    while True:
        params = {"limit": PAGE_LIMIT}
        if after:
            params["after"] = after
        data = _get(token, f"/responses/{resp_id}/input_items", params)
        items = data.get("data", [])
        for m in items:
            if m.get("role") == "system":
                t = _msg_text(m)
                if t:
                    parts.append(t)
        if not data.get("has_more") or not items:
            break
        after = data.get("last_id") or (items[-1].get("id") if items else None)
        if not after:
            break
    return "\n\n".join(parts)


def _response_output_text(resp: dict) -> Any:
    """Pull the assistant message text out of a Responses `output` array (the
    guardrail JSON envelope), skipping reasoning items."""
    out: list[str] = []
    for item in resp.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") in ("output_text", "text"):
                t = part.get("text")
                if t:
                    out.append(t)
    return "\n".join(out)


def _response_to_completion(resp: dict) -> dict:
    """Adapt a `/v1/responses` object into the chat-completion-shaped dict that
    `_render` consumes, so the Responses path reuses the same renderer/parser.
    Maps the Responses usage keys (`input_tokens`/`output_tokens`) onto the
    chat keys (`prompt_tokens`/`completion_tokens`)."""
    u = resp.get("usage") or {}
    return {
        "id": resp.get("id", ""),
        "created": resp.get("created_at", ""),
        "metadata": resp.get("metadata") or {},
        "model": resp.get("model", ""),
        "usage": {
            "prompt_tokens": u.get("input_tokens"),
            "completion_tokens": u.get("output_tokens"),
            "total_tokens": u.get("total_tokens"),
        },
        "choices": [{"message": {"content": _response_output_text(resp)}}],
    }


def _choose_match(matches: list[dict], expected_user: str | None) -> dict:
    """Pick one completion when several share a transaction (rare). Prefer the
    one whose metadata.user matches `expected_user`; else the most recent, with
    the others listed on stderr."""
    if len(matches) == 1:
        return matches[0]
    if expected_user:
        hit = next(
            (c for c in matches if (c.get("metadata") or {}).get("user") == expected_user),
            None,
        )
        if hit is not None:
            return hit
    print(
        f"⚠️ {len(matches)} memory-ingest completions matched — using the most recent. Others:",
        file=sys.stderr,
    )
    for c in matches[1:]:
        print(f"  - {c.get('id')}  created={c.get('created')}  "
              f"user={(c.get('metadata') or {}).get('user')}", file=sys.stderr)
    return matches[0]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # avoid cp1252 surprises on Windows

    parser = argparse.ArgumentParser(
        description=(
            "Fetch and parse one memory-ingestion guardrail trace from OpenAI "
            "stored completions (Memory [Production] project)."
        ),
    )
    parser.add_argument(
        "--transaction",
        required=True,
        help="The transaction UUID from the k8bot guardrail alert.",
    )
    parser.add_argument(
        "--user",
        default=None,
        help=(
            "Optional Firebase UID from the Slack alert — used as a paranoia "
            "cross-check against the completion's metadata.user."
        ),
    )
    # --lookback retained for CLI back-compat; OpenAI's list endpoint has no
    # native time filter and the metadata filter already scopes to one
    # transaction, so it's accepted but unused. Flagged on stderr if passed.
    parser.add_argument(
        "--lookback",
        default=None,
        help="[Deprecated post-BT migration] No longer used — the metadata "
             "filter scopes to one transaction regardless of age.",
    )
    args = parser.parse_args()

    if args.lookback:
        print(
            "# note: --lookback is ignored post-BT migration; the "
            "metadata[transaction] filter already scopes to one transaction.",
            file=sys.stderr,
        )

    transaction = args.transaction.strip()
    if not _UUID_RE.match(transaction):
        print(
            f"ERROR: '{transaction}' doesn't look like a UUID. "
            "Expected the 8-4-4-4-12 hex form from the k8bot `*Transaction:*` line.",
            file=sys.stderr,
        )
        return 2

    try:
        token = _load_token()
    except AuthError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # Resolution ladder — each rung distinguishes NOT_FOUND (advance) from
    # UNREACHABLE (an outage reported honestly, never mistaken for absence):
    #   1. chat completions   — pre-migration / non-migrated transactions
    #   2. ledger → Responses  — the normal memory-ingest path (Responses API)
    #   3. Braintrust          — genuine last resort, with a why-we're-here banner
    openai_unreachable = False

    # --- Rung 1: chat-completions metadata query.
    print(
        f"# rung 1: {MEMORY_PROJECT_NAME} chat-completions for "
        f"metadata[transaction]={transaction} AND metadata[name]={INGEST_PROMPTER}",
        file=sys.stderr,
    )
    try:
        matches = fetch_ingest_completions(token, transaction)
    except GuardrailFetchError as e:
        print(f"# chat-completions query unreachable ({e}); continuing to the "
              "ledger + Responses-API path.", file=sys.stderr)
        matches = []
        openai_unreachable = True

    if matches:
        chosen = _choose_match(matches, args.user)
        try:
            system_text = fetch_system_message(token, chosen["id"])
        except GuardrailFetchError as e:
            print(f"WARNING: /messages fetch failed ({e}); summary sections "
                  "unavailable.", file=sys.stderr)
            system_text = ""
        resolved_uid = (chosen.get("metadata") or {}).get("user") or args.user
        aura = _resolve_aura_user(resolved_uid)
        print(_render(chosen, transaction, args.user, system_text,
                      source="openai", aura=aura))
        return 0

    # --- Rung 2: ledger resolves transaction → (openaiId, openaiAPI), then read
    # the stored object by id. The ledger is Aura-side, so it stays reachable
    # during an OpenAI outage and doubles as an existence oracle.
    ledger_status, row = "skipped", None
    if args.user:
        print("# rung 2: resolving via the llmCall ledger (BigQuery) "
              f"(userId={args.user}, prompterName={INGEST_PROMPTER})…", file=sys.stderr)
        ledger_status, row = fetch_ledger_row(args.user, transaction)
    else:
        print("# rung 2 skipped: no --user, and the ledger read filters by userId "
              "(the k8bot alert always carries the UID; pass it to enable this path).",
              file=sys.stderr)

    ledger_oai_id: str | None = None
    oai_object_absent = False
    if ledger_status == "found" and row is not None:
        ledger_oai_id = row.get("openaiId") or ""
        api = row.get("openaiAPI") or ""
        src = "responses" if (api == "responses" or ledger_oai_id.startswith("resp_")) else "openai"
        print(f"# ledger resolved transaction → {ledger_oai_id} (openaiAPI={api}); "
              "fetching the stored object.", file=sys.stderr)
        completion: dict | None = None
        try:
            completion = (
                _response_to_completion(fetch_response(token, ledger_oai_id))
                if src == "responses"
                else fetch_completion_by_id(token, ledger_oai_id)
            )
        except NotFoundError:
            # The ledger has the id but OpenAI no longer stores the object
            # (likely past retention). Authoritative absence, not an outage.
            print(f"# ledger has {ledger_oai_id} but OpenAI returns 404 — the stored "
                  "object is gone (likely past retention), not an outage.", file=sys.stderr)
            oai_object_absent = True
        except GuardrailFetchError as e:
            print(f"# ledger resolved {ledger_oai_id} but the OpenAI fetch failed "
                  f"({e}) — OpenAI unreachable, not the record missing.", file=sys.stderr)
            openai_unreachable = True
        if completion is not None:
            # Object in hand. The summary fetch is best-effort and must NOT
            # discard the authoritative object if it fails (mirrors rung 1's
            # degrade-to-empty-summary behaviour).
            try:
                system_text = (
                    fetch_response_input_items(token, ledger_oai_id)
                    if src == "responses"
                    else fetch_system_message(token, ledger_oai_id)
                )
            except GuardrailFetchError as e:
                print(f"WARNING: input-items/messages fetch failed ({e}); summary "
                      "sections unavailable.", file=sys.stderr)
                system_text = ""
            resolved_uid = (completion.get("metadata") or {}).get("user") or args.user
            aura = _resolve_aura_user(resolved_uid)
            print(_render(completion, transaction, args.user, system_text,
                          source=src, aura=aura))
            return 0

    # --- Rung 3: Braintrust, with a provenance reason reflecting WHY we're here.
    if ledger_status == "found" and oai_object_absent:
        bt_reason = (
            f"The llmCall ledger has this transaction as `{ledger_oai_id}`, but "
            "OpenAI no longer stores that object (404 — likely past the retention "
            "horizon). Recovered content from Braintrust. Not an outage."
        )
    elif ledger_status == "found" and ledger_oai_id:
        bt_reason = (
            f"OpenAI unreachable. The llmCall ledger CONFIRMS this transaction "
            f"exists as `{ledger_oai_id}`, but the OpenAI fetch for it failed — an "
            "availability problem, not a missing record. Recovered content from Braintrust."
        )
    elif ledger_status == "no_token":
        bt_reason = (
            "`GCP_KEY` is unset, so rung 2 (the llmCall ledger in BigQuery, the normal "
            "Responses-API path) was skipped — recovered from Braintrust. Set the key to "
            "resolve via the Responses API instead. A configuration gap, not an outage."
        )
    elif openai_unreachable:
        bt_reason = (
            "OpenAI was unreachable (the chat-completions query failed) — recovered from "
            "Braintrust. This is NOT confirmation the record is absent from OpenAI."
        )
    elif ledger_status == "unreachable":
        bt_reason = (
            "OpenAI had no chat completion and the llmCall ledger was unreachable, "
            "so the transaction's presence in OpenAI (as a Responses-API call) could not "
            "be confirmed — recovered from Braintrust."
        )
    elif ledger_status == "not_found":
        bt_reason = (
            "Not stored as a chat completion and absent from the llmCall ledger "
            "(predates the ledger deploy, or never recorded) — recovered from Braintrust, "
            "the expected last-resort path. No action needed."
        )
    else:  # skipped — no user id
        bt_reason = (
            "No user id was available to query the llmCall ledger, so the "
            "Responses-API path couldn't be checked — recovered from Braintrust."
        )

    bt_token = os.environ.get("BRAINTRUST_API_KEY")
    bt_ran = False
    if bt_token:
        bt_ran = True
        print("# rung 3: trying Braintrust fallback "
              f"(BTQL on Braintrust {MEMORY_PROJECT_NAME})…", file=sys.stderr)
        try:
            bt_result = fetch_from_braintrust(bt_token, transaction, args.user)
        except GuardrailFetchError as e:
            print(f"# Braintrust fallback failed: {e}", file=sys.stderr)
            bt_result = None
        if bt_result is not None:
            bt_completion, bt_system_text = bt_result
            print("# recovered from Braintrust.", file=sys.stderr)
            bt_uid = (bt_completion.get("metadata") or {}).get("user") or args.user
            bt_aura = _resolve_aura_user(bt_uid)
            print(_render(bt_completion, transaction, args.user, bt_system_text,
                          source="braintrust", source_detail=bt_reason, aura=bt_aura))
            return 0
        print("# Braintrust fallback returned no memory-ingest span for this "
              "transaction.", file=sys.stderr)

    # Terminal message — all three rungs exhausted.
    detail = (
        "the Braintrust fallback found nothing either"
        if bt_ran else
        "the Braintrust fallback was NOT run because BRAINTRUST_API_KEY is unset "
        "(set it and re-run)"
    )
    print(
        f"0 memory-ingest traces matched transaction `{transaction}`: not in "
        f"{MEMORY_PROJECT_NAME} chat completions, not resolvable via the "
        f"llmCall ledger (status: {ledger_status}), and {detail}. Either the "
        "UUID is wrong, the trace predates the ledger deploy AND OpenAI's metadata "
        "rollout, it's past the retention horizon, or it was never persisted "
        "(have a human open the OpenAI Logs / Braintrust UI directly).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
