#!/usr/bin/env python3
"""Evidence and bookkeeping for the livekit-loop RECOMMEND leg. Read-only, except `mark`, `raised`,
`resolve` and `decline`.

  tick [--json]       every run tick: score the last 24h's candidate signals against the bar and
                      the item store; prints RAISE (over the bar, not yet raised), WATCHING (below
                      the bar) and HELD (over the bar but pending, picked, on hold or declined)
  raised --key --n    record that an item was shown to the owner (status pending, at n)
  resolve --key --status picked|declined|on_hold [--n]
                      record the owner's answer; a decline also needs `decline` for the log.
                      --n is required for a key that was never `raised` (it sets the hold level)
  due                 exit 0 when the daily digest should run now (first tick after 09:00 PT,
                      once per PT day), exit 1 otherwise
  mark                record that today's (PT) digest ran
  evidence [--days N] one block per full UTC day (never pooled) for the last N days (default 2):
                      sessions, `Ending session` reasons, `Interrupting turn shape` (barge-in ms,
                      premature share), `Spoken fallback line` by line_type; then the voice
                      parent card's children (open, plus closed in the last 14 days) with their
                      last comment, and the Recommendations log entries still inside 14 days
  decline --item --reason
                      add a Recommendations log comment on the parent card

Latency and ERROR groups are not read here: the skill composes eng:analyze-livekit-latency and
eng:monitor-livekit for those. Structured fields only; no spoken or transcript text is read.
"""

import argparse
import collections
import datetime as dt
import json
import os
import pathlib
import statistics
import sys
import urllib.request
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gcloud_sa  # noqa: E402

PROJECT = "firebase-auratech16"
LOG = f"projects/{PROJECT}/logs/aurie-voice-agent"
PARENT = "PROD-6647"
LOG_PREFIX = "Recommendations log"
QUIET_DAYS = 14
PT = zoneinfo.ZoneInfo("America/Los_Angeles")
DUE_HOUR = 9
STATE = pathlib.Path(os.path.expanduser("~/.cache/aura-skills/livekit-loop/last_recommend"))
ITEMS = STATE.parent / "items.json"
LIMIT = 20000

# The bar. A candidate is raised when, inside the last 24h, it reaches its count AND it is above
# every one of the previous 7 full UTC days. A burst is one session hearing the same scripted
# line at least BURST_PER_SESSION times (e.g. the "didn't catch that" re-prompt).
BURST_PER_SESSION = 5
BURST_SESSIONS = 3
VOLUME_SESSIONS = 20
BASELINE_DAYS = 7
# A held item comes back when its n has grown by half since it was last raised.
MATERIAL = 1.5
HEALTHY_ENDINGS = {"client left"}
# A signal a config change switched on (or reshaped) is compared only with full UTC days after the
# change; until there is one, it can't be raised. Keyed by the signal's subject (the part after the
# colon). Add an entry whenever a setting that produces a signal changes.
BASELINE_SINCE = {
    "away_check_in": "2026-10-26T21:07:00",  # PROD-5461 turned the away check-in on (90s / 30s)
    "user away": "2026-10-27T22:26:00",  # PROD-7913 AGENT_AWAY_GRACE=600: away endings drop
    "client left": "2026-10-27T22:26:00",  # PROD-7913: endings move from away to client left
    "burst:away_check_in": "2026-10-29T20:20:51",  # voice agent v1.16.6: away check-in back-off
    "fallback:stt_stall_reprompt": "2026-11-02T17:12:02",  # ENG-1276: agent AGC off
}


# MARK: - Schedule

def is_due(now_pt: dt.datetime, last: str | None) -> bool:
    """True from 09:00 PT until the step has run once that PT day."""
    return now_pt.hour >= DUE_HOUR and last != now_pt.date().isoformat()


def _last_run() -> str | None:
    try:
        return STATE.read_text().strip() or None
    except OSError:
        return None


# MARK: - Logs

def read(flt: str) -> list[dict]:
    """gcloud logging read as the service account; a result at the cap is refused, never returned."""
    proc = gcloud_sa.run(["logging", "read", flt, f"--project={PROJECT}", f"--limit={LIMIT}", "--format=json"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"gcloud logging read failed as {gcloud_sa.credential()}: {proc.stderr.strip()[:300]}")
    rows = json.loads(proc.stdout or "[]")
    if len(rows) >= LIMIT:
        raise RuntimeError(f"a read returned {len(rows)} rows, the cap; counts would be truncated")
    return rows


def _rows(day: dt.date, message: str) -> list[dict]:
    start = dt.datetime.combine(day, dt.time())
    out = []
    for h in range(0, 24, 6):  # quarter-day reads keep each page well under the cap
        a, b = start + dt.timedelta(hours=h), start + dt.timedelta(hours=h + 6)
        out += read(f'logName="{LOG}" AND timestamp>="{a.isoformat()}Z" AND timestamp<"{b.isoformat()}Z" '
                    f'AND jsonPayload.message="{message}"')
    return [r.get("jsonPayload", {}) for r in out]


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1] if len(values) > 1 else values[0]


