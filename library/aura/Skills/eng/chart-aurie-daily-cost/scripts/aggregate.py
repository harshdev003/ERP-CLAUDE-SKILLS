#!/usr/bin/env python3
"""Aggregate the per-user Aurie daily cost store into per-day rows + a window summary.

Reads from BigQuery (`aura-main.aurie_cost`, the source of truth maintained by
eng:store-aurie-user-daily-cost) and applies a date window, emitting JSON the
chart renderer consumes. Aggregate-only: counts/sums per day, never emits a
Firebase UID.

Voice is MULTI-VENDOR (PROD-3180), and ElevenLabs is split into its two roles:
the ConvAI agent bundle and the LiveKit-path TTS provider. The current store
schema splits voice into per-vendor columns: voice_11labs_agent,
voice_11labs_intelligence, voice_11labs_livekit, voice_cartesia, voice_fish,
voice_inworld, voice_deepgram, voice_livekit (+ voice_livekit_platform), plus the
kept voice_cost total (= their sum). This aggregator additively surfaces
the ElevenLabs-vs-LiveKit-path split per day so the chart can show the voice
migration over time. It stays backward-compatible with older schemas:
  - ORIGINAL 6-col (date, user_id, llm_cost, voice_cost, total_cost,
    subscription_state): a missing per-vendor column is read as
    voice_11labs_agent = voice_cost, other vendors 0 (pre-migration was 100%
    ElevenLabs ConvAI).
  - INTERMEDIATE 10-col (single voice_11labs): voice_11labs_agent = voice_11labs,
    voice_11labs_livekit = 0, LiveKit-path columns carried over.
For charting, voice_11labs = voice_11labs_agent + voice_11labs_livekit (the total
ElevenLabs spend across both roles).

Per-vendor voice $ derived from the LiveKit-path vendors (Cartesia / Fish /
Inworld / Deepgram / LiveKit) uses PRICING-SHEET ESTIMATE rates upstream in the store — see
eng:store-aurie-user-daily-cost. ElevenLabs voice $ is the authoritative billed
figure.

Also reads the KPI store maintained by eng:store-aurie-user-daily-cost
(columns: date, llm_cost_per_paying_msg, voice_cost_per_min, ambient_per_dau_month)
and joins it per-day so the renderer can draw KPI trend charts against their targets.

Reads BigQuery via bq.py (GCP_KEY service account) + google-cloud-bigquery.
"""

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for `from bq import ...`

# The single reconciling catch-all row store-aurie-user-daily-cost appends per day.
OTHER_USER = "Other"
STATES = ("paid", "trial", "free", "grace")

# BigQuery is the source of truth (the local CSVs are just a rebuildable copy).
BQ_PROJECT = os.environ.get("AURIE_BQ_PROJECT", "aura-main")
BQ_DATASET = os.environ.get("AURIE_BQ_DATASET", "aurie_cost")
PER_USER_TABLE = f"{BQ_PROJECT}.{BQ_DATASET}.aurie_per_user_daily_cost"
KPI_TABLE = f"{BQ_PROJECT}.{BQ_DATASET}.aurie_daily_eng_kpis"

KPI_TARGETS = {
    "cost_per_msg_paying":   0.018,
    "cost_per_voice_min":    0.07,
    "ambient_per_dau_month": 0.10,
}


def _fetch_rows(table_id, project=BQ_PROJECT):
    """Read all rows of a cost table from BigQuery, normalized to CSV-style
    strings (date -> YYYY-MM-DD, NULL -> "", ints/floats -> str) so the existing
    row-parsing below works unchanged."""
    from bq import query
    out = []
    for r in query(f"SELECT * FROM `{table_id}` ORDER BY date", project):
        out.append({k: ("" if v is None
                        else v.isoformat()[:10] if hasattr(v, "isoformat")
                        else str(v))
                    for k, v in r.items()})
    return out


