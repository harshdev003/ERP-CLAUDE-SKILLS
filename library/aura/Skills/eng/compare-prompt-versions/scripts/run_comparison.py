"""Run a Braintrust dataset through 2+ prompt versions and log one experiment each.

Every version gets the same rows, the same model call settings (the prompt's
own model + params, the run's service tier) and the same tool stubs. Each row's
result is scored in code (`tool-call`) or by a rubric judge (`llm-judge`) and
written as a Braintrust experiment event linked to its dataset record, so the
Braintrust compare view lines the versions up row by row.

    python3 run_comparison.py \\
      --prompt "prod=coach-production" --prompt "new=<prompt-url>@<version>" \\
      --dataset "Guardrail Trigger v1" --scorer tool-call \\
      --tool handoffToGuardrails --arg category --label-field expected.category \\
      --out <dir> [--dry-run] [--limit N]

Exit codes: 0 ok, 2 configuration / env error, 3 every row of a version failed.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import braintrust_prompt as btp  # noqa: E402
import prompt_diff  # noqa: E402
import scorers  # noqa: E402
import service_tier  # noqa: E402
import summarize  # noqa: E402
from render_template import TemplateError, render_messages  # noqa: E402
from render_template import production_layout as _production_layout  # noqa: E402

BT_API = "https://api.braintrust.dev/v1"
DEFAULT_PROJECT = "Conversation"
DEFAULT_STUB = '{"status": "ok"}'
# Metadata keys too large or too user-specific to copy onto every experiment event;
# the dataset record (linked by id) still holds them.
DROP_METADATA_KEYS = {"contextInstructions"}
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class ConfigError(RuntimeError):
    pass


# ------------------------------------------------------------------ Braintrust


def _bt(method: str, path: str, **kw: Any) -> Any:
    key = os.environ.get("BRAINTRUST_API_KEY")
    if not key:
        raise ConfigError("BRAINTRUST_API_KEY is not set (~/.claude/settings.json `env`).")
    r = requests.request(method, f"{BT_API}{path}", headers={"Authorization": f"Bearer {key}"},
                         timeout=120, **kw)
    if r.status_code >= 400:
        raise ConfigError(f"Braintrust {method} {path} → {r.status_code}: {r.text[:300]}")
    return r.json()


def resolve_project_id(name: str) -> str:
    objs = _bt("GET", "/project", params={"project_name": name})["objects"]
    if not objs:
        raise ConfigError(f"No Braintrust project named {name!r}.")
    return objs[0]["id"]


def parse_prompt_spec(spec: str) -> tuple[str | None, str, str | None]:
    """`[label=]ref[@version]` → (label, ref, version). ref is a URL, UUID or slug."""
    label = None
    if "=" in spec and not spec.startswith("http"):
        label, spec = spec.split("=", 1)
    version = None
    if "@" in spec:
        spec, version = spec.rsplit("@", 1)
    return (label or None), spec.strip(), (version.strip() or None) if version else None


def resolve_prompt_id(ref: str, project: str) -> str:
    if ref.startswith("http") or _UUID_RE.match(ref):
        return btp.parse_prompt_url(ref)
    objs = _bt("GET", "/prompt", params={"project_name": project, "slug": ref})["objects"]
    if not objs:
        raise ConfigError(f"No prompt with slug {ref!r} in project {project!r}.")
    return objs[0]["id"]


def resolve_dataset(ref: str, project: str) -> dict[str, Any]:
    if _UUID_RE.match(ref):
        return _bt("GET", f"/dataset/{ref}")
    objs = _bt("GET", "/dataset", params={"project_name": project, "dataset_name": ref})["objects"]
    if not objs:
        raise ConfigError(
            f"No dataset {ref!r} in project {project!r}. Create it first with "
            "data:save-to-braintrust-dataset --bulk-insert --create-if-missing."
        )
    return objs[0]


def fetch_rows(dataset_id: str, limit: int | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = None
    while True:
        body: dict[str, Any] = {"limit": 200}
        if cursor:
            body["cursor"] = cursor
        page = _bt("POST", f"/dataset/{dataset_id}/fetch", json=body)
        rows += page.get("events") or []
        cursor = page.get("cursor")
        if not cursor or not page.get("events") or (limit and len(rows) >= limit):
            break
    rows.sort(key=lambda r: r.get("created") or "")
    return rows[:limit] if limit else rows


def create_experiment(project_id: str, name: str, dataset_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return _bt("POST", "/experiment", json={
        "project_id": project_id, "name": name, "dataset_id": dataset_id,
        "metadata": metadata, "ensure_new": True,
    })


def insert_events(experiment_id: str, events: list[dict[str, Any]]) -> None:
    for i in range(0, len(events), 50):
        _bt("POST", f"/experiment/{experiment_id}/insert", json={"events": events[i:i + 50]})


def experiment_url(experiment_id: str, compare_to: str | None) -> str | None:
    params: dict[str, Any] = {"summarize_scores": "true"}
    if compare_to:
        params["comparison_experiment_id"] = compare_to
    try:
        return _bt("GET", f"/experiment/{experiment_id}/summarize", params=params).get("experiment_url")
    except ConfigError as e:
        print(f"warning: could not summarize experiment {experiment_id}: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------- OpenAI


def _client():
    from openai import OpenAI
    key = os.environ.get("OPENAI_EVALUATIONS_API_KEY")
    if not key:
        raise ConfigError("OPENAI_EVALUATIONS_API_KEY is not set (~/.claude/settings.json `env`).")
    return OpenAI(api_key=key)


def _chat_params(raw: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in (raw or {}).items():
        if k in {"use_cache", "store", "service_tier"}:
            continue
        if k == "verbosity" and v == "none":
            continue
        out[k] = v
    return out


# Chat-only params the Responses endpoint rejects; `max_tokens` is renamed instead.
_CHAT_ONLY = {"frequency_penalty", "presence_penalty", "stop", "logprobs", "top_logprobs", "n", "logit_bias"}


def _responses_params(raw: dict[str, Any]) -> dict[str, Any]:
    """Braintrust's chat-flavoured params in the shape /v1/responses takes."""
    out: dict[str, Any] = {}
    for k, v in _chat_params(raw).items():
        if k in _CHAT_ONLY:
            continue
        if k == "max_tokens":
            out["max_output_tokens"] = v
        elif k == "reasoning_effort":
            out.setdefault("reasoning", {})["effort"] = v
        elif k == "verbosity":
            out.setdefault("text", {})["verbosity"] = v
        else:
            out[k] = v
    return out


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Deep merge: `--params-json` wins key by key, nested dicts merged."""
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# Models that refused flex this run, so later rows skip the doomed first call.
_NO_FLEX: dict[str, str] = {}


def _call(client, api: str, kwargs: dict[str, Any], tier: str) -> Any:
    if api == "responses":
        # Raw POST: the typed SDK method rejects body fields it doesn't model, and a
        # production config copied verbatim (--params-json) may carry some.
        # store=false keeps eval traffic out of the project, so reasoning can't be
        # looked up server-side between hops: ask for it encrypted and pass it back.
        include = sorted(set(kwargs.get("include") or []) | {"reasoning.encrypted_content"})
        return client.post("/responses", cast_to=object,
                           body={**kwargs, "include": include, "service_tier": tier, "store": False})
    return client.chat.completions.create(**kwargs, service_tier=tier)


def _create(client, kwargs: dict[str, Any], tier: str, api: str = "chat") -> tuple[Any, str]:
    """One model call on `tier`, retrying on `auto` only for a flex refusal."""
    fallback = service_tier.FLEX_FALLBACK_TIER
    model = kwargs["model"]
    if tier == service_tier.TIERS["flex"] and model in _NO_FLEX:
        return _call(client, api, kwargs, fallback), f"{fallback} ({_NO_FLEX[model]})"
    try:
        return _call(client, api, kwargs, tier), tier
    except Exception as e:  # only a recognised flex refusal is retried; anything else re-raises
        reason = service_tier.flex_retry_reason(tier, e)
        if reason is None:
            raise
        if "does not offer" in reason and model not in _NO_FLEX:
            _NO_FLEX[model] = reason
            print(f"warning: {model}: {reason}; running it on {fallback} (standard rate).", file=sys.stderr)
        return _call(client, api, kwargs, fallback), f"{fallback} ({reason})"


def production_layout(messages: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """A rendered template in Intelligence Service 1.40's layout, for either
    Braintrust template shape (render_template.production_layout)."""
    return _production_layout(messages, metadata, instruction_role="developer")


def render_row(bundle: btp.PromptBundle, row: dict[str, Any], layout: str = "production") -> list[dict[str, Any]]:
    raw_input = row.get("input")
    ctx: dict[str, Any] = {"metadata": row.get("metadata") or {}}
    if isinstance(raw_input, dict):
        ctx["template_vars"] = raw_input
    else:
        ctx["input"] = raw_input or []
    messages, _warnings = render_messages(bundle.messages, ctx)
    return production_layout(messages, ctx["metadata"]) if layout == "production" else messages


def chat_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Responses-shape tools (`--tools-json`) in the Chat Completions shape."""
    return [{"type": "function", "function": {k: t[k] for k in ("name", "description", "parameters", "strict") if k in t}}
            for t in tools]


