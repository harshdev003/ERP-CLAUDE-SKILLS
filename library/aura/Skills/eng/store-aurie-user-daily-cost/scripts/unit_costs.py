#!/usr/bin/env python3
"""
Append daily Aurie unit costs to the unit-cost store CSV (idempotent).

Tracks the two business-model unit costs per day so drift is visible
(e.g. a Memory-pipeline optimization should pull cost_per_msg down):

  cost_per_msg         = message-driven LLM $ / visible messages (blended)
  cost_per_msg_paying  = cost_per_msg x the DAY'S measured non-free/blended
                         per-completion cost ratio, computed from the per-user
                         walk JSON + the per-user store's subscription states.
                         No fixed factor. Left EMPTY when per-user data for the
                         date is unavailable (e.g. deep backfill) — never faked.
  cost_per_msg_free    = same construction for the free cohort (free users'
                         messages are cheaper — shallow context, no memory)
  cost_per_voice_min   = TOTAL voice $ (all vendors) / TOTAL voice minutes
                         (ElevenLabs ConvAI minutes + LiveKit session minutes;
                         ElevenLabs TTS minutes are speech inside those LiveKit
                         sessions, so they are NOT added again) — the blended
                         all-path rate. DENOMINATOR GUARD: when LiveKit minutes
                         are 0 there is nothing to divide the LiveKit-path $ by,
                         so they are EXCLUDED from this blended
                         RATE only (voice_cost still carries them, because a
                         real dollar must not vanish for want of a denominator).
                         Whether that exclusion is a DATA GAP is separate: only a
                         failed log read, or Cartesia/Deepgram billing with no
                         minutes beside it, means the logs are actually unread —
                         the subscription accrual is owed on a silent day.
                         Dividing LiveKit-path $ by ElevenLabs-only minutes would
                         silently inflate the rate. Numerator and denominator
                         always cover the same paths; the excluded $ is flagged
                         in the run log.
  cost_per_voice_min_11labs = ElevenLabs ConvAI fiat $ / ConvAI minutes_used (the
                         legacy agent path rate; TTS $ and minutes excluded).
  cost_per_voice_min_livekit = LiveKit-path $ (Cartesia + Fish + Deepgram/LiveKit
                         Inference STT + LiveKit agent minutes, as the per-user
                         store booked them) / LiveKit session-minutes. The LiveKit path should be
                         cheaper than ElevenLabs — tracking both proves the
                         migration saving. Left EMPTY when there are no LiveKit
                         minutes for the day (nothing to divide by).

Row schema:
  date, openai_total, msg_driven_cost, memory_cost, ambient_cost,
  visible_messages, cost_per_msg, cost_per_msg_paying, cost_per_msg_free,
  voice_cost, voice_minutes, cost_per_voice_min,
  cost_per_voice_min_11labs, cost_per_voice_min_livekit

Also maintains the engineering-KPI store (--kpi-csv / $AURIE_KPI_CSV) — one row
per day:
  date, llm_cost_per_paying_msg, voice_cost_per_min, ambient_per_dau_month,
  ambient_per_active_user_month, ambient_per_engaged_user_month
The three ambient KPIs share the same numerator (ambient_cost x 30) over three
denominators from the per-user store: reached (any billed user — the original
KR, unchanged), active (opened the app that day), engaged (sent a message).
reached is ~5x active, so the per-active/engaged figures are much higher — they
measure ambient cost per genuinely-active user, not per batch-reached user. The
active/engaged KPIs are BLANK for days whose per-user rows predate the
engagement columns. KPI rows for already-stored unit-cost days are derived from
the unit-cost CSV without refetching.

Definitions (classification rule: does the prompt fire because the user is
actively engaging, or as a per-user daily job regardless of chat volume?):
- message-driven projects: Conversation, Memory, Summary, Profile, Journal —
  fire as a function of conversation activity, so they scale with engagement.
  (Summary + Profile both fire ~once per conversation; verified ~0.185-0.189
  requests per visible message, i.e. statistical twins — they share a bucket.)
- ambient projects: Notification, Feed, Onboarding, Insights — daily/per-new-user jobs
  that run regardless of how much the user chats (verified ~1.66-1.68 requests
  per DAU, cv 0.01 — they track active-user count, not message count).
  Insights added 2026-06-30 (per-user insight generation + renderers).
- visible_messages: Conversation [Production] num_model_requests (one visible
  user turn ~= one conversation completion; background prompters excluded).
- Only [Production] projects count toward buckets and visible_messages; dev and
  archived projects show up in openai_total but not in the unit-cost ratios.
- A day with zero total, zero visible messages, or zero voice minutes is refused
  (partial day or upstream API failure) rather than stored as zeros.
- memory_cost has its own column so Memory-pipeline cost changes are trendable.

Usage:
    unit_costs.py                                  # yesterday (UTC)
    unit_costs.py --date 2026-06-09                # one day
    unit_costs.py --date 2026-06-01 --until 2026-06-09   # inclusive backfill
    unit_costs.py --csv /path/to/daily_unit_costs.csv

CSV path resolution: --csv > $AURIE_UNIT_COST_CSV > ./daily_unit_costs.csv
Per-user inputs for the paying column: --llm-json template (default
/tmp/cpu_{date}.json, the Step 2 walk output) + --user-csv (default
$AURIE_COST_CSV, the Step 3 per-user store, for subscription states).

Env: OPENAI_ADMIN_API_KEY, ELEVENLABS_ADMIN_API_KEY (both required).
Optional (LiveKit-path voice split): CARTESIA_ADMIN_API_KEY,
DEEPGRAM_OWNER_API_KEY/DEEPGRAM_API_KEY, GCP_KEY/GOOGLE_APPLICATION_CREDENTIALS.
Missing keys simply leave cost_per_voice_min_livekit empty for the day.

LiveKit-path $ and minutes are read from the per-user store (--user-csv), which
books the month-to-date LiveKit overage and LiveKit Inference STT. Only a day with
no per-user rows falls back to the vendor APIs at the PRICING-SHEET ESTIMATE rates
below — an undercount, warned on stderr; override via --cartesia-rate-per-million /
--deepgram-rate-per-hour / --livekit-rate-per-min.
"""
import json
import os, sys, csv, argparse, datetime, requests
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store as store_mod  # noqa: E402  (reuse the vendor day-total helpers)
import paths as store_paths  # noqa: E402  (BQ-primary CSV cache path resolution)

