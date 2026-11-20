"""Reproduce guardrail triggers and turn them into eval-dataset rows.

The `guardrail-dataset` follow-up, as four subcommands run in order. Every file
they write holds conversation text, so it stays in the working directory; only
`rows` output is de-identified, and it is written for
`data:save-to-braintrust-dataset --bulk-insert` to load after a confirmation.

    rebuild     the coach request behind each alert (exact or rebuilt), plus production's tools/params
    replay      send rebuilt (or de-identified) requests; did the guardrail fire again?
    deidentify  replace names, places, employers and contacts; check for residue
    rows        reproduced, verdict-labelled alerts as JSONL dataset rows

The llmCall ledger keeps one row per tool-call loop, under the loop's last call,
so the coach call that ended in `handoffToGuardrails` has no row of its own. A
row written from Intelligence Service 1.32.2 on lists every call of its loop in
`callChain`, so `rebuild` fetches that coach call as it was sent (`exact`). Older
rows have no chain, and `rebuild` reconstructs the call instead (`rebuilt`):
model, params, tools and system message from the conversation's previous coach
call, and the conversation (ending in the message that fired) from the guardrails
handler's input. A rebuilt alert records how old that system message is, because
its context block is per turn.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import responses_reader as rr  # noqa: E402
from coach_calls import (CONTEXT_MARKER, PARAM_KEYS, REQUEST_KEYS, apply_mapping, build_mapping,  # noqa: E402,F401
                         coach_messages, coach_parts, conversation_of, deidentify, exact_request, item_text,
                         post_responses, production_config, residue, split_system, with_parts)
from fetch_conversation import CONVERSATION_PROJECT_ID  # noqa: E402

COACH = "coach-prompter"
HANDLER = "guardrails-handler-prompter"
TOOL = "handoffToGuardrails"



# ------------------------------------------------------------------ pure parts


def rebuild_request(coach_resp: dict[str, Any], coach_items: list[dict[str, Any]],
                    handler_items: list[dict[str, Any]], marker: str = CONTEXT_MARKER) -> dict[str, Any]:
    """The coach request that fired. Items are oldest-first.

    Everything before the previous call's conversation (system message, any `---`
    delimiters, the context) and its trailing developer messages (the time) are
    kept in place; the conversation between them is the handler's.
    """
    prev, _ = coach_messages(coach_items)
    if not prev or prev[0]["role"] not in ("system", "developer"):
        raise ValueError("previous coach call has no system message")
    parts = coach_parts(prev, marker, fired=False)
    start = parts["conversation_index"][0] if parts["conversation_index"] else parts["after_conversation"]
    conversation = conversation_of(coach_messages(handler_items)[0])
    if not conversation or conversation[-1]["role"] != "user":
        raise ValueError("handler input does not end with the user message that fired")
    req = {k: coach_resp[k] for k in REQUEST_KEYS if coach_resp.get(k) is not None}
    req["input"] = (prev[:start] + conversation
                    + [m for m in prev[parts["after_conversation"]:] if m["role"] == "developer"])
    return req


def _entry(req: dict[str, Any], marker: str, meta: dict[str, Any]) -> dict[str, Any]:
    """One `rebuild` entry: the request, its parts, and the marker they were read with."""
    parts = coach_parts(req["input"], marker)
    return {"request": req, "context": parts["context"], "developer": parts["developer"],
            "context_marker": marker, "meta": {**meta, "layout": parts["layout"], "turns": len(parts["conversation"])}}


def conversation_for(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The user/assistant messages of a `rebuild` entry's request."""
    return coach_parts(entry["request"]["input"], entry.get("context_marker", CONTEXT_MARKER))["conversation"]


def chain_coach_id(row: dict[str, Any]) -> str | None:
    """The coach call that handed off to this handler row, from its `callChain`.

    The chain is the loop's calls in order and ends at the row's own call. The
    handoff came from the last coach call before the first handler call: an
    earlier coach entry is a tool hop (findSimilarInMemory) in the same turn.
    """
    chain = row.get("callChain")
    if isinstance(chain, str):
        try:
            chain = json.loads(chain)
        except json.JSONDecodeError:
            return None
    if not isinstance(chain, list):
        return None
    coach = None
    for entry in chain:
        if not isinstance(entry, dict):
            continue
        if entry.get("prompterName") == HANDLER:
            return coach
        if entry.get("prompterName") == COACH and entry.get("openaiId"):
            coach = entry["openaiId"]
    return None


