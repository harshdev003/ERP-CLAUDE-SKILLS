#!/usr/bin/env python3
"""
Save a row to a Braintrust dataset in the `Conversation` project.

Defaults to the `Feedback Conversation` dataset (slug `feedback-conversation`);
pass `--dataset <name-or-slug>` to target a different dataset in the same
project. `--dataset` accepts the display name, the url slug, or a UUID, and
matches case-insensitively against name and slug.

Three sub-modes:

  * `--build --conversation-id <UUID>` -- fetches the production log via BTQL,
                                          builds a row matching the production
                                          shape, writes JSON to `--out` (or
                                          stdout if omitted). Optional
                                          metadata flags pre-fill fields the
                                          caller already knows; anything left
                                          unset is a placeholder the human
                                          fills before insert.

  * `--check-dupe --conversation-id <UUID>` -- queries the chosen dataset for
                                              rows whose
                                              `metadata.conversationId`
                                              matches; prints existing IDs.

  * `--insert --row <path>` -- reads a row file, validates the contract,
                              optionally `--update <row-id>` an existing row
                              or `--allow-duplicate` to insert anyway,
                              prints the row URL on success.

  * `--bulk-insert --rows <file> --dataset <name>` -- loads many prepared rows
                              (JSONL or a JSON array) into a dataset in
                              `--project`, creating the dataset with
                              `--create-if-missing`. Rows whose `--dedupe-key`
                              value already exists are skipped (counted), or
                              replaced with `--replace-existing`. `--dry-run`
                              prints the plan and writes nothing.

Used by the `data:save-to-braintrust-dataset` skill -- see SKILL.md for the
human-in-the-loop orchestration.

Env:
    BRAINTRUST_API_KEY      -- required
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from typing import Any

import requests

BT_API = "https://api.braintrust.dev"
ORG_NAME = "Aura Health 1"
PROJECT_NAME = "Conversation"
PROJECT_ID = "3bee3818-a9b4-40c4-ac14-9e274c52c560"
DEFAULT_DATASET_SLUG = "feedback-conversation"

# Looks-like-a-UUID check used by `resolve_dataset` to short-circuit lookups.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# -------- HTTP --------


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def btql(api_key: str, query: str) -> list[dict[str, Any]]:
    r = requests.post(
        f"{BT_API}/btql",
        headers=_headers(api_key),
        json={"query": query, "fmt": "json"},
        timeout=120,
    )
    if not r.ok:
        sys.exit(f"Braintrust BTQL {r.status_code}: {r.text}")
    return r.json().get("data", [])


# -------- dataset resolution --------


def resolve_dataset(api_key: str, name_or_slug: str) -> dict[str, str]:
    """Resolve a dataset name / slug / UUID to its canonical record.

    Returns `{ id, name, slug }`. Looks up datasets in `PROJECT_NAME`
    (`Conversation`) and matches case-insensitively against both the display
    name and the `url_slug`. Bare UUIDs short-circuit straight to a GET-by-id.
    """
    needle = (name_or_slug or "").strip()
    if not needle:
        sys.exit("--dataset requires a non-empty name or slug")

    if _UUID_RE.match(needle):
        r = requests.get(
            f"{BT_API}/v1/dataset/{needle}",
            headers=_headers(api_key),
            timeout=30,
        )
        if not r.ok:
            sys.exit(f"Braintrust dataset {needle} not found ({r.status_code}): {r.text}")
        d = r.json()
        return {"id": d["id"], "name": d["name"], "slug": d.get("url_slug") or ""}

    # List datasets in the Conversation project; match name OR slug, case-insensitive.
    r = requests.get(
        f"{BT_API}/v1/dataset",
        headers=_headers(api_key),
        params={"project_name": PROJECT_NAME, "org_name": ORG_NAME, "limit": 200},
        timeout=30,
    )
    if not r.ok:
        sys.exit(f"Braintrust dataset list {r.status_code}: {r.text}")
    needle_lower = needle.lower()
    for d in r.json().get("objects", []):
        if d.get("name", "").lower() == needle_lower or (d.get("url_slug") or "").lower() == needle_lower:
            return {"id": d["id"], "name": d["name"], "slug": d.get("url_slug") or ""}
    sys.exit(
        f"No dataset matching {name_or_slug!r} in project {PROJECT_NAME!r}. "
        "Check the spelling, or pass the dataset UUID directly."
    )


def dataset_fetch(api_key: str, dataset_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    r = requests.get(
        f"{BT_API}/v1/dataset/{dataset_id}/fetch",
        headers=_headers(api_key),
        params={"limit": limit},
        timeout=60,
    )
    if not r.ok:
        sys.exit(f"Braintrust dataset fetch {r.status_code}: {r.text}")
    return r.json().get("events", [])


def dataset_insert(
    api_key: str, dataset_id: str, events: list[dict[str, Any]]
) -> list[str]:
    r = requests.post(
        f"{BT_API}/v1/dataset/{dataset_id}/insert",
        headers=_headers(api_key),
        json={"events": events},
        timeout=60,
    )
    if not r.ok:
        sys.exit(f"Braintrust dataset insert {r.status_code}: {r.text}")
    return r.json().get("row_ids", [])


# -------- log -> row construction --------


def fetch_conversation_spans(api_key: str, conversation_id: str) -> list[dict[str, Any]]:
    """Pull every span for one conversation. Case-insensitive on UUID."""
    cid = conversation_id.strip()
    candidates = {cid.upper(), cid.lower()}
    clauses = []
    for c in candidates:
        clauses.append(f"metadata.conversationId = '{c}'")
        clauses.append(f"metadata.conversation_id = '{c}'")
    where = " OR ".join(clauses)
    q = f"""
        SELECT created,
               input,
               output,
               metadata,
               scores,
               span_attributes,
               span_id
        FROM project_logs('{PROJECT_ID}')
        WHERE {where}
        ORDER BY created ASC
    """.strip()
    return btql(api_key, q)


# The parent task span carries `metadata.contextInstructions`; its `llm`
# children never do. The name tracks the API generation -- `streamChat` on Chat
# Completions, `streamResponse` on the Responses API.
PARENT_SPAN_NAMES = frozenset({"streamChat", "streamResponse"})


def can_build_row(span: dict[str, Any]) -> bool:
    """True when a span carries BOTH halves a dataset row needs."""
    context_instructions = (span.get("metadata") or {}).get("contextInstructions")
    return (
        isinstance(span.get("input"), list)
        and isinstance(context_instructions, str)
        and bool(context_instructions.strip())
    )


def pick_source_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the canonical span for row construction.

    A row needs the conversation turns (`input`) and
    `metadata.contextInstructions` together, and only the parent task span
    carries both. Selection is therefore by what a span carries; the name is a
    preference among usable spans, never the qualifying test, so a span renamed
    by a future API migration still resolves to one that can build a row.
    """
    usable = [s for s in spans if can_build_row(s)]
    if usable:
        parents = [
            s for s in usable
            if (s.get("span_attributes") or {}).get("name") in PARENT_SPAN_NAMES
        ]
        return (parents or usable)[-1]
    # Nothing carries both halves. Fall back to the name/type preference so the
    # caller gets the specific downstream error about what the span is missing.
    parents = [
        s for s in spans
        if (s.get("span_attributes") or {}).get("name") in PARENT_SPAN_NAMES
    ]
    if parents:
        return parents[-1]
    llms = [s for s in spans if (s.get("span_attributes") or {}).get("type") == "llm"]
    return llms[-1] if llms else None