# csv defaults to \r\n. These stores are read by shell tooling (awk -F, / cut) as
# often as by the csv module, and a CRLF file leaves a stray \r on the LAST column
# — activity_source reads as "ambient\\r" to awk while csv strips it. Write LF.
LF = "\n"

COLS = ["date", "openai_total", "msg_driven_cost", "memory_cost", "ambient_cost",
        "visible_messages", "cost_per_msg", "cost_per_msg_paying", "cost_per_msg_free",
        "voice_cost", "voice_minutes", "cost_per_voice_min",
        "cost_per_voice_min_11labs", "cost_per_voice_min_livekit",
        "livekit_voice_users", "elevenlabs_agent_voice_users"]
# Prior unit-cost schemas, migrated in place before new-width rows land (otherwise appending wider
# rows onto a narrower header yields a malformed CSV). Any column present in COLS but not in the old
# header is backfilled BLANK for historical rows — never faked:
#   OLD_COLS   — 12 cols: single blended cost_per_voice_min, no 11labs/livekit split.
#   PREV_COLS  — 14 cols: adds the per-voice-path rate split, no per-day voice-path user counts.
OLD_COLS = ["date", "openai_total", "msg_driven_cost", "memory_cost", "ambient_cost",
            "visible_messages", "cost_per_msg", "cost_per_msg_paying", "cost_per_msg_free",
            "voice_cost", "voice_minutes", "cost_per_voice_min"]
PREV_COLS = OLD_COLS + ["cost_per_voice_min_11labs", "cost_per_voice_min_livekit"]
KPI_COLS = ["date", "llm_cost_per_paying_msg", "voice_cost_per_min", "ambient_per_dau_month",
            "ambient_per_active_user_month", "ambient_per_engaged_user_month"]
MSG_DRIVEN = ("conversation", "memory", "summary", "profile", "journal")
AMBIENT = ("notification", "feed", "onboarding", "insights")


def unix(d):
    return int(datetime.datetime.fromisoformat(d).replace(tzinfo=datetime.timezone.utc).timestamp())


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def project_names(oai):
    out = {}; cur = None
    while True:
        p = {"limit": 100, "include_archived": "true"}
        if cur:
            p["after"] = cur
        resp = requests.get("https://api.openai.com/v1/organization/projects",
                            headers=oai, params=p, timeout=60)
        resp.raise_for_status()
        r = resp.json()
        for x in r.get("data", []):
            out[x["id"]] = x["name"]
        if r.get("has_more"):
            cur = r["data"][-1]["id"]
        else:
            break
    return out


def by_project(oai, url, field, day, nxt):
    out = {}; page = None
    while True:
        p = {"start_time": unix(day), "end_time": unix(nxt), "bucket_width": "1d",
             "group_by": "project_id", "limit": 31}
        if page:
            p["page"] = page
        resp = requests.get(url, headers=oai, params=p, timeout=90)
        resp.raise_for_status()
        r = resp.json()
        for b in r.get("data", []):
            for res in b.get("results", []):
                pid = res.get("project_id") or "?"
                v = fnum(res.get("amount", {}).get("value")) if field == "amount" else (res.get(field) or 0)
                out[pid] = out.get(pid, 0) + v
        if r.get("has_more") and r.get("next_page"):
            page = r["next_page"]
        else:
            break
    return out


def elevenlabs(elh, metric, day, nxt):
    resp = store_mod._el_get("https://api.elevenlabs.io/v1/usage/character-stats", elh, {
        "start_unix": unix(day) * 1000, "end_unix": unix(nxt) * 1000, "breakdown_type": "none",
        "metric": metric, "aggregation_interval": "day", "include_workspace_metrics": "true"},
        timeout=90, label=f"elevenlabs character-stats {metric} {day}")
    resp.raise_for_status()
    r = resp.json()
    return sum(fnum(v) for s in (r.get("usage", {}) or {}).values() for v in s)


# ElevenLabs product buckets that are TTS. Those minutes are Aurie's speech INSIDE a
# LiveKit session, which `lk_min` already counts — adding them double-counts the session.
_EL_TTS_PRODUCTS = ("tts", "text_to_speech", "text to speech")


def elevenlabs_split(elh, metric, day, nxt):
    """(agent, tts) totals of `metric` for the day, split on ElevenLabs' product_type.

    agent is everything that is not TTS (ConvAI, plus any product ElevenLabs adds later,
    so the total never loses a bucket); agent + tts == the breakdown_type=none total."""
    resp = store_mod._el_get("https://api.elevenlabs.io/v1/usage/character-stats", elh, {
        "start_unix": unix(day) * 1000, "end_unix": unix(nxt) * 1000,
        "breakdown_type": "product_type", "metric": metric, "aggregation_interval": "day",
        "include_workspace_metrics": "true"},
        timeout=90, label=f"elevenlabs character-stats {metric} by product {day}")
    resp.raise_for_status()
    agent = tts = 0.0
    for product, series in ((resp.json().get("usage") or {}).items()):
        v = sum(fnum(x) for x in series)
        if product.strip().lower() in _EL_TTS_PRODUCTS:
            tts += v
        else:
            agent += v
    return agent, tts


