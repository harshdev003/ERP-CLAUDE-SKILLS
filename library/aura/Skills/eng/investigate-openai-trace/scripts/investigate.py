#!/usr/bin/env python3
"""
investigate-openai-trace CLI entry point.

Migrated off Braintrust 2026-06-05 (PROD-2464, umbrella PROD-2456). Reads
OpenAI stored chat-completions instead of Braintrust BTQL. Renamed from
`investigate-braintrust-trace` → `investigate-openai-trace` 2026-06-05 (the
deferred PROD-2464 follow-up, after the guardrail sibling that referenced the
old path merged).

Modes:
  Triage:  --project <name|id> --since <duration> [--status guardrail|success|all]
                                                   [--root-only] [--prompter <name>]
  Focus:   --transaction <uuid>                    (cross-project fan-out)
  Trace:   --span <chatcmpl-id> [--project <name|id>]
  User:    --user <firebase-uid> [--project <name|id>] --since <duration>

Plus link-follow:
  --url "https://platform.openai.com/logs/<chatcmpl-id>"   (or a bare chatcmpl id)

Defaults: since=1h (triage / user).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Optional

import requests

# Skill-local imports.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import format as fmt  # noqa: E402  (skill-local module)
from openai_client import (  # noqa: E402
    AuthError,
    OpenAIClient,
    OpenAIError,
    decode_logs_url,
    is_uuid,
    parse_since,
)
from normalize import NormalizedCompletion, normalize  # noqa: E402
from projects import Project, fetch_production_projects, resolve, suggest  # noqa: E402
import responses_source as rs  # noqa: E402  (ledger-indexed Responses reads)

DEFAULT_SINCE = "1h"


# ---------------------------------------------------------------------------
# Braintrust fallback for mode_focus (--transaction)
#
# When the cross-project OpenAI fan-out finds 0 completions, BT's parallel
# logging path may still hold spans for the transaction. Requires
# BRAINTRUST_API_KEY; degrades gracefully when absent.
# ---------------------------------------------------------------------------

_BRAINTRUST_BTQL = "https://api.braintrust.dev/btql"
_BRAINTRUST_ORG = "Aura Health 1"
# Production BT projects to fan out across in mode_focus. Mirrors the primary
# OpenAI production projects that carry metadata.transaction.
_BT_FOCUS_PROJECTS = ["Memory", "Conversation", "Journal"]
_BT_MAX_RETRIES = 3


def _bt_resolve_project_id(token: str, project_name: str) -> str | None:
    """Resolve a Braintrust project name to its UUID. Returns None on failure."""
    try:
        r = requests.get(
            "https://api.braintrust.dev/v1/project",
            headers={"Authorization": f"Bearer {token}"},
            params={"project_name": project_name, "org_name": _BRAINTRUST_ORG},
            timeout=30,
        )
        if not r.ok:
            return None
        objs = r.json().get("objects", [])
        return objs[0]["id"] if objs else None
    except Exception:
        return None


def _bt_run_query(token: str, query: str) -> list[dict]:
    """Run one BTQL query, with retry on 429/5xx."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in range(_BT_MAX_RETRIES):
        try:
            r = requests.post(
                _BRAINTRUST_BTQL, headers=headers, json={"query": query}, timeout=60,
            )
        except requests.RequestException:
            time.sleep(2)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code != 200:
            return []
        try:
            return r.json().get("data", []) or []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _bt_fallback_focus(
    transaction_uuid: str, bt_token: str, *, prompter: str | None = None
) -> str | None:
    """Fan out across BT production projects for a transaction UUID.

    Returns a formatted markdown string when spans are found, None otherwise.
    Tries the transaction as-given and lower-cased (BT string match is
    case-sensitive and stored casing has varied across client versions).
    When prompter is given, mirrors the OpenAI metadata.name filter so that
    a --prompter-scoped focus query doesn't return unrelated prompters from BT.
    """
    found: dict[str, list[dict]] = {}
    for project_name in _BT_FOCUS_PROJECTS:
        project_id = _bt_resolve_project_id(bt_token, project_name)
        if not project_id:
            continue
        seen_tx: set[str] = set()
        for tx in [transaction_uuid, transaction_uuid.lower()]:
            if tx in seen_tx:
                continue
            seen_tx.add(tx)
            prompter_clause = (
                f"and metadata.name = '{prompter}' " if prompter else ""
            )
            query = (
                "select id, span_id, span_attributes, metadata, output, created "
                f"from project_logs('{project_id}') "
                f"where metadata.transaction = '{tx}' "
                f"{prompter_clause}"
                "limit 50"
            )
            rows = _bt_run_query(bt_token, query)
            if rows:
                found[project_name] = rows
                break

    if not found:
        return None

    total = sum(len(rows) for rows in found.values())
    lines: list[str] = [
        f"Transaction `{transaction_uuid}` — {total} span(s) found in Braintrust",
        "(OpenAI returned 0 completions; Braintrust fallback active.)",
        "",
    ]
    for project_name, rows in found.items():
        lines.append(f"=== Braintrust: {project_name} — {len(rows)} span(s) ===")
        for row in sorted(rows, key=lambda r: r.get("created") or ""):
            md = row.get("metadata") or {}
            prompter = md.get("name") or md.get("prompterName") or "?"
            span_id = row.get("id") or row.get("span_id") or "?"
            created = row.get("created") or "?"
            user = md.get("user") or md.get("userId") or "—"
            conv_id = md.get("conversationId") or "—"
            dialogue_id = md.get("dialogueId") or "—"
            lines.append(f"  [{prompter}]  created={created}")
            lines.append(f"          span={span_id}  user={user}")
            if conv_id != "—":
                lines.append(f"          conversationId={conv_id}")
            if dialogue_id != "—":
                lines.append(f"          dialogueId={dialogue_id}")
        lines.append("")
    lines.append(
        "Note: Braintrust span IDs are UUIDs — no OpenAI Logs URL is available.\n"
        "→ Drill in: data:analyze-user-conversations --user-id <user>  "
        "(if coach spans are present)"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="investigate openai trace",
        description="Investigate Aura model traces in chained modes (OpenAI stored completions).",
    )
    # Mode discriminators (mutually exclusive at the conceptual level).
    p.add_argument("--transaction", help="Mode 2 (focus): transaction UUID — fans out across projects")
    p.add_argument("--span", help="Mode 3 (trace): single completion id (chatcmpl-...); --project optional")
    p.add_argument("--user", help="User pivot: Firebase UID (case-sensitive)")
    p.add_argument("--url", help="Decode an OpenAI Logs URL (or a bare chatcmpl id) and run Mode 3")

    # Filters
    p.add_argument("--project", help="Project short name (e.g. 'Memory', 'Conversation') or proj_ id")
    p.add_argument("--since", default=DEFAULT_SINCE,
                   help=f"Triage / user time window (e.g. 30m, 4h, 2d). Default {DEFAULT_SINCE}.")
    # --lookback retained for CLI back-compat; OpenAI's list endpoint has no
    # native time filter and the metadata filters already scope tightly, so
    # it's accepted but unused. Flagged on stderr if passed.
    p.add_argument("--lookback", default=None,
                   help="[Deprecated post-BT migration] No longer used — OpenAI has no "
                        "server-side time filter; metadata filters scope the result set.")
    p.add_argument("--status", choices=("guardrail", "success", "all"), default="all",
                   help="Triage: filter completions by their parsed envelope result")
    p.add_argument("--root-only", action="store_true",
                   help="Triage: limit to completions that carry a guardrail envelope")
    p.add_argument("--prompter",
                   help="Triage / focus: filter by metadata.name (e.g. "
                        "'memory-ingest-prompter' vs 'memory-prompter' inside Memory)")

    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print(label: str, msg: str) -> None:
    print(f"# {label}: {msg}", file=sys.stderr)