def load(rows):
    """Return {date: {llm, voice, voice_11labs, voice_11labs_agent,
    voice_11labs_livekit, voice_cartesia, voice_fish, voice_inworld,
    voice_deepgram, voice_livekit, total,
    users_attributed, paid, trial, free, grace}}.

    `voice_11labs` is the total ElevenLabs vendor spend (agent + LiveKit-TTS
    roles). Backward-compatible with older schemas:
      - current 11-col (voice_11labs_agent + voice_11labs_livekit): both read,
        voice_11labs = their sum.
      - intermediate 10-col (single voice_11labs): read as the agent role,
        voice_11labs_livekit = 0.
      - original 6-col (no per-vendor columns): whole voice_cost → agent role.
    """
    days = defaultdict(lambda: {
        "llm": 0.0, "voice": 0.0,
        "voice_11labs": 0.0, "voice_11labs_agent": 0.0,
        "voice_11labs_intelligence": 0.0, "voice_11labs_livekit": 0.0,
        "voice_cartesia": 0.0, "voice_fish": 0.0, "voice_inworld": 0.0,
        "voice_deepgram": 0.0, "voice_livekit": 0.0,
        "total": 0.0, "users_attributed": 0, "paid": 0, "trial": 0, "free": 0, "grace": 0,
        # Per-day distinct users on each voice path (one per-user row per day → a simple count is
        # already distinct). Tracks the ElevenLabs→LiveKit migration by adoption, not just $.
        "livekit_voice_users": 0, "elevenlabs_agent_voice_users": 0,
        # Engagement DAU (from the per-user store's engaged/active flags): active = opened the
        # app (any mobile AI event), engaged = sent a message. reached = users_attributed (billed,
        # incl. ambient/batch — NOT DAU). None when the store predates the engagement columns.
        "active": 0, "engaged": 0,
    })
    rows = list(rows)
    if not rows:
        return days, set()  # empty table → let the caller's "no rows" check give the right pointer
    fields = set(rows[0].keys())
    required = {"date", "user_id", "llm_cost", "voice_cost", "total_cost", "subscription_state"}
    missing = required - fields
    if missing:
        raise SystemExit(f"error: cost table missing columns: {', '.join(sorted(missing))}")
    has_split_cols = "voice_11labs_agent" in fields  # current schema
    has_single_11labs = "voice_11labs" in fields      # intermediate schema
    has_lk_vendor_cols = "voice_cartesia" in fields
    has_engagement = "active" in fields and "engaged" in fields  # engagement columns present
    eng_days = set()  # dates that carried a non-blank engagement flag (real 0 vs "no data")
    for row in rows:
        d = days[row["date"]]
        d["llm"] += float(row["llm_cost"] or 0)
        voice_total = float(row["voice_cost"] or 0)
        d["voice"] += voice_total
        if has_split_cols:
            agent = float(row.get("voice_11labs_agent") or 0)
            lk_tts = float(row.get("voice_11labs_livekit") or 0)
            # ENG-982: Intelligence Service raw TTS — a THIRD ElevenLabs caller,
            # absent from pre-2026-07-28 rows (reads 0 there).
            is_tts = float(row.get("voice_11labs_intelligence") or 0)
        elif has_single_11labs:
            agent = float(row.get("voice_11labs") or 0)
            lk_tts = 0.0
            is_tts = 0.0
        else:
            # Original schema: all voice was ElevenLabs ConvAI agent.
            agent = voice_total
            lk_tts = 0.0
            is_tts = 0.0
        d["voice_11labs_agent"] += agent
        d["voice_11labs_intelligence"] += is_tts
        d["voice_11labs_livekit"] += lk_tts
        # Total ElevenLabs vendor spend across ALL THREE caller roles.
        d["voice_11labs"] += agent + is_tts + lk_tts
        cartesia = fish = inworld = deepgram = livekit = 0.0
        if has_lk_vendor_cols:
            cartesia = float(row.get("voice_cartesia") or 0)
            # Fish / Inworld TTS joined the LiveKit path later; rows that predate
            # the columns don't carry the key and read 0.
            fish = float(row.get("voice_fish") or 0)
            inworld = float(row.get("voice_inworld") or 0)
            deepgram = float(row.get("voice_deepgram") or 0)
            # PROD-5998: overage + the subscription accrual. voice_livekit alone is
            # overage-only and reads $0 inside the allotment.
            livekit = float(row.get("voice_livekit") or 0) + float(row.get("voice_livekit_platform") or 0)
            d["voice_cartesia"] += cartesia
            d["voice_fish"] += fish
            d["voice_inworld"] += inworld
            d["voice_deepgram"] += deepgram
            d["voice_livekit"] += livekit
        d["total"] += float(row["total_cost"] or 0)
        if row["user_id"] != OTHER_USER:
            d["users_attributed"] += 1
            # LiveKit (chained) path = any LiveKit-path vendor > 0 (transport / Deepgram /
            # Cartesia / Fish / Inworld / ElevenLabs-as-TTS-on-LiveKit); ElevenLabs agent path = the ConvAI
            # bundle column. A user can count on both in a day (per-path adoption, not a split).
            if lk_tts > 0 or cartesia > 0 or fish > 0 or inworld > 0 or deepgram > 0 or livekit > 0:
                d["livekit_voice_users"] += 1
            if agent > 0:
                d["elevenlabs_agent_voice_users"] += 1
            if has_engagement:
                if (row.get("active") or "") != "" or (row.get("engaged") or "") != "":
                    eng_days.add(row["date"])
                if row.get("active") == "1":
                    d["active"] += 1
                if row.get("engaged") == "1":
                    d["engaged"] += 1
        state = (row["subscription_state"] or "").strip()
        if state in STATES:
            d[state] += 1
    return days, eng_days