def cohort_ratios(day, llm_json_tmpl, user_csv):
    """Day's (non-free, free) per-completion cost ratios vs blended, from per-user data.

    Returns (paying_ratio, free_ratio); either element is None when that cohort
    has no data. Returns (None, None) when the walk JSON or the store rows for
    the date are missing — callers leave the columns empty rather than estimate.
    """
    path = llm_json_tmpl.replace("{date}", day)
    if not (os.path.exists(path) and user_csv and os.path.exists(user_csv)):
        return None, None
    states = {}
    with open(user_csv) as f:
        for r in csv.DictReader(f):
            if r["date"] == day and r["user_id"] != "Other":
                states[r["user_id"]] = r["subscription_state"]
    if not states:
        return None, None
    bl_c = bl_m = nf_c = nf_m = fr_c = fr_m = 0.0
    for u in json.load(open(path)).get("users_all", []):
        amt, cmp_ = u.get("amount", 0.0), u.get("completion_count", 0)
        bl_c += amt; bl_m += cmp_
        if states.get(u["uid"], "free") != "free":
            nf_c += amt; nf_m += cmp_
        else:
            fr_c += amt; fr_m += cmp_
    if not (bl_m and bl_c):
        return None, None
    bl = bl_c / bl_m
    return ((nf_c / nf_m) / bl if nf_m else None,
            (fr_c / fr_m) / bl if fr_m else None)


def dau_count(day, user_csv):
    if not (user_csv and os.path.exists(user_csv)):
        return 0
    uids = set()
    with open(user_csv) as f:
        for r in csv.DictReader(f):
            if r["date"] == day and r["user_id"] != "Other":
                uids.add(r["user_id"])
    return len(uids)


def dau_counts_all(user_csv):
    """One pass over the per-user store → {date: {reached, active, engaged}} distinct
    user counts (Other row excluded). reached = any billed row (ambient/batch
    included); active = active==1 (opened the app); engaged = engaged==1 (sent a
    message). active/engaged are 0 for days whose rows predate the engagement
    columns; the KPI writer maps a 0 denominator to a blank (ambient/0 is undefined),
    so a pre-flag day gets blank per-active/engaged KPIs rather than a false value."""
    if not (user_csv and os.path.exists(user_csv)):
        return {}
    reached, active, engaged = defaultdict(set), defaultdict(set), defaultdict(set)
    with open(user_csv) as f:
        for r in csv.DictReader(f):
            if r["user_id"] == "Other":
                continue
            d, u = r["date"], r["user_id"]
            reached[d].add(u)
            if r.get("active") == "1":
                active[d].add(u)
            if r.get("engaged") == "1":
                engaged[d].add(u)
    return {d: {"reached": len(reached[d]), "active": len(active[d]), "engaged": len(engaged[d])}
            for d in reached}


# Per-user-store voice columns that mark each voice path. LiveKit (chained) path = any of its
# vendor columns is non-zero (LiveKit transport, Deepgram STT, Cartesia TTS, or ElevenLabs-as-TTS on
# LiveKit); ElevenLabs ConvAI agent path = the agent-bundle column. A user can appear on both in a
# day (counted in each — these are per-path adoption counts, not a mutually exclusive split).
_LIVEKIT_PATH_COLS = ("voice_livekit", "voice_deepgram", "voice_cartesia", "voice_fish", "voice_inworld",
                      "voice_11labs_livekit")
_ELEVENLABS_AGENT_COL = "voice_11labs_agent"


def voice_path_user_counts(day, user_csv):
    """Distinct users per voice path for `day` from the per-user store.

    Returns (livekit_voice_users, elevenlabs_agent_voice_users), or (None, None) when the per-user
    store is unavailable or has no rows for the day — the caller leaves the columns blank rather than
    record a misleading 0 (0 users and "no data" are different signals for a migration tracker).
    """
    if not (user_csv and os.path.exists(user_csv)):
        return None, None
    livekit, el_agent = set(), set()
    seen_day = False
    with open(user_csv) as f:
        for r in csv.DictReader(f):
            if r["date"] != day or r["user_id"] == "Other":
                continue
            seen_day = True
            if any(fnum(r.get(c)) > 0 for c in _LIVEKIT_PATH_COLS):
                livekit.add(r["user_id"])
            if fnum(r.get(_ELEVENLABS_AGENT_COL)) > 0:
                el_agent.add(r["user_id"])
    if not seen_day:
        return None, None
    return len(livekit), len(el_agent)


# The per-user store's LiveKit-path $ columns (voice_cost minus the ElevenLabs columns;
# deepgram_inference_drawdown is not in voice_cost). store.py books these against the
# month's prior minutes and LiveKit Inference billing; recomputing them here from the
# vendor helpers alone misses both (no overage accrual, no Inference STT).
_STORE_LK_COST_COLS = ("voice_cartesia", "voice_fish", "voice_inworld", "voice_deepgram", "voice_livekit",
                       "voice_livekit_platform")


def store_livekit_path(day, user_csv):
    """(cartesia_and_deepgram_usd, lk_cost_usd, livekit_minutes) for `day` summed over the
    per-user store, the `Other` row included. None when the store has no rows for the day."""
    if not (user_csv and os.path.exists(user_csv)):
        return None
    usage = cost = minutes = 0.0
    seen = False
    with open(user_csv) as f:
        for r in csv.DictReader(f):
            if r["date"] != day:
                continue
            seen = True
            cost += sum(fnum(r.get(c)) for c in _STORE_LK_COST_COLS)
            usage += fnum(r.get("voice_cartesia")) + fnum(r.get("voice_deepgram"))
            minutes += fnum(r.get("livekit_minutes"))
    return (usage, cost, minutes) if seen else None


def local_per_user_summary(days, user_csv):
    """{date: (rows, Σ voice_cost)} for these days in the local per-user CSV."""
    out = {}
    if not (user_csv and os.path.exists(user_csv)):
        return out
    with open(user_csv) as f:
        for r in csv.DictReader(f):
            if r["date"] in days:
                n, v = out.get(r["date"], (0, 0.0))
                out[r["date"]] = (n + 1, v + fnum(r.get("voice_cost")))
    return out


