#!/usr/bin/env python3
"""Prompt-cache health for one prompter, from the IntelligenceService LLM-call ledger.

Read-only. Pulls every call the prompter made in the window from
BigQuery `aura-main.llm.llm_call` (auth: GCP_KEY) and reports:

- overall cache hit rate (cachedTokens / inputTokens) and the share of calls
  that got zero cached tokens
- the same two numbers per hour, with the first hour whose zero-cache share
  jumps well above the window's opening baseline flagged as a step change
- splits by platform, version, serviceTier and model
- whether misses cluster by conversation (all-miss / all-hit / mixed)
- the typical cached prefix on a hit, i.e. how big the shared prefix is

No prompt text is read; the ledger holds metadata only.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import sys
import pathlib
import re
from datetime import datetime, timedelta, timezone



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


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_ledger(prompter: str, start: datetime, end: datetime) -> list[dict]:
    """Every call the prompter made in [start, end), from BigQuery `llm.llm_call`."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    try:
        return llm_ledger.rows({"from": iso(start), "to": iso(end), "prompterName": prompter})
    except llm_ledger.LedgerError as error:
        sys.exit(f"Ledger read failed: {error}")


def hit_rate(rows: list[dict]) -> float | None:
    inp = sum(r.get("inputTokens") or 0 for r in rows)
    cached = sum(r.get("cachedTokens") or 0 for r in rows)
    return 100 * cached / inp if inp else None


def zero_share(rows: list[dict]) -> float | None:
    rows = [r for r in rows if r.get("inputTokens")]
    if not rows:
        return None
    return 100 * sum(1 for r in rows if not r.get("cachedTokens")) / len(rows)


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def summarize(rows: list[dict], start: datetime, end: datetime, prompter: str, step_points: float) -> dict:
    rows = [r for r in rows if r.get("inputTokens")]
    hourly = collections.OrderedDict()
    for r in sorted(rows, key=lambda r: r["createdAt"]):
        hour = parse_ts(r["createdAt"]).strftime("%Y-%m-%d %H:00")
        hourly.setdefault(hour, []).append(r)

    hours = [
        {"hour": h, "calls": len(v), "hitRate": hit_rate(v), "zeroCacheShare": zero_share(v)}
        for h, v in hourly.items()
    ]
    # Baseline = the opening quarter of the window; a step is the first hour whose
    # zero-cache share exceeds it by `step_points`. Hours under 50 calls are too
    # noisy to call a step on.
    step = None
    if len(hours) >= 4:
        base_rows = [r for h in list(hourly)[: max(1, len(hourly) // 4)] for r in hourly[h]]
        baseline = zero_share(base_rows) or 0.0
        for h in hours:
            if h["calls"] >= 50 and (h["zeroCacheShare"] or 0) >= baseline + step_points:
                step = {"hour": h["hour"], "baselineZeroCacheShare": baseline, "zeroCacheShare": h["zeroCacheShare"]}
                break

    splits = {}
    for dim in ("platform", "version", "serviceTier", "model"):
        groups = collections.defaultdict(list)
        for r in rows:
            groups[str(r.get(dim))].append(r)
        splits[dim] = sorted(
            (
                {"value": k, "calls": len(v), "hitRate": hit_rate(v), "zeroCacheShare": zero_share(v)}
                for k, v in groups.items()
            ),
            key=lambda g: -g["calls"],
        )

    by_conversation = collections.defaultdict(list)
    for r in rows:
        if r.get("conversationId"):
            by_conversation[r["conversationId"]].append(bool(r.get("cachedTokens")))
    multi = [v for v in by_conversation.values() if len(v) >= 3]
    pattern = {
        "conversationsWith3PlusCalls": len(multi),
        "allMiss": sum(1 for v in multi if not any(v)),
        "allHit": sum(1 for v in multi if all(v)),
        "mixed": sum(1 for v in multi if any(v) and not all(v)),
    }

    hits = [r["cachedTokens"] for r in rows if r.get("cachedTokens")]
    return {
        "prompter": prompter,
        "window": {"from": iso(start), "to": iso(end)},
        "calls": len(rows),
        "users": len({r.get("userId") for r in rows if r.get("userId")}),
        "hitRate": hit_rate(rows),
        "zeroCacheShare": zero_share(rows),
        "medianInputTokens": statistics.median(r["inputTokens"] for r in rows) if rows else None,
        "medianCachedOnHit": statistics.median(hits) if hits else None,
        "stepChange": step,
        "hourly": hours,
        "splits": splits,
        "conversationPattern": pattern,
    }


def render(s: dict) -> str:
    out = [
        f"# Prompt cache — `{s['prompter']}`",
        "",
        f"Window: {s['window']['from']} → {s['window']['to']} · {s['calls']:,} calls · {s['users']:,} users",
        "",
        f"- **Cache hit rate:** {fmt(s['hitRate'])} of input tokens",
        f"- **Zero-cache calls:** {fmt(s['zeroCacheShare'])}",
        f"- **Median input / cached-on-hit:** {s['medianInputTokens'] or '—'} / {s['medianCachedOnHit'] or '—'} tokens",
    ]
    step = s["stepChange"]
    if step:
        out.append(
            f"- **Step change at {step['hour']} UTC:** zero-cache {fmt(step['baselineZeroCacheShare'])} → {fmt(step['zeroCacheShare'])}"
        )
    p = s["conversationPattern"]
    if p["conversationsWith3PlusCalls"]:
        out.append(
            f"- **Misses by conversation** (3+ calls): {p['mixed']} mixed · {p['allMiss']} all-miss · {p['allHit']} all-hit"
        )
    out += ["", "## Hourly", "", "| Hour (UTC) | Calls | Hit rate | Zero-cache |", "|---|---:|---:|---:|"]
    for h in s["hourly"]:
        out.append(f"| {h['hour']} | {h['calls']:,} | {fmt(h['hitRate'])} | {fmt(h['zeroCacheShare'])} |")
    for dim, groups in s["splits"].items():
        out += ["", f"## By {dim}", "", f"| {dim} | Calls | Hit rate | Zero-cache |", "|---|---:|---:|---:|"]
        for g in groups[:10]:
            out.append(f"| `{g['value']}` | {g['calls']:,} | {fmt(g['hitRate'])} | {fmt(g['zeroCacheShare'])} |")
    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompter", required=True, help="prompterName, e.g. coach-prompter")
    ap.add_argument("--hours", type=float, default=24, help="window ending now (default 24)")
    ap.add_argument("--since", help="UTC start, ISO-8601 (overrides --hours)")
    ap.add_argument("--until", help="UTC end, ISO-8601 (default now)")
    ap.add_argument("--step-points", type=float, default=20,
                    help="zero-cache rise in points over the opening baseline that counts as a step (default 20)")
    ap.add_argument("--json", dest="json_out", help="also write the structured result here")
    args = ap.parse_args()

    end = parse_ts(args.until) if args.until else datetime.now(timezone.utc)
    start = parse_ts(args.since) if args.since else end - timedelta(hours=args.hours)

    rows = fetch_ledger(args.prompter, start, end)
    if not rows:
        sys.exit(f"No ledger rows for prompterName={args.prompter} in that window.")
    summary = summarize(rows, start, end, args.prompter, args.step_points)
    print(render(summary))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)


if __name__ == "__main__":
    main()