# Non-ElevenLabs LiveKit-path vendors. ElevenLabs-as-LiveKit-TTS stays inside
# voice_11labs so the chart's two voice series never double-count it.
LIVEKIT_PATH_VENDORS = ("voice_cartesia", "voice_fish", "voice_inworld",
                        "voice_deepgram", "voice_livekit")


# Per-day rounding of up to seven vendor sums; anything above this is a missing column.
VOICE_GAP_TOLERANCE = 0.05


def livekit_path(d):
    return sum(d[k] for k in LIVEKIT_PATH_VENDORS)


def load_kpis(rows):
    """Return {date: {cost_per_msg_paying, cost_per_voice_min, ambient_per_dau_month}}.

    Returns {} if the KPI table has no rows — KPI fields will be null in output.
    """
    kpis = {}
    for row in rows:
        kpis[row["date"]] = {
            "cost_per_msg_paying":   round(float(row.get("llm_cost_per_paying_msg") or 0), 4),
            "cost_per_voice_min":    round(float(row.get("voice_cost_per_min") or 0), 4),
            "ambient_per_dau_month": round(float(row.get("ambient_per_dau_month") or 0), 4),
        }
    return kpis


def select_window(all_dates, args):
    """Return the sorted subset of dates to keep, per the window flags."""
    ordered = sorted(all_dates)
    if args.all:
        return ordered
    if args.date_from or args.date_to:
        lo = args.date_from or ordered[0]
        hi = args.date_to or ordered[-1]
        return [d for d in ordered if lo <= d <= hi]
    # default + --last N: most recent N days present in the store
    n = args.last if args.last is not None else 30
    return ordered[-n:] if n > 0 else ordered