def _status_match(c: NormalizedCompletion, status: str) -> bool:
    if status == "all":
        return True
    if status == "guardrail":
        return c.output_result == "guardrail" or c.serious_harm_flag is True
    if status == "success":
        return c.output_result == "success"
    return True


def _has_envelope(c: NormalizedCompletion) -> bool:
    return c.output_result is not None or c.serious_harm_flag is not None


# ---------------------------------------------------------------------------
# Mode handlers
# ---------------------------------------------------------------------------


def mode_triage(args, client: OpenAIClient, projects: list[Project]) -> str:
    project = resolve(args.project, projects)
    if not project:
        s = suggest(args.project or "", projects)
        return (
            f"Unknown project: {args.project!r}.\n"
            f"Did you mean: {', '.join(s)}?\n"
            "Run `--project Memory` (or any production project short name)."
        )
    since_secs = parse_since(args.since)
    import time as _time
    since_unix = int(_time.time()) - since_secs

    metadata = {"name": args.prompter} if args.prompter else None
    _print("triage", f"project={project.full_name} since={args.since} "
                     f"prompter={args.prompter or '(any)'} status={args.status}")

    completions: list[NormalizedCompletion] = []
    for doc in client.list_completions(project.project_id, metadata=metadata,
                                       since_unix=since_unix):
        c = normalize(doc)
        if args.root_only and not _has_envelope(c):
            continue
        if not _status_match(c, args.status):
            continue
        completions.append(c)
    # newest-first (the walk already yields newest-first, but be explicit)
    completions.sort(key=lambda c: c.created or 0, reverse=True)
    return fmt.render_triage(
        completions,
        project=project,
        since_human=args.since,
        status_filter=args.status,
        root_only=args.root_only,
        prompter=args.prompter,
    )