def detect_rating(spans: list[dict[str, Any]]) -> str:
    """Return `positive` | `negative` | `neutral` | `unknown`."""
    rating_span = next(
        (s for s in spans if isinstance(s.get("scores"), dict) and "User Rating" in s["scores"]),
        None,
    )
    if rating_span is None:
        return "unknown"
    score = rating_span["scores"].get("User Rating")
    if score == 1:
        return "positive"
    if score == 0:
        return "negative"
    return "neutral"


def trim_messages(input_field: Any) -> list[dict[str, str]]:
    """Coerce the span's `input` into a clean messages array.

    Drops system-role entries -- production logs sometimes carry the
    rendered system prompt as the first input element; the dataset row
    should only contain conversation turns since the eval prompt template
    re-injects the system instructions itself.
    """
    if not isinstance(input_field, list):
        return []
    out: list[dict[str, str]] = []
    for m in input_field:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            out.append({"role": role, "content": content})
    return out


def build_row(
    spans: list[dict[str, Any]],
    *,
    conversation_id: str,
    case_id: str | None,
    pattern: str | None,
    severity: str | None,
    observed_rating: str | None,
    source: str | None,
    source_date: str | None,
    expected: str | None,
    tags: list[str],
) -> dict[str, Any]:
    """Construct a dataset row from spans + caller-supplied metadata.

    Fields the caller doesn't provide are stamped with a `_TODO_` placeholder
    so the human can fill them in before insert. Validation on insert will
    refuse any row that still has placeholders in load-bearing fields.
    """
    if not spans:
        sys.exit(f"No spans found for conversation {conversation_id!r}.")

    source_span = pick_source_span(spans)
    if source_span is None:
        sys.exit(
            f"Could not pick a source span for conversation {conversation_id!r} "
            "-- none had both `input` and `metadata.contextInstructions`."
        )

    source_md = source_span.get("metadata") or {}
    context_instructions = source_md.get("contextInstructions")
    if not isinstance(context_instructions, str) or not context_instructions.strip():
        sys.exit(
            f"Source span for {conversation_id!r} has empty `metadata.contextInstructions`. "
            "Pick a different span or update the row manually."
        )

    messages = trim_messages(source_span.get("input"))
    if not messages:
        sys.exit(
            f"Source span for {conversation_id!r} has no user/assistant messages. "
            "Check that the spans returned actually represent a chat turn."
        )

    rating = observed_rating or detect_rating(spans)

    prompter_name = source_md.get("prompterName") or "unknown"
    pattern_for_tags = (pattern or "TODO_pattern").lower().replace(" ", "-")
    tag_set = sorted({prompter_name, pattern_for_tags, *tags})

    row: dict[str, Any] = {
        "input": messages,
        "expected": expected or "_TODO_expected_: pass criteria for this case -- LLM-judge friendly text describing what the assistant must / must not do.",
        "metadata": {
            "contextInstructions": context_instructions,
            "prompterName": prompter_name,
            "conversationId": source_md.get("conversationId") or conversation_id,
            "dialogueId": source_md.get("dialogueId"),
            "userId": source_md.get("userId"),
            "version": source_md.get("version"),
            "case_id": case_id or "_TODO_case_id_",
            "pattern": pattern or "_TODO_pattern_",
            "severity": severity or "_TODO_severity_",
            "observed_rating": rating,
            "source": source or "_TODO_source_",
            "source_date": source_date or date.today().isoformat(),
        },
        "tags": tag_set,
    }
    return row


