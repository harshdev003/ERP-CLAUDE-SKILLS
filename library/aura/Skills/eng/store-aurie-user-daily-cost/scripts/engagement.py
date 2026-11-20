"""Per-day Aurie engagement cohorts from Mixpanel (JQL), keyed by Firebase UID.

Used by store.py (and backfill_engagement.py) to stamp each user-day row with
whether the user actually DID something in the app that day vs was only reached
by an ambient/background job or the weekly Insights batch:

  engaged = sent a conversation message that day (the strict "did something" cut)
  active  = fired ANY AI event that day from a MOBILE source (opened/used the AI
            app at all) — superset of engaged. Web-only AI events (Aurie Web /
            mp_lib=web) are excluded so this is a mobile-app DAU.

This is deliberately NOT the shared data:analyze-user-behavior mixpanel_api.py:
that helper does per-user profile / export lookups; this one needs per-day
cohort MEMBERSHIP keyed by Firebase UID, which is a JQL groupBy the shared
helper doesn't expose. Kept small and self-contained on purpose.

Auth: MIXPANEL_SERVICE_ACCOUNT_USERNAME / _SECRET (HTTP Basic), project from
MIXPANEL_SERVICE_ACCOUNT_PROJECT_ID (default 963821). Degrades gracefully —
missing creds or a failed query returns empty cohorts + a note, so the cost
store still writes and the engagement columns just stay blank for that day.
"""
import json
import os
import sys
import time

import requests
from requests.auth import HTTPBasicAuth

JQL_URL = "https://mixpanel.com/api/2.0/jql"
NAMES_URL = "https://mixpanel.com/api/2.0/events/names"
PROJECT_ID = os.environ.get("MIXPANEL_SERVICE_ACCOUNT_PROJECT_ID", "963821")

# engaged = the strict "actually conversed" cut (mobile-only events already).
# Two namespaces both count: standalone Aurie ("AI …") and Aurie-in-Aura
# ("AIAura …" — PROD-6096 established this is a real, structurally distinct
# Aurie surface, not "Aura's own chat"). Order doesn't matter; membership does.
ENGAGED_EVENTS = ("AI Conversation Message Sent", "AIAura Conversation Message Sent")
# active = ANY AI event that day, from mobile. AI events are name-prefixed "AI "
# or "AIAura"; discovered dynamically (events/names) so new AI events
# auto-count, with a hardcoded fallback if that call fails. A user reached
# only by ambient/batch jobs fires no AI mobile event and stays uncounted.
AI_EVENT_PREFIXES = ("AI ", "AIAura")
_AI_EVENTS_FALLBACK = (
    "AI Conversation Message Sent", "AI Conversation Message Received",
    "AI Conversation Session Started", "AI Conversation Session Paused",
    "AI Conversation Session Completed", "AI Conversation Mode Switched",
    "AI Conversation History Opened", "AI Conversation History Screen Seen",
    "AI Conversation Done Screen Completed", "AI Microphone Muted",
    "AI Microphone Unmuted", "AI Connection Started", "AI Login",
    "AI Quick Process Session Started", "AI Quick Process Step Completed",
    "AI Journals Screen Seen",
    "AI Journal Details Seen", "AI Feed Item Tapped", "AI Feed Item Completed",
    "AI Affirmation Completed", "AI Modal Seen", "AI Settings Screen Seen",
    # Aurie-in-Aura (PROD-6096) — same events, "AIAura " namespace.
    "AIAura Aurie Opened", "AIAura Aurie Closed",
    "AIAura Conversation Message Sent", "AIAura Conversation Message Received",
    "AIAura Conversation Session Started", "AIAura Conversation Session Completed",
    "AIAura Conversation History Screen Seen", "AIAura Journals Screen Seen",
    "AIAura Sign Up Screen Seen", "AIAura Sign Up Completed",
)
# Mobile filter — a POSITIVE allowlist so only genuine mobile-client events count.
# "not web" is too loose: it would also let backend/server AI events through
# (mp_lib=node, no Source App), stamping users active without opening the app.
# Mobile = the Aurie/Companion mobile apps (Source App) or a mobile Mixpanel SDK
# (swift = iOS, android). Aurie Web (mp_lib=web) and backend events are excluded.
_MOBILE_PREDICATE = (
    'function(e){var sa=e.properties["Source App"];var lib=e.properties["mp_lib"];'
    'return sa==="Aurie Mobile"||sa==="Aura Companion App"||lib==="swift"||lib==="android";}'
)