def _responses_for_focus(
    uuid: str,
    client: OpenAIClient,
    projects: list[Project],
    *,
    prompter: str | None = None,
) -> tuple[dict[Project, list[NormalizedCompletion]], dict]:
    """Responses-API completions for a transaction, grouped by project.

    The ledger carries `project` per row, so unlike the completions path this
    never has to try each project's key and read 404s as misses -- the row says
    which project owns the id.
    """
    by_name = {p.name.lower(): p for p in projects}

    def key_for_project(short: str | None):
        project = by_name.get((short or "").lower())
        if project is None:
            return None, None
        return client._completion_key(project.project_id), project.project_id

    try:
        rows = rs.rows_for_transaction(uuid, prompter)
    except rs.ResponsesSourceError as exc:
        return {}, {"error": str(exc)[:300]}

    docs, unfetchable = rs.hydrate(rows, key_for_project)
    grouped: dict[Project, list[NormalizedCompletion]] = {}
    for doc in docs:
        project = by_name.get(str(doc.get("_ledger_project") or "").lower())
        if project is None:
            # A project the org discovery didn't return (renamed, or not
            # [Production]). Don't drop it -- the row is real.
            project = Project(
                name=str(doc.get("_ledger_project") or "unknown"),
                project_id="",
                full_name=f"{doc.get('_ledger_project') or 'unknown'} (from ledger)",
            )
            by_name[project.name.lower()] = project
        grouped.setdefault(project, []).append(normalize(doc))

    return grouped, {"row_count": len(rows), "unfetchable": unfetchable}


def mode_focus(args, client: OpenAIClient, projects: list[Project]) -> str:
    uuid = args.transaction
    if not is_uuid(uuid):
        return (
            f"--transaction expects a full UUID (got: {uuid!r}). "
            f"Example: 3e133cc2-dbb3-4977-a355-f8e8550d2b1f"
        )
    by_project: dict[Project, list[NormalizedCompletion]] = {}
    for project in projects:
        metadata = {"transaction": uuid}
        if args.prompter:
            metadata["name"] = args.prompter
        try:
            docs = list(client.list_completions(project.project_id, metadata=metadata))
        except OpenAIError as e:
            _print(f"{project.full_name} skipped", str(e))
            continue
        completions = [normalize(d) for d in docs]
        if completions:
            by_project[project] = completions

    # Responses-API calls are invisible to the fan-out above: `GET /v1/responses`
    # (the list form) rejects API keys, so they cannot be walked. The llmCall
    # ledger indexes them by transaction instead. This runs BEFORE the Braintrust
    # fallback -- most production projects are already on Responses
    # (ENGBACK-1235/1246), so without it a transaction touching only side-effect
    # prompters looks empty and gets handed to a legacy source.
    resp_by_project, resp_diag = _responses_for_focus(
        uuid, client, projects, prompter=args.prompter
    )
    for project, completions in resp_by_project.items():
        by_project.setdefault(project, []).extend(completions)
    if resp_diag.get("error"):
        _print(
            "focus",
            f"WARNING: could not read Responses-API calls ({resp_diag['error']}). "
            "Results below cover the Chat-Completions half ONLY.",
        )
    elif resp_diag.get("unfetchable"):
        _print(
            "focus",
            f"WARNING: {len(resp_diag['unfetchable'])} Responses object(s) were in "
            "the ledger but could not be fetched -- this view is incomplete, not empty.",
        )

    if not any(by_project.values()):
        bt_token = os.environ.get("BRAINTRUST_API_KEY")
        if bt_token:
            _print(
                "focus",
                "OpenAI returned 0 completions; trying Braintrust fallback…",
            )
            bt_report = _bt_fallback_focus(uuid, bt_token, prompter=args.prompter)
            if bt_report is not None:
                return bt_report
            _print("focus", "Braintrust fallback also returned 0 spans.")

    return fmt.render_focus(uuid, by_project)