def summarize_day(day: dt.date, started: list[dict], ended: list[dict], interrupts: list[dict],
                  fallbacks: list[dict]) -> dict:
    """One day's counts. Every rate carries its own n."""
    barge = [p["barge_in_ms"] for p in interrupts if isinstance(p.get("barge_in_ms"), (int, float))]
    premature = sum(1 for p in interrupts if p.get("premature") is True)
    return {
        "date": day.isoformat(),
        "sessions": len(started),
        "releases": dict(collections.Counter((p.get("serviceContext") or {}).get("version", "?") for p in started)),
        "ending_reasons": dict(collections.Counter(p.get("reason") or "?" for p in ended).most_common()),
        "interrupts": {"n": len(interrupts), "premature": premature,
                       "premature_share": round(premature / len(interrupts), 3) if interrupts else None,
                       "barge_in_ms_p50": _pct(barge, 50), "barge_in_ms_p90": _pct(barge, 90)},
        "fallback_lines": dict(collections.Counter(p.get("line_type") or "?" for p in fallbacks).most_common()),
    }


# MARK: - Bar

def signals(ended: list[dict], fallbacks: list[dict]) -> dict:
    """Affected-session counts per candidate signal. Keys are stable, so the item store can match them."""
    out = {}
    by_reason = collections.defaultdict(set)
    for p in ended:
        by_reason[p.get("reason") or "?"].add(p.get("transaction"))
    for reason, tx in by_reason.items():
        if reason not in HEALTHY_ENDINGS:
            out[f"ending:{reason}"] = {"kind": "volume", "n": len(tx), "label": f"sessions ending '{reason}'"}
    per = collections.defaultdict(collections.Counter)
    for p in fallbacks:
        per[p.get("line_type") or "?"][p.get("transaction")] += 1
    for line, counts in per.items():
        out[f"fallback:{line}"] = {"kind": "volume", "n": len(counts), "label": f"sessions hearing '{line}'"}
        bursts = sum(1 for c in counts.values() if c >= BURST_PER_SESSION)
        if bursts:
            out[f"burst:{line}"] = {"kind": "burst", "n": bursts,
                                    "label": f"sessions hearing '{line}' {BURST_PER_SESSION}+ times"}
    return out


def _held(item: dict | None, n: int, today: dt.date) -> bool:
    """Pending, picked, on hold, or declined inside the quiet window, unless n grew materially."""
    if not item or n >= MATERIAL * item.get("n", 0):
        return False
    if item["status"] == "declined":
        return dt.date.fromisoformat(item["at"]) > today - dt.timedelta(days=QUIET_DAYS)
    return True


def evaluate(now: dict, baseline: list[dict], items: dict, today: dt.date) -> dict:
    """Split today's candidates into raise / watching / held. Never raises without a baseline.

    `baseline` is one {"date", "signals"} per previous full UTC day.
    """
    out = {"raise": [], "watching": [], "held": []}
    for key, sig in sorted(now.items(), key=lambda kv: -kv[1]["n"]):
        # A full "kind:line" key resets that signal only; a bare line resets every kind of it.
        since = (BASELINE_SINCE.get(key) or BASELINE_SINCE.get(key.split(":", 1)[-1], ""))[:10]
        hist = [d["signals"].get(key, {}).get("n", 0) for d in baseline if d["date"] > since]
        bar = BURST_SESSIONS if sig["kind"] == "burst" else VOLUME_SESSIONS
        over = bool(hist) and sig["n"] >= bar and sig["n"] > max(hist)
        row = {"key": key, **sig, "bar": bar, "trailing_max": max(hist) if hist else None,
               "trailing_days": len(hist)}
        if not over:
            out["watching"].append(row)
        elif _held(items.get(key), sig["n"], today):
            out["held"].append({**row, "status": items[key]["status"]})
        else:
            out["raise"].append(row)
    return out


def _load_items() -> dict:
    try:
        return json.loads(ITEMS.read_text())
    except (OSError, ValueError):
        return {}