def _skill_version():
    """This skill's metadata.version (reads the sibling SKILL.md) for output provenance."""
    import re
    try:
        _t = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "SKILL.md"),
                  encoding="utf-8").read()
    except OSError:
        return None
    _fm = re.match(r"^---\n(.*?)\n---", _t, re.DOTALL)
    _fm = _fm.group(1) if _fm else _t
    _b = re.search(r"(?m)^metadata:[ \t]*\n((?:[ \t]+\S.*\n?)+)", _fm)
    if not _b:
        return None
    _v = re.search(r'''(?m)^[ \t]+version:[ \t]*["\']?([^"\'\s]+)["\']?[ \t]*$''', _b.group(1))
    return _v.group(1) if _v else None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(description="Aggregate the Aurie per-user daily cost store by day.")
    p.add_argument("--project", default=BQ_PROJECT, help="BigQuery project (default $AURIE_BQ_PROJECT or aura-main)")
    p.add_argument("--dataset", default=BQ_DATASET, help="BigQuery dataset (default $AURIE_BQ_DATASET or aurie_cost)")
    p.add_argument("--last", type=int, default=None, help="most recent N days present (default 30)")
    p.add_argument("--from", dest="date_from", help="window start, inclusive (YYYY-MM-DD)")
    p.add_argument("--to", dest="date_to", help="window end, inclusive (YYYY-MM-DD)")
    p.add_argument("--all", action="store_true", help="every day in the store")
    p.add_argument("--no-paid-line", action="store_true", help="render hint: omit the paid-user line")
    args = p.parse_args()

    # BigQuery is the source of truth (per PROD-3446 follow-on); read from it.
    per_user_table = f"{args.project}.{args.dataset}.aurie_per_user_daily_cost"
    kpi_table = f"{args.project}.{args.dataset}.aurie_daily_eng_kpis"
    from bq import BQUnavailable, BQError
    # Per-user store is essential — a failure here aborts.
    try:
        days, eng_days = load(_fetch_rows(per_user_table, args.project))
    except BQUnavailable as e:
        raise SystemExit(f"error: BigQuery unavailable: {e} — BigQuery is the source of truth for the "
                         "cost data (needs GCP_KEY + google-cloud-bigquery).")
    except BQError as e:
        raise SystemExit(f"error: BigQuery: {e}")
    if not days:
        raise SystemExit(f"error: no rows in {per_user_table} — "
                         "populate it with eng:store-aurie-user-daily-cost.")
    # KPI store is OPTIONAL — degrade to no KPI panels if its table is missing/
    # unreadable/empty (mirrors the old missing-KPI-CSV → {} behavior), rather
    # than aborting the whole chart when per-user data is fine.
    try:
        kpis = load_kpis(_fetch_rows(kpi_table, args.project))
    except BQError:
        kpis = {}

    keep = select_window(set(days), args)
    if not keep:
        raise SystemExit("error: window selected no days; widen --from/--to or use --all")

    out_days = []
    for date in keep:
        d = days[date]
        kpi = kpis.get(date)
        # LiveKit-path voice = the non-ElevenLabs LiveKit-path vendors combined (the
        # chart draws ElevenLabs vs LiveKit-path voice over time to show the migration).
        voice_livekit_path = round(livekit_path(d), 2)
        out_days.append({
            "date": date,
            "llm": round(d["llm"], 2),
            "voice": round(d["voice"], 2),
            "voice_11labs": round(d["voice_11labs"], 2),
            "voice_11labs_agent": round(d["voice_11labs_agent"], 2),
            "voice_11labs_intelligence": round(d["voice_11labs_intelligence"], 2),
            "voice_11labs_livekit": round(d["voice_11labs_livekit"], 2),
            "voice_cartesia": round(d["voice_cartesia"], 2),
            "voice_fish": round(d["voice_fish"], 2),
            "voice_inworld": round(d["voice_inworld"], 2),
            "voice_deepgram": round(d["voice_deepgram"], 2),
            "voice_livekit": round(d["voice_livekit"], 2),
            "voice_livekit_path": voice_livekit_path,
            "total": round(d["total"], 2),
            "users_attributed": d["users_attributed"],
            # Engagement DAU: active = opened the app, engaged = conversed. null (not 0) for
            # days whose per-user rows predate the engagement columns. users_attributed = reached.
            "active": (d["active"] if date in eng_days else None),
            "engaged": (d["engaged"] if date in eng_days else None),
            "livekit_voice_users": d["livekit_voice_users"],
            "elevenlabs_agent_voice_users": d["elevenlabs_agent_voice_users"],
            "paid": d["paid"],
            "trial": d["trial"],
            "free": d["free"],
            "grace": d["grace"],
            "cost_per_msg_paying":   kpi["cost_per_msg_paying"]   if kpi else None,
            "cost_per_voice_min":    kpi["cost_per_voice_min"]    if kpi else None,
            "ambient_per_dau_month": kpi["ambient_per_dau_month"] if kpi else None,
        })

    total = sum(d["total"] for d in out_days)
    llm_total = sum(d["llm"] for d in out_days)
    voice_total = sum(d["voice"] for d in out_days)
    voice_11labs_total = sum(d["voice_11labs"] for d in out_days)
    voice_11labs_agent_total = sum(d["voice_11labs_agent"] for d in out_days)
    voice_11labs_intelligence_total = sum(d["voice_11labs_intelligence"] for d in out_days)
    voice_11labs_livekit_total = sum(d["voice_11labs_livekit"] for d in out_days)
    voice_livekit_path_total = sum(d["voice_livekit_path"] for d in out_days)
    # voice comes from voice_cost; the two series the chart draws must still add up
    # to it, or a vendor column this reader doesn't know about is silently dropped.
    for d in out_days:
        gap = d["voice"] - d["voice_11labs"] - d["voice_livekit_path"]
        if abs(gap) > VOICE_GAP_TOLERANCE:
            print(f"WARNING: {d['date']} voice ${d['voice']:.2f} != ElevenLabs + LiveKit-path "
                  f"${d['voice_11labs'] + d['voice_livekit_path']:.2f} (gap ${gap:.2f}) — "
                  "a voice vendor column is not read by aggregate.py", file=sys.stderr)
    paid_user_days = sum(d["paid"] for d in out_days)
    n_days = len(out_days)
    latest = out_days[-1]
    peak = max(out_days, key=lambda d: d["total"])

    summary = {
        "days": n_days,
        "from": out_days[0]["date"],
        "to": latest["date"],
        "total": round(total, 2),
        "avg": round(total / n_days, 2),
        "llm_total": round(llm_total, 2),
        "voice_total": round(voice_total, 2),
        # ElevenLabs vs LiveKit-path voice split across the window. LiveKit-path
        # $ uses ESTIMATE vendor rates (see eng:store-aurie-user-daily-cost).
        "voice_11labs_total": round(voice_11labs_total, 2),
        # ElevenLabs split by role: ConvAI agent bundle vs LiveKit-path TTS.
        "voice_11labs_agent_total": round(voice_11labs_agent_total, 2),
        "voice_11labs_intelligence_total": round(voice_11labs_intelligence_total, 2),
        "voice_11labs_livekit_total": round(voice_11labs_livekit_total, 2),
        "voice_livekit_path_total": round(voice_livekit_path_total, 2),
        "voice_inworld_total": round(sum(d["voice_inworld"] for d in out_days), 2),
        "voice_fish_total": round(sum(d["voice_fish"] for d in out_days), 2),
        # Blended $/paid-active-user-day across the window (see SKILL.md caveat).
        "paid_user_days": paid_user_days,
        "cost_per_paid_user": round(total / paid_user_days, 4) if paid_user_days else None,
        "latest": {
            "date": latest["date"],
            "total": latest["total"],
            "paid": latest["paid"],
            "active": latest["active"],
            "engaged": latest["engaged"],
            "reached": latest["users_attributed"],
            "cost_per_paid": round(latest["total"] / latest["paid"], 4) if latest["paid"] else None,
            "cost_per_msg_paying":   latest["cost_per_msg_paying"],
            "cost_per_voice_min":    latest["cost_per_voice_min"],
            "ambient_per_dau_month": latest["ambient_per_dau_month"],
        },
        "peak": {"date": peak["date"], "total": peak["total"]},
        "kpi_targets": KPI_TARGETS,
        "kpi_source": kpi_table if kpis else None,
    }

    print(json.dumps({
        "skill_version": _skill_version(),
        "source": per_user_table,
        "render": {"paid_line": not args.no_paid_line},
        "summary": summary,
        "days": out_days,
    }, indent=2))


if __name__ == "__main__":
    main()