def mode_trace(args, client: OpenAIClient, projects: list[Project],
               *, project: Project | None = None,
               completion_id: str | None = None) -> str:
    completion_id = completion_id or args.span
    if not completion_id:
        return "--span <chatcmpl-id> is required (see --transaction or --url for discovery)."

    # A `resp_` id is a Responses object, not a chat completion. It cannot be
    # retrieved via /chat/completions/{id} at all, and the focus mode's own
    # "Drill in" hints emit these ids -- so without this branch the tool prints
    # follow-up commands that lead nowhere.
    if completion_id.startswith("resp_"):
        return _trace_response(args, client, projects, completion_id)

    project = project or (resolve(args.project, projects) if args.project else None)

    # Resolve which project holds this completion.
    doc = None
    if project:
        _print("trace", f"retrieving {completion_id} from {project.full_name}")
        doc = client.retrieve_completion(completion_id, project.project_id)
        if doc is None:
            return (
                f"Completion `{completion_id}` not found in project "
                f"`{project.full_name}` (404). Drop `--project` to fan out across "
                "all production projects, or confirm the completion id."
            )
    else:
        # Fan out across production projects until one returns 200.
        _print("trace", f"fanning out across {len(projects)} project(s) to locate {completion_id}")
        for p in projects:
            d = client.retrieve_completion(completion_id, p.project_id)
            if d is not None:
                project = p
                doc = d
                _print("trace", f"found in {p.full_name}")
                break
        if doc is None or project is None:
            return (
                f"Completion `{completion_id}` not found in any of the "
                f"{len(projects)} production projects. Either the id is wrong, the "
                "completion predates OpenAI's stored-completions retention horizon, "
                "or it lives in a non-production project."
            )

    completion = normalize(doc)
    try:
        messages = client.fetch_messages(completion_id, project.project_id)
    except OpenAIError as e:
        _print("trace", f"/messages fetch failed ({e}); input section unavailable")
        messages = []
    return fmt.render_trace(completion, messages, project=project)


