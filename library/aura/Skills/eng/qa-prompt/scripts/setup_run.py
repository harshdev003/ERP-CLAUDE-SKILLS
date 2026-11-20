"""Stage one of a QA run: fetch the prompt, resolve the context, render the
template, and emit a JSON state blob the orchestrator (Claude) will carry
through subsequent steps.

Usage:

    python3 setup_run.py <braintrust-prompt-url-or-uuid> \\
        --mode dataset \\
        --context dataset:<slug>:<row-id>

    python3 setup_run.py <url> --mode interactive --scenario "stressed parent, just woke up" \\
        --context dataset:<slug>:<row-id>      # used for metadata.contextInstructions; input is ignored
    python3 setup_run.py <url> --mode interactive --scenario "..." --context paste

Optional:
    --user-id <uid>      # required if invoke_real_tool will be called for tools like recommendTracks
    --state-file <path>  # write the state JSON to this path (default: stdout)

The output is a single JSON object on stdout (or written to --state-file). Shape:

    {
      "prompt": {"id", "name", "slug", "project_id", "project_name", "url"},
      "mode": "dataset" | "interactive",
      "scenario": "..." | null,        # set in interactive mode
      "model": "gpt-5.2",
      "params": {...},
      "provider": "openai" | "google" | "anthropic", # which provider send_turn.py will hit
      "api": "chat" | "responses",     # which OpenAI API (ignored when provider=google|anthropic)
      "messages": [...rendered messages...],
      "tools_chat": [...nested Chat Completions tool defs...],
      "tools_responses": [...flat Responses API tool defs...],
      "tools_gemini": [...{"function_declarations": [...]} blobs for Gemini...],
      "tools_anthropic": [...{name, description, input_schema} entries for Anthropic...],
      "tool_registry": {
        "<tool_name>": {
          "description": "...",
          "parameters": {...},
          "returns_schema": {...} | null,
          "function_id": "..." | null,
          "has_real_impl": true|false
        }
      },
      "tool_defaults": {                # populated by set_tool_default.py before the drive begins
        "<tool_name>": {
          "kind": "auto-mock" | "real" | "canned" | "ask-per-call",
          "canned_response": {...}      # only when kind == "canned"
        }
      },
      "context": {"source": "...", "warnings": [...], "structured_input": bool, "template_var_keys": [...]},
      "dataset_row": {                  # present even for `paste` context (id/expected = null then)
        "id": "..." | null,
        "expected": <any> | null        # the dataset row's `expected`, used by structured-plan-match scorer
      },
      "render_warnings": [...],
      "user_id": "..." | null,
      "turns": [],                      # populated by send_turn.py on each call
      "critique": null,                 # populated by self_critique.py
      "claude_analysis": null           # populated by write_report.py via --analysis-file
    }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make sibling modules importable when invoked as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import braintrust_context
from braintrust_prompt import BraintrustError, _detect_provider, fetch_prompt, parse_prompt_url
from render_template import production_layout, render_messages, template_shape

import service_tier

_EMPTY_CONTEXT_PASTE = '{"input": [], "metadata": {"contextInstructions": ""}}'

_REQUIRED_ENV = ("BRAINTRUST_API_KEY", "OPENAI_EVALUATIONS_API_KEY")


def _check_env_prereqs() -> None:
    """Fail fast if the env qa-prompt's downstream scripts need is missing.

    Without this check, the failure surfaces in Phase 3 at the first
    `send_turn.py` call — by which point the user has already clicked
    through Phase 2 tool-default questions and any spawned subagents
    have all failed identically. Cheaper to bail here.
    """
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if not missing:
        return
    msg = (
        f"qa-prompt cannot run — required env var(s) missing: {', '.join(missing)}. "
        "Add them under the `env` block in ~/.claude/settings.json (use the "
        "`update-config` skill or `/update-config`), then restart Claude Code "
        "so the new env values load. Inside a Cowork sandbox, paste-export each "
        "key once per session."
    )
    raise BraintrustError(msg)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stage one of a QA run: fetch prompt + context, emit state JSON."
    )
    p.add_argument("prompt_url", help="Braintrust prompt URL or bare UUID.")
    p.add_argument(
        "--version",
        default=None,
        help="Braintrust prompt version to pin (the `_xact_id` shown in the prompt's "
             "version dropdown in the UI). When omitted, fetches the latest published "
             "version. Useful in comparison runs to pit two versions of the same prompt "
             "against each other.",
    )
    p.add_argument(
        "--mode",
        choices=["dataset", "interactive"],
        default="dataset",
        help="Drive mode. `dataset` replays a row's input messages then stops; "
             "`interactive` has Claude simulate the user per scenario.",
    )
    p.add_argument(
        "--scenario",
        default=None,
        help="Free-form persona/situation description for interactive mode. "
             "Required when --mode interactive.",
    )
    ctx_group = p.add_mutually_exclusive_group(required=True)
    ctx_group.add_argument(
        "--context",
        default=None,
        help="dataset:<slug>:<row-id> | paste. In interactive mode the source's "
             "`input` messages are discarded; only `metadata.contextInstructions` is used.",
    )
    ctx_group.add_argument(
        "--no-context",
        action="store_true",
        help="Convenience flag for interactive mode runs that don't need a seed "
             "dataset row: synthesizes an empty paste payload ("
             "`{\"input\": [], \"metadata\": {\"contextInstructions\": \"\"}}`) so "
             "`{{metadata.contextInstructions}}` renders to an empty string. "
             "Equivalent to piping that JSON via `--context paste`.",
    )
    p.add_argument(
        "--dataset-id",
        default=None,
        help="Braintrust dataset UUID. When set, --context is interpreted as "
             "just `dataset:<row-id>` and the dataset is fetched directly by id "
             "(any project). Without this flag, --context must be "
             "`dataset:<slug>:<row-id>` and the slug is resolved in the "
             "Conversation project only — so use --dataset-id for datasets that "
             "live in Memory, Quick Processing, or any other project.",
    )
    p.add_argument(
        "--user-id",
        default=None,
        help="Aura user id. Required if you later ask invoke_real_tool.py to call recommendTracks for real.",
    )
    p.add_argument(
        "--api",
        choices=["chat", "responses"],
        default="chat",
        help="Which OpenAI API send_turn.py should hit for this run. `chat` (default) "
             "uses Chat Completions, preserving today's behaviour. `responses` uses the "
             "Response API in stateless mode (full messages re-sent every turn — no "
             "previous_response_id or conversations object). Braintrust does not record "
             "an API choice on prompts; pick the one production uses for the prompter. "
             "Ignored when --provider google or anthropic (each has its own API).",
    )
    p.add_argument(
        "--provider",
        choices=["openai", "google", "anthropic"],
        default=None,
        help="Which provider send_turn.py should hit. Default: auto-detect from "
             "the prompt's model — `gemini-*` or `google/*` → google, `claude-*` "
             "or `anthropic/*` → anthropic, everything else → openai. Explicit "
             "override is for edge cases where Braintrust's `model` string doesn't "
             "match the canonical prefix; the script errors if the override "
             "conflicts with the detected provider.",
    )
    p.add_argument(
        "--state-file",
        default=None,
        help="Write the state JSON to this path. Default: stdout.",
    )
    p.add_argument(
        "--layout",
        choices=["production", "template"],
        default="production",
        help="production (default): put the rendered messages in Intelligence "
             "Service 1.40's order (body, `---` pair, context, conversation) for "
             "either Braintrust template shape. template: keep the template as "
             "rendered. See render_template.production_layout.",
    )
    service_tier.add_argument(p)
    return p.parse_args()


def _build_state(args: argparse.Namespace, chosen_tier: str) -> dict:
    if args.mode == "interactive" and not args.scenario:
        raise BraintrustError(
            "--scenario is required when --mode interactive. Describe the persona "
            "or situation Claude should simulate as the user (e.g. 'stressed parent, "
            "just woke up, looking for a 3-minute meditation')."
        )

    prompt_id = parse_prompt_url(args.prompt_url)
    bundle = fetch_prompt(prompt_id, version=args.version)

    # Resolve the provider before touching anything else. Auto-detect from the
    # model prefix; honour --provider override but error out if it conflicts
    # with the detected value (cheaper than a confusing 400 later).
    detected_provider = bundle.provider
    if args.provider and args.provider != detected_provider:
        raise BraintrustError(
            f"model {bundle.model!r} resolves to provider {detected_provider!r}, "
            f"but --provider {args.provider!r} was passed. Pick one: drop the "
            f"flag to use the auto-detected provider, or correct the value."
        )
    provider = args.provider or detected_provider

    # Conditional GEMINI_API_KEY check — only needed when we'll actually call
    # Gemini. Phase 0's prereq_check.py is unconditional on Braintrust+OpenAI
    # because it runs before fetch_prompt() resolves the model; this is the
    # earliest point we know whether the key is required at all.
    if provider == "google" and not os.environ.get("GEMINI_API_KEY"):
        raise BraintrustError(
            "qa-prompt cannot run — model resolves to provider 'google' but "
            "GEMINI_API_KEY is missing. Add it under the `env` block in "
            "~/.claude/settings.json (use the `update-config` skill or "
            "`/update-config`), then restart Claude Code so the new env values "
            "load. Inside a Cowork sandbox, paste-export the key once per "
            "session. Obtain a key at https://aistudio.google.com/apikey under "
            "your aurahealth.io Google account."
        )
    # Symmetric conditional check for Anthropic (ENGPROM-54).
    if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise BraintrustError(
            "qa-prompt cannot run — model resolves to provider 'anthropic' "
            "but ANTHROPIC_API_KEY is missing. Add it under the `env` block "
            "in ~/.claude/settings.json (use the `update-config` skill or "
            "`/update-config`), then restart Claude Code so the new env "
            "values load. Inside a Cowork sandbox, paste-export the key once "
            "per session. Obtain a key at "
            "https://console.anthropic.com/settings/keys under the Aura "
            "Anthropic org."
        )

    if args.no_context:
        # Synthesize an empty paste payload — the orchestrator doesn't need to
        # pipe `{"input": [], "metadata": {"contextInstructions": ""}}` by hand.
        context = braintrust_context.resolve_paste_from_string(_EMPTY_CONTEXT_PASTE)
    else:
        context = braintrust_context.resolve(args.context, dataset_id=args.dataset_id)
    # In interactive mode, discard the source's `input` messages — Claude will
    # generate user turns against the scenario. The metadata (especially
    # contextInstructions) still flows through to the rendered system prompt.
    # Structured-input rows (template_vars set) don't carry chat messages —
    # the template renders against the per-key vars instead. Interactive
    # mode against a structured-input row keeps the template_vars so any
    # `{{<key>}}` placeholders resolve, even though there are no seed
    # messages to discard.
    seed_input = [] if args.mode == "interactive" else context.input_messages
    rendered, render_warnings = render_messages(
        bundle.messages,
        {
            "input": seed_input,
            "metadata": context.metadata,
            "template_vars": context.template_vars or {},
        },
    )
    if args.layout == "production" and not context.template_vars:
        # Roles stay `system` (send_turn maps them per provider). The time
        # message only belongs after a replayed conversation, never before
        # turns Claude has yet to compose.
        rendered = production_layout(
            rendered, context.metadata, instruction_role="system",
            append_developer_instructions=args.mode == "dataset",
        )

    project_label = bundle.project_name or "Conversation"
    prompt_url = (
        f"https://www.braintrust.dev/app/Aura%20Health%201/p/{project_label}"
        f"/prompts/{bundle.prompt_id}"
    )

    tool_registry: dict[str, dict] = {}
    for t in bundle.tools:
        tool_registry[t.name] = {
            "description": t.description,
            "parameters": t.parameters,
            "returns_schema": t.returns_schema,
            "function_id": t.function_id,
            "has_real_impl": bool(t.function_id),
        }

    return {
        "prompt": {
            "id": bundle.prompt_id,
            "name": bundle.name,
            "slug": bundle.slug,
            "project_id": bundle.project_id,
            "project_name": bundle.project_name,
            "url": prompt_url,
            "version": bundle.version,
            "version_pinned": bool(args.version),
        },
        "mode": args.mode,
        "scenario": args.scenario,
        "model": bundle.model,
        "params": bundle.params,
        "provider": provider,
        "api": args.api,
        "service_tier": chosen_tier,
        "messages": rendered,
        "tools_chat": bundle.openai_tools(),
        "tools_responses": bundle.openai_responses_tools(),
        "tools_gemini": bundle.gemini_tools(),
        "tools_anthropic": bundle.anthropic_tools(),
        "tool_registry": tool_registry,
        "tool_defaults": {},
        "context": {
            "source": context.source,
            "warnings": context.warnings,
            "input_message_count": len(seed_input),
            "input_discarded": args.mode == "interactive" and bool(context.input_messages),
            "has_context_instructions": bool(context.metadata.get("contextInstructions")),
            "structured_input": bool(context.template_vars),
            "template_var_keys": sorted((context.template_vars or {}).keys()),
        },
        "dataset_row": {
            "id": context.row_id,
            # `expected` is the dataset row's `expected` field — used by the
            # `structured-plan-match` scorer to grade the model's output
            # against the documented contract. None when the row has no
            # `expected` set, or when the context is `paste`.
            "expected": context.expected,
        },
        "layout": args.layout,
        "template_shape": template_shape(bundle.messages),
        "render_warnings": render_warnings,
        "user_id": args.user_id,
        "turns": [],
        "critique": None,
        "claude_analysis": None,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    try:
        # Resolved first, before the Braintrust fetch: a bad tier is a typo in a
        # flag or an env var, and it should say so immediately rather than after
        # a network round-trip. Resolved, not raw, so an unset flag falls through
        # to the env override before the flex default and the state records the
        # tier the run actually used.
        chosen_tier = service_tier.resolve(
            args.service_tier, policy=args.on_tier_unavailable
        )
        _check_env_prereqs()
        state = _build_state(args, chosen_tier)
    except (BraintrustError, service_tier.ServiceTierError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    payload = json.dumps(state, indent=2, default=str)
    if args.state_file:
        Path(args.state_file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.state_file).write_text(payload, encoding="utf-8")
        print(args.state_file)
    else:
        sys.stdout.write(payload)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