def production_category(rebuilt_alert: dict[str, Any], turn: dict[str, Any] | None) -> str | None:
    """The category production chose: read off the exact call when there is one, else from the logs."""
    return (rebuilt_alert.get("meta") or {}).get("production_category") or (turn or {}).get("category")


def fired(output: list[dict[str, Any]], tool: str = TOOL) -> tuple[bool, str | None]:
    for o in output or []:
        if o.get("type") == "function_call" and o.get("name") == tool:
            try:
                return True, (json.loads(o.get("arguments") or "{}") or {}).get("category")
            except json.JSONDecodeError:
                return True, None
    return False, None


def row_for(ts: str, rebuilt: dict[str, Any], deid: dict[str, Any], label: dict[str, Any],
            production_category: str | None, case_id: str) -> dict[str, Any]:
    md = {
        "case_id": case_id,
        "contextInstructions": deid["context"],
        "developerInstructions": rebuilt.get("developer") or None,
        "production_category": production_category,
        "pattern": label.get("pattern"),
        "note": label.get("note"),
        "source_alert_ts": ts,
        "deidentified": True,
        "reproduced_on_replay": True,
        "prompter_version": rebuilt["meta"].get("coach_version"),
        "request_source": rebuilt["meta"].get("source", "rebuilt"),
    }
    expected = label["expected"]
    return {"input": deid["input"], "expected": {"category": expected}, "metadata": md,
            "tags": ["guardrail", "false-positive" if expected == "no_trigger" else "true-positive"]}


