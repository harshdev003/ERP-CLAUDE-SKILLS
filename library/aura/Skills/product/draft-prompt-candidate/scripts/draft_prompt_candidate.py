#!/usr/bin/env python3
"""Turn an approved candidate into a labelled Braintrust prompt version.

Takes the new prompt body, creates a **new version** of a Braintrust prompt with a
description saying what changed, and records the version on the Notion row, moving
it `approved` → `drafted`.

Two things this deliberately does not do. It does not touch production — that is
the ship leg, after Gate 2. And it does not compose the change: the body comes in
as a file a human wrote, because a prompt is a layered behavioural system and the
one thing the process document is unambiguous about is that a human holds final
editorial authority over every word.

Self-contained per AINative principle 1: `notion_rest.py` is a per-skill copy.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import quote
from datetime import date
from typing import Any, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import notion_rest  # noqa: E402

AURIE_CONVERSATIONS_DS = "3e375e23-af20-81d5-b99a-000b1b861edc"
BRAINTRUST_API = "https://api.braintrust.dev/v1"
CONVERSATION_PROJECT = "3bee3818-a9b4-40c4-ac14-9e274c52c560"

DRAFTABLE = {"approved"}


# ---- Braintrust -----------------------------------------------------------

def _bt_key() -> str:
    key = os.environ.get("BRAINTRUST_API_KEY")
    if not key:
        raise SystemExit("BRAINTRUST_API_KEY is not set")
    return key


def _bt(method: str, path: str, payload: Optional[dict] = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BRAINTRUST_API + path, data=data, method=method,
        headers={"Authorization": "Bearer " + _bt_key(),
                 "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Braintrust {method} {path} → {e.code}\n{e.read().decode()[:600]}")


def get_prompt(prompt_id: str) -> dict[str, Any]:
    return _bt("GET", f"/prompt/{prompt_id}")


def prompt_url(project_id: str, prompt_id: str) -> str:
    """A link that actually opens the version in the Braintrust UI.

    The app routes on the ORG NAME and the PROJECT NAME, both url-encoded — not on
    their UUIDs, which is what a naive link built from the ids this script already
    holds would use. Both names are resolved from the API rather than hardcoded, so
    a rename does not silently produce dead links on every future row.

    The prompt itself is addressed by UUID: a slug-based prompt link does not open
    the version.
    """
    project = _bt("GET", f"/project/{project_id}")
    org = _bt("GET", f"/organization/{project['org_id']}")
    return ("https://www.braintrust.dev/app/"
            f"{quote(org['name'])}/p/{quote(project['name'])}/prompts/{prompt_id}")


def build_prompt_data(base: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    """The base's prompt_data with only the messages replaced.

    Everything else is carried across — `options`, `tool_functions`, and
    `prompt.tools` and its siblings. Rebuilding prompt_data from messages and
    options alone silently drops the tools, and the version that the evals and
    Gate 2 then see is a materially different prompt from the one that was
    approved. The coach base carries two tool_functions and a prompt.tools list.

    `origin` is the one field deliberately NOT carried: it records which prompt and
    version this was copied from, so duplicating it onto a new slug misattributes
    the new version's provenance.
    """
    data = copy.deepcopy(base.get("prompt_data") or {})
    data.pop("origin", None)
    prompt = data.setdefault("prompt", {})
    prompt["type"] = prompt.get("type", "chat")
    prompt["messages"] = messages
    return data


def create_version(*, project_id: str, slug: str, name: str, description: str,
                   prompt_data: dict[str, Any]) -> dict[str, Any]:
    """POST /v1/prompt — creates a new VERSION under the same slug.

    POST is what creates a versioned commit. PATCH mutates state without one, so a
    change made with PATCH cannot be bisected to, reverted to, or compared against
    later — which is the whole reason the loop keeps a granular history.
    """
    return _bt("POST", "/prompt", {
        "project_id": project_id,
        "slug": slug,
        "name": name,
        "description": description,
        "prompt_data": prompt_data,
    })


# ---- Notion ---------------------------------------------------------------

def _rows(ds_id: str) -> list[dict[str, Any]]:
    return list(notion_rest.notion.query_data_source(ds_id))


def candidate_id_str(row: dict[str, Any]) -> str:
    uid = (row.get("properties") or {}).get("Candidate ID", {}).get("unique_id") or {}
    prefix, number = uid.get("prefix"), uid.get("number")
    return f"{prefix}-{number}" if prefix and number is not None else "(no id)"


def _id_number(row: dict[str, Any]) -> int:
    uid = (row.get("properties") or {}).get("Candidate ID", {}).get("unique_id") or {}
    return uid.get("number") or 0


def resolve(ds_id: str, ref: str) -> dict[str, Any]:
    rows = _rows(ds_id)
    wanted = ref.strip().lower().removeprefix("conv-")
    if wanted.isdigit():
        for row in rows:
            if _id_number(row) == int(wanted):
                return row
        raise SystemExit(f"no candidate with id CONV-{wanted}")
    matches = [r for r in rows if ref.lower() in notion_rest.title_text(r, "Title").lower()]
    if len(matches) != 1:
        raise SystemExit(f"{ref!r} matches {len(matches)} candidates — use the CONV id")
    return matches[0]


# The *appended* template shape ends the body with `---` + the context tag;
# the *delimited* shape (Coach since 2026-10-20) keeps both in later messages.
_APPENDED_SCAFFOLD = re.compile(r"\s*(?:---\s*)?\{\{\s*metadata\.contextInstructions\s*\}\}\s*$")


def with_scaffold(base_content: object, body: str) -> str:
    """`body` carrying the base system message's appended scaffold, if it had one.

    A delimited base has none, so the body goes in as is; a body that already
    carries the tag is left alone rather than given a second one.
    """
    if not isinstance(base_content, str) or _APPENDED_SCAFFOLD.search(body):
        return body
    match = _APPENDED_SCAFFOLD.search(base_content)
    return body.rstrip("\n") + base_content[match.start():] if match else body


def cmd_draft(args: argparse.Namespace) -> int:
    row = resolve(args.data_source_id, args.ref)
    cid = candidate_id_str(row)
    status = notion_rest.select_value(row, "Status")

    if status not in DRAFTABLE:
        print(f"{cid} is {status}, not approved — Gate 1 has to pass before drafting.")
        print("  Drafting an unapproved candidate spends editorial effort on a")
        print("  diagnosis nobody has agreed to.")
        return 1

    base = get_prompt(args.base_prompt)
    prompt_data = base.get("prompt_data") or {}
    messages = (prompt_data.get("prompt") or {}).get("messages") or []
    if not messages:
        raise SystemExit(f"base prompt {args.base_prompt} has no chat messages to replace")

    body = open(args.body, encoding="utf-8").read()
    # The system message is the prompt body; later messages carry the context
    # scaffolding and are left exactly as they are.
    new_messages = [dict(messages[0], content=with_scaffold(messages[0].get("content"), body))] + \
        [dict(m) for m in messages[1:]]

    title = notion_rest.title_text(row, "Title")
    description = args.label or f"{cid}: {title}"
    slug = args.slug or f"{base.get('slug')}-{cid.lower()}"

    print(f"{cid}  {title}")
    print(f"  base    {base.get('slug')} ({args.base_prompt})")
    print(f"  slug    {slug}")
    print(f"  label   {description}")
    print(f"  body    {args.body} — {len(body)} chars, "
          f"{len(body) - len(messages[0].get('content') or ''):+d} vs base")
    print(f"  model   {(prompt_data.get('options') or {}).get('model')}")
    carried = build_prompt_data(base, new_messages)
    tools = (carried.get("prompt") or {}).get("tools")
    print(f"  carried {len(carried.get('tool_functions') or [])} tool_functions"
          f"{', prompt.tools' if tools else ''} — a body edit stays a body edit")

    # The dry run has to show the DESTRUCTIVE half too. A re-draft clears the
    # previous body's gate results, and a plan that omits it means the clear is
    # invisible until after it has happened — which is the plan-and-confirm gate
    # failing at exactly the case it exists for.
    stale_preview = notion_rest.rich_text_value(row, "Gate results").strip()
    if stale_preview:
        prior = (notion_rest.rich_text_value(row, "Braintrust version").strip()
                 or "the previous version")
        print(f"  CLEARS  {len(stale_preview)} chars of gate results belonging to "
              f"{prior} — evidence for the body being replaced")

    if not args.confirm:
        print("\n--- not writing. Re-run with --confirm to create the version.")
        return 0

    created = create_version(project_id=args.project_id, slug=slug, name=description,
                             description=description,
                             prompt_data=build_prompt_data(base, new_messages))
    prompt_id = created.get("id", "")
    url = prompt_url(args.project_id, prompt_id)

    note = notion_rest.rich_text_value(row, "Quick status note").strip()
    line = (f"{date.today().isoformat()} — approved → drafted by the loop. "
            f"Braintrust {slug} ({prompt_id}). {description}")

    # A re-draft after Gate 2 asked for changes leaves the PREVIOUS body's
    # scorecard on the row. `record` appends, so Gate 2 would read evidence for two
    # different prompts as one write-up — and a large prior scorecard can exhaust
    # the field's budget and block the new evidence outright. The superseded
    # evidence is cleared here; it is not lost, because the Braintrust experiments
    # that produced it are where it actually lives.
    stale = notion_rest.rich_text_value(row, "Gate results").strip()
    props_extra = {}
    if stale:
        prior = notion_rest.rich_text_value(row, "Braintrust version").strip() or "the previous version"
        line += f" Cleared {len(stale)} chars of gate results belonging to {prior}."
        props_extra["Gate results"] = notion_rest.make_rich_text("")
        print(f"  clearing {len(stale)} chars of gate results from {prior}")

    notion_rest.notion.update_page_props(row["id"], {
        **props_extra,
        "Status": notion_rest.make_select("drafted"),
        "Braintrust prompt": {"url": url},
        "Braintrust version": notion_rest.make_rich_text(f"{slug} · {prompt_id}"),
        "Quick status note": notion_rest.make_rich_text(f"{note}\n{line}".strip() if note else line),
        "Last seen": notion_rest.make_date(date.today().isoformat()),
    })
    print(f"\ndrafted  {cid} → {url}")
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    rows = [r for r in _rows(args.data_source_id)
            if notion_rest.select_value(r, "Status") in DRAFTABLE]
    if not rows:
        print("nothing at `approved` — harvest Gate 1 verdicts first")
        return 0
    for row in sorted(rows, key=_id_number):
        klass = notion_rest.select_value(row, "Change class") or "unclassified"
        print(f"  {candidate_id_str(row):<8} {klass:<14} "
              f"{notion_rest.title_text(row, 'Title')}")
        diff = notion_rest.rich_text_value(row, "Proposed diff")
        print(f"           {'diff recorded' if diff else 'NO PROPOSED DIFF — write it first'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-source-id", default=AURIE_CONVERSATIONS_DS)
    p.add_argument("--project-id", default=CONVERSATION_PROJECT)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ready", help="approved candidates waiting to be drafted")
    sp.set_defaults(func=cmd_ready)

    sp = sub.add_parser("draft", help="create a labelled Braintrust version")
    sp.add_argument("ref", help="CONV-9, 9, or a title substring")
    sp.add_argument("--base-prompt", required=True,
                    help="Braintrust prompt id to branch from (its options are kept)")
    sp.add_argument("--body", required=True, help="file holding the new system message")
    sp.add_argument("--slug", help="target slug (default: <base slug>-<conv id>)")
    sp.add_argument("--label", help="activity label (default: '<CONV-n>: <title>')")
    sp.add_argument("--confirm", action="store_true",
                    help="required to actually write — otherwise this is a dry run")
    sp.set_defaults(func=cmd_draft)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
