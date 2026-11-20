#!/usr/bin/env python3
"""Watch Cloud Armor's aura-web-edge policy for real users or our apps being denied or throttled.

Read-only on GCP: it reads load-balancer logs and the policy, and never changes either.

  watch    every --interval seconds for --checks windows (default 12 x 5 min): one line per window
  daily    the last 24 h against the 24 h before
  compare  served vs denied for a rule's match in a window before and after a change, plus the
           must-not-deny re-checks

Exit 0 clean, 1 flagged (a real-user signal), 2 a log or policy read failed. A failed read is never
reported as clean.

Counts and masked route shapes only: no IP, user id, email, token or full URL is ever printed or
written. Addresses are held in memory for set operations (the scanner set, distinct counts) only.
The rule numbers, crawler list and thresholds are read from references/rules.md.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcloud_sa  # noqa: E402

RULES_MD = Path(__file__).resolve().parents[1] / "references" / "rules.md"
FIXTURE = Path(__file__).resolve().parents[1] / "assets" / "fixture-log.json"
# What the fixture must produce: our app denied once, and the www static-asset throttles
# (the ENGBACK-2502 gap). The scanner's, the bot's and Webflow's entries must not flag.
SELFTEST_FLAGS = ["app_ua", "static_throttle"]
UTC = dt.timezone.utc


class ReadFailed(RuntimeError):
    pass


def load_config(path: Path = RULES_MD) -> dict:
    m = re.search(r"```json\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S)
    if not m:
        raise SystemExit(f"no JSON block in {path}")
    return json.loads(m.group(1))


def iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(v: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


# --- one log entry -------------------------------------------------------------------------------
def req(e: dict) -> dict:
    return e.get("httpRequest") or {}


def esp(e: dict) -> dict:
    return (e.get("jsonPayload") or {}).get("enforcedSecurityPolicy") or {}


def denied(e: dict) -> bool:
    return esp(e).get("outcome") == "DENY"


def priority(e: dict):
    return esp(e).get("priority")


def host_path(url: str) -> tuple[str, str]:
    m = re.match(r"https?://([^/?#]+)([^?#]*)", url or "")
    return (m.group(1), m.group(2) or "/") if m else ("", "/")


def ip_host(host: str) -> bool:
    """A bare IPv4 (with or without a port) or a bracketed IPv6 host: the load balancer's own address."""
    return bool(re.fullmatch(r"[\d.]+(:\d+)?|\[[0-9A-Fa-f:.]+\](:\d+)?", host or ""))


def shape(url: str) -> str:
    """`<host label> /<seg>/<seg>`: ids, numbers and long segments become `:x`; an IP host prints as `ip`."""
    host, path = host_path(url)
    if ip_host(host):
        host = "ip"
    seg = [s if (s.isalpha() and len(s) < 15) else ":x" for s in path.strip("/").split("/")[:2] if s]
    return f"{host.split('.')[0] or '?'} /" + "/".join(seg)


def mask_ua(ua: str) -> str:
    return re.sub(r"[\d._]+", "", ua or "")[:40]


def backend(e: dict) -> str:
    return ((e.get("resource") or {}).get("labels") or {}).get("backend_service_name", "")


# --- the checks ----------------------------------------------------------------------------------
def analyze(entries: list[dict], cfg: dict, level: str = "window") -> dict:
    """Counts and flags for one window. Pure: the fixture test drives it directly."""
    th = cfg["thresholds"][level]
    scan_rule, thr_rule = cfg["scanner_rule"], cfg["throttle_rule"]
    non_user = re.compile(cfg["non_user_ua"])
    app = re.compile(cfg["app_ua"])
    blog = re.compile(cfg["blog_path"])
    api = re.compile(cfg["api_path"])
    static_host, static_path = re.compile(cfg["static_host"]), re.compile(cfg["static_path"])

    e5 = sum(1 for e in entries if (req(e).get("status") or 0) >= 500
             and not blog.search(host_path(req(e).get("requestUrl"))[1]))
    den = [e for e in entries if denied(e)]
    scan = {req(e).get("remoteIp") for e in den if priority(e) == scan_rule}
    d4 = [e for e in den if priority(e) == thr_rule]

    def real_client(e):
        """Not a client that hit the scanner rule, and not a self-declared bot or link-preview fetcher."""
        return req(e).get("remoteIp") not in scan and not non_user.search(req(e).get("userAgent") or "")

    app_hits = [e for e in den if priority(e) != scan_rule and app.match(req(e).get("userAgent") or "")]
    # The load balancer's bare IP is only ever reached by scanners: no page or app uses it.
    api_hits = [e for e in d4 if real_client(e) and api.search(host_path(req(e).get("requestUrl"))[1])
                and not ip_host(host_path(req(e).get("requestUrl"))[0])]
    coach = [e for e in den if "coach-web" in backend(e) and priority(e) != scan_rule
             and not (priority(e) == thr_rule and req(e).get("remoteIp") in scan)]
    static = [e for e in d4 if real_client(e)
              and static_host.search(host_path(req(e).get("requestUrl"))[0])
              and static_path.search(host_path(req(e).get("requestUrl"))[1])]
    counts = {"app_ua": len(app_hits), "api_throttle": len(api_hits), "coach_deny": len(coach),
              "static_throttle": len(static), "e5xx": e5}
    flags = [k for k, v in counts.items() if v > th[k]]
    return {
        "entries": len(entries),
        "denies": dict(sorted(collections.Counter(str(priority(e)) for e in den).items())),
        "r4000_ips": len({req(e).get("remoteIp") for e in d4}),
        "top4000": collections.Counter(shape(req(e).get("requestUrl")) for e in d4).most_common(3),
        "ua4000": collections.Counter(mask_ua(req(e).get("userAgent")) for e in d4).most_common(2),
        "counts": counts,
        "flags": flags,
    }