# -------- validation --------


REQUIRED_METADATA_FIELDS = (
    "contextInstructions",
    "prompterName",
    "conversationId",
    "case_id",
    "pattern",
    "severity",
    "observed_rating",
    "source",
    "source_date",
)


def validate_row(row: dict[str, Any]) -> None:
    """Raise SystemExit with a helpful message if the row is not insertable."""
    errs: list[str] = []
    if not isinstance(row.get("input"), list) or not row["input"]:
        errs.append("input: must be a non-empty list of {role, content} messages")
    else:
        for i, m in enumerate(row["input"]):
            if not isinstance(m, dict) or m.get("role") not in {"user", "assistant"}:
                errs.append(f"input[{i}]: role must be 'user' or 'assistant'")
            if not isinstance(m.get("content"), str):
                errs.append(f"input[{i}]: content must be a string")
    md = row.get("metadata") or {}
    for k in REQUIRED_METADATA_FIELDS:
        v = md.get(k)
        if v is None or v == "":
            errs.append(f"metadata.{k}: required")
        elif isinstance(v, str) and v.startswith("_TODO_"):
            errs.append(f"metadata.{k}: still a placeholder ({v!r})")
    expected = row.get("expected")
    if not isinstance(expected, str) or not expected.strip() or expected.startswith("_TODO_"):
        errs.append("expected: required (pass criteria)")
    if errs:
        sys.exit("Row validation failed:\n  - " + "\n  - ".join(errs))


# -------- dedupe --------


def find_existing(
    api_key: str, dataset_id: str, conversation_id: str
) -> list[dict[str, Any]]:
    """Return all rows whose metadata.conversationId matches (case-insensitive)."""
    cid_lower = conversation_id.strip().lower()
    rows = dataset_fetch(api_key, dataset_id)
    matches = []
    for r in rows:
        md = r.get("metadata") or {}
        existing = md.get("conversationId") or md.get("conversation_id") or ""
        if existing.lower() == cid_lower:
            matches.append({
                "id": r.get("id"),
                "case_id": md.get("case_id"),
                "pattern": md.get("pattern"),
                "prompterName": md.get("prompterName"),
            })
    return matches


# -------- sub-commands --------