def run_row(client, bundle: btp.PromptBundle, row: dict[str, Any], tier: str,
            target_tool: str | None, stub: str, max_hops: int,
            api: str = "chat", overrides: dict[str, Any] | None = None,
            tools_override: list[dict[str, Any]] | None = None, layout: str = "production") -> dict[str, Any]:
    """Drive one row to the model's first answer. Non-target tool calls get `stub`."""
    messages = render_row(bundle, row, layout)
    if api == "responses":
        return _run_row_responses(client, bundle, messages, tier, target_tool, stub, max_hops, overrides,
                                  tools_override)
    kwargs: dict[str, Any] = {"model": bundle.model, "messages": messages,
                              **_merge(_chat_params(bundle.params), overrides or {})}
    tools = chat_tools(tools_override) if tools_override else bundle.openai_tools()
    if tools:
        kwargs["tools"] = tools
    else:
        kwargs.pop("tool_choice", None)

    calls: list[dict[str, Any]] = []
    text = ""
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    granted = None
    for hop in range(max_hops + 1):
        resp, granted = _create(client, kwargs, tier)
        if resp.usage:
            usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
            usage["completion_tokens"] += resp.usage.completion_tokens or 0
        msg = resp.choices[0].message
        text = msg.content or ""
        hop_calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            hop_calls.append({"id": tc.id, "name": tc.function.name, "arguments": args, "hop": hop})
        calls += hop_calls
        if not hop_calls or (target_tool and any(c["name"] == target_tool for c in hop_calls)):
            break
        if hop == max_hops:
            break
        assistant = {"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls]}
        kwargs["messages"] = kwargs["messages"] + [
            assistant,
            *({"role": "tool", "tool_call_id": c["id"], "content": stub} for c in hop_calls),
        ]
    return {"text": text, "tool_calls": calls, "usage": usage, "tier": granted}


def _run_row_responses(client, bundle: btp.PromptBundle, messages: list[dict[str, Any]], tier: str,
                       target_tool: str | None, stub: str, max_hops: int,
                       overrides: dict[str, Any] | None,
                       tools_override: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The same drive on /v1/responses, the API production's coach calls go through."""
    kwargs: dict[str, Any] = {"model": bundle.model,
                              "input": [{"role": m["role"], "content": m.get("content") or ""} for m in messages],
                              **_merge(_responses_params(bundle.params), overrides or {})}
    tools = tools_override or bundle.openai_responses_tools()
    if tools:
        kwargs["tools"] = tools
    else:
        kwargs.pop("tool_choice", None)
    calls: list[dict[str, Any]] = []
    text = ""
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    granted = None
    for hop in range(max_hops + 1):
        resp, granted = _create(client, kwargs, tier, api="responses")
        u = resp.get("usage") or {}
        usage["prompt_tokens"] += u.get("input_tokens") or 0
        usage["completion_tokens"] += u.get("output_tokens") or 0
        output = resp.get("output") or []
        text = "".join(c.get("text", "") for o in output if o.get("type") == "message"
                       for c in o.get("content") or [] if c.get("type") == "output_text")
        hop_calls = []
        for o in output:
            if o.get("type") != "function_call":
                continue
            try:
                args = json.loads(o.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": o.get("arguments")}
            hop_calls.append({"id": o.get("call_id"), "name": o.get("name"), "arguments": args, "hop": hop})
        calls += hop_calls
        if not hop_calls or (target_tool and any(c["name"] == target_tool for c in hop_calls)) or hop == max_hops:
            break
        # The whole output goes back, reasoning items included, as OpenAI's tool-call
        # loop does; dropping them loses the reasoning chain the next hop builds on.
        kwargs["input"] = kwargs["input"] + list(output) + [
            {"type": "function_call_output", "call_id": c["id"], "output": stub} for c in hop_calls]
    return {"text": text, "tool_calls": calls, "usage": usage, "tier": granted}


def judge_row(client, model: str, tier: str, row: dict[str, Any], out: dict[str, Any],
              rubric: str) -> tuple[float | None, str]:
    msgs = scorers.judge_messages(row.get("input"), out["text"], out["tool_calls"], rubric)
    resp, _ = _create(client, {"model": model, "messages": msgs}, tier)
    return scorers.parse_judge(resp.choices[0].message.content or "")


# ------------------------------------------------------------------------ main


def _row_rubric(row: dict[str, Any], args: argparse.Namespace, fixed: str | None) -> str:
    expected = scorers.lookup(row, args.label_field) if args.label_field else row.get("expected")
    if fixed:
        return fixed + (f"\n\nRow-specific expectation:\n{expected}" if expected else "")
    if not expected:
        raise ValueError("row has no rubric (empty expected) and no --rubric-file was given")
    return expected if isinstance(expected, str) else json.dumps(expected)


def arg_enum(bundle: btp.PromptBundle, tool: str | None, arg: str | None,
             tools_override: list[dict[str, Any]] | None = None) -> set[str] | None:
    """The argument's allowed values from the tool schema, folded like labels; None if open."""
    if not tool or not arg:
        return None
    if tools_override:
        params = next((t.get("parameters") or {} for t in tools_override if t.get("name") == tool), None)
    else:
        params = bundle.tools_by_name[tool].parameters if tool in bundle.tools_by_name else None
    if params is None:
        return None
    prop = (params.get("properties") or {}).get(arg) or {}
    values = prop.get("enum")
    return {scorers.normalize_label(v)[1] or "" for v in values if isinstance(v, str)} if values else None


def score_one(client, bundle, row, args, tier, fixed_rubric, trial: int = 0) -> dict[str, Any]:
    md = row.get("metadata") or {}
    group = scorers.lookup(row, args.group_field) if args.group_field else None
    base = {"row_id": row["id"], "case_id": md.get("case_id"), "trial": trial,
            "group": group if isinstance(group, str) or group is None else json.dumps(group)}
    if args.scorer == "tool-call":
        # Checked before the model call: a label the tool can never emit would
        # score every version wrong, which reads as a prompt regression.
        try:
            trig, val = scorers.normalize_label(scorers.lookup(row, args.label_field))
        except scorers.LabelError as e:
            return {**base, "error": f"label: {e}", "scores": {}, "label": None}
        enum = arg_enum(bundle, args.tool, args.arg, args.tools_override)
        if trig and val is not None and enum is not None and val not in enum:
            return {**base, "error": f"label {val!r} is not a value of {args.tool}.{args.arg}",
                    "scores": {}, "label": val}
    try:
        out = run_row(client, bundle, row, tier, args.tool, args.stub, args.max_hops,
                      api=args.api, overrides=args.params_override, tools_override=args.tools_override,
                      layout=getattr(args, "layout", "production"))
    except Exception as e:  # recorded on the row; the run continues
        return {**base, "error": f"{type(e).__name__}: {e}"[:500], "scores": {}, "label": None}
    rec: dict[str, Any] = {**base, "output": out, "error": None}
    if args.scorer == "tool-call":
        label = scorers.lookup(row, args.label_field)
        rec["label"] = label
        try:
            s = scorers.score_tool_call(out["tool_calls"], label, args.tool, args.arg)
        except scorers.LabelError as e:
            return {**rec, "error": f"label: {e}", "scores": {}}
        if s.get("skip"):
            # An "either is fine" row: kept in the experiment, counted, not scored.
            rec.update(s)
            rec["arg_score"] = None
            return rec
        rec.update(s)
        rec["arg_score"] = s["scores"].get(f"{args.arg}_correct") if args.arg else None
    else:
        try:
            rubric = _row_rubric(row, args, fixed_rubric)
            score, reason = judge_row(client, args.judge_model, tier, row, out, rubric)
        except Exception as e:
            return {**rec, "error": f"judge: {type(e).__name__}: {e}"[:500], "scores": {}, "label": None}
        rec.update({"label": rubric[:120], "judge_score": score, "judge_reason": reason,
                    "scores": {"judge": score}})
    return rec


def _event(row: dict[str, Any], rec: dict[str, Any], bundle: btp.PromptBundle, trials: int = 1) -> dict[str, Any]:
    md = {k: v for k, v in (row.get("metadata") or {}).items() if k not in DROP_METADATA_KEYS}
    md.update({"prompt_slug": bundle.slug, "prompt_version": bundle.version})
    out = rec.get("output") or {}
    if out.get("tier"):
        md["service_tier"] = out["tier"]
    if rec.get("judge_reason"):
        md["judge_reason"] = rec["judge_reason"]
    ev: dict[str, Any] = {
        # One event per trial, all linked to the same dataset record.
        "id": row["id"] if trials == 1 else f"{row['id']}:t{rec.get('trial', 0)}",
        "dataset_record_id": row["id"],
        "input": row.get("input"),
        "expected": row.get("expected"),
        "output": {"text": out.get("text"), "tool_calls": [
            {"name": c["name"], "arguments": c["arguments"]} for c in out.get("tool_calls", [])]},
        "scores": rec.get("scores") or {},
        "metadata": md,
    }
    if out.get("usage"):
        ev["metrics"] = dict(out["usage"])
    if rec.get("error"):
        ev["error"] = rec["error"]
    return ev


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare prompt versions on a Braintrust dataset.")
    p.add_argument("--prompt", action="append", required=True,
                   help="[label=]url|uuid|slug[@version]. Repeat per version; the first is the baseline.")
    p.add_argument("--dataset", required=True, help="Dataset UUID or name.")
    p.add_argument("--project", default=DEFAULT_PROJECT,
                   help=f"Project for slug / dataset-name lookup (default {DEFAULT_PROJECT}).")
    p.add_argument("--scorer", choices=["tool-call", "llm-judge"], required=True)
    p.add_argument("--tool", help="tool-call: tool that must / must not be called.")
    p.add_argument("--arg", help="tool-call: argument whose value is compared to the label.")
    p.add_argument("--label-field", default=None,
                   help="Dotted path to the row label (tool-call, default expected) or rubric (llm-judge).")
    p.add_argument("--rubric-file", help="llm-judge: rubric applied to every row.")
    p.add_argument("--judge-model", default="gpt-5.4", help="llm-judge model (default gpt-5.4).")
    p.add_argument("--stub", default=DEFAULT_STUB, help="Reply given to every non-target tool call.")
    p.add_argument("--max-hops", type=int, default=2, help="Stubbed tool round-trips before stopping.")
    p.add_argument("--api", choices=["chat", "responses"], default="chat",
                   help="OpenAI endpoint. Use the one production uses for this prompter (coach: responses).")
    p.add_argument("--layout", choices=["production", "template"], default="production",
                   help="production (default): send Intelligence Service 1.40's layout (developer/user `---` pair, "
                        "context as a developer message, metadata.developerInstructions last unless the row's "
                        "input already ends with a `### Current Time` message). Works for both template shapes: "
                        "an appended-context template is split, a delimited one only has its roles set. "
                        "template: send the template as rendered (system roles, no time message added).")
    p.add_argument("--trials", type=int, default=1,
                   help="Runs per row per version. The model is not deterministic; use 3-5 before trusting a difference.")
    p.add_argument("--tools-json",
                   help="File with the tool definitions production sends (Responses shape, e.g. the `tools` of a stored "
                        "production response). Replaces the Braintrust tool functions for every version.")
    p.add_argument("--params-json",
                   help="JSON (or a file holding it) merged over the prompt's params for every version: the runtime "
                        "config production sets outside Braintrust, e.g. '{\"reasoning\":{\"effort\":\"medium\"},\"top_p\":0.98}'.")
    p.add_argument("--group-field", default="metadata.issue",
                   help="Dotted path to a row's group (default metadata.issue): the readout breaks results down by it.")
    p.add_argument("--limit", type=int, help="Only the first N rows (by creation).")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--name", help="Experiment name prefix (default compare-<date>).")
    p.add_argument("--out", required=True, help="Directory for results.json / summary.md.")
    p.add_argument("--dry-run", action="store_true", help="Resolve and render only; no model calls, no writes.")
    service_tier.add_argument(p)
    a = p.parse_args()
    if len(a.prompt) < 2:
        p.error("pass --prompt at least twice")
    if a.scorer == "tool-call":
        if not a.tool:
            p.error("--scorer tool-call needs --tool")
        a.label_field = a.label_field or "expected"
    try:
        raw = a.params_json or ""
        # A path to a file (what reproduce.py --params-out writes) or the JSON itself.
        if raw and not raw.lstrip().startswith("{") and Path(raw).is_file():
            raw = Path(raw).read_text(encoding="utf-8")
        a.params_override = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        p.error(f"--params-json is not valid JSON: {e}")
    if not isinstance(a.params_override, dict):
        p.error("--params-json must be a JSON object")
    a.tools_override = None
    if a.tools_json:
        try:
            a.tools_override = json.loads(Path(a.tools_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            p.error(f"--tools-json: {e}")
        if not isinstance(a.tools_override, list) or not all(isinstance(t, dict) and t.get("name") for t in a.tools_override):
            p.error("--tools-json must be a JSON list of tool objects with a name")
    if a.trials < 1:
        p.error("--trials must be at least 1")
    return a


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        tier = service_tier.wire_value(args.service_tier, policy=args.on_tier_unavailable)
        dataset = resolve_dataset(args.dataset, args.project)
        rows = fetch_rows(dataset["id"], args.limit)
        if not rows:
            raise ConfigError(f"Dataset {dataset['name']!r} has no rows.")
        bundles: list[tuple[str, btp.PromptBundle]] = []
        for spec in args.prompt:
            label, ref, version = parse_prompt_spec(spec)
            bundle = btp.fetch_prompt(resolve_prompt_id(ref, args.project), version)
            if bundle.provider != "openai":
                raise ConfigError(f"{bundle.slug}: model {bundle.model!r} is not OpenAI; v1 runs Chat Completions only.")
            tool_names = [t["name"] for t in args.tools_override] if args.tools_override else list(bundle.tools_by_name)
            if args.tool and args.tool not in tool_names:
                raise ConfigError(f"{bundle.slug}@{bundle.version} has no tool {args.tool!r} "
                                  f"(has: {', '.join(tool_names)}).")
            bundles.append((label or f"{bundle.slug}@{bundle.version}", bundle))
        labels = [lbl for lbl, _ in bundles]
        if len(set(labels)) != len(labels):
            raise ConfigError(f"duplicate version labels {labels}; name them with label=…")
        fixed_rubric = Path(args.rubric_file).read_text(encoding="utf-8") if args.rubric_file else None
    except (ConfigError, btp.BraintrustError, service_tier.ServiceTierError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    plan = {
        "dataset": {"id": dataset["id"], "name": dataset["name"], "rows": len(rows)},
        "versions": [{"label": lbl, "prompt_id": b.prompt_id, "slug": b.slug, "version": b.version,
                      "model": b.model, "params": _chat_params(b.params), "tools": list(b.tools_by_name)}
                     for lbl, b in bundles],
        "scorer": args.scorer, "tool": args.tool, "arg": args.arg, "label_field": args.label_field,
        "service_tier": tier, "api": args.api, "layout": args.layout, "trials": args.trials, "params_override": args.params_override,
        "tools": "--tools-json (" + ", ".join(t["name"] for t in args.tools_override) + ")" if args.tools_override
                 else "Braintrust tool functions",
        "model_calls_upper_bound": len(rows) * args.trials * len(bundles) * (
            args.max_hops + 1 + (1 if args.scorer == "llm-judge" else 0)),
    }
    if args.scorer == "tool-call":
        dist: dict[str, int] = {}
        bad = 0
        for r in rows:
            try:
                trig, val = scorers.normalize_label(scorers.lookup(r, args.label_field))
                key = "either" if trig is None else ((val or "trigger") if trig else "no_trigger")
            except scorers.LabelError:
                bad, key = bad + 1, "UNREADABLE"
            dist[key] = dist.get(key, 0) + 1
        enum = arg_enum(bundles[0][1], args.tool, args.arg, args.tools_override)
        plan["label_distribution"] = dist
        plan["unreadable_labels"] = bad
        if enum is not None:
            outside = {k: n for k, n in dist.items() if k not in enum | {"trigger", "no_trigger", "either"}}
            plan["labels_outside_tool_enum"] = outside
            plan["tool_enum"] = sorted(enum)
    print(json.dumps(plan, indent=2))
    if args.dry_run:
        try:
            render_messages(bundles[0][1].messages, {"input": rows[0].get("input") or [],
                                                     "metadata": rows[0].get("metadata") or {}})
            print("dry run: first row renders against the baseline template; nothing was called or written.")
        except TemplateError as e:
            print(f"dry run: render failed on the first row: {e}", file=sys.stderr)
            return 2
        return 0

    try:
        client = _client()
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    prefix = args.name or f"compare-{dt.date.today().isoformat()}"
    results: dict[str, Any] = {"scorer": args.scorer, "tool": args.tool, "arg": args.arg,
                               "dataset": plan["dataset"], "versions": []}
    baseline_exp = None
    exit_code = 0
    for label, bundle in bundles:
        exp = create_experiment(dataset["project_id"], f"{prefix} · {label}", dataset["id"], {
            "prompt_id": bundle.prompt_id, "prompt_slug": bundle.slug, "prompt_version": bundle.version,
            "model": bundle.model, "scorer": args.scorer, "tool": args.tool, "arg": args.arg,
            "service_tier": tier, "runner": "eng:compare-prompt-versions",
        })
        with cf.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            work = [(r, t) for r in rows for t in range(args.trials)]
            recs = list(pool.map(lambda rt: score_one(client, bundle, rt[0], args, tier, fixed_rubric, rt[1]), work))
        insert_events(exp["id"], [_event(r, rec, bundle, args.trials) for (r, _t), rec in zip(work, recs)])
        url = experiment_url(exp["id"], baseline_exp)
        baseline_exp = baseline_exp or exp["id"]
        failed = sum(1 for r in recs if r.get("error"))
        print(f"{label}: {len(recs) - failed}/{len(recs)} rows scored → {url or exp['id']}", file=sys.stderr)
        if failed == len(recs):
            exit_code = 3
        results["versions"].append({"label": label, "experiment_id": exp["id"], "experiment_url": url,
                                    "prompt_version": bundle.version, "rows": recs})

    (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    summary = summarize.summarize(results)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    md = summarize.render_markdown(summary, bundles[0][0])
    (out_dir / "summary.md").write_text(md, encoding="utf-8")
    diff = prompt_diff.render(bundles)
    (out_dir / "prompt_diff.md").write_text(diff, encoding="utf-8")
    print(f"prompt changes vs {bundles[0][0]}: {out_dir / 'prompt_diff.md'}", file=sys.stderr)
    sys.stdout.write(md)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