def must_not_deny(entries: list[dict], cfg: dict) -> dict:
    """Denies of anything that must always get through (link previews, crawlers, our apps, /.well-known/)."""
    out = {}
    for name, m in cfg["must_not_deny"].items():
        ua = re.compile(m["ua"]) if "ua" in m else None
        path = re.compile(m["path"]) if "path" in m else None
        # A rule-200 deny is a scanner path: probing, whoever's user agent it carries.
        out[name] = sum(1 for e in entries if denied(e) and priority(e) != cfg["scanner_rule"]
                        and (not ua or ua.search(req(e).get("userAgent") or ""))
                        and (not path or path.search(host_path(req(e).get("requestUrl"))[1])))
    return out


def line(a: dict) -> str:
    c = a["counts"]
    return (("FLAG " if a["flags"] else "") + f"5xx={c['e5xx']} denies={a['denies']} r4000_ips={a['r4000_ips']} "
            f"r4000_api={c['api_throttle']} r4000_static={c['static_throttle']} coach_non200={c['coach_deny']} "
            f"app_ua={c['app_ua']} top4000={a['top4000']} ua4000={a['ua4000']}"
            + (f" flagged={a['flags']}" if a["flags"] else ""))


# --- GCP reads -----------------------------------------------------------------------------------
def gcloud_json(args: list[str]):
    out = gcloud_sa.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode:
        raise ReadFailed(f"{gcloud_sa.credential()}: {(out.stderr or '').strip()[:300]}")
    try:
        return json.loads(out.stdout or "[]")
    except json.JSONDecodeError as e:
        raise ReadFailed(f"unreadable gcloud output: {e}")


def read_logs(cfg: dict, since: dt.datetime, until: dt.datetime, extra: str = "") -> list[dict]:
    """The window's entries, read an hour at a time so a busy day stays under the per-read limit."""
    rows, start = [], since
    while start < until:
        end = min(start + dt.timedelta(hours=1), until)
        flt = (f'resource.type="http_load_balancer" AND resource.labels.backend_service_name:"{cfg["backend"]}" '
               f'AND timestamp>="{iso(start)}" AND timestamp<"{iso(end)}"')
        flt += f" AND ({extra})" if extra else (' AND (httpRequest.status>=500 OR httpRequest.status=429 OR '
                                                'jsonPayload.enforcedSecurityPolicy.outcome="DENY")')
        part = gcloud_json(["logging", "read", flt, "--project", cfg["project"],
                            "--limit", str(cfg["log_limit"]), "--format", "json"])
        if len(part) >= cfg["log_limit"]:
            print(f"WARNING: {iso(start)}..{iso(end)} hit the {cfg['log_limit']}-entry read limit; "
                  "counts are a floor", file=sys.stderr)
        rows += part
        start = end
    return rows


def known(p: int, cfg: dict) -> bool:
    return any(p == k if isinstance(k, int) else k[0] <= p <= k[1] for k in cfg["known_priorities"])


def unknown_priorities(policy: dict, cfg: dict) -> list[int]:
    return sorted(r["priority"] for r in policy.get("rules", []) if not known(r["priority"], cfg))


def check_policy(cfg: dict) -> dict:
    d = gcloud_json(["compute", "security-policies", "describe", cfg["policy"], "--project", cfg["project"],
                     "--format", "json"])
    d = d[0] if isinstance(d, list) else d
    for p in unknown_priorities(d, cfg):
        print(f"WARNING: rule {p} is not in references/rules.md; its denies are counted but not interpreted",
              file=sys.stderr)
    return d