def cmd_build(args: argparse.Namespace, api_key: str) -> None:
    dataset = resolve_dataset(api_key, args.dataset)
    spans = fetch_conversation_spans(api_key, args.conversation_id)
    row = build_row(
        spans,
        conversation_id=args.conversation_id,
        case_id=args.case_id,
        pattern=args.pattern,
        severity=args.severity,
        observed_rating=args.observed_rating,
        source=args.source,
        source_date=args.source_date,
        expected=args.expected,
        tags=list(args.tag or []),
    )
    body = json.dumps(row, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w") as f:
            f.write(body)
        print(f"Wrote draft row to {args.out}", file=sys.stderr)
        # Also print a compact summary to stdout for the orchestrating skill.
        md = row["metadata"]
        print(json.dumps({
            "out": args.out,
            "dataset": dataset,
            "conversationId": md["conversationId"],
            "prompterName": md["prompterName"],
            "observed_rating": md["observed_rating"],
            "turn_count": len(row["input"]),
            "context_chars": len(md["contextInstructions"]),
            "placeholders": [
                k for k in REQUIRED_METADATA_FIELDS
                if isinstance(md.get(k), str) and md[k].startswith("_TODO_")
            ] + ([
                "expected"
            ] if row["expected"].startswith("_TODO_") else []),
        }, indent=2))
    else:
        print(body)


def cmd_check_dupe(args: argparse.Namespace, api_key: str) -> None:
    dataset = resolve_dataset(api_key, args.dataset)
    matches = find_existing(api_key, dataset["id"], args.conversation_id)
    print(json.dumps({
        "dataset": dataset,
        "conversationId": args.conversation_id,
        "existing_count": len(matches),
        "existing": matches,
    }, indent=2))


def cmd_insert(args: argparse.Namespace, api_key: str) -> None:
    dataset = resolve_dataset(api_key, args.dataset)

    with open(args.row) as f:
        row = json.load(f)
    validate_row(row)

    conversation_id = (row.get("metadata") or {}).get("conversationId")
    if not conversation_id:
        sys.exit("Row metadata.conversationId is required for idempotency check.")

    existing = find_existing(api_key, dataset["id"], conversation_id)
    if existing and not args.update and not args.allow_duplicate:
        ids = ", ".join(e["id"] for e in existing)
        sys.exit(
            f"Found {len(existing)} existing row(s) for conversation "
            f"{conversation_id} in dataset {dataset['name']!r} (ids: {ids}). "
            "Pass --update <row-id> to replace one, or --allow-duplicate to "
            "insert anyway."
        )

    if args.update:
        row["id"] = args.update

    ids = dataset_insert(api_key, dataset["id"], [row])
    if not ids:
        sys.exit("Insert returned no row ids.")
    new_id = ids[0]
    slug = dataset["slug"] or dataset["id"]
    url = (
        f"https://www.braintrust.dev/app/Aura/p/{PROJECT_NAME}/datasets/"
        f"{slug}?r={new_id}"
    )
    print(json.dumps({
        "row_id": new_id,
        "operation": "update" if args.update else "insert",
        "url": url,
        "dataset": dataset,
    }, indent=2))


# -------- bulk load --------


def lookup_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def read_rows_file(path: str) -> list[dict[str, Any]]:
    """A JSON array, or JSONL (one row object per line)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    stripped = text.lstrip()
    if stripped.startswith("["):
        rows = json.loads(stripped)
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not all(isinstance(r, dict) for r in rows):
        sys.exit(f"{path}: every row must be a JSON object")
    return rows


def validate_bulk_row(row: dict[str, Any], i: int) -> list[str]:
    """The minimum a row needs to be runnable: an input the prompt template can take."""
    errs = []
    unknown = set(row) - {"id", "input", "expected", "metadata", "tags"}
    if unknown:
        errs.append(f"row {i}: unknown top-level keys {sorted(unknown)}")
    inp = row.get("input")
    if isinstance(inp, list):
        for j, m in enumerate(inp):
            if not (isinstance(m, dict) and m.get("role") and "content" in m):
                errs.append(f"row {i}: input[{j}] is not a {{role, content}} message")
                break
    elif not isinstance(inp, dict):
        errs.append(f"row {i}: input must be a messages list or an object")
    if "metadata" in row and not isinstance(row["metadata"], dict):
        errs.append(f"row {i}: metadata must be an object")
    return errs


def resolve_project_id(api_key: str, project: str) -> str:
    r = requests.get(f"{BT_API}/v1/project", headers=_headers(api_key),
                     params={"project_name": project}, timeout=30)
    if not r.ok or not r.json().get("objects"):
        sys.exit(f"Braintrust project {project!r} not found ({r.status_code}).")
    return r.json()["objects"][0]["id"]


def find_dataset(api_key: str, project: str, name: str) -> dict[str, Any] | None:
    r = requests.get(f"{BT_API}/v1/dataset", headers=_headers(api_key),
                     params={"project_name": project, "dataset_name": name}, timeout=30)
    if not r.ok:
        sys.exit(f"Braintrust dataset lookup {r.status_code}: {r.text}")
    objs = r.json().get("objects") or []
    return objs[0] if objs else None


def fetch_all_rows(api_key: str, dataset_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = None
    while True:
        body: dict[str, Any] = {"limit": 500}
        if cursor:
            body["cursor"] = cursor
        r = requests.post(f"{BT_API}/v1/dataset/{dataset_id}/fetch", headers=_headers(api_key),
                          json=body, timeout=120)
        if not r.ok:
            sys.exit(f"Braintrust dataset fetch {r.status_code}: {r.text}")
        page = r.json()
        rows += page.get("events") or []
        cursor = page.get("cursor")
        if not cursor or not page.get("events"):
            return rows


def plan_bulk(rows: list[dict[str, Any]], existing: list[dict[str, Any]], key: str,
              replace: bool) -> dict[str, Any]:
    """Split file rows into insert / replace / skip by their dedupe-key value.

    Pure, so the dedupe rule is testable. Rows with no key value are an error:
    without one a re-run would duplicate them, which is the failure the key exists
    to prevent.
    """
    seen: dict[str, int] = {}
    errors: list[str] = []
    for i, row in enumerate(rows):
        errors += validate_bulk_row(row, i)
        value = lookup_path(row, key)
        if value in (None, ""):
            errors.append(f"row {i}: no value at {key}")
            continue
        if str(value) in seen:
            errors.append(f"row {i}: {key}={value!r} repeats row {seen[str(value)]}")
        seen.setdefault(str(value), i)
    by_key = {str(lookup_path(r, key)): r["id"] for r in existing if lookup_path(r, key) not in (None, "")}
    insert, replace_rows, skip = [], [], []
    for row in rows:
        # A file row's own `id` is dropped: Braintrust upserts on id, so an id
        # copied from an export would overwrite whichever record holds it, not
        # the one the dedupe key names. The key alone decides insert vs replace.
        value = str(lookup_path(row, key))
        row = {k: v for k, v in row.items() if k != "id"}
        if value in by_key:
            if replace:
                replace_rows.append({**row, "id": by_key[value]})
            else:
                skip.append(value)
        else:
            insert.append(row)
    return {"errors": errors, "insert": insert, "replace": replace_rows, "skip": skip}


def cmd_bulk_insert(args: argparse.Namespace, api_key: str) -> None:
    rows = read_rows_file(args.rows)
    dataset = find_dataset(api_key, args.project, args.dataset)
    existing = fetch_all_rows(api_key, dataset["id"]) if dataset else []
    plan = plan_bulk(rows, existing, args.dedupe_key, args.replace_existing)
    summary = {
        "project": args.project,
        "dataset": dataset["name"] if dataset else args.dataset,
        "dataset_exists": bool(dataset),
        "rows_in_file": len(rows),
        "existing_rows": len(existing),
        "insert": len(plan["insert"]),
        "replace": len(plan["replace"]),
        "skipped_existing": len(plan["skip"]),
        "errors": plan["errors"],
    }
    if plan["errors"]:
        print(json.dumps(summary, indent=2))
        sys.exit("Fix the row errors above; nothing was written.")
    if not dataset and not args.create_if_missing:
        print(json.dumps(summary, indent=2))
        sys.exit(f"Dataset {args.dataset!r} does not exist in {args.project!r}; "
                 "pass --create-if-missing to create it.")
    if args.dry_run:
        print(json.dumps({**summary, "dry_run": True}, indent=2))
        return
    if not dataset:
        r = requests.post(f"{BT_API}/v1/dataset", headers=_headers(api_key), json={
            "project_id": resolve_project_id(api_key, args.project),
            "name": args.dataset, "description": args.description,
        }, timeout=30)
        if not r.ok:
            sys.exit(f"Braintrust dataset create {r.status_code}: {r.text}")
        dataset = r.json()
    events = plan["insert"] + plan["replace"]
    written: list[str] = []
    for i in range(0, len(events), 100):
        written += dataset_insert(api_key, dataset["id"], events[i:i + 100])
    print(json.dumps({**summary, "dataset_id": dataset["id"], "written": len(written)}, indent=2))


# -------- entrypoint --------


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=(
            "Save a row to a Braintrust dataset in the Conversation project "
            "(default: Feedback Conversation; override with --dataset)."
        )
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true",
                     help="Construct a row JSON from a production conversation log.")
    mode.add_argument("--check-dupe", action="store_true",
                     help="List existing rows in the dataset for this conversation.")
    mode.add_argument("--bulk-insert", action="store_true",
                     help="Load many prepared rows (JSONL / JSON array) into a dataset.")
    mode.add_argument("--insert", action="store_true",
                     help="Validate + insert a prepared row file.")

    # --build / --check-dupe args
    ap.add_argument("--conversation-id", help="Production conversation UUID.")

    # --build metadata flags (all optional; missing → placeholder)
    ap.add_argument("--case-id", help="metadata.case_id (e.g. qp_exit_tokens_v1_thanks_bye).")
    ap.add_argument("--pattern", help="metadata.pattern (e.g. qp_entry_on_exit_tokens).")
    ap.add_argument("--severity", choices=["low", "medium", "high"], help="metadata.severity.")
    ap.add_argument("--observed-rating", choices=["positive", "negative", "neutral", "unknown"],
                   help="metadata.observed_rating. Auto-detected from rating span if omitted.")
    ap.add_argument("--source", help="metadata.source (Slack permalink or Braintrust UI URL).")
    ap.add_argument("--source-date", help="metadata.source_date (YYYY-MM-DD). Defaults to today.")
    ap.add_argument("--expected",
                   help="Pass criteria text for the LLM-judge scorer. Required before insert.")
    ap.add_argument("--tag", action="append", help="Extra tag(s) to attach (repeatable).")
    ap.add_argument("--out", help="Write the built row JSON here (default: stdout).")

    # --insert args
    ap.add_argument("--row", help="Path to row JSON file for --insert.")
    ap.add_argument("--update", help="Existing row id to overwrite (with --insert).")
    ap.add_argument("--allow-duplicate", action="store_true",
                   help="Insert even if a row already exists for this conversationId.")

    # --bulk-insert args
    ap.add_argument("--rows", help="JSONL or JSON-array file of rows for --bulk-insert.")
    ap.add_argument("--project", default=PROJECT_NAME,
                   help=f"Project for --bulk-insert (default {PROJECT_NAME!r}).")
    ap.add_argument("--create-if-missing", action="store_true",
                   help="--bulk-insert: create the dataset when it does not exist.")
    ap.add_argument("--description", default="", help="--bulk-insert: description for a new dataset.")
    ap.add_argument("--dedupe-key", default="metadata.case_id",
                   help="--bulk-insert: dotted path unique per row (default metadata.case_id).")
    ap.add_argument("--replace-existing", action="store_true",
                   help="--bulk-insert: overwrite rows whose dedupe key already exists (default: skip).")
    ap.add_argument("--dry-run", action="store_true", help="--bulk-insert: print the plan, write nothing.")

    # Target dataset (shared by all sub-commands).
    ap.add_argument(
        "--dataset",
        default=None,
        help=(
            "Target dataset in the Conversation project. Accepts the dataset "
            "display name ('Feedback Conversation'), its url slug "
            f"('feedback-conversation'), or a UUID. Default: {DEFAULT_DATASET_SLUG!r}. "
            "Case-insensitive."
        ),
    )

    args = ap.parse_args()
    if args.bulk_insert:
        # No default here: a bulk load into the shared feedback dataset by omission
        # would be hard to undo.
        if not args.dataset or not args.rows:
            sys.exit("--bulk-insert requires --dataset <exact name> and --rows <file>")
    elif not args.dataset:
        args.dataset = DEFAULT_DATASET_SLUG

    api_key = os.environ.get("BRAINTRUST_API_KEY")
    if not api_key:
        sys.exit("BRAINTRUST_API_KEY not set")

    if args.build:
        if not args.conversation_id:
            sys.exit("--build requires --conversation-id")
        cmd_build(args, api_key)
    elif args.check_dupe:
        if not args.conversation_id:
            sys.exit("--check-dupe requires --conversation-id")
        cmd_check_dupe(args, api_key)
    elif args.bulk_insert:
        cmd_bulk_insert(args, api_key)
    elif args.insert:
        if not args.row:
            sys.exit("--insert requires --row <path>")
        cmd_insert(args, api_key)


if __name__ == "__main__":
    main()