# Ambient (per-user daily / per-new-user) job projects — used to explain WHY a
# reached-but-inactive user was billed. Insights is handled separately (batch).
_AMBIENT_PROJECTS = ("notification", "feed", "onboarding")
_AI_EVENTS_CACHE = None


def _creds():
    u = os.environ.get("MIXPANEL_SERVICE_ACCOUNT_USERNAME")
    s = os.environ.get("MIXPANEL_SERVICE_ACCOUNT_SECRET")
    return (u, s) if (u and s) else (None, None)


# events/names has no cursor — it just returns up to `limit`. 1,224 total names
# were observed on 2026-09-16; this is 4x headroom over that, not "unlimited".
# If the project ever grows past this, _ai_event_names degrades the same way
# events/names failing does today: the visible AI event set silently narrows.
# The truncation check below is what turns that into a printed warning instead
# of a silent narrowing.
_NAMES_FETCH_LIMIT = 5000


def _ai_event_names():
    """All current AI-prefixed event names (events/names), cached. Falls back to
    a hardcoded list if the Query API call fails — active then covers that subset."""
    global _AI_EVENTS_CACHE
    if _AI_EVENTS_CACHE is not None:
        return _AI_EVENTS_CACHE
    names = None
    u, s = _creds()
    if u:
        try:
            r = requests.get(NAMES_URL, params={"type": "general", "limit": _NAMES_FETCH_LIMIT,
                                                 "project_id": PROJECT_ID},
                             auth=HTTPBasicAuth(u, s), timeout=60)
            if r.status_code == 200:
                raw = r.json()
                if isinstance(raw, list) and len(raw) >= _NAMES_FETCH_LIMIT:
                    # events/names returned exactly the cap — it may have more
                    # to give. A silently truncated list here means the "active"
                    # flag silently stops covering events outside the first
                    # _NAMES_FETCH_LIMIT names, with no error anywhere.
                    print(f"# ai_event_names: events/names returned {len(raw)} == "
                          f"limit — the AI event set may be truncated", file=sys.stderr)
                names = [n for n in raw if isinstance(n, str) and n.startswith(AI_EVENT_PREFIXES)]
        except Exception:  # noqa: BLE001 — fall back to the static list
            names = None
    if not names:
        names = list(_AI_EVENTS_FALLBACK)
    for ev in ENGAGED_EVENTS:
        if ev not in names:
            names.append(ev)
    _AI_EVENTS_CACHE = names
    return names


def _selectors():
    """JQL event_selectors array for every AI event (active is any of these)."""
    return "[" + ",".join('{event:"%s"}' % e for e in _ai_event_names()) + "]"


def _run_jql(script):
    """POST a JQL script. Return (rows:list|None, note:str|None). Retries 429/5xx
    (JQL is rate-limited) honouring Retry-After then exponential backoff, and
    degrades to (None, note) rather than raising — callers never block."""
    u, s = _creds()
    if not u:
        return None, "no MIXPANEL_SERVICE_ACCOUNT creds — engagement columns blank"
    # Longer backoff schedule than a plain 2**attempt: JQL's per-project
    # concurrency/rate limit needs real waits, not sub-second retries.
    delays = [5, 15, 30, 60, 90]
    for attempt in range(len(delays) + 1):
        try:
            r = requests.post(JQL_URL, data={"script": script, "project_id": PROJECT_ID},
                              auth=HTTPBasicAuth(u, s), timeout=300)
        except Exception as e:  # noqa: BLE001
            if attempt == len(delays):
                return None, f"Mixpanel JQL error ({e}) — engagement columns blank"
            time.sleep(delays[attempt])
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            if attempt == len(delays):
                return None, f"Mixpanel JQL {r.status_code} after retries — engagement columns blank"
            ra = r.headers.get("Retry-After", "")
            time.sleep(float(ra) if ra.replace(".", "", 1).isdigit() else delays[attempt])
            continue
        if r.status_code != 200:
            return None, f"Mixpanel JQL {r.status_code} — engagement columns blank"
        return r.json(), None
    return None, "Mixpanel JQL retries exhausted — engagement columns blank"