# --- modes ---------------------------------------------------------------------------------------
def cmd_watch(cfg: dict, args) -> int:
    check_policy(cfg)
    since = parse_ts(args.since) if args.since else dt.datetime.now(UTC) - dt.timedelta(seconds=args.interval)
    for i in range(args.checks):
        now = dt.datetime.now(UTC)
        a = analyze(read_logs(cfg, since, now), cfg)
        print(f"{now:%H:%M:%SZ} [{since:%H:%M:%SZ}..] {line(a)}", flush=True)
        if a["flags"]:
            print("ANOMALY")
            return 1
        since = now
        if i < args.checks - 1:
            time.sleep(args.interval)
    print("watch done, clean")
    return 0


def cmd_daily(cfg: dict, args) -> int:
    check_policy(cfg)
    now = parse_ts(args.until) if args.until else dt.datetime.now(UTC)
    cur = analyze(read_logs(cfg, now - dt.timedelta(hours=24), now), cfg, "daily")
    prev = analyze(read_logs(cfg, now - dt.timedelta(hours=48), now - dt.timedelta(hours=24)), cfg, "daily")
    if not cur["flags"]:
        print(f"daily {now:%Y-%m-%d %H:%MZ} clean: {line(cur)}")
        return 0
    print(f"daily {now:%Y-%m-%d %H:%MZ} FLAG {cur['flags']}")
    print(f"  last 24 h:  {line(cur)}")
    print(f"  prior 24 h: {line(prev)}")
    return 1


def cmd_compare(cfg: dict, args) -> int:
    policy = check_policy(cfg)
    if args.rule not in {r["priority"] for r in policy.get("rules", [])}:
        print(f"WARNING: rule {args.rule} is not in the live policy", file=sys.stderr)
    span = dt.timedelta(hours=args.hours)
    match = []
    if args.match_ua:
        match.append(f'httpRequest.userAgent=~"{args.match_ua}"')
    if args.match_path:
        match.append(f'httpRequest.requestUrl=~"{args.match_path}"')
    flagged = False
    for label, start in (("before", parse_ts(args.before)), ("after", parse_ts(args.after))):
        end = start + span
        matched = read_logs(cfg, start, end, " AND ".join(match)) if match else []
        deny = sum(1 for e in matched if denied(e) and priority(e) == args.rule)
        extra = {}
        for rx in args.count or []:
            r = re.compile(rx)
            extra[rx] = sum(1 for e in matched if r.search(host_path(req(e).get("requestUrl"))[1]))
        guard = must_not_deny(read_logs(cfg, start, end, 'jsonPayload.enforcedSecurityPolicy.outcome="DENY"'), cfg)
        bad = {k: v for k, v in guard.items() if v}
        flagged |= bool(bad)
        print(f"{label} {iso(start)}..{iso(end)}: rule {args.rule} match served={len(matched) - deny} "
              f"denied={deny}" + (f" counts={extra}" if extra else "")
              + (f" FLAG must-not-deny={bad}" if bad else " must-not-deny clean"))
    return 1 if flagged else 0


def cmd_selftest(cfg: dict, args) -> int:
    """Dry run on the bundled fixture: no GCP call. Checks the flags and that no address is printed."""
    entries = json.loads(FIXTURE.read_text(encoding="utf-8"))
    a = analyze(entries, cfg)
    out = line(a)
    print(out)
    ips = {req(e).get("remoteIp") for e in entries}
    leaked = [ip for ip in ips if ip and ip in out]
    if a["flags"] != SELFTEST_FLAGS or leaked:
        print(f"SELFTEST FAILED: flags={a['flags']} expected={SELFTEST_FLAGS} leaked_addresses={len(leaked)}")
        return 1
    print("selftest ok")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch", help="one line per window, flag on a real-user signal")
    w.add_argument("--checks", type=int, default=12, help="windows to watch (12 = 1 h; 2016 = 7 days)")
    w.add_argument("--interval", type=int, default=300, help="seconds per window (default 300)")
    w.add_argument("--since", help="start of the first window, ISO (default one interval ago)")
    d = sub.add_parser("daily", help="last 24 h against the 24 h before")
    d.add_argument("--until", help="end of the day, ISO (default now)")
    c = sub.add_parser("compare", help="a rule change's effect, before vs after")
    c.add_argument("--rule", type=int, required=True, help="the changed rule's priority")
    c.add_argument("--before", required=True, help="start of the before window, ISO")
    c.add_argument("--after", required=True, help="start of the after window, ISO")
    c.add_argument("--hours", type=float, default=24, help="length of each window (default 24)")
    c.add_argument("--match-ua", help="the rule's user-agent regex (RE2), to count served vs denied")
    c.add_argument("--match-path", help="the rule's request-URL regex (RE2)")
    c.add_argument("--count", action="append", help="extra path regex to count in the matched requests")
    sub.add_parser("selftest", help="dry run on the bundled fixture log; no GCP call")
    args = ap.parse_args()
    cfg = load_config()
    try:
        return {"watch": cmd_watch, "daily": cmd_daily, "compare": cmd_compare, "selftest": cmd_selftest}[args.cmd](cfg, args)
    except ReadFailed as e:
        print(f"READ FAILED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
