#!/usr/bin/env python3
"""Monitor the Aurie LiveKit voice agent (`aurie-voice-agent`) from its forwarded Cloud Logging rows.

Two subcommands:

  window [--minutes N]   the last N minutes (default 30) against the same window 24h earlier:
                         sessions by release, ERROR+/session, ERROR+ groups new since -24h,
                         WARNING+ groups, the agent's log-screen stamps, its error codes and its
                         recovery rows, then a NORMAL / LOOK verdict naming every reason.
  stalls [--hours H]     what happened after each STT stall reconnect in the last H hours
                         (default 12): recovered, session ended (and why), pending, unknown.

The signal catalog below mirrors rows the agent emits (LivekitVoiceAgent `src/errors.py` codes,
`src/log/*_logging.py` stamps and the sessions/ recovery messages). A row the agent stops emitting
reads as zero here, not as an error, so when a release renames one, update the catalog with it.

Reads Cloud Logging through gcloud_sa.py (the GCP_KEY service account), and the latest agent release
through `gh`. Read-only.
"""

import argparse
import collections
import datetime as dt
import json
import pathlib
import re
import statistics
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gcloud_sa  # noqa: E402

PROJECT = "firebase-auratech16"
LOG = f"projects/{PROJECT}/logs/aurie-voice-agent"
AGENT_REPO = "AuraHealth/LivekitVoiceAgent"
# LiveKit Cloud drains old sessions for up to ~1h after a deploy; past this an old release is stuck.
ROLLOUT_MINUTES = 90

# jsonPayload boolean stamps set by the agent's log screens (vendor rows it relabels or demotes).
STAMPS = [
    "engine_reconnect_teardown",
    "eot_degraded_during_teardown",
    "interruption_timeout_teardown",
    "llm_inference_traceback",
    "telemetry_export_failed",
    "transport_teardown",
    "tts_inference_traceback",
]
# jsonPayload.error_code values worth counting on their own.
CODES = [
    "CONVERSATION_METRICS_FAILED",
    "EOT_DETECTOR_DEGRADED",
    "GREETING_NOT_SUBSCRIBED",
    "GREETING_PLAYOUT_TIMEOUT",
    "LLM_RESPONSE_FILTERED",
    "SESSION_ERROR",
    "STT_STALL_RECOVERY_FAILED",
    "TTS_RETRY_RECOVERY_FAILED",
    "TTS_RETRY_STALLED",
    "TTS_STREAM_CLOSED",
    "TURN_DETECTOR_REPROBE_FAILED",
    "TURN_TRANSCRIPTION_TIMEOUT",
]
# Recovery / lifecycle rows, matched on the exact message.
RECOVERY_MESSAGES = [
    "Client tool ack not received; using fallback result",
    "Client tool ack wait released; session closing",
    "Conversation metrics sent after a retry",
    "Reconnected stalled STT stream",
    "STT stream recovered after a stall reconnect",
    "Spoken fallback line",
    "TTS retry recovered; agent audio started",
    "Transcription timeout during session close; not a stall",
    "Turn detector re-probed on the cloud model",
]
# Answered turns (a `Turn latency` row with metric llm_ttft_ms: the LLM produced a first token) over user
# turns (`User turn shape`), per session (`room`). Normal is 65-75%. When the LLM fails without an error
# row, voice users get silence and nothing else moves: on 2026-11-03 from 03:44Z it was 10% (22/213) with
# 0 errors (ENG-1767). The verdict reads the median session, not the window total: one user who talks
# over every reply (83 turns, 5 answered) pulled a healthy window to 35% on 2026-11-04 06:26Z.
ANSWERED_MIN = 0.40
ANSWERED_SESSION_TURNS = 5  # a session with fewer user turns has no ratio worth reading
ANSWERED_MIN_SESSIONS = 4   # below this many such sessions the median is too noisy to call

