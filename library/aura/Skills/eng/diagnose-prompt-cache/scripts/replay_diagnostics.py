#!/usr/bin/env python3
"""Ask OpenAI why a request missed the prompt cache, by replaying a pair of calls.

For each pair (an earlier call A and a later call B that should share A's
prefix — usually consecutive turns of one conversation):

1. Read both originals from OpenAI: settings from GET /v1/responses/{id},
   inputs from GET /v1/responses/{id}/input_items. Needs a key for the project
   that made the calls with the `api.responses.read` scope — the org admin key
   does not have it.
2. Replay A, then replay B with `prompt_cache_options.comparison_response_id`
   set to the replayed A. Both replays come from the same source, so the
   comparison reports differences between the two turns, not between a replay
   and the original.
3. Report `prompt_cache_diagnostics` (type, reason, reusable / missed tokens)
   and the cached tokens on B.

Diagnostics exist only on the Responses API for GPT-5.6+ models. Older models
answer `unavailable`. A single `unavailable` can also be a transient miss, so
the comparison is retried once; a model that answers `unavailable` twice stops
the run rather than paying for the remaining pairs.

Spends money: two to three model calls per pair. Without --yes it prints the
plan and exits. Replays run on the **default** tier with a small output cap
(`--max-output-tokens`, default 512). The cap must leave room for the model to
finish: a replayed A that ends `incomplete` (a reasoning model that runs out of
output tokens) cannot be compared against, and B then comes back
`comparison_response_not_found` or `unavailable`. The script checks A's status
and stops the pair instead of reading that as a model limitation.
They do not use flex: on flex, diagnostics always answer `unavailable`
(measured on gpt-5.6-terra: flex 0/2, default 4/4 `cache_hit`), so a flex
replay pays for a request that cannot measure anything. Prompt text is never
printed.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
import pathlib
import re
from datetime import datetime, timedelta, timezone

import requests


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


OPENAI = "https://api.openai.com/v1"
MAX_PAIRS = 10
# Request settings copied from the original so the replay renders the same
# prefix. Anything that changes the prefix (tools, text format, reasoning
# effort) must match, or the comparison reports that instead of the real cause.
COPIED_SETTINGS = (
    "instructions",
    "model",
    "parallel_tool_calls",
    "prompt_cache_key",
    "temperature",
    "tool_choice",
    "tools",
    "top_p",
    "truncation",
)


def send(method: str, url: str, **kwargs) -> requests.Response:
    """Retry network errors, 429 and 5xx; return anything else to the caller."""
    last = None
    for attempt in range(4):
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.RequestException as error:
            last = str(error)
        else:
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
            last = f"HTTP {resp.status_code}"
        time.sleep(3 * (attempt + 1))
    sys.exit(f"{method} {url} failed after 4 attempts: {last}")


def openai_get(path: str, key: str, params: dict | None = None) -> dict:
    resp = send("GET", f"{OPENAI}{path}", headers={"Authorization": f"Bearer {key}"}, params=params, timeout=60)
    if resp.status_code == 401 and "api.responses.read" in resp.text:
        sys.exit("The key lacks api.responses.read — use a key for the project that made the calls, not the admin key.")
    if not resp.ok:
        sys.exit(f"GET {path} failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()


def input_items(response_id: str, key: str) -> list[dict]:
    items, after = [], None
    while True:
        params = {"limit": 100, "order": "asc"}
        if after:
            params["after"] = after
        page = openai_get(f"/responses/{response_id}/input_items", key, params)
        items += page.get("data") or []
        if not page.get("has_more"):
            return items
        after = page.get("last_id")


def clean_item(item: dict) -> dict:
    """Turn a stored input item back into a request input item: drop the
    server-assigned id/status, and reduce content parts to type + text."""
    item = {k: v for k, v in item.items() if k not in ("id", "status")}
    if item.get("type") == "message" and isinstance(item.get("content"), list):
        item["content"] = [
            {"type": part.get("type"), "text": part.get("text", "")} if "text" in part else part
            for part in item["content"]
        ]
    return item


def load_original(response_id: str, key: str) -> dict:
    original = openai_get(f"/responses/{response_id}", key)
    body = {k: original[k] for k in COPIED_SETTINGS if original.get(k) is not None}
    if original.get("reasoning", {}).get("effort"):
        body["reasoning"] = {"effort": original["reasoning"]["effort"]}
    text = original.get("text") or {}
    if text:
        body["text"] = {k: v for k, v in text.items() if k in ("format", "verbosity") and v is not None}
    body["input"] = [clean_item(i) for i in input_items(response_id, key)]
    body["_inputTokens"] = (original.get("usage") or {}).get("input_tokens")
    return body


def replay(original: dict, key: str, tier: str, comparison: str | None, max_output: int) -> dict:
    body = {k: v for k, v in original.items() if not k.startswith("_")}
    body.update(max_output_tokens=max_output, store=True, service_tier=tier)
    if comparison:
        body["prompt_cache_options"] = {"comparison_response_id": comparison}
    resp = send("POST", f"{OPENAI}/responses", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=180)
    if not resp.ok:
        sys.exit(f"Replay failed: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def ledger_pairs(prompter: str, hours: float, wanted: int) -> list[tuple[str, str]]:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    try:
        rows = llm_ledger.rows({
            "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 1000,
            "prompterName": prompter,
        })
    except llm_ledger.LedgerError as error:
        sys.exit(f"Ledger read failed: {error}")
    by_conversation = collections.defaultdict(list)
    for row in rows:
        # `openaiId` can be a JSON null, which .get's default does not cover.
        if row.get("conversationId") and (row.get("openaiId") or "").startswith("resp_"):
            by_conversation[row["conversationId"]].append(row)
    # Prefer pairs where B actually missed: those are the ones worth explaining.
    candidates = []
    for calls in by_conversation.values():
        calls.sort(key=lambda r: r["createdAt"])
        for a, b in zip(calls, calls[1:]):
            gap = (datetime.fromisoformat(b["createdAt"].replace("Z", "+00:00"))
                   - datetime.fromisoformat(a["createdAt"].replace("Z", "+00:00"))).total_seconds()
            if gap <= 600:
                candidates.append((not b.get("cachedTokens"), a["openaiId"], b["openaiId"]))
    candidates.sort(key=lambda c: not c[0])
    return [(a, b) for _, a, b in candidates[:wanted]]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pair", nargs=2, action="append", metavar=("EARLIER_ID", "LATER_ID"),
                     help="explicit response ids, repeatable")
    src.add_argument("--prompter", help="pick consecutive-turn pairs for this prompterName from the ledger")
    ap.add_argument("--pairs", type=int, default=3, help=f"pairs to pick with --prompter (max {MAX_PAIRS}, default 3)")
    ap.add_argument("--hours", type=float, default=2, help="ledger lookback for --prompter (default 2)")
    ap.add_argument("--key-env", default="OPENAI_PROJECT_API_KEY",
                    help="env var holding a key for the project that made the calls (default OPENAI_PROJECT_API_KEY)")
    ap.add_argument("--service-tier", default="default", choices=["default", "priority"],
                    help="replay tier (default `default`; flex is not offered, it disables diagnostics)")
    ap.add_argument("--gap", type=float, default=8,
                    help="seconds between replaying A and comparing B against it (default 8)")
    ap.add_argument("--max-output-tokens", type=int, default=512,
                    help="output cap per replay (default 512); too low and a reasoning model never completes A")
    ap.add_argument("--yes", action="store_true", help="spend: run the replays (otherwise print the plan only)")
    ap.add_argument("--json", dest="json_out", help="also write results here")
    args = ap.parse_args()

    key = os.environ.get(args.key_env, "").strip()
    if not key:
        sys.exit(f"{args.key_env} is not set — export a key for the project that made the calls.")

    pairs = [tuple(p) for p in args.pair] if args.pair else ledger_pairs(args.prompter, args.hours, min(args.pairs, MAX_PAIRS))
    pairs = pairs[:MAX_PAIRS]
    if not pairs:
        sys.exit("No usable pairs (consecutive Responses-API calls in one conversation, ≤10 min apart).")

    originals = {}
    for a, b in pairs:
        for rid in (a, b):
            if rid not in originals:
                originals[rid] = load_original(rid, key)
    total_tokens = sum((originals[a]["_inputTokens"] or 0) + (originals[b]["_inputTokens"] or 0) for a, b in pairs)
    models = sorted({originals[a].get("model", "?") for a, _ in pairs})

    print("## Plan")
    print(f"- Pairs: {len(pairs)} → {2 * len(pairs)} replay calls on `{', '.join(models)}`, tier `{args.service_tier}`")
    print(f"- Input tokens, upper bound: {total_tokens:,} (each replay outputs ≤{args.max_output_tokens} tokens)")
    print("- Price these against `Tools/OpenAI` → per-model rates before confirming.")
    if not args.yes:
        print("\nPlan only — re-run with --yes to spend.")
        return

    results = []
    for index, (a, b) in enumerate(pairs, 1):
        replay_a = replay(originals[a], key, args.service_tier, None, args.max_output_tokens)
        if replay_a.get("status") != "completed":
            print(f"- Pair {index}: replayed A ended `{replay_a.get('status')}` — raise --max-output-tokens; skipping")
            results.append({"pair": index, "original": [a, b], "type": "a_not_completed",
                            "reason": replay_a.get("status"), "model": replay_a.get("model")})
            continue
        # A comparison sent the instant A completes comes back `unavailable`:
        # A's diagnostic record is not written yet. Measured: immediate →
        # unavailable, 5s later → cache_hit, on the same pair.
        time.sleep(args.gap)
        replay_b = replay(originals[b], key, args.service_tier, replay_a["id"], args.max_output_tokens)
        diag = replay_b.get("prompt_cache_diagnostics") or {}
        if diag.get("type") == "unavailable":
            # One `unavailable` can be transient; a second one is the model.
            time.sleep(args.gap)
            replay_b = replay(originals[b], key, args.service_tier, replay_a["id"], args.max_output_tokens)
            diag = replay_b.get("prompt_cache_diagnostics") or {}
        details = (replay_b.get("usage") or {}).get("input_tokens_details") or {}
        row = {
            "pair": index,
            "original": [a, b],
            "replay": [replay_a["id"], replay_b["id"]],
            "model": replay_b.get("model"),
            "tier": replay_b.get("service_tier"),
            "type": diag.get("type"),
            "reason": diag.get("reason"),
            "comparisonReusableTokens": diag.get("comparison_reusable_tokens"),
            "cacheMissedTokens": diag.get("cache_missed_tokens"),
            "cachedTokens": details.get("cached_tokens"),
            "inputTokens": (replay_b.get("usage") or {}).get("input_tokens"),
        }
        results.append(row)
        print(f"- Pair {index}: {row['type']}"
              + (f" / {row['reason']}" if row["reason"] else "")
              + f" · cached {row['cachedTokens']} of {row['inputTokens']}")
        if row["type"] == "unavailable":
            print(f"\n`{row['model']}` returns no diagnostics (pre-5.6, or unsupported). Stopping before the remaining pairs.")
            break
        time.sleep(1)

    reasons = collections.Counter(r["reason"] or r["type"] for r in results)
    print("\n## By outcome")
    for reason, count in reasons.most_common():
        print(f"- `{reason}`: {count}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)


if __name__ == "__main__":
    main()