def select_rows(rebuilt: dict[str, Any], replay: dict[str, Any], deid_replay: dict[str, Any],
                deid: dict[str, Any], labels: dict[str, Any], turns: dict[str, Any],
                prefix: str) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Rows for alerts that reproduced before AND after de-identification. Everything else is counted."""
    skipped: dict[str, list[str]] = {}
    rows = []
    for ts in sorted(labels):
        why = None
        if "request" not in rebuilt.get(ts, {}):
            why = "not_rebuilt"
        elif not (replay.get(ts) or {}).get("reproduced"):
            why = "not_reproduced"
        elif ts not in deid:
            why = "not_deidentified"
        elif deid[ts].get("residue"):
            why = "pii_residue"
        elif not (deid_replay.get(ts) or {}).get("reproduced"):
            why = "changed_after_deid"
        if why:
            skipped.setdefault(why, []).append(ts)
            continue
        rows.append(row_for(ts, rebuilt[ts], deid[ts], labels[ts], production_category(rebuilt[ts], turns.get(ts)),
                            f"{prefix}-{len(rows) + 1:02d}"))
    return rows, skipped


# ------------------------------------------------------------------ network


def _post(body: dict[str, Any]) -> dict[str, Any]:
    key = os.environ.get("OPENAI_EVALUATIONS_API_KEY")
    if not key:
        raise SystemExit("OPENAI_EVALUATIONS_API_KEY is not set (~/.claude/settings.json `env`).")
    return post_responses(key, body)


def cmd_rebuild(a: argparse.Namespace) -> int:
    key = os.environ.get("OPENAI_CONVERSATION_API_KEY")
    if not key:
        raise SystemExit("OPENAI_CONVERSATION_API_KEY is not set: stored production responses need it.")
    out: dict[str, Any] = {}
    config = None
    for path in sorted(Path(a.cache_dir).glob("*.json")):
        ts = path.stem
        conv = json.loads(path.read_text(encoding="utf-8"))
        tx = (conv.get("alert_transaction") or "").lower()
        try:
            rows = sorted(rr.fetch_ledger_rows_by_conversation(conv["conversation_id"]), key=lambda r: r["createdAt"])
        except rr.ResponsesReaderError as e:
            out[ts] = {"error": f"ledger: {str(e)[:200]}"}
            continue
        handler = next((r for r in rows if (r.get("transaction") or "").lower() == tx
                        and r.get("prompterName") == HANDLER), None)
        if handler is None:
            out[ts] = {"error": "no handler call in the ledger for the alert transaction"}
            continue
        cid = chain_coach_id(handler)
        if cid:
            resp = rr.fetch_response(key, CONVERSATION_PROJECT_ID, cid)
            did_fire, prod_cat = fired(resp.get("output") or [])
            if not did_fire:
                out[ts] = {"error": "callChain coach call did not call handoffToGuardrails"}
                continue
            coach_items = list(reversed(rr.fetch_input_items(key, CONVERSATION_PROJECT_ID, cid)))
            try:
                req, others = exact_request(resp, coach_items, a.context_marker)
                out[ts] = _entry(req, a.context_marker, {
                    "source": "exact", "coach_version": handler.get("version"), "system_age_s": 0,
                    "service_tier": resp.get("service_tier"), "items_left_out": others,
                    "production_category": prod_cat})
            except ValueError as e:
                out[ts] = {"error": str(e)}
                continue
            config = config or production_config(resp)
            continue
        coach = [r for r in rows if r.get("prompterName") == COACH and r["createdAt"] < handler["createdAt"]]
        if not coach:
            out[ts] = {"error": "no callChain and no earlier coach call in the ledger to copy settings from"}
            continue
        prev = coach[-1]
        resp = rr.fetch_response(key, CONVERSATION_PROJECT_ID, prev["openaiId"])
        coach_items = list(reversed(rr.fetch_input_items(key, CONVERSATION_PROJECT_ID, prev["openaiId"])))
        handler_items = list(reversed(rr.fetch_input_items(key, CONVERSATION_PROJECT_ID, handler["openaiId"])))
        age = (dt.datetime.fromisoformat(handler["createdAt"].replace("Z", "+00:00"))
               - dt.datetime.fromisoformat(prev["createdAt"].replace("Z", "+00:00"))).total_seconds()
        try:
            req = rebuild_request(resp, coach_items, handler_items, a.context_marker)
            out[ts] = _entry(req, a.context_marker, {
                "source": "rebuilt", "coach_version": prev.get("version"), "system_age_s": int(age),
                "service_tier": resp.get("service_tier")})
        except ValueError as e:
            out[ts] = {"error": str(e)}
            continue
        config = config or production_config(resp)
    Path(a.out).write_text(json.dumps(out), encoding="utf-8")
    if config:
        Path(a.tools_out).write_text(json.dumps(config[0]), encoding="utf-8")
        Path(a.params_out).write_text(json.dumps(config[1]), encoding="utf-8")
    for ts, v in sorted(out.items(), reverse=True):
        m = v.get("meta") or {}
        print(ts, v.get("error") or f"{m['source']} · {m['turns']} turns · system {m['system_age_s']}s old "
                                    f"· coach {m['coach_version']}"
                                    + (f" · {m['items_left_out']} tool items left out" if m.get("items_left_out") else ""))
    sources = [v["meta"]["source"] for v in out.values() if "request" in v]
    print(json.dumps({"alerts": len(out), "exact": sources.count("exact"), "rebuilt": sources.count("rebuilt"),
                      "not_rebuilt": sum(1 for v in out.values() if "error" in v)}))
    return 0


def _request_for(ts: str, rebuilt: dict[str, Any], deid: dict[str, Any] | None) -> dict[str, Any]:
    req = json.loads(json.dumps(rebuilt[ts]["request"]))
    if deid is not None:
        req["input"] = with_parts(req["input"], rebuilt[ts].get("context_marker", CONTEXT_MARKER),
                                  context=deid[ts]["context"], conversation=deid[ts]["input"])
    # Production's own tier, so the replay is the same call; store=false keeps it out of the project.
    req.update(store=False, service_tier=rebuilt[ts]["meta"].get("service_tier") or "default")
    return req


def cmd_replay(a: argparse.Namespace) -> int:
    rebuilt = json.loads(Path(a.rebuilt).read_text(encoding="utf-8"))
    turns = json.loads(Path(a.turns).read_text(encoding="utf-8"))
    deid = json.loads(Path(a.deid).read_text(encoding="utf-8")) if a.deid else None
    todo = [ts for ts, v in rebuilt.items() if "request" in v and (deid is None or ts in deid)]

    def one(job: tuple[str, int]) -> tuple[str, Any]:
        ts, _trial = job
        try:
            return ts, fired(_post(_request_for(ts, rebuilt, deid)).get("output") or [])
        except RuntimeError as e:
            return ts, str(e)

    runs: dict[str, list[Any]] = {}
    with cf.ThreadPoolExecutor(a.concurrency) as pool:
        for ts, res in pool.map(one, [(ts, t) for ts in todo for t in range(a.trials)]):
            runs.setdefault(ts, []).append(res)
    out = {}
    for ts in todo:
        prod_cat = production_category(rebuilt[ts], turns.get(ts))
        ok = [r for r in runs[ts] if isinstance(r, tuple)]
        same = sum(1 for trig, cat in ok if trig and cat == prod_cat)
        # Reproduced = fired with production's category on a majority of trials.
        out[ts] = {"production_category": prod_cat, "trials": len(runs[ts]), "same_category": same,
                   "errors": len(runs[ts]) - len(ok), "reproduced": same * 2 > len(runs[ts]),
                   "outcomes": [list(r) if isinstance(r, tuple) else r for r in runs[ts]]}
        print(ts, f"production {prod_cat} · same on {same}/{len(runs[ts])}"
              + (f" · {out[ts]['errors']} errors" if out[ts]["errors"] else ""))
    Path(a.out).write_text(json.dumps(out), encoding="utf-8")
    print(json.dumps({"replayed": len(out), "reproduced": sum(1 for v in out.values() if v["reproduced"])}))
    return 0


def cmd_deidentify(a: argparse.Namespace) -> int:
    rebuilt = json.loads(Path(a.rebuilt).read_text(encoding="utf-8"))
    keep = a.ts.split(",")

    def one(ts: str) -> tuple[str, dict[str, Any]]:
        v = rebuilt[ts]
        cache = json.loads((Path(a.cache_dir) / f"{ts}.json").read_text(encoding="utf-8"))
        user = cache.get("aura_user") or {}
        # The cache carries givenName + email only (fetch_conversation.py keeps the
        # account record minimal); residue() derives what it can from the email.
        return ts, deidentify(_post, v["context"], conversation_for(v),
                              [user.get("givenName"), user.get("email")], a.model)

    with cf.ThreadPoolExecutor(a.concurrency) as pool:
        out = dict(pool.map(one, keep))
    Path(a.out).write_text(json.dumps(out), encoding="utf-8")
    for ts, v in out.items():
        print(ts, f"replaced {v['replaced']} · known-identifier residue {v['residue']}")
    return 0


def cmd_rows(a: argparse.Namespace) -> int:
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    rows, skipped = select_rows(load(a.rebuilt), load(a.replay), load(a.deid_replay), load(a.deid),
                                load(a.labels), load(a.turns), a.case_prefix)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    labels = [r["expected"]["category"] for r in rows]
    print(json.dumps({"rows": len(rows), "no_trigger": labels.count("no_trigger"),
                      "trigger": len(labels) - labels.count("no_trigger"),
                      "skipped": {k: len(v) for k, v in skipped.items()}}))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rebuild", help="Fetch (or rebuild) the coach request behind each cached alert.")
    r.add_argument("--cache-dir", required=True, help="extract_turns.py's --cache-dir")
    r.add_argument("--out", required=True)
    r.add_argument("--tools-out", required=True, help="production tool definitions, for --tools-json")
    r.add_argument("--params-out", required=True, help="production runtime params, for --params-json")
    r.add_argument("--context-marker", default=CONTEXT_MARKER)
    r.set_defaults(fn=cmd_rebuild)
    rp = sub.add_parser("replay", help="Send rebuilt requests; did the guardrail fire again?")
    rp.add_argument("--rebuilt", required=True)
    rp.add_argument("--turns", required=True, help="extract_turns.py's turns.json (production category)")
    rp.add_argument("--deid", help="replay the de-identified versions instead")
    rp.add_argument("--trials", type=int, default=1)
    rp.add_argument("--concurrency", type=int, default=5)
    rp.add_argument("--out", required=True)
    rp.set_defaults(fn=cmd_replay)
    d = sub.add_parser("deidentify", help="Replace identifying details in the chosen alerts.")
    d.add_argument("--rebuilt", required=True)
    d.add_argument("--cache-dir", required=True)
    d.add_argument("--ts", required=True, help="comma-separated alert ts values")
    d.add_argument("--model", default="gpt-5.4")
    d.add_argument("--concurrency", type=int, default=6)
    d.add_argument("--out", required=True)
    d.set_defaults(fn=cmd_deidentify)
    w = sub.add_parser("rows", help="Write dataset rows for the alerts that reproduced.")
    for flag in ("rebuilt", "replay", "deid", "deid-replay", "labels", "turns", "out"):
        w.add_argument(f"--{flag}", required=True)
    w.add_argument("--case-prefix", default="gfp")
    w.set_defaults(fn=cmd_rows)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