# A count above zero on any of these makes the verdict LOOK on its own.
ALWAYS_LOOK = ["STT_STALL_RECOVERY_FAILED", "TTS_RETRY_RECOVERY_FAILED", "TURN_DETECTOR_REPROBE_FAILED",
               "CONVERSATION_METRICS_FAILED", "TTS_RETRY_STALLED[none]"]


def read(flt: str, limit: int = 20000) -> list[dict]:
    """gcloud logging read as the service account; raises naming the credential on failure.

    A result that fills `limit` is refused rather than returned: a truncated page reads exactly
    like a quiet window, so every count built on it (sessions, ERROR+/session, stall outcomes)
    would be silently wrong. Narrow the window or raise the limit instead.
    """
    proc = gcloud_sa.run(
        ["logging", "read", flt, f"--project={PROJECT}", f"--limit={limit}", "--format=json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gcloud logging read failed as {gcloud_sa.credential()}: {proc.stderr.strip()[:300]}")
    rows = json.loads(proc.stdout or "[]")
    if len(rows) >= limit:
        raise RuntimeError(f"a read returned {len(rows)} rows, the --limit cap; counts would be truncated. "
                           "Narrow --minutes/--hours.")
    return rows


def latest_release() -> tuple[str, dt.datetime] | None:
    """(version, published UTC) of the latest LivekitVoiceAgent release, or None when gh can't say."""
    try:
        proc = subprocess.run(["gh", "release", "view", "--repo", AGENT_REPO, "--json", "tagName,publishedAt"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        data = json.loads(proc.stdout) if proc.returncode == 0 else None
        return (data["tagName"].lstrip("v"),
                dt.datetime.fromisoformat(data["publishedAt"].replace("Z", "")[:19])) if data else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def _semver(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else -1 for x in re.split(r"[.+-]", v.lstrip("v")))


def rollout(versions: dict, release: tuple[str, dt.datetime] | None, end: dt.datetime) -> str | None:
    """The info line when several releases serve because the latest one is still draining the old.

    Only while the newest version in the window is the latest release and that release is under
    ROLLOUT_MINUTES old at the window's end. Past that, an older version still serving is a stuck
    drain; a newest version that isn't the latest release is something else entirely.
    """
    if len(versions) < 2 or release is None:
        return None
    tag, published = release
    newest = max((v for v in versions if v != "?"), key=_semver, default=None)
    if newest != tag or not dt.timedelta(0) <= end - published < dt.timedelta(minutes=ROLLOUT_MINUTES):
        return None
    return f"rollout in progress: {versions} ({tag} released {published:%H:%M} UTC)"


def _ts(start: dt.datetime, end: dt.datetime) -> str:
    return f'logName="{LOG}" AND timestamp>="{start.isoformat()}Z" AND timestamp<"{end.isoformat()}Z"'


def _any(field: str, values: list[str]) -> str:
    return "(" + " OR ".join(f'{field}="{v}"' for v in values) + ")"


def collect(start: dt.datetime, end: dt.datetime) -> dict:
    """Every count the verdict needs, for one window."""
    base = _ts(start, end)
    sessions = read(f'{base} AND jsonPayload.message="Session started"')
    bad = read(f"{base} AND severity>=WARNING")
    stamped = read(f"{base} AND (" + " OR ".join(f"jsonPayload.{s}:*" for s in STAMPS) + ")")
    coded = read(f"{base} AND {_any('jsonPayload.error_code', CODES)}")
    recovered = read(f"{base} AND {_any('jsonPayload.message', RECOVERY_MESSAGES)}")
    user_turns = read(f'{base} AND jsonPayload.message="User turn shape"')
    answered = read(f'{base} AND jsonPayload.message="Turn latency" AND jsonPayload.metric="llm_ttft_ms"')

    versions = collections.Counter(
        (r.get("jsonPayload", {}).get("serviceContext") or {}).get("version", "?") for r in sessions
    )
    groups = collections.Counter()
    for r in bad:
        p = r.get("jsonPayload", {})
        msg = re.sub(r"\d+", "N", (p.get("message") or "").split("\n", 1)[0])[:90]
        groups[(r.get("severity", "?"), p.get("error_code") or "", msg)] += 1
    stamps = collections.Counter(s for r in stamped for s in STAMPS if r.get("jsonPayload", {}).get(s))
    signals = collections.Counter()
    for r in coded:
        p = r.get("jsonPayload", {})
        code = p.get("error_code")
        if code == "TTS_RETRY_STALLED":
            code += f"[{p.get('recovery')}]"
        signals[code] += 1
    for r in recovered:
        p = r.get("jsonPayload", {})
        key = p.get("message")
        if key == "Spoken fallback line":
            key += f"[{p.get('line_type')}]"
        signals[key] += 1
    errors = sum(c for (sev, _, _), c in groups.items() if sev in ("ERROR", "CRITICAL", "ALERT", "EMERGENCY"))
    return {"session_ratios": session_ratios(user_turns, answered), "sessions": len(sessions), "versions": dict(versions), "groups": groups, "stamps": dict(stamps),
            "signals": dict(signals), "errors": errors, "user_turns": len(user_turns), "answered": len(answered)}


def _room(r: dict) -> str | None:
    p = r.get("jsonPayload", {})
    return p.get("room") or p.get("transactionSlice")  # not conversationId: an app rejoin reuses it across rooms


def session_ratios(user_turns: list[dict], answered: list[dict]) -> list[float]:
    """Answered over user turns for each session with ANSWERED_SESSION_TURNS+ user turns.

    A row with no room is left out rather than pooled, which would make one fake session of them.
    """
    turns = collections.Counter(k for k in map(_room, user_turns) if k)
    replies = collections.Counter(k for k in map(_room, answered) if k)
    return sorted(replies[k] / n for k, n in turns.items() if n >= ANSWERED_SESSION_TURNS)


def verdict(now: dict, base: dict, rollout_info: str | None = None) -> list[str]:
    """Reasons to look; empty means NORMAL. `rollout_info` set means the version mix is a planned drain."""
    reasons = []
    rate_now = now["errors"] / max(now["sessions"], 1)
    rate_base = base["errors"] / max(base["sessions"], 1)
    if now["errors"] and rate_now > max(2 * rate_base, 0.05):
        reasons.append(f"ERROR+/session {rate_now:.2f} vs {rate_base:.2f} 24h earlier")
    new = sorted(k for k in now["groups"] if k[0] in ("ERROR", "CRITICAL") and k not in base["groups"])
    for sev, code, msg in new:
        reasons.append(f"new {sev} group: {code or '-'} {msg}")
    for key in ALWAYS_LOOK:
        if now["signals"].get(key):
            reasons.append(f"{key} x{now['signals'][key]}")
    reconnects = now["signals"].get("Reconnected stalled STT stream", 0)
    recovered = now["signals"].get("STT stream recovered after a stall reconnect", 0)
    if reconnects - recovered >= 2:
        reasons.append(f"STT stall reconnects {reconnects} vs recovered {recovered}: run `stalls`")
    ratios = now.get("session_ratios", [])
    if len(ratios) >= ANSWERED_MIN_SESSIONS and statistics.median(ratios) < ANSWERED_MIN:
        reasons.append(f"answered-turn ratio: median session {statistics.median(ratios):.0%} over {len(ratios)} sessions "
                       f"with {ANSWERED_SESSION_TURNS}+ user turns ({now.get('answered', 0)}/{now.get('user_turns', 0)} "
                       f"in the window; normal 65-75%): replies may be failing silently")
    if len(now["versions"]) > 1 and not rollout_info:
        reasons.append(f"more than one release serving: {now['versions']}")
    return reasons


def cmd_window(args: argparse.Namespace) -> int:
    end = dt.datetime.now(dt.UTC).replace(microsecond=0, tzinfo=None)
    start = end - dt.timedelta(minutes=args.minutes)
    now = collect(start, end)
    base = collect(start - dt.timedelta(days=1), end - dt.timedelta(days=1))
    info = rollout(now["versions"], latest_release() if len(now["versions"]) > 1 else None, end)
    if args.json:
        dump = {k: {**v, "groups": [[*g, c] for g, c in v["groups"].most_common()]} for k, v in
                (("now", now), ("minus_24h", base))}
        print(json.dumps({**dump, "start": f"{start.isoformat()}Z", "end": f"{end.isoformat()}Z",
                          "look": verdict(now, base, info), "info": [info] if info else []}, indent=2))
        return 0
    print(f"# aurie-voice-agent  {start:%Y-%m-%d %H:%M}–{end:%H:%M} UTC  vs same window -24h")
    for label, w in (("NOW", now), ("-24h", base)):
        print(f"\n## {label}: {w['sessions']} sessions  releases={w['versions']}  ERROR+={w['errors']}")
        ratio = f"{w['answered'] / w['user_turns']:.0%}" if w["user_turns"] else "-"
        rs = w.get("session_ratios", [])
        med = f"median session {statistics.median(rs):.0%} over {len(rs)}" if rs else "no session with 5+ turns"
        print(f"   answered turns: {w['answered']}/{w['user_turns']} ({ratio}; {med})")
        print(f"   stamps:  {w['stamps'] or '-'}")
        print(f"   signals: {w['signals'] or '-'}")
        for (sev, code, msg), c in w["groups"].most_common(args.top):
            print(f"   {c:4d}  {sev:8s} {code:28s} {msg}")
    reasons = verdict(now, base, info)
    print("\n## VERDICT: " + ("LOOK" if reasons else "NORMAL"))
    for r in reasons:
        print(f"   - {r}")
    if info:
        print(f"   info: {info}")
    return 0


def cmd_stalls(args: argparse.Namespace) -> int:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0, tzinfo=None)
    since = now - dt.timedelta(hours=args.hours)
    base = _ts(since, now)
    reconnects = read(f'{base} AND jsonPayload.message="Reconnected stalled STT stream"')
    recoveries = read(f'{base} AND jsonPayload.message="STT stream recovered after a stall reconnect"')
    endings = read(f'{base} AND jsonPayload.message="Ending session"')

    def at(row: dict) -> dt.datetime:
        return dt.datetime.fromisoformat(row["timestamp"].replace("Z", "")[:26])

    recovered_at = collections.defaultdict(list)
    for r in recoveries:
        recovered_at[r["jsonPayload"].get("transaction")].append(at(r))
    ended = {r["jsonPayload"].get("transaction"): (at(r), r["jsonPayload"].get("reason")) for r in endings}
    outcomes = collections.Counter()
    for r in reconnects:
        tx, t = r["jsonPayload"].get("transaction"), at(r)
        if any(x >= t for x in recovered_at[tx]):
            outcomes["recovered"] += 1
        elif tx in ended and ended[tx][0] >= t:
            outcomes[f"session ended: {ended[tx][1]}"] += 1
        elif (now - t).total_seconds() < 600:
            outcomes["pending (<10 min)"] += 1
        else:
            outcomes["unknown"] += 1
    total = len(reconnects)
    rate = f"{100 * outcomes['recovered'] / total:.0f}%" if total else "n/a"
    print(f"# STT stall reconnects since {since:%Y-%m-%d %H:%M} UTC: {total}, recovered {rate}")
    for k, c in outcomes.most_common():
        print(f"   {c:4d}  {k}")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("window", help="last N minutes vs the same window 24h earlier")
    w.add_argument("--minutes", type=int, default=30)
    w.add_argument("--top", type=int, default=15, help="WARNING+ groups to print per window")
    w.add_argument("--json", action="store_true")
    s = sub.add_parser("stalls", help="outcome of each STT stall reconnect")
    s.add_argument("--hours", type=float, default=12)
    args = ap.parse_args()
    try:
        return cmd_window(args) if args.cmd == "window" else cmd_stalls(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
