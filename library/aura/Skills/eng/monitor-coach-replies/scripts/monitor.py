#!/usr/bin/env python3
"""Daily anomaly check on Aurie coach replies: yesterday against the median of the 7 days before.

  monitor.py collect [--date D] [--refresh]          fill the local history for D and the 7 days before it
  monitor.py check [--date D] [--post] [--weekly]    flag anomalies on D; post only when something is off,
                                                     or a weekly all-clear with --weekly

Metrics per day, counts only (never a reply's text or a uid):
  length       coach-prompter output tokens, median and p90, chat and voice apart (BigQuery llm.llm_call)
  emoji        share of coach replies with an emoji (BigQuery llm.llm_call emojiCount, one row per turn;
               IntelligenceService Postgres only as a fallback, when INTELLIGENCE_DB_URL is set)
  incomplete   max_output_tokens responses on the first attempt (Cloud Logging)
  double       max_output_tokens responses on a second or later attempt: the user saw a failure
  empty        completed responses that carried no text (Cloud Logging)
Thresholds: references/thresholds.json. History: ~/.claude/monitor-coach-replies/history.json (mode 600).
GCP calls run as GCP_KEY with the AuraClaude user agent.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import statistics
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SKILL_ID = "eng:monitor-coach-replies"
LOG_PROJECT = "firebase-auratech16"
BQ_PROJECT = "aura-main"
IS_REPO = "AuraHealth/IntelligenceService"
DEFAULT_CHANNEL = "C07EX6CL43A"
MODES = ("chat", "voice")
HISTORY = pathlib.Path(os.environ.get("MONITOR_COACH_REPLIES_HISTORY")
                       or "~/.claude/monitor-coach-replies/history.json").expanduser()
LOG_BASE = 'resource.type="k8s_container" resource.labels.container_name="intelligence-service"'
# IntelligenceService's log lines (since about 2026-10-28; a 0 before that means not logged, not none).
LOG_FILTERS = {
    # every incomplete first attempt: retried with no output, or kept with partial output
    "incomplete": 'jsonPayload.reason="max_output_tokens" jsonPayload.attempt=1',
    # an incomplete on the last attempt: the user saw a failure (voice may reach attempt 3, ENGBACK-2366)
    "double": 'jsonPayload.reason="max_output_tokens" jsonPayload.attempt>=2',
    # a completed response that carried no text, retried or failed
    "empty": '(jsonPayload.message="Retrying an empty completed response" OR '
             'jsonPayload.message="OpenAI response completed empty")',
}
LENGTH_SQL = """SELECT FORMAT_DATE('%Y-%m-%d', DATE(createdAt)) d, IF(platform='livekit','voice','chat') mode,
  COUNT(*) n, APPROX_QUANTILES(outputTokens,100)[OFFSET(50)] out_med,
  APPROX_QUANTILES(outputTokens,100)[OFFSET(90)] out_p90
FROM `aura-main.llm.llm_call`
WHERE createdAt >= TIMESTAMP('{start}') AND createdAt < TIMESTAMP('{end}') AND prompterName='coach-prompter'
GROUP BY 1,2"""
# One row per turn: the last call of each dialogue that stored a reply (a retry writes a second row).
EMOJI_BQ_SQL = """SELECT d, COUNT(*) n, ROUND(100 * COUNTIF(e > 0) / COUNT(*), 1) pct FROM (
  SELECT FORMAT_DATE('%Y-%m-%d', DATE(createdAt)) d, emojiCount e
  FROM `aura-main.llm.llm_call`
  WHERE createdAt >= TIMESTAMP('{start}') AND createdAt < TIMESTAMP('{end}')
    AND prompterName = 'coach-prompter' AND replyChars IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY dialogueId ORDER BY createdAt DESC) = 1)
GROUP BY d"""
EMOJI_SQL = """SELECT count(*) n,
  round(100.0*count(*) FILTER (WHERE message ~ '[\\U0001F300-\\U0001FAFF☀-➿]')/nullif(count(*),0),1)