def _save_items(items: dict) -> None:
    ITEMS.parent.mkdir(parents=True, exist_ok=True)
    ITEMS.write_text(json.dumps(items, indent=2, sort_keys=True))


def _window(a: dt.datetime, b: dt.datetime, message: str) -> list[dict]:
    rows = read(f'logName="{LOG}" AND timestamp>="{a.isoformat()}Z" AND timestamp<"{b.isoformat()}Z" '
                f'AND jsonPayload.message="{message}"')
    return [r.get("jsonPayload", {}) for r in rows]


def _baseline(today: dt.date) -> list[dict]:
    """Signals for each of the previous 7 full UTC days, cached per day (one build a day)."""
    path = STATE.parent / f"baseline-v2-{today.isoformat()}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    days = []
    for back in range(BASELINE_DAYS, 0, -1):
        a = dt.datetime.combine(today - dt.timedelta(days=back), dt.time())
        b = a + dt.timedelta(days=1)
        days.append({"date": a.date().isoformat(),
                     "signals": signals(_window(a, b, "Ending session"), _window(a, b, "Spoken fallback line"))})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(days))
    return days


# MARK: - Linear

def _linear(query: str, variables: dict | None = None) -> dict:
    key = os.environ.get("LINEAR_API_KEY")
    if not key:
        raise RuntimeError("LINEAR_API_KEY unset")
    req = urllib.request.Request("https://api.linear.app/graphql",
                                 data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"Authorization": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read().decode("utf-8"))
    if body.get("errors"):
        raise RuntimeError(f"Linear: {body['errors'][0].get('message')}")
    return body["data"]


def is_on_hold(title: str) -> bool:
    t = title.lower()
    return "[on hold]" in t or "[livekit support]" in t


CHILDREN = """query($id:String!,$after:String){issue(id:$id){children(first:100,after:$after){
  pageInfo{hasNextPage endCursor} nodes{identifier title completedAt canceledAt state{name type}
  comments(last:1){nodes{body createdAt}}}}}}"""
COMMENTS = """query($id:String!,$after:String){issue(id:$id){comments(first:100,after:$after){
  pageInfo{hasNextPage endCursor} nodes{body createdAt}}}}"""


def _all(query: str, field: str) -> list[dict]:
    """Every node of the parent's `field` connection, following pageInfo to the end."""
    out, after = [], None
    while True:
        conn = _linear(query, {"id": PARENT, "after": after})["issue"][field]
        out += conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return out
        after = conn["pageInfo"]["endCursor"]


def cards(today: dt.date) -> dict:
    """Open children, children closed inside the quiet window, and live log entries."""
    cutoff = (today - dt.timedelta(days=QUIET_DAYS)).isoformat()
    kids = []
    for n in _all(CHILDREN, "children"):
        closed_at = (n.get("completedAt") or n.get("canceledAt") or "")[:10]
        if n["state"]["type"] in ("completed", "canceled") and closed_at < cutoff:
            continue
        last = (n["comments"]["nodes"] or [{}])[-1]
        kids.append({"id": n["identifier"], "title": n["title"], "state": n["state"]["name"],
                     "on_hold": is_on_hold(n["title"]),
                     "last_comment": (last.get("body") or "")[:200], "last_comment_at": (last.get("createdAt") or "")[:10]})
    log = [{"at": c["createdAt"][:10], "entry": c["body"][:300]} for c in _all(COMMENTS, "comments")
           if c["body"].startswith(LOG_PREFIX) and c["createdAt"][:10] >= cutoff]
    return {"parent": PARENT, "children": kids, "recommendations_log": log}


def decline_body(item: str, reason: str, today: dt.date) -> str:
    return f"{LOG_PREFIX} — declined {today.isoformat()}: {item}\nReason: {reason}\nNot re-proposed before {(today + dt.timedelta(days=QUIET_DAYS)).isoformat()} unless the evidence changes materially."


# MARK: - Commands

def cmd_tick(args: argparse.Namespace) -> int:
    end = dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0)
    start = end - dt.timedelta(hours=24)
    now = signals(_window(start, end, "Ending session"), _window(start, end, "Spoken fallback line"))
    out = evaluate(now, _baseline(end.date()), _load_items(), end.date())
    out["window"] = f"{start.isoformat()}Z to {end.isoformat()}Z"
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    print(f"# window {out['window']}  bar: burst >= {BURST_SESSIONS} sessions, volume >= {VOLUME_SESSIONS}, "
          f"and above the previous {BASELINE_DAYS} days")
    for name in ("raise", "held", "watching"):
        print(f"\n## {name.upper()} ({len(out[name])})")
        for r in out[name]:
            st = f"  [{r['status']}]" if "status" in r else ""
            print(f"   {r['key']:40s} n={r['n']:<4} bar={r['bar']:<3} 7d max={r['trailing_max']}{st}  {r['label']}")
    return 0