def stale_per_user_days(local, remote):
    """Days BigQuery holds whose local per-user rows differ from it: another row count, or
    Σ voice_cost more than a cent apart. A day BigQuery does not hold is not compared."""
    stale = []
    for d, (n, v) in sorted(remote.items()):
        ln, lv = local.get(d, (0, 0.0))
        if ln != n or abs(lv - v) > 0.01:
            stale.append(f"{d} (local {ln} rows ${lv:,.2f}, BigQuery {n} rows ${v:,.2f})")
    return stale


def build_row(day, projs, oai, elh, rates, lk_path=None, allow_unmeasured=False):
    nxt = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    cost = by_project(oai, "https://api.openai.com/v1/organization/costs", "amount", day, nxt)
    reqs = by_project(oai, "https://api.openai.com/v1/organization/usage/completions",
                      "num_model_requests", day, nxt)

    def is_prod(pid):
        return "production" in projs.get(pid, "").lower()

    def bucket(d, keys):
        return sum(v for pid, v in d.items()
                   if is_prod(pid) and any(k in projs.get(pid, "").lower() for k in keys))

    md = bucket(cost, MSG_DRIVEN)
    mem = bucket(cost, ("memory",))
    am = bucket(cost, AMBIENT)
    vis = sum(n for pid, n in reqs.items()
              if is_prod(pid) and "conversation" in projs.get(pid, "").lower())
    # ElevenLabs voice $ (every product: ConvAI agent + TTS) and ConvAI minutes only.
    # TTS minutes are speech inside LiveKit sessions already counted in lk_min, so TTS
    # $ rides the LiveKit-path minutes for the blended rate (see the guard below).
    el_agent_usd, el_tts_usd = (v / 100.0 for v in elevenlabs_split(elh, "fiat_units_spent", day, nxt))
    vcost_11 = el_agent_usd + el_tts_usd
    vmin_11, _tts_min = elevenlabs_split(elh, "minutes_used", day, nxt)

    # LiveKit-path $ and minutes: the per-user store's booked day (lk_path) when it has
    # one. Without it, recompute from the vendor helpers (DERIVED from ESTIMATED rates;
    # missing keys / auth degrade to $0 / 0 min) — an undercount the caller warns about.
    if lk_path is not None:
        usage_dollars, lk_cost, lk_min = lk_path
        lk_note = ""
    else:
        day_cartesia, ct_note = store_mod.cartesia_day_total(day, rates["cartesia"])
        day_deepgram, dg_note = store_mod.deepgram_day_total(day, rates["deepgram"])
        _lk_by_user, lk_min, day_livekit, lk_note, _lk_meta = store_mod.livekit_costs(day, rates["livekit"])
        # A failed vendor read returns $0; on a day with traffic that $0 would persist here
        # too. The LiveKit log gap keeps its own DATA GAP tagging below.
        # Deepgram's traffic is the DIRECT STT minutes only: Inference minutes never reach it.
        ct_min = (_lk_meta or {}).get("minutes_by_provider", {}).get("cartesia", 0.0)
        on_inf, _inf_min, direct_min, _ = store_mod.stt_transport_split(_lk_meta or {})
        store_mod.assert_vendor_reads(day, [
            ("cartesia", ct_note, None if lk_note else float(ct_min)),
            ("deepgram", dg_note, None if lk_note else float(direct_min if on_inf else lk_min)),
        ], allow_unmeasured=allow_unmeasured)
        day_fish, _ = store_mod.fish_day_total(_lk_meta, rates["fish"])
        day_inworld, _ = store_mod.inworld_day_total(day, _lk_meta, rates["inworld"])
        lk_cost = day_cartesia + day_fish + day_inworld + day_deepgram + day_livekit
        usage_dollars = day_cartesia + day_deepgram

    # Blended-rate denominator guard: the LiveKit-path $ (Cartesia/Deepgram/LiveKit)
    # only belong in the blended numerator when we actually have LiveKit minutes to
    # divide by — dividing that $ by ElevenLabs-only minutes would silently INFLATE
    # cost_per_voice_min. So they are excluded whenever minutes are 0, numerator and
    # denominator staying consistent (ElevenLabs-only over ElevenLabs-only minutes).
    #
    # Whether that exclusion is a DATA GAP is a separate question, and PROD-5998 split
    # the two. `$ > 0 with no minutes` used to imply a failed read, because every
    # LiveKit-path dollar was usage-derived. The subscription accrual is not: it is
    # owed on a day with no traffic at all, so a genuinely quiet day now produces
    # dollars-without-minutes legitimately and would be flagged as a missing-logs
    # failure by the old test.
    #
    # The two signals that still mean a failed read:
    #   - lk_note        — the forwarded-log query itself failed (auth / quota / error)
    #   - usage_dollars  — Cartesia or Deepgram billed for the day. Those come from the
    #                      vendors' own usage APIs, so a non-zero figure is proof there
    #                      WAS traffic; zero LiveKit minutes beside it is a contradiction.
    # ElevenLabs TTS $ has no minutes of its own here (its minutes are lk_min), so it is
    # LiveKit-path $ for this guard: with no LiveKit minutes it would divide by ConvAI
    # minutes alone and inflate the rate.
    lk_rate_cost = lk_cost + el_tts_usd
    lk_excluded_no_minutes = lk_rate_cost > 0 and lk_min <= 0
    lk_data_gap = bool(lk_note) or (usage_dollars > 0 and lk_min <= 0)
    # voice_cost / voice_minutes are REPORTED TOTALS and always carry the LiveKit path.
    # They answer "what did the day cost", and a real dollar must not vanish from that
    # because it has no denominator — the subscription accrual is owed on a silent day,
    # and dropping it would under-report the day while days WITH minutes kept it, leaving
    # one column on two models.
    vcost_total = vcost_11 + lk_cost
    vmin_total = vmin_11 + lk_min
    # The blended RATE is the only figure that needs the denominator guard, and it needs
    # numerator and denominator to agree: ConvAI $ over ConvAI minutes.
    if lk_excluded_no_minutes:
        rate_num, rate_den = el_agent_usd, vmin_11
    else:
        rate_num, rate_den = vcost_total, vmin_total
    cpm = md / vis if vis else 0.0
    return {"date": day, "openai_total": round(sum(cost.values()), 2),
            "msg_driven_cost": round(md, 2), "memory_cost": round(mem, 2),
            "ambient_cost": round(am, 2), "visible_messages": int(vis),
            "cost_per_msg": round(cpm, 4), "cost_per_msg_paying": "", "cost_per_msg_free": "",
            "voice_cost": round(vcost_total, 2), "voice_minutes": round(vmin_total, 1),
            "cost_per_voice_min": round(rate_num / rate_den, 4) if rate_den else 0.0,
            "cost_per_voice_min_11labs": round(el_agent_usd / vmin_11, 4) if vmin_11 else "",
            "cost_per_voice_min_livekit": round(lk_cost / lk_min, 4) if lk_min else "",
            "_lk_data_gap": lk_data_gap,
            "_lk_cost_excluded": round(lk_rate_cost, 2) if lk_excluded_no_minutes else 0.0}