def day_engagement(day):
    """Return (engaged:set[uid], active:set[uid], note:str|None) for one UTC day.

    Cohorts are keyed by the Firebase UID (Mixpanel "UserID" event property),
    matching the store's user_id. On any failure returns (set(), set(), note)
    so the caller degrades rather than blocks. Used by the DAILY store append
    (one call/day). For a multi-day backfill use window_engagement — one query
    for the whole range avoids the per-day rate-limit storm.
    """
    script = (
        "function main(){var EE=%s;return Events({" % json.dumps(list(ENGAGED_EVENTS))
        + 'from_date:"%s",to_date:"%s",event_selectors:%s' % (day, day, _selectors())
        + "}).filter(" + _MOBILE_PREDICATE + ").groupBy([\"properties.UserID\","
        + 'function(e){return EE.indexOf(e.name)>=0?"msg":"x";}],mixpanel.reducer.count())'
        + ".map(function(r){return {uid:r.key[0],kind:r.key[1]};});}"
    )
    rows, note = _run_jql(script)
    if rows is None:
        return set(), set(), note
    engaged, active = set(), set()
    for row in rows:
        uid = row.get("uid")
        if not uid:
            continue
        active.add(uid)                       # any AI mobile event → active
        if row.get("kind") == "msg":          # sent a message → engaged
            engaged.add(uid)
    return engaged, active, None


def window_engagement(from_date, to_date):
    """Return ({day: (engaged:set, active:set)}, note:str|None) for an inclusive
    UTC date range in ONE JQL query (day-bucketed by event UTC time). One call
    for the whole window — the rate-limit-friendly path for backfills, vs 38
    per-day calls that trip Mixpanel's JQL 429. On failure returns ({}, note)."""
    script = (
        "function main(){var EE=%s;return Events({" % json.dumps(list(ENGAGED_EVENTS))
        + 'from_date:"%s",to_date:"%s",event_selectors:%s' % (from_date, to_date, _selectors())
        + "}).filter(" + _MOBILE_PREDICATE + ").groupBy(["
        + 'function(e){return (new Date(e.time)).toISOString().slice(0,10);},'
        + '"properties.UserID",'
        + 'function(e){return EE.indexOf(e.name)>=0?"msg":"x";}],mixpanel.reducer.count())'
        + ".map(function(r){return {day:r.key[0],uid:r.key[1],kind:r.key[2]};});}"
    )
    rows, note = _run_jql(script)
    if rows is None:
        return {}, note
    out = {}
    for row in rows:
        day, uid, kind = row.get("day"), row.get("uid"), row.get("kind")
        if not day or not uid:
            continue
        eng, act = out.setdefault(day, (set(), set()))
        act.add(uid)                          # any AI mobile event → active
        if kind == "msg":                     # sent a message → engaged
            eng.add(uid)
    return out, None


def activity_source(uid, engaged, active, top_project):
    """Reason tag for one user-day: why is this user in the store today?

    engaged / active come first (they did something). Otherwise classify by the
    dominant cost project (top_project from the OpenAI per-user walk):
    Insights -> insights-batch, Notification/Feed/Onboarding -> ambient,
    any conversation-pipeline project -> usage (pipeline cost, no same-day event),
    unknown/absent -> reached.
    """
    if uid in engaged:
        return "engaged"
    if uid in active:
        return "active"
    tp = (top_project or "").lower()
    if "insight" in tp:
        return "insights-batch"
    if any(a in tp for a in _AMBIENT_PROJECTS):
        return "ambient"
    if tp:
        return "usage"
    return "reached"
