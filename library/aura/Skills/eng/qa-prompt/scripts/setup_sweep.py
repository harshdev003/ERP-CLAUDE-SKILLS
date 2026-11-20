"""Stage one of a SWEEP QA run: fan one prompt out across N scenarios.

A sweep is the "1 prompt version, N scenarios in parallel" shape — the
gap left by single-drive mode (1 prompt, 1 scenario) and comparison mode
(2 prompts, 1 scenario). Useful for pre-ship QA of a conversational
prompt against a spread of emotional registers (stressed / anxious /
grieving / depleted / etc.) before promoting it to prod.

Usage:

    python3 setup_sweep.py <braintrust-prompt-url-or-uuid> \\
        --scenarios-file scenarios.json \\
        --no-context \\
        --run-dir outputs/qa-prompt-sweep-quick-processing-<ts>

    # or pass scenarios inline (repeatable)
    python3 setup_sweep.py <url> \\
        --scenario "stressed=Mid-30s parent, hard day at work..." \\
        --scenario "anxious=Late 20s, 10pm, ruminating about a conversation..." \\
        --no-context

The scenarios file is a JSON array:

    [
      {"label": "stressed",  "scenario": "Mid-30s parent, hard day at work..."},
      {"label": "anxious",   "scenario": "Late 20s, 10pm, ruminating..."},
      {"label": "grieving",  "scenario": "User in 40s, processing loss..."},
      {"label": "lowenergy", "scenario": "Exhausted user, terse one-liners..."}
    ]

For each scenario the script:

  1. Resolves the prompt once (and pins `_xact_id` so every scenario sees
     the same version, even if the latest changes mid-run).
  2. Resolves the context once (paste / dataset row / --no-context) and
     mirrors it across all scenarios for fair comparison.
  3. Writes `<run-dir>/<label>/state.json` per scenario via the same
     `_build_state` path setup_run.py uses.
  4. Writes `<run-dir>/sweep.json` — the sweep manifest the parent reads
     to know which labels to drive + report against.

After this stage:

  - Phase 2: orchestrator decides tool defaults once and mirrors them
    across all N state files (one set_tool_default.py call per tool per
    scenario).
  - Phase 3: orchestrator spawns N parallel `qa-prompt-sweep-runner`
    subagents — one per scenario. See the sibling agent definition at
    `plugins/eng/agents/qa-prompt-sweep-runner.md` for the contract.
  - Phase 4: self_critique.py runs against each state file; then
    `write_sweep_report.py --run-dir <run-dir>` aggregates everything
    into one cross-scenario report.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Sibling modules.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import braintrust_context
from braintrust_prompt import BraintrustError, _detect_provider, fetch_prompt, parse_prompt_url
from render_template import production_layout, render_messages, template_shape

import service_tier

_EMPTY_CONTEXT_PASTE = '{"input": [], "metadata": {"contextInstructions": ""}}'

_REQUIRED_ENV = ("BRAINTRUST_API_KEY", "OPENAI_EVALUATIONS_API_KEY")


def _check_env_prereqs() -> None:
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if not missing:
        return
    raise BraintrustError(
        f"qa-prompt cannot run — required env var(s) missing: {', '.join(missing)}. "
        "Add them under the `env` block in ~/.claude/settings.json (use "
        "`update-config` or `/update-config`), then restart Claude Code. "
        "Inside a Cowork sandbox, paste-export each key once per session."
    )


def _parse_scenario_arg(value: str) -> dict:
    """Parse a single --scenario `label=description` value."""
    if "=" not in value:
        raise BraintrustError(
            f"--scenario must be `label=description` (got {value!r}). Example: "
            "`--scenario 'stressed=Mid-30s parent, hard day at work...'`"
        )
    label, description = value.split("=", 1)
    label = label.strip()
    description = description.strip()
    if not label or not description:
        raise BraintrustError(
            f"--scenario {value!r}: both `label` and `description` must be non-empty."
        )
    return {"label": label, "scenario": description}


def _load_scenarios(args: argparse.Namespace) -> list[dict]:
    scenarios: list[dict] = []
    if args.scenarios_file:
        try:
            raw = Path(args.scenarios_file).read_text(encoding="utf-8")
        except OSError as e:
            raise BraintrustError(f"could not read --scenarios-file: {e}") from e
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise BraintrustError(f"--scenarios-file is not valid JSON: {e}") from e
        if not isinstance(parsed, list):
            raise BraintrustError(
                "--scenarios-file must contain a JSON array of "
                "{label, scenario} objects."
            )
        for i, item in enumerate(parsed):
            if not isinstance(item, dict) or "label" not in item or "scenario" not in item:
                raise BraintrustError(
                    f"--scenarios-file entry {i} missing `label` or `scenario`: {item!r}"
                )
            scenarios.append({"label": str(item["label"]).strip(),
                              "scenario": str(item["scenario"]).strip()})

    for value in args.scenario or []:
        scenarios.append(_parse_scenario_arg(value))

    if len(scenarios) < 2:
        raise BraintrustError(
            f"sweep mode needs at least 2 scenarios; got {len(scenarios)}. "
            "Use single-drive mode (setup_run.py) for 1-scenario QA."
        )

    labels = [s["label"] for s in scenarios]
    if len(set(labels)) != len(labels):
        dupes = [l for l in labels if labels.count(l) > 1]
        raise BraintrustError(
            f"sweep labels must be unique within a run (duplicates: {sorted(set(dupes))!r}). "
            "Labels become directory names + report identifiers."
        )

    return scenarios


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stage one of a sweep QA run: fan one prompt across N scenarios."
    )
    p.add_argument("prompt_url", help="Braintrust prompt URL or bare UUID.")
    p.add_argument(
        "--version",
        default=None,
        help="Braintrust prompt version to pin (`_xact_id`). When omitted, fetches "
             "the latest and pins it across all scenarios in this sweep.",
    )
    p.add_argument(
        "--scenarios-file",
        default=None,
        help="Path to a JSON array of {label, scenario} objects. Either this or "
             "--scenario (repeatable) is required.",
    )
    p.add_argument(
        "--scenario",
        action="append",
        default=None,
        help="Inline scenario as `label=description`. Repeatable. Either this or "
             "--scenarios-file is required.",
    )
    ctx_group = p.add_mutually_exclusive_group(required=True)
    ctx_group.add_argument(
        "--context",
        default=None,
        help="dataset:<slug>:<row-id> | paste. Mirrored across all scenarios so "
             "metadata.contextInstructions is identical for fair comparison.",
    )
    ctx_group.add_argument(
        "--no-context",
        action="store_true",
        help="Synthesize an empty paste payload (`{\"input\": [], \"metadata\": "
             "{\"contextInstructions\": \"\"}}`). Most sweep runs use this.",
    )
    p.add_argument(
        "--dataset-id",
        default=None,
        help="Braintrust dataset UUID. When set, --context is interpreted as "
             "just `dataset:<row-id>` and the dataset is fetched directly by id "
             "(any project). Without this flag, --context must be "
             "`dataset:<slug>:<row-id>` and the slug is resolved in the "
             "Conversation project only.",
    )
    p.add_argument(
        "--user-id",
        default=None,
        help="Aura user id. Required if invoke_real_tool will be called for "
             "tools like recommendTracks. Mirrored across all scenarios.",
    )
    p.add_argument(
        "--api",
        choices=["chat", "responses"],
        default="chat",
        help="Which OpenAI API send_turn.py should hit for every scenario in "
             "this sweep. `chat` (default) uses Chat Completions; `responses` "
             "uses the Response API in stateless mode. Mirrored across all "
             "scenario state files. Ignored when --provider google or "
             "anthropic.",
    )
    p.add_argument(
        "--provider",
        choices=["openai", "google", "anthropic"],
        default=None,
        help="Which provider send_turn.py should hit for every scenario in "
             "this sweep. Default: auto-detect from the prompt's model — "
             "`gemini-*` or `google/*` → google, `claude-*` or `anthropic/*` "
             "→ anthropic, everything else → openai. Mirrored across all "
             "scenario state files.",
    )
    p.add_argument(
        "--run-dir",
        default=None,
        help="Output directory for the sweep. Per-scenario state files land at "
             "<run-dir>/<label>/state.json. Default: "
             "outputs/qa-prompt-sweep-<slug>-<timestamp>/.",
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


def _build_state_for_scenario(
    bundle,
    context: braintrust_context.Context,
    scenario_description: str,
    user_id: str | None,
    pinned_version: str | None,
    api: str,
    provider: str,
    chosen_tier: str,
    layout: str = "production",
) -> dict:
    """Mirror of setup_run.py:_build_state, specialized for sweep:
    always interactive, scenario per-call, prompt+context resolved once."""
    seed_input: list = []  # interactive mode — Claude composes user turns
    rendered, render_warnings = render_messages(
        bundle.messages,
        {
            "input": seed_input,
            "metadata": context.metadata,
            "template_vars": context.template_vars or {},
        },
    )
    if layout == "production" and not context.template_vars:
        # Interactive: no time message before turns Claude has yet to compose.
        rendered = production_layout(rendered, context.metadata, instruction_role="system",
                                     append_developer_instructions=False)
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
            "version_pinned": bool(pinned_version),
        },
        "mode": "interactive",
        "scenario": scenario_description,
        "model": bundle.model,
        "params": bundle.params,
        "provider": provider,
        "api": api,
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
            "input_message_count": 0,
            "input_discarded": bool(context.input_messages),
            "has_context_instructions": bool(context.metadata.get("contextInstructions")),
        },
        "layout": layout,
        "template_shape": template_shape(bundle.messages),
        "render_warnings": render_warnings,
        "user_id": user_id,
        "turns": [],
        "critique": None,
        "claude_analysis": None,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    try:
        # Before the fetch: a bad tier should fail on the typo, not after a
        # network round-trip — and before any state file is written.
        chosen_tier = service_tier.resolve(
            args.service_tier, policy=args.on_tier_unavailable
        )
        _check_env_prereqs()
        scenarios = _load_scenarios(args)

        # Resolve prompt + context ONCE so every scenario sees the same
        # version + contextInstructions.
        prompt_id = parse_prompt_url(args.prompt_url)
        bundle = fetch_prompt(prompt_id, version=args.version)

        # Resolve provider; honour --provider override but error out if it
        # conflicts with the detected value (mirror of setup_run.py).
        detected_provider = bundle.provider
        if args.provider and args.provider != detected_provider:
            raise BraintrustError(
                f"model {bundle.model!r} resolves to provider "
                f"{detected_provider!r}, but --provider {args.provider!r} was "
                f"passed. Pick one: drop the flag to use the auto-detected "
                f"provider, or correct the value."
            )
        provider = args.provider or detected_provider
        if provider == "google" and not os.environ.get("GEMINI_API_KEY"):
            raise BraintrustError(
                "qa-prompt cannot run — model resolves to provider 'google' "
                "but GEMINI_API_KEY is missing. Add it under the `env` block "
                "in ~/.claude/settings.json (use the `update-config` skill or "
                "`/update-config`), then restart Claude Code so the new env "
                "values load. Inside a Cowork sandbox, paste-export the key "
                "once per session. Obtain a key at "
                "https://aistudio.google.com/apikey under your "
                "aurahealth.io Google account."
            )
        if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
            raise BraintrustError(
                "qa-prompt cannot run — model resolves to provider "
                "'anthropic' but ANTHROPIC_API_KEY is missing. Add it under "
                "the `env` block in ~/.claude/settings.json (use the "
                "`update-config` skill or `/update-config`), then restart "
                "Claude Code so the new env values load. Inside a Cowork "
                "sandbox, paste-export the key once per session. Obtain a "
                "key at https://console.anthropic.com/settings/keys under "
                "the Aura Anthropic org."
            )

        if args.no_context:
            context = braintrust_context.resolve_paste_from_string(_EMPTY_CONTEXT_PASTE)
        else:
            context = braintrust_context.resolve(args.context, dataset_id=args.dataset_id)
    except (BraintrustError, service_tier.ServiceTierError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Resolve run-dir.
    import datetime as dt
    if args.run_dir:
        run_dir = Path(args.run_dir)
    else:
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = bundle.slug or "prompt"
        run_dir = Path("outputs") / f"qa-prompt-sweep-{slug}-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    written: list[dict] = []
    for entry in scenarios:
        scenario_dir = run_dir / entry["label"]
        scenario_dir.mkdir(parents=True, exist_ok=True)
        state = _build_state_for_scenario(
            bundle=bundle,
            context=context,
            scenario_description=entry["scenario"],
            user_id=args.user_id,
            pinned_version=args.version,
            api=args.api,
            provider=provider,
            chosen_tier=chosen_tier,
            layout=args.layout,
        )
        state_path = scenario_dir / "state.json"
        state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        written.append({
            "label": entry["label"],
            "scenario": entry["scenario"],
            "state_file": str(state_path),
        })

    # Sweep manifest — single source of truth for the parent + the sweep
    # report writer.
    manifest = {
        "prompt": {
            "id": bundle.prompt_id,
            "slug": bundle.slug,
            "name": bundle.name,
            "version": bundle.version,
            "version_pinned": bool(args.version),
            "url": f"https://www.braintrust.dev/app/Aura%20Health%201/p/"
                   f"{bundle.project_name or 'Conversation'}/prompts/{bundle.prompt_id}",
        },
        "context_source": context.source,
        "has_context_instructions": bool(context.metadata.get("contextInstructions")),
        "user_id": args.user_id,
        "provider": provider,
        "api": args.api,
        "service_tier": chosen_tier,
        "scenarios": written,
    }
    manifest_path = run_dir / "sweep.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    # Output: run_dir path (the parent uses it as the working dir).
    print(str(run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
