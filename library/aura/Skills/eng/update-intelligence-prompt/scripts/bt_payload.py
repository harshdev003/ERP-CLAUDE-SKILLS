#!/usr/bin/env python3
"""Build the `POST /v1/prompt` payload that commits a new version of an existing prompt.

A POST replaces the whole prompt record, so any field the payload leaves out is
cleared on the target. The payload therefore starts from the target's own record
and changes only what the release sets:

- kept from the target: project_id, name, slug, description, tags,
  `prompt_data.tool_functions` and every `options` key (position included);
- replaced: `prompt_data.prompt` (`--prompt`), `options.model` (`--model`) and
  `options.params` (`--params`), each only when given;
- `description` changes only with `--description` (an empty string clears it).

    python3 bt_payload.py --target target.json [--prompt prompt.json] \\
        [--model gpt-5.2] [--params '{"reasoning_effort":"low"}'] \\
        [--description "new text"] > payload.json

`target.json` is the target's `GET /v1/prompt?...&slug=<slug>` response (or its
first object). `prompt.json` is any shape `bt_scaffold.py` accepts; its
`prompt_data.prompt` is sent whole.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from typing import Any

KEPT = ("project_id", "name", "slug", "description", "tags")


class PayloadError(ValueError):
    pass


def target_object(doc: Any) -> dict[str, Any]:
    if isinstance(doc, dict) and isinstance(doc.get("objects"), list):
        if not doc["objects"]:
            raise PayloadError("the target response has no prompt objects")
        doc = doc["objects"][0]
    if not isinstance(doc, dict) or not isinstance(doc.get("prompt_data"), dict):
        raise PayloadError("the target has no prompt_data")
    for key in ("project_id", "name", "slug"):
        if not doc.get(key):
            raise PayloadError(f"the target has no {key}")
    return doc


def prompt_object(doc: Any) -> dict[str, Any]:
    if isinstance(doc, dict) and isinstance(doc.get("objects"), list):
        if not doc["objects"]:
            raise PayloadError("the prompt response has no prompt objects")
        doc = doc["objects"][0]
    if isinstance(doc, dict) and "prompt_data" in doc:
        doc = doc["prompt_data"]
    if isinstance(doc, dict) and isinstance(doc.get("prompt"), dict):
        doc = doc["prompt"]
    if not isinstance(doc, dict) or not isinstance(doc.get("messages"), list):
        raise PayloadError("no prompt.messages in the prompt input")
    return doc


def build_payload(target: dict[str, Any], prompt: dict[str, Any] | None = None,
                  model: str | None = None, params: dict[str, Any] | None = None,
                  description: str | None = None) -> dict[str, Any]:
    target = target_object(target)
    data = target["prompt_data"]
    out: dict[str, Any] = {k: copy.deepcopy(target[k]) for k in KEPT if target.get(k) is not None}
    if description is not None:
        out["description"] = description
    options = copy.deepcopy(data.get("options") or {})
    if model is not None:
        options["model"] = model
    if params is not None:
        options["params"] = copy.deepcopy(params)
    out["function_type"] = None
    out["function_data"] = {"type": "prompt"}
    out["prompt_data"] = {
        "prompt": copy.deepcopy(prompt if prompt is not None else prompt_object(data)),
        "tool_functions": copy.deepcopy(data.get("tool_functions") or []),
        "options": options,
    }
    return out


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", required=True, help="The target prompt's GET response.")
    p.add_argument("--prompt", help="Prompt JSON whose prompt_data.prompt is sent; default: the target's own.")
    p.add_argument("--model")
    p.add_argument("--params", help="options.params as a JSON object.")
    p.add_argument("--description", help="Set the description; omit to keep the target's.")
    args = p.parse_args()
    try:
        params = json.loads(args.params) if args.params is not None else None
        if params is not None and not isinstance(params, dict):
            raise PayloadError("--params must be a JSON object")
        prompt = prompt_object(_load(args.prompt)) if args.prompt else None
        payload = build_payload(_load(args.target), prompt, args.model, params, args.description)
    except (PayloadError, json.JSONDecodeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