def _migrate_unit_csv(path):
    """If `path` carries a prior unit-cost header, rewrite it in place with the current COLS.

    Handles both known prior schemas (OLD_COLS 12-col, PREV_COLS 14-col) → COLS. Any column present
    in COLS but not in the old header (the per-path rate columns and/or the per-day voice-path user
    counts) is backfilled BLANK for historical rows — those weren't computed then and are never
    faked. Mirrors store.py's _migrate_old_csv. Returns True if migrated (or already current), False
    if the file doesn't exist. Exits on an unrecognised header. Preserves the date set (idempotency)
    and every existing column value.
    """
    if not os.path.exists(path):
        return False
    with open(path, newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return False  # empty file — fresh header on write
        rows = list(csv.DictReader(f, fieldnames=header))
    if header == COLS:
        return True  # already current schema
    if header not in (OLD_COLS, PREV_COLS):
        sys.exit(f"unrecognised unit-cost CSV header in {path}: {header}\n"
                 f"expected {OLD_COLS}, {PREV_COLS}, or current {COLS}")
    # Map each row onto COLS; columns absent from the old header default to "" (never faked).
    migrated = [{c: r.get(c, "") for c in COLS} for r in rows]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, lineterminator=LF)
        w.writeheader()
        w.writerows(migrated)
    print(f"# migrated {len(migrated)} existing row(s) in {path} → current unit-cost "
          f"schema (columns new to this schema backfilled blank)", file=sys.stderr)
    return True


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Append daily Aurie unit costs to the unit-cost store")
    ap.add_argument("--date", help="day to record, YYYY-MM-DD (default: yesterday UTC)")
    ap.add_argument("--until", help="optional inclusive range end for backfill, YYYY-MM-DD")
    ap.add_argument("--csv", default=None,
                    help="unit-cost store path (default: $AURIE_UNIT_COST_CSV, else "
                         "$AURIE_COST_CACHE_DIR/daily_unit_costs.csv, else ~/.cache/aurie-cost/daily_unit_costs.csv)")
    ap.add_argument("--llm-json", default="/tmp/cpu_{date}.json",
                    help="per-user walk JSON path template, {date} placeholder (Step 2 output)")
    ap.add_argument("--user-csv", default=None,
                    help="per-user store for subscription states (default: $AURIE_COST_CSV or the cache dir)")
    ap.add_argument("--kpi-csv", default=None,
                    help="engineering-KPI store (default: $AURIE_KPI_CSV or the cache dir)")
    ap.add_argument("--force", action="store_true",
                    help="recompute and REPLACE the row(s) for --date (…--until) even when "
                         "already stored. Requires an explicit --date. Use to correct a day "
                         "recorded from bad inputs — the cohort columns only compute on write, "
                         "so a stored-but-wrong row can't otherwise be fixed. Sync the "
                         "corrected row with bq_sink.py --date <d> afterwards.")
    ap.add_argument("--allow-unmeasured-vendor", action="store_true",
                    help="when a day's LiveKit-path $ is recomputed from the vendor APIs, "
                         "persist it even if a Cartesia or Deepgram read failed on a day "
                         "with traffic. Off by default: that column would be stored as $0.")
    ap.add_argument("--cartesia-rate-per-million", type=float,
                    default=store_mod.DEFAULT_CARTESIA_RATE_PER_MILLION,
                    help="ESTIMATE — USD per 1M Cartesia credits (default "
                         f"{store_mod.DEFAULT_CARTESIA_RATE_PER_MILLION}, Scale overage, $299/8M plan).")
    ap.add_argument("--fish-rate-per-million", type=float, default=store_mod.DEFAULT_FISH_RATE_PER_MILLION,
                    help="ESTIMATE — USD per 1M Fish Audio UTF-8 bytes (s2-pro list); usage is the agent's tts_characters.")
    ap.add_argument("--inworld-rate-per-million", type=float, default=store_mod.DEFAULT_INWORLD_RATE_PER_MILLION,
                    help="USD per 1M Inworld TTS characters (TTS-2 On-Demand list); characters from Inworld's usage API.")
    ap.add_argument("--deepgram-rate-per-hour", type=float,
                    default=store_mod.DEFAULT_DEEPGRAM_RATE_PER_HOUR,
                    help="ESTIMATE — USD per Deepgram audio-hour (default "
                         f"{store_mod.DEFAULT_DEEPGRAM_RATE_PER_HOUR}, nova-3 streaming $0.0048/min).")
    ap.add_argument("--livekit-rate-per-min", type=float,
                    default=store_mod.DEFAULT_LIVEKIT_RATE_PER_MIN,
                    help="ESTIMATE — USD per LiveKit session-minute (default "
                         f"{store_mod.DEFAULT_LIVEKIT_RATE_PER_MIN}, LiveKit Cloud Scale).")
    a = ap.parse_args()

    # --force rewrites history, so it never rides the implicit default date: the
    # operator must name the day being corrected.
    if a.force and not a.date:
        ap.error("--force requires an explicit --date (refusing to force the default "
                 "yesterday-UTC run)")

    # BigQuery is the source of truth; the CSVs are a local cache (default under
    # ~/.cache/aurie-cost). --flag > $AURIE_*_CSV > cache dir. The KPI store is
    # always maintained (derived + rewritten fresh each run); user-csv is
    # read-only so it isn't force-created.
    a.csv = store_paths.resolve("unit_costs", a.csv, ensure=True)
    a.user_csv = store_paths.resolve("per_user", a.user_csv)
    a.kpi_csv = store_paths.resolve("eng_kpis", a.kpi_csv, ensure=True)

    oai = {"Authorization": f"Bearer {os.environ['OPENAI_ADMIN_API_KEY']}"}
    elh = {"xi-api-key": os.environ["ELEVENLABS_ADMIN_API_KEY"]}
    rates = {"cartesia": a.cartesia_rate_per_million, "fish": a.fish_rate_per_million,
             "inworld": a.inworld_rate_per_million,
             "deepgram": a.deepgram_rate_per_hour, "livekit": a.livekit_rate_per_min}

    start = a.date or (datetime.datetime.now(datetime.timezone.utc).date()
                       - datetime.timedelta(days=1)).isoformat()
    end = a.until or start
    d0, d1 = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    days = [(d0 + datetime.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    # Dates the operator is explicitly rewriting. They're recomputed even when
    # already stored, and are exempt from the CSV-behind-BigQuery guard below.
    forced = set(days) if a.force else set()

    # Idempotency arbiter is BigQuery (the source of truth). Hard-require it.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from bq import query, table_exists, BQUnavailable, BQError, PROJECT as BQ_PROJECT, DATASET as BQ_DATASET
    from bq_sink import STORES
    bq_project = os.environ.get("AURIE_BQ_PROJECT", BQ_PROJECT)
    bq_dataset = os.environ.get("AURIE_BQ_DATASET", BQ_DATASET)
    unit_table = f"{bq_project}.{bq_dataset}.{STORES['unit_costs']['table']}"
    per_user_table = f"{bq_project}.{bq_dataset}.{STORES['per_user']['table']}"

    def _bq_dates(table):
        if not table_exists(table, bq_project):
            return set()
        return {(r["d"].isoformat() if hasattr(r["d"], "isoformat") else str(r["d"]))
                for r in query(f"SELECT DISTINCT date AS d FROM `{table}`", bq_project)}

    def _csv_dates(path):
        if not path or not os.path.exists(path):
            return set()
        with open(path) as f:
            return {r["date"] for r in csv.DictReader(f)}

    # Migrate an old-schema local CSV to the current columns (the CSV is the local copy).
    _migrate_unit_csv(a.csv)
    try:
        bq_all = _bq_dates(unit_table)
        csv_have = _csv_dates(a.csv)
        # The KPI rewrite (only when --kpi-csv is set) reads the FULL local unit +
        # per-user CSVs, so neither may be behind BigQuery (the source of truth) or
        # it silently drops BQ-only days / uses stale cohort inputs. Refuse and
        # point to bq_pull rather than write an incomplete/wrong KPI store.
        if a.kpi_csv:
            # A forced date is deliberately absent from (or about to be overwritten
            # in) the local copy, so it isn't the "local copy is stale" case this
            # guard protects against — exempt it, keep the guard for every other day.
            behind_unit = sorted(bq_all - csv_have - forced)
            behind_pu = sorted(_bq_dates(per_user_table) - _csv_dates(a.user_csv))
            if behind_unit or behind_pu:
                parts = []
                if behind_unit:
                    parts.append(f"unit CSV by {len(behind_unit)} day(s)")
                if behind_pu:
                    parts.append(f"per-user CSV by {len(behind_pu)} day(s)")
                sys.exit(f"local copy is behind BigQuery ({'; '.join(parts)}) — "
                         "run bq_pull.py to refresh, then re-run.")
    except BQUnavailable as e:
        sys.exit(f"BigQuery is the source of truth but is unavailable: {e} — "
                 "fix GCP_KEY / install google-cloud-bigquery and re-run.")
    except BQError as e:
        sys.exit(f"BigQuery error during the idempotency check: {e}")
    bq_have = {d for d in bq_all if start <= d <= end}
    # Days present locally but not in BigQuery need a sync-up, not a recompute —
    # surfaced as a non-zero fail-fast at the END of main (after the KPI rewrite).
    stale_local = sorted((set(days) & csv_have) - bq_have)
    # Recompute+append only genuinely-new days: absent from BQ (authoritative) AND
    # from the local CSV (so the append can't duplicate a local row).
    todo = [d for d in days if d in forced or (d not in bq_have and d not in csv_have)]
    # The KPI store is fully DERIVED from the unit-cost + per-user stores, so it's
    # rewritten from scratch each run (below) — self-healing: it adopts the current
    # schema and fills active/engaged for every date without an append/migrate dance.
    # So a run always has KPI work even when the unit-cost store is complete; the
    # stale-local (local ahead of BigQuery) case is warned about at the END of main,
    # after the KPI rewrite (a warning, not a non-zero exit — see there for why).
    if not todo:
        print(f"{start}..{end}: no new unit-cost days — refreshing the derived KPI store only.")
    else:
        # LiveKit-path $, minutes and cohorts come from the LOCAL per-user CSV. A step that
        # rewrote the per-user store in BigQuery only (rebook_livekit.py --confirm) leaves it
        # stale, and a unit-cost row computed from it would then be published (ENG-1864).
        try:
            remote = {(r["d"].isoformat() if hasattr(r["d"], "isoformat") else str(r["d"])):
                      (int(r["n"]), float(r["v"] or 0.0))
                      for r in query(f"SELECT date AS d, COUNT(*) AS n, SUM(voice_cost) AS v "
                                     f"FROM `{per_user_table}` WHERE date BETWEEN DATE(@s) AND DATE(@e) "
                                     f"GROUP BY date", bq_project,
                                     params={"s": min(todo), "e": max(todo)})
                      if (r["d"].isoformat() if hasattr(r["d"], "isoformat") else str(r["d"])) in todo}
        except (BQUnavailable, BQError) as e:
            sys.exit(f"could not compare the local per-user CSV with BigQuery ({e}) — "
                     "refusing to compute unit costs from a copy that may be stale.")
        stale = stale_per_user_days(local_per_user_summary(set(todo), a.user_csv), remote)
        if stale:
            # Either side can be the newer one, and the wrong fix overwrites it, so the
            # message names both and never picks for the operator.
            sys.exit(f"REFUSING: the local per-user CSV differs from BigQuery for {len(stale)} day(s): "
                     f"{'; '.join(stale[:5])}{' …' if len(stale) > 5 else ''}. Make the two agree "
                     f"first, keeping whichever side is newer:\n"
                     f"  BigQuery is newer (rebook_livekit.py --confirm rewrote it): "
                     f"bq_pull.py --from {min(todo)} --to {max(todo)}\n"
                     f"  the local CSV is newer (store.py --force corrected it): "
                     f"bq_sink.py --date <d> for each day\n"
                     f"Then re-run. Pulling over an unsynced store.py --force loses that correction.")

    projs = project_names(oai) if todo else {}
    # A forced day whose walk JSON is gone keeps its stored cohort columns rather than
    # blanking them: they derive from OpenAI $ and the walk, which a re-store doesn't change.
    prior_rows = {}
    if forced and os.path.exists(a.csv):
        with open(a.csv) as f:
            prior_rows = {r["date"]: r for r in csv.DictReader(f) if r["date"] in forced}
    rows = []
    for d in todo:
        lk_path = store_livekit_path(d, a.user_csv)
        if lk_path is None:
            print(f"WARNING: {d} has no per-user rows — LiveKit-path $ recomputed from the vendor "
                  "APIs, which omits the month-to-date overage and LiveKit Inference STT. Run "
                  "store.py for the day first, then unit_costs.py --force.", file=sys.stderr)
        r = build_row(d, projs, oai, elh, rates, lk_path,
                      allow_unmeasured=a.allow_unmeasured_vendor)
        if r["openai_total"] <= 0 or r["visible_messages"] <= 0 or r["voice_minutes"] <= 0:
            sys.exit(f"refusing to append {d}: implausible zeros (openai_total={r['openai_total']}, "
                     f"visible_messages={r['visible_messages']}, voice_minutes={r['voice_minutes']}) — "
                     "likely a partial day or an API failure; nothing written.")
        pr_, fr_ = cohort_ratios(d, a.llm_json, a.user_csv)
        if pr_:
            r["cost_per_msg_paying"] = round(r["cost_per_msg"] * pr_, 4)
        if fr_:
            r["cost_per_msg_free"] = round(r["cost_per_msg"] * fr_, 4)
        if not (pr_ or fr_) and d in prior_rows:
            for c in ("cost_per_msg_paying", "cost_per_msg_free"):
                r[c] = prior_rows[d].get(c, "")
        # Per-day voice-path user counts (LiveKit vs ElevenLabs-agent) from the per-user store —
        # tracks the ElevenLabs→LiveKit migration by adoption, not just $. Blank when no per-user
        # data for the day (distinct from a real 0 — see voice_path_user_counts).
        lk_users, el_users = voice_path_user_counts(d, a.user_csv)
        r["livekit_voice_users"] = lk_users if lk_users is not None else ""
        r["elevenlabs_agent_voice_users"] = el_users if el_users is not None else ""
        if pr_ or fr_:
            paying = (f"paying ${r['cost_per_msg_paying'] or 0:.4f} / free ${r['cost_per_msg_free'] or 0:.4f} "
                      f"(ratios {pr_ or 0:.2f}/{fr_ or 0:.2f})")
        elif r["cost_per_msg_paying"] != "" or r["cost_per_msg_free"] != "":
            paying = (f"paying ${r['cost_per_msg_paying'] or 0} / free ${r['cost_per_msg_free'] or 0} "
                      "(kept from the stored row; no walk JSON)")
        else:
            paying = "paying/free — (no per-user data for this date; run Steps 2-3 first or leave blank)"
        # Strip the transient blended-rate-guard diagnostics (not CSV columns);
        # surface the excluded LiveKit-path $ so an inflated rate is never silent.
        lk_gap = r.pop("_lk_data_gap", False)
        lk_excluded = r.pop("_lk_cost_excluded", 0.0)
        rows.append(r)
        v11 = r["cost_per_voice_min_11labs"]
        vlk = r["cost_per_voice_min_livekit"]
        vsplit = (f"11labs ${v11 or '—'}/min, livekit ${vlk or '—'}/min [est]")
        # Two different messages: a read that failed is a WARNING, a quiet day is not.
        # Printing "data gap" on a day that simply had no voice traffic trains the reader
        # to ignore the one line that means the logs are actually unreadable.
        # The gap warning is driven by lk_gap, NOT by whether anything was excluded.
        # livekit_costs returns $0 on LiveKitUnavailable, so a log failure with no
        # Cartesia/Deepgram charges excludes nothing — gating the message on the excluded
        # amount meant a successful quiet day printed an explanation while a genuinely
        # unread day printed nothing at all. Exactly backwards.
        if lk_gap:
            amount = (f"LiveKit-path ${lk_excluded:.2f} EXCLUDED from blended rate; "
                      if lk_excluded else "")
            gap = f" | {amount}LiveKit minutes==0 DATA GAP — logs unreadable"
        elif lk_excluded:
            gap = (f" | LiveKit-path ${lk_excluded:.2f} excluded from the blended RATE "
                   "(no LiveKit minutes to divide by; still counted in voice_cost)")
        else:
            gap = ""
        users_bit = (f" | voice users: livekit {r['livekit_voice_users']}, "
                     f"11labs-agent {r['elevenlabs_agent_voice_users']}"
                     if r["livekit_voice_users"] != "" or r["elevenlabs_agent_voice_users"] != ""
                     else "")
        print(f"{d}: ${r['cost_per_msg']:.4f}/msg ({paying}) | "
              f"${r['cost_per_voice_min']:.4f}/voice-min blended ({vsplit}) | "
              f"memory ${r['memory_cost']:.2f}{gap}{users_bit}")
    if rows:
        replaced = sorted({r["date"] for r in rows} & csv_have)
        if replaced:
            # Forced dates already have a row: rewrite the file (drop the old rows,
            # keep every other date) instead of appending a duplicate. Written to a
            # temp file and moved into place so an interrupted run can't truncate the
            # store.
            keep = []
            with open(a.csv) as f:
                keep = [r for r in csv.DictReader(f) if r["date"] not in set(replaced)]
            merged = sorted(keep + [{c: r.get(c, "") for c in COLS} for r in rows],
                            key=lambda r: r["date"])
            tmp = f"{a.csv}.tmp"
            with open(tmp, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore",
                                lineterminator=LF)
                w.writeheader()
                w.writerows(merged)
            os.replace(tmp, a.csv)
            print(f"replaced {len(replaced)} day(s) ({', '.join(replaced)}) "
                  f"and wrote {len(merged)} row(s) -> {a.csv}")
        else:
            new = not os.path.exists(a.csv)
            with open(a.csv, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=COLS, lineterminator=LF)
                if new:
                    w.writeheader()
                w.writerows(sorted(rows, key=lambda r: r["date"]))
            print(f"appended {len(rows)} day(s) -> {a.csv}")
        if forced:
            print(f"NOTE: {len(forced & {r['date'] for r in rows})} forced day(s) rewritten "
                  f"locally — run bq_sink.py --date <d> to replace the BigQuery partition.",
                  file=sys.stderr)

    if a.kpi_csv:
        # Full REWRITE of the derived KPI store (not append): recompute every date
        # the unit-cost store can derive, so new columns fill and the schema self-
        # heals in one pass — no migrate-then-blank gap. Offline (reads CSVs only).
        unit = {r["date"]: r for r in rows}
        if os.path.exists(a.csv):
            with open(a.csv) as f:
                for r in csv.DictReader(f):
                    unit.setdefault(r["date"], r)
        counts = dau_counts_all(a.user_csv)
        prior = {}
        if os.path.exists(a.kpi_csv):
            with open(a.kpi_csv) as f:
                prior = {r["date"]: r for r in csv.DictReader(f)}
        kpi_rows = []
        for d in sorted(set(unit) | set(prior)):
            r = unit.get(d)
            if not r:  # a KPI date with no unit-cost row (shouldn't happen) — keep as-is
                kpi_rows.append({c: prior[d].get(c, "") for c in KPI_COLS})
                continue
            c = counts.get(d, {"reached": 0, "active": 0, "engaged": 0})
            amb_val = float(r.get("ambient_cost") or 0)

            def _per(n, _v=amb_val):  # ambient $ per user-of-that-denominator / 30d; 0 -> blank
                return round(_v / n * 30, 2) if n else ""
            kpi_rows.append({"date": d,
                             "llm_cost_per_paying_msg": r.get("cost_per_msg_paying", ""),
                             "voice_cost_per_min": r.get("cost_per_voice_min", ""),
                             "ambient_per_dau_month": _per(c["reached"]),          # original KR
                             "ambient_per_active_user_month": _per(c["active"]),   # per active user
                             "ambient_per_engaged_user_month": _per(c["engaged"])})  # per engaged
        with open(a.kpi_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=KPI_COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(kpi_rows)
        for d in days:  # print just this run's window
            k = next((x for x in kpi_rows if x["date"] == d), None)
            if k:
                print(f"{d} KPIs: ${k['llm_cost_per_paying_msg'] or '—'}/paying-msg | "
                      f"${k['voice_cost_per_min'] or '—'}/voice-min | ambient "
                      f"${k['ambient_per_dau_month'] or '—'}/reached · "
                      f"${k['ambient_per_active_user_month'] or '—'}/active · "
                      f"${k['ambient_per_engaged_user_month'] or '—'}/engaged (per mo)")
        print(f"wrote {len(kpi_rows)} KPI row(s) -> {a.kpi_csv}")

    # Warn (after the KPI self-heal) if the local unit-cost copy is AHEAD of
    # BigQuery for in-window days — those need a sync-up, not a recompute. The fix
    # is Step 6 (bq_sink.py), the documented next step, which syncs them up. This
    # is a loud stderr warning, NOT a non-zero exit: a hard fail here would make any
    # wrapper that gates Step 6 on Step 5's exit code skip the very sync that
    # resolves it (and would also fail a run that just appended new days fine).
    if stale_local:
        print(f"WARNING: {len(stale_local)} unit-cost day(s) are in the local copy but not "
              f"BigQuery ({', '.join(stale_local)}) — run bq_sink.py to sync them to the "
              "source of truth.", file=sys.stderr)


if __name__ == "__main__":
    main()