FROM dialogue WHERE "createdAt" >= '{start}' AND "createdAt" < '{end}'
  AND role='assistant' AND prompt->>'name'='coach-prompter'"""


def thresholds() -> dict:
    return json.loads((HERE.parent / "references" / "thresholds.json").read_text(encoding="utf-8"))


def day(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def days_back(d: dt.date, n: int) -> list:
    return [(d - dt.timedelta(days=i)).isoformat() for i in range(n, 0, -1)]


# --- history ------------------------------------------------------------------------------------------
def load_history() -> dict:
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save_history(h: dict) -> None:
    HISTORY.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(HISTORY, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(h, f, indent=1, sort_keys=True)


# --- detection (pure) -----------------------------------------------------------------------------------
def detect(history: dict, d: str, th: dict) -> dict:
    """Flags for day d against the median of up to `baseline_days` days before it. A metric with no value on
    d, or fewer than 4 baseline days, is listed as unmeasured, never as normal."""
    base_days = [x for x in days_back(day(d), th["baseline_days"]) if x in history]
    today = history.get(d) or {}
    flags, unmeasured = [], []

    def base(get):
        vals = [v for v in (get(history[x]) for x in base_days) if v is not None]
        return statistics.median(vals) if len(vals) >= 4 else None

    for mode in MODES:
        cur = (today.get(mode) or {})
        if (cur.get("n") or 0) < th["min_replies"]:
            unmeasured.append(f"length {mode} (fewer than {th['min_replies']} replies)")
            continue
        for stat, label in (("out_med", "median"), ("out_p90", "p90")):
            b = base(lambda r, m=mode, s=stat: (r.get(m) or {}).get(s))
            if b is None:
                unmeasured.append(f"length {mode} {label} (no baseline)")
                continue
            pct = 100.0 * (cur[stat] - b) / b
            if abs(pct) >= th["length_pct"]:
                flags.append({"metric": f"output tokens {label}", "mode": mode, "value": cur[stat],
                              "baseline": round(b, 1), "change": f"{pct:+.0f}%", "dir": "+" if pct > 0 else "-"})
    emoji = today.get("emoji_pct")
    b = base(lambda r: r.get("emoji_pct"))
    if emoji is None or b is None:
        unmeasured.append("emoji share")
    elif abs(emoji - b) >= th["emoji_points"]:
        flags.append({"metric": "replies with emoji", "mode": "all", "value": f"{emoji:.1f}%",
                      "baseline": f"{b:.1f}%", "change": f"{emoji - b:+.1f} pts",
                      "dir": "+" if emoji > b else "-"})
    inc = today.get("incomplete")
    b = base(lambda r: r.get("incomplete"))
    if inc is None or b is None:
        unmeasured.append("incomplete responses")
    elif inc >= th["incomplete_min"] and inc >= th["incomplete_ratio"] * max(b, 1):
        flags.append({"metric": "incomplete responses (retried)", "mode": "all", "value": inc,
                      "baseline": b, "change": f"{inc / max(b, 1):.0f}x", "dir": "+"})
    emp = today.get("empty")
    b = base(lambda r: r.get("empty"))
    if emp is None or b is None:
        unmeasured.append("empty replies")
    elif emp >= th["empty_min"] and emp >= th["empty_ratio"] * max(b, 1):
        flags.append({"metric": "empty completed replies", "mode": "all", "value": emp,
                      "baseline": b, "change": f"{emp / max(b, 1):.0f}x", "dir": "+"})
    dbl = today.get("double")
    if dbl is None:
        unmeasured.append("double failures")
    elif dbl >= th["double_failure_min"]:
        flags.append({"metric": "double failures (user saw a failure)", "mode": "all", "value": dbl,
                      "baseline": "-", "change": "any", "dir": "+"})
    return {"date": d, "baselineDays": len(base_days), "flags": flags, "unmeasured": unmeasured}


def key(f: dict) -> list:
    return [f["metric"], f["mode"], f["dir"]]


def detect_new(history: dict, d: str, th: dict, posted: bool = False) -> dict:
    """detect(), with each flag marked new or continuing. A step keeps flagging until the 7-day median
    catches up (about 4 days), so a flag the day before carried, same metric, mode and direction, is
    continuing: listed, but no reason to post again. With posted=True the day before counts only for what
    was actually posted then, so a post that failed is retried. A double failure is always new: each one
    is a user who saw a failed reply."""
    res = detect(history, d, th)
    prev = (day(d) - dt.timedelta(days=1)).isoformat()
    if posted:
        before = {tuple(k) for k in (history.get(prev) or {}).get("posted", [])}
    else:
        before = {tuple(key(f)) for f in detect(history, prev, th)["flags"]} if prev in history else set()
    for f in res["flags"]:
        f["new"] = f["metric"].startswith("double failures") or tuple(key(f)) not in before
    return res


# --- collection ----------------------------------------------------------------------------------------
def collect_length(start: str, end: str) -> dict:
    import bq
    out = {}
    for r in bq.client(BQ_PROJECT).query(LENGTH_SQL.format(start=start, end=end)).result():
        out.setdefault(r["d"], {})[r["mode"]] = {"n": r["n"], "out_med": r["out_med"], "out_p90": r["out_p90"]}
    return out


def collect_log_count(d: str, key: str) -> int | None:
    import gcloud_sa
    nxt = (day(d) + dt.timedelta(days=1)).isoformat()
    flt = f'{LOG_BASE} timestamp>="{d}T00:00:00Z" timestamp<"{nxt}T00:00:00Z" {LOG_FILTERS[key]}'
    out = gcloud_sa.run(["logging", "read", flt, f"--project={LOG_PROJECT}", "--limit=50000",
                         "--format=value(timestamp)"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    if out.returncode:
        print(f"WARN: log read {key} {d} failed as {gcloud_sa.credential()}", file=sys.stderr)
        return None
    return len([l for l in out.stdout.splitlines() if l.strip()])


def collect_emoji_bq(start: str, end: str) -> dict:
    """{day: emoji share %} from llm_call's emojiCount (stored since 2026-09-25), counts only."""
    import bq
    return {r["d"]: r["pct"] for r in bq.client(BQ_PROJECT).query(EMOJI_BQ_SQL.format(start=start, end=end)).result()
            if r["n"]}