def _trace_response(args, client: OpenAIClient, projects: list[Project],
                    resp_id: str) -> str:
    """Trace a single Responses object by id.

    No project fan-out is needed: the ledger row carries `project`, so the right
    project key is known on the first attempt. That is strictly better than the
    completions path, which has to try each project and read 404s as misses.

    The transcript comes from `/{id}/input_items` -- the response object's own
    `input` is null, so reading only the object would render an assistant-only
    trace that looks complete.
    """
    by_name = {p.name.lower(): p for p in projects}

    def key_for_project(short: str | None):
        project = by_name.get((short or "").lower())
        if project is None:
            return None, None
        return client._completion_key(project.project_id), project.project_id

    # An explicit --project is enough on its own: a Responses object is
    # retrievable by id given the owning project's key, so the ledger is only
    # needed to *discover* the project. Honour the flag before consulting the
    # ledger -- the focus drill-in hints include --project, and the ledger-miss
    # message below tells the caller to pass it, so ignoring it would have the
    # tool instruct an action it does not act on.
    explicit = resolve(args.project, projects) if getattr(args, "project", None) else None

    row: dict | None = None
    ledger_error: str | None = None
    try:
        row = rs.row_for_openai_id(resp_id)
    except rs.ResponsesSourceError as exc:
        ledger_error = str(exc)[:300]

    if row is None and explicit is not None:
        # Synthesise the minimum a hydrate() needs. Ids the ledger would have
        # supplied are absent, but the response object carries its own metadata,
        # so the rendered trace is still complete.
        row = {"openaiId": resp_id, "project": explicit.name}
        _print(
            "trace",
            f"no ledger row; using --project {explicit.name} to retrieve directly"
            + (f" (ledger unreadable: {ledger_error})" if ledger_error else ""),
        )

    if row is None:
        if ledger_error:
            return (
                f"Could not read the llmCall ledger to locate `{resp_id}` "
                f"({ledger_error}).\n\n"
                "A `resp_` id is a Responses object: it is not retrievable via "
                "/chat/completions/{id}, and OpenAI has no list endpoint for "
                "responses, so the ledger is how its project is found. Either fix the "
                "BigQuery read (GCP_KEY), or pass --project <name> to retrieve it directly."
            )
        return (
            f"No llmCall ledger row for `{resp_id}`.\n\n"
            "Either the id is wrong, or the call predates the ledger. A `resp_` id "
            "cannot be located on OpenAI without knowing its project (there is no "
            "list endpoint for stored responses), so pass --project <name> if you "
            "know which project owns it."
        )

    docs, unfetchable = rs.hydrate([row], key_for_project, with_messages=True)
    if not docs:
        why = (unfetchable[0].get("error") if unfetchable else "unknown")
        return (
            f"Ledger knows `{resp_id}` (project `{row.get('project')}`, prompter "
            f"`{row.get('prompterName')}`) but the stored object could not be "
            f"fetched: {why}\n\n"
            "This is a retrieval failure, not a missing call."
        )

    doc = docs[0]
    project = by_name.get(str(doc.get("_ledger_project") or "").lower()) or Project(
        name=str(row.get("project") or "unknown"),
        project_id="",
        full_name=f"{row.get('project') or 'unknown'} (from ledger)",
    )
    via = "--project" if explicit is not None and not row.get("prompterName") else "the ledger"
    _print("trace", f"resolved {resp_id[:26]}… via {via} -> {project.full_name}")
    if doc.get("_messages_error"):
        # Surface it in the rendered output too, not just on stderr -- a reader
        # scanning the trace should not have to infer that a missing Input
        # section means "fetch failed" rather than "there was no input".
        _print(
            "trace",
            f"input_items unavailable ({doc['_messages_error']}) -- the Input "
            "section below is INCOMPLETE, not empty.",
        )
    return fmt.render_trace(normalize(doc), doc.get("_messages") or [], project=project)


def mode_user(args, client: OpenAIClient, projects: list[Project]) -> str:
    uid = args.user
    if not uid:
        return "--user <firebase-uid> is required."
    since_secs = parse_since(args.since)
    import time as _time
    since_unix = int(_time.time()) - since_secs

    targets: list[Project]
    if args.project:
        project = resolve(args.project, projects)
        if not project:
            s = suggest(args.project, projects)
            return f"Unknown project: {args.project!r}. Did you mean: {', '.join(s)}?"
        targets = [project]
    else:
        targets = projects

    by_project: dict[Project, list[NormalizedCompletion]] = {}
    for project in targets:
        try:
            docs = list(client.list_completions(
                project.project_id, metadata={"user": uid}, since_unix=since_unix))
        except OpenAIError as e:
            _print(f"{project.full_name} skipped", str(e))
            continue
        completions = [normalize(d) for d in docs]
        completions.sort(key=lambda c: c.created or 0, reverse=True)
        if completions:
            by_project[project] = completions

    # Responses-API calls for this user, via the ledger (see _responses_for_focus
    # for why the walk above cannot see them). Honours --project by filtering the
    # grouped result, since the ledger pivot is per-user not per-project.
    by_name = {p.name.lower(): p for p in projects}

    def key_for_project(short: str | None):
        p = by_name.get((short or "").lower())
        return (client._completion_key(p.project_id), p.project_id) if p else (None, None)

    try:
        rows = [
            r for r in rs.rows_for_user(
                uid, prompter=args.prompter, since_unix=since_unix
            )
            if rs._row_unix_ok(r, since_unix)
        ]
        docs, unfetchable = rs.hydrate(rows, key_for_project)
        allowed = {p.name.lower() for p in targets}
        for doc in docs:
            short = str(doc.get("_ledger_project") or "").lower()
            if short not in allowed:
                continue
            project = by_name.get(short)
            if project is None:
                continue
            by_project.setdefault(project, []).append(normalize(doc))
        for project in by_project:
            by_project[project].sort(key=lambda c: c.created or 0, reverse=True)
        if unfetchable:
            _print(
                "user",
                f"WARNING: {len(unfetchable)} Responses object(s) were in the ledger "
                "but could not be fetched -- this view is incomplete, not empty.",
            )
    except rs.ResponsesSourceError as exc:
        _print(
            "user",
            f"WARNING: could not read Responses-API calls ({exc}). Results cover "
            "the Chat-Completions half ONLY.",
        )

    return fmt.render_user(by_project, user_id=uid, since_human=args.since)


