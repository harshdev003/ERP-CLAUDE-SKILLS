#!/usr/bin/env python3
"""Turn eng:profile-firebase-rtdb's counts-only output into findings (common.py).

  evaluate_profile.py --profile <run-profile stdout> --minutes N [--rest-detail <in> --rest-bursts <out>]

  fail   PROFILE FAILED, or no summary (nothing was captured: never a clean result)
  alert  denies on a watched node at or above the breakage threshold (a rules change broke a
         client): users >= 100 per 10 min, any other node >= max(20 per 10 min, 0.5% of its
         allowed ops) -- below that it is the signed-out listener pattern (`auth != null` rules) --
         unless they came from fewer than MIN_SOURCES distinct ids and IPs (one client retrying);
         users reads > 20,000 per 10 min (enumeration or scraping; the 7-day max was about 9,600);
         allowed REST reads > 300 per 10 min (a REST burst, named by path shape and client)
  note   total denies, root / whole-node / guessed-name attempts (the Incident Log step logs them),
         except allowed reads of PUBLIC nodes; signed-out users/{id} writes, smaller watched-node denies and one client's retries;
         allowed REST reads by path shape (counts only), so a burst seen elsewhere can be attributed later
Thresholds scale with the window length.

On a REST burst, --rest-bursts gets one line per IP + user agent + UTC day (from run-profile.sh
--rest-detail) with at least REST_SOURCE reads per 10 min, plus the window length and the non-public
top-level nodes it read, for the Security Incident Log (mode 600: it holds IPs). Always written, empty
when there is no burst.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import common

USERS_DENY, NODE_DENY, NODE_DENY_SHARE, USERS_READS = 100, 20, 0.005, 20000
REST_READS = 300
# One IP + user agent's share of a burst that goes to the Security Incident Log: the 2026-11-03 00:49Z
# /channels scrape was 286 reads from one source; the app's own traffic is a few reads per IP.
REST_SOURCE = 100
# Denies from fewer distinct ids and fewer distinct IPs than this are one client retrying: a note.
MIN_SOURCES = 3

# Top-level nodes whose rule is `.read: true` (Firebase rules.json, origin/master 92a260b, 2026-10-22).
# An allowed whole read of one is the rule working, not a probe. Kept explicit: re-check on a rules change.
PUBLIC = frozenset({
    "aiIterableMobileEvents", "appConfig", "auraCompanion", "backgroundImages", "blockedCommunities",
    "categories", "celebrities", "channels", "coaches", "communities", "countries", "experiments",
    "exploreHeaders", "exploreTopics", "iterableMobileEvents", "liveEventDetails", "meditationChallenges",
    "meditations", "slugCategory", "slugChannel", "slugChallenge", "slugCoach", "slugCommunity",
    "slugCourse", "slugEvent", "slugMeditation", "topics", "unguidedMeditations", "webPromoCodes",
})


def node_deny_limit(node: str, allowed: int, scale: float) -> float:
    if node == "users":
        return USERS_DENY * scale
    return max(NODE_DENY * scale, NODE_DENY_SHARE * allowed)


def public_read(line: str) -> bool:
    m = re.match(r"\d+\s+allowed\s+\S+\s+/(\S+)\s", line)
    return bool(m and m[1] in PUBLIC)


def burst_sources(path: str, minutes: float, scale: float) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return out
    for src in lines:
        if src.get("n", 0) < REST_SOURCE * scale:
            continue
        tops = {sh.split("/")[1] for sh in src.get("shapes", {}) if sh != "/"}
        out.append({**src, "minutes": minutes, "nonpublic": sorted(t for t in tops if t not in PUBLIC)})
    return out


def write_private(path: str, lines: list[dict]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(l) + "\n" for l in lines)


def section(text: str, head: str) -> list[str]:
    out, on = [], False
    for line in text.splitlines():
        if line.startswith(head):
            on = True
            continue
        if on and line and not line.startswith(" "):
            break
        if on and line.strip():
            out.append(line.strip())
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--rest-detail")
    ap.add_argument("--rest-bursts")
    a = ap.parse_args()
    if a.rest_bursts:
        write_private(a.rest_bursts, [])
    text = open(a.profile, encoding="utf-8", errors="replace").read()
    text = re.sub(r"\b\d{1,3}(\.\d{1,3}){3}\b", "<ip>", text)
    scale = max(a.minutes / 10, 0.1)
    if "PROFILE FAILED" in text or "Permission denied:" not in text:
        tail = " / ".join(l.strip() for l in text.splitlines()[-3:] if l.strip())[:200]
        common.emit("profile", "fail", "profile-failed", f"RTDB profile captured nothing: {tail}")
        return
    m = re.search(r"Permission denied: (\d+) of (\d+)", text)
    span = (re.search(r"Profile (\S+) -> (\S+)", text) or [None, "", ""])
    when = f"{span[1][11:16]}-{span[2][11:16]}Z" if span[1] else ""
    common.emit("profile", "note", "profile-denies", f"Permission denied: {m[1]} of {m[2]} rule-checked ops {when}")

    for line in section(text, "Root / whole top-level node"):
        if re.match(r"\d+\s+(DENIED|allowed)", line) and not public_read(line):
            common.emit("profile", "note", "profile-probe", "probe: " + re.sub(r"\s+", " ", line))

    for line in section(text, "Signed-out writes"):
        if line != "none":
            common.emit("profile", "note", "profile-signedout", "signed-out users/{id} writes: " + re.sub(r"\s+", " ", line))

    rest = section(text, "Allowed REST reads by path shape")
    tot = re.match(r"total (\d+) \((\d+) shallow\) from (\d+) IPs", rest[0]) if rest else None
    if tot:
        top = "; ".join(re.sub(r"\s+", " ", l) for l in rest[1:4])[:400]
        n = int(tot[1])
        # The raw ops don't carry ?shallow=true, so a 0 here is no evidence: the shallow-gets note has the count.
        marked = f" ({tot[2]} marked shallow)" if int(tot[2]) else ""
        line = f"allowed REST reads: {n}{marked} from {tot[3]} IPs {when} [{top}]"
        if n > REST_READS * scale:
            logged = ""
            if a.rest_detail and a.rest_bursts:
                srcs = burst_sources(a.rest_detail, a.minutes, scale)
                write_private(a.rest_bursts, srcs)
                logged = f"; {len(srcs)} source(s) at {REST_SOURCE * scale:.0f}+ reads for the Incident Log"
            common.emit("profile", "alert", "profile-rest-burst",
                        f"REST burst: {line}, above {REST_READS * scale:.0f} for the window{logged}")
        else:
            common.emit("profile", "note", "profile-rest-reads", line)

    # Counts only, every pass: a realtime read surge can be attributed from the pass log (ENG-1765).
    rt = section(text, "Allowed realtime reads by path shape")
    rtot = re.match(r"total (\d+) ops \((\d+) admin\), (\d+) bytes, from (\d+) IPs", rt[0]) if rt else None
    if rtot:
        top = "; ".join(re.sub(r"\s+", " ", l) for l in rt[1:4])[:400]
        common.emit("profile", "note", "profile-realtime-reads",
                    f"allowed realtime reads: {rtot[1]} ops ({rtot[2]} admin), {rtot[3]} bytes from {rtot[4]} IPs "
                    f"{when} [{top}]")

    denied_lines = section(text, "Denied on watched nodes")
    for line in section(text, "Watched nodes, rule-checked ops"):
        w = re.match(r"(\S+): (\d+) allowed, (\d+) denied(?: \(from (\d+) ids, (\d+) IPs\))?", line)
        if not w:
            continue
        node, ok, deny = w[1], int(w[2]), int(w[3])
        if node == "users" and ok > USERS_READS * scale:
            common.emit("profile", "alert", "profile-users-surge",
                        f"/users: {ok} allowed ops {when}, above {USERS_READS * scale:.0f} for the window (possible scraping)")
        limit = node_deny_limit(node, ok, scale)
        if deny:
            detail = "; ".join(re.sub(r"\s+", " ", l) for l in denied_lines
                               if re.search(rf"/{re.escape(node)}(/|\s|$)", l))[:300]
            level = "alert" if deny >= limit else "note"
            sources = f", from {w[4]} ids and {w[5]} IPs" if w[4] else ""
            # One client retrying in a loop is not a rules change that broke clients. Older profiler output
            # has no source counts and keeps the event-count rule.
            looping = w[4] is not None and max(int(w[4]), int(w[5])) < MIN_SOURCES
            if level == "alert" and looping:
                level, sources = "note", sources + " (one client retrying, not a breakage)"
            common.emit("profile", level, f"profile-watched-deny-{node}",
                        f"watched /{node}: {deny} denied, {ok} allowed{sources} {when}" + (f" [{detail}]" if detail else ""))


if __name__ == "__main__":
    main()