def collect_emoji(d: str) -> float | None:
    """The fallback for a day BigQuery has no emojiCount for: IntelligenceService Postgres, read-only, one
    day per query, never the text. Without INTELLIGENCE_DB_URL: None (not measured)."""
    url = os.environ.get("INTELLIGENCE_DB_URL")
    if not url:
        return None
    nxt = (day(d) + dt.timedelta(days=1)).isoformat()
    env = dict(os.environ, PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=280000")
    out = subprocess.run(["psql", url, "-At", "-F", "\t", "-c", EMOJI_SQL.format(start=d, end=nxt)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if out.returncode or not out.stdout.strip():
        print(f"WARN: emoji query {d} failed", file=sys.stderr)
        return None
    pct = out.stdout.strip().split("\t")[-1]
    return float(pct) if pct else None


def incomplete_row(row) -> bool:
    """A day to (re)read: missing, or a mode, log count or emoji share that a failed read left empty."""
    if not row:
        return True
    if any(row.get(m) is None for m in MODES) or any(row.get(k) is None for k in LOG_FILTERS):
        return True
    return row.get("emoji_pct") is None


def cmd_collect(a) -> int:
    th = thresholds()
    d = day(a.date)
    want = days_back(d, th["baseline_days"]) + [a.date]
    h = load_history()
    todo = [x for x in want if a.refresh or incomplete_row(h.get(x))]
    if todo:
        end = (day(max(todo)) + dt.timedelta(days=1)).isoformat()
        length = collect_length(min(todo), end)
        emoji = collect_emoji_bq(min(todo), end)
        for x in todo:
            row = dict(h.get(x) or {})
            row.update(length.get(x, {}))
            for k in LOG_FILTERS:
                if row.get(k) is None or a.refresh:
                    row[k] = collect_log_count(x, k)
            if row.get("emoji_pct") is None or a.refresh:
                row["emoji_pct"] = emoji.get(x)
                if row["emoji_pct"] is None:
                    row["emoji_pct"] = collect_emoji(x)
            h[x] = row
        save_history(h)
    print(json.dumps({"collected": todo, "history_days": len(h)}))
    return 0


# --- context and report --------------------------------------------------------------------------------
def changes(start: str, end: str, flag_day: str) -> list:
    """Our own changes: IntelligenceService releases published on the flagged day or the day before (the
    publish time is the deploy start; Spinnaker finishes 5-10 min later), and coach prompt commits in the
    whole window. A step with none of these is likely chat-latest drift."""
    out = []
    since = (day(flag_day) - dt.timedelta(days=1)).isoformat()
    r = subprocess.run(["gh", "release", "list", "-R", IS_REPO, "--limit", "100", "--json", "tagName,publishedAt"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        out.append("(release list unavailable: gh failed)")
    else:
        for rel in json.loads(r.stdout or "[]"):
            t = rel.get("publishedAt") or ""
            if since <= t[:10] <= flag_day:
                out.append(f"{t[:16]}Z release {rel['tagName']}")
    r = subprocess.run(["gh", "api", f"repos/{IS_REPO}/commits?path=src/prompters&since={start}T00:00:00Z"
                        f"&until={end}T00:00:00Z&per_page=100",
                        "--jq", ".[]|[.commit.committer.date, .sha[0:8], (.commit.message|split(\"\\n\")[0])]|@tsv"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        out.append("(prompt history unavailable: gh failed)")
    else:
        for line in r.stdout.splitlines():
            t, sha, head = (line.split("\t") + ["", "", ""])[:3]
            if "coach" in head.lower():
                out.append(f"{t[:16]}Z {sha} {head[:90]}")
    return sorted(out)


def render(res: dict, context: list, weekly_clear: bool = False) -> str:
    d = res["date"]
    if weekly_clear:
        lines = [f"*Coach replies: all clear for the 7 days to {d}* (no anomaly against the 7-day median)."]
    else:
        lines = [f"*Coach replies, {d}: {len(res['flags'])} anomaly(ies)* against the median of "
                 f"{res['baselineDays']} prior days"]
        for f in res["flags"]:
            tag = "" if f.get("new", True) else " (continuing)"
            lines.append(f"• {f['metric']} ({f['mode']}): {f['value']} vs {f['baseline']} ({f['change']}){tag}")
    if context:
        lines.append("Our changes in the window:")
        lines += [f"• {c}" for c in context]
    else:
        lines.append("No IntelligenceService release or coach prompt change in the window: likely model drift "
                     "(the coach runs on chat-latest).")
    if res["unmeasured"]:
        lines.append("Not measured: " + "; ".join(res["unmeasured"]) + ".")
    return "\n".join(lines)


def cmd_check(a) -> int:
    th = thresholds()
    h = load_history()
    res = detect_new(h, a.date, th, posted=a.post)
    start = (day(a.date) - dt.timedelta(days=th["baseline_days"])).isoformat()
    end = (day(a.date) + dt.timedelta(days=1)).isoformat()
    week = days_back(day(a.date), 6) + [a.date]
    # An all-clear says every metric was measured and normal on all 7 days.
    checked = [detect(h, x, th) for x in week if x in h]
    weekly_clear = (a.weekly and len(checked) == len(week) and not any(r["flags"] for r in checked)
                    and not any(r["unmeasured"] for r in checked))
    post = any(f["new"] for f in res["flags"]) or weekly_clear
    text = render(res, changes(start, end, a.date) if post else [], weekly_clear)
    print(json.dumps({"date": a.date, "flags": len(res["flags"]), "new": sum(f["new"] for f in res["flags"]),
                      "unmeasured": len(res["unmeasured"]),
                      "post": post}))
    print(text)
    if a.post and post:
        import slack_post
        try:
            resp = slack_post.post_message(a.channel, text, footer_skill=SKILL_ID)
            ok = bool(resp.get("ok")) if isinstance(resp, dict) else bool(resp)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - slack_post exits on a missing token or transport error
            print(f"Slack post failed: {type(e).__name__}", file=sys.stderr)
            ok = False
        if not ok:
            print("Slack post FAILED: nothing recorded as posted, so tomorrow's run posts these flags again",
                  file=sys.stderr)
            return 1
        if a.date in h:
            h[a.date]["posted"] = [key(f) for f in res["flags"]]
            save_history(h)
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    yesterday = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--date", default=yesterday)
    c.add_argument("--refresh", action="store_true", help="re-read days already in the history")
    k = sub.add_parser("check")
    k.add_argument("--date", default=yesterday)
    k.add_argument("--post", action="store_true", help="post to Slack when something is anomalous")
    k.add_argument("--weekly", action="store_true", help="also post an all-clear when the week had none")
    k.add_argument("--channel", default=os.environ.get("MONITOR_COACH_REPLIES_CHANNEL", DEFAULT_CHANNEL))
    a = ap.parse_args(argv)
    day(a.date)
    return cmd_collect(a) if a.cmd == "collect" else cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