def mode_url(args, client: OpenAIClient, projects: list[Project]) -> str:
    decoded = decode_logs_url(args.url)
    if decoded.legacy_braintrust:
        return (
            "This skill migrated off Braintrust to OpenAI stored completions "
            "(PROD-2464). Braintrust UI URLs are no longer decodable.\n"
            "Paste a `https://platform.openai.com/logs/<chatcmpl-id>` URL, a bare "
            "`chatcmpl-...` completion id, or use `--transaction <uuid>` to fan out."
        )
    if not decoded.completion_id:
        return (
            f"Could not extract a completion id from: {args.url!r}\n"
            "Expected an OpenAI Logs URL (https://platform.openai.com/logs/<chatcmpl-id>) "
            "or a bare `chatcmpl-...` id."
        )
    _print("decoded url", f"completion={decoded.completion_id}")
    return mode_trace(args, client, projects, completion_id=decoded.completion_id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # avoid cp1252 surprises on Windows
    args = parse_args(argv)

    if args.lookback:
        _print("note", "--lookback is ignored post-BT migration; OpenAI has no "
                        "server-side time filter.")

    # Mutual-exclusion at the mode level.
    mode_flags = [args.transaction, args.span, args.user, args.url]
    if sum(1 for f in mode_flags if f) > 1:
        print(
            "ERROR: --transaction, --span, --user, and --url are mutually exclusive.",
            file=sys.stderr,
        )
        return 2

    # Auth + project discovery.
    try:
        client = OpenAIClient()
    except AuthError as e:
        print(f"AUTH ERROR: {e}", file=sys.stderr)
        return 3

    try:
        projects = fetch_production_projects(client)
    except AuthError as e:
        print(f"AUTH ERROR: {e}", file=sys.stderr)
        return 3
    except OpenAIError as e:
        print(f"PROJECT DISCOVERY ERROR: {e}", file=sys.stderr)
        return 4
    if not projects:
        print(
            "ERROR: no `[Production]` projects discovered on the OpenAI org. "
            "Confirm the Admin key has Owner role and the org has production projects.",
            file=sys.stderr,
        )
        return 4
    _print("projects", f"discovered {len(projects)} [Production] project(s): "
                       f"{', '.join(p.name for p in projects)}")

    try:
        if args.url:
            out = mode_url(args, client, projects)
        elif args.transaction:
            out = mode_focus(args, client, projects)
        elif args.span:
            out = mode_trace(args, client, projects)
        elif args.user:
            out = mode_user(args, client, projects)
        elif args.project:
            out = mode_triage(args, client, projects)
        else:
            print(
                "ERROR: pick a mode — one of "
                "--project (triage), --transaction (focus), --span (trace), "
                "--user (user pivot), --url (link follow).",
                file=sys.stderr,
            )
            return 2
    except ValueError as e:
        print(f"INPUT ERROR: {e}", file=sys.stderr)
        return 2
    except AuthError as e:
        print(f"AUTH ERROR: {e}", file=sys.stderr)
        return 3
    except OpenAIError as e:
        print(f"QUERY ERROR: {e}", file=sys.stderr)
        return 4

    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