def cmd_evidence(args: argparse.Namespace) -> int:
    today = dt.datetime.now(dt.UTC).date()
    days = []
    for back in range(args.days, 0, -1):
        d = today - dt.timedelta(days=back)
        days.append(summarize_day(d, _rows(d, "Session started"), _rows(d, "Ending session"),
                                  _rows(d, "Interrupting turn shape"), _rows(d, "Spoken fallback line")))
    try:
        linear = cards(today)
    except Exception as exc:  # the skill falls back to the Linear MCP; say why
        linear = {"unavailable": str(exc)[:200]}
    out = {"days": days, "linear": linear}
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    for d in days:
        i = d["interrupts"]
        print(f"## {d['date']} UTC  sessions={d['sessions']}  releases={d['releases']}")
        print(f"   ending reasons: {d['ending_reasons']}")
        print(f"   interrupts: n={i['n']} premature={i['premature']} ({i['premature_share']})  "
              f"barge-in p50={i['barge_in_ms_p50']} p90={i['barge_in_ms_p90']} ms")
        print(f"   fallback lines: {d['fallback_lines'] or '-'}")
    if "unavailable" in linear:
        print(f"\n## {PARENT}: unavailable ({linear['unavailable']}); read its children with the Linear MCP")
        return 0
    print(f"\n## {PARENT} children (open, or closed in the last {QUIET_DAYS} days)")
    for c in linear["children"]:
        hold = "  [on hold]" if c["on_hold"] else ""
        print(f"   {c['id']}  {c['state']:16s} {c['title']}{hold}")
        if c["last_comment"]:
            print(f"      last comment {c['last_comment_at']}: {c['last_comment'].splitlines()[0]}")
    print(f"\n## Recommendations log (last {QUIET_DAYS} days)")
    for e in linear["recommendations_log"] or [{"at": "-", "entry": "none"}]:
        print(f"   {e['at']}  {e['entry'].splitlines()[0]}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tick")
    t.add_argument("--json", action="store_true")
    r = sub.add_parser("raised")
    r.add_argument("--key", required=True)
    r.add_argument("--n", type=int, required=True)
    v = sub.add_parser("resolve")
    v.add_argument("--key", required=True)
    v.add_argument("--status", required=True, choices=["picked", "declined", "on_hold"])
    v.add_argument("--n", type=int, help="current n; required when the key was never raised")
    sub.add_parser("due")
    sub.add_parser("mark")
    e = sub.add_parser("evidence")
    e.add_argument("--days", type=int, default=2)
    e.add_argument("--json", action="store_true")
    d = sub.add_parser("decline")
    d.add_argument("--item", required=True)
    d.add_argument("--reason", required=True)
    args = ap.parse_args()
    now_pt = dt.datetime.now(PT)
    today = dt.datetime.now(dt.UTC).date()  # the store and the log use the same UTC date `tick` compares
    try:
        if args.cmd == "due":
            due = is_due(now_pt, _last_run())
            print("due" if due else f"not due (last run {_last_run() or 'never'})")
            return 0 if due else 1
        if args.cmd == "mark":
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(now_pt.date().isoformat())
            print(f"marked {now_pt.date()}")
            return 0
        if args.cmd in ("raised", "resolve"):
            items = _load_items()
            prev = items.get(args.key)
            n = args.n if args.n is not None else (prev or {}).get("n")
            if not n:
                # Stored at n=0 the hold would never apply (any n >= 1.5 x 0), so the next tick re-raises it.
                raise RuntimeError(f"{args.key} was never raised: pass --n <current n> so the hold has a level")
            items[args.key] = {"status": "pending" if args.cmd == "raised" else args.status,
                               "n": n, "at": today.isoformat()}
            _save_items(items)
            print(f"{args.key}: {items[args.key]['status']}")
            return 0
        if args.cmd == "tick":
            return cmd_tick(args)
        if args.cmd == "decline":
            issue = _linear('query($id:String!){issue(id:$id){id}}', {"id": PARENT})["issue"]
            _linear('mutation($i:String!,$b:String!){commentCreate(input:{issueId:$i,body:$b}){success}}',
                    {"i": issue["id"], "b": decline_body(args.item, args.reason, today)})
            print(f"logged on {PARENT}")
            return 0
        return cmd_evidence(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
