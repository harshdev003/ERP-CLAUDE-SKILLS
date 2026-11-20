#!/usr/bin/env python3
"""Read and edit a Braintrust prompt's body without touching its context scaffold.

Braintrust templates carry `{{metadata.contextInstructions}}` in one of two shapes:

- **delimited** (the standard-layout prompters' [Production] / [Development]
  templates since 2026-10-20 -- Coach, QP, QP Sentinel, QP Done, Done Prompt,
  Guardrails Handler -- IS 1.40's order): system body, system `---`, user `---`, system
  `{{metadata.contextInstructions}}`, system `## Conversation\\n{{input}}`.
- **appended** (old eval and experiment copies): system `body\\n\\n---\\n\\n{{metadata.contextInstructions}}`,
  then system `## Conversation\\n{{input}}`.

The body is `messages[0]` in both; only the appended shape carries the scaffold
inside it. Writing the appended suffix back onto a delimited body would put the
context into the system message twice, so every write goes through `set_body`,
which keeps whichever shape the prompt already has.

    python3 bt_scaffold.py shape    < prompt.json
    python3 bt_scaffold.py body     < prompt.json            # the .txt body
    python3 bt_scaffold.py check    < prompt.json [--like other.json]
    python3 bt_scaffold.py set-body --body-file new.txt < prompt.json > prompt_out.json

`prompt.json` may be a `GET /v1/prompt?...` list response, one prompt object, its
`prompt_data`, or `prompt_data.prompt`. `set-body` prints `prompt_data.prompt`.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from typing import Any

TAG = re.compile(r"\{\{\s*metadata\.contextInstructions\s*\}\}")
# The appended scaffold: optional `---` separator, the tag, trailing whitespace.
SUFFIX = re.compile(r"\s*(?:---\s*)?\{\{\s*metadata\.contextInstructions\s*\}\}\s*$")
DELIMITER = "---"


class ScaffoldError(ValueError):
    pass


def prompt_object(doc: Any) -> dict[str, Any]:
    """`prompt_data.prompt` from any of the accepted document shapes."""
    if isinstance(doc, dict) and isinstance(doc.get("objects"), list):
        if not doc["objects"]:
            raise ScaffoldError("the response has no prompt objects")
        doc = doc["objects"][0]
    if isinstance(doc, dict) and "prompt_data" in doc:
        doc = doc["prompt_data"]
    if isinstance(doc, dict) and isinstance(doc.get("prompt"), dict):
        doc = doc["prompt"]
    if not isinstance(doc, dict) or not isinstance(doc.get("messages"), list):
        raise ScaffoldError("no prompt.messages in the input")
    return doc


def _text(m: dict[str, Any]) -> str:
    c = m.get("content")
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return c if isinstance(c, str) else ""


def shape(messages: list[dict[str, Any]]) -> str:
    """`delimited`, `appended`, `separate` (tag in its own message, no pair), or `none`."""
    for a, b in zip(messages, messages[1:]):
        if (a.get("role") in ("system", "developer") and _text(a).strip() == DELIMITER
                and b.get("role") == "user" and _text(b).strip() == DELIMITER):
            return "delimited"
    if messages and SUFFIX.search(_text(messages[0])):
        return "appended"
    return "separate" if any(TAG.search(_text(m)) for m in messages[1:]) else "none"


def body(messages: list[dict[str, Any]]) -> str:
    """The prompter `.txt` body: `messages[0]` minus any appended scaffold."""
    if not messages or messages[0].get("role") != "system":
        raise ScaffoldError("messages[0] is not a system message")
    return SUFFIX.sub("", _text(messages[0]))


def set_body(prompt: dict[str, Any], new_body: str) -> dict[str, Any]:
    """A copy of `prompt` with the body replaced and the scaffold kept as it was."""
    out = copy.deepcopy(prompt)
    messages = out["messages"]
    if not messages or messages[0].get("role") != "system":
        raise ScaffoldError("messages[0] is not a system message")
    if not isinstance(messages[0].get("content"), str):
        raise ScaffoldError("messages[0] content is not plain text")
    new_body = SUFFIX.sub("", new_body.rstrip("\n"))
    if TAG.search(new_body):
        raise ScaffoldError("the new body itself contains {{metadata.contextInstructions}}")
    old = messages[0]["content"]
    match = SUFFIX.search(old)
    messages[0]["content"] = new_body + (old[match.start():] if match else "")
    return out


def check(messages: list[dict[str, Any]], like: list[dict[str, Any]] | None = None) -> list[str]:
    """Problems that would break context interpolation; empty when fine."""
    problems = []
    if sum(len(TAG.findall(_text(m))) for m in messages) != 1:
        problems.append("expected exactly one {{metadata.contextInstructions}} tag across all messages")
    if like is not None:
        if shape(messages) != shape(like):
            problems.append(f"shape changed: {shape(like)} -> {shape(messages)}")
        if len(messages) != len(like) or [m.get("role") for m in messages] != [m.get("role") for m in like]:
            problems.append("message count or roles changed")
        elif any(_text(a) != _text(b) for a, b in zip(messages[1:], like[1:])):
            problems.append("a scaffold message (messages[1:]) changed")
    return problems


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("shape")
    sub.add_parser("body")
    c = sub.add_parser("check")
    c.add_argument("--like", help="Reference prompt JSON whose scaffold must be preserved.")
    s = sub.add_parser("set-body")
    s.add_argument("--body-file", required=True)
    args = p.parse_args()
    try:
        prompt = prompt_object(json.load(sys.stdin))
        messages = prompt["messages"]
        if args.cmd == "shape":
            print(shape(messages))
        elif args.cmd == "body":
            sys.stdout.write(body(messages))
        elif args.cmd == "check":
            like = None
            if args.like:
                with open(args.like, encoding="utf-8") as f:
                    like = prompt_object(json.load(f))["messages"]
            problems = check(messages, like)
            for prob in problems:
                print(f"FAIL: {prob}", file=sys.stderr)
            if problems:
                return 1
            print(f"OK: {shape(messages)} scaffold intact")
        else:
            with open(args.body_file, encoding="utf-8") as f:
                print(json.dumps(set_body(prompt, f.read()), ensure_ascii=False))
    except (ScaffoldError, json.JSONDecodeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
