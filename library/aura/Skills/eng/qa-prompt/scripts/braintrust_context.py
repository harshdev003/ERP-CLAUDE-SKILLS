"""Resolve the conversation context payload for a QA run.

Two input shapes — both return the same `Context` shape:

- dataset:<slug>:<row_id>  — fetch the dataset row via /v1/dataset/{id}/fetch.
- paste                    — open $EDITOR (or read stdin) with a JSON template.

See `Tools/Braintrust` in AI Knowledge for the dataset row contract
(`input` = chat messages array OR a JSON object of template vars,
`metadata.contextInstructions` = markdown, optional `expected` = comparison plan).

Dataset rows come in two shapes depending on the prompt under test:

- **Chat-messages input** (Aurie dialogue prompts) — `row.input` is a list of
  `{role, content}` entries. The rendered template splices them in at
  `{{input}}`. This is the original / common case.
- **Structured input** (Memory Sorting, Quick Processing classifier, Intent
  Router, etc.) — `row.input` is a dict. Each top-level key becomes a
  template variable accessible as `{{<key>}}` in the prompt; there are no
  chat messages to splice. The original prompt's `expected` (if present) is
  carried through to state so plan-comparison scorers can grade against it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any

import requests

from braintrust_prompt import BRAINTRUST_API, BraintrustError

# Source: Tools/Braintrust in AI Knowledge. Used as the default slug-lookup
# project when no `--dataset-id` is provided — keeps the original
# `dataset:<slug>:<row-id>` ergonomics for Conversation-project rows.
CONVERSATION_PROJECT_ID = "3bee3818-a9b4-40c4-ac14-9e274c52c560"


@dataclass
class Context:
    """The resolved `{input, metadata}` payload to feed into the template.

    Also carries a `source` description string used in the QA report header.

    Two mutually exclusive input shapes:
      - `input_messages` non-empty: chat-messages input (spliced at `{{input}}`).
      - `template_vars` non-empty: structured input (each key is a top-level
        template variable; no message splicing).

    Both shapes share `metadata` (which contains `contextInstructions` etc.).
    `expected` carries the dataset row's `expected` field (if any) for
    plan-comparison scorers.
    """
    input_messages: list[dict[str, Any]]
    metadata: dict[str, Any]
    source: str  # human-readable description, e.g. "dataset:feedback-conversation:abc123"
    warnings: list[str]
    template_vars: dict[str, Any] | None = None
    expected: Any = None
    row_id: str | None = None


def _api_key() -> str:
    key = os.environ.get("BRAINTRUST_API_KEY")
    if not key:
        raise BraintrustError("BRAINTRUST_API_KEY is not set.")
    return key


def _fetch_row(dataset_id: str, row_id: str, source_label: str) -> dict[str, Any]:
    """Fetch a single row by id from a known dataset_id.

    Shared by `resolve_dataset` (slug-resolved) and `resolve_dataset_by_id`
    (explicit). `source_label` is the human-readable label for error messages
    (e.g. `"dataset slug=foo"` or `"dataset id=abc-123"`).
    """
    fetch_r = requests.post(
        f"{BRAINTRUST_API}/dataset/{dataset_id}/fetch",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        json={"limit": 1000},  # row count is small for our datasets today
        timeout=30,
    )
    if fetch_r.status_code != 200:
        raise BraintrustError(
            f"POST /dataset/{dataset_id}/fetch failed: {fetch_r.text[:300]}"
        )
    fetch_body = fetch_r.json() or {}
    events = fetch_body.get("events") or []
    matching = [e for e in events if e.get("id") == row_id]
    if not matching:
        # Try matching by ._row_id (Braintrust internal) or _xact_id fallbacks.
        matching = [e for e in events if e.get("_row_id") == row_id]
    if not matching:
        raise BraintrustError(
            f"No row with id={row_id!r} in {source_label}. Found {len(events)} rows total."
        )
    return matching[0]


def _row_to_context(row: dict[str, Any], source: str, row_id: str) -> Context:
    """Validate a fetched row and build a Context.

    Auto-detects input shape:
      - `list` → chat-messages input (input_messages set, template_vars empty)
      - `dict` → structured input (template_vars set, input_messages empty)
      - anything else → error

    Always carries `row.expected` through to `Context.expected` when present so
    plan-comparison scorers can grade against it.
    """
    raw_input = row.get("input")
    metadata = row.get("metadata") or {}
    expected = row.get("expected")

    if isinstance(raw_input, list):
        return Context(
            input_messages=raw_input,
            metadata=metadata,
            source=source,
            warnings=[],
            template_vars=None,
            expected=expected,
            row_id=row_id,
        )
    if isinstance(raw_input, dict):
        return Context(
            input_messages=[],
            metadata=metadata,
            source=source,
            warnings=[],
            template_vars=raw_input,
            expected=expected,
            row_id=row_id,
        )
    raise BraintrustError(
        f"Dataset row {row_id!r} has input of type {type(raw_input).__name__}; "
        f"expected list (chat-messages input) or dict (structured input). "
        f"See `Tools/Braintrust` for the dataset row contract."
    )


def resolve_dataset(spec: str, dataset_id: str | None = None) -> Context:
    """Fetch a single row from a Braintrust dataset.

    Two modes:
      - `dataset_id` is None — spec is `<slug>:<row-id>`; the dataset is
        looked up by slug in the Conversation project (current default).
      - `dataset_id` is set — spec is `<row-id>` (or `<slug>:<row-id>`; the
        slug is then ignored). The dataset is fetched directly by id.

    The `--dataset-id` path bypasses the project-scoped slug lookup so
    datasets in any project (Memory, Quick Processing, etc.) can be QA'd
    without changing the skill's default behavior.
    """
    if dataset_id:
        # Spec is allowed to be `<row-id>` or `<slug>:<row-id>` (slug ignored).
        if ":" in spec:
            _slug, _, row_id = spec.partition(":")
        else:
            row_id = spec
        if not row_id:
            raise BraintrustError(
                f"--dataset-id was set but no row id was given in --context "
                f"(got {spec!r}). Pass `dataset:<row-id>` or "
                f"`dataset:<slug>:<row-id>` (slug ignored when --dataset-id is set)."
            )
        row = _fetch_row(dataset_id, row_id, f"dataset id={dataset_id}")
        return _row_to_context(row, f"dataset:{dataset_id}:{row_id}", row_id)

    if ":" not in spec:
        raise BraintrustError(
            f"--context dataset:<slug>:<row-id> expected; got dataset:{spec!r}. "
            f"(Tip: pass --dataset-id <uuid> to address a dataset in any project "
            f"by id instead of slug.)"
        )
    slug, _, row_id = spec.partition(":")
    if not slug or not row_id:
        raise BraintrustError(
            f"dataset spec must be <slug>:<row-id>; got slug={slug!r} row_id={row_id!r}"
        )

    # Resolve slug → dataset_id by listing datasets in the Conversation project.
    list_r = requests.get(
        f"{BRAINTRUST_API}/dataset",
        params={"project_id": CONVERSATION_PROJECT_ID, "slug": slug},
        headers={"Authorization": f"Bearer {_api_key()}"},
        timeout=30,
    )
    if list_r.status_code != 200:
        raise BraintrustError(f"GET /dataset?slug={slug} failed: {list_r.text[:300]}")
    body = list_r.json() or {}
    objects = body.get("objects") or []
    if not objects:
        raise BraintrustError(
            f"No dataset with slug={slug!r} in the Conversation project. "
            f"If the dataset lives in another project, pass --dataset-id <uuid> "
            f"explicitly (resolve via Braintrust UI URL or `resolve_object`)."
        )
    resolved_id = objects[0].get("id")
    if not resolved_id:
        raise BraintrustError(f"Dataset list response missing id field: {body}")

    row = _fetch_row(resolved_id, row_id, f"dataset slug={slug!r}")
    return _row_to_context(row, f"dataset:{slug}:{row_id}", row_id)


_PASTE_TEMPLATE = """{
  "input": [
    {"role": "user", "content": "Hi Aurie"}
  ],
  "metadata": {
    "contextInstructions": "## Context\\n### Current Conversation Summary\\n  #### <Title>  (Started: ...; Summary: ...)\\n### User Information\\n  #### User Profile\\n    ##### Identity and Life Context\\n    ..."
  }
}
"""


def resolve_paste() -> Context:
    """Open $EDITOR (or read stdin if not a TTY) with a JSON template.

    User edits, saves, returns. We validate and produce a Context.
    """
    if sys.stdin.isatty():
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".json", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(_PASTE_TEMPLATE)
            tf.flush()
            path = tf.name
        try:
            subprocess.run([editor, path], check=True)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    else:
        content = sys.stdin.read()

    return resolve_paste_from_string(content)


def resolve_paste_from_string(content: str) -> Context:
    """Parse a paste-shape JSON string directly into a Context.

    Same validation rules as `resolve_paste()` but skips the editor / stdin
    dance. Used by `setup_run.py --no-context` to synthesize an empty
    payload internally, and by `setup_sweep.py` to mirror one context
    across N per-scenario state files without re-prompting per scenario.

    Supports both chat-messages input (list) and structured input (dict)
    on the `input` key — same auto-detect rule as dataset rows.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise BraintrustError(
            f"Pasted payload is not valid JSON: {e}\n\n"
            f"Expected shape:\n{_PASTE_TEMPLATE}"
        ) from e

    raw_input = data.get("input")
    metadata = data.get("metadata") or {}
    expected = data.get("expected")

    if isinstance(raw_input, list):
        return Context(
            input_messages=raw_input,
            metadata=metadata,
            source="paste",
            warnings=[],
            template_vars=None,
            expected=expected,
        )
    if isinstance(raw_input, dict):
        return Context(
            input_messages=[],
            metadata=metadata,
            source="paste",
            warnings=[],
            template_vars=raw_input,
            expected=expected,
        )
    raise BraintrustError(
        f"Pasted payload `input` must be a list (chat messages) or a dict "
        f"(template variables for structured-input prompts); got "
        f"{type(raw_input).__name__}."
    )


def resolve(spec: str, dataset_id: str | None = None) -> Context:
    """Top-level dispatch from --context <spec>.

    When `dataset_id` is provided, the slug-lookup path is bypassed and the
    spec is interpreted as just the row id (`dataset:<row-id>`). Otherwise
    the legacy `dataset:<slug>:<row-id>` syntax applies (slug looked up in
    the Conversation project).
    """
    if spec == "paste":
        return resolve_paste()
    if spec.startswith("dataset:"):
        return resolve_dataset(spec[len("dataset:"):], dataset_id=dataset_id)
    raise BraintrustError(
        f"Unrecognized --context value {spec!r}. "
        f"Expected `dataset:<slug>:<row-id>` or `paste`."
    )
